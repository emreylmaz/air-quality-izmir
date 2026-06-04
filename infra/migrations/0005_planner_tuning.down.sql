-- YZM536 Air Quality — Migration 0005: ROLLBACK
-- Owner: database-architect agent (H5 sprint-05 T3)
-- Companion of: 0005_planner_tuning.sql
--
-- ÖNEMLİ: Runner (`infra/migrations/run.py`) `*.down.sql` dosyalarını
-- otomatik UYGULAMAZ. Manuel rollback için:
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f infra/migrations/0005_planner_tuning.down.sql
--     psql "$DATABASE_URL" -c \
--          "DELETE FROM schema_migrations WHERE version = '0005';"
--
-- Etki:
--   `ALTER DATABASE ... RESET <param>` ilgili satırı
--   `pg_db_role_setting` kataloğundan siler. Parametre o DB'ye açılan
--   yeni bağlantılarda tekrar PostgreSQL global default'una döner
--   (random_page_cost = 4.0, effective_cache_size = 4GB — derleme
--   default'ları). Veri kaybı YOK; yalnız planner maliyet modeli
--   tuning-öncesi davranışa döner. Mevcut açık bağlantılar etkilenmez.
--
-- Idempotency: `ALTER DATABASE ... RESET` parametre zaten set
-- değilse de hata vermez — yarım kalmış rollback yeniden çalışabilir.

DO $$
DECLARE
    db_name TEXT := current_database();
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I RESET effective_cache_size', db_name
    );
    EXECUTE format(
        'ALTER DATABASE %I RESET random_page_cost', db_name
    );
    RAISE NOTICE 'planner tuning reset on database=% (back to PG defaults)', db_name;
END
$$;
