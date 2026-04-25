"""Storage layer — PostgreSQL star schema.

H4'ten itibaren şema tek source-of-truth olarak `infra/migrations/` altındaki
`NNNN_<slug>.sql` dosyaları. `SCHEMA_SQL` sabiti H3'te `src/storage/schema.sql`'e
işaret ediyordu; runner'a (B stratejisi, sprint-04 T1) geçişle birlikte aynı
sabit artık `0001_baseline.sql`'e işaret ediyor — H3 testleri ve csv_loader
şema referansı bozulmadan çalışır, ama yetkili dosya runner'ın altındadır.
"""

from pathlib import Path

# `infra/migrations/0001_baseline.sql` — repo kökü ↔ src/storage'tan göreli yol.
SCHEMA_SQL: Path = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0001_baseline.sql"
)
"""Absolute path to the baseline schema (migration 0001).

Hafta 3'te `src/storage/schema.sql`'e işaret ediyordu; H4 sprint-04 T1 ile
migration runner'a (`infra/migrations/run.py`) geçildi ve baseline dosyası
`infra/migrations/0001_baseline.sql`'e taşındı. İçerik birebir korunur,
genişletmeler 0002, 0003, … olarak ayrı migration dosyalarında inşa edilir.
"""

__all__ = ["SCHEMA_SQL"]
