# YZM536 — Hava Kalitesi İzleme (Hybrid Deployment)

## Proje Özeti
İzmir hava kalitesi istasyonlarından gerçek zamanlı + tarihsel veri toplayan
Kafka + Spark + PostgreSQL pipeline. Grafana/Streamlit ile sunum.

**Ders:** YZM536 Data Engineering · **Teslim:** H8 %40 + H16 %60

## Mimari
1. **Ingestion** — OpenWeatherMap API → Python → Kafka
2. **Processing** — Kafka → Spark Structured Streaming → PostgreSQL
3. **Storage** — PostgreSQL 16 star schema
4. **Presentation** — Grafana + Streamlit

Detaylar: `docs/MIMARI.md` · Plan: `docs/PROJE_PLANI.md`

## Deploy Stratejisi (Hybrid)

**Local Docker Compose** (`infra/docker-compose.local.yml`) — Tüm stack, dev+demo:
- Kafka (KRaft), Spark master+worker, PostgreSQL, Grafana, Streamlit, API collector

**Coolify Production** (API ile provision) — Stateless katman:
| Coolify Kaynağı | Tip | Kaynak |
|-----------------|-----|--------|
| `air-quality-db` | PostgreSQL 16 (managed) | `POST /api/v1/databases/postgresql` |
| `air-quality-grafana` | Grafana service template | `POST /api/v1/services` |
| `aqi-streamlit` | Public GitHub app | `POST /api/v1/applications/public` |
| `aqi-ingestion` | Public GitHub app | `POST /api/v1/applications/public` |
| `aqi-kafka` (ops.) | Docker Compose | `POST /api/v1/services` (custom compose) |

