# Hafta 4 Sprint Plan

## Hedef

PostgreSQL star schema'yı production-grade seviyeye çıkarmak: Hafta 3 stub
şemasını **backward-compatible migration**'larla genişletmek (drop yok),
`fact_measurements` tablosunu aylık RANGE partition + index seti +
idempotency UNIQUE constraint ile donatmak, `dim_station` ve `dim_time`
tablolarını seed etmek, `csv_loader`'ı yeniden çalıştırılabilir hale
getirmek (`ON CONFLICT DO NOTHING` + `--station-slug`), ve testcontainers
ile gerçek PG 16 üzerinde idempotency + partition pruning + load
performansını doğrulamak.

Hafta 5 (boyut tabloları + indeks ince ayarı) için zemin bu sprintte atılır;
Hafta 6 Spark batch işlemenin yazacağı fact tablosu da bu sprintte
sözleşme olarak donar.

## Bağımlılık & Blocker Analizi

### Hafta 3 çıktıları — devralınanlar
| Devralınan | Durum | H4 etkisi |
|------------|-------|-----------|
| `src/storage/schema.sql` (3 tablo, indeks/partition yok) | STUB | Migration zincirinin 0001 baseline'ı; H4'te `0002_*` ve `0003_*` ekleyerek genişlet |
| `dim_pollutant` 6 seed satırı (pm25/pm10/no2/so2/o3/co) | ✓ | Değişmiyor — `pollutant_id` yabancı anahtar referansı stabil |
| `csv_loader.INSERT_SQL` plain INSERT | EKSİK | Idempotency yok; aynı CSV iki kere yüklenirse duplicate. TD-09 fix bu sprintte |
| `dim_station` boş (slug→id resolve yok) | EKSİK | `csv_loader --station-id 1` magic number'ı çalışıyor; slug-based lookup gerek. TD-10 fix bu sprintte |
| `config/stations.yaml` (6 İzmir istasyonu) | ✓ | `seed_dim_station.py` script'inin tek source-of-truth'u |
| `tests/ingestion/test_csv_loader.py` MagicMock | KISMEN | Integration testi yok; testcontainers eklenecek (T7) |
| Codex review C1/C2/C3 fix'leri merge edildi | ✓ | Dockerfile config/, timestamp tz, init.sql DO block — H4 başlangıç durumu temiz |

### Yeni blocker'lar
| # | Blocker | Sahip | Çözüm penceresi |
|---|---------|-------|-----------------|
| B1 | Partition stratejisi: pg_partman extension mı, manuel `CREATE TABLE … PARTITION OF` mu? | database-architect | **Karar:** manuel — Coolify managed PG'de extension yetkisi belirsiz, 16 hafta scope'u içinde 24 ay × 1 partition fonksiyonu yeterli |
| B2 | Migration runner: pure SQL mi, Alembic mi, basit Python `psycopg` script mi? | database-architect + devops-engineer | **Karar:** `infra/migrations/run.py` saf psycopg + version table (Alembic ORM gerektirir, bu projede SQLAlchemy yok) |
| B3 | testcontainers-postgres CI maliyeti (Docker-in-Docker, GitHub Actions image pull) | devops-engineer | **Karar:** lokalde mecburi, CI'da `@pytest.mark.integration` arkasında opsiyonel |
| B4 | `dim_time` granülerite (saatlik mi günlük mi?) | database-architect | **Karar:** saatlik. `time_id = year*1000000 + month*10000 + day*100 + hour` (örn. 2026042514). 24 ay × 30 gün × 24 saat ≈ 17K satır — küçük dim |

---

## Tech-Debt Envanteri (sprint kapsam haritası)

Bu sprintte **fix edilenler:** TD-09, TD-10, TD-12.
Bu sprintte **yeni açılanlar:** yok hedeflenen.
**Ertelenenler:** TD-11 → H10, TD-13 → H10, TD-07 → bu sprintte CLAUDE.md patch (T10).

