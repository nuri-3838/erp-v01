# Üretim modülü sadeleştirme — ilerleme (C planı)

Gece oturumu 2026-10-11. Her adım: kod → ilgili testler → tam paket → commit → standart deploy (CLAUDE.md) → canlıda yalnız görüntüleme doğrulaması.
Oturum kesilirse buradan devam edilir. Canlıda hiçbir kayıt oluşturulmaz/onaylanmaz/silinmez (tek istisna: adım 1 veri migration'ı).

| # | Adım | Durum | Commit | Tam paket | Canlı doğrulama |
|---|------|-------|--------|-----------|-----------------|
| 1 | İş İstasyonları: `fason` / `fasoncu` / `sira`; sabit "10" kodu kaldırıldı; liste sütunları; şema 0204 + veri 0205 | kod+test tamam, deploy bekliyor | | | |
| 2 | Tarih bazlı stok kontrolü (`eldeki_miktar(..., tarih=)`) + maliyet düzeltme komutu (yalnız kuru çalıştırma) | bekliyor | | | |
| 3 | ÜS detay: istasyon bazlı katlanır gruplar; "Bu istasyonu üret" / "Tümünü üret" (atomik) | bekliyor | | | |
| 4 | Operasyon Kayıtları: varsayılan Onaylı; bağımsız yeni kaldırıldı; geri al yetkisi + iz | bekliyor | | | |
| 5 | İhtiyaç Hesapla → Ürün Ağacı 4. görünüm; eski URL yönlendirme; düğümde ✎ | bekliyor | | | |
| 6 | Operasyon Tanımları formu: Gelişmiş ▸ bölümü | bekliyor | | | |
| 7 | İstasyon Emirleri: istasyon sekmeleri, filtre, toplu üret | bekliyor | | | |
| 8 | ÜS listesi: varsayılan Açık; tamamlanmış stok ÜS'de "Tamamlandı" | bekliyor | | | |

## Adım notları

- **Adım 1:** `IsIstasyonu.fason` (bool), `fasoncu` (Cari FK, PROTECT, yalnız fason istasyonunda), `sira` (ordering `sira, kod`). `uretim.istasyon_olustur/guncelle`
  yeni alanları alır (verilmeyen korunur; açık emri olan fason istasyonunun bayrağı kaldırılamaz), `istasyon_listesi()` operasyon sayısı + açık emir sayısı
  (sabit sorgu). `fason.FASON_ISTASYON_KODU` kaldırıldı: kesim listesi, `istasyon_emri_uret`, ÜS detay / İstasyon Emirleri düğmeleri `istasyon.fason`a bakar.
  Fason dönüş formu `?istasyon_emri=` ile açılınca istasyonun varsayılan fasoncusu seçili gelir. Migration 0204 saf şema, 0205 saf veri (kod 10 → fason +
  fasoncu `320-30-0040`, kod sırasıyla sıra 10/20/30…). Testler: `test_is_istasyonu_fason.py`; eski testlerde kod "10" ile kurulan istasyonlara `fason=True`.
