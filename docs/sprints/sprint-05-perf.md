# Sprint 5 — Planner Tuning Runbook

**Owner:** database-architect + data-quality-engineer
**Migration:** `infra/migrations/0005_planner_tuning.sql`
**Test:** `tests/infra/test_migration_0005.py::TestMigration0005Integration`
**Marker:** `@pytest.mark.integration`
**DoD ref:** `docs/sprints/sprint-05.md` T3 + T5 + T7

Bu doküman 0005 migration'ının `pg_db_role_setting` katalogunda yazdığı
iki parametrenin (`random_page_cost`, `effective_cache_size`) **etki
kanıtını** dondurur. Sprint 4 perf raporundaki (`sprint-04-perf.md`) 312 K
satır benchmark'ı baseline olarak alır; tuning öncesi/sonrası plan
seçimini yan yana koyar.

## Tuning kararlarının kapsamı

| Parametre | Default | Sprint 5 değeri | Gerekçe |
|-----------|---------|-----------------|---------|
| `random_page_cost` | 4.0 | **1.1** | SSD/NVMe profili — `sprint-04-perf.md` "Donanım baseline" tablosunda host NVMe SSD; Coolify VPS managed PG NVMe-backed block storage. SSD'de rastgele/ardışık okuma maliyet farkı neredeyse yok; PG topluluğunun standart önerisi 1.1 (1.0 değil — `seq_page_cost` ile eşitlik planner kararsızlığı yaratmasın diye marjinal yüksek). |
| `effective_cache_size` | 4 GB | **2 GB** | Local docker-compose container profili. RAM bütçesi ayırtmaz; yalnız planner'a "OS page cache + shared_buffers toplamda ne kadar sıcak tutar" ipucu. Konservatif değer; aşırı yüksek olursa fonksiyonel risk yok — yalnız index scan tercihi etkilenir. |

**Out-of-scope (sprint-05.md B2 kararı):**

* `shared_buffers` — `postgresql.conf` + restart gerektirir, Coolify managed PG'de restart yetkisi belirsiz, scope dışı.
* `work_mem` — session-level RAM bütçesi, planner cost parametresi değil; query workload tipi belirlendiğinde (H6 Spark batch + H7 streaming) yeniden değerlendirilir.

## Uygulama vektörü (B3 kararı)

`ALTER DATABASE <current_database()> SET <param> = <value>` →
`pg_db_role_setting` kataloğuna yazılır → o veritabanına açılan **HER yeni
bağlantıda** otomatik etkili. Mevcut açık bağlantılar etkilenmez.

`ALTER SYSTEM` **kullanılmaz** çünkü Coolify managed PostgreSQL'de
uygulamaya verilen rol superuser değil — `ALTER SYSTEM` orada
`ERROR: permission denied to set parameter` ile patlar. `ALTER DATABASE`
veritabanı OWNER yetkisinde, uygulama rolü kendi DB'sinin sahibidir.

## Katalog doğrulaması

İntegration test (`test_migration_0005.py::test_random_page_cost_in_catalog`,
`::test_effective_cache_size_in_catalog`) aşağıdaki SQL'i çalıştırır ve
sonucu `pg_db_role_setting` üzerinden `current_database()` için doğrular:

```sql
SELECT s.setconfig
FROM pg_db_role_setting s
JOIN pg_database d ON d.oid = s.setdatabase
WHERE d.datname = current_database() AND s.setrole = 0;
```

Beklenen array içeriği:

```
{random_page_cost=1.1, effective_cache_size=2GB}
```

Ayrıca `test_new_session_picks_up_setting` yeni bir `psycopg.connect`'in
`SHOW random_page_cost` ile `1.1` döndürdüğünü doğrular — `ALTER DATABASE`
etkisinin oturum başlangıcında uygulandığının kanıtı.

## Plan diff (öncesi vs sonrası)

