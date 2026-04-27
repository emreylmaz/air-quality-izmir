# Sprint 4 — Performance Smoke Runbook

**Owner:** data-quality-engineer
**Test:** `tests/integration/test_load_performance.py::TestLoadPerformance`
**Marker:** `@pytest.mark.integration` + `@pytest.mark.slow`
**DoD ref:** `docs/sprints/sprint-04.md` T8

Bu doküman, 312K satırlık sentetik yük altında 0003 partition + index
tasarımının ölçülen davranışını dondurur. Sayılar gerçek bir test
çalıştırmasından alınır — re-tune sırasında numaralar değişirse,
runbook bir sonraki yeşil suite çıktısından yeniden pin edilir
(`tests/integration/_artefacts/perf-last-run.txt`).

## Donanım baseline

| Bileşen | Değer |
|---------|-------|
| Host | ASUS TUF Gaming F15 FX507VI |
| CPU | Intel64 Family 6 (13th gen, 2.4 GHz nominal), 16 logical core |
| RAM | 32 GB |
| Storage | NVMe SSD (üretici default) |
| OS | Windows 11 Pro (build 26200) |
| Docker | Docker Desktop 27.3.1 (linux WSL2 backend) |
| Postgres image | `postgres:16.4-alpine` (testcontainers, ryuk disabled) |
| Python | 3.13.7, psycopg 3.2 (binary) |

> CI runner profili farklıdır; CI'da bu test default'ta `make test`
> içinde **çalışmaz** — `make test-integration` opt-in. Aşağıdaki
> rakamlar yalnız bu donanımda anlamlı.

## Sentetik veri seti

* 12 ay × 30 gün × 24 saat × 6 istasyon × 6 kirletici = **311,040 satır**
* `START = 2024-01-01 00:00 UTC`; `month_offset × 30 + day_offset` →
  yıla göre absolut gün indeksi (Şubat overflow trap'i yok).
* `random.Random(seed=20260427)` — deterministic; aynı host'ta tekrarlı
  run'lar bit-for-bit aynı veri.
* Kirletici aralıkları (µg/m³): pm25 5–100, pm10 10–150, no2 5–80,
  so2 1–30, o3 10–200, co 200–4000.
* Her saat için sinüs tabanlı diürnal swing + ±2 µg/m³ jitter.
* Yazım yolu: `csv_loader.insert_rows` (`executemany`, batch 10K),
  `source='synthetic'`. Pandas cleaning bypass — yük profili pure
  DB throughput olsun diye (rationale test docstring'inde).

## 312K satır yükleme süresi

| Metrik | Değer |
|--------|-------|
| Inserted rows | **311,040** |
| Wall-clock | **52.575 s** |
| Throughput | **~5,916 satır/sn** |
| DoD bütçesi | 60.000 s |
| Marj | 7.4 s (~%12 buffer) |

`pytest --durations=10` çıktısında setup phase 58.28 s — bu sayı
container boot + migration apply + seed + bulk load toplamıdır;
çıplak yükleme (yukarıdaki wall-clock) 52.6 s.

## BRIN vs B-tree size karşılaştırma

`pg_partition_tree(<index>::regclass)` ile her index'in tüm
partition leaf'leri toplanarak ölçüldü.

| Index | Tip | Boyut | Notlar |
|-------|-----|-------|--------|
| `fact_measurements_measured_at_brin` | BRIN | **600 KiB** (614,400 B) | 24 monthly + default partition leaf'leri toplam |
| `fact_measurements_station_time_idx` | B-tree `(station_id, measured_at DESC)` | **7.00 MiB** (7,168,000 B) | Composite — "son 24 saat şu istasyon" |
| `fact_measurements_pollutant_idx` | B-tree `(pollutant_id)` | **2.49 MiB** (2,613,248 B) | Single-column |

**BRIN/B-tree composite oranı:** 600 KiB / 7,168 KiB ≈ **1:11.7**.
Test'teki kanıt eşiği `B-tree ≥ 5 × BRIN` (gerçek oran 11×, marj
yeterli). 0003 boş tabloda yapılan `<=` sanity check'i bu yük
altında "%11x daha küçük"e dönüşüyor — append-only timestamp
verisinde BRIN'in textbook win'i.

## EXPLAIN ANALYZE — partition pruning kanıtı

Sorgu: `SELECT count(*), avg(value) FROM fact_measurements WHERE
measured_at >= '2024-06-01' AND measured_at < '2024-07-01'`.

```
Aggregate  (cost=786.38..786.39 rows=1 width=16) (actual time=2.696..2.697 rows=1 loops=1)
  Buffers: shared hit=268
  ->  Seq Scan on fact_measurements_2024_06 fact_measurements  (cost=0.00..656.80 rows=25916 width=8) (actual time=0.009..1.627 rows=25920 loops=1)
        Filter: ((measured_at >= '2024-06-01 00:00:00+00'::timestamp with time zone) AND (measured_at < '2024-07-01 00:00:00+00'::timestamp with time zone))
        Buffers: shared hit=268
Planning:
  Buffers: shared hit=400
Planning Time: 1.673 ms
Execution Time: 2.721 ms
```

Gözlemler:

* **Tek partition scan'lendi** — `fact_measurements_2024_06`. Diğer 23
  monthly partition + default plana hiç girmedi (test'te dört
  canary partition için negatif assertion var).
* **Seq Scan** — 25,920 satır için planner sequential scan'i index
  scan'e tercih etti. 30 gün × 24 saat × 6 istasyon × 6 kirletici =
  25,920 satır = ~268 sayfa, B-tree seek + heap fetch'tan ucuz.
  Bu doğru bir karar — partition pruning'in marjinal kazancı zaten
  diğer 23 partition'ı planlamamak.
* **Buffers: shared hit=268** — partition tamamen cache'te (test
  load'ı taze; cold cache'te `read=268` görürdük).
