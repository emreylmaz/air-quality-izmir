"""Schema stub sanity tests (Hafta 3).

Gerçek bir Postgres bağlantısı kurmaz — sadece `schema.sql` dosyasının
varlığını ve ilkel yapısal beklentileri (tablo sayısı, seed kapsamı)
regex ile doğrular. Tam DDL doğrulaması Hafta 4'te testcontainers
tabanlı integration testine taşınacak.
"""

from __future__ import annotations

import re

from src.storage import SCHEMA_SQL

EXPECTED_TABLES = {"dim_station", "dim_pollutant", "fact_measurements"}
EXPECTED_POLLUTANT_CODES = {"pm25", "pm10", "no2", "so2", "o3", "co"}


def _read_schema() -> str:
    assert SCHEMA_SQL.exists(), f"schema.sql bulunamadı: {SCHEMA_SQL}"
    return SCHEMA_SQL.read_text(encoding="utf-8")


def test_schema_file_is_readable() -> None:
    sql = _read_schema()
    assert sql.strip(), "schema.sql boş olmamalı"


def test_schema_creates_three_expected_tables() -> None:
    sql = _read_schema()
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)",
        re.IGNORECASE,
    )
    found = {match.group(1).lower() for match in pattern.finditer(sql)}
    missing = EXPECTED_TABLES - found
    assert not missing, f"Eksik tablo(lar): {missing} (bulunan: {found})"


def test_seed_inserts_six_pollutant_codes() -> None:
    sql = _read_schema()
    # Seed bloğunu izole et — `INSERT INTO dim_pollutant ... ON CONFLICT`
    seed_match = re.search(
        r"INSERT\s+INTO\s+dim_pollutant\b.*?ON\s+CONFLICT",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert seed_match, "dim_pollutant seed bloğu bulunamadı"
    seed_block = seed_match.group(0)

    for code in EXPECTED_POLLUTANT_CODES:
        assert f"'{code}'" in seed_block, f"Pollutant kodu seed'de eksik: {code!r}"
