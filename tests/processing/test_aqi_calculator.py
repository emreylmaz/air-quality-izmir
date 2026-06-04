"""Tests for `src.processing.aqi_calculator`.

The EPA AQI formula is a linear interpolation between fixed breakpoints,
so we can ground-truth every band corner using EPA's published examples.

Reference: EPA (2024) Technical Assistance Document — example
calculations in Appendix B.

DoD source: `docs/sprints/sprint-06.md` T2 (planned) and the H16 report
section 3F (AQI hesaplaması). No Spark dependency — pure Python tests.
"""

from __future__ import annotations

import pytest

from src.processing.aqi_calculator import (
    BREAKPOINTS,
    calculate_overall_aqi,
    calculate_sub_index,
    category_for_aqi,
    convert_to_native_unit,
)

# ---------------------------------------------------------------------------
# convert_to_native_unit — µg/m³ → ppb/ppm
# ---------------------------------------------------------------------------


class TestConvertToNativeUnit:
    """PM stays in µg/m³; gas pollutants convert to their EPA unit."""

    def test_pm25_no_conversion(self) -> None:
        assert convert_to_native_unit("pm25", 35.4) == 35.4

    def test_pm10_no_conversion(self) -> None:
        assert convert_to_native_unit("pm10", 154.0) == 154.0

    def test_no2_ugm3_to_ppb(self) -> None:
        # 188 µg/m³ NO2 ÷ 1.88 → 100 ppb
        assert convert_to_native_unit("no2", 188.0) == pytest.approx(100.0, rel=1e-3)

    def test_so2_ugm3_to_ppb(self) -> None:
        # 262 µg/m³ SO2 ÷ 2.62 → 100 ppb
        assert convert_to_native_unit("so2", 262.0) == pytest.approx(100.0, rel=1e-3)

    def test_co_ugm3_to_ppm(self) -> None:
        # 1145 µg/m³ CO ÷ 1145 → 1 ppm
        assert convert_to_native_unit("co", 1145.0) == pytest.approx(1.0, rel=1e-3)

    def test_o3_ugm3_to_ppm(self) -> None:
        # 196 µg/m³ O3 ÷ 1.96 → 100 ppb → 0.100 ppm
        assert convert_to_native_unit("o3_8h", 196.0) == pytest.approx(0.100, rel=1e-3)

    def test_negative_concentration_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            convert_to_native_unit("pm25", -5.0)


# ---------------------------------------------------------------------------
# calculate_sub_index — band edges + interpolation
# ---------------------------------------------------------------------------


