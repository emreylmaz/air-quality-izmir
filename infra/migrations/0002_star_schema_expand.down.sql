-- YZM536 Air Quality — Migration 0002: ROLLBACK
-- Owner: database-architect agent (H4 sprint-04 T2)
-- Companion of: 0002_star_schema_expand.sql
--
-- ÖNEMLİ: Runner (`infra/migrations/run.py`) `*.down.sql` dosyalarını
-- otomatik UYGULAMAZ. Bu script ops içindir; manuel rollback'te
-- aşağıdaki sırayla psql üzerinden çalıştırılır:
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f infra/migrations/0002_star_schema_expand.down.sql
--     psql "$DATABASE_URL" -c \
--          "DELETE FROM schema_migrations WHERE version = '0002';"
--
-- Veri kaybı uyarısı:
--   - dim_time tablosu DROP olur; H5'te seed edilen satırlar gider.
--   - fact_measurements UNIQUE constraint kalkar; downstream csv_loader
--     `ON CONFLICT ON CONSTRAINT fact_measurements_unique_reading` referansı
--     hata verir — T4'te yazılan kod bu rollback sonrası çalışmaz.
--   - dim_station.elevation_m / updated_at kolonları DROP olur; mevcut
--     veriler KAYBOLUR.
--
-- Idempotent: tüm DROP'lar `IF EXISTS` formunda.

-- (c) UNIQUE constraint'i kaldır (T4 ON CONFLICT clause'u kırılır).
ALTER TABLE fact_measurements
    DROP CONSTRAINT IF EXISTS fact_measurements_unique_reading;

-- (b) dim_time'ı tamamen düşür (FK referansı yok — fact_measurements henüz
-- bu tabloya bağlanmadı, H5/H6'da bağlanacak).
DROP TABLE IF EXISTS dim_time;

-- (a) dim_station kolon kaldırma — created_at/category baseline'dan, bunlar
-- kalır; sadece 0002'nin eklediği iki kolon düşer.
ALTER TABLE dim_station
    DROP COLUMN IF EXISTS updated_at;

ALTER TABLE dim_station
    DROP COLUMN IF EXISTS elevation_m;
