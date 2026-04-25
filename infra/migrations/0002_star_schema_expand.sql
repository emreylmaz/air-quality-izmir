-- YZM536 Air Quality — Migration 0002: star schema expand
-- Owner: database-architect agent (H4 sprint-04 T2)
-- Target: PostgreSQL 16+
-- Depends on: 0001_baseline.sql
--
-- Amaç:
--   H3 stub şemasını backward-compatible şekilde genişletmek. Bu migration
--   tablo/kolon EKLER veya CONSTRAINT ekler; mevcut nesneleri DROP etmez,
--   verileri taşımaz. Partition + index seti 0003_partition_and_indexes.sql'e
--   ertelendi (T3) — burada sadece şema sözleşmesi büyütülür.
--
-- Eklenenler:
--   1. dim_station: `elevation_m NUMERIC(6,1) NULL`, `updated_at TIMESTAMPTZ
--      DEFAULT now()` kolonları (baseline'daki category/created_at korunur).
--   2. dim_time: 9 kolonlu yeni saatlik boyut tablosu. Granülerite kararı
--      sprint-04 B4: time_id = year*1000000 + month*10000 + day*100 + hour
--      (örn. 2026042514). Holiday flag boş bırakılır; H5'te dolar.
--   3. fact_measurements: TD-09 idempotency UNIQUE constraint
--      `(station_id, pollutant_id, measured_at, source)`.
--      Constraint'e isim verildi (`fact_measurements_unique_reading`) çünkü
--      T4'te csv_loader `ON CONFLICT ON CONSTRAINT ...` referansı için stabil
--      isim gerekiyor.
--
-- Idempotency:
--   Tüm ADD COLUMN'lar `IF NOT EXISTS` ile, yeni tablo `CREATE TABLE IF NOT
--   EXISTS`, UNIQUE constraint pg_constraint lookup'lı `DO $$` block ile
--   korunur. İkinci kez apply edildiğinde NOTICE üretmeden sessiz geçer.
--
-- Ret kriterleri (sprint-04.md):
--   - DROP yok ✓
--   - CREATE EXTENSION yok ✓
--   - Hardcoded credential yok ✓
--   - Idempotent ✓

-- =============================================================================
-- (a) dim_station: kolon ekle
-- =============================================================================

-- elevation_m: rakım (metre); NULL serbest, çünkü tüm 6 İzmir istasyonu için
-- güvenilir kaynak henüz yok (H5 seed_dim_station çağrılırken doldurulabilir).
-- NUMERIC(6,1) → max 99999.9 m, sahil seviyesinin altı için negatif de mümkün.
ALTER TABLE dim_station
    ADD COLUMN IF NOT EXISTS elevation_m NUMERIC(6, 1) NULL;

-- updated_at: seed UPSERT'ler (T5: seed_dim_station.py) DO UPDATE clause'unda
-- bu kolonu now()'a setleyecek. created_at baseline'da zaten mevcut.
ALTER TABLE dim_station
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- =============================================================================
-- (b) dim_time: saatlik boyut tablosu
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_time (
    -- time_id formülü: year*1000000 + month*10000 + day*100 + hour
    -- Örn. 2026-04-25 14:00 UTC → 2026042514. INT range yeterli (max ~21M yıl).
    time_id     INTEGER PRIMARY KEY,
    measured_at TIMESTAMPTZ NOT NULL UNIQUE,
    year        SMALLINT NOT NULL,
    month       SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day         SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    hour        SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    -- dow: PostgreSQL EXTRACT(DOW FROM ...) ile uyumlu. 0=Sunday..6=Saturday.
    dow         SMALLINT NOT NULL CHECK (dow BETWEEN 0 AND 6),
    -- season: kuzey yarıküre; CHECK ile dört değere kısıtla.
    season      TEXT NOT NULL CHECK (season IN ('winter', 'spring', 'summer', 'autumn')),
    -- is_holiday: H5'te TR resmi tatil API'si veya statik liste ile UPDATE
    -- edilecek. Baseline'da false — fact join'leri bozulmasın diye NOT NULL.
    is_holiday  BOOLEAN NOT NULL DEFAULT false
);

-- =============================================================================
-- (c) fact_measurements: TD-09 UNIQUE constraint
-- =============================================================================

-- ADD CONSTRAINT'in `IF NOT EXISTS` formu PostgreSQL'de yok; pg_constraint
-- lookup'ı ile kendi idempotency sarmalımızı yazıyoruz. Constraint adı
-- downstream `ON CONFLICT ON CONSTRAINT fact_measurements_unique_reading`
-- referansı için sabit (T4 csv_loader handoff'u).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fact_measurements_unique_reading'
          AND conrelid = 'public.fact_measurements'::regclass
    ) THEN
        ALTER TABLE fact_measurements
            ADD CONSTRAINT fact_measurements_unique_reading
            UNIQUE (station_id, pollutant_id, measured_at, source);
    END IF;
END
$$;
