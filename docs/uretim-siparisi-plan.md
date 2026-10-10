# Üretim Siparişi (satış siparişinden) + stok ayırma + istasyon emirleri — plan ve durum

Bu dosya işin **tek güncel durum kaydıdır**: her adım commit edildikçe tablo güncellenir. Analiz 2026-10-10'da onaylandı;
kararlar aşağıda. Ara deploy YOK — tüm adımlar `main`'de birikir, canlıya alma ayrı talimatla.

## Kararlar (kullanıcı, 2026-10-10)

1. `UretimEmri` genişletilir (yeni model yok). Ekran adı "Üretim Siparişleri"; yeni numara `ÜS-yyyy-nnnn`, eski `UE-` kayıtlar aynen.
2. Manuel (siparişsiz) ÜS de yeni akışı kullanır. Canlıdaki kayıtsız 1 eski emir deploy'da silinir.
3. Kullanılabilir kuralı **yumuşak**: hiçbir çıkış engellenmez; kullanılabilir eksiye düşünce ÜS'de ve ilgili ekranlarda uyarı.
4. Ürün ağacı malzeme sekmesi: Ayrılan, Kullanılabilir, kullanılabilirden Eksik. İhtiyaç Hesapla varsayılan BRÜT, "stoku düş" seçmeli.
5. Sevk = depolu satış faturası onayı; deposuz fatura ÜS'yi kapatmaz, uyarı verir. Satış irsaliyesi ayrı iş (yok).
6. Onaylı satış siparişi kalem düzenlemesi açılır → aynı işlemde ÜS revize. Siparişli ÜS kalemi = sipariş kalem miktarı (aşamaz, farklı olamaz).
7. Kayıt hedefi kalanı aşabilir (uyarıyla), fazlası serbest stok. Fason firesi sonrası kalan açık kalır.
8. Tam boy ve PARÇALA fazlası serbest stok (ayrılmaz).
9. Başlamış ÜS kalıcı silinmez, yalnız İPTAL.

## Tasarım özeti

- **Ayırma** (`StokAyirma`): (ÜS, stok) başına tek aktif satır, miktar ≥ 0; stok hareketi değil. Kullanılabilir = eldeki − açık ÜS'lerin
  ayrılan toplamı (tüm depolar); `haric_emir` ile ÜS kendi ayırdığını kullanılabilir görür. Tek yazma noktası `stok_ayirma.ayirma_ayarla/ekle`.
- **Net ihtiyaç**: `ihtiyac_hesapla(kullanilabilir=)` — `None` iken kod yolu birebir aynı (golden değişmez). Verilince her seviyede
  ayrılan = min(talep, kalan kullanılabilir), net = talep − ayrılan, çalıştırma netten (tam boy ⌈·⌉, PARÇALA max), fazla serbest.
- **ÜS açılışı**: net plan → `StokAyirma` (mamul/ara/hammadde) + her operasyon için `IstasyonEmri` (planlanan/tamamlanan çalıştırma
  biriminde, durum BEKLIYOR/BASLADI/BITTI/IPTAL). Taslak `OperasyonKaydi` otomatik açılmaz.
- **Kayıt**: emirden açılır (`OperasyonKaydi.istasyon_emri`), hedef varsayılan kalan; onayda girdi tüketimi ayırmayı düşer, sürücü çıktı girişi
  ÜS'nin kalan ihtiyacına kadar ayrılır, tamamlanan gelenden artar. Fason: istasyon 10 emrinden fason dönüşü açılır.
- **Revize**: kullanılabilir(haric=ÜS) ile yeniden plan; ayırmalar hedefe çekilir; emir `planlanan = tamamlanan + yeni üretilecek`;
  başlamamış ve gereksiz emir IPTAL; taslak kayıt hedefi kalanı aşarsa düşürülür; sevk edilenin altı reddedilir; `UretimEmriRevizyon` logu.
- **Kapanış**: depolu satış faturası onayı → mamul ayırması düşer, tüm emirler BITTI/IPTAL ise ÜS KAPALI; fatura silinince yeniden açılır.

## Adımlar ve durum