class TestCalculateSubIndex:
    """Validate band edges and linear interpolation per EPA's formula."""

    def test_pm25_band_lower_edge_is_zero(self) -> None:
        assert calculate_sub_index("pm25", 0.0) == 0

    def test_pm25_good_upper_edge_is_50(self) -> None:
        assert calculate_sub_index("pm25", 9.0) == 50

    def test_pm25_moderate_band_midpoint(self) -> None:
        # 9.1..35.4 → 51..100. Midpoint concentration ~22.25 → AQI ~75.
        result = calculate_sub_index("pm25", 22.25)
        assert 73 <= result <= 77

    def test_pm25_moderate_upper_edge_is_100(self) -> None:
        assert calculate_sub_index("pm25", 35.4) == 100

    def test_pm25_usg_upper_edge_is_150(self) -> None:
        assert calculate_sub_index("pm25", 55.4) == 150

    def test_pm10_band_edges(self) -> None:
        assert calculate_sub_index("pm10", 54.0) == 50
        assert calculate_sub_index("pm10", 154.0) == 100
        assert calculate_sub_index("pm10", 254.0) == 150

    def test_no2_band_edges(self) -> None:
        # 53 ppb = 53 × 1.88 = 99.64 µg/m³ → AQI 50
        assert calculate_sub_index("no2", 99.64) == 50

    def test_so2_band_edges(self) -> None:
        # 35 ppb = 35 × 2.62 = 91.7 µg/m³ → AQI 50
        assert calculate_sub_index("so2", 91.7) == 50

    def test_co_band_edges(self) -> None:
        # 4.4 ppm = 4.4 × 1145 = 5038 µg/m³ → AQI 50
        assert calculate_sub_index("co", 5038.0) == 50

    def test_above_max_band_capped_at_top(self) -> None:
        # PM2.5 top band ends at 325.4; anything higher caps at 500.
        assert calculate_sub_index("pm25", 1000.0) == 500

    def test_unknown_pollutant_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown pollutant"):
            calculate_sub_index("invalid_code", 50.0)  # type: ignore[arg-type]

    def test_negative_concentration_raises(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            calculate_sub_index("pm25", -1.0)


# ---------------------------------------------------------------------------
# calculate_overall_aqi — max of sub-indices
# ---------------------------------------------------------------------------


class TestCalculateOverallAQI:
    """Overall AQI follows EPA's worst-of-all approach."""

    def test_empty_measurements_returns_zero(self) -> None:
        assert calculate_overall_aqi({}) == 0

    def test_single_pollutant_equals_its_sub_index(self) -> None:
        result = calculate_overall_aqi({"pm25": 9.0})
        assert result == 50

    def test_max_wins_across_pollutants(self) -> None:
        # PM2.5 9.0 → AQI 50, PM10 154 → AQI 100. Max = 100.
        result = calculate_overall_aqi({"pm25": 9.0, "pm10": 154.0})
        assert result == 100

    def test_realistic_izmir_winter_scenario(self) -> None:
        # PM2.5 dominant in winter inversion: high PM2.5 + moderate gases.
        measurements: dict[str, float] = {
            "pm25": 75.0,  # USG-Unhealthy band
            "pm10": 120.0,  # Moderate band
            "no2": 95.0,  # ~50 ppb → low band
            "so2": 50.0,  # ~19 ppb → low band
        }
        result = calculate_overall_aqi(measurements)  # type: ignore[arg-type]
        # PM2.5 75 µg/m³ falls in (55.5..125.4 → 151..200) band.
        assert 151 <= result <= 200

    def test_zero_concentration_all_pollutants_returns_zero(self) -> None:
        # All zeros: every sub-index = 0, max = 0 (Good baseline).
        result = calculate_overall_aqi(
            {"pm25": 0.0, "pm10": 0.0, "no2": 0.0, "so2": 0.0, "co": 0.0, "o3_8h": 0.0}
        )
        assert result == 0


# ---------------------------------------------------------------------------
# category_for_aqi — EPA 6-band labels
# ---------------------------------------------------------------------------


class TestCategoryForAQI:
    """EPA 6-band category mapping."""

    @pytest.mark.parametrize(
        ("aqi", "expected"),
        [
            (0, "Good"),
            (50, "Good"),
            (51, "Moderate"),
            (100, "Moderate"),
            (101, "USG"),
            (150, "USG"),
            (151, "Unhealthy"),
            (200, "Unhealthy"),
            (201, "Very Unhealthy"),
            (300, "Very Unhealthy"),
            (301, "Hazardous"),
            (500, "Hazardous"),
        ],
    )
    def test_aqi_band_label(self, aqi: int, expected: str) -> None:
        assert category_for_aqi(aqi) == expected


# ---------------------------------------------------------------------------
# Breakpoint integrity — every band has consistent bounds
# ---------------------------------------------------------------------------


class TestBreakpointTableIntegrity:
    """The EPA table is hand-typed; guard against off-by-one regressions."""

    @pytest.mark.parametrize("pollutant", list(BREAKPOINTS))
    def test_bands_are_monotonically_increasing(self, pollutant: str) -> None:
        bands = BREAKPOINTS[pollutant]  # type: ignore[index]
        for i in range(1, len(bands)):
            prev = bands[i - 1]
            curr = bands[i]
            assert curr.bp_lo > prev.bp_hi or curr.bp_lo >= prev.bp_hi
            assert curr.i_lo >= prev.i_hi

    @pytest.mark.parametrize("pollutant", list(BREAKPOINTS))
    def test_lower_aqi_lt_upper_in_every_band(self, pollutant: str) -> None:
        for band in BREAKPOINTS[pollutant]:  # type: ignore[index]
            assert band.i_lo < band.i_hi
            assert band.bp_lo < band.bp_hi

    @pytest.mark.parametrize("pollutant", list(BREAKPOINTS))
    def test_first_band_starts_at_aqi_0(self, pollutant: str) -> None:
        assert BREAKPOINTS[pollutant][0].i_lo == 0  # type: ignore[index]
