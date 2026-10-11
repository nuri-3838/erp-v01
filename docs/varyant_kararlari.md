# Varyantlı stok + proforma varyant sekmesi + lazer markalama — kararlar ve analiz özeti (2026-10-11)

Bu dosya kalıcı karar kaydıdır; uygulama adımları ilerledikçe durum tablosu güncellenir.

## Kullanıcı kararları (kesin)

1. **Varyant seçimi proformada** (ve teklif/sipariş satırında): her ürün satırında "Varyant" sekmesi, 4 soru — Markalama, Kullanma
   kılavuzu, Afiş, Yan etiket. Hepsi varsayılan SEMTA; özel üretimde değiştirilir. Seçimler teklif → proforma → sipariş → ÜS'ye otomatik
   taşınır; ÜS reçetesi ve istasyon emirleri buna göre kurulur. Proforma/sipariş PDF'inde standart dışı seçimler görünür (TR/EN).
   Cari kartında varsayılan TUTULMAZ.
2. **Varyantlı stok kartı:** ayrı kart açılmaz. Kartta "varyantlı" işareti; varyantlı kartlarda stok, ayırma, eldeki, hareket ve MALİYET
   "kart + varyant" bazında; her varyantın ayrı ortalama maliyeti. Varyantsız kartlar (perçin, profil, kayış…) bugünkü gibi.
3. **Varyantlı kartlar (53):** afiş A/C (150-50-0001/0003), yan etiket (150-50-0002), kullanma kılavuzu (150-50-0004); A tipi kesilmiş ön
   ayak 151-10-0001…0005, bükülmüş ön ayak 151-20-0001…0005, birleştirilmiş ön ayak 151-80-0001…0005; çift çıkış SOL ayak
   151-10-0014/16/18/20/22/24 ve birleştirilmiş ön ayak 151-80-0011…0016; paketsiz 151-99 (11); mamul 152 (11).
   Varyantsız: SAĞ ayaklar, arka ayak zinciri, basamaklar, Q16 borular, kayış, menteşe, naylon, perçin.
4. **Varyant değerleri ekrandan** (AYARLAR › Varyantlar): ilk değerler SEMTA (varsayılan), YOK, MR. MOP; proforma/ÜS'de "+ yeni varyant".
5. **Veri göçü:** mevcut stok → SEMTA; **istisna:** kesilmiş ön ayak / SOL ayak mevcut stoğu → YOK (markasız; lazer istasyonu yeni).
   152 mamul ve ambalaj kartları (afiş, yan etiket, kılavuz) → SEMTA.
6. **Markalama:** A tipinde markalanan tek parça = ön ayak (büküm öncesi). Çift çıkışta 4 ayaktan YALNIZ 1 SOL ayak markalı
   (2+2 … 7+7 tüm modellerde aynı); montaj 2 birleştirilmiş ön ayak kullanır: biri markalı (sol ayağı markalı), biri markasız.
7. **Müşteri afişi/kılavuzu/yan etiketi FATURALI gelir** (alış faturasında ilgili varyant seçilerek girilir).
8. **Fazla üretim engeli** LAZER MARKALAMA ve PAKETLEME emirlerinde (kalanı aşan hedef reddedilir).
9. **Boş = YOK:** teklif/proforma/sipariş/ÜS varyant alanları otomatik SEMTA dolu gelir. Kullanıcı bir alanı BOŞ bırakırsa anlamı YOK:
   markalama boş → lazer emri açılmaz, ayak markasız kalır; afiş/kılavuz/yan etiket boş → o girdi reçeteden kalkar, tüketilmez.
   Stok hareketlerinde (fatura, numune, sarf, transfer) varyantlı kartta varyant alanı varsayılan SEMTA dolu gelir; hareket varyantsız
   yazılamaz.
10. Yan etiket MONTAJ'dan PAKETLEME'ye taşınır (paketsiz 151-99 varyanttan nötr kalır). Mamul (152) varyantı = 4 seçimin kombinasyonu;
    standart = SEMTA/SEMTA/SEMTA/SEMTA; serbest stok standart varyanttır; standart dışı mamul siparişe ayrılı kalır. Stok için ÜS standart
    varyantla çalışır. Açık/tamamlanmış ÜS'ler (ÜS-2026-0002 Ertekpa) ve mevcut maliyetler bozulmaz.

## Analiz özeti

