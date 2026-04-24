-- YZM536 Air Quality — Hafta 3 STUB schema
-- Owner: database-architect agent
-- Target: PostgreSQL 16+
--
-- NOT (H3): Bu dosya minimum stub'tır. csv_loader insert atabilsin ve
-- `make up` healthy çıksın diye sade tutuldu. Hafta 4'te database-architect
-- tekrar gelip aşağıdakileri ekleyecek:
--   * fact_measurements aylık RANGE partitioning (pg_partman)
--   * BRIN(time_id) + B-tree(station_id, time_id DESC) + (pollutant_id) indexleri
--   * dim_time tam takvim boyutu + UNIQUE(station_id, time_id, pollutant_id)
--   * v_hourly_aqi view, v_daily_trends materialized view
--   * data_quality_runs audit tablosu
-- Mevcut tablolar geriye-uyumlu kalacak şekilde genişletilecek.

-- =============================================================================
-- Dimensions
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_station (
    station_id   SERIAL PRIMARY KEY,
    slug         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    district     TEXT NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    category     TEXT NOT NULL,                    -- urban / suburban / rural / traffic
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_pollutant (
    pollutant_id SERIAL PRIMARY KEY,
    code         TEXT UNIQUE NOT NULL,             -- pm25, pm10, no2, so2, o3, co
    display_name TEXT NOT NULL,
    -- Birim notu: OpenWeatherMap tüm kirleticileri µg/m³ olarak verdiği için
    -- şemada da uniform µg/m³ tutuyoruz. WHO/EU CO limitleri normalde mg/m³;
    -- µg/m³'a çevrilip aşağıda seed edildi (4 mg/m³ = 4000 µg/m³).
    unit         TEXT NOT NULL DEFAULT 'µg/m³',
    who_limit    DOUBLE PRECISION,                 -- WHO 2021 guideline (µg/m³)
    eu_limit     DOUBLE PRECISION                  -- EU directive 2008/50/EC (µg/m³)
);

-- =============================================================================
-- Fact (H4'te partitioned olacak — şimdilik düz tablo)
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_measurements (
    measurement_id BIGSERIAL PRIMARY KEY,
    station_id     INT NOT NULL REFERENCES dim_station (station_id),
    pollutant_id   INT NOT NULL REFERENCES dim_pollutant (pollutant_id),
    measured_at    TIMESTAMPTZ NOT NULL,
    value          DOUBLE PRECISION NOT NULL,
    source         TEXT NOT NULL DEFAULT 'openweather',  -- openweather | csv | stream
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- Seed: dim_pollutant
-- WHO 2021 air quality guidelines + EU 2008/50/EC limits (µg/m³, yıllık ort.)
-- =============================================================================

INSERT INTO dim_pollutant (code, display_name, unit, who_limit, eu_limit) VALUES
    ('pm25', 'PM2.5',           'µg/m³',     5,    25),
    ('pm10', 'PM10',            'µg/m³',    15,    40),
    ('no2',  'NO₂',             'µg/m³',    10,    40),
    ('so2',  'SO₂',             'µg/m³',    40,   125),
    ('o3',   'O₃',              'µg/m³',   100,   120),
    -- CO limitleri kaynakta mg/m³, µg/m³'a çevrildi (×1000).
    ('co',   'CO',              'µg/m³',  4000, 10000)
ON CONFLICT (code) DO NOTHING;
