---
description: Sprint/PR kapatmadan önce tüm kalite kontrolleri tek komutta çalıştır ve raporla.
---

`data-quality-engineer` subagent'a şu task'ı ver:

"Quality gate denetimi. Aşağıdaki kontrolleri sırayla çalıştır, toplu rapor üret:

### 1. Kod Kalitesi
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/ --strict --ignore-missing-imports
```
- Fail → raporla, **blocker**

### 2. Test Suite
```bash
pytest tests/ --cov=src --cov-report=term-missing -m 'not slow' -v
```
- Coverage < 80 → warn
- Coverage < 60 → blocker
- Skipped test > 10 → warn (neden?)

### 3. Integration Test (opt-in)
Eğer `--integration` arg verildiyse:
```bash
make up  # docker compose up
pytest tests/ -m integration -v
make down
```

### 4. Secret Scan
```bash
detect-secrets scan --baseline .secrets.baseline
```
- Yeni unapproved finding → **blocker**
- Baseline güncellenmesi gerekiyorsa: `detect-secrets scan --update .secrets.baseline`

### 5. Pre-commit Hook Doğrulama
```bash
pre-commit run --all-files
```
- Fail → blocker

### 6. Docker Build Sanity
```bash
docker build -f infra/Dockerfile.streamlit -t aqi-streamlit:test .
docker build -f infra/Dockerfile.ingestion -t aqi-ingestion:test .
```
- Build error → blocker

### 7. Coolify Drift Check (opsiyonel)
```bash
make coolify-status  # config drift uyarı var mı?
```
- Drift varsa raporla, kritik değilse bilgi amaçlı

### 8. DQ Framework Runtime Check (varsa data)
```bash
python -m src.quality.data_quality --window=24h
```
- Completeness < 0.80 → CRITICAL
- Freshness > 120min → CRITICAL
- Validity violations > 5% → WARN

### Çıktı
```markdown
# Quality Gate Report

| Check | Status | Detay |
|-------|--------|-------|
| ruff | ✅ | 0 issue |
| mypy | ✅ | strict pass |
| pytest | 🟡 | 85 test, %78 coverage (target 80) |
| secrets | ✅ | baseline temiz |
| docker build | ✅ | 2 image |
| DQ | 🟡 | completeness 0.92, freshness 45min |

## Blocker
[varsa]

## Warn
- Coverage %78 (target 80) — tests/processing ekle

## Sonraki Adım
[sprint kapat / PR merge / fix required]
```

Secret scan VEYA lint VEYA test fail ise → sprint/PR merge **engelle**,
`tech-lead`'e eskalasyon."
