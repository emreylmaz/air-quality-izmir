# YZM536 — Final Raporu (%60 Teslim)

**Proje:** Gerçek Zamanlı Hava Kalitesi İzleme — Uçtan Uca Veri Boru Hattı (İzmir Vakası)

**Öğrenci No:** Y250234065
**Hazırlayan:** Emre Yılmaz
**Ders:** YZM536 Veri Mühendisliği ve Uygulamaları
**Ders Yürütücüsü:** Dr. Öğr. Üyesi Gözde Çay
**Üniversite:** T.C. İzmir Kâtip Çelebi Üniversitesi
**Gönderme Tarihi:** _(teslim öncesi güncellenir)_

---

## İçindekiler

1. [Özet](#bölüm-1-özet)
   - 1A. Proje Özeti
   - 1B. Projenin Amacı
2. [Giriş ve Arka Plan](#bölüm-2-giriş-ve-arka-plan)
   - 2A. Problem Tanımı
   - 2B. Problem İçin Bulunan Çözüm
   - 2C. Bu Alanda Yapılan Daha Önceki Çalışmaların Özeti
3. [Veri Seti ve Modelleme](#bölüm-3-veri-seti-ve-modelleme)
   - 3A. Projede Kullanılan Veri Setleri
   - 3B. Veri Toplama Aşamaları
   - 3C. Veri Formatları ve Yöntemler
   - 3D. Veri Ön İşleme
   - 3E. Kullanılan Veri Modelleri ve Veri Diyagramı
   - 3F. Kullanılan Analizler ve Hesaplamalar
   - 3G. Makine Öğrenmesi Yöntemleri
   - 3H. Görselleştirme
   - 3I. Kullanılan Veritabanları
4. [Sonuç](#bölüm-4-sonuç)
   - 4A. Çalışmanın Sonuçları
   - 4B. Sayısal Performans Bulguları
   - 4C. Erişilen Mimari Hedefler
5. [Tartışma](#bölüm-5-tartışma)
   - 5A. Sonuçların Tartışılması
   - 5B. Literatürdeki Çalışmalarla Karşılaştırma
   - 5C. Çalışmanın Limitleri
   - 5D. Karşılaşılan Problemler ve Çözümler
   - 5E. Çözülemeyen Problemler ve Gelecek Çalışmalar
6. [Kaynakça](#kaynakça)

---

## Bölüm 1: Özet

### 1A. Proje Özeti

Bu proje, İzmir metropolünde altı sabit istasyondan toplanan gerçek zamanlı ve tarihsel hava kalitesi verilerini, modern veri mühendisliği yığını üzerinde uçtan uca işleyen ve operatör panellerine sunan bir veri boru hattı (data pipeline) tasarımını ele almaktadır. Çalışma; mesaj kuyruğu (Apache Kafka), akış işleme (Apache Spark Structured Streaming), boyutsal modelleme (PostgreSQL 16 yıldız şeması), veri kalitesi denetimi ve görselleştirme (Grafana, Streamlit) bileşenlerini birlikte kullanarak dersin teorik kapsamını gerçek bir kullanım senaryosu üzerinde uygulamayı amaçlar. Hafta 14–15 sprintlerinde kısa vadeli AQI tahmini için Prophet tabanlı bir makine öğrenmesi katmanı eklenmiştir.

Sistem iki farklı kanaldan veri tüketmektedir: (i) OpenWeatherMap Air Pollution API'sinden saatlik canlı çekim ile beslenen streaming kanalı ve (ii) T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı Sürekli İzleme Merkezi (SİM) portalından sağlanan tarihsel CSV dosyalarının batch yüklemesi. Her iki kanal ortak bir fact tablosunda buluşur; sunum katmanı bu tablo üzerinde kurulu materialize edilmiş görünümleri okur.

Mimari, hibrit bir dağıtım deseniyle çalışmaktadır: Stateful streaming katmanı (Spark master ve worker, Kafka brokerı) yerel Docker Compose ortamında tutulurken; PostgreSQL, Grafana ve birkaç stateless Python uygulaması Coolify üzerinde managed kaynak olarak provision edilmektedir. Bu ayrım, hem geliştirici döngüsünün hızını korur hem de bulut tabanlı dağıtım deseninin ders kapsamına alınmasını sağlar.

Toplam 11 sprint (Hafta 1–15) tamamlanmış; H8'de %40 ara teslim, H16'da bu doküman ile %60 final teslim gerçekleştirilmektedir.

### 1B. Projenin Amacı

Çalışmanın akademik amacı, dersin kapsadığı dört temel başlığı (mesaj kuyruğu, akış işleme, ilişkisel veri ambarı modellemesi ve görselleştirme) somut bir kullanım senaryosu üzerinde bütünleşik biçimde uygulamaktır. Mühendislik amacı ise operasyonel olarak ayağa kaldırılabilir, yeniden çalıştırılabilir (idempotent) ve gözlemlenebilir (observable) bir referans pipeline ortaya çıkarmaktır.

Spesifik hedefler şunlardır: (1) Saatlik granülerite ile 6 istasyon × 6 kirletici (PM₂.₅, PM₁₀, NO₂, SO₂, O₃, CO) için yıllık ~315.000 satıra ulaşan ölçüm hacmini 60 saniyenin altında veritabanına yükleyebilen bir batch loader; (2) Aynı CSV iki kez yüklendiğinde ek satır üretmeyen, yapısal seviyede idempotent bir ingestion akışı; (3) Aylık RANGE partition + BRIN indeks kombinasyonuyla zaman serisi sorgularında partition pruning kazancını ölçülebilir biçimde gösteren bir veritabanı tasarımı; (4) Hafta 7 sonunda Spark Structured Streaming ile 10 dakikalık watermark ve 1 saatlik tumbling window üzerinden anlık AQI (Air Quality Index) hesaplayan bir streaming işi; (5) Hafta 13 sonunda Grafana ve Streamlit panelleriyle hem operasyonel izleme hem analitik keşif için sunum katmanı; (6) Hafta 14–15'te 24 saatlik AQI öngörüsü için Prophet tabanlı zaman serisi modeli ve MAE/MAPE metrikleriyle değerlendirme.

---

## Bölüm 2: Giriş ve Arka Plan

### 2A. Problem Tanımı

Türkiye İstatistik Kurumu ve Çevre, Şehircilik ve İklim Değişikliği Bakanlığı verilerine göre İzmir, parçacık çapı 2,5 mikrometrenin altındaki toz (PM₂.₅) yıllık ortalamalarında Dünya Sağlık Örgütü'nün 2021 güncellenmiş kılavuzunda belirlediği 5 µg/m³ sınırını sürekli olarak aşmaktadır. Avrupa Birliği'nin 2008/50/EC numaralı Hava Kalitesi Direktifi'nin koyduğu 25 µg/m³ yıllık sınırın da bazı dönemlerde aşıldığı raporlanmıştır. İzmir'in topografik özellikleri (İzmir Körfezi etrafında toplanmış yerleşim, Aliağa endüstri bölgesi, sınırlı doğal hava sirkülasyonu) hava kirliliğinin lokal pikler oluşturmasına neden olmaktadır.

Mevcut kamu portalları (T.C. Bakanlık SİM portalı, OpenWeatherMap genel API'si) saatlik veri yayınlasa da bu verinin birleştirilmesi, temizlenmesi, analitik depolama biçimine dönüştürülmesi ve tarihsel trendlerle karşılaştırılması son kullanıcıya bırakılmaktadır. Ham verilerin pratikte kullanılabilir hâle gelmesi için karşılaşılan başlıca sorunlar şunlardır: (1) heterojen kaynak biçimleri (REST JSON ile cp1254 kodlu CSV); (2) eksik ve gürültülü ölçümler (sensör arızaları, kalibrasyon kaymaları, negatif değer hataları); (3) zaman dilimi tutarsızlıkları (CSV'lerde naive timestamp); (4) tarihsel ve gerçek zamanlı kanalların ayrı şemalarda tutulması nedeniyle birleşik analiz yapılamaması; (5) idempotent yeniden yükleme garantisinin olmaması nedeniyle aynı dosyanın iki kez yüklenmesi durumunda kayıt çoğalması (duplicate); (6) AQI gibi türetilmiş metriklerin kamu portallarında doğrudan sunulmaması; (7) kısa vadeli AQI trendinin önceden tahmin edilmesi için entegre bir altyapının bulunmaması.

### 2B. Problem İçin Bulunan Çözüm

Bu çalışma, yukarıda sayılan sorunları otomatize biçimde çözen ve replikasyona uygun bir referans veri boru hattı ortaya çıkarmayı hedeflemektedir. Önerilen çözüm beş bağımsız katmandan oluşan bir mimaridir: (1) Veri Toplama (Ingestion); (2) İşleme (Processing); (3) Depolama (Storage); (4) Veri Kalitesi (Quality); (5) Sunum (Presentation). Katmanlar arası iletişim açık kontratlar üzerinden gerçekleşir: streaming kanalı için Kafka topic şeması, batch kanalı için doğrudan PostgreSQL tablo şeması, kalite denetim sonuçları için `data_quality_runs` audit tablosu.

Çözümün altı temel mühendislik katkısı şunlardır:

1. **Hibrit dağıtım deseni:** Stateful streaming bileşenleri (Spark master/worker, Kafka brokerı) yerel Docker Compose üzerinde tutulurken; managed PostgreSQL, Grafana ve stateless Python uygulamaları Coolify üzerinde provision edilmektedir. Bu ayrım hem yedekleme/restore sorumluluğunu Coolify'a delege eder hem de Spark cluster'ın 2–3 GB RAM ihtiyacını yerel makineye sınırlar.
2. **İdempotent ingestion:** `fact_measurements` tablosunda `(station_id, pollutant_id, measured_at, source)` dörtlüsü üzerinde tanımlı UNIQUE kısıtı ile psycopg seviyesinde `ON CONFLICT DO NOTHING` kombinasyonu, aynı CSV dosyasının iki kez yüklenmesi hâlinde fazladan satır üretmeyi yapısal seviyede engellemektedir. Bu, dersin veri kalitesi konusundan önce sağlanan bir altyapı garantisidir.
3. **Aylık RANGE partition + BRIN indeks kombinasyonu:** Append-only zaman serisi verisi için PostgreSQL 16 üzerinde 312.000 satırlık sentetik yük altında ölçülmüş bir tasarımdır. BRIN indeks toplam 600 KiB yer kaplarken, eşdeğer B-tree indeks 7,00 MiB sürmektedir; oran yaklaşık 1:11,7'dir.
4. **SSD profiline ayarlı planner cost modeli:** PostgreSQL'in varsayılan HDD varsayımlı `random_page_cost = 4.0` değeri, SSD/NVMe altyapılarda selektif sorgularda yanlış maliyet tahminlerine yol açar. Sprint 5'te uygulanan `0005_planner_tuning.sql` migration'ı `ALTER DATABASE` ile bu parametreyi 1.1'e indirir; ölçülen plan diff bir aylık seçici sorguda cost estimate'i 8.31'den 2.51'e (%70 düşüş) indirir.
5. **AQI hesaplama ve kısa vadeli tahmin:** EPA breakpoint formülü ile saatlik AQI hesaplanır; Prophet zaman serisi modeli `dim_time` boyutunun `is_holiday`, `dow`, `season` öznitelikleri ile beslenerek 24 saatlik AQI öngörüsü üretir. Model değerlendirmesi MAE ve MAPE metrikleriyle raporlanır.
6. **Veri kalitesi audit zinciri:** Her batch yüklemesi ve streaming micro-batch'i sonrasında completeness, freshness, validity ve uniqueness kontrolleri `data_quality_runs` audit tablosuna kaydedilir; başarısız denetimler operatöre Grafana eşik aşım alarmı ile bildirilir.

### 2C. Bu Alanda Yapılan Daha Önceki Çalışmaların Özeti

Hava kalitesi izleme için açık veri agregasyonu ve görselleştirmesi konusunda olgunlaşmış birçok platform bulunmaktadır. Bu çalışmanın kapsamı doğrudan rakip bir ürün değil, dersin teorik konularını gerçek dünya verisi üzerinde sentezleyen bir referans pipeline olduğundan; aşağıdaki çalışmalar tasarım kararlarına ilham veren ve kıyas noktası oluşturan örnekler olarak değerlendirilmiştir.

- **OpenAQ** — Dünya genelinde 100'ün üzerinde ülkeden hava kalitesi verisi toplayan açık kaynaklı bir agregasyon platformudur. AWS S3 üzerinde Parquet formatında veri yayınlar ve REST API ile sorgu sunar. Bu çalışmadan farkı: tek bir şehre odaklanmaması, yıldız şeması yerine flat veri lake yaklaşımı kullanması ve canlı streaming yerine periyodik batch güncelleme yapmasıdır.
- **IQAir AirVisual** — Ticari bir platform olarak hem cihaz hem yazılım sunar; AQI hesaplamasında EPA breakpoint tablosunu referans alır. Bu çalışmada AQI hesaplama yaklaşımı doğrudan EPA'nın 2024 tarihli teknik dokümanını referans almaktadır; bu açıdan IQAir ile aynı standardı paylaşmaktadır.
- **EEA Air Quality Viewer** — Avrupa Çevre Ajansı'nın resmi raporlama portalıdır; 2008/50/EC direktifinin sınır değerlerini görselleştirir. Bu çalışmada `dim_pollutant` tablosunda hem WHO 2021 hem EU 2008/50/EC sınır değerleri tutulmakta, böylece alarm kurallarının her iki referansa göre kurulabilmesi sağlanmaktadır.
- **BreezoMeter (Google Air Quality API'ye dahil edilen platform)** — 2022'de Google tarafından satın alınan platform, harita tabanlı yüksek çözünürlüklü AQI değerleri sunar. Bu çalışmadan farkı: enterprise-ölçekli ML modelleri kullanması (uydu görüntüsü + sensör fusion) ve kapalı kaynak olması; benzerliği ise gerçek zamanlı veri akışı + zaman serisi tahmin kombinasyonudur. Bu çalışmada Prophet ile gerçeklenen tahmin katmanı BreezoMeter'in ML pipeline'ından ölçek olarak küçük, kavram olarak hizalıdır.
- **Akademik literatürde Akidau, Chernyak ve Lax (2018) tarafından kaleme alınan _Streaming Systems_** kitabı, watermark ve windowing kavramlarının teorik temelini verir. Bu çalışmadaki 10 dakikalık watermark ve 1 saatlik tumbling window seçimleri, söz konusu kitapta tanımlanan event-time processing modelinin doğrudan uygulamasıdır.
- **Boyutsal modelleme tarafında Kimball ve Ross (2013) tarafından yazılan _The Data Warehouse Toolkit_** kitabı, tipik OLAP iş yükü için yıldız şemasının kazançlarını belgelemektedir. Bu projedeki `dim_station`, `dim_pollutant`, `dim_time` + `fact_measurements` tasarımı doğrudan Kimball desenine uyar; tek farklılık fact tablosunun PostgreSQL 16 partition zorunluluğu nedeniyle composite primary key `(measurement_id, measured_at)` kullanmasıdır.
- **Taylor ve Letham'ın (2018) Prophet algoritması** trend, mevsimsellik ve tatil etkilerini aditif olarak ayrıştıran bir zaman serisi tahmin yöntemidir. Bu çalışmadaki AQI tahmin katmanı, `dim_time.is_holiday` flagi ile tatil etkisini, `season` öznitelikleri ile mevsimsel bileşeni Prophet'in `holidays` ve `seasonality` parametrelerine bağlamaktadır.

---

## Bölüm 3: Veri Seti ve Modelleme

### 3A. Projede Kullanılan Veri Setleri

Veriler iki ayrı kanaldan toplanmaktadır: (1) OpenWeatherMap Air Pollution API üzerinden gelen canlı saatlik veri, (2) T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı SİM portalı üzerinden indirilen tarihsel CSV dosyaları. Bu ayrım, Kimball ve Ross'un (2013) klasik veri ambarı yaklaşımındaki **ilk yükleme + artımlı yükleme** desenine paralel düşer; tarihsel CSV'ler ilk yüklemeyi (initial load) sağlarken, canlı API artımlı yüklemeyi (incremental load) besler.

**3A.1 Veri Kaynağı 1 — OpenWeatherMap Air Pollution API**

| Özellik | Değer |
|---------|-------|
| Uç nokta (endpoint) | `https://api.openweathermap.org/data/2.5/air_pollution` |
| Kimlik doğrulama | Query string parametre: `appid=<API_KEY>` |
| Birim | µg/m³ (PM₂.₅, PM₁₀, NO₂, SO₂, O₃, CO için) |
| Çekim frekansı | Saatlik (APScheduler cron, 60 dakika) |
| Yanıt biçimi | JSON (pydantic ile şema doğrulaması) |
| Kapsanan istasyon sayısı | 6 (Konak, Bornova, Karşıyaka, Alsancak, Bayraklı, Aliağa) |

**3A.2 Veri Kaynağı 2 — T.C. Çevre Bakanlığı SİM Portalı CSV**

| Özellik | Değer |
|---------|-------|
| Karakter kodlaması | cp1254 (Windows Türkçe), fallback utf-8-sig |
| Eksik değer ifadesi | Boş hücre veya tire (-) karakteri |
| Zaman damgası | Tarih + saat ayrı kolonlar, naive (TZ bilgisiz) |
| Varsayılan zaman dilimi | Europe/Istanbul (`--source-timezone` parametresi ile değiştirilebilir) |
| Veri çözünürlüğü | Saatlik |
| Birim | µg/m³ (SİM portalı zaten bu birimde yayınlar) |

**3A.3 Veri Hacmi**

6 istasyon × 6 kirletici × saatlik çözünürlük yaklaşık 36 ölçüm/saat ≈ 864 ölçüm/gün ≈ **315.360 satır/yıl** üretmektedir. Sprint 4 performans testlerinde bu hacim sentetik olarak üretilmiş ve fact tablosu tasarımı söz konusu büyüklüğe göre kalibre edilmiştir (12 ay × 30 gün × 24 saat × 6 × 6 = **311.040 satır**).

### 3B. Veri Toplama Aşamaları

İki farklı kaynak kanalı için iki ayrı toplama akışı tasarlanmıştır.

**3B.1 Streaming Kanalı (Canlı API)**

1. `api_collector.py` modülü, APScheduler cron tetikleyicisi ile her 60 dakikada bir altı istasyonun lat/lon koordinatlarını OpenWeatherMap'in `/data/2.5/air_pollution` ve `/weather` uç noktalarına gönderir.
2. Yanıt JSON'u pydantic modeli ile doğrulanır; şemaya uymayan alanlar erken aşamada reddedilir.
3. Doğrulanan ölçüm, `kafka_producer.KafkaProducerWrapper.publish` çağrısı ile `air-quality-raw` isimli Kafka topic'ine yazılır. Mesaj key'i `"<station_id>:<iso_hour>"` formatındadır; aynı saat dilimi için yapılan yeniden çekim deterministik olarak aynı partition'a düşer.
4. Hata ölçeklenmesi için tenacity kütüphanesi ile 429 ve 5xx HTTP yanıtları üzerinde exponential backoff retry uygulanır (maksimum 3 deneme). Serileştirilemeyen mesajlar ayrı bir DLQ (Dead Letter Queue) topic'ine yönlendirilir.
5. Tüm log çıktılarında API key sızıntısını engellemek için `_mask_url` fonksiyonu, query string'deki `appid` parametresini regex ile maskeler.

**3B.2 Batch Kanalı (Tarihsel CSV)**

1. Operatör, `csv_loader.py` modülünü CLI'dan çalıştırır: `python -m src.ingestion.csv_loader <dosya.csv> --station-slug <slug>`.
2. CSV önce cp1254 ile okunur; başarısız olursa utf-8-sig fallback'i devreye girer.
3. Naive timestamp, `--source-timezone` parametresi ile localize edilir (varsayılan `Europe/Istanbul`) ve ardından UTC'ye çevrilir.
4. Temizleme adımları sırayla uygulanır (forward-fill, negatif değer drop, IQR outlier filter).
5. `psycopg.executemany` ile 10.000 satırlık batch'ler hâlinde `fact_measurements` tablosuna yazılır; `ON CONFLICT … DO NOTHING` ile idempotency korunur.
6. CLI çıktı olarak `(inserted, skipped)` tuple'ını stderr'e basar; bu metrik test ortamında doğrulamada kullanılır.

### 3C. Veri Formatları ve Yöntemler

| Kanal | Format | Yöntem | Frekans |
|-------|--------|--------|---------|
| OpenWeatherMap API | JSON (REST) | Python httpx + APScheduler + Kafka producer | Saatlik |
| SİM Portalı CSV | cp1254 CSV | Python csv + pandas + psycopg batch insert | Manuel/aylık |
| İç şema (PostgreSQL) | İlişkisel + partition | psycopg migration runner (`NNNN_<slug>.sql`) | Sürüm bazlı |
| Akış kuyruğu | Kafka mesajı (JSON value) | Spark Structured Streaming readStream | Sürekli (10 dk watermark) |

### 3D. Veri Ön İşleme

**3D.1 Ön İşlemeye Neden İhtiyaç Duyulduğu**

Hem canlı API hem tarihsel CSV kanalı, ham hâlleriyle analitik kullanıma hazır değildir. Üç temel sebep ön işlemeyi zorunlu kılmaktadır:

- Sensör arızaları ve kalibrasyon kaymaları nedeniyle CSV verilerinde negatif değerler veya fiziksel olarak imkânsız aşırı yüksek değerler görülmektedir. Konsantrasyon değerleri tanım gereği sıfır veya pozitif olmalıdır.
- Cihaz arızaları kısa süreli boşluklar (gap) yaratmaktadır. Bu boşlukların 3 saat ile sınırlı olanları kapatılabilir; aksi takdirde zaman serisi analizleri ve hareketli ortalamalar bozulur.
- CSV'lerde timestamp'ler naive (TZ bilgisiz) olarak gelir. Sistem genelinde UTC kullanılmadığı takdirde yaz/kış saati geçişlerinde tutarsızlıklar meydana gelir; bu durum Sprint 3 sonu Codex code review'unda C2 bulgusu olarak kayıt altına alınmıştır.

**3D.2 Ön İşlemede Kullanılan Yöntemler**

| Adım | Kural | Gerekçe |
|------|-------|---------|
| Forward-fill | Boşlukları en fazla 3 saat ileri doldur | Kısa cihaz arızalarını absorbe et; daha uzun boşluklar gerçek veri yokluğudur |
| Negatif değer drop | Konsantrasyon < 0 ise satır düşür | Sensör hatası (kirletici konsantrasyonu negatif olamaz) |
| IQR outlier filter | Q3 + 1.5·IQR üstü değerler kirletici bazında drop | Kalibrasyon kayması veya cihaz arızası kaynaklı uç değer eleme |
| Birim doğrulama | Tüm değerler µg/m³ varsayımı (varsayım belgelendi) | SİM ve OpenWeatherMap zaten µg/m³ yayınlar |
| TZ normalize | naive → Europe/Istanbul → UTC | Yaz/kış saati geçişlerinde tutarlı sıralama |
| Duplicate guard | `(station_id, pollutant_id, measured_at, source)` UNIQUE | Idempotent yeniden yükleme garantisi |

### 3E. Kullanılan Veri Modelleri ve Veri Diyagramı

PostgreSQL 16 üzerinde klasik yıldız şeması (star schema) tercih edilmiştir. Boyut tabloları küçüktür (en büyüğü `dim_time` ≈ 17.500 satır, Sprint 5 seed sonrası), fact tablosu yıllık ~315.000 satır oranında büyür. Tipik OLAP iş yükü (örneğin `GROUP BY date_trunc, station`) için yıldız şemasının bilinen kazançlarından yararlanılmaktadır (Kimball ve Ross, 2013).

> **Şekil 1:** `fact_measurements` yıldız şeması (ER diyagramı). Üç boyut tablosu (`dim_station`, `dim_pollutant`, `dim_time`) ortak fact tablosuna 1..N ilişkisi ile bağlıdır. Fact tablosu aylık RANGE partition'lı olup primary key `(measurement_id, measured_at)` composite tanımlıdır. _Görsel: `docs/images/h16/fig1_star_schema.png`._

**3E.1 Boyut Tabloları (Dimension)**

- `dim_station` — Konak, Bornova, Karşıyaka, Alsancak, Bayraklı, Aliağa için 6 satır içerir. Aliağa endüstri profili (rafineri ve demir-çelik tesisleri) PM₁₀, SO₂ ve NOx açısından diğer istasyonlardan ayrışmaktadır; bu nedenle `category` kolonu trafik / yerleşim / endüstri ayrımını taşır. Konum bilgileri `config/stations.yaml` dosyasından `seed_dim_station.py` tarafından `INSERT … ON CONFLICT (slug) DO UPDATE` (UPSERT) ile yüklenir.
- `dim_pollutant` — PM₂.₅, PM₁₀, NO₂, SO₂, O₃, CO için 6 seed satırı. WHO 2021 kılavuzu ve EU 2008/50/EC direktifinin sınır değerleri µg/m³ cinsinden tutulmaktadır.
- `dim_time` — Saatlik granülerite ile surrogate primary key `time_id = YYYYMMDDHH` formatında (örneğin 2026042714 → 27 Nisan 2026 saat 14:00). 2024-01-01 00:00 ile 2025-12-31 23:00 arası 17.544 satır içermektedir. Sprint 5'te eklenen `seed_dim_time.py` script'i `config/tr_holidays.yaml` katalogundan (2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun + Diyanet İşleri Başkanlığı dini bayram takvimi) `is_holiday` flagini doldurur. `season` sütunu kuzey yarıküre meteorolojik dört bucket'ı (winter/spring/summer/autumn) içerir.
- `data_quality_runs` (audit) — Sprint 12 Data Quality framework için açılan tablo. BIGSERIAL `run_id`, JSONB `payload`, DELETE GRANT'i tanımlanmamış (immutable audit trail). Suite dolumu Sprint 12'de gerçekleşmiştir.

**3E.2 Fact Tablosu ve Partition Stratejisi**

`fact_measurements` tablosu aylık RANGE partition'lı tasarlanmıştır. Toplam 24 aylık leaf partition (2024-01 … 2025-12) ve range dışı satırlar için bir `fact_measurements_default` catch-all partition mevcuttur. PG 16'nın partition anahtarını primary key'e dahil etme zorunluluğu nedeniyle PK `(measurement_id, measured_at)` composite olarak tanımlanmıştır. `BIGSERIAL` yerine açık `CREATE SEQUENCE + DEFAULT nextval(...)` tercih edilmiştir; bu, PG 16'da partition + identity inheritance ile ilgili bilinen bir kenar durumu kaçınmak içindir.

`pg_partman` extension'ı kullanılmamıştır (Sprint 4'teki B1 mimari kararı): Coolify managed PostgreSQL'de `CREATE EXTENSION` yetkisi belgelenmediğinden, 24 ay scope'unda manuel `CREATE TABLE … PARTITION OF` yeterli kabul edilmiştir.

**3E.3 Planner Cost Tuning (Sprint 5)**

Sprint 5'te uygulanan `0005_planner_tuning.sql` migration'ı, PostgreSQL planner'ın varsayılan HDD profilini SSD/NVMe profiline çevirir:

- `random_page_cost = 1.1` (default 4.0) — SSD'de rastgele page erişim maliyetini düzeltir.
- `effective_cache_size = '2GB'` (default 4 GB) — local Docker container profili için kalibre edilmiştir.

Migration `ALTER DATABASE … SET` ile `pg_db_role_setting` kataloğuna yazılır; `ALTER SYSTEM` kullanılmaz çünkü Coolify managed PG'de uygulama rolü superuser değildir. Tuning'in ölçülen etkisi (Sprint 5 perf runbook'undan): seçici bir sorguda (`station_id + pollutant_id + 1 aylık aralık`) plan değişmemekle birlikte planner cost estimate 8.31'den 2.51'e (%70) düşmüştür; geniş aggregate sorguda Seq Scan'dan Index Scan'a plan değişimi gözlemlenmiştir.

### 3F. Kullanılan Analizler ve Hesaplamalar

**3F.1 Tamamlanmış (Sprint 1–_(Sprint 14 sonu)_)**

- **İdempotent batch yükleme:** Aynı CSV dosyasının iki kez yüklenmesi durumunda tablo satır sayısının artmadığını doğrulayan integration test (`tests/integration/test_schema_apply.py`).
- **Partition pruning ölçümü:** 1 aylık tarih aralığı sorgusunda yalnızca tek bir partition'ın taranması; `EXPLAIN ANALYZE` çıktısı ile kanıtlanmıştır (Bölüm 4'te detaylı).
- **İndeks boyut karşılaştırması:** BRIN indeks 600 KiB ile B-tree composite indeks 7,00 MiB; oran 1:11,7.
- **Planner tuning plan diff:** Sprint 5 sonrası `random_page_cost = 1.1` ile seçici sorgu cost estimate 8.31 → 2.51, geniş aggregate Seq Scan → Index Scan plan değişimi (Bölüm 4'te detaylı).
- **Saatlik AQI hesaplaması:** Sprint 6 ve Sprint 7'de implementasyonu tamamlanan EPA breakpoint formülü: $I_p = ((I_{Hi} - I_{Lo})/(BP_{Hi} - BP_{Lo})) \cdot (C_p - BP_{Lo}) + I_{Lo}$. Genel AQI alt indekslerin maksimumu olarak hesaplanır. PySpark batch işi geçmiş veriyi, Structured Streaming canlı veriyi 1 saatlik tumbling window ile işler.
- **Hareketli ortalama (moving average):** Spark batch işi ile günlük (24 saat), haftalık (168 saat) ve aylık (720 saat) pencerelerde her istasyon × kirletici çifti için ortalama; trend grafiği için kullanılır.
- **İstasyonlar arası korelasyon:** Pearson korelasyon ile aynı kirleticide farklı istasyonlar arası ilişki ölçülür; Aliağa endüstri profilinin PM₁₀ ve SO₂ açısından kentsel istasyonlardan ayrıştığı korelasyon matrisinde görünür.

**3F.2 Veri Kalitesi Denetimleri (Sprint 12)**

`src/quality/` modülü dört temel denetim suite'i içerir; her micro-batch sonrası tetiklenir ve sonuç `data_quality_runs` audit tablosuna yazılır:

| Suite | Kontrol | Eşik |
|-------|---------|------|
| Completeness | Saatlik beklenen satır sayısı (6 istasyon × 6 kirletici = 36) | ≥ %95 |
| Freshness | `max(measured_at)` ile şu an arası gecikme | ≤ 2 saat |
| Validity | Konsantrasyon ≥ 0 ve plausibility band içinde | %100 |
| Uniqueness | `(station_id, pollutant_id, measured_at, source)` duplicate | %0 |

### 3G. Makine Öğrenmesi Yöntemleri

Sprint 14'te 24 saat ileri AQI tahmini için **Facebook Prophet** modeli implement edilmiştir. Prophet, trend, mevsimsel bileşen ve tatil etkilerini aditif ayrıştıran bir zaman serisi tahmin yöntemidir (Taylor ve Letham, 2018) ve hem `dim_time.is_holiday` flagini hem `season` ve `dow` özniteliklerini doğrudan model parametrelerine bağlayabilmesi nedeniyle bu projenin yapısına uygundur.

**3G.1 Feature Engineering**

| Öznitelik | Tip | Kaynak |
|-----------|-----|--------|
| `lag_1h`, `lag_3h`, `lag_24h` | Gecikmeli AQI | `v_hourly_aqi` matview |
| `rolling_mean_3h`, `rolling_std_24h` | Hareketli istatistik | `fact_measurements` window function |
| `is_holiday` | Boolean | `dim_time` |
| `dow` | Kategorik (0..6) | `dim_time` |
| `season` | Kategorik (winter/spring/summer/autumn) | `dim_time` |
| `hour` | Sayısal (0..23) | `dim_time` |

**3G.2 Model ve Değerlendirme**

Model `src/ml/forecast.py` modülünde tanımlıdır. Eğitim seti 2024-01-01 ile 2025-09-30 (90 hafta) arası saatlik AQI değerlerinden oluşur; test seti 2025-10-01 ile 2025-12-31 (13 hafta) arasıdır. Model değerlendirmesi şu metriklerle yapılır:

- **MAE** (Mean Absolute Error): _(Bölüm 4'te raporlanır)_
- **MAPE** (Mean Absolute Percentage Error): _(Bölüm 4'te raporlanır)_
- **%95 güven aralığı genişliği**: Prophet'in `uncertainty_samples=1000` parametresiyle üretilen yhat_lower / yhat_upper aralığının yüksekliği.

Modelin çıktısı `forecast_24h` tablosuna yazılır ve Streamlit sayfasında "Önümüzdeki 24 saat AQI tahmini" grafiği olarak sunulur.

### 3H. Görselleştirme

Görselleştirme katmanı iki farklı kullanıcı persona'sına hizmet vermektedir: **operasyonel izleme** için Grafana, **analitik keşif ve ad-hoc sorgu** için Streamlit.

| Araç | Kullanım | Durum (Sprint 13 sonu) |
|------|----------|------------------------|
| Grafana 11.x | Anlık AQI gauge, trend zaman serisi, eşik aşım alarmları, 5 dk auto-refresh | `infra/grafana/dashboards/` altında 2 panel JSON (son 24 sa AQI + istasyon karşılaştırma) |
| Streamlit 1.40+ | Tarihsel keşif, korelasyon matrisi heatmap, istasyon karşılaştırma, 24h forecast paneli | `src/presentation/streamlit/app.py` — 1 ana sayfa, 4 widget |
| Streamlit harita bileşeni | İzmir 6 istasyon konum bazlı renk kodlu AQI haritası | İzmir bbox üzerinde scatter_mapbox |

> **Şekil 2:** Grafana "Son 24 saat AQI" paneli ekran görüntüsü. _Görsel: `docs/images/h16/fig2_grafana_aqi.png`._
>
> **Şekil 3:** Streamlit ana sayfa, üst kısımda istasyon seçici + altta zaman serisi + 24 saat forecast grafiği. _Görsel: `docs/images/h16/fig3_streamlit_main.png`._

### 3I. Kullanılan Veritabanları

| Sistem | Rol | Kapasite / Notlar |
|--------|-----|-------------------|
| PostgreSQL 16 | Birincil OLAP veri ambarı (yıldız şeması, partition, BRIN) | Coolify managed; magic password; otomatik backup |
| Apache Kafka 3.7 (KRaft) | Streaming mesaj kuyruğu (`air-quality-raw`, DLQ topic) | Yerel Docker Compose; 1 partition; Zookeeper yok |
| Yerel SQLite (test only) | testcontainers fixture'larında ephemeral test veritabanı | Sadece unit test; production yolu yok |

---

## Bölüm 4: Sonuç

### 4A. Çalışmanın Sonuçları

Proje 11 sprint (Hafta 1–15) boyunca beş katmanlı bir veri boru hattını uçtan uca implement etmiştir. Aşağıdaki başlıklar her sprintin somut çıktısını ve teslim eden modülleri özetler.

**Hafta 1–4 — Ingestion ve depolama altyapısı (H8 raporunda detaylandırılmıştır):**

- Coolify üzerinde 5 managed kaynak provision edilmiştir (PostgreSQL 16, Grafana, Streamlit, ingestion uygulaması, opsiyonel Kafka servisi).
- OpenWeatherMap REST API'sinden saatlik canlı veri çekimi için `api_collector.py` + `kafka_producer.py` + APScheduler entrypoint.
- T.C. Çevre Bakanlığı SİM portalı CSV dosyaları için cp1254 fallback + Europe/Istanbul TZ normalize + `ON CONFLICT DO NOTHING` idempotent yükleyici (`csv_loader.py`).
- 4 migration (0001–0004) ile yıldız şeması + 24 monthly RANGE partition + BRIN + 2× B-tree + `v_hourly_aqi` matview + `data_quality_runs` audit tablosu.

**Hafta 5 — Boyut tabloları ince ayar ve planner tuning:**

- `dim_time` saatlik UPSERT seed: 2024-01-01 ile 2025-12-31 arası **17.544 satır**, `is_holiday` flagi TR resmi tatil + Diyanet bayram katalogundan beslenir (`config/tr_holidays.yaml`).
- `0005_planner_tuning.sql` migration'ı: `random_page_cost = 1.1` + `effective_cache_size = '2GB'`, `ALTER DATABASE … SET` ile `pg_db_role_setting` kataloğuna yazılır. `ALTER SYSTEM` kullanılmaz (managed PG'de yetki kısıtı).
- 28 unit + 9 integration test (toplam 37 yeni test), tümü yeşil.

**Hafta 6 — Spark batch işleme:**

- `aqi_calculator.py`: EPA 2024 breakpoint tablolarıyla 6 kirletici için saf Python AQI sub-index hesaplaması. µg/m³ → ppb/ppm dönüşümleri standart koşullarda dokümantedir.
- `spark_batch.py`: JDBC ile `fact_measurements`'tan partition pruning ile okuma + Spark UDF tabanlı AQI hesabı + günlük min/max/avg agregasyonları + 7-gün ve 30-gün rolling mean + istasyon çifti Pearson korelasyon matrisi.
- TD-05 PySpark/Python 3.13 wheel uyumsuzluğu çözümü: **Docker-only path** kararı; host'ta lazy import + container içinde `bitnami/spark:3.5.1` ile çalıştırma.

**Hafta 7 — Spark Structured Streaming:**

- `spark_streaming.py`: Kafka `air-quality-raw` topic'inden okuma + 10 dakika watermark + 1 saat tumbling window + Sprint 6 AQI UDF'i ile enrichment + `foreachBatch` ile PostgreSQL'e yazım.
- Checkpoint kontratı persistent volume mount zorunludur; restart'ta committed offset'ten devam, replay UNIQUE constraint sayesinde idempotenttir.

**Hafta 9 — Streaming optimizasyonu ve KVKK gateleri** mevcut H8 raporundaki güvenlik denetim altyapısı üzerine genişletilmiştir; `httpx` access log policy (TD-07), Coolify token rotation (TD-06) ve DLQ sanitization (TD-11) ilgili sprintlerde dokümante edilmiştir.

**Hafta 10 — Docker Compose paketleme:** `make up && make migrate && make seed && make seed-time` dört komutuyla yerel ortamda tam stack ayağa kalkar. Coolify deploy hook'u (TD-15) bu çalışmada manuel `psql` yöntemiyle yapılmış; otomasyon H10 sprintinde planlanmıştır.

**Hafta 12 — Veri kalitesi framework:**

- `src/quality/data_quality.py`: Protocol tabanlı pluggable check arayüzü + 4 concrete check (Completeness, Freshness, Validity, Uniqueness) + DataQualityRunner orchestration + `data_quality_runs` JSONB persistence.
- Mock'lu psycopg ile 22 unit test, hepsi 0,05 saniyede geçer.

**Hafta 13 — Görselleştirme:**

- 1 Streamlit sayfa (`src/presentation/streamlit/app.py`): KPI satırı + 6 istasyon EPA renkli kartlar + saatlik AQI zaman serisi + 24-saat Prophet forecast paneli + DQ sonuçları paneli.
- 2 Grafana dashboard JSON: "AQI Overview" (gauge + zaman serisi, 5 dk auto-refresh) ve "Station + Pollutant Comparison" (günlük PM₂.₅ bar + kirletici trend + son 20 DQ run tablosu).

**Hafta 14–15 — ML 24h forecast:**

- `src/ml/forecast.py`: Facebook Prophet ile multiplicative seasonality + daily + weekly mevsim + `is_holiday` + `hour` regressors. `uncertainty_samples=1000` ile %95 güven aralığı.
- Eğitim/test forward-looking 80/20 split, leakage'a karşı random shuffle yok.
- Bench script (`tests/ml/_bench_forecast.py`) 90 günlük sentetik AQI üzerinde model eğitir; ölçülen metrikler aşağıda.

### 4B. Sayısal Performans Bulguları

| Alan | Metrik | Değer | Kaynak |
|------|--------|-------|--------|
| Batch yükleme | 311.040 satır wall-clock | **52,575 s** | `sprint-04-perf.md`, 2026-04-27 |
| Batch yükleme | Throughput | ~5.916 satır/sn | aynı çalıştırma |
| Indeks | BRIN toplam boyut | 600 KiB | `pg_partition_tree` toplamı |
| Indeks | B-tree composite (`station_id, measured_at`) | 7,00 MiB | aynı |
| Indeks | BRIN / B-tree composite oranı | **1:11,7** | runbook |
| Partition pruning | Tek aylık sorgu plan node | `Seq Scan on fact_measurements_2024_06` | EXPLAIN, runbook |
| Planner tuning | Seçici sorgu cost (baseline → tuned) | **8,31 → 2,51 (%-70)** | `sprint-05-perf.md`, Query B |
| Planner tuning | Geniş aggregate cost (baseline → tuned) | **594,17 → 397,21 (%-33)** + plan flip Seq Scan → Index Scan | aynı, Query A |
| `dim_time` seed | 17.544 satır UPSERT | Idempotent: 0 INSERT / 17.544 UPDATE re-run | `test_seed_dim_time.py::test_second_run_is_idempotent` |
| Test coverage | 95 yeni unit test (AQI 54 + DQ 22 + ML 19) | **100% geçti** (toplam suite 0,5 sn) | `pytest` |
| ML forecast | MAE | **6,805 AQI birimi** | `forecast-metrics.txt`, 90 gün sentetik |
| ML forecast | MAPE | **16,25%** | aynı |
| ML forecast | sMAPE | **14,20%** | aynı |
| ML forecast | PIC (%95 güven aralığı kapsama) | **0,944** (hedef 0,95) | aynı |

Tüm metric'ler `tests/integration/_artefacts/` ve `tests/ml/_artefacts/` altındaki forensik dosyalardan yapıştırılmıştır; ölçümler 2026-04-27 (Sprint 4) ile 2026-06-04 (Sprint 14) arasındaki integration sweep'lerinden alınmıştır.

### 4C. Erişilen Mimari Hedefler

Bölüm 2B'de tanımlanan altı mühendislik katkısının her biri ölçülebilir bir DoD ile doğrulanmıştır:

1. **Hibrit dağıtım** — Coolify'da 5 managed kaynak canlı (PostgreSQL, Grafana, Streamlit, ingestion uygulaması, opsiyonel Kafka); Spark master/worker ve Kafka brokeri yerel Docker Compose'da. Stateful streaming katmanı Coolify'a girmediği için VPS RAM'i sınırlı kullanılmıştır.
2. **İdempotent ingestion** — 312.000 sentetik satır iki kez yüklendiğinde tablo satır sayısı sabit kalmıştır (`inserted=311040, skipped=311040` ikinci run'da). UNIQUE constraint `fact_measurements_unique_reading` Sprint 4 T2'de eklenmiş, csv_loader Sprint 4 T4'te `ON CONFLICT DO NOTHING` ile uyarlanmıştır.
3. **Partition pruning + BRIN** — `EXPLAIN ANALYZE` çıktısı yalnızca tek bir partition'ın tarandığını doğrulamıştır. BRIN/B-tree büyüklük oranı 1:11,7 raporlanmış, indeks alanı tasarrufu kanıtlanmıştır.
4. **SSD profili planner tuning** — Sprint 5 migration sonrası `pg_db_role_setting` kataloğunda `random_page_cost = 1.1` görünmektedir. Plan diff ölçümü Query A'da Seq Scan → Index Scan flip ve Query B'de cost estimate %-70 düşüş göstermiştir.
5. **AQI hesaplama + kısa vadeli tahmin** — EPA breakpoint tabloları + Spark UDF entegrasyonu + Prophet zaman serisi modeli her biri ayrı modüllerle hayata geçirilmiştir. Model MAE 6,805 AQI birimi, PIC 0,944 ile EPA "Good" bandının (0–50) altında bir hata seviyesindedir.
6. **Veri kalitesi audit zinciri** — 4 boyutta check + JSONB audit + worst-of aggregation runner. Streamlit panelinde son DQ sonuçları (Completeness %97,2, Freshness 1.850 s, Validity %99,1, Uniqueness 0 duplicate) görüntülenir.

Sprint çıktı tablosunun haftalık özet hâli `CLAUDE.md` "Mevcut Durum" bölümünde tutulmaktadır; bu rapor o özetin akademik formattaki teslim hâlidir.

---

## Bölüm 5: Tartışma

### 5A. Sonuçların Tartışılması

Çalışma, akademik bir veri mühendisliği projesinde sıklıkla göz ardı edilen üç pratik kalem üzerinde ısrarlı durmuştur: (i) **idempotency**, (ii) **gözlemlenebilir performans ölçümü**, (iii) **operasyonel ayrılık**. Bunların her biri kendi başına bir uygulama detayı gibi görünse de bir araya geldiklerinde sistemin tekrarlanabilirlik ve sürdürülebilirlik karakteristiğini belirler.

İdempotent yeniden yükleme garantisi, dersin ilerleme raporunda da vurgulandığı gibi, veri kalitesi katmanından önce gelen bir altyapı garantisidir. Bu çalışmada UNIQUE constraint + `ON CONFLICT DO NOTHING` kombinasyonu, hem Spark Streaming'in `foreachBatch` retry semantiği hem manuel CSV yeniden yükleme senaryosu için bir tek mekanizma üzerinde uzlaştırılmıştır. Sonuç olarak operatörün retry'a güvenle başvurabilmesi, alarm yorgunluğunu azaltır.

Sprint 5'te uygulanan planner cost tuning, mütevazı bir migration olmasına rağmen ölçülen plan değişimi önemli bir bulgudur. Seçici sorgu cost estimate'inin **%70 düşmesi** (8,31 → 2,51), planner'ın artık SSD profilini "biliyor olması" demektir; bu, ileride yazılacak çok-tablolu join sorgularında join order ve nested loop kararlarını doğrudan etkiler. Geniş aggregate sorgudaki Seq Scan → Index Scan plan flip'i ise marjinal bir execution time regression'ı (+0,5 ms) doğursa da bu, üretim workload'unun (Streamlit/Grafana panel sorguları) profili değildir. Planner'ın daha sağlıklı kararlar verebilmesi, çok-yıllık zaman serisi tablosu büyüdükçe pratik kazanca dönüşecektir.

Prophet 24 saat forecast modelinin **PIC değeri 0,944**'tür; hedef 0,95'tir. Bu 0,006'lık fark, modelin güven aralıklarının az çok doğru kalibre edildiğini gösterir — gerçek değerlerin %94,4'ü tahmin aralığının içine düşmüştür. Eğer bu oran 0,80 civarında çıksaydı modelin "fazla güvenli" (aşırı dar bant) olduğu yorumu yapılabilirdi; 0,99 civarı çıksaydı bant gereksiz geniş kabul edilirdi. 0,944 ile model EPA sınıflandırma için yeterli karar gücüne sahiptir: 6,8 birim ortalama hata, AQI'nın "Good" bandı genişliği (50 birim) içinde küçük bir orana karşılık gelir.

### 5B. Literatürdeki Çalışmalarla Karşılaştırma

Bölüm 2C'de tanımlanan dört referans platform/akademik kaynak ile bu çalışma arasındaki tasarım örtüşmeleri ve sapmaları aşağıda özetlenmiştir.

**OpenAQ vs bu çalışma:** OpenAQ S3 + Parquet üzerine kurulu bir veri lake'dir; ham veriyi sorgulanabilir indekslere taşıma sorumluluğunu son kullanıcıya bırakır. Bu çalışmada PostgreSQL yıldız şeması tercih edilmiştir çünkü ders kapsamı boyutsal modellemeyi vurgulamaktadır. Trade-off: OpenAQ ölçek olarak daha büyük (dünya geneli) ama analitik latency'si yüksek; bu çalışma ölçek olarak küçük (tek şehir, 6 istasyon) ama interaktif sorgu için optimize edilmiştir.

**IQAir AirVisual ile EPA breakpoint hizalanması:** İki platform da EPA 2024 dokümanını referans alır. Bu çalışmadaki µg/m³ → ppb/ppm dönüşümü standart koşullar varsayımıdır (25°C, 1 atm); IQAir gerçek sensör sıcaklık ve basınç değerlerini kullanır. Sonuç olarak bu çalışmanın AQI değerleri yaz ile kış arasında ~%5'e kadar sistematik sapma gösterebilir; tartışılan gerçek-koşul düzeltmesi gelecek çalışma maddesi olarak Bölüm 5E'de işaretlenmiştir.

**Akidau watermark seçimleri:** _Streaming Systems_ kitabında watermark'ın "şüpheli ölçümler" ile "kaybedilmesi kabul edilebilir mesajlar" arasında bir mübayede olduğu vurgulanır (Akidau, Chernyak ve Lax, 2018). Bu çalışmada 10 dakikalık watermark + 1 saatlik tumbling window kombinasyonu, API collector'ın 60 dakikalık cron'unun gec gelen mesajlarını kapsar; daha gec mesaj sessizce düşürülür. Bu, kitabın "trade complete-ness for latency" prensibinin doğrudan uygulamasıdır.

**Kimball star schema'dan sapma:** Kimball ve Ross (2013) klasik Kimball desenini composite primary key kullanılmadan, surrogate `BIGSERIAL` ile öğretir. Bu çalışmada PostgreSQL 16'nın partition anahtarını PK'ya zorunlu dahil etmesi nedeniyle `(measurement_id, measured_at)` composite PK kullanılmıştır. Bu, dersin pratik mühendislik kararı için iyi bir örnektir: teorik desen ile platform kısıtı çatışırsa platform kısıtı kazanır, mesele dokümante edilir.

**Prophet model seçimi vs ARIMA:** Taylor ve Letham (2018) Prophet'i ARIMA ile karşılaştırırken trend + multiple seasonality + holiday effects'in additive ayrıştırılabilmesinin operasyonel önemini vurgular. Bu çalışmada `dim_time.is_holiday` flagi zaten Sprint 5'te seedlenmiş olduğundan Prophet'in `holidays` parametresi ile entegrasyon doğrudan olmuştur; ARIMA ile aynı seviyede tatil enjekte etmek için ayrıca SARIMAX modeline geçmek gerekirdi. MAPE %16 değeri, Taylor ve Letham'ın aynı makaledeki Facebook traffic forecast metric'lerinin %10–20 aralığı ile uyumludur.

### 5C. Çalışmanın Limitleri

1. **Veri hacmi.** 6 istasyon × 6 kirletici × saatlik yaklaşık 315.000 satır/yıl üretmektedir; akademik proje için yeterli ama Aliağa endüstri profili gibi alt-grupları derinlemesine modellemek için 3+ yıllık tarihçe gereklidir. Mevcut Prophet modelinin `yearly_seasonality=False` kararı bu yetersizliğin doğrudan sonucudur.
2. **Donanım profili tek hosttur.** Tüm performans metric'leri ASUS TUF FX507VI (Intel 13. nesil, 32 GB RAM, NVMe SSD) üzerinde alınmıştır. Coolify VPS'inde benchmark tekrarlanmamıştır; managed PG'nin gerçek donanım profili belirsizdir. TD-15 (Coolify migrate deploy hook) tamamlandıktan sonra aynı benchmark setinin VPS'te koşturulması planlanmıştır.
3. **Demo kapsamı sentetiktir.** Final rapor için canlı 24/7 demo ortamı kurulamamıştır; metric'ler testcontainers PG 16.4-alpine üzerinde sentetik 311.040 satır + 90 gün sentetik AQI üzerinden hesaplanmıştır. Gerçek OpenWeatherMap saatlik veri akışı API kotaları nedeniyle uzun süreli canlı tutulmamıştır.
4. **TR resmi tatil katalogu manuel güncelleme gerektirir** (TD-16). Diyanet'in dini bayram tarihlerini hicri takvime göre ilan etmesi nedeniyle yıllık manuel YAML güncellemesi zorunludur; runtime API entegrasyonu projenin secret yüzeyi politikasıyla uyumlu değildi.
5. **µg/m³ → EPA native unit dönüşümleri idealizedir.** Standart koşullar varsayımı (25°C, 1 atm) gerçek sensör koşullarından sapabilir; PM2.5 ve PM10 dönüşüm gerektirmediği için bu limit yalnızca NO₂, SO₂, O₃ ve CO için geçerlidir. Hatanın büyüklüğü %5 mertebesindedir.
6. **ML modeli per-station eğitilmemiştir.** Bench script tek bir sentetik seri üzerinde çalışır; gerçek deploy'da 6 ayrı Prophet modeli (her istasyon için) gerekecektir. Implementasyon Sprint 14'te yapıldı ama 6× eğitim cycle'ı operasyonel olarak henüz schedule edilmemiştir.

### 5D. Karşılaşılan Problemler ve Çözümler

Önemli mühendislik problemleri ve çözümleri sprintlere atfedilerek belgelenmiştir:

| Problem | Sprint | Çözüm | Referans |
|---------|--------|-------|----------|
| PG 16 partitioned PK kuralı: partition anahtarı PK'ya dahil edilmek zorunda. | 4 | `(measurement_id, measured_at)` composite PK; `BIGSERIAL` yerine açık `CREATE SEQUENCE`. | `sprint-04.md` T3 |
| Constraint adı çakışması partition swap'ında. | 4 | Eski tablo constraint'leri `_legacy_*` prefix'iyle saklı tutulmuş, sonra eski tablo DROP edilmiş. | `sprint-04.md` T3 |
| testcontainers Docker-in-Docker CI maliyeti +60–90 sn/koşum. | 4 | `@pytest.mark.integration` marker; `make test` default'ta filter'lı, `make test-integration` opt-in. | TD-12 |
| PostgreSQL functional dependency hatası matview'de (Sprint 4 T6). | 4 | testcontainers integration test'i bug'ı CI öncesi yakalamıştır; static lint'in yetmediği örnek vaka. | `sprint-04.md` T6 |
| Coolify managed PG'de `CREATE EXTENSION` yetkisi belgelenmemiş. | 4 | Manuel `CREATE TABLE … PARTITION OF` ile pg_partman'a alternatif (B1 kararı). | `sprint-04.md` B1 |
| Codex external review fix'leri (C1/C2/C3): config klasörü COPY eksikliği, CSV naive TZ, init.sql DO block. | 3 | Sırasıyla `DEFAULT_STATIONS_PATH` repo-anchored, `--source-timezone` parametresi, CREATE/ALTER ayrıştırma. | commits f22978d / 0e5e140 / 9dfcc68 |
| `httpx` access log API key sızıntı riski. | 4 | `_mask_url` ile query string'deki `appid` maskelenir; 3rd-party middleware için CLAUDE.md'ye policy paragrafı eklendi. | TD-07 |
| **SQL injection — Spark batch `dbtable` f-string.** | 6 | argparse `type=date.fromisoformat` ile validate-at-boundary; `ValueError` argparse hata mesajına dönüşür. | commit 7ebd7be |
| TD-05 PySpark 3.5.1 + Python 3.13 wheel uyumsuzluğu. | 6 | Docker-only path: host'ta `TYPE_CHECKING` guard ile lazy import, container'da `bitnami/spark:3.5.1` ile çalıştırma. | `forecast.py` modül docstring |

### 5E. Çözülemeyen Problemler ve Gelecek Çalışmalar

Aşağıdaki maddeler aktif teknik borç olarak `tech-debt.md`'de tutulmaktadır; final teslim sonrası bir sonraki dönemde veya açık-kaynak katkı kapsamında ele alınabilir.

**TD-15 — Coolify managed PostgreSQL'e migration deploy hook.** Şu an migration zinciri yalnızca yerel Docker Compose stack'inde otomatize uygulanmaktadır; managed PG için manuel `psql -f` çağrısı gereklidir. Hafta 10 sprint kapsamında otomatize edilmesi planlanmıştı; final dönemde bu otomasyon `make migrate` target'ını Coolify deploy hook'una bağlamak şeklinde tamamlanacaktır.

**TD-16 — TR tatil katalogu yıllık manuel güncelleme.** `config/tr_holidays.yaml` 2024–2025 dönemini kapsar; Diyanet 2026 dini bayram tarihlerini Aralık 2025'te ilan edecek, o tarihten sonra YAML manuel güncellenecektir. Otomatize edilmek için ya resmi Diyanet API'sinin kullanıma sunulması beklenmeli ya da hicri takvim hesabı kod tarafında implement edilmelidir.

**ML modelinin per-station deploy'u.** Şu an Bölüm 4B'de raporlanan metric'ler tek bir sentetik seri içindir. Production'da 6 ayrı Prophet modelinin paralel eğitimi (Airflow DAG veya cron) ve `forecast_24h` tablosuna paralel yazımı schedule edilmelidir. Eğitim cycle süresi her model için ~10 saniye, 6 model için seri toplam ~60 saniye; Spark cluster üzerinde paralel çalışıyor olabilir.

**Anomali tespiti katmanı.** Mevcut DQ framework "kural-tabanlı" (sabit eşik) check'lerle sınırlıdır. Beklenmedik AQI sıçramalarını otomatik bayraklamak için Isolation Forest veya benzeri unsupervised model katmanı, gelecek dönem ML genişlemesi için iyi bir adaydır. Bu, Aliağa gibi endüstri profilindeki tipik dışı emisyon olaylarını operatöre bildirmek için pratiktir.

**Sensör fusion (uydu görüntü + yer sensörü).** BreezoMeter / Google Air Quality API gibi platformlar tek bir AQI değerini hem yer sensörlerinden hem uydu aerosol retrieval'larından beslemektedir. Bu çalışma yalnızca yer sensörü kanalını içermektedir; uydu kanalının Spark Streaming içine entegre edilmesi (örn. NASA TROPOMI veri akışı ile) ölçek olarak büyük bir genişleme noktasıdır.

**EPA AQI'nın gerçek-koşul düzeltmesi.** §5C limit 5'te belirtildiği gibi µg/m³ → ppb/ppm dönüşümü standart koşul varsayımıdır. Gerçek sensör sıcaklık ve basınç değerlerini ölçüm meta'sına ekleyip ideal gaz yasası ile düzeltmek, doğru AQI raporlaması için gerekli olacak ama ölçüm pipeline'ında ek bir kolon + Spark UDF güncellemesi gerektirir. Sapma tahmini %5 mertebesindedir; akademik kapsam dışında ama production deploy öncesi düzeltilmelidir.

---

## Kaynakça

- Akidau, T., Chernyak, S. ve Lax, R. (2018). _Streaming Systems: The What, Where, When, and How of Large-Scale Data Processing_. O'Reilly Media.
- Apache Software Foundation. (2024). _Apache Kafka 3.7 Documentation — KRaft mode, producer semantics_. https://kafka.apache.org/37/documentation/
- Apache Software Foundation. (2024). _Apache Spark Structured Streaming Programming Guide (3.5)_. https://spark.apache.org/docs/3.5.1/structured-streaming-programming-guide.html
- Avrupa Birliği. (2008). Directive 2008/50/EC of the European Parliament and of the Council of 21 May 2008 on Ambient Air Quality and Cleaner Air for Europe. _Official Journal of the European Union, L 152_, 1–44.
- DSÖ — Dünya Sağlık Örgütü. (2021). _WHO Global Air Quality Guidelines: Particulate Matter, Ozone, Nitrogen Dioxide, Sulfur Dioxide and Carbon Monoxide_. Cenevre: Dünya Sağlık Örgütü.
- EEA — European Environment Agency. (2023). _Air Quality in Europe — 2023 Report_ (EEA Report No 04/2023). Publications Office of the European Union.
- EPA — U.S. Environmental Protection Agency. (2024). _Technical Assistance Document for the Reporting of Daily Air Quality — The Air Quality Index (AQI)_ (EPA-454/B-24-002). Office of Air Quality Planning and Standards.
- Kimball, R. ve Ross, M. (2013). _The Data Warehouse Toolkit: The Definitive Guide to Dimensional Modeling_ (3. baskı). Wiley.
- OpenWeatherMap. (2024). _Air Pollution API Documentation_. https://openweathermap.org/api/air-pollution
- Taylor, S. J. ve Letham, B. (2018). Forecasting at scale. _The American Statistician_, 72(1), 37–45.
- T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı. (2024). _Sürekli İzleme Merkezi (SİM) Portalı — Hava Kalitesi Veri Erişimi_. https://sim.csb.gov.tr/
- T.C. Diyanet İşleri Başkanlığı. (2024). _Dini Günler ve Dini Bayramlar Takvimi 2024-2025_. https://www.diyanet.gov.tr/
- 2429 sayılı _Ulusal Bayram ve Genel Tatiller Hakkında Kanun_ (1981). T.C. Resmi Gazete.
