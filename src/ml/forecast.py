"""AQI 24-hour ahead forecast via Facebook Prophet.

Sprint 14 deliverable. Trains a per-station Prophet model on hourly
AQI history, generates a 24-hour forecast, and persists the result
to the `forecast_24h` table for Streamlit consumption.

Modeling choices (sprint-14 B1):

* **Prophet** chosen over ARIMA because it natively models trend
  + multiple seasonalities + holiday effects in an additive form.
  TR resmi tatil + Diyanet bayram setini Sprint 5 `dim_time` seed'i
  zaten doldurdu; Prophet'in `holidays` parametresine bu seti
  doğrudan bağlayabiliriz.
* **Per-station model** rather than a single multi-output model.
  Aliağa endüstri profili kentsel istasyonlardan farklı dinamiklere
  sahip; tek bir model bu farklılığı yumuşatır. 6 ufak model
  eğitilmek operasyonel olarak da basit (paralelleştirilebilir).
* **Multiplicative seasonality** modu — kış aylarında AQI baseline'ı
  yaz aylarından sistematik olarak yüksek; oransal mevsimsellik
  bu profili aditif modele göre daha iyi yakalar.
* **Uncertainty intervals**: `uncertainty_samples=1000` ile %95
  güven aralığı (yhat_lower / yhat_upper). Streamlit panelinde
  shaded band olarak gösterilir.

Değerlendirme metrikleri (`evaluate`):

* MAE  — Mean Absolute Error
* MAPE — Mean Absolute Percentage Error
* sMAPE — symmetric MAPE (oransal hatada sıfır-bölme tuzağına karşı
  daha güvenli; yan yana karşılaştırılır)
* PIC — Prediction Interval Coverage (gerçek değerin %95 aralığa
  düşme oranı; iyi kalibre bir modelde ~0.95)

Veri sözleşmesi:

* Girdi DataFrame'i sütunları: `ds` (datetime, UTC), `y` (float, AQI).
  Prophet'in zorunlu kalıbı.
* `dim_time` öznitelikleri (`is_holiday`, `dow`, `season`, `hour`)
  pandas merge ile katılır; Prophet `add_regressor` ile alır.

Bu modül `[ml]` opsiyonel extras (`pip install -e ".[ml]"`) altında
toplanır; Streamlit container bu extras'la build alır, Spark container
ML bağımlılıklarına gerek duymaz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import pandas as pd
    from prophet import Prophet

_LOG = logging.getLogger(__name__)

# Forecast horizon (saat). Sprint 14 hedefi 24 saat ileri; daha uzun
# horizon'lar Prophet'in mevsimsel komponentlerinin gürültüsünü ortaya
# çıkartır. 24 saat hem operasyonel hem ders kapsamı için yeterli.
FORECAST_HORIZON_HOURS: Final[int] = 24

# %95 güven aralığı için uncertainty sampling boyutu. Yüksek sayı daha
# kararlı interval ama eğitim ~2x yavaşlar; 1000 makul orta nokta.
_UNCERTAINTY_SAMPLES: Final[int] = 1000

# Train-test split ratio — 80% eğitim, 20% test. Hem zaman serisinin
# son 20%'sini kapsar (forward-looking validasyon) hem yeterli eğitim
# verisi bırakır.
_TRAIN_RATIO: Final[float] = 0.80


@dataclass(frozen=True)
class ForecastMetrics:
    """Evaluation metrics for a single forecast run.

    Attributes:
        mae: Mean Absolute Error in AQI units.
        mape: Mean Absolute Percentage Error (fraction; multiply by
            100 for percentage display).
        smape: Symmetric MAPE — bounded in [0, 2], avoids the
            asymmetric blow-up of regular MAPE when y is near zero.
        prediction_interval_coverage: Fraction of test rows whose true
            value falls within the %95 prediction interval. A
            well-calibrated model should be near 0.95.
        sample_count: Number of test rows the metrics were computed
            over.
    """

    mae: float
    mape: float
    smape: float
    prediction_interval_coverage: float
    sample_count: int


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def prepare_training_frame(
    hourly_aqi: pd.DataFrame,
    dim_time: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reshape hourly AQI into Prophet's expected `(ds, y)` schema.

    Args:
        hourly_aqi: DataFrame with at least columns `measured_at`
            (UTC tz-aware) and `aqi`. Additional columns are passed
            through for regressor wiring.
        dim_time: Optional `dim_time` table slice. If provided, the
            `is_holiday`, `dow`, `season` and `hour` columns are
            merged on `measured_at` for regressor usage in `train`.

    Returns:
        DataFrame indexed contiguously, with columns
        `(ds, y, [is_holiday, dow, season, hour])`. The `ds` column
        is tz-naive UTC (Prophet does not accept tz-aware datetimes
        as of v1.1).
    """
    import pandas as pd  # noqa: PLC0415

    frame = hourly_aqi.rename(columns={"measured_at": "ds", "aqi": "y"})
    # Prophet refuses tz-aware ds — convert to naive UTC.
    if hasattr(frame["ds"].dtype, "tz") and frame["ds"].dt.tz is not None:
        frame = frame.assign(ds=frame["ds"].dt.tz_convert(None))
    else:
        frame = frame.assign(ds=pd.to_datetime(frame["ds"]))

    if dim_time is not None and not dim_time.empty:
        dim = dim_time.copy()
        if "measured_at" in dim.columns:
            dim = dim.rename(columns={"measured_at": "ds"})
        if hasattr(dim["ds"].dtype, "tz") and dim["ds"].dt.tz is not None:
            dim = dim.assign(ds=dim["ds"].dt.tz_convert(None))
        keep = ["ds"] + [c for c in ("is_holiday", "dow", "season", "hour") if c in dim.columns]
        frame = frame.merge(dim[keep], on="ds", how="left")
        # `is_holiday` to int — Prophet regressors must be numeric.
        if "is_holiday" in frame.columns:
            frame = frame.assign(is_holiday=frame["is_holiday"].fillna(False).astype(int))

    return frame.sort_values("ds").reset_index(drop=True)


