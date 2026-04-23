# Mimari Dokümanı

## Genel Bakış

Bu platform dört ana katmandan oluşur. Her katman bağımsız olarak ölçeklenebilir
ve Docker container'ları içinde çalışır.

## Katman 1: Veri Toplama (Ingestion)

**Bileşenler:** `api_collector.py`, `kafka_producer.py`, `csv_loader.py`

Veri iki kanaldan akar:
- **Streaming kanal:** OpenWeatherMap API → Python Producer → Kafka topic (`air-quality-raw`)
- **Batch kanal:** Tarihsel CSV dosyaları → Pandas temizleme → PostgreSQL doğrudan yükleme

API çağrısı her 60 dakikada bir yapılır (APScheduler ile). Her çağrı İzmir'deki
tanımlı istasyonlar için PM2.5, PM10, NO₂, SO₂, O₃ ve CO değerlerini çeker.

**Neden Kafka?** API'den gelen veri doğrudan veritabanına yazılabilirdi, ancak Kafka
kullanmak şu avantajları sağlar:
- Üretici (API collector) ve tüketici (Spark) birbirinden bağımsız çalışır
- Veri kaybı riski azalır (Kafka diske yazar)
- Birden fazla tüketici aynı veriyi okuyabilir (dashboard + batch + alert)

## Katman 2: Veri İşleme (Processing)

**Bileşenler:** `spark_batch.py`, `spark_streaming.py`, `aqi_calculator.py`

İki işleme modu:
- **Batch:** Tarihsel verinin günlük/haftalık/aylık agregasyonu, hareketli ortalama
- **Streaming:** Kafka'dan Spark Structured Streaming ile okuma, 1 saatlik tumbling
  window'da anlık AQI hesaplama

AQI hesaplama EPA standardını takip eder: her kirletici için breakpoint tablosuna
göre alt-indeks hesaplanır, genel AQI en yüksek alt-indeks değeridir.

## Katman 3: Depolama (Storage)

**Bileşen:** PostgreSQL 16 + Star Schema

Yıldız şeması seçildi çünkü:
- Analitik sorgular (aggregation, GROUP BY) için optimize
- Boyut tabloları küçük, fact tablosu büyük — bu duruma ideal
- OLAP tarzı sorgulamalara uygun

Tablo yapısı:
- `dim_station` — İstasyon kimlik bilgileri ve coğrafi konum
- `dim_time` — Zaman boyutu (saat, gün, ay, mevsim, tatil)
- `dim_pollutant` — Kirletici türü, birim, yasal limit
- `fact_measurements` — Ölçüm değerleri, AQI skoru, kaynak bilgisi

Fact tablosu aylık partitioning ile bölünür. Zaman bazlı sorgular için
BRIN index kullanılır.

## Katman 4: Sunum (Presentation)

**Bileşenler:** Grafana, Streamlit

- **Grafana:** Operasyonel izleme. Anlık AQI, trend grafikleri, alarmlar.
  PostgreSQL'e doğrudan bağlanır. Refresh: 5 dakika.
- **Streamlit:** Analitik keşif. Tarihsel karşılaştırma, korelasyon analizi,
  rapor indirme. Kullanıcı etkileşimli arayüz.

## Veri Akış Diyagramı

```
OpenWeatherMap API ──→ Python Producer ──→ Kafka (air-quality-raw)
                                              │
                                              ├──→ Spark Streaming ──→ PostgreSQL (fact)
                                              │                            │
Tarihsel CSV ──→ Pandas Temizleme ────────────┘                           │
                                                                          ├──→ Grafana
                                                                          └──→ Streamlit
```

## Teknoloji Seçim Gerekçeleri

| Teknoloji | Alternatifler | Neden bu? |
|-----------|---------------|-----------|
| Kafka | RabbitMQ, Redis Streams | Yüksek throughput, replay yeteneği, partition |
| Spark | Flink, plain Python | Batch + streaming tek API, geniş ekosistem |
| PostgreSQL | ClickHouse, TimescaleDB | Basit kurulum, SQL bilgisi yeterli, Grafana desteği |
| Grafana | Metabase, Superset | Real-time refresh, alert desteği, Kafka/PG entegrasyonu |
| Streamlit | Dash, Flask | Hızlı prototipleme, Python-native, sıfır frontend |
| Docker Compose | Kubernetes | Bireysel proje ölçeğinde yeterli, düşük karmaşıklık |