| ID | Başlık | Hafta 4 ile ilişki |
|----|--------|---------------------|
| TD-09 | `fact_measurements` UNIQUE constraint + `ON CONFLICT DO NOTHING` | **Bu sprintte fix** (T2 + T4) |
| TD-10 | `csv_loader` slug→station_id resolve | **Bu sprintte fix** (T4) |
| TD-11 | DLQ envelope `repr(raw)[:500]` sanitization | Ertelendi → H10 Kafka security pass |
| TD-12 | Makefile `test` target `-m "not slow and not integration"` filter | **Bu sprintte chore** (T9 sub-task) |
| TD-13 | `default=str` JSON serialization strict mode | Ertelendi → H10 |
| TD-07 | `httpx` access log policy CLAUDE.md'ye yazılsın | **Bu sprintte docs patch** (T10 sub-task) |

Tam envanter için: `tech-debt.md` (TD-01..TD-13).

---

## Tasks

| # | Task | Agent | DoD | Est |
|---|------|-------|-----|-----|
| 1 | **Migration runner iskeleti** — `infra/migrations/run.py` (psycopg, version tablosu `schema_migrations(version, applied_at)`, dosya naming `NNNN_<slug>.sql`, idempotent re-run); Makefile `make migrate` target | devops-engineer | `make migrate` aynı DB'de iki kez çalıştırılınca ikincide "0 migrations applied" döner; `schema_migrations` tablosu schema baseline migration'ından önce oluşur; structured log (versiyon + süre) | 3h |
| 2 | **`0002_star_schema_expand.sql`** — backward-compatible: (a) `dim_station` mevcut → kolon ekle: `category TEXT`, `elevation_m NUMERIC(6,1) NULL`, `created_at TIMESTAMPTZ DEFAULT now()`; `slug` UNIQUE varsa no-op; (b) `dim_time` yeni tablo (`time_id INT PK`, `measured_at TIMESTAMPTZ UNIQUE`, `year/month/day/hour/dow/season/is_holiday`); (c) `fact_measurements` UNIQUE `(station_id, pollutant_id, measured_at, source)` ekle (TD-09); H3'te yazılmış satırlar varsa index oluşturma `CONCURRENTLY` ya da migration başında `TRUNCATE` (boş tablo varsayımıyla NOTICE log) | database-architect | Migration apply sonrası `\d fact_measurements` UNIQUE constraint görünür; `\d dim_time` 8 kolon; H3 stub'da yazılan `dim_pollutant` seed satırları kayıp değil; rollback SQL'i (`0002_star_schema_expand.down.sql`) ayrı dosya | 4h |
| 3 | **`0003_partition_and_indexes.sql`** — (a) `fact_measurements` mevcut tablo → yeni `fact_measurements_partitioned` PARTITION BY RANGE (`measured_at`); 24 aylık partition (2024-01..2025-12) önceden oluştur, default partition `fact_measurements_default`; (b) eski tablodan `INSERT INTO ... SELECT` kopya; (c) ALTER swap (`RENAME`); (d) BRIN index `(measured_at)`, B-tree `(station_id, measured_at DESC)`, B-tree `(pollutant_id)`; (e) FK constraint'ler partition tabanlı yeniden eklenir; rollback path ayrı down.sql | database-architect | `\d+ fact_measurements` partition listesi 24 + default; `EXPLAIN` 2024-06 filter ile sadece tek partition scan ediyor (partition pruning kanıtı); BRIN size < B-tree size (sanity check log); migration süresi boş tabloda < 5 sn | 5h |
| 4 | **`csv_loader` idempotency + station-slug** — (a) `INSERT_SQL` → `... ON CONFLICT (station_id, pollutant_id, measured_at, source) DO NOTHING` (TD-09); (b) CLI flag `--station-slug konak` mutual-exclusive `--station-id` ile; slug verilirse `SELECT station_id FROM dim_station WHERE slug=%s` lookup, bulamazsa `ValueError` "station slug not found, run seed_dim_station first"; (c) return value: `(inserted, skipped)` tuple — log "Inserted N, skipped M (duplicate)" | data-engineer | `tests/ingestion/test_csv_loader.py`: aynı fixture iki kez yüklenince inserted_count_2 == 0 ve skipped_count_2 == 6×100; slug lookup happy path + missing-slug ValueError testleri; coverage ≥85% bu modül | 3h |
| 5 | **`dim_station` seed script** — `infra/postgres/seed_dim_station.py`: `config/stations.yaml` oku, her istasyon için `INSERT INTO dim_station (slug, name, district, lat, lon, category) VALUES (...) ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name, district=EXCLUDED.district, lat=EXCLUDED.lat, lon=EXCLUDED.lon, category=EXCLUDED.category, updated_at=now()`; idempotent; `python -m infra.postgres.seed_dim_station` CLI; compose `aqi-postgres` healthcheck sonrası ya da migrate target'tan tetiklenir | data-engineer | `tests/infra/test_seed_dim_station.py`: testcontainers PG, ilk run 6 row insert, ikinci run 0 insert + 6 update; YAML schema mismatch'te pydantic ValidationError; coverage ≥85% | 2h |
| 6 | **Materialized view + audit table** — `0004_views_and_audit.sql`: (a) `v_hourly_aqi` materialized view (`fact_measurements` → `dim_pollutant` join + AQI placeholder calculation `null::numeric`); refresh strateji yorumda (H7 streaming triggers), (b) `v_daily_trends` view (günlük min/max/avg per station+pollutant), (c) `data_quality_runs` tablosu (`run_id BIGSERIAL PK, run_at TIMESTAMPTZ, suite_name TEXT, total_checks INT, passed INT, failed INT, payload JSONB`) — H12 DQ framework dolduracak ama tablo şimdi açık | database-architect | `REFRESH MATERIALIZED VIEW v_hourly_aqi` boş tabloda hatasız çalışıyor (CREATE WITH NO DATA); `v_daily_trends` boş tabloda 0 row döndürüyor; `INSERT INTO data_quality_runs` smoke test pass | 2h |
| 7 | **Integration test: testcontainers-postgres** — `tests/integration/test_schema_apply.py`: container PG 16 ayağa kaldır, tüm migration'ları sırayla uygula, `seed_dim_station` çalıştır, fixture CSV yükle, idempotency assert (iki kez yükle → row count değişmez), partition pruning assert (`EXPLAIN ANALYZE` planında "Seq Scan on fact_measurements_2024_06" sadece bir kez); `@pytest.mark.integration` marker | data-quality-engineer | Lokalde `pytest -m integration tests/integration/` yeşil; CI'da opsiyonel marker (T9 chore'u Makefile filter ekleyince default'ta atlanır); test süresi < 90 sn | 4h |
| 8 | **Performance smoke test** — `tests/integration/test_load_performance.py`: 1 yıl × 6 istasyon × 6 pollutant ≈ 312K satır sentetik veri (hash-bazlı reproducible); `csv_loader.load_csv` çalıştır, wall-clock süre ≤ 60 sn; `EXPLAIN ANALYZE` raporu `docs/sprints/sprint-04-perf.md` runbook'una yapıştır | data-quality-engineer | Test 60 sn altında geçiyor (lokal, SSD); BRIN vs B-tree size oranı runbook'ta dokümante; partition pruning son 30 günlük query'de tek partition scan | 3h |
| 9 | **Makefile + compose chore** — (a) TD-12 fix: `test` target `-m "not slow and not integration"` ekle, ayrı `test-integration` target; (b) `migrate` target compose içinde değil host'ta `make migrate` (DSN .env.local'dan); (c) compose `aqi-postgres` healthcheck sonrası `aqi-migrate` one-shot service çalıştırsın (depends_on `service_healthy`); (d) `make seed` target → seed_dim_station çağırır | devops-engineer | `make test` integration testleri atlıyor (önce 60 sn'di, şimdi 8 sn baseline); `make up && make migrate && make seed` 3 komutla schema + dim_station hazır; `make ps` çıktısında `aqi-migrate` exited (0) | 2h |
| 10 | **Docs + security pre-review** — (a) `CLAUDE.md`'ye TD-07 `httpx` access log policy paragrafı ekle; (b) `docs/MIMARI.md` star schema bölümünü güncelle (partition + UNIQUE constraint diagramı); (c) security-compliance review: yeni migration'larda secret/credential leak yok mu, `seed_dim_station` log'unda DSN sızıntısı yok mu, `data_quality_runs.payload` JSONB GRANT'leri (`app_writer` INSERT, `app_reader` SELECT only) doğru mu | technical-writer + security-compliance | `docs/MIMARI.md` diff'i tech-lead onaylı; security audit raporu `docs/sprints/sprint-04-security-audit.md` (PASS gerekli, kritik bulgu yoksa merge); CLAUDE.md TD-07 paragrafı `## Secret Management Policy` altında | 2h |

**Toplam tahmin:** ~30h (1 sprint = 25-30h bandı, %10 risk buffer dahil — partition migration ve testcontainers entegrasyonu en yüksek risk).

---

## Blocker'lar (sprint başlarken çözüldü)

1. **B1: Partition stratejisi onayı** — Karar: **manuel `CREATE TABLE ... PARTITION OF`**, pg_partman ekleme yok. Gerekçe: Coolify managed PG'de extension yetkisi belirsiz, 16 hafta scope'u içinde 24 ay × 1 partition fonksiyonu yeterli; H10'da pg_partman değerlendirmesi yapılır (TD candidate).
2. **B2: Migration runner** — Karar: **`infra/migrations/run.py` saf psycopg + version table**. Alembic ORM gerektirir, bu projede SQLAlchemy yok. T1'de yazılır.
3. **B3: testcontainers CI maliyeti** — Karar: lokalde **mecburi**, CI'da `@pytest.mark.integration` ile opsiyonel. T9 Makefile filter'ı default test çalıştırmasını hızlı tutar.
4. **B4: `dim_time` granülerite** — Karar: **saatlik**. `time_id = year*1000000 + month*10000 + day*100 + hour` (örn. 2026042514). 24 ay × 30 gün × 24 saat ≈ 17K satır — küçük dim, fact'ten ayrı bir join cost'u olmaz. Holiday flag boş bırakılır (H5'te dolar).
5. **`make up` smoke** — H3 demo runbook'u (sprint-03-demo.md) hâlâ geçerli; H4'te `make migrate && make seed` adımları runbook'a eklenecek.

---

## Demo Senaryosu (Hafta 4 sonu, 12 dk)

1. **(1 dk)** `make down && make up` — 5/5 healthy.
2. **(2 dk)** `make migrate` — terminal'de "Applied 4 migrations: 0001, 0002, 0003, 0004 in 3.2s". Tekrar çalıştır → "0 migrations applied" (idempotency).
3. **(1 dk)** `make seed` — "dim_station: 6 inserted, 0 updated".
4. **(2 dk)** `psql -c "\d+ fact_measurements"` — partition listesi (24 aylık + default), UNIQUE constraint, 3 index görünür.
5. **(2 dk)** `python -m src.ingestion.csv_loader fixture.csv --station-slug konak` — "Inserted 600, skipped 0". Tekrar çalıştır → "Inserted 0, skipped 600" (idempotency kanıtı).
6. **(2 dk)** `EXPLAIN ANALYZE SELECT ... WHERE measured_at >= '2024-06-01' AND measured_at < '2024-07-01'` — sadece `fact_measurements_2024_06` partition scan'lendi (pruning kanıtı).
7. **(1 dk)** `pytest -m integration tests/integration/` — schema_apply + load_performance yeşil; 312K satır < 60 sn.
8. **(1 dk)** `pytest --cov=src/ingestion --cov=infra` — coverage tablosu, conventional commit log'u.

---

## Agent Atama Özeti

| Agent | Task'ları | Başlangıç sırası |
|-------|-----------|------------------|
| database-architect | 2, 3, 6 (ana sahip) | **İlk** — T1 hazır olunca paralel; T2 → T3 → T6 sıralı |
| data-engineer | 4, 5 | T2 biter bitmez (dim_station seed migration sonrası), T4 ise T2'nin UNIQUE constraint'i live olduktan sonra |
| devops-engineer | 1, 9 | T1 ilk gün, T9 sprint sonu |
| data-quality-engineer | 7, 8 | T2-T6 hepsi merge'lendikten sonra (en geç) |
| security-compliance + technical-writer | 10 | PR-merge gate (tüm task'lar review aşamasında) |

---

## Sprint Çıktı Tablosu (haftalık rapor)

| Hafta | Hedef | Durum | Agent | Blocker |
|-------|-------|-------|-------|---------|
| 1-2 | Setup + Coolify provision | ✅ | tech-lead + coolify-engineer | - |
| 3 | Kafka + API + CSV loader | ✅ | data-engineer + devops-engineer | - (Codex review C1/C2/C3 fix'lendi) |
| **4** | **Star schema + partition + idempotency** | **🟡 planned** | **database-architect** (ana) + data-engineer | **Tüm B1-B4 kickoff'ta çözüldü** |
| 5 | Boyut tabloları + indeks ince ayarı | ⏳ | database-architect | dim_time holiday seed kaynağı (TR resmi tatil API'si veya statik liste) |
| 6 | Spark batch işleme | ⏳ | spark-engineer | TD-05 PySpark/Py3.13 wheel kararı |

---

## Ret Kriterleri (PR review checklist)

- Migration `DROP TABLE` veya `DROP COLUMN` içeriyor → **reject** (backward-compat ihlali, H3 verisi korunmalı)
- Migration idempotent değil (aynı versiyon iki kez apply'da hata) → **reject**
- `pg_partman` veya başka extension `CREATE EXTENSION` çağrısı → **reject** (B1 kararı, manuel partition)
- `csv_loader` `--station-slug` lookup'ı SQL injection'a açık (string concat) → **hard reject + security eskalasyon**
- Test coverage `src/ingestion/csv_loader.py` < %85 → **reject (data-quality-engineer'a geri)**
- Integration test yok / sadece MagicMock → **reject** (testcontainers DoD'u açık)
- Yeni migration'da hardcoded password / DSN → **hard reject + security eskalasyon**
- Coolify managed PG'ye manuel `psql` ile schema değişikliği → **reject** (migration script'i üzerinden gitmeli; coolify-engineer'a `make migrate` runner'ını Coolify deploy hook'una bağlama TD-candidate)
- `mypy --strict` fail → **reject, revizyon**

---

## Sonraki Adım — İlk Handoff

**Hedef:** `database-architect` — Sprint kickoff'ta B1 + B4 kararlarını onayla, T2 + T3 migration draft'ı hazırla.

Handoff context:
> Hafta 4 sprint başlıyor. Ana hedef: H3 stub schema'sını backward-compatible
> migration zinciriyle (0002, 0003, 0004) production-grade star schema'ya
> genişletmek. **B1 kararı: manuel `CREATE TABLE ... PARTITION OF`, pg_partman
> yok.** **B4 kararı: dim_time saatlik granülerite, `time_id = YYYYMMDDHH`
> integer PK.** İlk task'ın T2 (`0002_star_schema_expand.sql`) — `dim_station`
> kolon eklemeleri (`category`, `elevation_m`, `created_at`), `dim_time` yeni
> tablo, `fact_measurements` UNIQUE `(station_id, pollutant_id, measured_at,
> source)` (TD-09 fix). DROP yok, sadece ADD COLUMN ve ADD CONSTRAINT.
> Rollback için ayrı `*.down.sql`. T3 (partition + index) sırada bekle —
> T1 (migration runner) tamamlanmadan başlama. Conventional Commits zorunlu:
> `feat(db): expand dim_station and add dim_time table` ve
> `feat(db): partition fact_measurements by month with brin and btree
> indexes` ayrı commit'ler. Acceptance: migration apply sonrası H3 stub'da
> yazılmış 6 `dim_pollutant` seed satırı kaybolmaz; rollback path test edilir
> (testcontainers integration testi T7'de).

**Paralel handoff (kickoff):** `devops-engineer` → T1 (migration runner)
T2'den önce hazır olmalı. T1 acceptance: `make migrate` idempotent + version
tablosu + structured log.

**Pre-review request:** `security-compliance` — T2/T3/T6 SQL'lerinde GRANT
statement'ları gözden geçirilecek (`app_reader` SELECT only, `app_writer`
INSERT+UPDATE, hiçbiri SUPERUSER veya CREATEROLE almaz). T10'da formal
audit raporu.
