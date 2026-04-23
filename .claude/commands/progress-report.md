---
description: H8 ara rapor ya da H16 final rapor draft'ı üret — rapor şablonu baz alınır.
argument-hint: [h8|h16|custom]
---

`technical-writer` subagent'a şu task'ı ver:

"İlerleme raporu hazırlanıyor. Argüman: $ARGUMENTS

### Mod: h8 (İlerleme Raporu %40)
Şablon: `docs/RAPOR_SABLONU.md`
Çıktı: `docs/RAPOR_H8.md`

#### Hazırlık
1. `git log --oneline --since='<proje başlangıcı>'` — proje history
2. `src/` altındaki modüllerin durumu:
   - ingestion: ✅/🟡/❌
   - processing: ...
   - storage: ...
   - presentation: ...
3. Kafka topic mesaj sayısı: `make kafka-status` veya docker exec
4. PostgreSQL row count: `psql -c 'SELECT count(*) FROM fact_measurements'`
5. Ekran görüntüsü listesi:
   - `docs/images/h8/`: architecture.png, kafka-topic.png, grafana-live.png

#### İçerik Üretimi
- Şablonun her bölümü için gerçek veri ile doldur:
  - Placeholder'ları at ([...] gibi)
  - Ekran görüntülerini ekle (mermaid diagramları inline)
  - 'Zorluklar' tablosunu `docs/ASSUMPTIONS.md` + tech-debt'ten besle
- Türkçe prose, İngilizce kod, APA kaynakça

#### Kalite Kontrol
- Tüm [placeholder] temizlendi mi? (`grep '\[' docs/RAPOR_H8.md`)
- Ekran görüntülerinde secret, email, API key var mı? (manuel review)
- 8-12 sayfa hedef (akademik yoğunluk)

### Mod: h16 (Final Rapor %60)
Şablon: `docs/RAPOR_SABLONU.md` + ek bölümler
Çıktı: `docs/RAPOR_H16.md`

#### Ek Bölümler (H16'ya özgü)
- Literatür incelemesi (OpenAQ, IQAir, BreezoMeter karşılaştırma)
- Performans benchmark:
  - Kafka throughput (msg/sec)
  - Spark streaming latency (p50/p95/p99)
  - PostgreSQL query time (top 10)
- ML sonuçları (`ml-engineer`'dan metric fetch):
  - MAE/RMSE/MAPE
  - Naive baseline karşılaştırma
  - Coverage %
- KVKK compliance özeti (`docs/GUVENLIK.md`'den)
- Gelecek çalışmalar:
  - Multi-city scale-out
  - Mobile app
  - Advanced ML (LSTM, Transformer)

#### Kalite Kontrol (ek)
- Tüm sprint review'lardan lessons-learned sindirilmiş mi?
- Kaynakça minimum 10 referans (APA)
- Mimari diyagramı son hali (Hafta 16 sonrası)

### Mod: custom
Özel bölüm talebi için $ARGUMENTS geri kalanını oku.

### Çıktı
- `docs/RAPOR_H<N>.md` dosyası
- Eksik veri varsa sorularla geri dön (örn. 'Kafka topic mesaj sayısını nasıl
  ölçtün? make komutu var mı?')

**Asla** ChatGPT/generic prose üretme — proje gerçek state'inden besle."
