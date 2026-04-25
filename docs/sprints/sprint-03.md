# Hafta 3 Sprint Plan

## Hedef
Ingestion katmanını ayağa kaldırmak: OpenWeatherMap API → Python Producer → Kafka topic akışını canlı hale getirmek, tarihsel CSV verisini `fact_measurements`'a temiz şekilde yüklemek ve `make up` ile tüm local stack'i tek komutla çalışır hale getirmek.

## Bağımlılık & Blocker Analizi

### Hafta 1–2 çıktıları — tam mı?
| Kontrol | Durum | Not |
|--------|-------|-----|
| `docs/MIMARI.md` mevcut | ✓ | Veri akış diyagramı doğrulandı |
| `src/` iskeletleri (`api_collector.py`, `kafka_producer.py`, `csv_loader.py`) NotImplementedError stub | ✓ | H3'te doldurulacak |
| `infra/docker-compose.local.yml` (Kafka + Postgres + Spark + Grafana + Streamlit + ingestion) | ✓ | H3'te smoke-test gerekli — hiç `make up` edilmedi |
| `src/storage/schema.sql` var mı? | ⚠ **BLOCKER adayı** | Compose `schema.sql:/docker-entrypoint-initdb.d/01_schema.sql:ro` mount ediyor ama dosya listesi yok — `database-architect` H4 işi. H3'te geçici minimal schema yoksa `csv_loader` insert'leri patlar |
| `.env.local` dosyası (compose `env_file: ../.env.local` referans) | ⚠ **BLOCKER** | `.envrc.example` var ama `.env.local` template'i yok — ingestion + streamlit container start etmez |
| `OPENWEATHER_API_KEY` — gerçek vs mock | ⏳ | Coolify'a push edildi (pickup notes), local dev için user'ın `~/.config/air-quality/secrets.env` üzerinden alınması gerek; gelmediyse `respx` mock mode (`TODO / Açık Kararlar`) |
| İzmir istasyon listesi (lat/lon) | ⚠ **BLOCKER** | `IZMIR_STATIONS` boş — en az 3 istasyonun lat/lon'u netleşmeli. `docs/PROJE_PLANI.md` H1-2'de "en az 3-5 nokta" deniyor ama kaynak listesi yok |
| `tests/conftest.py` | ✓ | Ingestion fixture yok, H3'te eklenecek |
| `.venv` ingestion extras | ✗ | Şu an sadece `[coolify]` kurulu — ilk task |

