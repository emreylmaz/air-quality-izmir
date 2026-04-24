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

**Son güncelleme:** 2026-04-25 gece — Sprint 3 yarıda. 7/11 task DONE, kalan 4 task yarın.

### Sprint 3 ilerleme (`docs/sprints/sprint-03.md`)
| # | Task | Durum | Notlar |
|---|------|-------|--------|
| T1 | venv `[dev,ingestion]` + pre-commit | ✅ | `processing` extras skip — TD-05 PySpark/Py3.13 |
| T8 | `.env.local.example` template | ✅ | compose validate PASS |
| T2 | 6 İzmir istasyonu (Konak/Bornova/Karşıyaka/Alsancak/Bayraklı/**Aliağa**) | ✅ | `config/stations.yaml` + `src/ingestion/stations.py`, 10 test |
| T7 | `schema.sql` minimal stub (3 tablo + 6 pollutant seed) | ✅ | H4'te database-architect partition+BRIN ekleyecek |
| T3 | `api_collector.py` async httpx + tenacity retry | ✅ | 20 test, %96.23 coverage, API key masking |
| T4 | `kafka_producer.py` confluent-kafka wrapper + DLQ | ✅ | 13 test + 1 integration skip, %96.58 coverage |
| T5 | `main.py` APScheduler entrypoint + graceful shutdown | ✅ | 9 test, **%100 coverage**, Dockerfile.ingestion CMD güncel |
| **T6** | **`csv_loader.py` tarihsel veri temizleme (cp1254/utf-8-sig, ffill ≤3h, IQR, psycopg execute_batch)** | **⏳ SIRADA** | data-engineer'a ilk handoff |
| T9 | `make up` smoke test (Kafka + ingestion live data) | ⏳ | devops-engineer, T6'dan sonra |
| T10 | DQ baseline tests (contracts + key format + row-count invariant) | ⏳ | data-quality-engineer |
| T11 | Security audit (key leak, .env.local.example check) | ⏳ | security-compliance, son gate |

### Yarın açılış komutu
**Doğrudan T6 handoff** — yeni `/sprint-start` komutuna gerek yok, sprint-03.md plan hazır:
```
data-engineer → T6: src/ingestion/csv_loader.py
- Çevre Bakanlığı CSV (encoding cp1254 veya utf-8-sig), kolonlar kirletici bazlı
- Temizlik: ffill ≤3h, negatif drop, IQR outlier filter (kirletici başına), µg/m³ standardize
- psycopg.execute_batch ile fact_measurements insert
- DoD: tests/ingestion/test_csv_loader.py 100-satır fixture, ≥80% coverage
```

### Son commit'ler (origin'e push'lu, `b9ad111` HEAD)
```
b9ad111 feat(ingestion): add apscheduler entrypoint with graceful shutdown
bf0aea8 feat(ingestion): add kafka producer wrapper with dlq routing
f05bc95 feat(ingestion): add async openweather collector with tenacity retry
24b6443 feat(db): add minimal schema stub with dim_station, dim_pollutant, fact_measurements
b547cf9 feat(ingestion): add 6-station izmir catalog with industrial coverage
cb714b3 docs: log TD-05 pyspark python 3.13 wheel mismatch
9ba27cc style: apply ruff-format baseline and stub type annotations
9963c7d feat(infra): expand .env.local.example with full compose env contract
```

### Tamamlanan (Hafta 1-2 kapsamı)
- ✅ 11 subagent + 8 slash komut + `.claude/settings.local.json`
- ✅ Proje iskeleti, secret policy, Coolify IaC, GitHub repo, 5 Coolify kaynağı canlı

### Açık Hatırlatmalar
- `main-backup` local branch hâlâ duruyor (Claude trailer'lı eski history) — H8 sonrası sil
- TD-05: PySpark 3.5.1 + Python 3.13 wheel uyumsuz — H6 spark-engineer karar verecek
- Coolify app'lerde `DATABASE_URL` Magic Variable referansı; ilk deploy'da `sync_secrets list` ile doğrula
- Pre-commit hook'ta detect-secrets test fixture'larında `# pragma: allowlist secret` kullan (T3'te yaşandı)
- `tech-debt.md` → TD-01..TD-05 kayıtlı (Grafana FQDN, main-backup, Spark fixture, commitizen, PySpark wheel)

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
