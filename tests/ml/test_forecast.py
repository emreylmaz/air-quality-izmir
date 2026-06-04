"""Tests for `src.ml.forecast`.

Two layers:

* **Unit** — pure-numpy metric computation, no Prophet dependency.
  Always run, fast (<10 ms).
* **Smoke** (`@pytest.mark.slow`) — fits a Prophet model on synthetic
  AQI data and asserts a small forecast can be produced + evaluated.
  Skipped by default; opt-in with `pytest -m slow`.

DoD source: RAPOR_H16.md section 3G + section 4 (model değerlendirme
metrikleri MAE/MAPE/sMAPE/PIC).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.ml.forecast import (
    FORECAST_HORIZON_HOURS,
    compute_metrics,
    prepare_training_frame,
    split_train_test,
    to_persistence_frame,
)

# ---------------------------------------------------------------------------
# compute_metrics — pure numerical
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    """MAE / MAPE / sMAPE / PIC against hand-calculated answers."""

    def test_perfect_prediction_zeroes_errors(self) -> None:
        truth = [10.0, 20.0, 30.0]
        metrics = compute_metrics(truth, truth)
        assert metrics.mae == 0.0
        assert metrics.mape == 0.0
        assert metrics.smape == 0.0
        assert metrics.sample_count == 3

    def test_mae_arithmetic(self) -> None:
        truth = [10.0, 20.0, 30.0]
        pred = [12.0, 18.0, 33.0]
        # |2| + |2| + |3| / 3 = 7/3 ≈ 2.333
        metrics = compute_metrics(truth, pred)
        assert metrics.mae == pytest.approx(7 / 3)

    def test_mape_arithmetic(self) -> None:
        # |2/10| + |2/20| + |3/30| / 3 = (0.2 + 0.1 + 0.1) / 3 = 0.133
        truth = [10.0, 20.0, 30.0]
        pred = [12.0, 18.0, 33.0]
        metrics = compute_metrics(truth, pred)
        assert metrics.mape == pytest.approx(0.4 / 3, rel=1e-6)

    def test_smape_bounded_zero_to_two(self) -> None:
        truth = [10.0, 20.0, 30.0]
        pred = [-10.0, 40.0, 60.0]
        metrics = compute_metrics(truth, pred)
        assert 0.0 <= metrics.smape <= 2.0

    def test_smape_safe_when_truth_is_zero(self) -> None:
        # Regular MAPE would divide by zero; sMAPE handles gracefully.
        truth = [0.0, 0.0]
        pred = [1.0, 2.0]
        metrics = compute_metrics(truth, pred)
        # Must produce a finite value, not inf or nan.
        assert np.isfinite(metrics.smape)

    def test_pic_full_coverage(self) -> None:
        truth = [50.0, 60.0, 70.0]
        lower = [40.0, 50.0, 60.0]
        upper = [60.0, 70.0, 80.0]
        metrics = compute_metrics(truth, truth, lower, upper)
        assert metrics.prediction_interval_coverage == 1.0

    def test_pic_zero_coverage(self) -> None:
        truth = [50.0, 60.0, 70.0]
        # Intervals far from truth — no coverage.
        lower = [0.0, 0.0, 0.0]
        upper = [10.0, 10.0, 10.0]
        metrics = compute_metrics(truth, truth, lower, upper)
        assert metrics.prediction_interval_coverage == 0.0

    def test_pic_partial_coverage(self) -> None:
        truth = [50.0, 60.0, 70.0, 80.0]
        # First two covered, last two outside.
        lower = [40.0, 50.0, 0.0, 0.0]
        upper = [60.0, 70.0, 10.0, 10.0]
        metrics = compute_metrics(truth, truth, lower, upper)
        assert metrics.prediction_interval_coverage == 0.5

    def test_pic_defaults_to_zero_without_bounds(self) -> None:
        metrics = compute_metrics([1.0, 2.0], [1.0, 2.0])
        assert metrics.prediction_interval_coverage == 0.0

    def test_shape_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="shape mismatch"):
            compute_metrics([1.0, 2.0, 3.0], [1.0, 2.0])

    def test_sample_count_reflects_input(self) -> None:
        metrics = compute_metrics([1.0] * 100, [1.0] * 100)
        assert metrics.sample_count == 100


# ---------------------------------------------------------------------------
# prepare_training_frame — pandas reshaping
# ---------------------------------------------------------------------------


class TestPrepareTrainingFrame:
    """Reshape hourly_aqi into Prophet's (ds, y) schema + optional regressors."""

    def test_renames_columns(self) -> None:
        df = pd.DataFrame(
            {
                "measured_at": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
                "aqi": [50, 60, 70],
            }
        )
        out = prepare_training_frame(df)
        assert list(out.columns)[:2] == ["ds", "y"]

    def test_strips_timezone(self) -> None:
        df = pd.DataFrame(
            {
                "measured_at": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
                "aqi": [50, 60, 70],
            }
        )
        out = prepare_training_frame(df)
        # tz must be stripped for Prophet compatibility.
        assert out["ds"].dt.tz is None

    def test_merges_dim_time_regressors(self) -> None:
        ts = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
        df = pd.DataFrame({"measured_at": ts, "aqi": [50, 60, 70]})
        dim = pd.DataFrame(
            {
                "measured_at": ts,
                "is_holiday": [True, False, True],
                "hour": [0, 1, 2],
                "dow": [1, 1, 1],
                "season": ["winter", "winter", "winter"],
            }
        )
        out = prepare_training_frame(df, dim_time=dim)
        assert "is_holiday" in out.columns
        # is_holiday must be int for Prophet regressor.
        assert out["is_holiday"].tolist() == [1, 0, 1]
        assert "hour" in out.columns
        assert "season" in out.columns

    def test_no_regressors_when_dim_time_omitted(self) -> None:
        df = pd.DataFrame(
            {
                "measured_at": pd.date_range("2024-01-01", periods=3, freq="h"),
                "aqi": [50, 60, 70],
            }
        )
        out = prepare_training_frame(df)
        assert "is_holiday" not in out.columns