**Local kalır (Coolify'a girmez):**
- Spark master + worker (streaming state, resource-intensive)
- Streaming job (`spark_streaming.py`) — local submit

**Neden bu bölünme?**
- Stateful streaming workload Coolify app lifecycle'ına (restart, redeploy)
  uygun değil — checkpoint state kaybı riski
- Spark cluster 2-3 GB RAM daha ister — VPS maliyeti artar
- Stateless servisler (Streamlit, API collector) git-push deployment için ideal
- PostgreSQL managed olunca backup/restore Coolify'a delege edilir

## Secret Management Policy

**Kural:** Hiçbir secret git repo'suna girmez.

### Local Dev
- `~/.config/air-quality/coolify.env` — Coolify API token (Claude Code/CI okur)
- `.envrc` (gitignored, `direnv` ile yüklenir) — Local docker compose değişkenleri
- `.envrc.example` — Template, commit edilir

`.envrc` örnek (gitignored):
```bash
# direnv loads on cd
source_env_if_exists ~/.config/air-quality/coolify.env
export DATABASE_URL="postgresql://app:local_dev_pw@localhost:5432/air_quality"
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
export OPENWEATHER_API_KEY="replace_me_from_1password"
export APP_ENV="local"
```

### Coolify Production
- **Magic Variables:** `SERVICE_PASSWORD_*`, `SERVICE_URL_*_<PORT>`, `SERVICE_FQDN_*`
  değerlerini Coolify otomatik üretir — `provision.py` bunları referanslar, üretmez
- **Custom secrets** (OpenWeatherMap API key): `infra/coolify/sync_secrets.py`
  ile local `~/.config/air-quality/secrets.env` (gitignored) dosyasından API
  üzerinden push
- **Preview deployments** için ayrı env scope'u — provision.py bunu yönetir

### Token Güvenliği
- `CoolifyClient.__repr__` token'ı maskeler (`token=***`)
- Log'lara token yazılmaz (request URL loglanır, Authorization header değil)
- `detect-secrets` pre-commit hook ile yanlışlıkla commit engellenir

### CI/CD (GitHub Actions)
- `COOLIFY_API_TOKEN` GitHub Secret olarak saklanır
- PR merge sonrası `provision apply --diff-only` çalışır
- Workflow'larda `echo $TOKEN` yasak — `::add-mask::` kullan

### `httpx` Access Log Policy (TD-07)

`httpx.AsyncClient` default'unda HTTP request/response için INFO-level
access log emit **etmez** — bu projede güvenli durum. Ancak 3rd-party
middleware eklenirse (örn. `opentelemetry-httpx`, `httpx-request-id`),
URL query string'inde `appid=<API_KEY>` sızabilir. Yeni middleware
eklemeden önce:

1. Middleware'in URL path/query'yi log'layıp log'lamadığını kontrol et.
2. Log'luyorsa `_mask_url(url)` filter'ını middleware'e enjekte et veya
   logger seviyesini WARNING'e indir.
3. `tests/ingestion/test_api_collector.py:test_url_masking_in_logs` benzeri
   bir contract testi ekle.

`api_collector._request_with_retry` zaten `safe_url = _mask_url(url)` ile
log'luyor — uygulama tarafında risk yok. Bu policy 3rd-party kütüphane
güncellemelerinde unutulmasın.

## Teknik Stack
- **Runtime:** Python 3.11+, PySpark 3.5.1
- **Streaming:** Bitnami Kafka 3.7 (KRaft)
- **DB:** PostgreSQL 16.4 (Coolify managed veya local container)
- **Viz:** Grafana 11.x, Streamlit 1.40+
- **IaC:** Python `infra/coolify/` scripts (Terraform değil — overkill)
- **Dev:** direnv, pre-commit, detect-secrets, Docker Desktop

## Komutlar
```bash
# Local dev
make up                     # docker compose up
make down
make test
make lint

# Coolify (requires ~/.config/air-quality/coolify.env)
make coolify-plan           # dry-run, diff göster
make coolify-apply          # gerçek provisioning
make coolify-status         # tüm kaynakların health'i

# Deploy manuel (genelde lazım değil, git push otomatik tetikler)
python -m infra.coolify.provision deploy streamlit

# Secret sync (nadir — genelde magic variables yeter)
python -m infra.coolify.sync_secrets push --file ~/.config/air-quality/secrets.env
python -m infra.coolify.sync_secrets pull --app aqi-streamlit
```

## Agent Takımı (11)
- **tech-lead** — Sprint plan, review (code yazmaz)
- **data-engineer** — API collector, Kafka producer
- **spark-engineer** — PySpark batch+streaming, AQI calc
- **database-architect** — Star schema, SQL optimization
- **devops-engineer** — Docker, local compose, CI
- **coolify-engineer** — Coolify API provisioning (YENİ)
- **analytics-engineer** — Grafana, Streamlit
- **data-quality-engineer** — Tests, DQ framework
- **ml-engineer** — Feature eng, forecasting (H14-15)
- **security-compliance** — KVKK, secrets audit (H11)
- **technical-writer** — README, rapor

## Agent Koordinasyonu (Hybrid-specific)
- Yeni Coolify kaynağı gerekiyorsa: `coolify-engineer` → `provision.py` update
- DB schema değişikliği: `database-architect` → migration yaz → `coolify-engineer`
  → Coolify PostgreSQL'e psql ile uygula
- Streamlit feature: `analytics-engineer` → commit push → Coolify auto-deploy
- Env variable değişikliği: `coolify-engineer` → `sync_secrets.py push`
  (manuel UI tıklama yasak — audit trail yok olur)

## Git Commit Convention (Conventional Commits)

Bu projedeki tüm commit'ler [Conventional Commits 1.0](https://www.conventionalcommits.org/)
formatına uyar. Subject line **İngilizce**, body (opsiyonel) Türkçe olabilir.

**Format:**
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Type** (zorunlu, lowercase):
| Type | Kullanım |
|------|----------|
| `feat` | Yeni özellik (kullanıcıya görünen) |
| `fix` | Bug fix |
| `docs` | Sadece dokümantasyon (README, CLAUDE.md, docs/*) |
| `refactor` | Davranış değiştirmeyen kod düzenleme |
| `test` | Test ekleme/düzenleme (prod kod değişmez) |
| `chore` | Build, dependency, config — user-facing değişiklik yok |
| `ci` | CI/CD (GitHub Actions, pre-commit) |
| `perf` | Performans iyileştirmesi |
| `style` | Formatlama (ruff, black) — semantik değişiklik yok |
| `build` | Paketleme, pyproject.toml, Dockerfile |
| `revert` | Önceki commit'i geri alma |

**Scope** (opsiyonel, kebab-case): `ingestion`, `coolify`, `spark`, `db`, `grafana`,
`streamlit`, `dq`, `ml`, `infra`, `docs`. Birden fazla modül etkileniyorsa scope'u atla.

**Subject kuralları:**
- İmperatif ("add", "fix", değil "added"/"adds"/"fixes")
- Lowercase başlat, sonda nokta yok
- Max 72 karakter
- Problemi değil değişikliği tarif et

**Body (opsiyonel):** Neden'i açıkla, ne'yi değil (diff zaten ne'yi gösteriyor).
Türkçe olabilir. Her satır max 72 char.

**Footer:**
- Breaking change: `BREAKING CHANGE: <açıklama>` veya type'a `!` ekle (`feat!:`)
- Issue ref: `Refs: #12`, `Closes: #34`
- `Co-Authored-By: Claude ...` trailer'ı **asla** ekleme (bkz. `feedback_no_claude_coauthor.md`)

**Örnekler:**
```
feat(ingestion): add kafka producer for openweather responses
fix(coolify): handle empty ports_exposes for background workers
docs: add sprint 3 pickup notes to CLAUDE.md
chore(deps): bump confluent-kafka to 2.5.0
refactor(spark)!: split aqi calculation into pure function

BREAKING CHANGE: aqi_calc.compute() artık Row yerine dict döndürüyor.
```

**Pre-commit hook (ileride):** `commitizen` veya `conventional-pre-commit`
eklenecek (TODO). Şimdilik elle uyulacak.

## Anti-Patterns
- ❌ `.env` içine gerçek secret — `direnv` kullan, kişisel secret manager'dan
- ❌ Coolify UI'dan manuel env variable eklemek — idempotency bozulur
- ❌ Password manuel üretmek — Magic Variables kullan
- ❌ Spark streaming job'ını Coolify'a deploy — lifecycle uyumsuz
- ❌ Tarihsel veriyi Kafka'ya push — doğrudan PostgreSQL (batch kanal)
- ❌ Conventional Commits formatını bypass etme — her commit `type(scope): subject` kalıbına uyar

## Mevcut Durum (pickup notes)

**Son güncelleme:** 2026-04-28 — Sprint 4 kapandı (10/10 ✅), security audit
PASS sıfır finding, 312K satır perf testi 52.6 sn. H8 ara raporu draft hazır
(`docs/RAPOR_H8.md`, 718 satır). Sprint 5 kickoff database-architect +
data-engineer'a (dim_time `is_holiday` seed + indeks tuning).

### Sprint 4 final durumu (`docs/sprints/sprint-04.md`)
| # | Task | Durum | Notlar |
|---|------|-------|--------|
| T1 | Migration runner `infra/migrations/run.py` | ✅ | psycopg + checksum, `schema_migrations` audit, %92 coverage |
| T2 | `0002_star_schema_expand.sql` | ✅ | dim_time + dim_station genişletme + `fact_measurements_unique_reading` |
| T3 | `0003_partition_and_indexes.sql` | ✅ | 24 monthly + default partition, BRIN + 2× B-tree |
| T4 | `csv_loader` idempotent + `--station-slug` | ✅ | TD-09 + TD-10 closed, %97 coverage |
| T5 | `seed_dim_station.py` UPSERT | ✅ | 6 satır, %93 coverage |
| T6 | `0004_views_and_audit.sql` | ✅ | `v_hourly_aqi` matview + `v_daily_trends` + `data_quality_runs` |
| T7 | testcontainers integration suite | ✅ | end-to-end migration + seed + load + idempotency |
| T8 | 312K satır perf smoke test | ✅ | 52.6 sn / 5916 rows/sec, BRIN:B-tree 1:11.7 |
| T9 | Makefile `make migrate`/`make seed` + test filter | ✅ | TD-12 closed |
| T10 | Docs patch + security audit | ✅ PASS | MIMARI star schema güncel, sıfır kritik bulgu |

**Sprint 4 closeout artefaktları:**
- `docs/sprints/sprint-04-perf.md` — partition pruning EXPLAIN, BRIN/B-tree size benchmark
- `docs/sprints/sprint-04-security-audit.md` — credential leak + GRANT audit, PASS
- `docs/RAPOR_H8.md` — H8 ara rapor draft (öğrenci no + ekran görüntüleri teslim öncesi)

### Sprint 5 sıradaki — kickoff context
**Tema:** Boyut tabloları ince ayarı + indeks tuning (database-architect ana sahibi)
**Plan:** `docs/sprints/sprint-05.md` (yazılacak — `/sprint-start 5`)

**İlk handoff hatları:**
```
database-architect + data-engineer → Sprint 5 kickoff
- dim_time.is_holiday seed (TR resmi tatil API'si veya statik liste)
- random_page_cost / effective_cache_size tune (Coolify managed PG default'ları)
- v_hourly_aqi CONCURRENTLY refresh stratejisi (H7 streaming trigger için yer)
- spark-engineer paralelde TD-05 PySpark/Py3.13 kararı (H6 kickoff)
```

### Son commit'ler (origin/main HEAD `26088f1`)
```
26088f1 docs(security): add sprint 4 security audit report
24771a3 docs: update mimari with star schema partition layout and sprint 4 closeout
6b82b78 test(integration): add 312k row load performance smoke test
d36c511 chore(infra): add migrate seed make targets and pytest integration filter
9430047 test(integration): add end-to-end schema and ingestion smoke test
684b4ab feat(db): add hourly aqi materialized view, daily trends, and dq audit table
13d0bbb feat(ingestion): add idempotent csv loader with station slug lookup
75dfb33 feat(db): add dim_station seed script reading stations yaml
bbd786a feat(db): partition fact_measurements by month with brin and btree indexes
c685013 test(db): add 0002 migration integration tests for dim_time and unique constraint
```

### Tamamlanan (Sprint 1-4 kapsamı)
- ✅ Sprint 1-2: 11 subagent, 5 Coolify kaynağı canlı, secret policy + IaC
- ✅ Sprint 3: API collector + Kafka producer + CSV loader, 11/11 task, Codex C1/C2/C3 fix
- ✅ Sprint 4: Star schema (4 migration), partition + indeks, idempotency, 312K perf, security PASS

### Açık Hatırlatmalar
- `main-backup` local branch H8 teslim sonrası silinecek (TD-02)
- TD-05: PySpark/Py3.13 wheel uyumsuzluğu — H6 spark-engineer kararı
- TD-15: Coolify managed PG'ye `make migrate` deploy hook (H10) — şimdilik manuel `psql` apply
- Pre-commit detect-secrets test fixture'larında `# pragma: allowlist secret`
- `tech-debt.md` → TD-01..TD-15 kayıtlı; TD-07/09/10/12 H4'te kapatıldı (Kapatılanlar tablosunda commit hash'leriyle)
- `docs/RAPOR_H8.md` H8 teslim öncesi: öğrenci no + watermark'lı ekran görüntüleri + 12 dk demo video bağlantısı

## TODO / Açık Kararlar
- [ ] OpenWeatherMap Student Pack yanıtı — gelene kadar `respx` mock mode
- [x] ~~Coolify service template ID doğrulama: `grafana-with-postgresql`~~ ✅ doğrulandı (2026-04-23)
- [ ] Kafka Coolify custom compose mu, local-only mi (VPS RAM'e göre karar H10)
- [ ] ML model seçimi Prophet/ARIMA — H14 veri hacmine göre
- [ ] **Grafana domain fix** — sub-app FQDN'inde `:3000` port'u Caddy/Traefik routing'ini
  bozuyor (default Caddy landing dönüyor). Çözüm: Coolify UI → grafana sub-app → Domains
  → port'suz clean FQDN yaz, VEYA provision.py'ye `fqdn` field injection ekle (şu an
  atlanıyor — Coolify UUID-based default FQDN üretiyor). Hafta 13'te analytics-engineer
  Grafana'yı gerçekten kullanmaya başlarken hallet.
- [ ] **provision.py `fqdn` injection** — `apply_actions` → `ensure_public_app` çağrısı
  `fqdn` parametresini geçirmiyor. config.yaml'daki URL'ler etkisiz kalıyor. `ensure_*`
  imzalarına `fqdn` ekle + payload'a dahil et.
- [x] ~~**Sprint 4 (H4) kickoff** — migration zinciri 0001-0004, partition +
  UNIQUE + dim_time + materialized view + audit table~~ ✅ kapandı
  (HEAD `26088f1`, 10/10 task, 312K perf 52.6 sn, security audit PASS)
- [ ] **Sprint 5 (H5) kickoff** — `/sprint-start 5`: dim_time `is_holiday`
  seed (TR resmi tatil), `random_page_cost` tune, matview refresh stratejisi.
- [ ] **H8 final teslim öncesi** — `docs/RAPOR_H8.md`'ye öğrenci no, watermark'lı
  ekran görüntüleri (`docs/images/h8/`), 12 dakikalık demo video bağlantısı eklensin.
