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

_(Bu bölüm Sprint 6-14 deliverable'ları tamamlandıktan sonra ölçülen metric'lerle doldurulacaktır. Şu anda şablon olarak duruyor — yazım sırası: Sprint kod çıktıları yazıldıktan sonra.)_

### 4A. Çalışmanın Sonuçları

_(TODO — Sprint 6-14 deliverable'ları + final metric'ler)_

### 4B. Sayısal Performans Bulguları

_(TODO — pytest coverage, EXPLAIN bench, Spark micro-batch latency, ML MAE/MAPE)_

### 4C. Erişilen Mimari Hedefler

_(TODO — 6 mühendislik katkısının her birinin doğrulanması)_

---

## Bölüm 5: Tartışma

_(Bu bölüm Bölüm 4 ile birlikte yazılacak.)_

### 5A. Sonuçların Tartışılması

_(TODO)_

### 5B. Literatürdeki Çalışmalarla Karşılaştırma

_(TODO — OpenAQ / IQAir / BreezoMeter karşılaştırması, Akidau watermark seçimleri, Kimball star schema sapmaları)_

### 5C. Çalışmanın Limitleri

_(TODO — veri hacmi, hardware profili, demo kapsamı)_

### 5D. Karşılaşılan Problemler ve Çözümler

_(TODO — H8'den taşınanlar + Sprint 5-14'te eklenenler)_

### 5E. Çözülemeyen Problemler ve Gelecek Çalışmalar

_(TODO — TD envanteri + literatür bazlı genişletme önerileri)_

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
