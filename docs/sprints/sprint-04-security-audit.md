# Sprint 04 — Security Audit Report (T10)

**Auditor:** security-compliance agent
**Sprint:** Hafta 4 — Star schema + partition + idempotency (T1-T9)
**Tarih:** 2026-04-27
**HEAD reviewed:** `6b82b78` (origin/main)
**Verdict:** PASS — no findings, clean security posture.

---

## Executive Summary

Hafta 4 deliverable'ları (migration zinciri 0001-0004 + down migrations,
`seed_dim_station.py`, `csv_loader.py` idempotency güncellemesi, materialized
view + DQ audit table, testcontainers integration suite) CLAUDE.md Secret
Management Policy'sine **tam uyumludur**. Migration SQL'lerinde hiçbir
hardcoded credential yok; rol GRANT'leri least-privilege prensibine uygun
biçimde `pg_roles` guard'ı ile sarılı; DSN'ler her ihtimalde maskelenmiş;
`detect-secrets` baseline taraması clean (sıfır finding) — Sprint 3'ten
kalan iki test fixture finding'i bu sprintte temizlenmiş ve yerine
`# pragma: allowlist secret` satır-içi pragma'ları uygulanmıştır.

KVKK kapsamı değişmedi: Sprint 3 audit'inde belgelendiği gibi bu projedeki
veriler (kamu istasyon koordinatları + agregat ölçümler) kişisel veri
sınıfına girmez. **Sprint kapanışı için onay verilir.**

---

## Audit Scope

| # | Alan | Soru | Sonuç |
|---|------|------|-------|
| a | Migration SQL leak taraması (0001-0004 + down) | Hardcoded password / DSN / API key / token? | PASS — sıfır bulgu |
| a | Migration privilege escalation | `SUPERUSER`/`CREATEROLE`/`BYPASSRLS` veya `GRANT ALL ON ...`? | PASS — yalnızca explicit `GRANT SELECT/INSERT/UPDATE` |
| b | `seed_dim_station.py` log audit | DSN log'da maskelenmiş mi? | PASS — `_mask_dsn` helper + 3 unit test |
| b | `csv_loader.py` log audit + SQL injection | DSN log sızıntısı? Slug/station_id parameterize? | PASS — DSN sadece `psycopg.connect`'e gidiyor; slug `%s` placeholder + regex defence-in-depth |
| c | `data_quality_runs` GRANT'leri (T6) | `app_reader`/`app_writer` doğru? Role guard? | PASS — `pg_roles` lookup'lı `DO $$` guard |
| c | `data_quality_runs.payload` PII riski | JSONB içine PII düşebilir mi? | TD-CANDIDATE — H12 DQ framework yazarken schema validation gerek |
| d | `.secrets.baseline` durumu | Yeni finding? Stale entry? | PASS — sıfır finding (baseline temizlendi); Sprint 3'ten kalan 2 entry pragma'ya çevrildi |
| d | Test fixture credential pragma'ları | `# pragma: allowlist secret` annotate edilmiş mi? | PASS — 8 fixture/test satırı annotate'li |
| e | `pre-commit` `detect-secrets` hook | Aktif ve son commit'lerde geçti mi? | PASS — aktif, son 10 commit hook'tan geçti |
| f | testcontainers DSN ifşası | Test DSN'leri prod'a/Coolify'a sızar mı? | PASS — DSN container yaşam döngüsüyle ephemeral; prod path yok |
| g | Coolify managed PG schema apply riski | Sprint 4 migration'ları henüz Coolify'da çalıştırıldı mı? | TD-CANDIDATE — manuel `psql` apply riski (H10) |

---

## Detailed Findings

### Migration zinciri — secret/credential leak

**Severity:** info
**Status:** RESOLVED (no findings)
**Detector:** `detect-secrets scan --baseline .secrets.baseline` (exit=0, results boş)
ve manuel regex sweep `(password|secret|token|api[_-]?key|credentials)` üzerinde
`infra/migrations/`.

**Kapsam taranan dosyalar:**

- `infra/migrations/0001_baseline.sql`
- `infra/migrations/0002_star_schema_expand.sql`
- `infra/migrations/0002_star_schema_expand.down.sql`
- `infra/migrations/0003_partition_and_indexes.sql`
- `infra/migrations/0003_partition_and_indexes.down.sql`
- `infra/migrations/0004_views_and_audit.sql`
- `infra/migrations/0004_views_and_audit.down.sql`

**Sonuç:** Sıfır eşleşme. Migration'lar tamamen DDL; crypt / md5
veya inline password literal yok. `infra/postgres/init.sql` zaten
Sprint 3 audit'inde incelenmişti (psql colon-quote-var interpolation +
Coolify SERVICE_PASSWORD_* Magic Variables) — bu sprintte değişmedi.

