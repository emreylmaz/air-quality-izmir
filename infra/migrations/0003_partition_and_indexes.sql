-- YZM536 Air Quality — Migration 0003: partition + indexes
-- Owner: database-architect agent (H4 sprint-04 T3)
-- Target: PostgreSQL 16+
-- Depends on: 0001_baseline.sql, 0002_star_schema_expand.sql
--
-- Amaç:
--   `fact_measurements`'i aylık RANGE partition'lı tabloya dönüştürmek.
--   PG 16 partition kuralları:
--     1. Partitioned tabloda her UNIQUE/PRIMARY KEY constraint partition
--        anahtarını (`measured_at`) İÇERMEK ZORUNDA.
--     2. SERIAL/BIGSERIAL partitioned tabloda doğrudan kullanılamaz —
--        manuel SEQUENCE + `DEFAULT nextval(...)` ile çözüyoruz.
--     3. ATTACH/DETACH yerine RENAME swap kullanıyoruz ki `pg_constraint`
--        kayıtları ve dolaylı referanslar (T4 csv_loader) bozulmasın.
--
-- DROP YOK kuralı:
--   Eski tablo `fact_measurements_legacy` olarak kalır. H10'da manuel
--   temizlik runbook'u (`docs/sprints/sprint-10-cleanup.md`) yazılınca
--   düşürülür. Şimdilik veri kaybı sıfır.
--
-- Idempotency:
--   Runner her migration'ı tek transaction'da uyguluyor. Bu migration
--   idempotent DEĞİL (RENAME chain çift uygulamada fail eder); runner
--   checksum guard'ı (`_verify_no_drift`) ikinci run'da bu dosyayı zaten
--   skip ediyor. Partition CREATE'lerde `IF NOT EXISTS` KULLANMIYORUZ —
--   sprint-04 T3 acceptance: "runner halletsin, migration kendi içinde
--   idempotent değil".
--
-- Acceptance (sprint-04.md T3):
--   - 24 aylık partition (2024-01..2025-12) + default partition
--   - `EXPLAIN` 2024-06 filter ile sadece tek partition scan
--   - BRIN index size < B-tree size (sanity check)
--   - Boş tabloda migration süresi < 5 sn
--
-- Constraint adları (downstream stabilite):
--   - `fact_measurements_unique_reading` (T2'den devralındı, T4 csv_loader
--     `ON CONFLICT ON CONSTRAINT` ile referanslıyor) — KORUNMALI.
--   - `fact_measurements_pkey` — PG default; partitioned tabloda
--     `(measurement_id, measured_at)` composite olarak yeniden kuruluyor.
--   - `fact_measurements_station_id_fkey` / `fact_measurements_pollutant_id_fkey`
--     — PG default FK isimleri; yeniden kurulurken aynı isim kullanılıyor.

-- =============================================================================
-- (0) Eski tablodaki çakışacak isimleri rename et
-- =============================================================================
-- Yeni partitioned tabloda PK adı `fact_measurements_pkey`, UNIQUE adı
-- `fact_measurements_unique_reading` olacak. Aynı isimler zaten eski
-- (regular) tabloda var — PG namespace'inde index/constraint adları
-- schema-global olduğu için aynı schema'da tekrar oluşturmak fail eder.
-- Bu yüzden eski tablonun constraint'lerini önce `_legacy` prefix'ine
-- taşıyoruz; tablo rename swap'i (5) adımında yapılıyor.

ALTER TABLE fact_measurements
    RENAME CONSTRAINT fact_measurements_pkey
    TO fact_measurements_legacy_pkey;

ALTER TABLE fact_measurements
    RENAME CONSTRAINT fact_measurements_unique_reading
    TO fact_measurements_legacy_unique_reading;

