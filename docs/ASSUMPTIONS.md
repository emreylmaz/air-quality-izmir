# Doğrulanmamış Varsayımlar

Bu dosya, geliştirme sürecinde yapılan ve henüz kanıtlanmamış teknik
varsayımların takibi içindir. İlk gerçek API çağrısı / integration sonrası
buradaki her satır ya doğrulanır, ya da düzeltilir.

## Coolify API

| # | Varsayım | Doğrulama yolu | Durum |
|---|----------|----------------|-------|
| 1 | `POST /api/v1/databases/postgresql` payload: `project_uuid`, `environment_name`, `server_uuid`, `name`, `image`, `is_public` yeterli | İlk `make coolify-apply`, Coolify UI + API response karşılaştırma | ✅ 201 Created (2026-04-23) |
| 2 | Service template ID `grafana-with-postgresql` güncel Coolify sürümünde mevcut | Coolify UI → Add Resource → Service → Search | ✅ 201 Created (2026-04-23) |
| 3 | `PATCH /api/v1/applications/{uuid}/envs/bulk` payload: `{"data": [...]}` formatında kabul ediyor | İlk `sync_secrets push` çağrısı | ✅ 201 Created (2026-04-23) |
| 4 | Env variable flag'leri `is_build_time`, `is_literal`, `is_multiline` davranışı | Test env üzerinde deneme | ⏳ (deploy sonrası runtime doğrulama) |
| 5 | `List applications` response root `{"data": [...]}` mı, düz array mı? | İlk `list_applications` log | ✅ Düz array (2026-04-23) |
| 9 | Coolify description field `-` (hyphen) kabul eder, `—` (em-dash) kabul ETMEZ | İlk `POST /projects` 422 validation error | ✅ ASCII-only punctuation zorunlu (2026-04-23) |
| 10 | `POST /applications/public` payload'da `ports_exposes` **zorunlu** (background worker bile olsa) | İlk `aqi-ingestion` create 422 | ✅ Dummy port "8080" geçer (2026-04-23) |
| 11 | Resource create ≠ deploy. Coolify explicit `/start` endpoint'i tetiklenmeden container başlamaz. | `status` komutu `exited` döner | ✅ Manuel deploy gerekli (2026-04-23) |
| 6 | Magic Variable referansı `${SERVICE_PASSWORD_X}` env value içinde substituted oluyor | `aqi-streamlit` deploy sonrası `DATABASE_URL` env incele | ⏳ |
| 7 | 429 response `Retry-After` header int-second ile dönüyor | Rate limit test (deliberately spam GET) | ⏳ |
| 8 | Rezerve edilen Docker Compose "dockercompose" build-pack desteği aktif | `aqi-kafka` compose-app deploy denemesi (H10) | ⏳ |

## OpenWeatherMap API

| # | Varsayım | Doğrulama yolu | Durum |
|---|----------|----------------|-------|
| 1 | Free tier `air_pollution` endpoint 60 call/min rate limit | API docs re-read + test | ⏳ |
| 2 | PM2.5/PM10 değerleri µg/m³ olarak dönüyor (birim dönüşümü gerekmeyecek) | Sample response inspect | ⏳ |
| 3 | Student Pack başvurusu cevabı gelecek (ek quota) | Başvuru follow-up | ⏳ |

## Veri Modeli

| # | Varsayım | Doğrulama yolu | Durum |
|---|----------|----------------|-------|
| 1 | Aylık partition yeterli (günlük partition overkill) | Hacim hesabı: 5 station × 6 pollutant × 24h × 30day = 21 600 row/ay | ✅ |
| 2 | BRIN index time_id üzerinde B-tree'den daha iyi (insert-heavy, sequential) | H4'te EXPLAIN benchmark | ⏳ |
| 3 | `grafana_ro` role `v_hourly_aqi` view'ine doğrudan erişebiliyor | Role setup sonrası test query | ⏳ |

## Spark

| # | Varsayım | Doğrulama yolu | Durum |
|---|----------|----------------|-------|
| 1 | PySpark 3.5.1 + Python 3.11 uyumluluğu sorunsuz | Local SparkSession boot test | ⏳ |
| 2 | Watermark 10 dk İzmir API gecikmesi için yeterli | H7 monitoring | ⏳ |
| 3 | JDBC batch size 1000 PostgreSQL için optimal | H7 throughput test | ⏳ |

## Altyapı

| # | Varsayım | Doğrulama yolu | Durum |
|---|----------|----------------|-------|
| 1 | Coolify VPS min 4 GB RAM (Kafka Coolify'a alındığında yeterli) | `free -h` on VPS | ⏳ |
| 2 | `direnv` Windows WSL2'de stabil çalışıyor | User confirmation | ⏳ |
| 3 | `detect-secrets` v1.5.0 baseline compatibility | `pre-commit run` | ⏳ |

## Güncelleme Kuralı

Bir varsayım doğrulandığında: `⏳` → `✅` + referans (commit hash / log snippet).
Yanlış çıktıysa: `⏳` → `❌` + düzeltme notu + ilgili kodu güncelle.
