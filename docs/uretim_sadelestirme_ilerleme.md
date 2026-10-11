# Üretim modülü sadeleştirme — ilerleme (C planı)

Gece oturumu 2026-10-11. Her adım: kod → ilgili testler → tam paket → commit → standart deploy (CLAUDE.md) → canlıda yalnız görüntüleme doğrulaması.
Oturum kesilirse buradan devam edilir. Canlıda hiçbir kayıt oluşturulmaz/onaylanmaz/silinmez (tek istisna: adım 1 veri migration'ı).

| # | Adım | Durum | Commit | Tam paket | Canlı doğrulama |
|---|------|-------|--------|-----------|-----------------|
| 1 | İş İstasyonları: `fason` / `fasoncu` / `sira`; sabit "10" kodu kaldırıldı; liste sütunları; şema 0204 + veri 0205 | TAMAM (canlıda) | `f263438` | 3210 OK | 0204+0205 uygulandı; istasyon 10 → fason + 320-30-0040 FASON GÜRBÜZ LAZER, 11 istasyona sıra 10…110; liste/İstasyon Emirleri/ÜS-2026-0002 detay/istasyon düzenle 200; journal'da traceback yok |
| 2 | Tarih bazlı stok kontrolü (`eldeki_miktar(..., tarih=)`) + maliyet düzeltme komutu (yalnız kuru çalıştırma) | kod+test tamam, deploy bekliyor | | 3233 OK | |
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
- **Adım 2:** `hareket.eldeki_miktar(stok, depo, tarih=)` (tarih sonu itibarıyla), `hareket.cikis_kontrol` / `yetersiz_mesaji` TEK biçim:
  `Bu tarihte yetersiz: KOD (eldeki X, gerekli Y) — DEPO, gg.aa.yyyy`. `hareket_ekle` (üretim girdisi, satış faturası, elle çıkış, transfer bacağı),
  `sarf_cikis_ekle`, `numune.uygunluk_kontrol` hepsi bu kontrolden geçer; hata = hiçbir şey yazılmaz (mevcut atomik yapı). "Üretildi" satırında aynı mesaj
  + `— eksik: N.` (`uretim._eksik_mesaji` yeni biçimi ayrıştırır). Kontrol YALNIZ çıkışın kendi tarihindeki bakiyeye bakar (sonraki günlerde bakiye eksiye
  düşer mi kontrol edilmez — spec "o tarihte"). Test fixture dersi: üretim bugünün tarihiyle, sevk faturası geçmiş tarihle kurulamaz (`KapanisTaban.uret`
  artık `tarih=GUN`). Komut `stok_maliyet_duzelt [--fiyat KOD=TUTAR ...] [--uygula]` (varsayılan 150-50-0004=1,20; 151-40-0007=0,50; dry-run = transaction
  içinde uygula + rapor + geri al): fiyatsız GİRİŞ'lere miktar×fiyat (USD o günün kuru), `stok_ortalama.yeniden_hesapla` zinciri; rapor: fiyatlanan girişler,
  tutarı/durumu değişen hareketler, ortalaması değişen kartlar (152/151/diğer), 150/151/152/620/621/623 mizan ve 150-153 stok değeri farkları, değişen fiş
  sayısı. **UYGULAMA kullanıcı onayı bekliyor** — canlı kuru çalıştırma çıktısı `docs/stok_maliyet_duzelt_dryrun_20261011.txt`.
