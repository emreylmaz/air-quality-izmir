# Coolify IaC — Workflow

Bu dizin Coolify v4 kaynaklarını **kod olarak** yönetir. UI'dan manuel
değişiklik yapmak yasaktır — `provision.py` ile senkron kaybolur.

## Dosya Yapısı

| Dosya | Amaç |
|-------|------|
| `client.py` | httpx tabanlı REST API wrapper; idempotent `ensure_*` metotlar |
| `provision.py` | Desired-state reconciler (`plan` / `apply` / `status`) |
| `sync_secrets.py` | Custom 3rd-party secret push/pull |
| `config.yaml` | Desired state — **secret içermez** |

## İlk Kurulum (kullanıcı)

```bash
# 1. Coolify UI'dan API token oluştur:
#    Keys & Tokens → API tokens → Create → "can_read_sensitive" ability ile
mkdir -p ~/.config/air-quality
cat > ~/.config/air-quality/coolify.env <<'EOF'
COOLIFY_BASE_URL="https://coolify.yourdomain.com"
COOLIFY_API_TOKEN="1|replace-with-real-token"
EOF
chmod 600 ~/.config/air-quality/coolify.env

# 2. Custom 3rd-party secret dosyası:
cat > ~/.config/air-quality/secrets.env <<'EOF'
OPENWEATHER_API_KEY=real-key-here
EOF
chmod 600 ~/.config/air-quality/secrets.env

# 3. Python deps:
pip install -e ".[coolify]"
```

## Çalıştırma

```bash
# Dry-run: ne yapılacak göster
make coolify-plan

# Gerçek provisioning (interaktif onay)
make coolify-apply

# Mevcut durum
make coolify-status

# Custom secret push (OpenWeatherMap API key vs)
python -m infra.coolify.sync_secrets push --app aqi-streamlit
python -m infra.coolify.sync_secrets push --app aqi-ingestion

# Env inventory (value'lar maskelenmiş)
python -m infra.coolify.sync_secrets list
```

## İdempotency

Her `ensure_*` çağrısı önce `list` yapar, isim bazlı eşleştirir:
- **Var:** log "exists", mevcut kaydı döner.
- **Yok:** yeni oluşturur.

`apply` komutu plan çıktısındaki eylemleri sırayla uygular. Aynı `apply`'ı
tekrar çalıştırmak güvenlidir — ikinci run "no changes" çıkarır.

## Secret Güvenliği

- `CoolifyClient.__repr__` token'ı `***` maskeler.
- `upsert_envs_bulk` log'da value yerine `***` basar.
- `config.yaml` **asla** gerçek password içermez — Magic Variables referansı
  (`${SERVICE_PASSWORD_X}`) veya `__SECRET_FROM_SYNC__` placeholder kullanılır.
- `sync_secrets.py` Magic prefix'li key'leri (`SERVICE_*`) reddeder — Coolify
  üretir, biz yazmayız.

## Anti-pattern

- ❌ Coolify UI'dan env variable ekleme → `provision.py` / `sync_secrets.py` bypass olur
- ❌ `config.yaml`'a gerçek password yazma → Magic Variables kullan
- ❌ `COOLIFY_API_TOKEN`'ı log'a basma → `client.py` header'ı loglamıyor, dışarıdan da eklemeyin
- ❌ `--yes` flag'ini default yapma → production'da onay adımı zorunlu

## Troubleshooting

**401 Unauthorized:** Token süresi doldu veya `can_read_sensitive` yok →
UI'dan yeni token, `~/.config/air-quality/coolify.env` güncelle.

**404 service_type:** `config.yaml`'daki `type` değerini Coolify UI'dan teyit
(Add Resource → Service → Search). Sürüme göre değişebilir.

**Plan drift sürekli çıkıyor:** Coolify UI'dan manuel değişiklik yapılmış.
UI değişikliğini geri al veya `config.yaml`'a işle.