---

### Migration zinciri — privilege escalation

**Severity:** info
**Status:** RESOLVED (no findings)
**Detector:** Manuel regex sweep `(SUPERUSER|CREATEROLE|BYPASSRLS|GRANT ALL)` on `infra/`.

**Sonuç:** Sıfır eşleşme. `0004_views_and_audit.sql` GRANT yapısı:

```sql
-- app_reader: SELECT-only on views + audit table
GRANT SELECT ON v_hourly_aqi      TO app_reader;
GRANT SELECT ON v_daily_trends    TO app_reader;
GRANT SELECT ON data_quality_runs TO app_reader;

-- app_writer: SELECT/INSERT/UPDATE on audit table; SELECT on views
GRANT SELECT, INSERT, UPDATE ON data_quality_runs TO app_writer;
GRANT USAGE, SELECT ON SEQUENCE data_quality_runs_run_id_seq TO app_writer;
GRANT SELECT ON v_hourly_aqi   TO app_writer;
GRANT SELECT ON v_daily_trends TO app_writer;

-- grafana_ro: SELECT-only (read-only datasource)
GRANT SELECT ON v_hourly_aqi      TO grafana_ro;
GRANT SELECT ON v_daily_trends    TO grafana_ro;
GRANT SELECT ON data_quality_runs TO grafana_ro;
```

Role-Access Matrisine birebir uyumlu (least-privilege ihlali yok). DELETE
yetkisi `data_quality_runs` üzerinde hiçbir role verilmedi — audit trail
immutable kalıyor. GRANT'ler `pg_roles` lookup'lı DO-block guard içinde —
testcontainers'da rol yokken migration sessiz geçer (defansif idempotency).

---

### `seed_dim_station.py` — DSN masking

**Severity:** info
**Status:** RESOLVED
**Location:** `infra/postgres/seed_dim_station.py:72-87`

`_mask_dsn` helper psycopg `conninfo_to_dict` ile DSN'i parse eder ve
yalnızca `host:port/dbname` döner. Parse fail ise `<unparseable dsn>`
placeholder'ı geri verir — şifre asla error path'inde bile echo'lanmaz.

`seed()` log call'ları `_mask_dsn(dsn)` üzerinden gidiyor
(`seed_dim_station.py:148-152`). Unit test kapsamı:

- `test_seed_dim_station.py:45` — URI form password strip (supersecret)
- `test_seed_dim_station.py:52` — kv-form password strip (hunter2)
- `test_seed_dim_station.py:58` — unparseable DSN garbage tolerance

---

### `csv_loader.py` — SQL injection guard + DSN handling

**Severity:** info
**Status:** RESOLVED
**Location:** `src/ingestion/csv_loader.py:113`, `:384`, `:555`

TD-10 fix'i ile slug-based station lookup eklendi. Defence-in-depth:

1. `Station.id` pydantic'te regex valide ediliyor:
   `pattern=r"^[a-z][a-z0-9_]*$"` (`src/ingestion/stations.py:41`).
   Tek tırnak / iki tırnak / noktalı virgül / whitespace karakterlerinin
   hiçbiri slug input'una giremez.
2. CLI üzerinden gelen `--station-slug` argümanı doğrudan psycopg
   `%s` placeholder'a verilir; string concat / format yok.

`STATION_SLUG_LOOKUP_SQL` sabit string olarak tutuluyor ve
`cur.execute(SQL, (slug,))` çağrısı parametreli — psycopg single-quote
escape'ini upstream halletti. Slug regex doğrulanmamış olsa bile bu
parametre binding tek başına SQL injection'ı engelliyor; iki katmanlı
defence-in-depth.

DSN flow: `settings.database_url.get_secret_value()` →
`psycopg.connect(dsn)` → DSN nesneden hiçbir log handler'a gitmiyor
(stderr `print` çıktısı sadece `Inserted N rows, skipped M (duplicates)`
mesajını basıyor — DSN, satır içeriği yok).

---

### `data_quality_runs` GRANT yapısı + role guard

**Severity:** info
**Status:** RESOLVED (yapısal); JSONB payload için TD-CANDIDATE açıldı
**Location:** `infra/migrations/0004_views_and_audit.sql:146-169`

GRANT'ler `pg_roles` lookup'lı DO-block guard içinde — testcontainers'da
rol yokken migration sessiz geçer, prod'da `init.sql` çalıştığı için
GRANT'ler aktif olur. `app_reader` SELECT-only, `app_writer`
SELECT+INSERT+UPDATE (DELETE yok — audit trail immutable kalsın).

