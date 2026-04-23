-- YZM536 Air Quality — Star Schema
-- Owner: database-architect agent (Hafta 4-5)
-- Target: PostgreSQL 16+

-- =============================================================================
-- Dimensions
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_station (
    id           SERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,           -- external station code
    name         TEXT NOT NULL,
    district     TEXT,
    lat          NUMERIC(9, 6) NOT NULL,
    lon          NUMERIC(9, 6) NOT NULL,
    elevation_m  NUMERIC(6, 1),
    station_type TEXT,                            -- urban / suburban / rural
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_time (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL UNIQUE,
    date         DATE NOT NULL,
    hour         SMALLINT NOT NULL,
    day_of_week  SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    quarter      SMALLINT NOT NULL,
    year         SMALLINT NOT NULL,
    season       TEXT NOT NULL,                  -- winter / spring / summer / fall
    is_weekend   BOOLEAN NOT NULL,
    is_holiday   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_dim_time_ts ON dim_time (ts);

CREATE TABLE IF NOT EXISTS dim_pollutant (
    id         SERIAL PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,             -- pm25, pm10, no2, so2, o3, co
    name       TEXT NOT NULL,
    unit       TEXT NOT NULL,                     -- µg/m³ | mg/m³ | ppb
    who_limit  NUMERIC(8, 3),                    -- WHO guideline
    tr_limit   NUMERIC(8, 3)                     -- Turkish regulation limit
);

-- =============================================================================
-- Fact table (partitioned by time_id range)
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_measurements (
    id             BIGSERIAL,
    station_id     INT NOT NULL REFERENCES dim_station (id),
    time_id        BIGINT NOT NULL REFERENCES dim_time (id),
    pollutant_id   INT NOT NULL REFERENCES dim_pollutant (id),
    value          NUMERIC(10, 3) NOT NULL,
    aqi_subindex   SMALLINT,
    source         TEXT NOT NULL,                -- 'api' | 'csv' | 'stream'
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, time_id)
) PARTITION BY RANGE (time_id);

-- TODO (Hafta 4): pg_partman ile aylık partition otomasyonu.
-- Geçici — ilk partition manuel:
-- CREATE TABLE fact_measurements_2026_04 PARTITION OF fact_measurements
--   FOR VALUES FROM (...) TO (...);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_fact_time_brin
    ON fact_measurements USING BRIN (time_id);
CREATE INDEX IF NOT EXISTS idx_fact_station_time
    ON fact_measurements (station_id, time_id DESC);
CREATE INDEX IF NOT EXISTS idx_fact_pollutant
    ON fact_measurements (pollutant_id);

-- Unique constraint — no duplicates per station/time/pollutant
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_triple
    ON fact_measurements (station_id, time_id, pollutant_id);

-- =============================================================================
-- Views
-- =============================================================================

CREATE OR REPLACE VIEW v_hourly_aqi AS
SELECT
    s.id   AS station_id,
    s.name AS station_name,
    t.ts,
    p.code AS pollutant,
    m.value,
    m.aqi_subindex
FROM fact_measurements m
JOIN dim_station   s ON s.id = m.station_id
JOIN dim_time      t ON t.id = m.time_id
JOIN dim_pollutant p ON p.id = m.pollutant_id;

-- TODO (Hafta 5): v_daily_trends materialized view, REFRESH concurrently.

-- =============================================================================
-- Data Quality audit table
-- =============================================================================

CREATE TABLE IF NOT EXISTS data_quality_runs (
    id           BIGSERIAL PRIMARY KEY,
    check_name   TEXT NOT NULL,
    dimension    TEXT NOT NULL,                  -- completeness | freshness | ...
    status       TEXT NOT NULL,                  -- pass | warn | fail
    metric_value NUMERIC,
    threshold    NUMERIC,
    message      TEXT,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dq_checked_at ON data_quality_runs (checked_at DESC);