| # | Adım | Durum | Commit |
|---|------|-------|--------|
| 1 | `StokAyirma` + `stok_ayirma` servisi (kullanılabilir) + ürün ağacı malzeme sekmesi Ayrılan/Kullanılabilir/Eksik (ekran + Excel) | TAMAM | `09233a8` |
| 2 | Motor: `ihtiyac_hesapla(kullanilabilir=)`; İhtiyaç Hesapla "stoku düş" seçeneği | TAMAM | `b6cc2a8` |
| 3 | Şema: ÜS durum/revizyon alanları, `UretimEmriKalemi.siparis_kalem`, `IstasyonEmri`, `OperasyonKaydi.istasyon_emri`, `UretimEmriRevizyon`; ÜS-numara; sipariş servis kilitleri | TAMAM | `9387369` |
| 4 | ÜS açılış servisi (siparişten + manuel): ayırma + istasyon emirleri; taslak kayıt açma kaldırılır | TAMAM | `f6069f6` |
| 5 | Emirden kayıt açma; onay/geri alma etkileri (ayırma, tamamlanan, durum) | TAMAM | `1cc6443`, `9d71ed2` |
| 6 | Fason dönüşü emre bağlama, fire | TAMAM | `404fc57` |
| 7 | Revize algoritması + geçmiş; onaylı sipariş kalem düzenleme → ÜS revize | TAMAM | `7a0b49a` |
| 8 | Kapanış (satış faturası) + yeniden açılış; deposuz fatura uyarısı | TAMAM | `75edbf9` |
| 9 | Ekranlar: ÜS liste/detay, istasyon emirleri (yeni ekran kodu `istasyon_emirleri`), kayıt listesi/formu, sipariş detayı rozeti | TAMAM | (bu commit) |
| 10 | Deploy (ayrı talimat): yedek, migrate, canlıdaki eski emrin silinmesi, yetki ataması | bekliyor | |

## Adım notları

- **Adım 1:** Ayırma kümesi adım 1'de "silinmemiş ÜS" ile sınırlıydı; adım 3'te `durum=ACIK` filtresi eklendi (`stok_ayirma.acik_ayirmalar`).
  Ürün ağacı malzeme sekmesinde depo filtresi seçiliyse eldeki o depodan, ayrılan tüm depolardan; kullanılabilir eksiye düşebilir (`asim`).
  Mevcut `uretim_emri_olustur` davranışı (taslak kayıt açma) bu adımda DEĞİŞMEDİ.
- **Adım 2:** `ihtiyac_hesapla(kullanilabilir=)` — sözlük ya da çağrılabilir (zincir keşfinden sonra stok pk listesiyle tek çağrı,
  `stok_ayirma.kullanilabilir_haritasi`). Ayırma her stok için TEK kez: sürücü çıktılar kendi operasyonunda (seviye sırası, toplanmış talep),
  yapraklar operasyon döngüsünden sonra. Plan/özet satırlarına `ayrilan`, `net`, `eksik` (yaprak); ağaç düğümlerine `ayrilan_toplam`,
  `net_toplam`, `eksik_toplam`; dönüşe `net_mod`, `ayrilan` haritası. Tam boy olmayan dalda çocuk talebi net/ihtiyaç oranıyla küçülür.
  `pay_dus=True` ile birlikte kullanım öngörülmedi (maliyet görünümleri brüt). Brüt yol (None) birebir aynı — golden geçti.
  İhtiyaç Hesapla ekranı: "Kullanılabilir stoğu düş" onay kutusu (varsayılan kapalı = brüt); özet tablosuna "Stoktan Ayrılan" ve
  "Net İhtiyaç / Eksik" sütunları, ağaçta "stoktan X · net Y" / "eksik" notu.
