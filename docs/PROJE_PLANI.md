# Proje Planı — Gerçek Zamanlı Hava Kalitesi İzleme

## Genel Zaman Çizelgesi

| Hafta | Ders Konusu | Proje Aktivitesi | Çıktı |
|-------|-------------|-------------------|-------|
| 1–2 | Giriş, Veri Toplama | Araştırma & mimari tasarım | Mimari diyagram, API seçimi |
| 3 | Veri Temizleme | API bağlantısı, Kafka kurulumu | Çalışan producer + consumer |
| 4 | Veri Depolama | PostgreSQL şema tasarımı | Star schema, tarihsel veri yüklemesi |
| 5 | Veri Modelleme | Boyut tabloları, indeksler | Optimize edilmiş veri ambarı |
| 6 | Büyük Veri / Hadoop | Spark batch işleme | PySpark dönüşüm scriptleri |
| 7 | Apache Spark | Spark Structured Streaming | Kafka → Spark → PostgreSQL akışı |
| **8** | **İlerleme Raporu** | **Rapor yazımı** | **Çalışan pipeline demo + rapor (%40)** |
| 9 | Veri Akışı | Streaming optimizasyonu | Watermark, late data yönetimi |
| 10 | Bulut Tabanlı | Docker Compose paketleme | Tek komutla ayağa kalkan sistem |
| 11 | Veri Güvenliği | KVKK uyumu, maskeleme | Güvenlik dokümantasyonu |
| 12 | Veri Kalitesi | Otomatik kalite kontrolleri | Completeness/freshness metrikleri |
| 13 | Görselleştirme | Grafana + Streamlit dashboard | Çalışan dashboard'lar |
| 14 | ML için Hazırlık | Feature engineering, AQI tahmini | Basit tahmin modeli |
| 15 | Trendler | Dokümantasyon, temizlik | Proje portföyü hazır |
| **16** | **Final Raporu** | **Final rapor yazımı** | **Tamamlanmış proje + rapor (%60)** |

---

## Detaylı Haftalık Plan

### Hafta 1–2: Araştırma ve Mimari Tasarım

**Hedef:** Proje kapsamını netleştirmek ve teknik mimariyi belirlemek.

**Yapılacaklar:**
- OpenWeatherMap API'ye kayıt ol ve ücretsiz API key al
  - Air Pollution API: https://openweathermap.org/api/air-pollution
  - Weather API: https://openweathermap.org/api/one-call-3
- İzmir'deki hava kalitesi izleme istasyonlarını belirle (en az 3–5 nokta)
- Çevre Bakanlığı açık veri portalından tarihsel veri setlerini indir
- Veri şemasının ilk taslağını çiz (hangi alanlar, veri tipleri, ilişkiler)
- Mimari diyagramı oluştur (bileşenler arası veri akışı)
- Geliştirme ortamını hazırla (Python 3.11+, Docker Desktop, IDE)

**Teslim:** Mimari dokümanı (docs/MIMARI.md), API erişim testi

---

### Hafta 3: API Bağlantısı ve Kafka Kurulumu

**Hedef:** Veri toplama katmanını çalışır hale getirmek.

**Yapılacaklar:**
- `api_collector.py`: OpenWeatherMap'ten JSON veri çekme
  - PM2.5, PM10, NO₂, SO₂, O₃, CO değerleri
  - Sıcaklık, nem, rüzgar hızı/yönü
  - Her istasyon için saatlik çekim (cron veya scheduler)
- Kafka'yı Docker ile ayağa kaldırma
  - Topic oluşturma: `air-quality-raw`, `weather-raw`
  - Partition ve replication ayarları
- `kafka_producer.py`: API'den çekilen veriyi Kafka'ya yazma
  - JSON serialization, schema tanımlama
  - Hata yönetimi (retry logic, dead letter queue)
