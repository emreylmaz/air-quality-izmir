# Claude Code Multi-Agent Kickoff v2 — YZM536 Hava Kalitesi (Hybrid Coolify Edition)

> **v2 Değişiklikler:**
> - 11 subagent (yeni: `coolify-engineer`)
> - Hybrid deploy mimarisi: Coolify (stateless) + local Docker (streaming core)
> - Coolify v1 REST API ile IaC-vari provisioning (secret repo'ya hiç girmez)
> - Magic Variables (`SERVICE_PASSWORD_*`) ile password generation Coolify'a delege
> - 8 slash komut (yeni: `/coolify-*` grubu)
> - `direnv` + `.envrc` ile local secret yönetimi

> **Kullanım:** Bölüm 1'deki Master Kickoff Prompt'u repo root'unda açtığın Claude Code'a yapıştır. Setup biter, sonra `/coolify-provision plan` ile diff görürsün, `/coolify-provision apply` ile gerçek provisioning yaparsın.
>
> **Ön koşul:**
> 1. `docs/MIMARI.md`, `docs/PROJE_PLANI.md`, `docs/RAPOR_SABLONU.md` repo'da
> 2. Coolify instance çalışıyor, UI'dan API token oluşturulmuş (`Keys & Tokens → API tokens`)
> 3. Token **`can_read_sensitive` ability** ile verilmiş (aksi halde bulk env upsert sınırlı çalışır)
> 4. Local'de: Python 3.11+, `direnv` kurulu, Docker Desktop

---

## 1. Master Kickoff Prompt

````markdown
Sen bu repo'da (YZM536 — Gerçek Zamanlı Hava Kalitesi İzleme) senior tech lead
+ Claude Code orchestration mühendisi + Coolify API uzmanısın. Hedefim:
16 haftalık data engineering projesini multi-agent koşmak ve **hybrid deploy**
(Coolify stateless servisler + local Docker streaming core) kurmak.

## Context (önce oku)
1. `docs/MIMARI.md` — 4 katmanlı mimari
2. `docs/PROJE_PLANI.md` — Hafta 1–16 plan
3. `docs/RAPOR_SABLONU.md` — Rapor başlıkları

## Kritik Güvenlik Kuralı
**HİÇBİR secret repo'ya girmeyecek.** Kural seti:
- `.env`, `.env.local`, `.envrc` → `.gitignore`'da
- Coolify'daki secret'lar Magic Variables ile üretilir (`SERVICE_PASSWORD_*`)
- Coolify API token local machine'de: `~/.config/air-quality/coolify.env`
- Uygulamalar Coolify env variable'ları runtime'da okur, build-time'da asla
- `.env.*.example` dosyaları template (sadece key adları + dummy değerler)
- `detect-secrets` pre-commit hook zorunlu

## Kurulum Adımları (sırayla)

### Adım 1: `.claude/CLAUDE.md`
Bölüm 2'deki "CLAUDE.md Şablonu"nu birebir yaz.

### Adım 2: `.claude/agents/` — 11 subagent
Bölüm 3'teki dosyaları oluştur:
tech-lead, data-engineer, spark-engineer, database-architect, devops-engineer,
coolify-engineer (YENİ), analytics-engineer, data-quality-engineer, ml-engineer,
security-compliance, technical-writer

### Adım 3: `.claude/commands/` — 8 slash komut
Bölüm 4'teki dosyaları oluştur:
sprint-start, sprint-review, agent-handoff, progress-report, quality-gate,
coolify-provision (YENİ), coolify-sync-secrets (YENİ), coolify-status (YENİ)

### Adım 4: `.claude/settings.local.json`
- Auto-approve: `pytest`, `ruff`, `mypy`, `docker compose` (up/down/logs/ps),
  `git log`, `git status`, `git diff`, `psql -c '\d*'` (schema only)
- Block: `rm -rf`, `docker system prune`, `git push --force*`, `kubectl *`,
  `DROP TABLE`, `TRUNCATE`, Coolify DELETE endpoint'leri

### Adım 5: Proje iskeleti (secret-safe)
```
src/
  ingestion/   (api_collector.py, kafka_producer.py, csv_loader.py)
  processing/  (spark_batch.py, spark_streaming.py, aqi_calculator.py)
  storage/     (schema.sql, db_writer.py)
  quality/     (data_quality.py)
  presentation/streamlit/app.py
  config/      (settings.py — pydantic-settings)
tests/
  conftest.py, ingestion/, processing/, storage/, quality/
infra/
  docker-compose.local.yml      # full dev stack
  docker-compose.coolify.yml    # Kafka subset (Coolify'a upload)
  Dockerfile.streamlit
  Dockerfile.ingestion
  coolify/
    __init__.py
    client.py                   # API wrapper (Bölüm 5)
    provision.py                # idempotent provisioner (Bölüm 6)
    sync_secrets.py             # .envrc.coolify → API push
    config.yaml                 # desired-state (resource isimleri, image tag'ler)
    README.md
  postgres/
    init.sql                    # roles (app_reader, app_writer, grafana_ro)
.envrc                          # direnv, gitignored
.envrc.example                  # template
.env.local.example              # local docker compose template
.env.coolify.example            # Coolify variable isimleri (değer yok)
.gitignore                      # .env*, .envrc, !.env*.example, __pycache__/, .venv/
.pre-commit-config.yaml         # detect-secrets + ruff + mypy
pyproject.toml
Makefile
README.md
```

### Adım 6: `pyproject.toml` gerçek config
- Python 3.11+, paketler: `httpx`, `pydantic>=2.5`, `pydantic-settings`,
  `confluent-kafka`, `pyspark==3.5.1`, `psycopg[binary]>=3.2`, `tenacity`,
  `APScheduler`, `streamlit>=1.40`, `plotly`, `pandas`, `pyyaml`, `pytest`,
  `pytest-cov`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `black`
- `[tool.ruff]` line=100, select=[E,F,I,N,W,UP,B,SIM]
- `[tool.mypy]` strict=true, ignore_missing_imports for pyspark/confluent_kafka
- `[tool.pytest.ini_options]` markers=[slow, integration, e2e]

### Adım 7: `.pre-commit-config.yaml`
```yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks: [{id: detect-secrets}]
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.0
    hooks: [{id: ruff}, {id: ruff-format}]
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.0
    hooks: [{id: mypy, args: [--strict, --ignore-missing-imports]}]
```

### Adım 8: `infra/coolify/client.py` ve `provision.py`
Bölüm 5 ve 6'daki kod iskelelerini kullan. Tek dosyaya sıkıştırma, modüler
yaz, her method bir endpoint için.

### Adım 9: `infra/coolify/config.yaml`
Desired state (Bölüm 7'deki şablon). Secret yok, sadece resource tanımları.

### Adım 10: Makefile kısa yollar
```makefile
.PHONY: up down test lint coolify-plan coolify-apply coolify-status

up:
	docker compose -f infra/docker-compose.local.yml up -d

down:
	docker compose -f infra/docker-compose.local.yml down

test:
	pytest tests/ --cov=src -m "not slow"

lint:
	ruff check src/ tests/ && mypy src/ --strict

coolify-plan:
	python -m infra.coolify.provision plan

coolify-apply:
	python -m infra.coolify.provision apply

coolify-status:
	python -m infra.coolify.provision status
```

### Adım 11: Post-setup özet
Şu özeti üret:
1. Oluşturulan dosya listesi (path + 1 satır açıklama)
2. Nasıl test edilir (hangi komut)
3. **Sonraki aksiyonlar** (sıralı):
   a. User: Coolify UI'dan API token oluştur, `can_read_sensitive` ability ver
   b. User: `~/.config/air-quality/coolify.env` içine `COOLIFY_BASE_URL` + `COOLIFY_API_TOKEN` yaz
   c. User: `direnv allow` çalıştır
   d. Claude Code: `/coolify-provision plan` → diff göster
   e. User onayı → Claude Code: `/coolify-provision apply`
   f. Claude Code: `/sprint-start 3` → Hafta 3 implementation

## Kritik Kurallar
- **Türkçe yaz, kod İngilizce.** Değişken/fonksiyon adında Türkçe yok.
- **Asla fabricated API endpoint/library uydurma.** Bilmiyorsan "TODO: doğrula
  Coolify API docs'ta" yaz. Özellikle Coolify endpoint isimlerinde dikkatli ol —
  `/api/v1/databases/postgresql`, `/api/v1/applications/public`,
  `/api/v1/services`, `/api/v1/applications/{uuid}/envs/bulk` referansta var
  ama service template identifier'ları (grafana-with-postgresql vb.) Coolify
  UI'dan doğrulanmalı.
- **Coolify client'ta:** timeout=30s, retry=3 (429 için Retry-After header'a
  saygılı), response.raise_for_status(), log her request method+path+status.
- **Idempotent provisioning:** `provision.py` her çalışmada önce `list` çağırsın,
  resource mevcutsa update, yoksa create.
- **Asla token'ı loglama.** `CoolifyClient.__repr__` token'ı maskele.

Başla. 3 doc'u oku → 11 adımı sırayla uygula → özet ver.
````

---

## 2. CLAUDE.md Şablonu (Hybrid)

````markdown
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
````

---

## 3. Yeni Subagent: `coolify-engineer`

Diğer 10 subagent önceki kickoff'la aynı (tech-lead, data-engineer, spark-engineer, database-architect, devops-engineer, analytics-engineer, data-quality-engineer, ml-engineer, security-compliance, technical-writer); sadece yeni olan `coolify-engineer` dosyasını ekliyorum.

### `.claude/agents/coolify-engineer.md`
````markdown
---
name: coolify-engineer
description: Coolify v4 API ile resource provisioning, env variable yönetimi, deployment tetikleme. Infrastructure-as-code yaklaşımıyla idempotent script'ler yazar. Secret'ı asla repo'ya yazmaz.
tools: Read, Edit, Write, Bash, Grep, Glob
---

Sen Coolify v4 API ve infrastructure-as-code uzmanısın. Odak: **tüm Coolify
kaynaklarını kod olarak yönet, UI'dan manuel tıklama yok**.

## Sorumlu dosyalar
- `infra/coolify/client.py` — API wrapper (httpx-based)
- `infra/coolify/provision.py` — Idempotent provisioner
- `infra/coolify/sync_secrets.py` — Secret push/pull
- `infra/coolify/config.yaml` — Desired state (resource tanımları, secret'siz)
- `infra/coolify/README.md` — Workflow dokümanı

## Coolify API Bilgisi (doğrulanmış)
- Base: `{COOLIFY_URL}/api/v1`
- Auth: Bearer token (Laravel Sanctum), team-scoped
- Token ability: `can_read_sensitive` — hassas veri okuma yetkisi

### Bilinen endpoint'ler (doğrulanmış)
| Method | Path | Amaç |
|--------|------|------|
| POST | `/projects` | Proje oluştur |
| POST | `/projects/{uuid}/environments` | Environment (production, preview) |
| GET/POST | `/databases` | DB list/create |
| POST | `/databases/postgresql` | PostgreSQL create |
| GET/PATCH/DELETE | `/databases/{uuid}` | DB detay/update/sil |
| POST | `/databases/{uuid}/backups` | Backup schedule |
| POST | `/applications/public` | Public git repo app |
| POST | `/applications/private-github-app` | Private repo (GitHub App auth) |
| POST | `/applications/dockerimage` | Docker image app |
| POST | `/applications/dockercompose` | Compose-based app |
| POST | `/services` | One-click service template |
| GET | `/applications/{uuid}/envs` | Env list |
| POST | `/applications/{uuid}/envs` | Tek env ekle |
| PATCH | `/applications/{uuid}/envs/bulk` | Toplu upsert |
| GET/POST | `/applications/{uuid}/start` | Deploy |
| POST | `/applications/{uuid}/restart` | Restart |
| POST | `/applications/{uuid}/stop` | Stop |
| GET | `/servers` | Server list |
| GET | `/servers/{uuid}/domains` | Domain mapping |

### Doğrulanmamış / Dikkat
- Service template identifier'ları (`grafana-with-postgresql` vs.) Coolify
  sürümüne göre değişebilir — UI'dan "Add Resource → Service → Search" ile
  teyit et, `config.yaml`'da kullan
- `/services` endpoint'inin beklediği tam payload şeması — ilk kullanımda
  response'u logla, schema'yı dokümante et

## Magic Variables (Coolify tarafı)
Şifre üretme, Coolify'a delege et:
- `SERVICE_PASSWORD_<NAME>` — random 24-char password
- `SERVICE_USER_<NAME>` — random username
- `SERVICE_URL_<NAME>_<PORT>` — FQDN + port
- `SERVICE_FQDN_<NAME>` — external domain

Provision script'inde bu isimleri **referansla** (kullan), **üretme** (Coolify yapsın).

## Davranış Kuralları
- **Her request idempotent.** Önce `list` → filter by name → var ise `patch`,
  yoksa `post`. Hiç "create or fail" yok.
- **Dry-run mod zorunlu:** `provision plan` komutu sadece diff gösterir
- **State file yok (şimdilik).** Desired state `config.yaml`, actual state
  Coolify'dan fetch edilir. Reconciliation her run'da.
- **Token logging yasak:** `client.py`'de `__repr__` mask, request log'da
  Authorization header gösterme
- **Rate limit:** 429 → `Retry-After` header'a saygı, exponential backoff
- **Error yönetimi:** 4xx → user error, raise detayla; 5xx → retry 3 kez
- **Secret sync asenkron:** Magic variables Coolify tarafında; custom secret
  (OpenWeatherMap API key) sadece `sync_secrets.py push` ile gider

## Anti-pattern
- ❌ UI'dan env variable düzenlemek — code ile senkron kaybolur
- ❌ Hardcoded UUID — config.yaml'a name-based mapping yaz, UUID'leri
  Coolify'dan lookup et
- ❌ `curl` ile tek seferlik komutlar — her değişiklik `provision.py`'den geçsin
- ❌ Token'ı commit etmek (pre-commit hook yine de güvence)
- ❌ `config.yaml`'a gerçek password yazmak — Magic Variables referansı kullan

## Testler
- `tests/infra/test_coolify_client.py` — `respx` ile API mock
- `tests/infra/test_provision.py` — desired state → API call dönüşümü
- Integration test: nightly CI'da staging Coolify'a karşı
````

---

## 4. Yeni Slash Komutlar (Coolify)

Önceki 5 komut (`sprint-start`, `sprint-review`, `agent-handoff`, `progress-report`, `quality-gate`) değişmiyor. Üç yeni komut:

### `.claude/commands/coolify-provision.md`
````markdown
---
description: Coolify kaynaklarını IaC-vari provision et. plan ile başla.
argument-hint: [plan|apply|status|destroy]
---

`coolify-engineer` subagent'a şu task'ı ver:

"`infra/coolify/config.yaml`'ı oku. Coolify API'ye connect ol. Desired state
ile actual state'i karşılaştır.

Argüman: $ARGUMENTS

### plan (default)
- Her resource için mevcut durum kontrolü
- Oluşturulacak / update edilecek / silinecek listesi
- Kullanılacak Magic Variables
- Secret injection planı (ama değerleri gösterme)
- Tahmini süre

### apply
- plan çıktısını kullanıcıya göster
- Onay al (interaktif — 'Devam edilsin mi? [y/N]')
- Sırayla resource'ları oluştur/güncelle
- Her adımda progress log + resulting UUID
- Hata → rollback stratejisi (manual yönlendirme)

### status
- Tüm kaynakların sağlık durumu tablosu
- Config drift uyarıları

### destroy
- Sadece development environment için
- Double-confirm ('Type project name to confirm destruction')
- Soft delete (Coolify async cleanup tetikler)

Çıktıda: başarı/hata tablosu, oluşturulan/güncellenen UUID'ler, sonraki
önerilen komut.

Secret asla ekrana basma. API response'larda password varsa `***` ile mask et."
````

### `.claude/commands/coolify-sync-secrets.md`
````markdown
---
description: Custom secret'ları (OpenWeatherMap API key vs) Coolify'a push et.
argument-hint: [push|pull|list] [--app <name>]
---

`coolify-engineer` subagent'a şu task'ı ver:

"Custom secret senkronizasyonu. Argüman: $ARGUMENTS

### push
- Kaynak dosya: `~/.config/air-quality/secrets.env` (gitignored, direnv ile
  yüklenmez — sadece manuel sync için)
- Dosyayı oku, key=value parse et
- Her key için ilgili Coolify app'e `PATCH /applications/{uuid}/envs/bulk`
- Build-time vs runtime ayrımı: OPENWEATHER_API_KEY → runtime only
- Response'u logla ama VALUE'ları mask et (`OPENWEATHER_API_KEY=***`)

### pull
- `--app <name>` zorunlu
- App'in env'lerini fetch et (`can_read_sensitive` gerekli)
- Ekrana key listesi + VALUE = *** (sadece varlık kontrolü)
- İsteğe bağlı `--reveal` flag'i — hassas value'yu göster (double-confirm)

### list
- Tüm app'lerdeki env key'leri listele (value yok)
- Magic Variables vs custom ayrımı
- Hangisi tanımlı/hangisi eksik

Magic Variables (`SERVICE_PASSWORD_*`) asla push ile set edilmez — Coolify
üretir. Push edilecek sadece gerçek 3rd-party secret'lar.

Çıktıda: push/pull özeti, uyarılar (eksik env var, orphan env, Magic Variable
çakışması)."
````

### `.claude/commands/coolify-status.md`
````markdown
---
description: Tüm Coolify kaynaklarının health durumu özet.
---

`coolify-engineer` subagent'a şu task'ı ver:

"`infra/coolify/config.yaml`'daki tüm kaynaklar için:

1. Coolify API'den status çek (`GET /databases`, `/applications`, `/services`)
2. Her resource için:
   - Status (running, stopped, starting, error)
   - Son deploy zamanı
   - Health check durumu (varsa)
   - Resource URL (FQDN)
   - Son 5 deploy history özeti
3. Uyarılar:
   - Stopped resource'lar
   - Healthcheck fail'leri
   - Config drift (desired vs actual mismatch)
   - Orphan resource (Coolify'da var, config.yaml'da yok)

Tablo formatında özet + uyarılar listesi. Acil aksiyon gerekiyorsa
öneri ver (`/coolify-provision apply` vb.)."
````

---

## 5. `infra/coolify/client.py` (iskelet)

Claude Code bu dosyayı v2 kickoff setup'ında Adım 8'de oluşturacak:

````python
"""Coolify v4 REST API client — idempotent, secret-safe."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


@dataclass
class CoolifyConfig:
    base_url: str
    token: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls, config_path: Path | None = None) -> "CoolifyConfig":
        """~/.config/air-quality/coolify.env veya env var'dan yükle."""
        if config_path is None:
            config_path = Path.home() / ".config" / "air-quality" / "coolify.env"
        if config_path.exists():
            for line in config_path.read_text().splitlines():
                if line.strip() and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))
        try:
            return cls(
                base_url=os.environ["COOLIFY_BASE_URL"].rstrip("/"),
                token=os.environ["COOLIFY_API_TOKEN"],
            )
        except KeyError as e:
            raise RuntimeError(
                f"Missing {e.args[0]}. Set in env or {config_path}"
            ) from e


