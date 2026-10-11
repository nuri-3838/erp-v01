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
| E-2 | Hareket/ayırma/özet varyant boyutu, motor, veri göçü | bekliyor | |