- **Adım 3 (migration 0199, saf şema):** `UretimEmri.durum` (ACIK/KAPALI/IPTAL, varsayılan ACIK), `revizyon_no`, `kapanis_tarihi`; yeni
  numara `ÜS-yyyy-nnnn` (aynı yıl/sıra sayacı, eski `UE-` numaralar aynen); `UretimEmriKalemi.siparis_kalem` (FK, PROTECT) ve
  `eldeki_ayrilan` (açılış/revize snapshot'ı); `IstasyonEmri` (sipariş×operasyon tek aktif satır, `IE-yyyy-nnnn`, `seviye`, `planlanan`/
  `tamamlanan` ÇALIŞTIRMA biriminde, `kalan` özelliği, durum BEKLIYOR/BASLADI/BITTI/IPTAL); `OperasyonKaydi.istasyon_emri` (FK, PROTECT);
  `UretimEmriRevizyon` (sipariş içinde sıralı `no`, tür ACILIS/REVIZE/KAPANIS/YENIDEN_ACILIS/IPTAL, `tarih`, `detay` JSON).
  Sipariş servis kilidi: `teklif_siparis._uretim_siparisi_hedefi` — SATIŞ siparişi iptal edilmemiş bir üretim siparişine bağlıysa
  düzenleme / onay geri alma / iptal servis katmanında da reddedilir (önceden yalnız ekranda). `stok_ayirma.acik_ayirmalar` artık yalnız
  `durum=ACIK` siparişleri sayar (kapalı/iptal siparişin ayırması kullanılabilirden düşmez). Test fixture'ı `SiparisUretimFixture` mixin'e
  çıkarıldı (test_teklif_siparis). Numara üretimi ve emir açma servisi adım 4'te.
  İnceleme notu (adım 4/9'a devir): `siparisten_uretim_emri_olustur` ve sipariş detay ekranı iptal edilmiş ÜS'yi hâlâ "açılmış" sayar
  (servis kilidi saymaz) — adım 4'te kural: siparişin tüm ÜS'leri IPTAL ise yeni ÜS açılabilir; adım 9'da ekran rozeti/düğmeleri aynı kurala.
- **Adım 4:** `uretim_emri_olustur` artık net plan çalıştırır (`ihtiyac_hesapla(kullanilabilir=kullanilabilir_haritasi)`; hesap yazmadan önce, döngü hatası
  hiçbir şey bırakmaz) ve yazar: kalemler (`eldeki_ayrilan` snapshot'ı, kök stoğun ayrılanından sırayla, kalem miktarını aşmaz), ayırmalar
  (`StokAyirma`, stok hareketi yok), her operasyon için `IstasyonEmri` (`planlanan` = net çalıştırma, `seviye` plan satırından — `ihtiyac_hesapla`
  planına `seviye` anahtarı eklendi; net çalıştırma 0 ise emir açılmaz), açılış revizyonu (no 0, detay JSON). OperasyonKaydi AÇILMAZ.
  `emir_hedef_cikti(ie)` = planlanan × referans çıktı miktarı (kayıt hedefi; adım 5'te kalan × referans). Golden eşdeğerlik değişmedi: emir kayıtları
  istasyon emri hedefinden açılınca eski taslak kayıtlarla BİREBİR aynı hedef/girdi planı verir (test bu yoldan türetir).
  `IstasyonEmri.planlanan/tamamlanan` 10 ondalık (6 ondalıkta 20/12 gibi çalıştırmalar 20,000004 hata veriyordu); 0199 henüz uygulanmadığı için yerinde güncellendi.
  Siparişten açılış: `siparis_kalemleri` ile `siparis_kalem` bağı; ÜS miktarı sipariş kalem miktarına EŞİT olmalı (karar 6/10); aynı sipariş kalemi
  iki kez seçilemez; aynı stok iki ayrı sipariş satırında olabilir (toplanmış talepten netleme); siparişin tüm ÜS'leri IPTAL ise yenisi açılabilir
  (servis + sipariş ekranları). `uretim_emri_ilerleme` istasyon emirlerinden (BITTI/iptal olmayan; eski emirlerde kayıtlardan).
  `uretim_emri_sil` yalnız başlamamış ÜS'yi siler (başlamış = istasyon emri BASLADI/BITTI/tamamlanan>0 ya da onaylı/emirden açılmış kayıt; karar 9),
  `uretim_emri_iptal` eklendi (taslak kayıtlar iptal, bitmemiş istasyon emirleri IPTAL, ayırmalar kapanır, IPTAL revizyonu). İptal ekranı adım 9.
- **Adım 5 (migration 0200, saf şema):** üç iz alanı — `IstasyonEmri.ihtiyac` (açılış/revize anı: sürücü çıktı başına üretimle karşılanacak NET miktar),
  `OperasyonKaydiCikti.ayrilan` (onayda ÜS'ye ayrılan), `OperasyonKaydiGirdi.ayrilan_dusen` (onayda ayırmadan düşen) — geri alma bu izlerle aynen geri sarılır.
  `istasyon_emri_kayit_ac(ie, hedef=None, depo_id, tarih)`: emir satırı kilitli; varsayılan hedef = açık kalan × referans (kalan = planlanan − tamamlanan −
  açık taslak kayıtlar); kalan yoksa hedef zorunlu; hedef kalanı aşabilir (`kayit_fazla_uyarisi`; karar 7). Yalnız açık ÜS / iptal olmayan emir.
  Onay (`operasyon_kaydi_onayla` sonu): girdi tüketimi kadar ÜS ayırması düşer, üretilen parça `ihtiyac − önceki onaylı ayrılan` kadar ayrılır (fazlası
  serbest — karar 8), `tamamlanan += kayit_calistirma(kayit)` (ÜRET'te ana çıktının giriş miktarı / referans = fason fire sonrası; PARÇALA'da hedef/referans),
  durum yeniden hesaplanır (BEKLIYOR/BASLADI/BITTI; açık taslak = BASLADI). Geri al (`operasyon_kaydi_sil(onayli_geri_al=True)`): ayırma izleri ve tamamlanan
  geri sarılır; taslak silinince durum güncellenir. İptal edilmiş/kapanmış ÜS'nin emre bağlı kaydı onaylanamaz. Emre bağlı olmayan (bağımsız) kayıtlar etkilenmez.
  Eş zamanlılık: onay ve geri alma etkisi istasyon emri satırını `select_for_update` ile kilitler (aynı emrin iki kaydı sıraya girer; çift ayırma yok).
  Fason dönüşü emre bağlama adım 6'da.
- **Adım 6:** `donus_olustur` satırına 5. öğe `istasyon_emri_id` (ÜS istasyon emri): operasyon eşleşmeli, ÜS açık ve emir iptal olmamalı; kayıt `uretim_emri` +
  `istasyon_emri` bağlı açılır, emir durumu güncellenir. `istasyon_emri_fason_donusu_ac(ie, cari_id, depo_id, tarih, adet=None, gelen_ana, gelen)`: tek satırlı
  taslak belge; varsayılan adet = açık kalan × referans (ÜRET beklenen ana adet; PARÇALA referansın gelen adedi). Onayda tamamlanan GELEN adetten artar
  (`kayit_calistirma`): fire sonrası kalan açık kalır, ikinci dönüş kalanı kapatır; ayırma yalnız gelen kadar. Tam boy kuralı korunur (kalan 0,67 boy için
  kayıt 1 tam boy beklenir; fazlası serbest stok). PARÇALA: çıktı bazlı gelenden çalıştırma, talep edilmeyen kardeş çıktı serbest stok. Geri al / taslak sil
  emri geri sarar. Ekran: fason dönüş formunda gizli `istasyon_emri` alanı; `?istasyon_emri=<pk>` ile operasyon ve açık kalan adet ön dolu gelir
  (istasyon emri ekranındaki "Fason dönüş aç" düğmesi adım 9'da bu bağlantıyı kullanacak).
- **Adım 7:** `uretim_emri_revize(emir, kalemler, tarih, aciklama, siparis_kalemleri)` — danışma kilidi + ÜS satır kilidi; (1) sevk/fatura edilmiş
  miktarın (`sevk_edilen_haritasi`: kaynak siparişin bağlı satış faturası satırları) altına düşürme reddedilir; (2) net plan ÜS'nin kendi ayırmaları
  kullanılabilir sayılarak (`haric_emir`) çözülür; (3) kalemler soft-delete + yeniden (`eldeki_ayrilan` yeniden); ayırmalar plana çekilir
  (`_ayirmalari_esitle`: azalan fark serbest, planda olmayan kapanır); (4) her operasyon: planlanan = tamamlanan + yeni net, `ihtiyac` = yeni net +
  onaylıdan ayrılan (KÜMÜLATİF — onay kuralı `ihtiyac − önceki ayrılan` ile uyumlu), yeni net 0 & tamamlanan 0 → IPTAL (taslakları iptal), tamamlanan varsa
  planlanan = tamamlanan (BITTI); iptal edilmiş emir gerekli olursa aynı satır yeniden açılır (uq kısıtı); yeni operasyona yeni emir; açık taslak kayıtlar
  eski sıradan yeni nete sığdırılır (`_taslaklari_sigdir`: hedef düşürme / iptal, revizyon detayında listelenir); (5) REVIZE revizyonu (`once`/`sonra`
  özetleri). `siparis_revize(siparis, satirlar)`: onaylı satış siparişinin kalemlerini değiştirir (teklif_siparis satır biçimi, eski kalemler soft-delete)
  ve ÜS'yi üretime uygun kalemlerle revize eder; uygun kalem kalmazsa reddedilir. Kalem/ÜS ekranı adım 9. Test: `tutarlilik_dogrula` ortak doğrulayıcı
  (ayırma = güncel net plan ayrılanı; planlanan = tamamlanan + net; ihtiyaç kümülatif; taslaklar kalana sığar; durumlar) her revize sonrası çağrılır.
- **Adım 8 (migration 0201, saf şema `UretimEmri.sevk_dusen`):** sevk = depolu + ONAYLI satış faturası (`sevk_depolu_mu`). `siparis_sevk_edildi(siparis)` (sipariş→fatura
  ekranı faturayı bağlayınca çağrılır): fatura satırı kadar ÜS'nin o stok ayırması düşer (`sevk_dusen`e yazılır); tüm istasyon emirleri BITTI/IPTAL ise ÜS KAPALI
  (`kapanis_tarihi`, revizyon KAPANIS), değilse uyarı ("bitmemiş istasyon emirleri var"); deposuz/onaysız fatura ÜS'ye dokunmaz, uyarı döner (ekranda `messages.warning`).
  Son istasyon emri onaylanınca (`_istasyon_emri_onay_etkisi`) sevk edilmiş ÜS otomatik kapanır. `fatura_sil` → `siparis_fatura_silindi`: `sevk_dusen` aynen geri
  eklenir, KAPALI ÜS yeniden ACIK (YENIDEN_ACILIS); deposuz fatura silinmesi ÜS'yi değiştirmez. Revize artık sevk edilen miktarı üretim ihtiyacından düşer
  (`plan_kalemleri`): sevk edilen mamul yeniden üretilmez/ayrılmaz; kalem miktarı sevkin altına düşemez (gerçek fatura fixture'ıyla test edildi). Önceki bilinen
  sınır (fatura düzenlemesiyle depo/miktar değişimi) adım 9 başında KAPATILDI (`8b33865`): ÜS'ye (ACIK/KAPALI) bağlı faturada `fatura_guncelle` depo/stok/miktar
  değişimini "Üretim siparişine bağlı; faturayı silip yeniden kesin" diye reddeder; fiyat/tarih/açıklama serbest; İPTAL ÜS engel değil.
- **Adım 9 (ekranlar, şema yok):** `moduller.py`: "Üretim Siparişleri" (kod `uretim_emirleri` aynı) + yeni ekran `istasyon_emirleri` (kimseye otomatik açılmaz; adım 10'da yetki ata).
  ÜS liste: durum süzgeci/rozeti, revizyon no, kaynak sipariş; sil yalnız başlamamış ÜS'de (`uretim_emri_silinebilir`). ÜS detay: kalem tablosu (hedef/ayrılan/üretilecek/sevk/ilerleme %),
  istasyon emirleri (planlanan/tamamlanan/açık kalan referans adedinde; "Kayıt aç" [isteğe bağlı adet] ve "Fason dönüş aç" `?istasyon_emri=`), ayırma listesi, operasyon kayıtları
  (istasyon emri sütunu), revizyon geçmişi (REVIZE'de "KOD: 3 → 5"), düğmeler Revize / İptal Et / Sil. Yeni görünümler: `uretim_emri_iptal`, `uretim_emri_revize` (manuel ÜS;
  siparişli ÜS sipariş revizesine yönlenir), `siparis_revize` (sipariş kalemleri + ÜS tek işlemde), `istasyon_emirleri` (istasyon/durum/ÜS/arama süzgeci; varsayılan bekleyen+başlayan),
  `istasyon_emri_kayit_ac` (POST; fazla uyarısı). Operasyon kayıtları listesi: istasyon emri sütunu + `?istasyon_emri=`/`?uretim_emri=`/`?emir=bagimsiz`. Sipariş detayı: ÜS rozeti
  (durum, revizyon) + "Kalemleri revize et"; IPTAL ÜS rozet/kilit yok ve yeniden açılabilir. Testler: `test_uretim_siparisi_ekranlar.py`.
- **Canlı ilk deneme düzeltmeleri (adım 10 sonrası, 0202):** (1) Tek fabrika deposu: `Depo.aktif` (migration `0202_depo_aktif`, saf şema; pasif depo seçimde çıkmaz, silinmez, eldeki stoklu depo pasif yapılamaz);
  `aktif_depolar()` yalnız aktif, `tum_depolar()` yönetim listesi (Pasif rozeti), `varsayilan_depo()` = SEMTA DEPO (yoksa ANA DEPO, yoksa ilk fason-dışı). Komut `depo_sadelestir [--dry-run]`:
  150 → "SEMTA DEPO" adı, 151/152 eldeki stoğu `depo_transferi_yap` ile (maliyet değişmez, tarih bugün) 150'ye, sonra 151/152 pasif; değerleme/mizan veya stok toplamı değişirse tüm işlem geri alınır;
  fason depoları dokunulmaz. (2) ÜS detayı "Eksik Malzeme (satınalma ihtiyacı)" (`uretim_emri_eksikleri`: revizenin net-plan hesabıyla yaprak eksikleri) + liste rozeti. (3) "Fason dönüş aç" yalnız istasyon kodu 10
  (`fason.FASON_ISTASYON_KODU`) emirlerinde. (4) Revizyon tarihi = işlem günü, ekranda `created_at` saatiyle ("İşlem Zamanı").
