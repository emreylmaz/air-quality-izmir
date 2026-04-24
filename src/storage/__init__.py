"""Storage layer — PostgreSQL star schema."""

from pathlib import Path

SCHEMA_SQL: Path = Path(__file__).parent / "schema.sql"
"""Absolute path to the local-dev initdb schema file.

Hafta 3'te csv_loader ve smoke testleri bu sabiti referans alır.
Hafta 4'te partition/index'ler geldiğinde aynı dosya genişletilecek;
yol değişmeyecek.
"""

__all__ = ["SCHEMA_SQL"]