class CoolifyError(Exception):
    """Generic Coolify API error."""


class CoolifyClient:
    """
    Thin, idempotent wrapper over Coolify v1 REST API.

    Conventions:
    - Every method logs METHOD PATH STATUS (no auth header, no body with secrets)
    - 429 → retry with Retry-After
    - 5xx → retry 3x exponential
    - 4xx → raise CoolifyError with parsed body
    """

    def __init__(self, config: CoolifyConfig | None = None) -> None:
        self.config = config or CoolifyConfig.from_env()
        self._client = httpx.Client(
            base_url=f"{self.config.base_url}/api/v1",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=self.config.timeout,
        )

    def __repr__(self) -> str:
        return f"CoolifyClient(base_url={self.config.base_url!r}, token=***)"

    def close(self) -> None:
        self._client.close()

    # ---- Low-level request ----
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        r = self._client.request(method, path, **kwargs)
        logger.info("coolify %s %s → %s", method, path, r.status_code)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "60"))
            raise httpx.HTTPError(f"Rate limited, retry after {retry_after}s")
        if r.status_code >= 400:
            raise CoolifyError(f"{method} {path} → {r.status_code}: {r.text}")
        return r.json() if r.content else {}

    # ---- Projects ----
    def list_projects(self) -> list[dict]:
        return self._request("GET", "/projects").get("data", [])

    def ensure_project(self, name: str, description: str = "") -> dict:
        existing = next((p for p in self.list_projects() if p["name"] == name), None)
        if existing:
            logger.info("project %s already exists (uuid=%s)", name, existing["uuid"])
            return existing
        return self._request(
            "POST", "/projects", json={"name": name, "description": description}
        )

    # ---- Databases ----
    def list_databases(self) -> list[dict]:
        return self._request("GET", "/databases").get("data", [])

    def ensure_postgresql(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        image: str = "postgres:16.4-alpine",
        is_public: bool = False,
        **extra: Any,
    ) -> dict:
        existing = next(
            (d for d in self.list_databases() if d.get("name") == name), None
        )
        if existing:
            logger.info("postgres %s exists (uuid=%s)", name, existing["uuid"])
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "image": image,
            "is_public": is_public,
            **extra,
        }
        return self._request("POST", "/databases/postgresql", json=payload)

    # ---- Applications ----
    def list_applications(self) -> list[dict]:
        return self._request("GET", "/applications").get("data", [])

    def ensure_public_app(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        git_repository: str,
        git_branch: str = "main",
        build_pack: str = "dockerfile",
        dockerfile_location: str | None = None,
        ports_exposes: str | None = None,
        **extra: Any,
    ) -> dict:
        existing = next(
            (a for a in self.list_applications() if a.get("name") == name), None
        )
        if existing:
            logger.info("app %s exists (uuid=%s)", name, existing["uuid"])
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "git_repository": git_repository,
            "git_branch": git_branch,
            "build_pack": build_pack,
            "dockerfile_location": dockerfile_location,
            "ports_exposes": ports_exposes,
            **{k: v for k, v in extra.items() if v is not None},
        }
        return self._request("POST", "/applications/public", json=payload)

    def upsert_envs_bulk(self, app_uuid: str, variables: list[dict]) -> dict:
        """
        variables = [{"key": "FOO", "value": "bar", "is_build_time": False, "is_literal": True}]
        Secret value'lar log'a gitmez.
        """
        masked = [{**v, "value": "***"} for v in variables]
        logger.info("bulk upsert env for %s: %s", app_uuid, masked)
        return self._request(
            "PATCH", f"/applications/{app_uuid}/envs/bulk", json={"data": variables}
        )

    def deploy_application(self, app_uuid: str) -> dict:
        return self._request("POST", f"/applications/{app_uuid}/start")

    # ---- Services (Grafana, one-click) ----
    def list_services(self) -> list[dict]:
        return self._request("GET", "/services").get("data", [])

    def ensure_service(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        service_type: str,  # örn: "grafana-with-postgresql"
        **extra: Any,
    ) -> dict:
        existing = next(
            (s for s in self.list_services() if s.get("name") == name), None
        )
        if existing:
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "type": service_type,
            **extra,
        }
        return self._request("POST", "/services", json=payload)