Bench script: `tests/integration/_bench_planner_tuning.py` —
testcontainers PG 16.4 ayağa kaldırır, 0001..0005 zincirini uygular,
`dim_station` seed eder, 311 040 sentetik satır yükler ve aynı container
üstünde iki sorguyu hem baseline (`SET random_page_cost = 4.0` +
`SET effective_cache_size = '4GB'` ile session-level override) hem
tuned (0005'in `pg_db_role_setting`'deki ayarlarını inherit eden taze
bağlantı) profilinde çalıştırır. Çıktı:
`tests/integration/_artefacts/planner-tuning-diff.txt` (gitignored).

Aşağıdaki satırlar 2026-06-04 run'undan birebir yapıştırıldı
(`postgres:16.4-alpine`, 311 040 satır, host ASUS TUF FX507VI / NVMe SSD —
`sprint-04-perf.md` "Donanım baseline" ile aynı kutu).

### Query A — geniş aggregate (tek ay, filtre yok)

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT count(*), avg(value)
FROM fact_measurements
WHERE measured_at >= '2024-06-01 00:00+00'::timestamptz
  AND measured_at <  '2024-07-01 00:00+00'::timestamptz;
```

**Baseline — `random_page_cost = 4.0`, `effective_cache_size = 4GB`**

```
Aggregate  (cost=594.17..594.18 rows=1 width=16) (actual time=4.831..4.832 rows=1 loops=1)
  Buffers: shared hit=268
  ->  Seq Scan on fact_measurements_2024_06 fact_measurements  (cost=0.00..593.62 rows=109 width=8) (actual time=0.018..3.296 rows=25920 loops=1)
        Filter: ((measured_at >= '2024-06-01 00:00:00+00'::timestamptz) AND (measured_at < '2024-07-01 00:00:00+00'::timestamptz))
        Buffers: shared hit=268
Planning Time: 2.286 ms
Execution Time: 4.885 ms
```

**Tuned — `random_page_cost = 1.1`, `effective_cache_size = 2GB`**

```
Aggregate  (cost=397.21..397.22 rows=1 width=16) (actual time=5.333..5.334 rows=1 loops=1)
  Buffers: shared hit=2121
  ->  Index Scan using fact_measurements_2024_06_station_id_measured_at_idx on fact_measurements_2024_06  (cost=0.29..396.66 rows=109 width=8) (actual time=0.025..3.815 rows=25920 loops=1)
        Index Cond: ((measured_at >= '2024-06-01 00:00:00+00'::timestamptz) AND (measured_at < '2024-07-01 00:00:00+00'::timestamptz))
        Buffers: shared hit=2121
Planning Time: 2.469 ms
Execution Time: 5.384 ms
```

**Plan değişti:** Seq Scan → **Index Scan** (`station_id+measured_at` B-tree). Cost estimate %33 düştü (594.17 → 397.21). Execution time +0.5 ms (4.885 → 5.384) — bu sorgu tek partition üzerinde geniş tarama olduğu için Seq Scan zaten optimal idi; tuning planner'ı Index Scan'e itti. Buffer hit'i 268 → 2121 (index sayfaları + heap), bu yüzden marjinal yavaşlama. **Production'da bu sorgu pattern'i nadir** — geniş aggregate'ler Streamlit/Grafana panel'lerinde değil, batch raporlarda görünür.

### Query B — seçici (tek istasyon + tek kirletici, ordered)

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT measured_at, value
FROM fact_measurements
WHERE measured_at >= '2024-06-01 00:00+00'::timestamptz
  AND measured_at <  '2024-07-01 00:00+00'::timestamptz
  AND station_id = 1
  AND pollutant_id = 1
ORDER BY measured_at;
```

**Baseline — `random_page_cost = 4.0`, `effective_cache_size = 4GB`**

```
Index Scan Backward using fact_measurements_2024_06_station_id_measured_at_idx on fact_measurements_2024_06  (cost=0.29..8.31 rows=1 width=16) (actual time=0.016..0.773 rows=720 loops=1)
  Index Cond: ((station_id = 1) AND (measured_at >= '2024-06-01 00:00:00+00'::timestamptz) AND (measured_at < '2024-07-01 00:00:00+00'::timestamptz))
  Filter: (pollutant_id = 1)
  Rows Removed by Filter: 3600
  Buffers: shared hit=292
Planning Time: 0.300 ms
Execution Time: 0.819 ms
```