### Tech-debt (yeni `tech-debt.md` dosyasında tutulacak)
- TD-01 Grafana sub-app FQDN port injection (H13'te çözülecek, `provision.py fqdn` parametresi eksik)
- TD-02 `main-backup` local branch temizliği (güvenlik ağı — H8 rapor sonrası silinir)
- TD-03 `SparkSession` pytest fixture `pytest.skip` ile bırakılmış (H6 spark-engineer)
- TD-04 `commitizen`/`conventional-pre-commit` hook eklemesi (CLAUDE.md TODO)

---

## Tasks

| # | Task | Agent | DoD | Est |
|---|------|-------|-----|-----|
| 1 | **Venv genişlet + dev deps** — `pip install -e ".[dev,ingestion,processing]"`, `pre-commit install`, `make lint` temiz, `make test` baseline yeşil | devops-engineer | `make install` idempotent çalışıyor; `ruff check` ve `mypy --strict` geçiyor; CI yoksa en az local smoke-test logu | 1h |
| 2 | **İstasyon kataloğu** — `config/stations.yaml` (5 istasyon, lat/lon/district), `api_collector.IZMIR_STATIONS` bunu yüklesin | data-engineer | `tests/ingestion/test_stations.py`: yaml schema valid, 5 kayıt, lat 38.0-38.8 / lon 26.8-27.5 aralığında; pydantic `Station` validation | 1h |
| 3 | **`api_collector.py` — async httpx client** — OpenWeatherMap `/data/2.5/air_pollution` + `/weather` çağrısı, pydantic response model, `tenacity` retry (429/5xx, exp backoff max 3), `openweather_api_key` settings'den | data-engineer | `tests/ingestion/test_api_collector.py` (respx mock): happy path (200 → parsed dict), 429 retry, 500 retry-then-fail; `fetch_air_pollution(station)` mypy strict; ≥85% coverage bu modül için | 4h |
| 4 | **`kafka_producer.py` — confluent-kafka wrapper** — `KafkaProducerWrapper.publish(key, value, topic)`, key = `f"{station_id}:{iso_hour}"`, JSON serialize via pydantic, DLQ routing (`kafka_topic_dlq`), `flush(timeout)` returns pending count | data-engineer | `tests/ingestion/test_kafka_producer.py`: in-process mock producer (MagicMock), serialization contract test, malformed payload → DLQ topic; integration test `@pytest.mark.integration` skip'li ama yazılmış (gerçek broker'a bağlanır) | 3h |
| 5 | **Scheduler entrypoint** — `src/ingestion/main.py`: APScheduler cron (`ingestion_interval_minutes`), her tick'te tüm istasyonlar → API → Producer; graceful shutdown (SIGTERM → flush) | data-engineer | `python -m src.ingestion.main` 60 sn süre içinde en az 1 publish; `Dockerfile.ingestion` CMD bunu çağırıyor; structured logging (JSON, request URL loglu ama header yok) | 2h |
| 6 | **`csv_loader.py` — tarihsel veri temizleme** — Çevre Bakanlığı CSV (encoding: `cp1254` veya `utf-8-sig`), kolonlar kirletici bazlı, eksik değer forward-fill ≤ 3h, negatif → drop, IQR outlier filter (kirletici başına), birim µg/m³ standardizasyonu, `psycopg.execute_batch` ile `fact_measurements` insert | data-engineer | `tests/ingestion/test_csv_loader.py`: fixture 100-satır sample CSV; temizlik kuralları (negatif drop, IQR, ffill) ayrı ayrı test; return value = satır sayısı; ≥80% coverage | 4h |
| 7 | **Minimal `schema.sql` stub** — H4'te genişletilecek ama H3 smoke-test için shell: `dim_station` + `dim_pollutant` (seed data: 6 pollutant) + `fact_measurements` (FK'ler, index yok) | database-architect (H3 dar scope) | `make up` sonrası `psql -c "\dt"` 3 tablo gösteriyor; seed olarak 6 pollutant row'u var; H4'te `database-architect` bunu star schema'ya genişletecek (partition + BRIN) | 1.5h |
| 8 | **`.env.local.example` + direnv uyumu** — compose'un beklediği tüm env var'ları (POSTGRES_USER, KAFKA_BOOTSTRAP_SERVERS, OPENWEATHER_API_KEY=replace_me, GRAFANA_ADMIN_PASSWORD, STREAMLIT_SERVER_PORT) + `.gitignore`'da `.env.local` olduğu doğrulansın | devops-engineer | `.env.local.example` commit edilmiş; `cp .env.local.example .env.local` sonrası `make up` 0 warning ile çıkıyor; `detect-secrets scan` temiz | 1h |
| 9 | **`make up` smoke test** — Tüm stack ayağa kalkıyor, Kafka topic auto-create, ingestion container 60 sn içinde Kafka'ya ilk mesajı yazıyor; `kafka-console-consumer --topic air-quality-raw --from-beginning` veri gösteriyor | devops-engineer | Makefile'a `make smoke` target eklenmesin (sadece manuel doğrulama demo için); `docs/sprints/sprint-03-demo.md` (opsiyonel) çıktı screenshot'ı; 5 container'ın hepsi `healthy` | 2h |
| 10 | **Ingestion DQ baseline** — H12'ye kadar full framework yok ama H3'te `tests/ingestion/test_contracts.py`: API response JSON schema contract test, producer key format test, CSV loader row-count invariant | data-quality-engineer | `pytest -m "not slow and not integration"` tamamı yeşil; coverage rapor `src/ingestion/` ≥80% | 2h |
| 11 | **Security pre-review (secret audit)** | security-compliance | OpenWeatherMap key `.env.local.example`'da `replace_me` placeholder mı? `direnv` yolu CLAUDE.md policy'sine uygun mu? `api_collector` log'larında key leak riski yok mu (URL'de query param olarak gider → `httpx` access log KAPALI olmalı)? Audit raporu PR comment olarak | 1h |

**Toplam tahmin:** ~22.5h (1 sprint haftası = 20-25h bandı; risk buffer %10)

---

## Blocker'lar (sprint başlarken çözülmesi şart)

1. **`.env.local` template eksik** → Task 8 ilk sırada, Task 1'den önce paralel yapılabilir.
2. **İzmir istasyon listesi yok** → Task 2 data-engineer'a ama lat/lon kaynağı user onayı ister. **Karar:** İzmir Büyükşehir açık veri + Çevre Bakanlığı SIM istasyonları → 5 istasyon (Konak, Bornova, Karşıyaka, Alsancak, Bayraklı) — data-engineer kickoff'ta öneri getirsin, tech-lead onaylasın.
3. **`schema.sql` yok** → Task 7 geçici stub; H4'te `database-architect` refactor edecek, H3 sprint boundary'si bu.
4. **OpenWeatherMap key local'de var mı?** → User teyit etmeli; yoksa `respx` mock mode (`APP_ENV=local` + `OPENWEATHER_MOCK=1` flag) — Task 3 DoD'u her iki modda da yeşil vermeli.