* **Execution: 2.7 ms** — 25K satır agregasyonu için kabul edilebilir.
* **Planning: 1.7 ms** — partitioned tabloda planning cost'u
  partition sayısıyla doğrusal artar; 25 partition için sub-2 ms iyi.

## Sonuç ve next steps

* **0003 partition tasarımı geçerli.** 312K satırda bile her index
  on-disk boyutu küçük (toplam ~10 MiB), pruning testbook gibi
  çalışıyor, append-only ingestion 60 sn bütçesinin altında.
* **BRIN değer üretiyor.** B-tree composite'ten 11× küçük, range scan
  partition-pruning sonrası BRIN'in olmaması durumunda sequential
  scan zaten yeterli — BRIN minor overhead karşılığında "tüm tablo
  range scan'i" senaryosunda değer.
* **Hafta 6 Spark batch ingestion için aksiyon:** Spark streaming
  job'ı `fact_measurements`'a yazarken **partition-aware insert
  pattern**'i kullanmalı:
  - JDBC writer'a `partitionColumn = measured_at`,
    `numPartitions = 12` (aylık) ile veriyi pre-partition'la
  - Ya da `df.coalesce(12).foreachPartition` → her Spark task
    ayrı ay'a INSERT yapsın
  - Default partition'a düşen satırlar için Grafana alarmı (H10
    rolling partition cron task'ı ile birlikte)
* **TD candidate (H10):** `pg_partman` değerlendirmesi.
  Manuel `CREATE TABLE … PARTITION OF` 24 ay için kabul, ama H14
  ML feature engineering 2026+ ay aralığını eklemeye başlayınca
  bu manuel liste yorucu olur.
* **TD candidate (H10):** `effective_cache_size` + `random_page_cost`
  tuning — testcontainers default'ları conservative, prod Coolify
  managed PG'de sayılar farklı çıkacak. Bu runbook prod-readiness
  baseline değil; "0003'ün tasarım vaadi tutuyor mu" kanıtı.

## Re-run komutu

```bash
make test-integration                       # tüm integration suite
pytest tests/integration/test_load_performance.py -v -m integration
```

Yeni rakamlar `tests/integration/_artefacts/perf-last-run.txt`
dosyasına yazılır (gitignored). Runbook'u re-pin etmek için bu
dosyayı oku ve yukarıdaki tabloları güncelle.
