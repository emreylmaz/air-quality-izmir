"""Sprint 14 ad-hoc bench — Prophet model MAE/MAPE/sMAPE/PIC on synthetic AQI.

NOT a pytest test; a one-shot script invoked manually to fill in the
results section of RAPOR_H16.md. Generates a 90-day hourly AQI series
with realistic diurnal + weekly + holiday-effect patterns, fits
Prophet, evaluates on the last 20%, and writes results to a sidecar
text file the report references.

Run:

    .venv/Scripts/python.exe tests/ml/_bench_forecast.py
    # Output: tests/ml/_artefacts/forecast-metrics.txt
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from src.ml.forecast import (  # noqa: E402
    evaluate,
    forecast,
    prepare_training_frame,
    split_train_test,
    train,
)


def _synthetic_aqi_series(days: int = 90, seed: int = 14) -> pd.DataFrame:
    """Build a realistic-shaped synthetic AQI dataset.

    Components:
    * Baseline 50 AQI ("Moderate" band).
    * Diurnal swing: ±25 with morning rush + afternoon trough.
    * Weekly cycle: weekends -10 (less traffic).
    * Mild upward trend: +5 over the window (winter inversion).
    * Random noise: σ=8.
    * Two "holiday spike" days where AQI doubles (model should
      handle these via Prophet's regressor when wired).
    """
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    holiday_dates = {start.date() + timedelta(days=20), start.date() + timedelta(days=60)}
    for i in range(days * 24):
        ts = start + timedelta(hours=i)
        hour = ts.hour
        dow = ts.weekday()
        diurnal = 25 * np.sin(2 * np.pi * (hour - 6) / 24)
        weekly = -10 if dow >= 5 else 0
        trend = 5 * (i / (days * 24))
        noise = rng.normal(0, 8)
        is_holiday = ts.date() in holiday_dates
        base = 50 + diurnal + weekly + trend + noise
        value = base * 2 if is_holiday else base
        value = max(value, 5)  # Floor at "Good" minimum
        rows.append(
            {
                "measured_at": ts,
                "aqi": value,
                "is_holiday": is_holiday,
                "hour": hour,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    print("[bench] generating 90-day synthetic AQI series")
    df = _synthetic_aqi_series(days=90)
    print(f"[bench] rows: {len(df)}, AQI range: [{df['aqi'].min():.1f}, {df['aqi'].max():.1f}]")

    # The bench uses the hour + is_holiday columns directly as
    # regressors; no need to merge a dim_time table.
    prepared = prepare_training_frame(df[["measured_at", "aqi"]])
    # Re-attach the regressors after prepare_training_frame's reshape.
    prepared = prepared.merge(
        df[["measured_at", "is_holiday", "hour"]]
        .rename(columns={"measured_at": "ds"})
        .assign(
            ds=lambda x: pd.to_datetime(x["ds"]).dt.tz_convert(None),
        ),
        on="ds",
        how="left",
    )
    prepared["is_holiday"] = prepared["is_holiday"].fillna(False).astype(int)

    train_df, test_df = split_train_test(prepared, train_ratio=0.80)
    print(f"[bench] train: {len(train_df)} rows, test: {len(test_df)} rows")

    print("[bench] training Prophet model")
    model = train(train_df)

    print("[bench] evaluating on test set")
    metrics = evaluate(model, test_df)

    print("[bench] generating 24h ahead forecast")
    fcst = forecast(model, horizon_hours=24)

    out_dir = _REPO_ROOT / "tests" / "ml" / "_artefacts"
    out_dir.mkdir(exist_ok=True)
    report = out_dir / "forecast-metrics.txt"
    report.write_text(
        "\n".join(
            [
                "=== Sprint 14 Prophet AQI Forecast Bench ===",
                f"window_days: 90  ({len(df)} hourly rows)",
                f"train_rows: {len(train_df)}",
                f"test_rows:  {len(test_df)}",
                "",
                "--- Evaluation metrics (on test set) ---",
                f"MAE  = {metrics.mae:.3f} AQI units",
                f"MAPE = {metrics.mape * 100:.2f}%",
                f"sMAPE = {metrics.smape * 100:.2f}%",
                f"PIC (%95 interval coverage) = {metrics.prediction_interval_coverage:.3f}",
                f"sample_count = {metrics.sample_count}",
                "",
                "--- 24h ahead forecast (first 5 rows) ---",
                fcst.head().to_string(),
                "",
                f"forecast_anchor: {train_df['ds'].max().isoformat()}",
                "forecast_horizon: 24 hours",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[bench] report written: {report}")
    print(
        f"[bench] MAE={metrics.mae:.3f}, MAPE={metrics.mape*100:.2f}%, PIC={metrics.prediction_interval_coverage:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
