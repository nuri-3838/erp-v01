# Üretim modülü sadeleştirme — ilerleme (C planı)

Gece oturumu 2026-10-11. Her adım: kod → ilgili testler → tam paket → commit → standart deploy (CLAUDE.md) → canlıda yalnız görüntüleme doğrulaması.
Oturum kesilirse buradan devam edilir. Canlıda hiçbir kayıt oluşturulmaz/onaylanmaz/silinmez (tek istisna: adım 1 veri migration'ı).

| # | Adım | Durum | Commit | Tam paket | Canlı doğrulama |
|---|------|-------|--------|-----------|-----------------|
| 1 | İş İstasyonları: `fason` / `fasoncu` / `sira`; sabit "10" kodu kaldırıldı; liste sütunları; şema 0204 + veri 0205 | TAMAM (canlıda) | `f263438` | 3210 OK | 0204+0205 uygulandı; istasyon 10 → fason + 320-30-0040 FASON GÜRBÜZ LAZER, 11 istasyona sıra 10…110; liste/İstasyon Emirleri/ÜS-2026-0002 detay/istasyon düzenle 200; journal'da traceback yok |
| 2 | Tarih bazlı stok kontrolü (`eldeki_miktar(..., tarih=)`) + maliyet düzeltme komutu (yalnız kuru çalıştırma) | TAMAM (canlıda) | `a51f9e1` | 3233 OK | ÜS detay/İstasyon Emirleri/Numune 200; canlı veride geriye tarihli çıkış denemesi "Bu tarihte yetersiz: 150-20-0003 (eldeki 0, gerekli 1) — 150, 30.06.2026" ile reddedildi (geri alındı); kuru çalıştırma raporu alındı (aşağıda) |
| 3 | ÜS detay: istasyon bazlı katlanır gruplar; "Bu istasyonu üret" / "Tümünü üret" (atomik) | TAMAM (canlıda) | `3268375` | 3244 OK | ÜS-2026-0002/0003/0004 detay 200; gruplar istasyon sırasıyla (12,15,20,…,80), tamamlanan gruplar kapalı; açık emir olmadığından toplu düğmeler görünmüyor (beklenen); "Kayıtları gör" var; traceback yok |
| 4 | Operasyon Kayıtları: varsayılan Onaylı; bağımsız yeni kaldırıldı; geri al yetkisi + iz | TAMAM (canlıda) | `e820306` | 3244 OK (klon DB'ler yeniden kuruldu) | 0206 uygulandı; liste 200 (aktif sekme Onaylı, "Bağımsız" yok, canlıda taslak 0 → Taslak sekmesi gizli), Tümü 200, OP-2026-0437 detay 200 ("Geri al ve sil" + iz notu); `/uretim/kayitlar/ekle/` dışarıdan 404; traceback yok |
| 5 | İhtiyaç Hesapla → Ürün Ağacı 4. görünüm; eski URL yönlendirme; düğümde ✎ | TAMAM (canlıda) | `9ad676d` | 3248 OK | eski adres 301 → `/uretim/urun-agaci/?gorunum=ihtiyac`; yeni görünüm 200 (segment aktif, form var); 152-10-0001 ağacında 16 ✎ + düzenle bağlantısı; ÜRETİM menüsünde İhtiyaç Hesapla yok; traceback yok |
| 6 | Operasyon Tanımları formu: Gelişmiş ▸ bölümü | TAMAM (canlıda) | `893aef6` | 3252 OK | yeni form ve sade tanım (151-10-0001) düzenlemede Gelişmiş kapalı, tür alanı içinde; PARÇALA tanımı (151-20-0014) düzenlemede açık + "parçala" rozeti; canlıda yan çıktılı tanım yok; traceback yok |
| 7 | İstasyon Emirleri: istasyon sekmeleri, filtre, toplu üret | kod+test tamam, deploy bekliyor | | | |
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
  sayısı. **UYGULAMA kullanıcı onayı bekliyor** — canlı kuru çalıştırma çıktısı `docs/stok_maliyet_duzelt_dryrun_20261011.txt` (2026-10-11 02:40):
  fiyatlanan giriş 2 (150-50-0004 hareket 98: 1000 × 1,20 = 1.200,00; 151-40-0007 hareket 99 [irsaliye, faturasız]: 34.632 × 0,50 = 17.316,00);
  tutarı değişen hareket 140 (39 girdi tüketimi [28 kılavuz + 11 tapa] + 151-99-* ara mamul giriş/çıkışları + 152 mamul girişleri + 13 satış ve 30 numune
  çıkışı; GECICI→KESIN); mamul birim maliyeti 11 kartta +3,20 (A tipi 2+1) … +4,20 (A 4+1/5+1/6+1) / +1,20 (çift çıkışlı); mizan farkı 150 −1.009,20 /
  151 −1.076,00 / 152 +1.901,00 / 620 +184,20; stok değeri farkı 150 +190,80 / 151 +16.240,00 / 152 +1.827,60; değişen fiş 48. Not: iki girişin fişi yok
  (manuel + faturasız irsaliye) → uygulanınca 150/151 stok değeri mizandan bu giriş tutarları kadar ayrışır (değerleme raporunda görünür).
- **Adım 3:** `uretim.istasyon_emri_sirasi` (istasyon `sira`, kod → aynı istasyonda derin seviye önce → pk), `toplu_uretilecek_emirler` (BITTI/IPTAL
  değil, fason değil, açık kalan > 0), `uretim_emri_toplu_uret(emir, istasyon=, tarih=)` — ÜS satırı kilitli, her emir `istasyon_emri_uret` (açık kalan kadar;
  kayıt aç + onayla; adım 2 tarih kontrolü) ile, `@transaction.atomic`: ilk hatada `TopluUretimHatasi` (emir, istasyon, çıktı, iç mesaj + "N emir üretilmişti;
  hepsi geri alındı"). Görünüm `uretim_emri_toplu_uret` (POST `istasyon`? + `tarih`; yetki `operasyon_kayitlari`), hata `messages.error` + satırda (`uret_hata`
  oturum anahtarı, mevcut desen). Detay: `istasyon_emri_gruplari` → `<details class="ie-grup">` (tamamlanan grup `open` değil), grup başlığında açık/bitti/iptal
  sayıları, "▶ Bu istasyonu üret", üstte "▶▶ Tümünü üret"; İş İstasyonu sütunu kalktı (grup başlığında). `istasyon_emri_satiri` ÜS detayı + İstasyon Emirleri
  listesinin ortak satır kurucusu. Operasyon Kayıtları bölümü: "N onaylı / M taslak / toplam" + "Kayıtları gör →" (`?uretim_emri=`). Testler
  `test_uretim_toplu_uret.py` (sıra, atomiklik, tek istasyon, fason atlama, geçmiş tarih, ekran).
- **Adım 4 (migration 0206, saf şema):** `OperasyonKaydi.geri_alan` (User FK) + `geri_alma_zamani`; `operasyon_kaydi_sil(onayli_geri_al=True)` izi yazar.
  Liste: varsayılan sekme **Onaylı** (emirden gelen `?uretim_emri=`/`?istasyon_emri=` bağlantıları yine Tümü); **Taslak** sekmesi yalnız açık taslak
  varken (sayıyla) görünür — pratikte yalnız fason dönüş taslakları (bağımsız kayıt ekranı kalktığından başka taslak kaynağı yok; "yalnız fason taslağı"
  yerine "herhangi açık taslak" seçildi ki olası bir taslak görünmez kalmasın); gizli sekme istenirse Onaylı'ya düşer; **Tümü** geri alınan (soft-silinmiş,
  `geri_alan` dolu) kayıtları "Geri alındı" rozetiyle (kim/ne zaman ipucu) listeler. "+ Yeni (Bağımsız)" view/URL/form/şablon KALDIRILDI
  (`operasyon_kaydi_ekle`, `OperasyonKaydiForm`, `operasyon_kaydi_form.html`; servis `operasyon_kaydi_olustur` fason/üretim için duruyor). "Geri al ve sil"
  yetkisi `operasyon_kayitlari` ekran yetkisi (eski `@yonetici_gerekli` kalktı); detay geri alınmış kaydı salt-okunur gösterir ("Geri alan: kullanıcı ·
  zaman"). Fason dönüş geri alma (`fason_donus_geri_al_sil`) DEĞİŞMEDİ (hâlâ yönetici). Testler: `test_uretim_yan_cikti_saklama.GeriAlSilEkranTest`,
  `test_operasyon_kayitlari_liste`, `test_uretim.test_bagimsiz_kayit_ekrani_kaldirildi`. Ders: yeni migration sonrası `--parallel --keepdb` klon
  test DB'leri eski şemada kalır → tam paket bir kez `--keepdb`siz koşulmalı (sahte 270 hata görüldü).
- **Adım 5:** Ürün Ağacı'na 4. segment `?gorunum=ihtiyac` "İhtiyaç (çoklu ürün)" (`views._ihtiyac_gorunumu`, şablon `_ua_ihtiyac.html`; POST formset,
  "Kullanılabilir stoğu düş", zincir + istasyon özeti aynen; `graf` ile hesap). `core:ihtiyac_hesapla` URL adı/yolu duruyor → Ürün Ağacı'na 301 (yetki artık
  `urun_agaci`; yalnız eski `ihtiyac_hesapla` yetkisi olan kullanıcı yeni yerde 403 alır — yetki ekranından `urun_agaci` verilmeli). `moduller.py`'den
  "İhtiyaç Hesapla" ekranı kalktı; eski `ihtiyac_hesapla.html` silindi; ÜS listesindeki düğme yeni yere. Ağaç düğümlerinde (ürün ağacı satırı ve ihtiyaç
  zinciri) ✎ → `operasyon_duzenle` (yalnız `operasyon_tanimlari` yetkisi olana; yazdırmada gizli). Testler `test_urun_agaci_ihtiyac.py`.
