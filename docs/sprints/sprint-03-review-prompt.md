# Sprint 03 — External Review Prompt (Codex / Gemini)

> **Kullanım:** Bu dosyayı kopyalayıp Codex CLI veya Gemini'ye olduğu
> gibi yapıştırın. Prompt self-contained — repo'ya erişimi olmayan
> bir reviewer için bile yeterince context taşır.
>
> **Amaç:** YZM536 hava kalitesi izleme projesinin Sprint 3 (ingestion
> layer) deliverable'larını bağımsız iki LLM reviewer ile çapraz
> doğrulamak. Çıktıları human reviewer (Claude) konsolide edecek.

---

## REVIEW PROMPT — KOPYALA & YAPIŞTIR

```
You are a senior staff engineer reviewing a Python data engineering
sprint deliverable. Your review will be cross-checked against a parallel
review from another LLM, so be thorough, specific, and cite line numbers.

## PROJECT CONTEXT

This is sprint 3 of "YZM536 Air Quality Monitoring", a 16-week academic
data engineering project for Izmir, Turkey. The pipeline:

  OpenWeatherMap API → Python ingestion → Kafka → Spark Structured
  Streaming → PostgreSQL → Grafana + Streamlit

Deployment is hybrid: stateless services (ingestion, streamlit) ship to
Coolify (managed VPS); stateful Spark stays local. Sprint 3 covers ONLY
the ingestion layer:

  - Async OpenWeatherMap collector (httpx + tenacity retry)
  - Kafka producer wrapper (confluent-kafka, idempotent, DLQ routing)
  - APScheduler entrypoint (graceful shutdown, signal handling)
  - Historical CSV loader (Çevre Bakanlığı open data → fact_measurements)
  - Station catalog (6 Izmir sites: Konak, Bornova, Karşıyaka, Alsancak,
    Bayraklı, Aliağa) with pydantic validation
  - Minimal schema stub (3 tables, 6-pollutant seed)
  - DQ baseline contract tests
  - Security audit

Tech stack: Python 3.11+ (3.13 in dev), pydantic v2, httpx 0.28,
confluent-kafka 2.5, APScheduler 3.11, pandas 3.0, psycopg 3.3,
PostgreSQL 16, Bitnami Kafka 3.7 (KRaft), pytest 8.

## FILES TO REVIEW

Total: 11 files (~1,800 LOC). Read each in full before judging.

### Production code (src/)
1. src/config/settings.py            — pydantic-settings, SecretStr
2. src/ingestion/stations.py         — YAML catalog loader, pydantic
3. src/ingestion/api_collector.py    — async HTTP client, retry, masking
4. src/ingestion/kafka_producer.py   — Producer wrapper, DLQ, idempotence
5. src/ingestion/main.py             — APScheduler lifecycle, signals
6. src/ingestion/csv_loader.py       — pandas pipeline, executemany
7. src/storage/schema.sql            — minimal DDL stub + seed
8. config/stations.yaml              — 6-station catalog

### Tests (tests/)
9. tests/ingestion/test_api_collector.py   (~20 tests, respx mocks)
10. tests/ingestion/test_kafka_producer.py (~13 tests + 1 skipped int.)
11. tests/ingestion/test_main.py           (~9 tests, MagicMock)
12. tests/ingestion/test_csv_loader.py     (~29 tests, fixture-based)
13. tests/ingestion/test_contracts.py      (~16 cross-cutting invariants)
14. tests/ingestion/test_stations.py       (~10 tests)
15. tests/ingestion/fixtures/izmir_sample_utf8.csv  (101 hourly rows)

### Operational
16. infra/Dockerfile.ingestion       — multi-stage build
17. infra/docker-compose.local.yml   — full local stack
18. .env.local.example               — placeholder template
19. Makefile                         — install / up / down / lint / test
20. pyproject.toml                   — deps + ruff + mypy strict + pytest
21. docs/sprints/sprint-03.md        — sprint plan + DoD
22. docs/sprints/sprint-03-demo.md   — smoke-test runbook
23. docs/sprints/sprint-03-security-audit.md  — T11 audit report

## QUALITY GATES (REPORTED PASSING)

The author reports these gates pass on their machine:

  - pytest: 103 passed, 1 deselected (integration), 12s
  - Coverage: 86.41% overall; ingestion modules ≥ 96%
      api_collector 96.23%, csv_loader 98.05%,
      kafka_producer 96.58%, main 100%, stations 89.47%
  - ruff check src/ tests/ infra/coolify/  → all checks pass
  - mypy src/ --strict --ignore-missing-imports  → 0 errors (20 files)
  - detect-secrets scan → 0 findings outside .secrets.baseline

Do not rerun these — focus on what the gates miss.

## REVIEW DIMENSIONS (rate each 1-5, justify)

For each dimension, give a score and 2-5 specific findings with
file:line citations. If a dimension has no findings, say so explicitly
("No findings.") rather than padding.

### A. Correctness & Edge Cases
   - Are the cleaning rules in csv_loader correct? Tukey IQR with
     <4-sample passthrough, ffill ≤3h, mg/m³→µg/m³ heuristic for CO.
   - Does the retry logic in api_collector cover all transient errors?
     Are 4xx (non-429) really non-retryable as documented?
   - Does the producer wrapper correctly handle: BufferError,
     malformed payloads → DLQ, idempotent producer config, fork safety
     warning?
   - Does main.py shutdown correctly under SIGINT and SIGTERM on both
     POSIX and Windows? (Author claims tested on Win 11.)
   - Are timezone handling decisions correct? Schema is TIMESTAMPTZ;
     CSV loader localizes naive timestamps to UTC via dayfirst=True.

### B. Security
   - Is the API key truly never logged? `_mask_url` regex coverage?
   - SecretStr usage and repr masking — verified everywhere?
   - DLQ envelope — could `repr(raw_value)[:500]` leak unexpected data
     types (e.g., bytes payload with embedded creds)?
   - .env.local exclusion + .gitignore policy — gaps?
   - detect-secrets allowlist — over-allowed? Any pragma misuse?

### C. Test Quality
   - Are the cleaning rule tests asserting the RIGHT invariants, or
     just that the function returns *something*?
   - Mock fidelity: does mock_producer in test_kafka_producer mirror
     real librdkafka semantics (delivery callbacks, error returns)?
   - Coverage gaps despite high % — what untested paths matter most?
   - Integration test is skipped by default — is the skip mechanism
     correct (env var gate)? Is there an obvious way to make it run
     in CI without a real broker?
   - test_csv_loader uses MagicMock for psycopg.Connection. Does this
     hide real cursor lifecycle bugs? Suggest a docker-postgres
     integration upgrade path if any.

### D. API Design & Maintainability
   - Are public functions named consistently? (clean, drop_negative,
     forward_fill, iqr_filter, standardise_units — verbs vs nouns.)
   - Are pure cleaning steps composable, or do they assume the result
     of a prior step? Document any hidden coupling.
   - Should `clean()` accept a config object instead of using module-
     level constants for `IQR_MULTIPLIER`, `DEFAULT_FFILL_LIMIT_HOURS`?
   - Argparse CLI in csv_loader — sufficient for ops use, or does it
     need --dry-run, --batch-size, --resume-from-row?

### E. Performance
   - csv_loader uses pandas + executemany. For 1-year hourly data
     (~52K rows × 6 pollutants = 312K rows), is this fast enough?
     Should it be COPY FROM instead?
   - api_collector uses asyncio.gather for parallel fan-out. Any
     risk of overwhelming the OpenWeather rate limit (60 req/min on
     free tier) with 6 stations × 2 endpoints?
   - Kafka producer uses linger.ms=50, gzip compression. For 6 hourly
     messages this is fine but is anything sized wrong?

### F. Documentation
   - Are docstrings complete and accurate, or do they drift from the
     implementation?
   - Is the smoke-test runbook (sprint-03-demo.md) reproducible by a
     new engineer with no prior context?
   - Are commit messages following Conventional Commits 1.0?

### G. Risks & Tech Debt
   - Schema stub with no indexes/partitions — will this cause obvious
     pain by sprint 4 (database-architect)?
   - csv_loader does not deduplicate against existing rows. Re-running
     the same CSV will create duplicates. Is this intentional or a
     missing UNIQUE constraint?
   - station_id in csv_loader is an int FK; how does the operator
     resolve "Konak" → station_id=1 in production?
   - Multiple tests use real datetime objects; could non-determinism
     bite us if the test runs across midnight UTC?

## DELIVERABLE FORMAT

Output a single Markdown report with this structure:

# Sprint 03 Review — <YourName>

## Verdict
- Overall: APPROVE / APPROVE WITH CHANGES / REQUEST CHANGES
- Risk level: LOW / MEDIUM / HIGH
- One-paragraph summary (≤120 words)

## Scores
| Dimension | Score (1-5) | One-line rationale |
|-----------|-------------|--------------------|
| Correctness | x | ... |
| Security | x | ... |
| Tests | x | ... |
| API Design | x | ... |
| Performance | x | ... |
| Documentation | x | ... |
| Risks/Tech Debt | x | ... |

## Critical Findings (must-fix before merge)
For each: file:line, severity (CRITICAL/HIGH), problem, suggested fix.

## Important Findings (should-fix this sprint)
Same format, severity MEDIUM.

## Nice-to-Have (post-merge improvements)
Brief list, severity LOW.

## What I Would Test Differently
Specific test ideas the author missed.

## Comparison Hooks (for cross-review consolidation)
List 3-5 specific claims you made that the other reviewer might
disagree with. This helps the human consolidator triangulate.

## END OF PROMPT — paste this whole block to the reviewer LLM.
```

