-- YZM536 Air Quality — Migration 0005: planner cost parameter tuning
-- Owner: database-architect agent (H5 sprint-05 T3)
-- Target: PostgreSQL 16+
-- Depends on: 0001_baseline.sql .. 0004_views_and_audit.sql
--
-- Amaç:
--   Star schema sorgu profiline göre PostgreSQL planner maliyet
--   parametrelerini ayarlamak. Sprint 5 B2 kararı: kapsam YALNIZCA
--   `random_page_cost` ve `effective_cache_size`. `shared_buffers` ve
--   `work_mem` kapsam DIŞI — bu ikisi `postgresql.conf` / instance
--   restart gerektirir (`shared_buffers` shared-memory segment'i boot'ta
--   ayrılır), `ALTER DATABASE SET` ile session bazında değiştirilemez
--   biçimde davranmazlar; restart riski H5 scope'unda istenmiyor.
--
-- Neden `ALTER DATABASE` — `ALTER SYSTEM` DEĞİL:
--   `ALTER SYSTEM` `postgresql.auto.conf` dosyasına yazar ve süper
--   kullanıcı yetkisi ister. Coolify managed PostgreSQL'de uygulamaya
--   verilen rol süper kullanıcı DEĞİLDİR (managed servis kendi
--   `postgres` superuser'ını saklı tutar). `ALTER SYSTEM` orada
--   `ERROR: permission denied to set parameter` ile patlar.
--   `ALTER DATABASE <db> SET` ise veritabanının OWNER'ı tarafından
--   çalıştırılabilir — managed PG'de uygulama rolü kendi DB'sinin
--   sahibidir. Ayar `pg_db_role_setting` kataloğuna yazılır ve o
--   veritabanına açılan HER yeni bağlantıda otomatik uygulanır
--   (mevcut açık bağlantılar etkilenmez — yeni session'da geçerli).
--
-- Idempotency:
--   `ALTER DATABASE ... SET ...` doğası gereği idempotent — aynı
--   parametre ikinci kez set edilince `pg_db_role_setting` satırı
--   sadece üzerine yazılır, hata vermez. Runner checksum guard'ı
--   (`_verify_no_drift`) ikinci apply'da bu dosyayı zaten atlar;
--   manuel `psql -f` ile çift uygulama da güvenlidir.
--
-- DB adı:
--   `ALTER DATABASE` hedef DB adını literal ister; `current_database()`
--   ifadesi DDL'de kullanılamaz. Migration'ın hangi ortamda
--   çalıştığından bağımsız olması için `DO $$` bloğu içinde
--   `current_database()` okunup `format()` ile EXECUTE ediliyor.
--   Bu sayede testcontainers (`test`), local compose (`air_quality`)
--   ve Coolify managed (rastgele DB adı) — hepsinde aynı dosya çalışır.
--
-- Ret kriterleri (sprint-05.md):
--   - `ALTER SYSTEM` yok ✓ (yukarıda gerekçeli)
--   - DROP / DROP COLUMN yok ✓
--   - Hardcoded DSN/credential yok ✓
--   - Idempotent ✓

-- =============================================================================
-- (1) random_page_cost = 1.1
-- =============================================================================
-- Default 4.0 — dönen disk (HDD) varsayımı: rastgele bir sayfayı okumak
-- ardışık okumadan 4x pahalı. Bu projenin tüm hedef ortamları SSD/NVMe:
--   - Local dev: NVMe SSD (sprint-04-perf.md donanım baseline)
--   - Coolify VPS: managed PG, NVMe-backed block storage
-- SSD'de random/sequential okuma maliyet farkı neredeyse yok. 1.1
-- (1.0 değil — bir miktar > seq_page_cost kalsın ki tam eşitlikte
-- planner kararsızlığı olmasın) PostgreSQL topluluğunun SSD için
-- standart önerisidir. Etki: planner index scan'leri seq scan'e karşı
-- daha cazip değerlendirir — istasyon + zaman aralığı sorgularında
-- `fact_measurements_station_time_idx` B-tree scan'i seq scan'e
-- tercih edilir hale gelir.
--
-- (2) effective_cache_size
-- =============================================================================
-- Bu parametre PG'ye fiziksel bellek AYIRTMAZ — yalnızca planner'a
-- "OS page cache + shared_buffers toplamda ne kadar veri sıcak
-- tutabilir" ipucudur. Yüksek değer → index scan'ler daha ucuz
-- tahmin edilir (tekrarlı sayfa erişimlerinin cache'ten geleceği
-- varsayılır).
--
--   * LOCAL ORTAM (bu migration'ın set ettiği değer): '2GB'.
--     Local docker-compose PostgreSQL container'ına tipik olarak
--     2-4 GB ayrılır (Docker Desktop default WSL2 paylaşımı). RAM'in
--     ~%50-60'ı kuralıyla muhafazakar '2GB' seçildi. Testcontainers
--     PG'si de bu aralıkta çalışır; değer aşırı yüksek olsa bile
--     planner sadece "cache büyük" varsayar — fonksiyonel risk yok,
--     yalnızca plan seçimi etkilenir.
--
--   * COOLIFY MANAGED PG (uygulama notu — bu migration ile SET EDİLMEZ):
--     Managed instance RAM'i farklıdır. Coolify VPS profiline göre
--     toplam RAM'in ~%60'ı verilmeli. Örnek:
--         4 GB VPS  → effective_cache_size = '2GB'   (bu dosyadaki ile aynı)
--         8 GB VPS  → effective_cache_size = '4GB'
--        16 GB VPS  → effective_cache_size = '10GB'
--     Coolify'a uygulama TD-15 (H10 — managed PG'ye migration deploy
--     hook'u) kapsamında yapılır. O zamana kadar managed PG'de bu
--     parametre default'ta (genelde '4GB') kalır; uygulama notu:
--         psql "$DATABASE_URL" -c \
--           "ALTER DATABASE <db> SET effective_cache_size = '<VPS RAM * 0.6>';"
--     komutu deploy runbook'una eklenecek. random_page_cost = 1.1
--     ise SSD varsayımı her ortamda geçerli olduğu için bu migration
--     ile managed PG'de de doğru değere taşınır (apply edildiğinde).

DO $$
DECLARE
    db_name TEXT := current_database();
BEGIN
    -- random_page_cost — SSD varsayımı, tüm ortamlarda geçerli.
    EXECUTE format(
        'ALTER DATABASE %I SET random_page_cost = 1.1', db_name
    );

    -- effective_cache_size — local/testcontainers profili için '2GB'.
    -- Coolify managed PG'de VPS RAM'ine göre yukarıdaki tabloya uyarak
    -- TD-15 deploy hook'unda override edilir.
    EXECUTE format(
        'ALTER DATABASE %I SET effective_cache_size = ''2GB''', db_name
    );

    RAISE NOTICE 'planner tuning applied on database=% (random_page_cost=1.1, effective_cache_size=2GB)', db_name;
END
$$;