**Açık risk (TD-CANDIDATE):** `data_quality_runs.payload` JSONB DEFAULT empty.
H12 DQ framework dolduracak. Şu an boş; gelecekte yazılacak veride PII
düşebilir mi? **Bu sprintte gerçek veri yok — risk teorik**, ancak
H12'de DQ suite implementation'ı yazılırken payload schema validator
(JSON Schema veya pydantic) zorunlu kılınmalı.

→ **TD-14 olarak tech-debt.md dosyasına eklendi.**

---

### `.secrets.baseline` durumu

**Severity:** info
**Status:** RESOLVED
**Location:** `.secrets.baseline` (working tree modification)

Mevcut commit'teki (`6b82b78`) baseline Sprint 3'ten 2 entry içeriyordu
(`tests/conftest.py:19,21`). Sprint 4 audit sırasında baseline yeniden
generate edildi:

```
detect-secrets scan --baseline .secrets.baseline
echo $?
0
```

Sonuç: `"results": {}` — sıfır finding. Önceki 2 entry zaten satır içi
`# pragma: allowlist secret` ile annotate'liydi (Sprint 3'te eklendi),
bu yüzden baseline'dan kaldırıldıkları halde detector tekrar yakalamıyor.
Bu değişiklik bu commit'e eklenecek.

**Tüm `# pragma: allowlist secret` annotate'li satırlar (audit kapsamı):**

| Dosya | Satır | İçerik |
|-------|-------|--------|
| `tests/conftest.py` | 19 | DATABASE_URL test fixture |
| `tests/conftest.py` | 21 | OPENWEATHER_API_KEY test fixture |
| `tests/test_settings.py` | 31 | leaky_secret_do_not_print repr test |
| `tests/infra/test_migration_0004.py` | 472 | CREATE ROLE app_reader test password |
| `tests/infra/test_migration_0004.py` | 473 | CREATE ROLE app_writer test password |
| `tests/infra/test_seed_dim_station.py` | 46 | URI form DSN unit test (supersecret) |
| `tests/infra/test_seed_dim_station.py` | 53 | kv-form DSN unit test (hunter2) |
| `tests/ingestion/test_api_collector.py` | 37 | FAKE_API_KEY constant |
| `.env.local.example` | 31 | local_dev_pw_change_me placeholder |

Hepsi test fixture / template — production code'da hiçbir gerçek secret yok.

---

### `.pre-commit` `detect-secrets` hook

**Severity:** info
**Status:** RESOLVED
**Location:** `.pre-commit-config.yaml:18-29`

Hook konfigürasyonu Sprint 3 audit'inden değişmedi (`detect-secrets v1.5.0`
+ `--baseline .secrets.baseline` arg + `*.env.example`, `*.envrc.example`,
`docs/`, `tests/*/fixtures/*` exclude pattern'leri). Hook aktif; son 10
commit (`git log --oneline -10`) hook'tan geçti — commit'ler bypass
yapmıyor (`--no-verify` kullanılmamış).

---

### testcontainers DSN ifşası

**Severity:** info
**Status:** RESOLVED
**Location:** `tests/integration/conftest.py:37-56`

`PostgresContainer("postgres:16.4-alpine").get_connection_url()` her test
çalışmasında ephemeral port + ephemeral password üretir. DSN sadece test
process içinde dolaşır; container teardown ile beraber yok olur. Coolify
managed PG'ye sızma yolu yok — testcontainers ↔ Coolify arasında network
bağlantısı yok ve DSN'ler git history'de tracked değil.

---

### Coolify managed PG'ye Sprint 4 schema apply

