"""EPA AQI calculator — sub-index per pollutant, overall AQI = max(sub-indices).

Reference: EPA (2024) Technical Assistance Document for the Reporting of
Daily Air Quality — The Air Quality Index (AQI), EPA-454/B-24-002.

Hesaplama formülü (her kirletici için):

    I_p = ((I_hi - I_lo) / (BP_hi - BP_lo)) * (C - BP_lo) + I_lo

Genel AQI tüm alt indekslerin maksimumudur (max-AQI yaklaşımı).

µg/m³ → ppb/ppm dönüşümü:

EPA breakpoint tabloları gaz kirleticileri için ppb (NO2, SO2) veya ppm
(O3, CO) cinsinden tanımlıdır. Projedeki ölçümler (OpenWeatherMap +
SİM portalı) µg/m³ cinsinden gelir; standart koşullarda (25°C, 1 atm)
yaklaşık dönüşüm faktörleri uygulanır:

    O3:  1 ppb ≈ 1.96 µg/m³
    NO2: 1 ppb ≈ 1.88 µg/m³
    SO2: 1 ppb ≈ 2.62 µg/m³
    CO:  1 ppm ≈ 1145 µg/m³

Bu dönüşüm idealize bir varsayımdır — gerçek sıcaklık ve basınç değerleri
ile düzeltme yapılmaz. Akademik proje kapsamında kabul edilebilir bir
sadeleştirmedir; üretim sistemleri sensörden gelen `T` ve `P` değerlerini
kullanır.

PM2.5 ve PM10 için EPA breakpoint'leri zaten µg/m³ cinsindendir;
dönüşüm yapılmaz.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

PollutantCode = Literal["pm25", "pm10", "o3_8h", "no2", "so2", "co"]
"""Project pollutant codes — match `dim_pollutant.code` column."""

AQICategory = Literal["Good", "Moderate", "USG", "Unhealthy", "Very Unhealthy", "Hazardous"]


@dataclass(frozen=True)
class Breakpoint:
    """A single AQI breakpoint row.

    Attributes:
        bp_lo: Lower concentration bound (inclusive) in the band's native unit.
        bp_hi: Upper concentration bound (inclusive) in the band's native unit.
        i_lo: Lower AQI sub-index for this band.
        i_hi: Upper AQI sub-index for this band.
    """

    bp_lo: float
    bp_hi: float
    i_lo: int
    i_hi: int


# EPA 2024 AQI breakpoint tables.
# - PM2.5: 24-hour average, µg/m³
# - PM10:  24-hour average, µg/m³
# - O3:    8-hour average,  ppm
# - NO2:   1-hour average,  ppb
# - SO2:   1-hour average,  ppb
# - CO:    8-hour average,  ppm
BREAKPOINTS: Final[dict[PollutantCode, list[Breakpoint]]] = {
    "pm25": [
        Breakpoint(0.0, 9.0, 0, 50),
        Breakpoint(9.1, 35.4, 51, 100),
        Breakpoint(35.5, 55.4, 101, 150),
        Breakpoint(55.5, 125.4, 151, 200),
        Breakpoint(125.5, 225.4, 201, 300),
        Breakpoint(225.5, 325.4, 301, 500),
    ],
    "pm10": [
        Breakpoint(0, 54, 0, 50),
        Breakpoint(55, 154, 51, 100),
        Breakpoint(155, 254, 101, 150),
        Breakpoint(255, 354, 151, 200),
        Breakpoint(355, 424, 201, 300),
        Breakpoint(425, 604, 301, 500),
    ],
    "o3_8h": [
        Breakpoint(0.000, 0.054, 0, 50),
        Breakpoint(0.055, 0.070, 51, 100),
        Breakpoint(0.071, 0.085, 101, 150),
        Breakpoint(0.086, 0.105, 151, 200),
        Breakpoint(0.106, 0.200, 201, 300),
    ],
    "no2": [
        Breakpoint(0, 53, 0, 50),
        Breakpoint(54, 100, 51, 100),
        Breakpoint(101, 360, 101, 150),
        Breakpoint(361, 649, 151, 200),
        Breakpoint(650, 1249, 201, 300),
        Breakpoint(1250, 2049, 301, 500),
    ],
    "so2": [
        Breakpoint(0, 35, 0, 50),
        Breakpoint(36, 75, 51, 100),
        Breakpoint(76, 185, 101, 150),
        Breakpoint(186, 304, 151, 200),
        Breakpoint(305, 604, 201, 300),
        Breakpoint(605, 1004, 301, 500),
    ],
    "co": [
        Breakpoint(0.0, 4.4, 0, 50),
        Breakpoint(4.5, 9.4, 51, 100),
        Breakpoint(9.5, 12.4, 101, 150),
        Breakpoint(12.5, 15.4, 151, 200),
        Breakpoint(15.5, 30.4, 201, 300),
        Breakpoint(30.5, 50.4, 301, 500),
    ],
}


# µg/m³ → EPA native unit conversion factors at standard conditions
# (25°C, 1 atm). Source: NIOSH Pocket Guide, EPA conversion tables.
_UGM3_TO_NATIVE: Final[dict[PollutantCode, float]] = {
    "o3_8h": 1.0 / 1.96,  # µg/m³ → ppb → (× 0.001) → ppm
    "no2": 1.0 / 1.88,  # µg/m³ → ppb
    "so2": 1.0 / 2.62,  # µg/m³ → ppb
    "co": 1.0 / 1145.0,  # µg/m³ → ppm (CO daha sık ppm cinsindendir)
}
"""Conversion factors from µg/m³ to EPA native unit for gas pollutants.

