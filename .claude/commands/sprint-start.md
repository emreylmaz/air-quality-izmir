---
description: Verilen hafta için sprint planı çıkar, task breakdown yap, agent ata.
argument-hint: [hafta numarası 1-16]
---

`tech-lead` subagent'a şu task'ı ver:

"Hafta $ARGUMENTS için sprint başlangıcı. Şu adımları sırayla yap:

### 1. Plan okuma
- `docs/PROJE_PLANI.md` içinden Hafta $ARGUMENTS bölümünü oku
- Hedef, yapılacaklar listesi ve teslim çıktısını özetle

### 2. Bağımlılık kontrolü
- Hafta N-1 çıktıları tamam mı? Değilse blocker olarak işaretle
- Önceki hafta teslim etmediğin şeyler varsa `tech-debt.md` (yoksa oluştur) ekle

### 3. Acceptance Criteria (her task için net)
Her task için:
- Definition of Done (DoD): hangi dosyada ne değişecek, hangi test geçecek
- Test beklentileri (unit + integration)
- Demo senaryosu (nasıl gösterilecek?)

### 4. Agent Ataması
Hafta temasına göre delege et (CLAUDE.md'deki Agent Yönlendirme Matrisi):
- Hafta 3 → data-engineer + devops-engineer (Kafka compose)
- Hafta 4-5 → database-architect
- Hafta 6-7 → spark-engineer
- Hafta 10 → devops-engineer + coolify-engineer
- Hafta 11 → security-compliance
- Hafta 12 → data-quality-engineer
- Hafta 13 → analytics-engineer
- Hafta 14-15 → ml-engineer
- Hafta 16 → technical-writer (review: tech-lead)

### 5. Çıktı Formatı
```markdown
# Hafta $ARGUMENTS Sprint Plan

## Hedef
[tek cümle]

## Tasks
| # | Task | Agent | DoD | Est |
|---|------|-------|-----|-----|
| 1 | ... | data-engineer | tests/... passes | 4h |
| 2 | ... | ... | ... | ... |

## Blocker'lar
- [varsa]

## Demo Senaryosu
[nasıl gösterilecek]

## Sonraki Adım
İlk task'ı kim alıyor? '/agent-handoff <agent>' ile başlat.
```

Secret repo'ya yazma riski içeren task varsa `security-compliance`'a ön-review sor."
