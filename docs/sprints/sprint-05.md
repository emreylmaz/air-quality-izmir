# Hafta 5 Sprint Plan

## Hedef

Sprint 4'te kurulan production-grade star schema'yı **boyut tabloları seed'i
ve PostgreSQL planner ince ayarı** ile tamamlamak. İki ana eksen:

1. **`dim_time` saatlik seed** — 2024-01-01 ile 2025-12-31 arası 17 544 saatlik
   satır UPSERT'i. `season` (kuzey yarıküre 4 bucket), `dow` (PostgreSQL
   `EXTRACT(DOW)` konvansiyonu 0=Pazar..6=Cumartesi) ve `is_holiday` (TR
   resmi + Diyanet bayram katalogundan join) sütunları doldurulur. Sprint 4
   T6'da hazırlanmış `v_hourly_aqi` matview'in zaman ekseni böylece anlamlı
   hale gelir; H7 Spark streaming `fact_measurements`'a yazarken time-key
   join'i ek seed gerektirmez.
2. **Planner cost parametre tuning** — Sprint 4 perf raporundaki (`sprint-04-perf.md`)
   312 K satırlık benchmark'ta partition pruning B-tree/BRIN scan'i seçimini
   etkileyen `random_page_cost` (default 4.0 → SSD profili 1.1) ve
   `effective_cache_size` (default 4 GB → local container profili 2 GB)
   parametreleri tek bir migration ile (`0005_planner_tuning.sql`) ortama
   `ALTER DATABASE ... SET` ile yazılır. `pg_db_role_setting` kataloğuna
   yazıldığı için yeni bağlantılarda otomatik etkili.

Hafta 6 Spark batch işleme bu sprintte donmuş `dim_time` time-key kontratı
üzerine yazılır; Hafta 7 streaming `v_hourly_aqi` `REFRESH MATERIALIZED VIEW
CONCURRENTLY` tetiklerini bu sprintte konfigüre edilmiş planner profili
üzerinde test eder.

## Bağımlılık & Blocker Analizi

### Hafta 4 çıktıları — devralınanlar

