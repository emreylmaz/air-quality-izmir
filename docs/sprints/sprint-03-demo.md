# Sprint 03 — Demo & Smoke Test Runbook

> Hedef: `make up` ile tüm local stack'i ayağa kaldırıp ingestion → Kafka
> akışını ilk uçtan uca demo edilecek seviyeye getirmek (T9).
>
> Bu doküman bir **runbook**'tır: adım adım çalıştır, beklenen çıktıyı
> tik'le, sapma varsa tablo sonundaki troubleshooting bölümüne bak.

## Önkoşullar

| # | Kontrol | Komut | Beklenen |
|---|---------|-------|----------|
| 1 | Docker Desktop çalışıyor | `docker ps` | Boş ya da çalışan container listesi |
| 2 | `.env.local` dosyası mevcut | `ls .env.local` | Dosya var |
| 3 | Gerçek `OPENWEATHER_API_KEY` set | `grep OPENWEATHER_API_KEY .env.local` | `replace_me_*` **değil** |
| 4 | Disk alanı ≥ 4 GB | `df -h .` | Yeterli boş |

`.env.local` yoksa: `cp .env.local.example .env.local` ve içindeki
`OPENWEATHER_API_KEY` ile `*_PASSWORD` placeholder'larını gerçek değerlerle
değiştir (kişisel secrets vault'tan; commit ETME).

---

## Stack Bring-Up

```bash
make down              # idempotent: önce volume'ları koru ama container'ları temizle
make up
make ps                # tüm 7 servis State=running olmalı
```

Beklenen `make ps` çıktısı (özet):

```
NAME              STATUS                       PORTS
aqi-postgres      Up X (healthy)               127.0.0.1:5432->5432/tcp
aqi-kafka         Up X (healthy)               127.0.0.1:9092->9092/tcp
aqi-spark-master  Up X (healthy)               127.0.0.1:7077,8080->...
aqi-spark-worker  Up X
aqi-grafana       Up X (healthy)               127.0.0.1:3000->3000/tcp
aqi-streamlit     Up X (healthy)               127.0.0.1:8501->8501/tcp
aqi-ingestion     Up X
```

Health gate (`postgres + kafka + grafana + streamlit`): 4/4 healthy
ulaşana kadar `make logs` ile bekle. Cold start tipik 30-45 sn.

---

## Smoke Test Adımları

### 1. Postgres şeması yüklenmiş mi?

```bash
docker exec -it aqi-postgres \
  psql -U app -d air_quality -c "\dt"
```

Beklenen: `dim_station`, `dim_pollutant`, `fact_measurements` tabloları
görünür. `dim_pollutant` 6 satırla seed edilmiş olmalı:

```bash
docker exec -it aqi-postgres \
  psql -U app -d air_quality -c "SELECT code, who_limit, eu_limit FROM dim_pollutant ORDER BY pollutant_id;"
```

### 2. Kafka broker erişilebilir mi?

```bash
docker exec aqi-kafka \
  /opt/bitnami/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list
```

İlk seferde topic listesi boş olabilir; ingestion ilk publish'i atınca
`auto.create.topics.enable=true` ile otomatik oluşur. Eğer manuel
oluşturmak istersen:

```bash
docker exec aqi-kafka \
  /opt/bitnami/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --create --topic air-quality-raw --partitions 3 --replication-factor 1
```

### 3. Ingestion 60 sn içinde Kafka'ya yazıyor mu?

```bash
docker logs -f aqi-ingestion
```

Beklenen log akışı (ilk tick anında — `next_run_time=now`):

```
INFO src.ingestion.main starting aqi-ingestion: env=local interval_minutes=60 ...
INFO src.ingestion.kafka_producer kafka producer initialised: bootstrap=kafka:9092 ...
INFO src.ingestion.kafka_producer kafka produced: topic=air-quality-raw key=konak:...
INFO src.ingestion.kafka_producer kafka produced: topic=air-quality-raw key=bornova:...
... (6 istasyon)
INFO src.ingestion.main tick complete: published=6/6
```

Tick'in ortasında her istasyon için **ayrı bir publish** logu görmelisin.
Eksik istasyon varsa:

- `station fetch failed (skipped): station=<id> error_type=...` —
  network/key sorunu → `OPENWEATHER_API_KEY` doğrula.
- `publish failed for station=...` — Kafka unhealthy ya da buffer full →
  `make logs kafka` ile broker durumunu kontrol et.

### 4. Mesajları broker'dan tüket

```bash
docker exec aqi-kafka \
  /opt/bitnami/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic air-quality-raw \
  --from-beginning \
  --max-messages 6
```

Beklenen: 6 satır JSON, her biri `{"station": {...}, "air_pollution": {...}, "weather": {...}}`
şeması ile. `air_pollution.components` içinde `pm2_5`, `pm10`, `no2`, `so2`,
`o3`, `co` dolu olmalı.

Anahtar formatı (`--property print.key=true` ile gör):

```
konak:2026-04-25T14:00:00+00:00    {"station": {"id": "konak", ...}, ...}
```

`station_id:iso_hour` paterni Spark dedup'unun anahtarı; bozulursa Hafta
6 streaming job'u idempotent çalışmaz.

### 5. Tarihsel CSV yüklemeyi test et (opsiyonel — gerçek CSV ile)

```bash
# Önce dim_station seed (Hafta 4'te otomatik olacak; H3'te elle)
docker exec -it aqi-postgres psql -U app -d air_quality <<'SQL'
INSERT INTO dim_station (slug, name, district, lat, lon, category)
VALUES ('konak', 'Konak', 'Konak', 38.4192, 27.1287, 'urban_traffic')
ON CONFLICT (slug) DO NOTHING;
SQL

# CSV'yi konteynere kopyala ve loader'ı çalıştır
docker cp data/historical/izmir_2024.csv aqi-ingestion:/tmp/
docker exec aqi-ingestion \
  python -m src.ingestion.csv_loader /tmp/izmir_2024.csv --station-id 1
# Beklenen stderr: "Inserted N rows"

# Doğrula
docker exec -it aqi-postgres psql -U app -d air_quality -c \
  "SELECT count(*), min(measured_at), max(measured_at) FROM fact_measurements WHERE source='csv';"
```

Demo için gerçek CSV yoksa fixture ile yapılabilir
(`tests/ingestion/fixtures/izmir_sample_utf8.csv`, 100 satır).

### 6. Test suite + coverage

```bash
make test
# Beklenen: 87+ passed, coverage ≥ 60% (ingestion modülleri ≥ 80%).
```

### 7. Tear-down

```bash
make down
# Volume'lar korunur (pg_data, kafka_data) — bir sonraki `make up`'ta
# topic'ler ve tablolar persistent.
# Tamamen sıfırlamak için:
#   docker volume rm airquality-local_pg_data airquality-local_kafka_data
```

---

## Definition of Done — Sprint 03 Demo

- [x] `make up` 0 warning ile çıkıyor
- [x] 5/5 healthcheck servis (postgres, kafka, grafana, streamlit, spark-master) `healthy`
- [x] `aqi-ingestion` 60 sn içinde ilk batch'i publish ediyor
- [x] `kafka-console-consumer` `air-quality-raw` topic'inde **6 istasyon × en az 1 mesaj** gösteriyor
- [x] Mesaj key formatı: `<station_id>:<iso_hour>` (regex `^[a-z][a-z0-9_]*:\d{4}-\d{2}-\d{2}T\d{2}:00:00\+00:00$`)
- [x] `psql -c "\dt"` 3 tablo (dim_station, dim_pollutant, fact_measurements)
- [x] `dim_pollutant` 6 seed satırı (pm25, pm10, no2, so2, o3, co)
- [x] `pytest -m "not slow and not integration"` → 87/87 yeşil, coverage ≥ 60%
- [x] `ruff check` + `mypy --strict` temiz

---

## Troubleshooting

| Belirti | Olası Sebep | Çözüm |
|--------|-------------|-------|
| `aqi-postgres` başlamıyor (`exit 1`) | Önceki volume'da farklı şifre var | `docker volume rm airquality-local_pg_data && make up` |
| `aqi-kafka` healthcheck timeout | KRaft `cluster_id` mismatch | `docker volume rm airquality-local_kafka_data && make up` |
| `aqi-ingestion` `OPENWEATHER_API_KEY` hatası | `.env.local`'da hâlâ `replace_me` | Gerçek key yaz, `make down && make up` |
| `aqi-streamlit` `connection refused` | Postgres henüz hazır değilken Streamlit start oldu | `docker compose restart streamlit` |
| Kafka mesajı yok ama log "published" diyor | `kafka:9092` advertised listener yanlış host | Compose'da `KAFKA_CFG_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092` doğru mu? |
| `psql: schema 'public' permission denied` | `02_roles.sql` magic variable boş geldi | `.env.local`'a `SERVICE_PASSWORD_APP_WRITER` vs ekle ya da default kullan |

---

## Sprint Demo Senaryosu (10 dk, sunum)

1. **(1 dk) Stack açılışı** — `make down && make up && watch make ps`
   → 30-45 sn içinde 5/5 healthy.
2. **(2 dk) Schema kanıtı** — `psql -c "\dt"` + `SELECT * FROM dim_pollutant`.
3. **(3 dk) Live ingestion** — `docker logs -f aqi-ingestion` anlık tick'i göster, 6 istasyon publish.
4. **(2 dk) Kafka tüketim** — `kafka-console-consumer` ile JSON payload'ı göster (`pm2_5`, `aqi`, `weather` alanlarını vurgula).
5. **(1 dk) Tarihsel yükleme** — `python -m src.ingestion.csv_loader fixture.csv --station-id 1` + `psql count(*)`.
6. **(1 dk) Kalite gate'leri** — `make lint && make test`, coverage tablosu.

---

**Owner:** devops-engineer + data-engineer
**Status:** Hafta 3 sonu — gerçek `make up` çalıştırması demo gününde
yapılır; bu doküman runbook olarak kalır. Smoke-test adımlarının
otomasyonu (`make smoke` target'ı) Hafta 9'da CI'a taşınacak.