- `csv_loader.py`: Tarihsel CSV dosyalarını temizleme ve yükleme
  - Eksik değer tespiti ve interpolasyon
  - Birim standardizasyonu (µg/m³)
  - Anomali tespiti (negatif değer, aşırı uç değer)

**Teslim:** Kafka'ya akan canlı veri akışı, temizlenmiş tarihsel veri

---

### Hafta 4–5: Veritabanı Tasarımı ve Modelleme

**Hedef:** Star schema ile veri ambarı oluşturmak.

**Yapılacaklar:**
- `schema.sql` dosyasını tamamla:
  - `dim_station`: İstasyon bilgileri (id, ad, enlem, boylam, ilçe)
  - `dim_time`: Zaman boyutu (tarih, saat, gün_adı, ay, mevsim, tatil_mi)
  - `dim_pollutant`: Kirletici türleri (id, ad, birim, WHO limiti, TR limiti)
  - `fact_measurements`: Ölçümler (station_id, time_id, pollutant_id, değer, aqi_skoru)
- İndeks stratejisi: Zaman bazlı sorgular için BRIN index, istasyon bazlı B-tree
- Partitioning: fact tablosunu aylık partition'lara böl
- `db_writer.py`: Spark'tan PostgreSQL'e yazma fonksiyonları
- İlk veri yüklemesi: Tarihsel CSV → PostgreSQL

**Teslim:** Çalışan veritabanı, yüklenmiş tarihsel veri, sorgu performans testleri

---

### Hafta 6–7: Spark ile Veri İşleme

**Hedef:** Batch ve streaming dönüşüm pipeline'ını kurmak.

**Yapılacaklar:**
- `spark_batch.py`: Tarihsel veri üzerinde batch işleme
  - Saatlik ham veri → günlük/haftalık/aylık agregasyonlar
  - Hareketli ortalama (7 gün, 30 gün)
  - İstasyonlar arası korelasyon matrisi
  - Mevsimsel pattern tespiti
- `aqi_calculator.py`: AQI (Air Quality Index) hesaplama
  - EPA standardına göre breakpoint tablosu
  - Her kirletici için alt-indeks hesaplama
  - Genel AQI = max(alt-indeksler)
  - Kategori etiketleme: İyi / Orta / Hassas / Sağlıksız / Çok Sağlıksız / Tehlikeli
- `spark_streaming.py`: Kafka → Spark Structured Streaming → PostgreSQL
  - Watermark: 10 dakika (geç gelen veriler için)
  - Tumbling window: 1 saatlik pencerede ortalama
  - Sliding window: 15 dakikalık kayma ile 1 saatlik pencere
  - Output mode: append (fact tablosu), complete (aggregation tablosu)

**Teslim:** Çalışan batch + streaming pipeline, AQI hesaplama doğrulaması

---

### Hafta 8: İlerleme Raporu (%40)

**Raporda Sunulacaklar:**
1. Proje tanımı ve motivasyon
2. Mimari tasarım ve teknoloji seçim gerekçeleri
3. Veri kaynakları ve toplama stratejisi
4. Veritabanı şeması ve modelleme kararları
5. Spark işleme pipeline'ının açıklaması
6. Karşılaşılan zorluklar ve çözümler
7. Canlı demo: API → Kafka → Spark → PostgreSQL akışı
8. Kalan haftalardaki plan

---

### Hafta 9: Streaming Optimizasyonu

**Yapılacaklar:**
- Late data yönetimi: Watermark stratejisi fine-tuning
- Exactly-once semantics: Kafka offset yönetimi
- Checkpoint mekanizması: Spark streaming state recovery
- Backpressure yönetimi: Kafka consumer lag monitoring
- Performans metrikleri: İşleme gecikmesi (latency) ölçümü

---

### Hafta 10: Docker ile Paketleme

**Yapılacaklar:**
- `docker-compose.yml`: Tüm servisleri tek dosyada tanımla
  - Kafka + Zookeeper
  - Spark master + worker
  - PostgreSQL
  - Grafana
  - Streamlit
