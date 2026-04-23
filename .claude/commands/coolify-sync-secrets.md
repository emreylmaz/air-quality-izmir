---
description: Custom secret'ları (OpenWeatherMap API key vs) Coolify'a push et.
argument-hint: [push|pull|list] [--app <name>]
---

`coolify-engineer` subagent'a şu task'ı ver:

"Custom secret senkronizasyonu. Argüman: $ARGUMENTS

### push
- Kaynak dosya: `~/.config/air-quality/secrets.env` (gitignored, direnv ile
  yüklenmez — sadece manuel sync için)
- Dosyayı oku, key=value parse et
- Her key için ilgili Coolify app'e `PATCH /applications/{uuid}/envs/bulk`
- Build-time vs runtime ayrımı: OPENWEATHER_API_KEY → runtime only
- Response'u logla ama VALUE'ları mask et (`OPENWEATHER_API_KEY=***`)

### pull
- `--app <name>` zorunlu
- App'in env'lerini fetch et (`can_read_sensitive` gerekli)
- Ekrana key listesi + VALUE = *** (sadece varlık kontrolü)
- İsteğe bağlı `--reveal` flag'i — hassas value'yu göster (double-confirm)

### list
- Tüm app'lerdeki env key'leri listele (value yok)
- Magic Variables vs custom ayrımı
- Hangisi tanımlı/hangisi eksik

Magic Variables (`SERVICE_PASSWORD_*`) asla push ile set edilmez — Coolify
üretir. Push edilecek sadece gerçek 3rd-party secret'lar.

Çıktıda: push/pull özeti, uyarılar (eksik env var, orphan env, Magic Variable
çakışması)."