---

## Demo Senaryosu (Hafta 3 sonu, 10 dk)

1. `make down && make up` — tüm stack 30 sn içinde healthy
2. `docker logs aqi-ingestion -f` → APScheduler tick loglarında "Published 5 stations to air-quality-raw"
3. `docker exec aqi-kafka kafka-console-consumer --topic air-quality-raw --from-beginning --max-messages 10` → JSON payload'lar görülüyor (PM2.5, PM10, NO2 dolu)
4. `python -m src.ingestion.csv_loader data/historical/izmir_2024.csv --station-id 1` → "Inserted 8760 rows" (1 yıl saatlik)
5. `psql -h localhost -U app -d air_quality -c "SELECT count(*), min(measured_at), max(measured_at) FROM fact_measurements"` → temizlenmiş tarihsel veri
6. `pytest tests/ingestion/ --cov` → tüm yeşil, coverage ≥80%
7. Conventional commit log: `git log --oneline --grep="feat(ingestion)"` → temiz feature commits

---

## Agent Ataması Özeti

| Agent | Task'ları | Başlangıç sırası |
|-------|-----------|------------------|
| devops-engineer | 1, 8, 9 | **İlk** (venv + env) — Task 2-6 ona bağımlı |
| data-engineer | 2, 3, 4, 5, 6 | Task 1 & 8 biter bitmez paralel |
| database-architect | 7 (dar scope) | Task 1'den sonra, Task 6'dan önce |
| data-quality-engineer | 10 | Task 3-6 bitişinde |
| security-compliance | 11 | PR-review gate (hepsi birleşmeden önce) |

---

## Sprint Çıktı Tablosu (haftalık rapor için)

| Hafta | Hedef | Durum | Agent | Blocker |
|-------|-------|-------|-------|---------|
| 1-2 | Setup + Coolify provision | ✓ | tech-lead + coolify-engineer | - |
| **3** | **Kafka + API + CSV loader** | **🟡 planned** | **data-engineer + devops-engineer** | **İstasyon listesi onayı, OpenWeather key teyit, schema stub** |
| 4 | Star schema + tarihsel yükleme | ⏳ | database-architect | H3 schema stub'ın H4'te partition + BRIN'e genişletilmesi |
| 5 | Dim tabloları + indeks | ⏳ | database-architect | - |

---

## Ret Kriterleri (PR review checklist)

- `SERVICE_PASSWORD_*` yerine hardcoded password → **reject**
- `OPENWEATHER_API_KEY` .env.local.example'da gerçek → **hard reject + security eskalasyon**
- Type hint eksik, `mypy --strict` fail → **reject, revizyon**
- `tests/ingestion/test_*.py` yok → **reject (data-quality-engineer'a geri)**
- Coolify UI'dan manuel değişiklik → **reject (coolify-engineer IaC'e çeksin)**
- `respx` mock mode default olarak `True` (production'da gerçek API atlanır) → **reject**

---

## Sonraki Adım

**İlk handoff:** `devops-engineer` — Task 1 + Task 8 paralel.

Handoff context:
> Hafta 3 sprint başlıyor. İlk blocker `.env.local` template ve venv genişletme. Task 1: `pip install -e ".[dev,ingestion,processing]"`, `pre-commit install`, `make lint && make test` baseline yeşil. Task 8: `.env.local.example` oluştur (compose'un beklediği tüm env vars — CLAUDE.md Secret Management policy'sine uygun, `OPENWEATHER_API_KEY=replace_me_from_1password`). DoD: `cp .env.local.example .env.local && make up` hatasız. Conventional Commits zorunlu. Biter bitmez data-engineer'a Task 2 (istasyon kataloğu) için handoff at.

**Paralel handoff (kickoff'ta):** `data-engineer` → Task 2 öneri (5 İzmir istasyonu lat/lon listesi) + `respx` mock stratejisi için ön-çalışma. Kod yazmaya henüz başlama — blocker'lar user onayında.

**Security pre-review request:** `security-compliance` — Task 3 ve Task 8'de OpenWeatherMap key'in hiçbir log/URL/env example'da görünmediğini doğrulayan checklist hazırla; `httpx` `Authorization: Bearer` yerine query param `appid=` kullanıldığı için access log policy netleşmeli.
