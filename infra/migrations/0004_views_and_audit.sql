-- YZM536 Air Quality — Migration 0004: views + DQ audit table
-- Owner: database-architect agent (H4 sprint-04 T6)
-- Target: PostgreSQL 16+
-- Depends on: 0001_baseline.sql, 0002_star_schema_expand.sql, 0003_partition_and_indexes.sql
--
-- Amaç:
--   Sunum/raporlama katmanı için iki view (biri materialized) ve H12 DQ
--   framework'ün dolduracağı `data_quality_runs` audit tablosunu açmak.
--
-- İçerik:
--   (a) `v_hourly_aqi`  — MATERIALIZED VIEW (`WITH NO DATA`). station ×
--       pollutant × saat granülerite. AQI sütunu şimdilik
--       `NULL::NUMERIC` placeholder; H7'de Spark streaming batch sonrası
--       gerçek AQI değeri yazılır + `REFRESH MATERIALIZED VIEW
--       CONCURRENTLY v_hourly_aqi` tetiklenir.
--   (b) `v_daily_trends` — regular VIEW. (station, pollutant, gün) per
--       MIN/MAX/AVG/COUNT. Hot path; planner cache'leyebilsin diye
--       materialized değil (24 ay × 6 istasyon × 6 pollutant ≈ 26K row
--       agregasyonu — runtime kabul edilebilir).
--   (c) `data_quality_runs` — DQ suite çalışmalarının audit tablosu.
--       BIGSERIAL PK, JSONB payload. H12'de Great Expectations/custom
--       suite dolduracak. `payload` default `{}`.
--   (d) GRANT'ler — `app_reader` SELECT on view + audit tablo,
--       `app_writer` INSERT on audit tablo. Roller migration time'da
--       mevcut OLMAYABİLİR (testcontainers, fresh PG'de yok); bu yüzden
--       her GRANT bloğu `pg_roles` lookup'lı `DO $$` guard'ı içinde.
--
-- Idempotency:
--   `CREATE MATERIALIZED VIEW IF NOT EXISTS`, `CREATE OR REPLACE VIEW`,
--   `CREATE TABLE IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`.
--   GRANT'ler PG'de zaten idempotent (aynı GRANT iki kez fail etmez).
--
-- Ret kriterleri (sprint-04.md):
--   - DROP yok ✓
--   - CREATE EXTENSION yok ✓
--   - Hardcoded credential yok ✓
--   - Idempotent ✓

-- =============================================================================
-- (a) v_hourly_aqi — MATERIALIZED VIEW
-- =============================================================================
-- WITH NO DATA: ilk yaratımda boş; ilk REFRESH'te populate olur. Fact
-- tablosu boş bile olsa REFRESH hata vermez (0 row üretir).
--
-- AQI hesabı NULL placeholder — H7'de Spark streaming sub-index hesabını
-- başka bir tabloya yazınca burada coalesce edilecek. Şimdi yapısal
-- sözleşme dondurulur ki Grafana/Streamlit dashboard'ları bu kolonları
-- erkenden referans alabilsin.
--
-- CONCURRENTLY refresh için PG'nin zorunlu kıldığı UNIQUE INDEX (a)'dan
-- hemen sonra tanımlanıyor. Index olmadan `REFRESH ... CONCURRENTLY`
-- ERROR atar.

CREATE MATERIALIZED VIEW IF NOT EXISTS v_hourly_aqi AS
SELECT
    -- dim_time anahtarı — H5'te dim_time seed'iyle eşleşir, şimdilik
    -- saat-hassasiyetli measured_at üzerinden YYYYMMDDHH formülüyle
    -- türetiliyor. EXTRACT'i doğrudan f.measured_at üzerinde çağırırsak
    -- PG GROUP BY functional-dependency'sini tanımıyor — bu yüzden
    -- agregat-sonrası `hour_ts` üzerinden hesap.
    (EXTRACT(YEAR  FROM date_trunc('hour', f.measured_at))::INT * 1000000)
    + (EXTRACT(MONTH FROM date_trunc('hour', f.measured_at))::INT * 10000)
    + (EXTRACT(DAY   FROM date_trunc('hour', f.measured_at))::INT * 100)
    + EXTRACT(HOUR  FROM date_trunc('hour', f.measured_at))::INT
        AS time_id,
    date_trunc('hour', f.measured_at)        AS measured_at,
    f.station_id,
    s.slug                                   AS station_slug,
    f.pollutant_id,
    p.code                                   AS pollutant_code,
    AVG(f.value)                             AS value,
    -- AQI sub-index placeholder. H7 streaming write path:
    --   UPDATE v_hourly_aqi SET aqi = ... -- yapamayız (matview),
    -- bunun yerine H7'de ayrı tablo (`fact_aqi_hourly`) tutup matview'i
    -- LEFT JOIN ile yeniden tanımlıyoruz. O migration 0007'de gelir.
    NULL::NUMERIC                            AS aqi
FROM fact_measurements f
JOIN dim_station   s ON s.station_id   = f.station_id
JOIN dim_pollutant p ON p.pollutant_id = f.pollutant_id
GROUP BY
    f.station_id, s.slug,
    f.pollutant_id, p.code,
    date_trunc('hour', f.measured_at)
WITH NO DATA;

-- CONCURRENTLY refresh prerequisite — composite UNIQUE INDEX matview'in
-- "logical primary key"'i. Sıra önemli: column'ler GROUP BY ile bire-bir
-- aynı olmalı, aksi takdirde refresh sırasında "could not find
-- corresponding row" hatası olur.
CREATE UNIQUE INDEX IF NOT EXISTS ix_v_hourly_aqi_pk
    ON v_hourly_aqi (station_id, pollutant_id, measured_at);

-- =============================================================================
-- (b) v_daily_trends — regular VIEW
-- =============================================================================
-- CREATE OR REPLACE VIEW idempotent: ikinci apply'da kolon listesi aynıysa
-- no-op, değişmişse view'i günceller. fact_measurements'i partitioned
-- olduğu için planner partition pruning yapabilir (date_trunc'u immutable
-- function'a sardığı sürece).

CREATE OR REPLACE VIEW v_daily_trends AS
SELECT
    date_trunc('day', f.measured_at)::DATE AS day,
    f.station_id,
    f.pollutant_id,
    MIN(f.value)   AS min_value,
    MAX(f.value)   AS max_value,
    AVG(f.value)   AS avg_value,
    COUNT(*)       AS sample_count
FROM fact_measurements f
GROUP BY
    date_trunc('day', f.measured_at)::DATE,
    f.station_id,
    f.pollutant_id;

-- =============================================================================
-- (c) data_quality_runs — audit table
-- =============================================================================
-- H12 DQ framework dolduracak. Şema BIGSERIAL PK + run_at default now()
-- + counters + JSONB payload. Payload her suite'in kendi formatında
-- detayları (failed check listesi, threshold değerleri, vb.) tutar.

CREATE TABLE IF NOT EXISTS data_quality_runs (
    run_id        BIGSERIAL PRIMARY KEY,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    suite_name    TEXT        NOT NULL,
    total_checks  INT         NOT NULL,
    passed        INT         NOT NULL,
    failed        INT         NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- Hot query pattern: "show me last 30 runs of suite X". (suite_name,
-- run_at DESC) composite ile single index seek + ordered scan.
CREATE INDEX IF NOT EXISTS ix_dqr_suite_run_at
    ON data_quality_runs (suite_name, run_at DESC);

-- =============================================================================
-- (d) GRANT'ler — role guard'lı
-- =============================================================================
-- Roller `infra/postgres/init.sql`'de yaratılır (Coolify deploy hook'u
-- çalıştırır). Migration'lar lokalde testcontainers'da bu init script'i
-- olmadan çalışır → role'ler yok. `pg_roles` lookup'ı ile guard ediyoruz
-- ki migration her ortamda çalışsın.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_reader') THEN
        GRANT SELECT ON v_hourly_aqi       TO app_reader;
        GRANT SELECT ON v_daily_trends     TO app_reader;
        GRANT SELECT ON data_quality_runs  TO app_reader;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_writer') THEN
        GRANT SELECT, INSERT, UPDATE ON data_quality_runs TO app_writer;
        GRANT USAGE, SELECT ON SEQUENCE data_quality_runs_run_id_seq TO app_writer;
        -- View'lara writer'ın UPDATE'ı anlamlı değil (matview UPDATE
        -- alamaz, regular view tek-tablo değil); SELECT yeter.
        GRANT SELECT ON v_hourly_aqi   TO app_writer;
        GRANT SELECT ON v_daily_trends TO app_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT SELECT ON v_hourly_aqi       TO grafana_ro;
        GRANT SELECT ON v_daily_trends     TO grafana_ro;
        GRANT SELECT ON data_quality_runs  TO grafana_ro;
    END IF;
END
$$;