- Her servis için Dockerfile yaz
- Volume mapping: Veri kalıcılığı
- Network configuration: Servisler arası iletişim
- Health check tanımları
- `.env.example`: Hassas bilgilerin yönetimi

---

### Hafta 11: Veri Güvenliği ve Gizlilik

**Yapılacaklar:**
- Konum verisi hassasiyeti: İstasyon çevresindeki bina/kişi bilgisi yok
- PostgreSQL erişim kontrolü: Read-only kullanıcılar
- API key yönetimi: Environment variable, asla hardcoded değil
- KVKK uyumluluk dokümanı: Toplanan verinin kişisel olmadığını belgeleme
- Kafka SSL/TLS yapılandırması (dokümantasyon seviyesinde)
- Veri saklama politikası: 1 yıldan eski raw verinin arşivlenmesi

---

### Hafta 12: Veri Kalitesi ve Yönetimi

**Yapılacaklar:**
- `data_quality.py`: Otomatik kalite kontrol framework'ü
  - Completeness: Beklenen kayıt sayısı vs gelen kayıt sayısı
  - Freshness: Son verinin yaşı (alert: >2 saat)
  - Validity: Değer aralığı kontrolleri (PM2.5: 0–500 µg/m³)
  - Consistency: İstasyonlar arası tutarlılık kontrolü
  - Uniqueness: Duplikasyon tespiti
- Alert mekanizması: Kalite eşiği aşıldığında log/notification
- Kalite raporu: Günlük otomatik özet üretimi
- Data lineage: Verinin kaynaktan hedefe izlenebilirliği

---

### Hafta 13: Görselleştirme

**Yapılacaklar:**
- Grafana dashboard:
  - Anlık AQI göstergesi (gauge panel) — her istasyon için
  - Kirletici trend grafikleri (time series panel)
  - İstasyonlar arası karşılaştırma (bar chart)
  - Alarm paneli: Eşik aşımları
  - Harita paneli: İstasyonların coğrafi konumları
- Streamlit uygulaması:
  - Tarih aralığı seçici ile tarihsel analiz
  - İstasyon ve kirletici filtresi
  - Korelasyon ısı haritası (heatmap)
  - Dağılım grafikleri ve box plot
  - CSV/PDF olarak rapor indirme

---

### Hafta 14–15: ML Hazırlığı ve Tamamlama

**Yapılacaklar:**
- Feature engineering:
  - Lag değerleri (t-1, t-6, t-24 saat önceki ölçüm)
  - Rolling statistics (ortalama, std, min, max)
  - Mevsimsel özellikler (ay, gün_tipi, saat_dilimi)
  - Meteorolojik özellikler (sıcaklık, nem, rüzgar)
- Basit tahmin modeli: Prophet veya ARIMA ile 24 saatlik AQI tahmini
- Model sonuçlarını Streamlit'te gösterme
- Proje dokümantasyonunu tamamla
- Kod temizliği, docstring'ler, type hint'ler
- GitHub README ve demo ekran görüntüleri

---

### Hafta 16: Final Raporu (%60)

**Rapor İçeriği:**
1. Özet (Executive Summary)
2. Giriş ve motivasyon
3. Literatür / benzer projeler incelemesi
4. Sistem mimarisi (detaylı diyagramlar)
5. Veri kaynakları ve toplama süreci
6. Veri modeli ve depolama stratejisi
7. ETL/ELT pipeline tasarımı
8. Gerçek zamanlı işleme detayları
9. Veri kalitesi framework'ü
10. Güvenlik ve gizlilik önlemleri
11. Görselleştirme ve dashboard tasarımı
12. ML pipeline ve tahmin sonuçları
13. Performans metrikleri ve değerlendirme
14. Karşılaşılan zorluklar ve çözümler
15. Gelecek çalışmalar
16. Kaynakça