def split_train_test(
    frame: pd.DataFrame, train_ratio: float = _TRAIN_RATIO
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Forward-looking time-series split.

    Returns ``(train, test)`` where `train` covers the first
    `train_ratio` of `ds`-sorted rows and `test` is the remainder.
    Random shuffling is intentionally not used — leakage from future
    observations would inflate metrics meaninglessly.
    """
    if not 0.0 < train_ratio < 1.0:
        msg = f"train_ratio must be in (0, 1), got {train_ratio}"
        raise ValueError(msg)
    cutoff = int(len(frame) * train_ratio)
    return frame.iloc[:cutoff].copy(), frame.iloc[cutoff:].copy()


# ---------------------------------------------------------------------------
# Training + forecasting
# ---------------------------------------------------------------------------


def build_prophet_model() -> Prophet:
    """Construct a Prophet instance with project-tuned hyperparameters.

    Defaults:

    * `seasonality_mode='multiplicative'` — AQI baseline rises in
      winter; multiplicative captures the proportional swing better
      than additive.
    * `daily_seasonality=True` — diurnal traffic + heating profile is
      the dominant within-day signal.
    * `weekly_seasonality=True` — weekday vs weekend traffic delta.
    * `yearly_seasonality=False` — sample window covers ~2 years; the
      yearly component is unreliable and adds noise. Set to True once
      ≥ 3 years of history are available.
    * `uncertainty_samples=1000` — see module docstring.
    """
    from prophet import Prophet  # noqa: PLC0415

    return Prophet(
        seasonality_mode="multiplicative",
        daily_seasonality=True,
        weekly_seasonality=True,
        yearly_seasonality=False,
        uncertainty_samples=_UNCERTAINTY_SAMPLES,
        interval_width=0.95,
    )


def train(train_frame: pd.DataFrame) -> Prophet:
    """Fit a Prophet model on the prepared training frame.

    If regressor columns (`is_holiday`, `hour`) are present, they are
    wired with `add_regressor` before fitting. The model expects them
    to be present in the future DataFrame too — see `forecast`.
    """
    model = build_prophet_model()
    if "is_holiday" in train_frame.columns:
        model.add_regressor("is_holiday")
    if "hour" in train_frame.columns:
        model.add_regressor("hour")
    _LOG.info("training prophet model: %d rows", len(train_frame))
    model.fit(train_frame)
    return model


def forecast(
    model: Prophet,
    horizon_hours: int = FORECAST_HORIZON_HOURS,
    last_observation: datetime | None = None,
) -> pd.DataFrame:
    """Generate an hourly forecast `horizon_hours` ahead of the last fitted row.

    Args:
        model: Trained Prophet model.
        horizon_hours: Number of hourly future rows to project.
        last_observation: Override the anchor point. Defaults to the
            last `ds` seen during fit.

    Returns:
        DataFrame with Prophet's standard output: `(ds, yhat,
        yhat_lower, yhat_upper)` + any regressors that were declared.
        Only the future rows (not history) are returned.
    """
    import pandas as pd  # noqa: PLC0415

    future = model.make_future_dataframe(periods=horizon_hours, freq="h")

    # Future regressors must be populated — Prophet refuses NaN.
    if "is_holiday" in model.extra_regressors:
        future = future.assign(is_holiday=0)
    if "hour" in model.extra_regressors:
        future = future.assign(hour=future["ds"].dt.hour)

    raw_forecast = model.predict(future)

    if last_observation is None:
        # `model.history` is the frame seen during fit; the last ds is
        # the anchor of our future projection.
        last_observation = model.history["ds"].max()
    # Filter to future-only rows for the Streamlit panel + JDBC write.
    return raw_forecast.loc[
        raw_forecast["ds"] > pd.Timestamp(last_observation),
        ["ds", "yhat", "yhat_lower", "yhat_upper"],
    ].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    model: Prophet,
    test_frame: pd.DataFrame,
) -> ForecastMetrics:
    """Predict over `test_frame.ds` and compute MAE/MAPE/sMAPE/PIC.

    The model must be fitted; this function applies it to the test
    DataFrame's `ds` column (and required regressor columns) and
    compares against `y`.
    """
    if test_frame.empty:
        msg = "Cannot evaluate on empty test_frame"
        raise ValueError(msg)
    needed = ["ds"]
    if "is_holiday" in model.extra_regressors:
        needed.append("is_holiday")
    if "hour" in model.extra_regressors:
        needed.append("hour")
    future = test_frame[needed].copy()
    predicted = model.predict(future)
    return compute_metrics(
        y_true=test_frame["y"].to_numpy(),
        y_pred=predicted["yhat"].to_numpy(),
        y_lower=predicted["yhat_lower"].to_numpy(),
        y_upper=predicted["yhat_upper"].to_numpy(),
    )


def compute_metrics(
    y_true: object,
    y_pred: object,
    y_lower: object | None = None,
    y_upper: object | None = None,
) -> ForecastMetrics:
    """Compute MAE/MAPE/sMAPE/PIC from raw arrays.

    Split from `evaluate` so the metric logic is unit-testable without
    a Prophet dependency.

    Args:
        y_true: Ground truth (1-D array-like).
        y_pred: Point predictions (1-D array-like, same length as y_true).
        y_lower: Lower bound of prediction interval (optional).
        y_upper: Upper bound of prediction interval (optional).

    Returns:
        ForecastMetrics. If `y_lower`/`y_upper` are omitted, PIC = 0.
    """
    import numpy as np  # noqa: PLC0415

    truth = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    if truth.shape != pred.shape:
        msg = f"shape mismatch: y_true={truth.shape} y_pred={pred.shape}"
        raise ValueError(msg)

    abs_err = np.abs(truth - pred)
    mae = float(np.mean(abs_err))

    # MAPE — guard against division by zero with a tiny epsilon.
    eps = 1e-9
    mape = float(np.mean(abs_err / np.maximum(np.abs(truth), eps)))

    # sMAPE — symmetric, bounded in [0, 2].
    denom = (np.abs(truth) + np.abs(pred)) / 2.0
    smape = float(np.mean(abs_err / np.maximum(denom, eps)))

    if y_lower is not None and y_upper is not None:
        lower = np.asarray(y_lower, dtype=float)
        upper = np.asarray(y_upper, dtype=float)
        pic = float(np.mean((truth >= lower) & (truth <= upper)))
    else:
        pic = 0.0

    return ForecastMetrics(
        mae=mae,
        mape=mape,
        smape=smape,
        prediction_interval_coverage=pic,
        sample_count=int(truth.size),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def to_persistence_frame(forecast_df: pd.DataFrame, station_slug: str) -> pd.DataFrame:
    """Annotate a forecast DataFrame with metadata for the
    `forecast_24h` table.

    Adds `station_slug` and `forecasted_at` (the moment we ran the
    prediction) so the Streamlit panel can pick the latest forecast
    per station with `ORDER BY forecasted_at DESC LIMIT 1`.
    """
    import pandas as pd  # noqa: PLC0415

    return forecast_df.assign(
        station_slug=station_slug,
        forecasted_at=pd.Timestamp(datetime.now(UTC)).tz_convert(None),
    )


__all__ = [
    "FORECAST_HORIZON_HOURS",
    "ForecastMetrics",
    "build_prophet_model",
    "compute_metrics",
    "evaluate",
    "forecast",
    "prepare_training_frame",
    "split_train_test",
    "to_persistence_frame",
    "train",
]
