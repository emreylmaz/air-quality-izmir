# Tech Debt Log

Bu dosya sprint sürecinde biriken — ama o sprint'te çözülmesi planlanmamış — teknik borçları tutar. Her kayıt: ID, başlık, kaynak sprint, hedef sprint, kısa açıklama.

| ID | Başlık | Açıldı | Hedef | Sahip | Açıklama |
|----|--------|--------|-------|-------|----------|
| TD-01 | Grafana sub-app FQDN port injection | H1-2 | H13 | analytics-engineer + coolify-engineer | `provision.py:apply_actions → ensure_public_app` `fqdn` parametresini geçirmiyor; config.yaml'daki URL'ler etkisiz, Coolify UUID-based default FQDN üretiyor. Sub-app FQDN'inde `:3000` port'u Caddy/Traefik routing'ini bozuyor (default Caddy landing dönüyor). Çözüm: `ensure_*` imzalarına `fqdn` ekle + payload'a dahil et. |
| TD-02 | `main-backup` local branch temizliği | H1-2 | H8 (rapor sonrası) | tech-lead | Claude trailer'lı eski history güvenlik ağı olarak duruyor. H8 ara raporu teslim edildikten sonra silinecek (`git branch -D main-backup`). |
| TD-03 | `SparkSession` pytest fixture skip durumu | H1-2 | H6 | spark-engineer | `tests/conftest.py` içindeki Spark fixture şu an `pytest.skip` ile bırakılmış. H6'da spark-engineer streaming işine başlayınca gerçek SparkSession fixture'ı tamamlayacak. |
| TD-04 | `commitizen` / `conventional-pre-commit` hook | H1-2 | H10 (DevOps güçlendirme) | devops-engineer | Şu an Conventional Commits formatı elle uygulanıyor. Pre-commit hook ekleyince yanlış formatta commit reddedilecek. CLAUDE.md TODO'sunda kayıtlı. |
| TD-05 | PySpark 3.5.1 + Python 3.13 wheel uyumsuzluğu | H3 | H6 (spark-engineer kickoff) | devops-engineer + spark-engineer | Local `.venv` Python 3.13.7. PyPI'da `pyspark-3.5.1` için cp313 wheel yok; sadece sdist (317 MB) var → `pip install -e ".[processing]"` build aşamasında dakikalarca asılıyor. H3'te `[dev,ingestion]` kuruldu, `processing` skip. Çözümler: (a) venv'i Python 3.11/3.12'ye düşür, (b) PySpark 3.5.4+ veya 4.0'a yükselt (pyproject.toml `pyspark==3.5.1` pin'i gevşet), (c) yalnız Docker `bitnami/spark:3.5.1` üzerinden çalış (host'ta pyspark hiç kurulmasın — IDE type-hint için stub paket). H6 kickoff'ta spark-engineer karar versin. |