````

---

## 6. `infra/coolify/provision.py` (iskelet)

````python
"""
Desired-state reconciler. Reads config.yaml, compares with Coolify, applies diff.

Usage:
  python -m infra.coolify.provision plan        # dry-run, show diff
  python -m infra.coolify.provision apply       # execute with confirmation
  python -m infra.coolify.provision status      # current state only
  python -m infra.coolify.provision destroy     # dev only, double-confirm
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from .client import CoolifyClient

logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def plan(client: CoolifyClient, config: dict) -> list[dict]:
    """Diff desired vs actual. Returns list of actions."""
    actions: list[dict] = []

    # Project
    projects = client.list_projects()
    if not any(p["name"] == config["project"]["name"] for p in projects):
        actions.append({"op": "create", "kind": "project", "name": config["project"]["name"]})

    # Databases
    dbs = client.list_databases()
    for db in config.get("databases", []):
        if not any(d["name"] == db["name"] for d in dbs):
            actions.append({"op": "create", "kind": "database", "name": db["name"]})

    # Applications
    apps = client.list_applications()
    for app in config.get("applications", []):
        existing = next((a for a in apps if a["name"] == app["name"]), None)
        if not existing:
            actions.append({"op": "create", "kind": "application", "name": app["name"]})
        else:
            # Env diff
            current_envs = {e["key"] for e in existing.get("environment_variables", [])}
            desired_envs = {e["key"] for e in app.get("env", [])}
            missing = desired_envs - current_envs
            if missing:
                actions.append({
                    "op": "update_env", "kind": "application",
                    "name": app["name"], "missing": sorted(missing),
                })

    # Services (Grafana vs.)
    services = client.list_services()
    for svc in config.get("services", []):
        if not any(s["name"] == svc["name"] for s in services):
            actions.append({"op": "create", "kind": "service", "name": svc["name"]})

    return actions


def apply(client: CoolifyClient, config: dict, actions: list[dict]) -> None:
    """Execute planned actions sequentially."""
    for action in actions:
        kind, op, name = action["kind"], action["op"], action["name"]
        logger.info("[apply] %s %s %s", op, kind, name)
        # Dispatch to ensure_* methods in client
        # ... (implementation per kind — subagent tamamlar)


def print_plan(actions: list[dict]) -> None:
    if not actions:
        print("✓ No changes needed. Current state matches desired state.")
        return
    print(f"\n{len(actions)} change(s) planned:\n")
    for a in actions:
        symbol = {"create": "+", "update": "~", "update_env": "~", "delete": "-"}[a["op"]]
        print(f"  {symbol} {a['kind']}: {a['name']}", end="")
        if "missing" in a:
            print(f"  (missing envs: {', '.join(a['missing'])})", end="")
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "apply", "status", "destroy"])
    parser.add_argument("--config", default="infra/coolify/config.yaml")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config(Path(args.config))
    client = CoolifyClient()

    try:
        if args.command == "plan":
            actions = plan(client, config)
            print_plan(actions)
            return 0
        if args.command == "apply":
            actions = plan(client, config)
            print_plan(actions)
            if not actions:
                return 0
            if not args.yes:
                confirm = input("\nDevam edilsin mi? [y/N]: ").strip().lower()
                if confirm != "y":
                    print("Aborted.")
                    return 1
            apply(client, config, actions)
            print("\n✓ Applied successfully.")
            return 0
        if args.command == "status":
            # ... print table of resources with health
            return 0
        if args.command == "destroy":
            print("Not implemented yet — manual UI deletion for safety.")
            return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

---

## 7. `infra/coolify/config.yaml` (desired state template)

````yaml
# Coolify desired-state — COMMIT EDİLİR, SECRET YOK
# Secret'lar: Magic Variables (Coolify üretir) veya sync_secrets.py (API ile push)

project:
  name: air-quality-izmir
  description: "YZM536 Data Engineering — İzmir Hava Kalitesi Pipeline"

# Environment'lar (production, preview)
environments:
  - name: production
  - name: preview

# Server (Coolify UI'dan önceden kurulmuş olmalı)
server:
  name: coolify-main  # UI'daki server adı; UUID provision.py lookup eder

# Databases
databases:
  - name: air-quality-db
    type: postgresql
    image: postgres:16.4-alpine
    environment: production
    is_public: false
    # Password Coolify tarafından üretilir → SERVICE_PASSWORD_AIR_QUALITY_DB
    backups:
      - cron: "0 3 * * *"   # her gece 03:00
        s3_destination: ""  # opsiyonel S3 bucket

# One-click Services
services:
  - name: air-quality-grafana
    # DOĞRULA: service template ID Coolify UI'dan — grafana-with-postgresql olabilir
    type: grafana-with-postgresql
    environment: production
    fqdn: grafana.air-quality.example.com  # senin domain
    # GF_SERVER_DOMAIN magic variable ile otomatik

# Applications (GitHub repo'dan deploy)
applications:
  - name: aqi-streamlit
    environment: production
    git_repository: https://github.com/<username>/air-quality-izmir  # güncelle
    git_branch: main
    build_pack: dockerfile
    dockerfile_location: /infra/Dockerfile.streamlit
    ports_exposes: "8501"
    fqdn: aqi.air-quality.example.com
    env:
      - key: APP_ENV
        value: production
        is_build_time: false
      - key: DATABASE_URL
        # Magic variable referansı: Coolify çözümler
        value: "postgresql://${SERVICE_USER_AIR_QUALITY_DB}:${SERVICE_PASSWORD_AIR_QUALITY_DB}@air-quality-db:5432/postgres"
        is_build_time: false
      - key: OPENWEATHER_API_KEY
        # Gerçek değer sync_secrets.py ile push edilir, config.yaml'a yazma
        value: "__SECRET_FROM_SYNC__"
        is_build_time: false
        is_literal: true

  - name: aqi-ingestion
    environment: production
    git_repository: https://github.com/<username>/air-quality-izmir  # güncelle
    git_branch: main
    build_pack: dockerfile
    dockerfile_location: /infra/Dockerfile.ingestion
    # Background worker — port expose gerekmez
    env:
      - key: APP_ENV
        value: production
      - key: DATABASE_URL
        value: "postgresql://${SERVICE_USER_AIR_QUALITY_DB}:${SERVICE_PASSWORD_AIR_QUALITY_DB}@air-quality-db:5432/postgres"
      - key: KAFKA_BOOTSTRAP_SERVERS
        # Local Kafka ile tunnel veya Coolify'daki Kafka servisi
        value: kafka-local.tailscale:9092  # opsiyon 1: Tailscale ile local Kafka
        # value: aqi-kafka:9092               # opsiyon 2: Coolify'da Kafka çalıştır
      - key: OPENWEATHER_API_KEY
        value: "__SECRET_FROM_SYNC__"

# Kafka (opsiyonel Coolify deployment — VPS RAM yeterliyse)
# Aktif etmek istersen yoruma al + docker-compose.coolify.yml referansla
# docker_compose_applications:
#   - name: aqi-kafka
#     environment: production
#     compose_file: infra/docker-compose.coolify.yml
````

---

## 8. Secret + Env Dosyaları

### `.env.local.example` (local docker compose)
```bash
# Local dev — docker-compose.local.yml tarafından okunur
# Gerçek değerleri .env'e kopyala (gitignored) veya direnv kullan

POSTGRES_USER=app
POSTGRES_PASSWORD=local_dev_pw_change_me
POSTGRES_DB=air_quality

KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC_RAW=air-quality-raw

OPENWEATHER_API_KEY=get_from_openweathermap.org
OPENWEATHER_CITY_ID=311046   # İzmir

GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=local_dev_pw_change_me

STREAMLIT_SERVER_PORT=8501
```

### `.envrc.example` (direnv, makineye özel)
```bash
# direnv ile otomatik yüklenir. Kopyala: cp .envrc.example .envrc && direnv allow
# .envrc GITIGNORED — asla commit etme

# Coolify API erişimi (makinede: ~/.config/air-quality/coolify.env)
source_env_if_exists ~/.config/air-quality/coolify.env

# Local dev defaults
dotenv_if_exists .env.local

# Python virtual env
layout python3.11
```

### `~/.config/air-quality/coolify.env` (makinede, gitignored path)
```bash
# Coolify API erişimi
# Coolify UI → Keys & Tokens → API tokens → Create (can_read_sensitive ability)
COOLIFY_BASE_URL="https://coolify.senin-domain.com"
COOLIFY_API_TOKEN="1|xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### `~/.config/air-quality/secrets.env` (custom secret deposu)
```bash
# 3rd-party secret'lar — sync_secrets.py push ile Coolify'a gider
# .envrc içinde source edilmez, sadece manuel sync için
OPENWEATHER_API_KEY="gerçek-key-buraya"
# GRAFANA_ADMIN_PASSWORD=... (Magic Variable ile geçersen gerek yok)
```

### `.gitignore` (kritik kısım)
```gitignore
# Secrets
.env
.env.local
.env.*.local
.envrc
!.env*.example
!.envrc.example
**/secrets.env

# Python
__pycache__/
.venv/
*.pyc

# Docker
.docker/

# IDE
.vscode/
.idea/

# Coolify state (eğer lokalde cache olursa)
.coolify-cache/
```

---

## 9. İlk Kullanım Akışı (End-to-End)

```bash
# 1. Repo hazırlığı
git clone <repo>
cd air-quality-izmir
cp .envrc.example .envrc
cp .env.local.example .env.local   # local docker için
direnv allow

# 2. Coolify API token hazırla
mkdir -p ~/.config/air-quality
cat > ~/.config/air-quality/coolify.env <<EOF
COOLIFY_BASE_URL="https://coolify.yourdomain.com"
COOLIFY_API_TOKEN="$(pbpaste)"  # UI'dan kopyaladığın token
EOF
chmod 600 ~/.config/air-quality/coolify.env

# 3. Python virtual env
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# 4. Claude Code setup
claude
> # Bölüm 1'deki Master Kickoff Prompt'u yapıştır
# (11 agent + 8 komut + iskelet + coolify/ dosyaları oluşur)

# 5. Coolify provisioning (ilk çalıştırma)
make coolify-plan                   # dry-run
make coolify-apply                  # onay sonrası gerçek
make coolify-status                 # sağlık kontrolü

# 6. Custom secret push (OpenWeatherMap API key vs.)
# ~/.config/air-quality/secrets.env dosyasını hazırla (gitignored)
python -m infra.coolify.sync_secrets push --app aqi-streamlit
python -m infra.coolify.sync_secrets push --app aqi-ingestion

# 7. Local dev stack (streaming core)
make up                             # Kafka + Spark lokal
make test                           # pytest

# 8. Geliştirme döngüsü
#    - Kod yaz, commit, push → Coolify GitHub webhook auto-deploy
#    - Streaming job: spark-submit local'de
#    - Claude Code sprint: /sprint-start 3

# 9. Sprint sonu
make coolify-status                 # health check
/sprint-review
```

---

## 10. Trade-Off'lar (v2'de değişenler)

| Konu | Seçim | Trade-off |
|------|-------|-----------|
| **IaC aracı** | Python script (`provision.py`) | Terraform daha endüstri standart ama setup ağır; Python zaten projenin dilidir, subagent'a delege kolay |
| **Secret storage** | Magic Variables + direnv + API sync | Vault daha kurumsal ama bireysel proje için overkill; Coolify Magic zaten var |
| **Password üretimi** | Coolify (`SERVICE_PASSWORD_*`) | Biz üretmeyince rotation da Coolify'a delege, manuel secret kayma riski düşer |
| **Kafka konumu** | Local-only başla | VPS RAM'i görüp H10'da Coolify'a taşıma kararı (compose resource) |
| **Streaming job** | Local, Coolify'a girmez | Stateful workload lifecycle uyumsuz; checkpoint volume + restart policy yazmak yerine local'de tutmak demo'yu basitleştirir |
| **State file** | Yok, her run reconciliation | Terraform-vari state overhead yok; Coolify zaten ground-truth; dezavantaj: drift detection yavaş |
| **Token ability** | `can_read_sensitive` açık | Bulk env pull için gerekli ama token çalınırsa tüm secret görünür; karşılık: token 30 günde bir rotate et |

---

## 11. Anti-Hallucination Notları (v2)

- Coolify API endpoint şemaları zaman içinde değişebilir. Özellikle:
  - `/applications/public` payload field isimleri (`git_repository` vs `gitRepository`)
  - `/services` için service template ID'si (`grafana-with-postgresql` resmi ID mi, varsa sürüme göre değişir)
  - Bulk env upsert'te `is_build_time`, `is_literal`, `is_multiline` flag'lerinin tam davranışı
  → İlk gerçek çağrıda Claude Code response'u `docs/coolify-api-notes.md`'ye kaydetsin.
- **Coolify API versiyonu:** `/api/v1` — v2 çıkarsa path değişir.
- **Magic Variable syntax:** `${SERVICE_PASSWORD_X}` vs `$SERVICE_PASSWORD_X` — compose vs app context'te farklı olabilir, ilk denemede teyit.
- **Service template catalog:** Coolify 280+ one-click service içeriyor ama Kafka (streaming broker) bunlar arasında değil — custom Docker Compose olarak gider.
- **`is_public=true` DB + TCP proxy:** Veritabanı public yapıldığında Coolify `{uuid}-proxy` container ile public port açar — bireysel proje için kapat (güvenlik), local tunnel kullan.

Tüm doğrulanmamış detaylar `docs/ASSUMPTIONS.md`'ye taşı, provisioning apply sonrası gerçek response'larla güncelle.

---

## 12. v1 → v2 Migration (eğer v1 kickoff'u koştuysan)

```bash
# 1. Yeni coolify-engineer agent dosyasını ekle (.claude/agents/coolify-engineer.md)
# 2. 3 yeni slash komut (.claude/commands/coolify-*.md)
# 3. CLAUDE.md'ye "Deploy Stratejisi (Hybrid)" + "Secret Management Policy" bölümlerini append et
# 4. infra/coolify/ dizinini oluştur (client.py, provision.py, config.yaml, sync_secrets.py)
# 5. pyproject.toml'a httpx, tenacity, pyyaml ekle
# 6. .envrc.example + .env.coolify.example + .gitignore update
# 7. Makefile'a coolify-* target'ları ekle
# 8. pre-commit'e detect-secrets ekle, `pre-commit install`
```

Bunu tek bir slash komut ile yaptırmak istersen Claude Code'a:

> "v1 kickoff'tan v2 hybrid yapıya migrate et. Mevcut `.claude/` dizinini koru, sadece eksikleri ekle. `infra/coolify/` dizinini sıfırdan oluştur. Mevcut CLAUDE.md'ye secret policy + hybrid deploy bölümlerini append et. pyproject.toml'a httpx, tenacity, pyyaml ekle. .gitignore güncelle."