---

## How to Run Reviews

### Codex (CLI)
```bash
# Codex tipik 4-5K token context'e sığar; bu prompt + dosyalar 30-40K token
# olabilir → repo'yu local clone'la, sonra parça parça besle.
codex --model o1-preview review \
  --prompt-file docs/sprints/sprint-03-review-prompt.md \
  --files src/ingestion/ tests/ingestion/ src/storage/schema.sql \
          docs/sprints/sprint-03.md \
  > docs/sprints/sprint-03-codex-review.md
```

Eğer Codex CLI'da `--files` yoksa: prompt'u stdin'den ver, ardından
her dosyayı `cat <file>` yapıp tek tek append et.

### Gemini (web veya CLI)
Web UI: prompt'u tek mesaj olarak yapıştır, sonra ardışık mesajlarda
her dosyayı kod bloğu içinde paylaş ("File: src/ingestion/csv_loader.py"
header ile). 1M token Gemini context'i bu sprint'in tamamını kaldırır.

CLI:
```bash
gemini -m gemini-2.5-pro \
  --prompt-file docs/sprints/sprint-03-review-prompt.md \
  --attach src/ingestion/ \
  --attach tests/ingestion/ \
  > docs/sprints/sprint-03-gemini-review.md
```

### Konsolide (Claude)
Her iki review tamamlanınca:

```
@claude consolidate sprint-03-codex-review.md ve sprint-03-gemini-review.md
- Hem fold üzerinde anlaştıkları (yüksek güven) hem de ayrıştıkları
  (manuel inceleme gerek) finding'leri ayır.
- "Comparison Hooks" bölümlerindeki çapraz iddiaları kontrol et.
- Final action list'i çıkar (CRITICAL → HIGH → MEDIUM sırasıyla).
```

---

## Notes for Reviewer LLMs (içsel — prompta dahil edilmedi)

- Bu prompt sentetik olarak değil, gerçek deliverable üzerinde test edildi.
- Beklenen risk seviyesi: **LOW-MEDIUM**. CRITICAL bulgu çıkarsa tekrar
  bakılmalı; sprint testlerinin %86 coverage + 103 yeşil olması ile
  uyumsuz.
- Reviewer'ın takılma ihtimali olan noktalar:
  - `clean()` orchestration sırası — `standardise_units` → `drop_negative`
    → `iqr_filter` → `forward_fill`. CO mg→µg dönüşümü drop_negative'den
    önce, çünkü mg space'inde IQR farklı çıkar.
  - `_iqr_filter` <4 sample passthrough — design choice, sprint plan
    DoD'unda yer alıyor.
  - Producer `publish()` BufferError'da rethrow ediyor (caller drop/retry
    karar verir). main.py içindeki try/except bunu yakalayıp continue
    yapıyor — sprint plan'a uygun "per-station isolation" tasarımı.
- Reviewer "neden integration test'ler skip edilmiş" derse: Hafta 9'da
  testcontainers ile gerçek broker eklenecek (TD listesinde, tracked).
