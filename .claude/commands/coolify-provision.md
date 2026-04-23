---
description: Coolify kaynaklarını IaC-vari provision et. plan ile başla.
argument-hint: [plan|apply|status|destroy]
---

`coolify-engineer` subagent'a şu task'ı ver:

"`infra/coolify/config.yaml`'ı oku. Coolify API'ye connect ol. Desired state
ile actual state'i karşılaştır.

Argüman: $ARGUMENTS

### plan (default)
- Her resource için mevcut durum kontrolü
- Oluşturulacak / update edilecek / silinecek listesi
- Kullanılacak Magic Variables
- Secret injection planı (ama değerleri gösterme)
- Tahmini süre

### apply
- plan çıktısını kullanıcıya göster
- Onay al (interaktif — 'Devam edilsin mi? [y/N]')
- Sırayla resource'ları oluştur/güncelle
- Her adımda progress log + resulting UUID
- Hata → rollback stratejisi (manual yönlendirme)

### status
- Tüm kaynakların sağlık durumu tablosu
- Config drift uyarıları

### destroy
- Sadece development environment için
- Double-confirm ('Type project name to confirm destruction')
- Soft delete (Coolify async cleanup tetikler)

Çıktıda: başarı/hata tablosu, oluşturulan/güncellenen UUID'ler, sonraki
önerilen komut.

Secret asla ekrana basma. API response'larda password varsa `***` ile mask et."