# ---------------------------------------------------------------------------
# split_train_test — forward-looking
# ---------------------------------------------------------------------------


class TestSplitTrainTest:
    """80/20 forward-looking split, no shuffle."""

    def test_default_80_20_split(self) -> None:
        df = pd.DataFrame(
            {
                "ds": pd.date_range("2024-01-01", periods=100, freq="h"),
                "y": list(range(100)),
            }
        )
        train, test = split_train_test(df)
        assert len(train) == 80
        assert len(test) == 20

    def test_forward_order_preserved(self) -> None:
        df = pd.DataFrame(
            {
                "ds": pd.date_range("2024-01-01", periods=10, freq="h"),
                "y": list(range(10)),
            }
        )
        train, test = split_train_test(df, train_ratio=0.5)
        # train rows must precede test rows in time.
        assert train["ds"].max() < test["ds"].min()

    def test_invalid_ratio_raises(self) -> None:
        df = pd.DataFrame({"ds": [], "y": []})
        with pytest.raises(ValueError, match="train_ratio"):
            split_train_test(df, train_ratio=1.5)


# ---------------------------------------------------------------------------
# to_persistence_frame — annotation
# ---------------------------------------------------------------------------


class TestToPersistenceFrame:
    """Adds station_slug + forecasted_at without dropping forecast columns."""

    def test_adds_metadata_columns(self) -> None:
        df = pd.DataFrame(
            {
                "ds": pd.date_range("2024-01-01", periods=3, freq="h"),
                "yhat": [50.0, 55.0, 60.0],
                "yhat_lower": [40.0, 45.0, 50.0],
                "yhat_upper": [60.0, 65.0, 70.0],
            }
        )
        out = to_persistence_frame(df, station_slug="konak")
        assert "station_slug" in out.columns
        assert "forecasted_at" in out.columns
        assert (out["station_slug"] == "konak").all()


# ---------------------------------------------------------------------------
# Prophet smoke (opt-in, slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestProphetSmoke:
    """End-to-end Prophet fit + forecast on synthetic seasonal data.

    Marked slow because Prophet's Stan compilation + sampling adds
    ~10-20 seconds. Skipped in `make test`; runs in `make test-slow`
    or the final QA sweep before submission.
    """

    def test_fit_and_forecast_produces_24h_horizon(self) -> None:
        from src.ml.forecast import forecast, train

        # 30 days × 24 hours = 720 rows. Synthetic diurnal pattern +
        # noise — gives Prophet enough signal to fit cleanly.
        start = datetime(2024, 1, 1, tzinfo=UTC)
        rows = []
        rng = np.random.default_rng(seed=14)
        for i in range(720):
            ts = start + timedelta(hours=i)
            hour = ts.hour
            # 50 ± 30 diurnal swing + 5 unit noise.
            diurnal = 50 + 30 * np.sin(2 * np.pi * hour / 24)
            value = diurnal + rng.normal(0, 5)
            rows.append({"measured_at": ts, "aqi": value})
        df = pd.DataFrame(rows)

        prepared = prepare_training_frame(df)
        model = train(prepared)
        future = forecast(model, horizon_hours=FORECAST_HORIZON_HOURS)
        assert len(future) == FORECAST_HORIZON_HOURS
        # Forecast must include the standard Prophet output columns.
        assert {"ds", "yhat", "yhat_lower", "yhat_upper"}.issubset(future.columns)
        # Predictions should be in a sensible AQI range, not negative.
        assert future["yhat"].between(0, 200).all()