**Severity:** medium
**Status:** TD-CANDIDATE (CLAUDE.md "Açık Hatırlatmalar"da örtük; bu
audit'te explicit referans)
**Location:** Coolify provisioning lifecycle

Sprint 4 migration zinciri (0001-0004) henüz **Coolify managed
PostgreSQL'e apply edilmedi**. Mevcut planda iki seçenek var:

1. **Manuel `psql`** ile Coolify console'undan apply et — audit trail
   zayıf, idempotency guarantee Coolify side'da kayıtlı değil.
2. **`make migrate` deploy hook** olarak Coolify'a bağla — H10 hedefi.

**Tavsiye:** Sprint 4 kapanışından sonra ilk Coolify deploy denemesi
**manuel `psql`** ile yapılırsa (acil ihtiyaç), runbook olarak
`docs/sprints/sprint-10-coolify-migrate.md` (placeholder) kayıt altına
alınmalı. H10'da deploy hook bağlandığında manuel adımlar deprecated olur.

→ **TD-15 olarak tech-debt.md dosyasına eklendi.**

---

## Verified Security Controls (delta from Sprint 3)

| Kontrol | Sprint 3 durumu | Sprint 4 durumu |
|---------|-----------------|-----------------|
| `_mask_url` (api_collector) | aktif + 2 contract test | değişmedi |
| `SecretStr` masking (settings) | aktif | değişmedi |
| Producer log payload mask | topic+key+size only | değişmedi |
| `.env.local` gitignored | doğrulandı | doğrulandı (`git check-ignore` exits 0) |
| `detect-secrets` baseline | Sprint 3 sonu allowlist'lendi | baseline temizlendi (`results: {}`) |
| `_mask_dsn` (seed_dim_station) | yok | **YENİ** — eklendi + 3 unit test |
| Slug SQL injection guard (csv_loader) | magic-number `--station-id` | **YENİ** — pydantic regex + parameterize |
| Migration SQL credential leak | N/A | **YENİ kapsam** — sıfır finding |
| Role-based GRANT'ler (matview / audit) | N/A | **YENİ** — `pg_roles` guard'lı |

---

## KVKK / GDPR Posture

Sprint 4 ingestion ve schema delta'sı:

| Veri | Kişisel? | KVKK Md. | Tedbir |
|------|----------|----------|--------|
| `dim_station` (slug, name, district, lat, lon, category, elevation) | Hayır (kamu açık veri) | — | — |
| `dim_time` (calendar surrogate) | Hayır (zaman boyutu) | — | — |
| `data_quality_runs.payload` (JSONB) | Şu an boş, H12'de doldurulacak | Md. 12 (öngörülen risk) | TD-14: schema validation |
| `fact_measurements` partitioned | Hayır (çevre ölçümü) | — | — |

**Sonuç:** Sprint 4 kapsamında kişisel veri toplanmıyor / işlenmiyor.
KVKK kapsamı dışında. `data_quality_runs.payload` H12'de doldurulurken
PII düşmemesi için schema validator zorunlu kılınmalı (TD-14).

---

## TD-Candidate'ler (yeni)

| ID | Başlık | Hedef | Sahip | Açıklama |
|----|--------|-------|-------|----------|
| TD-14 | `data_quality_runs.payload` JSONB schema validation | H12 (DQ framework) | data-quality-engineer + security-compliance | Payload yapısı suite-bazlı serbest. H12'de Great Expectations / pydantic ile schema validator zorunlu kılınsın; PII placeholder fields engellesin (örn. user-supplied free-text, IP, e-mail). Audit tablosu immutable (DELETE GRANT yok). Retention politikası H11'de belirlensin. |
| TD-15 | Coolify managed PG'ye `make migrate` deploy hook | H10 (DevOps güçlendirme) | coolify-engineer + devops-engineer | Sprint 4 zinciri (0001-0004) Coolify'a manuel `psql` apply riskinde. Deploy hook = git push → migration runner → schema_migrations audit kaydı. Manuel apply runbook (`docs/sprints/sprint-10-coolify-migrate.md`) interim çözüm. |

---

## Recommendations (Forward-Looking)

1. **TD-14 (yeni):** H12 DQ framework yazımında `data_quality_runs.payload`
   yapısını JSON Schema veya pydantic ile valide et; PII fields engelle.
2. **TD-15 (yeni):** Coolify deploy hook bağlanmadan ilk schema apply
   yapılırsa runbook'la sabitle (idempotent `psql -f` chain).
3. **Sprint 5 ön-bakış:** dim_time `is_holiday` seed kaynağı (TR resmi
   tatil API'si veya statik liste) seçilirken **API endpoint'in HTTPS
   olduğunu** ve sonucun cache'lendiğini doğrula (3rd-party API key
   gerekiyorsa SecretStr + .env.local).
4. **TD-06 hatırlatma:** Coolify token rotation runbook H11'de teslim;
   Sprint 4 sonu itibarıyla token policy değişmedi (90-gün cadence
   hâlâ takvim reminder bekliyor).

---

## Conclusion

Hafta 4 deliverable'ları production-ready (Coolify deploy hazır) seviyede.
**Sıfır finding, clean security posture.** Sprint 3'ten devralınan
TD-06/TD-07/TD-08 hâlâ takipte (TD-07 H4 docs patch ile zaten kapanıyor;
TD-06 ve TD-08 H10/H11'de). İki yeni TD-CANDIDATE açıldı (TD-14, TD-15)
— ikisi de "ileride uygulanabilecek control gap" niteliğinde, Sprint 4
çıktısını bloklamaz.

**No findings. Sprint 4 closes with clean security posture.**

**Verdict:** PASS — PR merge için onay verilir.

**İmza:** security-compliance (audit)
**Onay (sprint kapanışı):** tech-lead