-- =============================================================================
-- (1) Yeni partitioned tablo
-- =============================================================================
-- Kolon yapısı baseline + 0002 sonrası `fact_measurements`'la birebir aynı.
-- BIGSERIAL yerine açık SEQUENCE + DEFAULT nextval kullanıyoruz çünkü
-- partitioned tablolarda BIGSERIAL identity inheritance'i sürpriz yapabilir
-- (PG bug history: pg_attribute.attidentity partition'lara propagate olmuyor).

CREATE SEQUENCE fact_measurements_partitioned_measurement_id_seq
    AS BIGINT
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE fact_measurements_partitioned (
    measurement_id BIGINT NOT NULL DEFAULT nextval('fact_measurements_partitioned_measurement_id_seq'),
    station_id     INT NOT NULL,
    pollutant_id   INT NOT NULL,
    measured_at    TIMESTAMPTZ NOT NULL,
    value          DOUBLE PRECISION NOT NULL,
    source         TEXT NOT NULL DEFAULT 'openweather',
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- PG 16: partitioned tabloda PK partition key'i içermek zorunda.
    CONSTRAINT fact_measurements_pkey PRIMARY KEY (measurement_id, measured_at),
    -- TD-09 idempotency UNIQUE — measured_at zaten içinde, partition rule OK.
    -- T2'deki adı birebir koruyoruz (T4 csv_loader referansı).
    CONSTRAINT fact_measurements_unique_reading
        UNIQUE (station_id, pollutant_id, measured_at, source)
) PARTITION BY RANGE (measured_at);

-- Sequence'i tabloya bağla — DROP TABLE durumunda sequence de düşsün.
ALTER SEQUENCE fact_measurements_partitioned_measurement_id_seq
    OWNED BY fact_measurements_partitioned.measurement_id;

-- =============================================================================
-- (2) FK constraint'ler — partition tablosu seviyesinde
-- =============================================================================
-- FK'ler partition'lara otomatik propagate olur (PG 12+).

ALTER TABLE fact_measurements_partitioned
    ADD CONSTRAINT fact_measurements_station_id_fkey
    FOREIGN KEY (station_id) REFERENCES dim_station (station_id);

ALTER TABLE fact_measurements_partitioned
    ADD CONSTRAINT fact_measurements_pollutant_id_fkey
    FOREIGN KEY (pollutant_id) REFERENCES dim_pollutant (pollutant_id);

-- =============================================================================
-- (3) 24 aylık partition (2024-01 .. 2025-12) + default
-- =============================================================================
-- RANGE bound semantiği: [start, end) — start dahil, end hariç.
-- Tek tek yazmak verbose ama explicit; pg_partman ekleme yasağı (B1) gereği
-- generate_series + EXECUTE format() yerine açık DDL.

CREATE TABLE fact_measurements_2024_01 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2024-02-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_02 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-02-01 00:00:00+00') TO ('2024-03-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_03 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-03-01 00:00:00+00') TO ('2024-04-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_04 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-04-01 00:00:00+00') TO ('2024-05-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_05 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-05-01 00:00:00+00') TO ('2024-06-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_06 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-06-01 00:00:00+00') TO ('2024-07-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_07 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-07-01 00:00:00+00') TO ('2024-08-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_08 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-08-01 00:00:00+00') TO ('2024-09-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_09 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-09-01 00:00:00+00') TO ('2024-10-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_10 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-10-01 00:00:00+00') TO ('2024-11-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_11 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-11-01 00:00:00+00') TO ('2024-12-01 00:00:00+00');
CREATE TABLE fact_measurements_2024_12 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2024-12-01 00:00:00+00') TO ('2025-01-01 00:00:00+00');

CREATE TABLE fact_measurements_2025_01 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-02-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_02 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-02-01 00:00:00+00') TO ('2025-03-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_03 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-03-01 00:00:00+00') TO ('2025-04-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_04 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-04-01 00:00:00+00') TO ('2025-05-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_05 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-05-01 00:00:00+00') TO ('2025-06-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_06 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-06-01 00:00:00+00') TO ('2025-07-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_07 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-07-01 00:00:00+00') TO ('2025-08-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_08 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-08-01 00:00:00+00') TO ('2025-09-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_09 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-09-01 00:00:00+00') TO ('2025-10-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_10 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-10-01 00:00:00+00') TO ('2025-11-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_11 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-11-01 00:00:00+00') TO ('2025-12-01 00:00:00+00');
CREATE TABLE fact_measurements_2025_12 PARTITION OF fact_measurements_partitioned
    FOR VALUES FROM ('2025-12-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');

-- Default partition: range dışı tüm satırlar buraya düşer.
-- H5 streaming ingestion 2026+ veri yazmaya başlayınca buraya akar; alarm
-- niyetiyle tutuluyor — H10'da rolling partition cron'u bu tabloyu izler.
CREATE TABLE fact_measurements_default PARTITION OF fact_measurements_partitioned
    DEFAULT;

-- =============================================================================
-- (4) Veri kopya — eski tablodan yeni partitioned tabloya
-- =============================================================================
-- H3 stub'unda yazılmış olası satırlar (lokal dev veya manuel test) +
-- H4 T2 sonrası csv_loader smoke yüklemeleri. Boş tabloda 0 row.
-- Sequence'i mevcut MAX(measurement_id)'ya senkronize et ki yeni INSERT'ler
-- çakışmasın.

INSERT INTO fact_measurements_partitioned (
    measurement_id, station_id, pollutant_id, measured_at, value, source, ingested_at
)
SELECT
    measurement_id, station_id, pollutant_id, measured_at, value, source, ingested_at
FROM fact_measurements;

-- Sequence'i ileri sar. setval'in 3. argümanı `is_called`: true → next nextval
-- arg + 1 verir (bizim istediğimiz). Eski tablo boşsa MAX NULL döner; bu
-- durumda sequence başlangıç değerinde kalsın diye COALESCE.
SELECT setval(
    'fact_measurements_partitioned_measurement_id_seq',
    COALESCE((SELECT MAX(measurement_id) FROM fact_measurements_partitioned), 1),
    (SELECT MAX(measurement_id) IS NOT NULL FROM fact_measurements_partitioned)
);

-- =============================================================================
-- (5) Swap — eski tabloyu _legacy yap, yeniyi gerçek isme al
-- =============================================================================
-- Sırası önemli: önce eski → _legacy, sonra _partitioned → gerçek isim.
-- Aksi takdirde aynı isimde iki tablo olur (PG fail eder ama net hata).

-- Eski tablonun PK + UNIQUE constraint adları (0) adımında zaten `_legacy_*`
-- prefix'ine taşındı; burada yalnızca tablo isimlerini swap ediyoruz.
ALTER TABLE fact_measurements RENAME TO fact_measurements_legacy;
ALTER TABLE fact_measurements_partitioned RENAME TO fact_measurements;

-- =============================================================================
-- (6) Index'ler — partition tablosu üzerinde
-- =============================================================================
-- Partitioned tabloda CREATE INDEX otomatik olarak her partition'a yansır
-- (PG 11+). BRIN: zaman serisi append-only workload için ideal — block range
-- summary, B-tree'den 100x küçük; range scan'de hâlâ partition pruning sonrası
-- partition içi block atlaması sağlıyor.

-- BRIN: aylık partition içinde measured_at append-only (correlated) →
-- pages_per_range default (128) yeterli; tuning H10 perf sprint'te.
CREATE INDEX fact_measurements_measured_at_brin
    ON fact_measurements
    USING BRIN (measured_at);

-- B-tree composite: (station_id, measured_at DESC) — "son 24 saat şu istasyon"
-- query pattern'i için index-only scan adayı.
CREATE INDEX fact_measurements_station_time_idx
    ON fact_measurements (station_id, measured_at DESC);

-- B-tree pollutant: kirletici-bazlı raporlar (`v_hourly_aqi`'da heavy join).
CREATE INDEX fact_measurements_pollutant_idx
    ON fact_measurements (pollutant_id);

-- =============================================================================
-- (7) Sanity log — BRIN ve B-tree size karşılaştırma
-- =============================================================================
-- Boş tabloda her iki index ~16 KB (sayfa başına minimum). Asıl fark veri
-- yüklendikten sonra: 312K satır ile BRIN ~24 KB, B-tree ~9 MB beklenir.
-- T8 perf testinde dokümante edilecek.

DO $$
DECLARE
    brin_size BIGINT;
    btree_size BIGINT;
BEGIN
    SELECT pg_relation_size('fact_measurements_measured_at_brin') INTO brin_size;
    SELECT pg_relation_size('fact_measurements_station_time_idx') INTO btree_size;
    RAISE NOTICE 'index_sizes brin=% btree=%', brin_size, btree_size;
END
$$;
