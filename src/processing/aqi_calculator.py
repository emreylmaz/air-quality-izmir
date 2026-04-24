"""EPA AQI calculator — sub-index per pollutant, overall AQI = max.

TODO (Hafta 6): Full implementation by `spark-engineer` agent.
Reference: EPA (2024) Technical Assistance Document for the Reporting of Daily AQI.

Responsibilities:
- Breakpoint tables per pollutant (PM2.5, PM10, O3, NO2, SO2, CO)
- Formula: AQI = ((I_hi - I_lo) / (BP_hi - BP_lo)) * (C - BP_lo) + I_lo
- Overall AQI = max(sub-indices)
- Category label: Good / Moderate / USG / Unhealthy / Very Unhealthy / Hazardous
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PollutantCode = Literal["pm25", "pm10", "o3_8h", "no2", "so2", "co"]

AQICategory = Literal["Good", "Moderate", "USG", "Unhealthy", "Very Unhealthy", "Hazardous"]


@dataclass(frozen=True)
class Breakpoint:
    """Single AQI breakpoint row."""

    bp_lo: float
    bp_hi: float
    i_lo: int
    i_hi: int


# TODO: fill with EPA 2024 tables — spark-engineer agent verifies
BREAKPOINTS: dict[PollutantCode, list[Breakpoint]] = {
    "pm25": [],
    "pm10": [],
    "o3_8h": [],
    "no2": [],
    "so2": [],
    "co": [],
}


def calculate_sub_index(pollutant: PollutantCode, concentration: float) -> int:
    """Calculate AQI sub-index for a single pollutant.

    TODO: implement in Hafta 6.
    """
    raise NotImplementedError("Hafta 6: spark-engineer agent implements this")


def category_for_aqi(aqi: int) -> AQICategory:
    """Map AQI integer to EPA category."""
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "USG"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"
