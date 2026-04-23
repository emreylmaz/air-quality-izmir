---
description: Tüm Coolify kaynaklarının health durumu özet.
---

`coolify-engineer` subagent'a şu task'ı ver:

"`infra/coolify/config.yaml`'daki tüm kaynaklar için:

1. Coolify API'den status çek (`GET /databases`, `/applications`, `/services`)
2. Her resource için:
   - Status (running, stopped, starting, error)
   - Son deploy zamanı
   - Health check durumu (varsa)
   - Resource URL (FQDN)
   - Son 5 deploy history özeti
3. Uyarılar:
   - Stopped resource'lar
   - Healthcheck fail'leri
   - Config drift (desired vs actual mismatch)
   - Orphan resource (Coolify'da var, config.yaml'da yok)

Tablo formatında özet + uyarılar listesi. Acil aksiyon gerekiyorsa
öneri ver (`/coolify-provision apply` vb.)."