- **Etki:** `StokHareket.varyant` (+ `StokAyirma.varyant`, `StokVaryantOzet` ortalama önbelleği); `hareket_ekle/eldeki_miktar/toplu_eldeki/
  cikis_kontrol` varyant süzgeci (varyantsız çağrı = kart toplamı); `stok_ortalama.yeniden_hesapla` varyant başına zincir, `Stok` önbelleği
  kart toplamı; `ihtiyac_hesapla` düğümü `(stok, varyant)`, istenen varyanta göre operasyon seçimi, girdi varyantı kurallardan; `IstasyonEmri`
  (ÜS, operasyon, çıktı varyantı); operasyon kaydı girdi/çıktı varyantı; fatura/irsaliye/numune/sarf/transfer varyant parametresi;
  stok listesi/kartı varyant kırılımı. Değerleme/mizan kart toplamında aynı.
- **Operasyon kuralları:** `OperasyonGirdi.varyant_kurali` = SABIT(değer) | TASI (çıktının istenen varyantından o kartın boyutları);
  `Operasyon.cikti_varyant_kurali` = TASI | SABIT. Lazer markalama = DÖNÜŞTÜR operasyonu (aynı kart: girdi SABIT YOK → çıktı TASI);
  kesim çıktısı SABIT YOK. Çift çıkış: birleştirme SOL TASI, SAĞ varyantsız, çıktı TASI (markalı/markasız iki varyant, iki istasyon
  emri); montaj girdileri 151-80-00xx ×1 TASI + ×1 SABIT YOK. Paketleme: paketsiz TASI(markalama), afiş/yan etiket/kılavuz TASI
  (boş → girdi kalkar), naylon varyantsız.
- **Veri göçü:** şema ve veri migration'ları ayrı; varyantlı kartların tüm hareketleri + açık ayırmaları tek varyanta (SEMTA / ayaklarda
  YOK) → varyant zinciri = bugünkü kart zinciri, tutarlar birebir aynı (migration testi önce/sonra karşılaştırır). Ertekpa ÜS'si:
  ayırmalar SEMTA, istasyon emirleri `cikti_varyant=SEMTA`.
- **Adımlar:** E-1 tanım (bu commit) → E-2 hareket/ayırma/özet varyant boyutu + veri göçü → E-3 giriş/çıkış ekranlarında varyant →
  E-4 proforma/teklif/sipariş varyant sekmesi + PDF → E-5 operasyon kuralları + LAZER MARKALAMA istasyonu + DÖNÜŞTÜR → E-6 planlama
  motoru + ÜS/istasyon emri/kayıt → E-7 stok kuralları (standart dışı 152'den karşılanmaz, fazla üretim engeli) → E-8 Mr. Mop pilotu.

## Adım durumu

| Adım | İçerik | Durum | Commit |
|---|---|---|---|
| E-1 | `Varyant` + `Stok.varyantli/varyant_boyutlari` + `StokVaryant` + AYARLAR › Varyantlar + stok kartı işareti/boyut seçimi; seed SEMTA/YOK/MR. MOP | TAMAM, canlıda | `9bd7917` (3278 test) |
| E-2 | Hareket/ayırma/özet varyant boyutu, motor (eldeki, tarih kontrolü, ortalama, ayırma), stok kartı kırılımı, `varyant_veri_gocu` komutu (kuru + altın kontrol) | TAMAM, canlıda; VERİ GÖÇÜ UYGULANDI 2026-10-11 15:35 (53 kart, 338 hareket, 7 ayırma; altın kontrol EŞİT; raporlar `docs/varyant_veri_gocu_kuru_20261011.txt`, `docs/varyant_veri_gocu_uygulandi_20261011.txt`) | `064f2c0` (3288 test) |
| E-3 | Giriş/çıkış ekranlarında varyant seçimi (alış/satış faturası, irsaliye, numune, sarf, transfer, elle hareket; kartın varsayılanı dolu gelir, varyantsızda alan yok; alış faturasında yeni kombinasyon açılır; sipariş→fatura ve alış zincirinde kalem varyantı taşınır) | TAMAM, canlıda | (bu commit) |
| E-4 | Proforma/teklif/sipariş satırında "Varyant" bölümü (4 soru; SEMTA dolu, boş = YOK), "+ yeni varyant", dönüşümlerde taşıma, PDF TR/EN'de yalnız standart dışı seçimler; ÜS kilidi (özel varyantlı siparişten ÜS açılmaz, E-6'ya kadar) | TAMAM, canlıda | (bu commit) |
| E-5 | Operasyon girdi/çıktı varyant kuralları (TAŞI/SABİT) + DÖNÜŞTÜR tipi + `varyant_operasyon_hazirla` komutu (LAZER MARKALAMA istasyonu, kesim SABİT YOK, 11 dönüştür tanımı, ÇÇ montaj ayak bölme, yan etiket → paketleme; altın kontrol) | kod canlıda; VERİ (kuru rapor alındı) kullanıcı onayı bekliyor | (bu commit) |
| E-6 | Planlama motoru (stok, varyant) + ÜS/istasyon emri/kayıt/onay/fason dönüş varyantı; lazer emirleri | bekliyor | |

