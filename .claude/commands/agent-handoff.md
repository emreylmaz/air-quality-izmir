---
description: Bir agent'ın çıktısını diğerine context ile aktar. Multi-agent koordinasyonun merkezi.
argument-hint: <source-agent> → <target-agent> [task özet]
---

`tech-lead` subagent'a şu task'ı ver:

"Handoff koordinasyonu. Argüman: $ARGUMENTS

### Handoff Çerçevesi
Format: `<source> → <target> [task]`

Örnekler:
- `data-engineer → spark-engineer [Kafka schema hazır, streaming consumer yaz]`
- `database-architect → coolify-engineer [schema.sql finale, migration uygula]`
- `spark-engineer → data-quality-engineer [streaming job aktif, DQ check bind]`

### Source Agent Çıktısını Özetle
- Hangi dosyalar değişti? (`git diff --stat`)
- Hangi test'ler eklendi?
- Acceptance criteria karşılandı mı?
- **Kritik:** Ne yapılmadı / bekleyen karar var mı?

### Target Agent'a Context Paketi
```markdown
## Başlangıç Context'i
**Önceki ajan:** <source>
**Çıktı dosyaları:** [list]
**Test durumu:** [pass/fail/partial]
**Bekleyen kararlar:** [varsa]

## Senin Görevin
[task özeti]

## Acceptance Criteria
- [ ] ...
- [ ] ...
- [ ] Testler `tests/<alan>/` altında
- [ ] `make lint && make test` geçiyor
- [ ] Secret repo'ya girmemiş

## Referans
- CLAUDE.md: [ilgili bölüm]
- docs/MIMARI.md: [ilgili katman]
```

### Bilgi Boşluğu Önleme
- Source agent'ın yaptığı varsayımlar (`docs/ASSUMPTIONS.md`'ye yazıldı mı?)
- Magic Variable mapping'leri (`config.yaml`'daki env referansları)
- Local mi Coolify'a mı gidecek (hybrid strateji)

### Döngü Koruma
- Aynı task 2 kez bir agent'tan diğerine geldiyse → `tech-lead` araya girer,
  acceptance criteria'yı netleştirir
- Target agent reddederse (out-of-scope, blocker) → `tech-lead` re-delegate

### Çıktı
Target agent'ı invoke etmeden önce yukarıdaki context paketi hazır ve özet
formatında kullanıcıya göster. Kullanıcı onay verirse agent'a pasla."