**Tuned — `random_page_cost = 1.1`, `effective_cache_size = 2GB`**

```
Index Scan Backward using fact_measurements_2024_06_station_id_measured_at_idx on fact_measurements_2024_06  (cost=0.29..2.51 rows=1 width=16) (actual time=0.016..0.773 rows=720 loops=1)
  Index Cond: ((station_id = 1) AND (measured_at >= '2024-06-01 00:00:00+00'::timestamptz) AND (measured_at < '2024-07-01 00:00:00+00'::timestamptz))
  Filter: (pollutant_id = 1)
  Rows Removed by Filter: 3600
  Buffers: shared hit=292
Planning Time: 0.243 ms
Execution Time: 0.831 ms
```

**Plan aynı, cost düştü:** Index Scan Backward (zaten optimal). Cost estimate **8.31 → 2.51 (%70 düşüş)** — planner artık random page erişiminin gerçek SSD maliyetini biliyor; başka query'lerin (örn. join'lerin) maliyet karşılaştırmasında doğru oransal ağırlık kullanılabiliyor. Execution time aynı (~0.83 ms), buffer hit aynı (292) — bu sorgu için tuning'in pratik etkisi yok ama planner sağlığı önemli.

### Karar metriği — ölçülen

| Metrik | Query A (geniş aggregate) | Query B (seçici) |
|--------|---------------------------|------------------|
| Plan node — baseline | Seq Scan | Index Scan Backward |
| Plan node — tuned | **Index Scan (değişti)** | Index Scan Backward (aynı) |
| Cost — baseline | 594.17 | 8.31 |
| Cost — tuned | **397.21 (%-33)** | **2.51 (%-70)** |
| Execution time — baseline | 4.885 ms | 0.819 ms |
| Execution time — tuned | 5.384 ms (+0.5 ms, marjinal) | 0.831 ms (≈ aynı) |
| Buffers — baseline | 268 hit | 292 hit |
| Buffers — tuned | 2 121 hit | 292 hit |

**Net değerlendirme:** Tuning, **selektif sorguların cost estimate'ini düzeltir** (Query B %70 düşüş) ve **planner'ın oransal kararlarını sağlıklı tutar**. Geniş tarama sorgularında plan değişikliği marjinal regresyona yol açabilir (Query A +0.5 ms) ama bu pattern Streamlit/Grafana panel sorguları değil — production workload selektif, dim+fact join + time-window filter ağırlıklı.

> Cost değerlerinin oranı tam olarak `4.0 / 1.1 ≈ 3.64` değil çünkü planner cost = `cpu_tuple_cost × rows + io_cost`; bizim sorgularımızda io_cost dominant olduğu için ratio yaklaşık ama özdeş değil.

## dim_time seed runtime

| Metrik | Beklenen | Ölçülen (2026-06-04) |
|--------|----------|----------------------|
| Toplam satır | 17 544 (731 gün × 24 saat) | **17 544 ✓** (`_build_rows` unit test + integration sayım) |
| Batch size | 1 000 (`_BATCH_SIZE`) | sabit, modül-level constant |
| Integration suite süresi | ≤ 90 s | **123.6 s** (5 migration 0005 + 9 seed = 14 senaryo, testcontainers cold-start + 9 reset+migrate dahil) |
| Idempotency | ilk run inserted=17544/updated=0; ikinci run inserted=0/updated=17544 | ✓ `test_second_run_is_idempotent` PASS |
| Holiday flag — resmi (29 Ekim) | 24/24 saat is_holiday=true | ✓ `test_official_holiday_all_24_hours_flagged` PASS |
| Holiday flag — dini (Ramazan 30 Mart 2025) | 24/24 saat is_holiday=true | ✓ `test_religious_holiday_all_24_hours_flagged` PASS |
| Mevsim sınırları (Feb/Mar, Aug/Sep) | winter→spring, summer→autumn flip | ✓ `test_season_boundary_*` PASS |

> Integration test her senaryoda public schema'yı drop + migrate (`_reset_and_migrate`) ediyor — 9 reset × ~13 s ≈ 117 s. Seed kendisinin runtime'ı ~3-4 s; cold-start (~8 s) + reset maliyeti baskın. Production'da seed tek seferlik (`make seed-time`), bu maliyet test artifaktıdır.