PM2.5 and PM10 are not in this map — they're already in µg/m³ and match
EPA breakpoints directly. O3 conversion produces ppb; the breakpoint
table for `o3_8h` is in ppm, so an additional ×0.001 factor is applied
in `convert_to_native_unit`.
"""


def convert_to_native_unit(pollutant: PollutantCode, concentration_ugm3: float) -> float:
    """Convert a µg/m³ measurement to the EPA breakpoint's native unit.

    Args:
        pollutant: Pollutant code (matches `dim_pollutant.code`).
        concentration_ugm3: Measurement in micrograms per cubic meter.

    Returns:
        Concentration in the pollutant's EPA breakpoint unit (µg/m³
        for PM, ppb for NO2/SO2, ppm for O3/CO).

    Raises:
        ValueError: If concentration is negative.
    """
    if concentration_ugm3 < 0:
        msg = f"Concentration cannot be negative: {concentration_ugm3}"
        raise ValueError(msg)
    if pollutant in ("pm25", "pm10"):
        return concentration_ugm3
    factor = _UGM3_TO_NATIVE[pollutant]
    native = concentration_ugm3 * factor
    # O3 conversion produces ppb; breakpoint table is in ppm.
    if pollutant == "o3_8h":
        native = native / 1000.0
    return native


def calculate_sub_index(pollutant: PollutantCode, concentration_ugm3: float) -> int:
    """Calculate the AQI sub-index for a single pollutant.

    Implements the EPA breakpoint linear interpolation formula:

        I_p = ((I_hi - I_lo) / (BP_hi - BP_lo)) * (C - BP_lo) + I_lo

    Args:
        pollutant: Pollutant code (matches `dim_pollutant.code`).
        concentration_ugm3: Measurement in micrograms per cubic meter.

    Returns:
        Integer AQI sub-index in the range [0, 500]. Concentrations
        exceeding the highest defined breakpoint return the maximum
        AQI value of the highest band (capped at 500 for safety).

    Raises:
        ValueError: If the pollutant code is unknown.
    """
    if pollutant not in BREAKPOINTS:
        msg = f"Unknown pollutant code: {pollutant}"
        raise ValueError(msg)

    concentration = convert_to_native_unit(pollutant, concentration_ugm3)
    bands = BREAKPOINTS[pollutant]

    for band in bands:
        if band.bp_lo <= concentration <= band.bp_hi:
            # Linear interpolation across the band.
            ratio = (band.i_hi - band.i_lo) / (band.bp_hi - band.bp_lo)
            sub_index = ratio * (concentration - band.bp_lo) + band.i_lo
            return round(sub_index)

    # Above the highest band — cap at the top of the last band.
    if concentration > bands[-1].bp_hi:
        return bands[-1].i_hi
    # Below zero shouldn't happen (guarded above), but be defensive.
    return 0


def calculate_overall_aqi(measurements: dict[PollutantCode, float]) -> int:
    """Calculate the overall AQI as the maximum of all available sub-indices.

    EPA's "max AQI" approach: when multiple pollutants are measured, the
    overall AQI is the worst (highest) sub-index. The reported AQI
    therefore reflects the most concerning pollutant for the hour.

    Args:
        measurements: Mapping of pollutant code → concentration (µg/m³).
            Empty mapping returns 0 (no data).

    Returns:
        Overall AQI integer.
    """
    if not measurements:
        return 0
    sub_indices = [calculate_sub_index(p, c) for p, c in measurements.items()]
    return max(sub_indices)


def category_for_aqi(aqi: int) -> AQICategory:
    """Map an AQI integer to the EPA category label.

    Categories follow EPA's 6-band classification:

    |   AQI   | Category          |
    |---------|-------------------|
    | 0–50    | Good              |
    | 51–100  | Moderate          |
    | 101–150 | USG (Unhealthy for Sensitive Groups) |
    | 151–200 | Unhealthy         |
    | 201–300 | Very Unhealthy    |
    | 301+    | Hazardous         |
    """
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


__all__ = [
    "AQICategory",
    "BREAKPOINTS",
    "Breakpoint",
    "PollutantCode",
    "calculate_overall_aqi",
    "calculate_sub_index",
    "category_for_aqi",
    "convert_to_native_unit",
]