- **Adım 6 (yalnız şablon):** `operasyon_form.html` — ana kart: istasyon + çıktı + çıktı miktarı (+ tam boy kutusu) ve Girdiler; `<details
  class="op-gelismis" id="gelismis">` "Gelişmiş ▸": Tür (Üret/Parçala), Çıktılar (parçala) + maliyet pay anahtarı, Yan Çıktılar + ana çıktı boyu (mm).
  Yeni tanımda ve sade tanım düzenlemede KAPALI; PARÇALA tanımı ya da yan çıktısı olan tanım (kopya dahil) düzenlenirken AÇIK. Ana kart başlığında
  "parçala" rozeti (JS ile tür değişince de). Alan adları/ID'ler ve JS mantığı değişmedi (POST davranışı aynı; mevcut 149 form/servis testi geçti).
  Testler `test_operasyon_form_gelismis.py`.
- **Adım 7:** `istasyon_emirleri` görünümü: istasyon SEKMELERİ (`uretim.istasyon_acik_emir_sayilari`: açık ÜS'de BEKLIYOR/BASLADI emir sayısı) + Tümü;
  süzgeç yalnız ÜS no (`us`; eski `ara` bağlantıları da kabul: ÜS no / emir no) ve durum (varsayılan açık); `?istasyon=` ve `?uretim_emri=` korunur;
  bilinmeyen istasyon pk → boş liste. Satırda tek işlem: Tamamlandı rozeti / "Fason dönüş aç" (fason istasyonu) / Üretildi formu; emir no → o emrin
  Operasyon Kayıtları (eski "Kayıtlar" bağlantısı kalktı); seçili istasyonda İş İstasyonu sütunu yok. Seçili, fason olmayan istasyonda ve açık süzgecinde
  "▶▶ Bu istasyondaki açık emirlerin tümünü üret" → `istasyon_toplu_uret` (yeni URL `istasyon_toplu_uret`; yetki `operasyon_kayitlari`):
  istasyonun TÜM açık ÜS'lerdeki üretilebilir emirleri ÜS sırasıyla (yıl, sıra; aynı ÜS'de derin seviye önce), tek tarih, tek atomik işlem; hata mesajında
  ÜS no + emir + stok (`_sirayla_uret` adım 3 ile ortak). Testler `test_istasyon_emirleri_sekme.py`.