## `v_hourly_aqi` CONCURRENTLY refresh smoke

Sprint 4 T6'da `v_hourly_aqi` matview UNIQUE index'i (`ix_v_hourly_aqi_pk`)
ile birlikte yaratıldı (`CONCURRENTLY` refresh prereq'i). Sprint 5'te
matview üzerinde **yeni şema değişikliği yok**. H4 testleri zaten
boş ve initial-populate sonrası `REFRESH MATERIALIZED VIEW CONCURRENTLY`
çağrısının hata vermediğini doğruluyor:

* `test_migration_0004.py::TestMigration0004Integration::test_v_hourly_aqi_refreshes_when_empty` — boş matview üzerinde non-concurrent REFRESH.
* `test_migration_0004.py::TestMigration0004Integration::test_v_hourly_aqi_concurrent_refresh_after_initial` — initial populate sonrası `REFRESH ... CONCURRENTLY`.

H7 streaming gerçek veri yazdığında refresh tetiği `data_quality_runs` tablosuna duration kaydı bırakacak.

## Coolify managed PG'ye uygulama notu (TD-15)

0005 migration runner ile Coolify managed PG'ye **otomatik
uygulanmaz** — Sprint 4 sözleşmesi: managed PG'ye migrate hook'u
TD-15 kapsamında H10'da kurulur. Bu süre boyunca:

```bash
# 1. Migration'ı uygula
psql "$DATABASE_URL_COOLIFY" -v ON_ERROR_STOP=1 \
     -f infra/migrations/0005_planner_tuning.sql
psql "$DATABASE_URL_COOLIFY" -c \
     "INSERT INTO schema_migrations (version, duration_ms, checksum) \
      VALUES ('0005', 0, '<file_sha256>')"

# 2. effective_cache_size'ı VPS RAM'ine göre override (opsiyonel)
#    4 GB VPS  → '2GB' (default ile aynı, override gerekmez)
#    8 GB VPS  → '4GB'
#   16 GB VPS  → '10GB'
psql "$DATABASE_URL_COOLIFY" -c \
     "ALTER DATABASE <db_name> SET effective_cache_size = '4GB'"
```

> `random_page_cost = 1.1` SSD varsayımı her ortamda geçerli — Coolify VPS
> NVMe-backed block storage olduğu için bu parametre Coolify'da da olduğu
> gibi kalır.

## Rollback runbook

Runner `*.down.sql`'i otomatik uygulamaz (Sprint 4 T1 sözleşmesi).
Manuel rollback:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
     -f infra/migrations/0005_planner_tuning.down.sql
psql "$DATABASE_URL" -c "DELETE FROM schema_migrations WHERE version = '0005'"
```

Etki: `ALTER DATABASE ... RESET` `pg_db_role_setting` satırlarını siler;
parametreler PG global default'una (`random_page_cost = 4.0`,
`effective_cache_size = 4GB` — derleme default'ları) geri döner. **Veri
kaybı yok** — yalnız planner maliyet modeli tuning öncesi davranışa döner.

## Sprint 5 demo komutu

```bash
make migrate
psql -c "SELECT setconfig FROM pg_db_role_setting \
         WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())"
make seed-time
pytest -m integration tests/infra/test_migration_0005.py tests/infra/test_seed_dim_time.py
```

## Re-tune bench (forensic re-run)

Planner cost değerlerini değiştirmek istediğinde plan diff'i tekrar
yakalamak için:

```bash
.venv/Scripts/python.exe tests/integration/_bench_planner_tuning.py
# Çıktı: tests/integration/_artefacts/planner-tuning-diff.txt
# Süre: ~130 s (testcontainers cold-start + 312K load + 4 EXPLAIN)
```

Script `_artefacts/` altına yazar (gitignored); değerleri bu runbook'a el
ile yapıştır. Yeniden tune sonrası "Plan diff" bölümündeki kod blokları
güncellenmeli + yeni "Karar metriği" satırı eklenmeli.
