# YZM536 — Proje İlerleme Raporu

**Proje Adı:** Gerçek Zamanlı Hava Kalitesi İzleme — Uçtan Uca Veri Boru Hattı

**Öğrenci:** [Ad Soyad — Öğrenci No]

**Tarih:** [Hafta 8 tarihi]

---

## 1. Proje Tanımı ve Motivasyon

[Projenin ne olduğunu, neden bu konuyu seçtiğinizi ve hangi problemi çözdüğünü açıklayın.
İzmir özelinde hava kalitesi izlemenin neden önemli olduğunu tartışın.]

## 2. Sistem Mimarisi

[Mimari diyagramı ekleyin. Her katmanı (toplama, işleme, depolama, sunum) açıklayın.
Teknoloji seçim gerekçelerinizi sunun.]

### 2.1 Teknoloji Yığını
- Veri Toplama: ...
- Mesaj Kuyruğu: ...
- Veri İşleme: ...
- Depolama: ...
- Görselleştirme: ...

## 3. Veri Kaynakları

[Hangi API'leri kullandığınızı, veri formatını, güncelleme sıklığını belirtin.
Örnek JSON çıktısı ekleyin.]

### 3.1 API Detayları
### 3.2 Veri Şeması
### 3.3 Veri Hacmi Tahmini

## 4. Veritabanı Tasarımı

[Star schema diyagramını ekleyin. Her boyut ve fact tablosunu açıklayın.
İndeks ve partition stratejinizi tartışın.]

## 5. ETL Pipeline

[Veri akışını adım adım açıklayın:
1. API'den çekim
2. Kafka'ya yazma
3. Spark ile okuma ve dönüşüm
4. PostgreSQL'e yazma]

### 5.1 Veri Temizleme Kuralları
### 5.2 AQI Hesaplama Mantığı
### 5.3 Streaming Yapılandırması

## 6. Mevcut Durum ve Demo

[Şu ana kadar neyin çalıştığını gösterin. Ekran görüntüleri ekleyin.
Kafka topic'lerindeki mesaj sayısı, PostgreSQL'deki kayıt sayısı gibi metrikler.]

## 7. Karşılaşılan Zorluklar

[Teknik zorlukları ve nasıl çözdüğünüzü açıklayın.]

| Zorluk | Çözüm |
|--------|-------|
| ... | ... |

## 8. Kalan Haftalardaki Plan

| Hafta | Aktivite |
|-------|----------|
| 9 | Streaming optimizasyonu |
| 10 | Docker Compose paketleme |
| 11 | Güvenlik ve KVKK |
| 12 | Veri kalitesi |
| 13 | Görselleştirme |
| 14–15 | ML ve tamamlama |

## 9. Kaynakça

[Kullandığınız kaynakları listeleyin.]
