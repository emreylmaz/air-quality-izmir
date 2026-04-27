-- YZM536 Air Quality — Migration 0004: ROLLBACK
-- Owner: database-architect agent (H4 sprint-04 T6)
-- Companion of: 0004_views_and_audit.sql
--
-- ÖNEMLİ: Runner (`infra/migrations/run.py`) `*.down.sql` dosyalarını
-- otomatik UYGULAMAZ. Manuel rollback için:
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f infra/migrations/0004_views_and_audit.down.sql
--     psql "$DATABASE_URL" -c \
--          "DELETE FROM schema_migrations WHERE version = '0004';"
--
-- Veri kaybı uyarısı:
--   - `data_quality_runs` tablosu DROP olur; H12 DQ suite çalışma
--     geçmişi tamamen silinir. Geri alımda bu tablodaki audit veriyi
--     dışa al (`COPY data_quality_runs TO ...`) çünkü yeniden üretmek
--     mümkün değil.
--   - `v_hourly_aqi` matview'de cache'lenmiş row'lar kaybolur — H7
--     streaming bir sonraki refresh'te yeniden üretir (kayıp değil
--     ama kısa süreli boşluk).
--   - View'lar fonksiyonel değildir; DROP geri alımı sadece şema
--     temizliği.
--
-- Idempotency: tüm DROP'lar IF EXISTS formunda. Yarım kalmış bir
-- rollback yeniden çalıştırıldığında hata vermez.

-- (c) Audit tablosu (önce — view'lardan bağımsız).
DROP TABLE IF EXISTS data_quality_runs;

-- (b) Regular view.
DROP VIEW IF EXISTS v_daily_trends;

-- (a) Materialized view (UNIQUE INDEX otomatik düşer — matview'in
-- bağımlısı). DROP MATERIALIZED VIEW kullanmak zorundayız; düz
-- `DROP VIEW` matview üzerinde çalışmaz.
DROP MATERIALIZED VIEW IF EXISTS v_hourly_aqi;
