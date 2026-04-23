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

## Anti-Patterns
- ❌ `.env` içine gerçek secret — `direnv` kullan, kişisel secret manager'dan
- ❌ Coolify UI'dan manuel env variable eklemek — idempotency bozulur
- ❌ Password manuel üretmek — Magic Variables kullan
- ❌ Spark streaming job'ını Coolify'a deploy — lifecycle uyumsuz
- ❌ Tarihsel veriyi Kafka'ya push — doğrudan PostgreSQL (batch kanal)

## TODO / Açık Kararlar
- [ ] OpenWeatherMap Student Pack yanıtı — gelene kadar `respx` mock mode
- [ ] Coolify service template ID doğrulama: `grafana-with-postgresql` tam ID ne?
- [ ] Kafka Coolify custom compose mu, local-only mi (VPS RAM'e göre karar H10)
- [ ] ML model seçimi Prophet/ARIMA — H14 veri hacmine göre
