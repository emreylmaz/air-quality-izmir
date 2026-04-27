-- YZM536 Air Quality — Migration 0003: ROLLBACK
-- Owner: database-architect agent (H4 sprint-04 T3)
-- Companion of: 0003_partition_and_indexes.sql
--
-- ÖNEMLİ: Runner (`infra/migrations/run.py`) `*.down.sql` dosyalarını
-- otomatik UYGULAMAZ. Manuel rollback için:
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f infra/migrations/0003_partition_and_indexes.down.sql
--     psql "$DATABASE_URL" -c \
--          "DELETE FROM schema_migrations WHERE version = '0003';"
--
-- Veri kaybı uyarısı:
--   - 0003 sonrası yeni partitioned `fact_measurements`'e yazılan satırlar
--     KAYBOLUR. Rollback önce bu satırları `_legacy`'e geri kopyalamayı
--     dener; satırlar partition aralığı dışındaysa (`fact_measurements_default`
--     içinde) yine kopyalanır — değer-uyumluluğu kontrol edilmez.
--   - Sequence `fact_measurements_partitioned_measurement_id_seq` düşer;
--     eski tablo orijinal `fact_measurements_measurement_id_seq`'i kullanır
--     (baseline'da BIGSERIAL ile otomatik oluşmuştu) — aynı kalır.
--
-- Idempotency:
--   Tüm DROP'lar `IF EXISTS` formunda. RENAME'ler ise yalnızca tablo
--   varsa çalışsın diye DO block içinde sarılı. Bu sayede yarım kalmış
--   bir rollback yeniden çalıştırıldığında hata vermez.

-- =============================================================================
-- (1) Yeni partitioned tablodan eski tabloya geri kopya (varsa)
-- =============================================================================
-- Eğer 0003 uygulandıysa `fact_measurements` partitioned'dır ve `_legacy`
-- da var. Önce yeni veriyi legacy'e taşı, sonra swap.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'fact_measurements_legacy'
    ) AND EXISTS (
        SELECT 1
        FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'fact_measurements'
    ) THEN
        INSERT INTO fact_measurements_legacy (
            measurement_id, station_id, pollutant_id, measured_at, value, source, ingested_at
        )
        SELECT measurement_id, station_id, pollutant_id, measured_at, value, source, ingested_at
        FROM fact_measurements
        ON CONFLICT DO NOTHING;
    END IF;
END
$$;

-- =============================================================================
-- (2) Yeni partitioned tabloyu (ve tüm partition'larını) düşür
-- =============================================================================
-- DROP TABLE ... CASCADE partitioned parent'ı düşürünce tüm partition'lar
-- da gider; sequence ALTER OWNED BY ile bağlı olduğu için o da gider.

DROP TABLE IF EXISTS fact_measurements CASCADE;

-- Sequence ayrıca gitmediyse (CASCADE almadıysa) garanti için.
DROP SEQUENCE IF EXISTS fact_measurements_partitioned_measurement_id_seq;

-- =============================================================================
-- (3) _legacy tabloyu eski adına geri al
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'fact_measurements_legacy'
    ) THEN
        ALTER TABLE fact_measurements_legacy RENAME TO fact_measurements;
        ALTER TABLE fact_measurements
            RENAME CONSTRAINT fact_measurements_legacy_unique_reading
            TO fact_measurements_unique_reading;
        ALTER TABLE fact_measurements
            RENAME CONSTRAINT fact_measurements_legacy_pkey
            TO fact_measurements_pkey;
    END IF;
END
$$;