## E-5 notları

- DÖNÜŞTÜR tanımının çıktı satırı sürücü DEĞİLDİR: `ureten_operasyon`, `ihtiyac_hesapla` ve ürün ağacı bu tanımı görmez → E-6'ya kadar pasif,
  standart ÜS planı/istasyon emirleri birebir (komutun altın kontrolü + test). Kartı üreten tanım kesim (fason) tanımı kalır.
- Kurallar yalnız tanımdır; E-6 planlama: istenen çıktı varyantı → TASI girdiler o kartın boyutlarıyla aynı varyant, SABİT girdiler sabit değer;
  istenen varyant kesimin SABİT YOK çıktısından farklıysa DÖNÜŞTÜR (lazer) tanımı araya girer.
- Veri hazırlığı `python manage.py varyant_operasyon_hazirla` (kuru) → `--uygula` yalnız onayla; ÜS-2026-0001…0004 ve geçmiş kayıtlar değişmez.

## E-4 notları

- Boş bırakılan boyut `YOK` değerine normalize edilir (`stok_varyanti_bul_veya_olustur`): "Afiş: boş" ve "Afiş: YOK" tek StokVaryant satırı.
  Ekranda boş seçenek "YOK" diye görünür; ayaklardaki YOK varsayılanı da boş seçenek olarak gelir.
- `standart_disi_metin(sv, dil)`: yalnız varsayılan (SEMTA) dışındaki boyutlar; YOK → "yok"/"none", diğerleri kod (EN: ad_en varsa).
  PDF satır notu `varyant-not`; tamamı standart ise şablon çıktısı birebir aynı.
- ÜS kilidi: `uretim.siparis_ozel_varyantli` + `OZEL_VARYANT_MESAJI`; `siparisten_uretim_emri_olustur` reddeder, sipariş detayında düğme pasif.

## E-3 notları

- `FaturaSatir.varyant`, `TeklifSiparisKalem.varyant`, `NumuneCikisSatir.varyant` (0211 yalnız şema). Boş = kartın varsayılanı (hareket o
  varyantla yazılır); satır/kalem kaydında varyant seçildiyse `varyant.secim_coz` 'v:BOYUT=pk|…' değerini çözer, kombinasyon yoksa açar.
- Ortak JS `core/_varyant_js.html`: `select[name$="-stok"]` değişince satırda boyut başına select; tek stoklu formlarda `.varyant-alan[data-stok]`.
  Alış/satış faturası, teklif/sipariş/irsaliye, sipariş revize, numune, elle hareket, depo transferi, sarf bu parçayı kullanır.
- Fatura yerinde güncelleme anahtarı artık (stok, miktar, varyant): yalnız fiyat değişince hareket korunur; varyant değişince sil + yeniden yaz.
- Satış belgelerinde (teklif/proforma) 4 soruluk "Varyant" sekmesi ve PDF E-4'te; bu adımda satış sipariş formunda da aynı genel alan var.

## E-2 notları

- Şema ve veri ayrı: 0210 yalnız şema; veri göçü `python manage.py varyant_veri_gocu` (varsayılan KURU; `--uygula` yalnız altın kontrol
  birebir eşitse yazar; önce `bash scripts/db_backup.sh`).
- `Stok.varsayilan_varyant`: varyant verilmeden yazılan hareket/ayırma bu varyanta gider (E-3/E-6'ya kadar üretim/fatura/numune akışları
  dokunulmadan çalışsın diye). Standart kartlarda SEMTA; kesilmiş ön ayak / SOL ayakta veri göçü YOK atar (karar 5: mevcut ayaklar markasız).
  Karar 9'daki "formlarda SEMTA dolu gelir" ekran kuralı E-3'te; ayaklarda formun varsayılanı kartın varsayılanı (YOK) olacak.
- Varyantlı kartta her varyant ayrı ortalama; kart önbelleği toplam (tek varyantta bugünkü değerlerle birebir — altın kontrol bunu kanıtlar).
- Kullanılabilir (kart toplamı) ve `ihtiyac_hesapla` değişmedi (E-6).
