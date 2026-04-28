# Tech Debt Log

Bu dosya sprint sürecinde biriken — ama o sprint'te çözülmesi planlanmamış —
teknik borçları tutar. Her kayıt: ID, başlık, kaynak sprint, hedef sprint,
sahip, kısa açıklama.

## Açık

| ID | Başlık | Açıldı | Hedef | Sahip | Açıklama |
|----|--------|--------|-------|-------|----------|
| TD-01 | Grafana sub-app FQDN port injection | H1-2 | H13 | analytics-engineer + coolify-engineer | `provision.py:apply_actions → ensure_public_app` `fqdn` parametresini geçirmiyor; config.yaml'daki URL'ler etkisiz, Coolify UUID-based default FQDN üretiyor. Sub-app FQDN'inde `:3000` port'u Caddy/Traefik routing'ini bozuyor (default Caddy landing dönüyor). Çözüm: `ensure_*` imzalarına `fqdn` ekle + payload'a dahil et. |
| TD-02 | `main-backup` local branch temizliği | H1-2 | H8 (rapor sonrası) | tech-lead | Claude trailer'lı eski history güvenlik ağı olarak duruyor. H8 ara raporu teslim edildikten sonra silinecek (`git branch -D main-backup`). |
| TD-03 | `SparkSession` pytest fixture skip durumu | H1-2 | H6 | spark-engineer | `tests/conftest.py` içindeki Spark fixture şu an `pytest.skip` ile bırakılmış. H6'da spark-engineer streaming işine başlayınca gerçek SparkSession fixture'ı tamamlayacak. |
| TD-04 | `commitizen` / `conventional-pre-commit` hook | H1-2 | H10 (DevOps güçlendirme) | devops-engineer | Şu an Conventional Commits formatı elle uygulanıyor. Pre-commit hook ekleyince yanlış formatta commit reddedilecek. CLAUDE.md TODO'sunda kayıtlı. |
| TD-05 | PySpark 3.5.1 + Python 3.13 wheel uyumsuzluğu | H3 | H6 (spark-engineer kickoff) | devops-engineer + spark-engineer | Local `.venv` Python 3.13.7. PyPI'da `pyspark-3.5.1` için cp313 wheel yok; sadece sdist (317 MB) var → `pip install -e ".[processing]"` build aşamasında dakikalarca asılıyor. H3'te `[dev,ingestion]` kuruldu, `processing` skip. Çözümler: (a) venv'i Python 3.11/3.12'ye düşür, (b) PySpark 3.5.4+ veya 4.0'a yükselt (pyproject.toml `pyspark==3.5.1` pin'i gevşet), (c) yalnız Docker `bitnami/spark:3.5.1` üzerinden çalış (host'ta pyspark hiç kurulmasın — IDE type-hint için stub paket). H6 kickoff'ta spark-engineer karar versin. |
| TD-06 | Coolify token rotation runbook | H3 (security audit) | H11 | security-compliance | Token user config'inde gitignored, repr maskeli, header masked — tek eksik 90-gün rotation cadence + runbook. H11 KVKK + güvenlik gate'inde formal döküman. |
| TD-08 | DLQ topic ACL kısıtlaması | H3 (security audit) | H10 (Kafka security pass) | security-compliance + data-engineer | Kafka producer wrapper raw payload'ı DLQ'ya yazıyor; consumer access kontrolsüz. Operator-only ACL. H10 Docker/Kafka security pass'inde. |
| TD-11 | DLQ `repr(raw)[:500]` envelope sanitization | H3 (Codex review M3) | H10 | data-engineer + security-compliance | 5+ MB payload `repr()` patlatabilir; serialize edilemeyen objeler için `try/except` koruması. Pratik senaryoda nadir. H10 Kafka security pass ile birlikte. |
| TD-13 | `default=str` JSON serialization strict mode | H3 (Codex review M5) | H10 | data-engineer | Producer JSON serialize'da datetime için `default=str` mapping; bilinmeyen tip sessizce string'e dönüşüyor. Strict mode'da `TypeError` raise + DLQ route. H10 Kafka security pass. |
| TD-14 | `data_quality_runs.payload` JSONB schema validation | H4 (security audit) | H12 (DQ framework) | data-quality-engineer + security-compliance | H4 T6 ile `data_quality_runs` tablosu açıldı; `payload JSONB DEFAULT '{}'`. H12'de DQ suite implementation'ı yazılırken payload yapısı JSON Schema veya pydantic ile valide edilsin. PII placeholder fields (free-text user input, IP, e-mail) engellesin. Audit tablosu immutable (DELETE GRANT yok, sadece SELECT/INSERT/UPDATE) — retention politikası H11 KVKK gate'inde belirlenir. |
| TD-15 | Coolify managed PG'ye `make migrate` deploy hook | H4 (security audit) | H10 (DevOps güçlendirme) | coolify-engineer + devops-engineer | Sprint 4 migration zinciri (0001-0004) henüz Coolify managed PostgreSQL'e apply edilmedi; manuel `psql` apply yapılırsa audit trail zayıf, idempotency Coolify side'da kayıtlı değil. H10'da `make migrate` deploy hook olarak Coolify'a bağlanır (git push → migration runner → schema_migrations audit kaydı). Geçici çözüm: `docs/sprints/sprint-10-coolify-migrate.md` runbook (placeholder) ile manuel apply adımlarını sabitle. |

## Kapatılanlar

| ID | Başlık | Kapatıldı | Çözüm |
|----|--------|-----------|-------|
| TD-07 | `httpx` access log policy CLAUDE.md'ye dökümante edilsin | H4 (2026-04-25) | `adeaa46` — CLAUDE.md `## Secret Management Policy` altına 3rd-party middleware uyarı paragrafı eklendi; `_mask_url` zaten contract test ile korunuyor. |
| TD-09 | `fact_measurements` UNIQUE constraint + ON CONFLICT DO NOTHING | H4 (2026-04-25) | `c685013` (UNIQUE migration test) + `13d0bbb` (csv_loader idempotent INSERT) — `(station_id, pollutant_id, measured_at, source)` UNIQUE + `ON CONFLICT DO NOTHING`. Sprint 4 perf testi 312K satır çift yükleme: `inserted=0, skipped=311040`. |
| TD-10 | `csv_loader` slug→station_id resolve | H4 (2026-04-25) | `13d0bbb` — CLI `--station-slug` mutually exclusive `--station-id` ile; `SELECT station_id FROM dim_station WHERE slug=%s`; bulunamazsa `ValueError`. |
| TD-12 | Makefile `test` target `-m "not slow and not integration"` filter | H4 (2026-04-27) | `d36c511` — `make test` artık unit-only (~13 sn), `make test-integration` testcontainers PG opt-in. CI'da default fast suite, integration ayrı job. |

> Not: Bir TD kapatıldığında "Açık" tablodan "Kapatılanlar" tablosuna taşı,
> "Çözüm" sütununa kapatan commit hash'ini yaz. Sprint çıktı tablosunda
> (sprint-NN.md → "Sprint Çıktı Tablosu") referansla.
