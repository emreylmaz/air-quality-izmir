---
description: Mevcut sprint çıktılarını gözden geçir, tech debt kaydet, sonraki sprint riskleri çıkar.
---

`tech-lead` subagent'a şu task'ı ver:

"Mevcut sprint'i kapatıyoruz. Şu adımları uygula:

### 1. Teslimat Kontrolü
- `git log --oneline <sprint_start_ref>..HEAD` (kullanıcıdan aralık öğren veya son 7 gün)
- Yeni/değişen dosyaları tespit et: `git diff --name-only`
- Beklenen teslimatlar (sprint-start çıktısı) ile karşılaştır
- Tamamlanmamışları listele

### 2. Quality Gate
- `make test` → fail varsa sprint kapatılmaz
- `make lint` → warnings raporla
- Coverage rapor: `pytest --cov=src --cov-report=term`
- `detect-secrets scan --baseline .secrets.baseline` → temiz mi?

### 3. Tech Debt Audit
Yeni eklenen:
- `TODO`, `FIXME`, `HACK` yorumları (grep ile)
- Doğrulanmamış Coolify endpoint / service template ID'leri (`docs/ASSUMPTIONS.md`)
- Skip edilen test'ler (`pytest -v | grep SKIP`)
- mypy `type: ignore` artışı

### 4. Sprint Retrospektif
3 başlık:
- **Neyi doğru yaptık?** (devam ettireceğimiz)
- **Neyi kaybettik?** (scope creep, zaman tahmini sapması, blocker)
- **Sonraki sprint için aksiyonlar**

### 5. Sonraki Hafta Risk Taraması
- `PROJE_PLANI.md`'den sonraki hafta içeriğini oku
- Yeni bağımlılık (örn. Hafta 10 → Coolify production VPS hazır olmalı)
- External blocker (API key, domain, vs) — user'a erken haber ver

### 6. Rapor Hazırlığı (H8 ve H16 öncesi)
Eğer sonraki hafta = 8 veya = 16 → `technical-writer` agent'a ilerleme raporu
draft brief'i hazırla.

### 7. Çıktı
```markdown
# Sprint Review: Hafta [N]

## Teslimatlar
- ✅ ...
- 🟡 ... (kısmen — [sebep])
- ❌ ... (kaymış → tech-debt/backlog)

## Quality Gate
| Check | Status |
|-------|--------|
| tests | ✅ 85% coverage |
| lint  | ✅ |
| secrets | ✅ baseline temiz |

## Tech Debt (yeni)
- ...

## Sonraki Sprint Riskleri
- ...

## Aksiyonlar
- [ ] ...
```

Teslim edilmemiş kritik iş varsa `pending/` klasörüne not düş ve kullanıcıyı bilgilendir."