| Devralınan | Durum | H5 etkisi |
|------------|-------|-----------|
| `infra/migrations/run.py` (psycopg + checksum + schema_migrations) | ✓ | `0005_planner_tuning.sql` runner ile otomatik discover edilir; yeni Python kod yok |
| `dim_time` tablosu (`0002_star_schema_expand.sql`) | ✓ stub | `is_holiday` boş; H5 seed'i UPSERT ile doldurur. Şema değişikliği yok |
| `dim_station` 6 satır seed (`seed_dim_station.py`) | ✓ | Pattern referans: `seed_dim_time.py` aynı `_mask_dsn` + pydantic validation kalıbını kullanır |
| `v_hourly_aqi` matview + UNIQUE INDEX | ✓ | `CONCURRENTLY` refresh prereq'i 0004'te tamam; H5'te ek migration yok, sadece DOCs not |
| `sprint-04-perf.md` 312K satır benchmark | ✓ | Tuning öncesi/sonrası EXPLAIN karşılaştırması için referans |
| `tech-debt.md` TD-15 (Coolify managed PG'ye migrate deploy hook) | açık | H10'a kalıyor; H5 lokalde `make migrate` ile uygulanır, Coolify tarafı manuel `psql` |

### Yeni blocker'lar (sprint başında çözüldü)

| # | Blocker | Sahip | Çözüm |
|---|---------|-------|-------|
| B1 | TR resmi tatil kaynağı: API mı, statik liste mi? | data-engineer | **Karar: statik YAML (`config/tr_holidays.yaml`)**. Runtime API çağrısı yok → secret yüzeyi sıfır; 2 yıllık 2 kaynak (2429 sayılı kanun + Diyanet takvimi) elle hazırlandı. Yenileme yıllık manuel — sprint review chore'u |
| B2 | Planner tuning kapsamı: hangi parametreler? | database-architect | **Karar: yalnız `random_page_cost` + `effective_cache_size`**. `shared_buffers` ve `work_mem` `postgresql.conf` + restart gerektirir, Coolify managed PG'de restart yetkisi belirsiz → H5 scope dışı |
| B3 | `ALTER SYSTEM` vs `ALTER DATABASE`? | database-architect | **Karar: `ALTER DATABASE`**. Coolify managed PG'de uygulama rolü superuser değil; `ALTER SYSTEM` `permission denied to set parameter` hatası verir. `ALTER DATABASE` veritabanı OWNER'ı yetkisinde — uygulama rolü kendi DB'sinin sahibidir |
| B4 | Migration runner `*.down.sql`'i otomatik uyguluyor mu? | devops-engineer | **Hayır** — Sprint 4 T1 kararı: runner sadece `NNNN_<slug>.sql` discover eder, `*.down.sql` manuel `psql -f` ile uygulanır. 0005 rollback aynı sözleşmede |

---

## Tech-Debt Envanteri (sprint kapsam haritası)

Bu sprintte **fix edilenler:** —.
Bu sprintte **yeni açılanlar:** TD-16 (yıllık tatil katalogu yenileme chore'u).
**Ertelenenler:** TD-05 (PySpark/Py3.13) → H6, TD-15 (Coolify migrate deploy hook) → H10.

| ID | Başlık | Hafta 5 ile ilişki |
|----|--------|---------------------|
| TD-16 | `config/tr_holidays.yaml` yıllık manuel yenileme | **Bu sprintte açılır** — `tech-debt.md`'ye kayıt, sprint review chore template'i (her Aralık ayında bir sonraki yıl tatillerini ekleme) |
| TD-15 | Coolify managed PG'ye `make migrate` deploy hook | Ertelendi → H10 (0005 lokalde uygulanır, managed PG'de manuel `psql -f` + `effective_cache_size` VPS RAM'ine göre override) |
| TD-05 | PySpark/Py3.13 wheel uyumsuzluğu | Ertelendi → H6 (spark-engineer ana kararı) |

Tam envanter için: `tech-debt.md`.

---

## Tasks

| # | Task | Agent | DoD | Est |
|---|------|-------|-----|-----|
| 1 | **`infra/postgres/seed_dim_time.py`** — `config/tr_holidays.yaml` oku → 2024-01-01 00:00 ile 2025-12-31 23:00 arası saatlik satır üret → `INSERT INTO dim_time (...) ON CONFLICT (time_id) DO UPDATE SET is_holiday = EXCLUDED.is_holiday, season = EXCLUDED.season RETURNING (xmax = 0)` ile UPSERT; (a) `time_id = year*1e6 + month*1e4 + day*100 + hour` formülü (B4 sprint-04 kararı); (b) `dow = (weekday() + 1) % 7` (PG `EXTRACT(DOW)` konvansiyonu); (c) `season` aralarında kuzey yarıküre meteorolojik (Dec-Feb winter, Mar-May spring, Jun-Aug summer, Sep-Nov autumn); (d) `_mask_dsn` ile DSN log sızıntısı engelle; (e) CLI: `python -m infra.postgres.seed_dim_time [--dsn URL] [--holidays-path PATH]` | data-engineer | İlk run: 17 544 inserted / 0 updated; ikinci run: 0 inserted / 17 544 updated; YAML'da bir tarih `is_holiday` değişince re-seed'de o günün 24 satırının flag'i flip; pydantic validation YAML'da bozuk `type` görünce hata; coverage ≥ %65 | 3h |
| 2 | **`config/tr_holidays.yaml`** — 2024-2025 TR resmi tatilleri (2429 sayılı kanun) + Diyanet dini bayram takvimi; her satır `{date, name, type: official|religious}` formatında; yarım gün arifeler (28 Ekim, Ramazan/Kurban arifeleri) `is_holiday=true` olarak dahil — saatlik granülerite tam gün bayrak tutar (B1 kararı) | data-engineer | YAML pydantic `HolidayEntry` modeliyle parse edilir; 2024 + 2025 toplam ~30 tarih; resmi/dini ayrımı `type` ile; YAML başında kaynak gerekçesi (kanun + Diyanet referansı) | 1h |
| 3 | **`infra/migrations/0005_planner_tuning.sql`** + `.down.sql` — (a) `ALTER DATABASE <current_database()> SET random_page_cost = 1.1` (SSD profili — sprint-04 perf raporundaki NVMe baseline'ı); (b) `ALTER DATABASE ... SET effective_cache_size = '2GB'` (local Docker container profili); (c) `DO $$ ... EXECUTE format(...) ...$$` bloğu içinde `current_database()` runtime'da çözülür — testcontainers + local + Coolify aynı dosyayı kullanır; (d) `*.down.sql` `ALTER DATABASE ... RESET` ile parametreleri PG default'a geri çevirir; (e) Managed PG için uygulama notu (VPS RAM × 0.6 → effective_cache_size) migration header'ında dokümante | database-architect | Migration apply sonrası `SELECT setconfig FROM pg_db_role_setting` → `random_page_cost=1.1` + `effective_cache_size=2GB` görünür; iki kez apply idempotent (`ALTER DATABASE SET` doğası gereği); rollback `RESET` ile katalog satırı silinir; `ALTER SYSTEM` veya `pg_settings` global yazımı yok | 2h |
| 4 | **`tests/infra/test_seed_dim_time.py`** — `test_seed_dim_station.py` pattern'i ile katmanlı: (a) **Unit**: `_mask_dsn` URI + key-value formları, `load_holidays` YAML validation (boş/bozuk/extra field), `_season_for_month` 4 bucket parametrize, `_build_rows` 17 544 sayım + endpoint + time_id formülü + dow PG konvansiyonu + holiday flag; (b) **Integration** (`@pytest.mark.integration`): testcontainers PG 16, 0001..0005 zincirini uygula, ilk seed insert+sayım, ikinci seed update-only idempotency, resmi tatil (29 Ekim) 24 saatte flag, Diyanet bayramı 24 saatte flag, mevsim sınırları (28 Şubat winter / 1 Mart spring; 31 Ağustos summer / 1 Eylül autumn), YAML edit'inden sonra holiday flip, CLI smoke `--dsn` + `main([])` Settings | data-quality-engineer | Unit suite ≥ 25 test (28 ≥ 25 ✓); coverage ≥ %65; integration testcontainers PG'de tüm bayrak ve mevsim assertion'ları geçer; suite süresi < 90 sn | 3h |
| 5 | **`tests/infra/test_migration_0005.py`** — `pg_db_role_setting` katalogundan `random_page_cost` ve `effective_cache_size` değerlerini sorgula; (a) migration apply sonrası iki parametre de set görünür; (b) ikinci apply idempotent (NOTICE log var, hata yok); (c) `*.down.sql` manuel uygulandıktan + `DELETE FROM schema_migrations WHERE version='0005'` sonrası katalog satırları kaybolur (RESET etkisi); (d) `EXPLAIN` smoke — `random_page_cost=1.1` ile dim küçük tablodaki sayım sorgusu Seq Scan değil Index Scan seçer (statistical proxy, brittle değil) | data-quality-engineer | `@pytest.mark.integration`; testcontainers PG'de 4 assertion geçer; süresi < 30 sn; `pg_db_role_setting` join'i schema_migrations'a bağlı değil — bağımsız doğrulama | 2h |
| 6 | **`Makefile` `seed-time` target + lint kapsam genişletme** — (a) `make seed-time` → `python -m infra.postgres.seed_dim_time` (help'e eklenir); (b) `make lint` ve `make format` path listesine `infra/postgres/` eklenir (Sprint 4'te eklenmemiş; `seed_dim_station.py` de tarama dışında kalıyormuş); (c) `make typecheck` aynı genişletme | devops-engineer | `make seed-time` lokal stack'te 17 544 satır UPSERT log'lar; `make lint` `infra/postgres/seed_dim_time.py` ve `seed_dim_station.py` üzerinde de çalışır; `make help` çıktısında `seed-time` görünür | 1h |
| 7 | **Sprint 5 perf doc** — `docs/sprints/sprint-05-perf.md`: (a) tuning öncesi/sonrası `pg_db_role_setting` snapshot; (b) `EXPLAIN (ANALYZE, BUFFERS)` sprint-04 perf testindeki tipik sorguda `random_page_cost=4.0` vs `1.1` plan diff'i (Bitmap Heap Scan → Index Scan tercih edilir mi?); (c) `dim_time` seed runtime (yerel ≤ 5 sn beklenir); (d) `v_hourly_aqi REFRESH MATERIALIZED VIEW CONCURRENTLY` smoke (boş matview üzerinde idempotent) | database-architect + data-quality-engineer | Markdown doc commit'lenir; EXPLAIN plan output formatı `sprint-04-perf.md` ile tutarlı; gerçek sorgu örneğinde plan tercihi değişimi gösterilir | 2h |
| 8 | **Docs + `__init__.py` güncellemesi + tech-debt** — (a) `infra/postgres/__init__.py` docstring'e `seed_dim_time` modülü eklenir; (b) `tech-debt.md` TD-16 (`tr_holidays.yaml` yıllık yenileme) açılır; (c) `CLAUDE.md` "Mevcut Durum" bölümü Sprint 5 sonuçlarına güncellenir; (d) `docs/MIMARI.md` `dim_time` `is_holiday` bayrak kaynağı paragrafı eklenir | technical-writer | `__init__.py` import path'leri korunur (`from infra.postgres import seed_dim_time` mevcut testlerde kırılmaz); TD-16 sprint review template'ine bağlı; CLAUDE.md pickup notes güncel | 1h |

**Toplam tahmin:** ~15h (Sprint 4'ten daha küçük; ana iş seed + tek migration + test'ler). %15 risk buffer dahil — TR tatil katalogunun manuel hazırlığı ve mevsim sınırlarındaki edge-case test'ler en yüksek detay-yoğunluğu.

---

## Blocker'lar (sprint başlarken çözüldü)

1. **B1: TR tatil kaynağı** — Karar: **statik YAML (`config/tr_holidays.yaml`)**. 2429 sayılı kanun (resmi) + Diyanet takvimi (dini). Runtime API çağrısı yok → secret yüzeyi sıfır, OpenWeatherMap quota stresine eklenen ek bağımlılık yok. Maliyeti: yıllık manuel yenileme (TD-16).
2. **B2: Planner tuning kapsamı** — Karar: **yalnız `random_page_cost` ve `effective_cache_size`**. `shared_buffers` (`postgresql.conf` + restart) ve `work_mem` (oturum bazlı, planner cost değil RAM bütçesi) kapsam dışı; H5 scope'unda Coolify managed PG restart riski istenmiyor.
3. **B3: `ALTER SYSTEM` vs `ALTER DATABASE`** — Karar: **`ALTER DATABASE`**. Managed PG'de uygulama rolü superuser değil; `ALTER SYSTEM` reddedilir. `ALTER DATABASE` veritabanı OWNER yetkisinde, ayar `pg_db_role_setting` kataloğuna yazılır ve yeni bağlantılarda otomatik etkili.
4. **B4: Runner `*.down.sql` davranışı** — Sprint 4 T1 kararı korunur: runner sadece `NNNN_<slug>.sql` discover eder, rollback manuel `psql -f` + `DELETE FROM schema_migrations WHERE version=...`.
5. **`make migrate` smoke** — H4 demo runbook'una eklenmişti; H5'te ek olarak `make seed-time` adımı runbook'a girer.

---

## Demo Senaryosu (Hafta 5 sonu, 8 dk)

1. **(1 dk)** `make down && make up && make migrate` — 5 migration ("Applied 1 migration: 0005 in 0.X s" tek satır).
2. **(1 dk)** `psql -c "SELECT setconfig FROM pg_db_role_setting WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())"` — `random_page_cost=1.1`, `effective_cache_size=2GB` set görünür.
3. **(1 dk)** `make seed-time` — "dim_time: 17544 inserted, 0 updated".
4. **(1 dk)** Re-run `make seed-time` — "dim_time: 0 inserted, 17544 updated" (idempotency).
5. **(1 dk)** `psql -c "SELECT count(*), bool_or(is_holiday) FROM dim_time WHERE year=2024 AND month=10 AND day=29"` → `(24, true)` (29 Ekim resmi tatil).
6. **(1 dk)** `psql -c "SELECT season, count(*) FROM dim_time GROUP BY season ORDER BY count DESC"` → 4 bucket dağılımı (winter/spring/summer/autumn).
7. **(1 dk)** `EXPLAIN ANALYZE SELECT * FROM fact_measurements WHERE measured_at >= '2024-06-01' AND measured_at < '2024-07-01' LIMIT 10` — plan output (tuning sonrası Index Scan tercihi varsa highlight).
8. **(1 dk)** `pytest -m integration tests/infra/test_seed_dim_time.py tests/infra/test_migration_0005.py` — yeşil + commit log + coverage tablosu.

---

## Agent Atama Özeti

| Agent | Task'ları | Başlangıç sırası |
|-------|-----------|------------------|
| data-engineer | 1, 2 | **İlk** — T2 (YAML katalogu) → T1 (seed script) sıralı |
| database-architect | 3, 7 | T3 paralel (seed'den bağımsız); T7 perf doc T3 + T1 merge'lendikten sonra |
| data-quality-engineer | 4, 5 | T1 + T3 merge'lendikten sonra (testcontainers integration suite'leri) |
| devops-engineer | 6 | T1 hazır olunca paralel (Makefile target seed dosyasına bağımlı değil) |
| technical-writer | 8 | PR-merge gate (tüm task'lar review aşamasında) |

---

## Sprint Çıktı Tablosu (haftalık rapor)

| Hafta | Hedef | Durum | Agent | Blocker |
|-------|-------|-------|-------|---------|
| 1-2 | Setup + Coolify provision | ✅ | tech-lead + coolify-engineer | - |
| 3 | Kafka + API + CSV loader | ✅ | data-engineer + devops-engineer | - |
| 4 | Star schema + partition + idempotency | ✅ | database-architect (ana) + data-engineer | - (Sprint 4 closeout PASS) |
| **5** | **Boyut tabloları seed + planner tuning** | **🟡 planned** | **data-engineer + database-architect** | **B1-B4 kickoff'ta çözüldü** |
| 6 | Spark batch işleme | ⏳ | spark-engineer | TD-05 PySpark/Py3.13 wheel kararı |
| 7 | Spark streaming + matview refresh trigger | ⏳ | spark-engineer + database-architect | Sprint 5 planner tuning canlı olmalı |

---

## Ret Kriterleri (PR review checklist)

- Migration `ALTER SYSTEM` veya `pg_settings` global write içeriyor → **reject** (B3 kararı ihlali; managed PG'de patlatır)
- Migration `shared_buffers` veya `work_mem` set ediyor → **reject** (B2 scope dışı; restart riski)
- Seed script `dim_time` tablosuna `DELETE` veya `TRUNCATE` çağırıyor → **reject** (idempotency UPSERT ile sağlanır, mevcut satır silinmemeli)
- `tr_holidays.yaml`'da kaynak referansı yorum olarak yok (kanun no veya Diyanet) → **reject + revize** (denetlenebilirlik kaybı)
- `_mask_dsn` benzeri DSN maskeleme yok / log'da şifre sızıyor → **hard reject + security eskalasyon**
- `mypy --strict` fail → **reject, revizyon**
- Test coverage `infra/postgres/seed_dim_time.py` < %65 → **reject (data-quality-engineer'a geri)**
- Integration test yok / sadece MagicMock → **reject** (Sprint 4 DoD standardı korunur — testcontainers PG zorunlu)
- `0005_planner_tuning.sql` rollback dosyası (`*.down.sql`) yok → **reject** (T3 DoD)
- Coolify managed PG'ye manuel `psql ALTER DATABASE` çalıştırıldı (audit trail yok) → **reject** (TD-15 deploy hook gelene kadar Coolify env'da uygulanmaz)

---

## Sonraki Adım — İlk Handoff

**Hedef:** `data-engineer` — Sprint kickoff'ta B1 onayını teyit et, T2 (YAML katalogu) → T1 (seed script) sıralı işle.

Handoff context:
> Hafta 5 sprint başlıyor. Ana hedef: Sprint 4'te kurulan `dim_time` tablosunun
> `is_holiday` ve `season` sütunlarını UPSERT seed'i ile doldurmak. **B1
> kararı: statik YAML (`config/tr_holidays.yaml`) — runtime API çağrısı yok.**
> İlk task'ın T2 — `config/tr_holidays.yaml`'i 2024 + 2025 TR resmi tatilleri
> (2429 sayılı kanun) + Diyanet dini bayram takvimi ile doldur. Her satır
> `{date: YYYY-MM-DD, name: TR-tatil-adı, type: official|religious}`. YAML
> başında kaynak yorumu zorunlu (kanun no + Diyanet referansı). T1'e geç:
> `infra/postgres/seed_dim_time.py` — `seed_dim_station.py` pattern'ini
> kopyala (`_mask_dsn`, pydantic `BaseModel`, CLI `argparse`). UPSERT SQL:
> `INSERT INTO dim_time (...) VALUES (...) ON CONFLICT (time_id) DO UPDATE
> SET is_holiday = EXCLUDED.is_holiday, season = EXCLUDED.season RETURNING
> (xmax = 0) AS inserted`. `time_id` formülü Sprint 4 B4 kararından
> (`year*1e6 + month*1e4 + day*100 + hour`). DROP yok, TRUNCATE yok.
> Conventional Commits: `feat(infra): add tr public holiday dataset for
> dim_time seed` ve `feat(db): add dim_time hourly seed with holiday and
> season derivation` ayrı commit'ler.

**Paralel handoff (kickoff):** `database-architect` → T3 (`0005_planner_tuning.sql`).
T1/T2'den bağımsız çalışabilir. T3 acceptance: `ALTER DATABASE` ile `random_page_cost=1.1` + `effective_cache_size=2GB`; `ALTER SYSTEM` yok; rollback `*.down.sql` `RESET` ile; managed PG için VPS RAM notu header'da.

**Test handoff (kickoff sonrası):** `data-quality-engineer` → T4 + T5 paralel.
T4 unit + integration suite, T5 dedicated migration testi. Sprint 4 testcontainers pattern'i korunur.

**Pre-review request:** `security-compliance` — T1 seed script'inde DSN log sızıntısı kontrolü (mevcut `_mask_dsn` kalıbı yeterli mi), T3 migration'da managed PG superuser eskalasyonu açan SQL var mı (B3 kararı).
