# ERP v0.1 — Dondurulmuş Kapsam ve Çekirdek Veri Modeli

> **Tek kural:** v0.1 ayakta çalışıp sen onu *gerçekten kullanmaya* başlamadan, bu kapsama hiçbir şey eklenmez. "Es geçmeyelim" dediğin her şey atılmıyor — sadece sıraya konuyor.

---

## 0. Mimari karar (şimdi verilir, sonra değiştirilmez)

**Defter ile modülleri ayır.** Çekirdek = çift taraflı yevmiye motoru. Stok, satınalma, satış, üretim — hepsi birer **fiş üreticisidir.** Defter, fişin nereden geldiğini umursamaz; yalnızca **borç = alacak** dengeli fişleri kabul eder.

**Maliyet yöntemi: 7/A** (işleve göre). 7'li hesaplar birer **ara duraktır**: içine topladıklarını yansıtma (7x1) ile dışarı akıtır, bakiyeleri sıfırlanır. **Yansıtma yazılım tarafından otomatik ATILMAZ — ay sonunda kullanıcı (mali müşavir) elle yapar.** Yazılımın görevi sağlam manuel fiş girişi ve 6'lıdan türeyen raporlardır.

**Para birimi:**
- **TL = fonksiyonel (defter) para birimi.** Yevmiye, mizan, bilanço, gelir tablosu tamamen TL; tek doğruluk kaynağı budur.
- **USD = ikincil raporlama görünümü.** Ayrı defter değil; TL'nin üstüne giydirilen çeviri katmanı. USD tutar her zaman `TL ÷ usd_kuru` türetilir.
- **EUR, GBP = işlem para birimleri.** İşlem orijinal para biriminde + o günün TL karşılığıyla saklanır; defter yine TL yürür. EUR/GBP için ayrı raporlama görünümü YOK.
- ERP toplam **4 işlem para birimi** kullanır: TRY / USD / EUR / GBP. Her fişe/satıra o günün TCMB kuru yazılır — **şimdiden** başla, sonradan doldurmak zahmetlidir.

**Kapsam dışı (hiç yapılmayacak):** e-Fatura, e-Arşiv, e-İrsaliye, e-Defter, beyannameler. Resmi/sertifikalı kanalda kalır. Bu ERP bir **iç yönetim + muhasebe sistemidir.**

---

## 0b. Sistem geneli temel kurallar (en baştan, istisnasız)

> **Meta-ilke: Kurallar çekirdekte zorlanır, kenara güvenilmez.** Tüm değişmezler (denge, sayı biçimi, büyük harf, zorunlu alanlar) UI'da değil **model/servis katmanında tek noktada** zorlanır. UI hata yapsa bile çekirdek yanlış veriyi kabul etmez. (Decimal hatasının asıl dersi: bir giriş yolu merkezi fonksiyonu atlamıştı.)

### a) Sayı / para biçimlendirme — KRİTİK
Geçmişte "10,35 → 1.035,00" hatasının kökü: saklama ile gösterim/giriş'in karışması. Kesin kural:

- **Saklama:** Para/miktar/kur alanları DB'de **DECIMAL/NUMERIC** (asla float). Ölçek: para 2 hane (kuruş), kur 6 hane, miktar 3–4 hane. İç temsil her zaman **nokta-ondalıklı, ayraçsız, saf Decimal** (`10.35`).
- **Locale yalnızca iki kenarda yaşar:** giriş ve gösterim. Çekirdek ve DB hiç locale bilmez.
- **TEK giriş ayrıştırıcı (parser)** + **TEK gösterim biçimleyici (formatter).** Her giriş/çıkış istisnasız bunlardan geçer. Hiçbir yerde elle `parseFloat`/string matematiği YOK.
- **Tek, belirsizliksiz kural:** nokta = binlik, virgül = ondalık (TR). `1.035` = 1035 · `10,35` = 10.35 · `1.234.567,89` = 1234567.89. Giriş alanı kullanıcıyı bu biçime yönlendirir (maske/canlı format).
- **Gönderim:** Forma yazılan "10,35", sunucuya gitmeden kanonik `10.35`e çevrilir; sunucu da strict TR parser ile tekrar doğrular. **Ham locale metni asla DB'ye ulaşmaz; string üstünde asla matematik yapılmaz.**
- **Zorunlu otomatik test:** `10,35` / `1.035,00` / `1.035` / `0,5` / `-1.234.567,89` / `1000000` / boş / `10.35` (kanonik) girdileriyle parser+formatter test edilir. Bu testler geçmeden bu konu "bitti" sayılmaz.
- **Tek yuvarlama kuralı:** Para 2 haneye, **yarımı yukarı** (ROUND_HALF_UP) yuvarlanır; tek merkezi fonksiyon. KDV/kur çevriminde 1 kuruş tutarsızlıklarını önler.

### b) Görsel yükleme — otomatik küçültme
Amaç kalite değil **okunabilirlik.** Yüklenen her görsel kaydedilmeden önce:
- En uzun kenar **~1600 px**'e küçültülür (zaten küçükse dokunma).
- **JPEG/WebP, ~%80 kalite** ile yeniden kodlanır; EXIF/metadata temizlenir.
- Sonuç: 5–10 MB → birkaç yüz KB. Orijinali saklamak gerekmez.
- (Opsiyonel) liste görünümü için küçük bir thumbnail üret.
- Kütüphane: Pillow.

### c) Metin: büyük harf normalizasyonu (TR) — KRİTİK
Sayı kuralının kardeşi. Standart `upper()`/`toUpperCase()` Türkçe i'yi bozar.

- **Kullanıcının girdiği tüm metin alanları TR büyük harfe çevrilip öyle saklanır** (giriş kenarında normalize; kanonik değer büyük harf). Böylece rapor/arama/dışa aktarımda karışıklık olmaz.
- **TR kuralı:** i→İ, ı→I. **Tek merkezi fonksiyon + zorunlu testler:** "istanbul"→"İSTANBUL", "ışık"→"IŞIK", "iğne"→"İĞNE", "çiçek"→"ÇİÇEK".
- **Asla büyük harfe çevrilmeyen alanlar:**
  - **Şifre/parola** — büyük-küçük duyarlı, dokunulmaz (yoksa giriş bozulur).
  - **E-posta** — çevrilmez; tercihen **küçük** harfe normalize edilir.
  - **Web/URL** alanları.
  - **Sistem/dış kimlikler:** API anahtarı, token, dosya yolu, büyük-küçük duyarlı dış referans/seri no.
- **Arayüz statik metni ≠ veri.** Etiket/menü/başlık büyük görünümü, veriye dokunmadan **CSS `text-transform: uppercase` + sayfada `lang="tr"`** ile yapılır (geri alınabilir). Veri normalizasyonu yalnızca kullanıcı girdisi içindir.

### d) Muhasebe dönemi kilidi
Manuel ay sonu kapanışı yapılan dönem **kilitlenir**; açıkça yeniden açılmadan içine kayıt girilemez/değiştirilemez. Geçmiş bir dönemi kazara bozmayı ve raporların oynamasını önler.

### e) Tarih disiplini
İki tarih en baştan ayrı tutulur: **muhasebe tarihi** (işlemin değer tarihi, kullanıcı seçer — fiş tarihi) ve **kayıt zamanı** (created_at, sistem koyar). Tüm zaman damgaları **UTC saklanır, TR saatiyle gösterilir.** Dönemsel raporun doğruluğu buna dayanır.

### f) Numaralandırma
**İnsana görünen fiş/belge numarası** dönem içinde **boşluksuz ve müteselsil** (denetim/yasal beklenti). **Veritabanı iç kimliği (PK)** ise ayrı, teknik bir alan. İkisi karıştırılmaz.

### g) Audit + soft delete (şimdiden)
Çok kullanıcı v0.1'de olmasa bile her tabloda **created_by/at + updated_by/at** tutulur. Hiçbir kayıt fiziksel silinmez; **soft delete** (iptal/pasif). Sonradan eklemek çok acılıdır. (Eski sistemde bu DNA zaten vardı — taşı.)

### h) Kodlama ve sıralama
Veritabanı **UTF-8**; **Türkçe collation** ile kurulur ki ç/ğ/ı/ö/ş/ü doğru sıralansın.

### i) Responsive (mobil uyumlu) tasarım — en baştan
Uygulama hem masaüstü hem **telefon tarayıcısında** düzgün çalışmalı. Ayrı bir mobil uygulama DEĞİL; aynı web arayüzü ekran boyutuna göre uyarlanır.
- `<meta name="viewport" content="width=device-width, initial-scale=1">` her sayfada.
- Mobil öncelikli (mobile-first) CSS; dokunmaya uygun buton/giriş boyutları.
- **Geniş tablolar (yevmiye, mizan, bilanço)** dar ekranda: ya yatay kaydırılabilir kapsayıcı, ya da satırların "kart" görünümüne dönüşmesi.
- Bu kural **mevcut ekranlar dahil** tüm ekranlara uygulanır (fiş giriş, mizan, bilanço/gelir tablosu).
- Test: ~375px genişlikte taşma/kırpma olmadan kullanılabilir olmalı.

---

## 1. v0.1'in tanımı

**Amaç:** TDHP + 7/A'ya hazır hesap planı üstünde, manuel fişlerle çalışan, **canlı bilanço ve gelir tablosu** üreten muhasebe omurgası.

### İÇERİDE (v0.1)
1. **Hesap planı** (TDHP, 7/A yapısı tam seed'lenir)
2. **Yevmiye fişi** girme (manuel; borç=alacak zorunlu)
3. **Canlı mizan**
4. **Canlı bilanço + gelir tablosu** (mizandan, 6'lı hesaplardan türetilir)
5. **Fişte TCMB USD alış kuru** alanı + **USD gelir tablosu** (tarihi kurla otomatik)

### DIŞARIDA (yol haritasında — v0.1'de YOK)
- Stok modülü + otomatik yevmiye (v0.2)
- Cari + satınalma/satış + KDV (v0.3)
- Ürün ağacı + üretim maliyeti / 710-720-730 → 151 → 152 → 620 otomatik akışı (v0.4)
- Fire / kesim — boru lazer (v0.5)
- e-belge için veri ihracı (v0.6)
- USD bilanço (parasal kalemler kapanış kuruyla revalüasyonlu) — biraz sonra, ama veri şimdiden hazır
- TCMB kuru otomatik çekme / EVDS entegrasyonu (önce manuel giriş)
- Raporlama detayı, yetki, çoklu depo (v0.7+)

> Not: Rapor yalnızca 6'lıdan türer. Ay içinde üretim maliyetleri 7'lilerde beklediğinden gelir tablosu maliyet tarafı **ay sonu manuel kapanışa kadar eksik** görünür; kapanış fişlerinden sonra tamdır. Bu, aylık kapanış yapan bir işletme için normaldir.

---

## 2. Çekirdek veri modeli (4 tablo)

### Tablo: HESAP_PLANI
| Alan | Tip | Not |
|---|---|---|
| hesap_kodu | metin (PK) | TDHP kodu |
| hesap_adi | metin | |
| rapor_grubu | metin | BILANCO / GELIR_TABLOSU / MALIYET(7xx) |
| rapor_kalemi | metin | Gelir tablosu/bilanço satır kodu (aşağıdaki harita) |
| parasal | evet/hayır | USD bilanço için. Parasal (kasa, banka, alıcı, satıcı, kredi) → kapanış kuru; parasal değil (stok, demirbaş, sermaye) → tarihi kur |
| aktif | evet/hayır | |

### Tablo: YEVMIYE_FISI (başlık)
| Alan | Tip | Not |
|---|---|---|
| fis_no | sayı (PK) | Otomatik artan |
| tarih | tarih | |
| aciklama | metin | |
| kaynak | metin | v0.1'de MANUEL |
| kur_usd | sayı | O günün TCMB USD alış kuru (USD raporlama görünümü için). KUR tablosundan; tatil/hafta sonu → son yayımlanan kur |

### Tablo: YEVMIYE_SATIR (satırlar)
| Alan | Tip | Not |
|---|---|---|
| satir_id | sayı (PK) | |
| fis_no | sayı (FK → YEVMIYE_FISI) | |
| hesap_kodu | metin (FK → HESAP_PLANI) | |
| borc | sayı (Decimal) | **Her zaman TL** (fonksiyonel) |
| alacak | sayı (Decimal) | **Her zaman TL** (fonksiyonel) |
| islem_pb | metin | TRY / USD / EUR / GBP |
| islem_tutari | sayı (Decimal) | Orijinal para birimindeki tutar (örn. 1.000 EUR) |
| islem_kuru | sayı (Decimal) | islem_pb'nin o günkü TL kuru. TRY ise 1. `TL = islem_tutari × islem_kuru` |
| aciklama | metin | |

> TRY işlemde: islem_pb=TRY, islem_kuru=1, borc/alacak = islem_tutari. Yabancı işlemde TL değeri kurla hesaplanır. Bu yapı yabancı para bakiyelerini ve ileride kur farkını (646/656) görmeyi sağlar.

### Tablo: KUR (günlük TCMB alış)
| Alan | Tip | Not |
|---|---|---|
| tarih | tarih (PK) | |
| usd_alis | sayı (Decimal) | TCMB USD alış (1 USD = X TL) |
| eur_alis | sayı (Decimal) | TCMB EUR alış (1 EUR = X TL) |
| gbp_alis | sayı (Decimal) | TCMB GBP alış (1 GBP = X TL) |

> Başta elle girilir; ileride otomatik çekilir. islem_kuru bu tablodan, kur_usd (USD raporlama) da usd_alis'ten gelir.

---

## 3. İki motor kuralı (Claude Code'a aynen söyle)

**a) Dengeli fiş zorunlu.** Satırlarda `SUM(borc) = SUM(alacak)` değilse fiş kaydedilmez.

**b) Bakiyeleri HESAPLA, saklama.** Hesap bakiyesi, mizan, bilanço, gelir tablosu — hepsi her zaman `YEVMIYE_SATIR`dan hesaplanır. Tek doğruluk kaynağı yevmiye satırlarıdır.

---

## 4. Gelir tablosu türetme haritası (7/A)

> Gelir tablosu **yalnızca 6'lı hesaplardan** kurulur. 7'liler ara duraktır, sonuçları yansıtmayla 15x ve 6'lılara akar.

| Gelir Tablosu Kalemi | Hesaplar | İşaret |
|---|---|---|
| A. Brüt Satışlar | 600, 601, 602 | + |
| B. Satış İndirimleri | 610, 611, 612 | − |
| **= Net Satışlar** | A − B | |
| C. Satışların Maliyeti | 620, 621, 622, 623 | − |
| **= BRÜT SATIŞ KÂRI** | | |
| D. Faaliyet Giderleri | 630, 631, 632 | − |
| **= FAALİYET KÂRI** | | |
| E. Diğer Olağan Gelir/Kâr | 640, 642, 645, 646, 647, 649 | + |
| F. Diğer Olağan Gider/Zarar | 653, 654, 655, 656, 657, 659 | − |
| G. Finansman Giderleri | 660, 661 | − |
| **= OLAĞAN KÂR** | | |
| H. Olağandışı Gelir/Kâr | 671, 679 | + |
| I. Olağandışı Gider/Zarar | 680, 681, 689 | − |
| **= DÖNEM KÂRI** | | |
| J. Vergi Karşılığı | 691 | − |
| **= DÖNEM NET KÂRI** | | |

## 4b. Ay sonu manuel kapanış (yansıtma) şablonu

> Bu fişleri **ay sonunda sen elle atarsın.** Yazılım otomatik atmaz; bu tablo senin kapanış checklist'indir.

| Toplayan (7xx) | Yansıtma | Gider yeri |
|---|---|---|
| 710 Direkt İlk Madde | 711 | → 151 Yarı Mamul |
| 720 Direkt İşçilik | 721 | → 151 |
| 730 Genel Üretim Gideri | 731 | → 151 |
| 151 Yarı Mamul | — | → 152 Mamul (üretim bitince) |
| 152 Mamul | — | → 620 SMM (satışta) |
| 740 Hizmet Maliyeti | 741 | → 622 |
| 750 Ar-Ge | 751 | → 630 |
| 760 Pazarlama-Satış | 761 | → 631 |
| 770 Genel Yönetim | 771 | → 632 |
| 780 Finansman | 781 | → 660/661 |

> **Kural:** Kapanış sonrası 7xx + 7x1 = 0 olmalı. Bu dengeyi ay sonunda sen sağlarsın.

---

## 4c. USD katmanı (çeviri mantığı)

> TL defter saf kalır; USD bunların üstünde **hesaplanan** bir gösterimdir. Hiçbir USD için ayrı kayıt/fiş tutulmaz.

**USD gelir tablosu (ve tüm akışlar):** her satır, ait olduğu fişin **tarihi kuruyla** çevrilir → `USD = TL ÷ fiş.kur_usd`. Enflasyondan arınmış gerçek USD performansını verir. (v0.1'de hazır.)

**USD bilanço:**
- **Parasal** kalemler (kasa, banka, alıcı, satıcı, kredi) → **kapanış (dönem sonu) kuruyla** değerlenir.
- **Parasal olmayan** kalemler (stok, demirbaş, sermaye) → **tarihi kurla** kalır.
- Aradaki fark = **kur çevrim farkı** (TL tutmaktan doğan gerçek USD kâr/zararı). Enflasyonda görmek istediğin asıl rakam budur.

> Örnek: Ocak'ta 100.000 TL kasa, kur 30 → 3.333 USD. Mart'ta kur 40 → kapanış değeri 2.500 USD. Aradaki **833 USD kayıp** kur çevrim farkıdır.

**Kur kuralı:** TCMB USD alış. Hafta sonu/tatilde kur yok → en son yayımlanan kuru kullan.

**Karar (kesin):** USD bilançoda parasal kalemler **kapanış kuruyla** değerlenir (erimeyi gösterir).

**Değerleme tarihi = rapor tarihi.** Günlük/sürekli revalüasyon YOK; değerleme rapor açıldığında o tarihin kuruyla **tek seferlik anlık** hesaplanır, saklanmaz ("bakiyeleri hesapla, sakla­ma" kuralının doğal sonucu).
- **Varsayılan rapor tarihi = bugün.** Kullanıcı isterse tarih seçer (örn. ay sonu) ve geçmişe de bakabilir.
- Seçilen tarihte kur yoksa (hafta sonu/tatil) → en son yayımlanan TCMB USD alış kuru.

---

## 5. v0.1 "bitti" tanımı (buraya gelene kadar v0.2 yok)

- [ ] TDHP + 7/A yapısı seed'lendi
- [ ] **Sayı parser/formatter testleri geçiyor** (10,35 · 1.035,00 · -1.234.567,89 vb.) — istisnasız tek fonksiyondan geçiyor
- [ ] **TR büyük harf testleri geçiyor** (istanbul→İSTANBUL, ışık→IŞIK); şifre/e-posta/URL hariç tutuluyor
- [ ] **Görsel yüklemede otomatik küçültme** çalışıyor (5–10 MB → birkaç yüz KB)
- [ ] Açılış bakiyelerini fiş olarak girdim
- [ ] Gerçek işlemleri manuel fiş olarak işliyorum
- [ ] Mizan, bilanço ve gelir tablosu anlık ve doğru
- [ ] Her fişe TCMB kuru (USD/EUR/GBP) giriliyor; USD gelir tablosu doğru
- [ ] En az 1–2 hafta fiilen kullandım

---

## 6. Yol haritası (hiçbir şey atılmadı, sadece sıralandı)

- **v0.2:** Stok modülü + stok hareketi → otomatik yevmiye
- **v0.3:** Cari (120/320) + satınalma/satış → otomatik fiş + KDV (191/391)
- **v0.4:** Ürün ağacı + üretim kaydı (operasyonel). Maliyet **yansıtması ay sonu manuel kalır** — yazılım sadece veriyi toplar, kapanışı sen yaparsın
- **v0.5:** Fire / kesim (boru lazer)
- **v0.6:** e-belge / e-defter için veri ihracı (resmi belge üretmez)
- **v0.7+:** Raporlama detayı, yetki, çoklu depo

Her sürüm, bir öncekinin "bitti" kutuları dolmadan başlamaz.

---

## Yol haritası notu (2026-05-30)
- **Görsel otomatik küçültme (0b-b):** v0.1'de görsel yükleme yapılan bir yer yok; bu kural, görsel ekleyen İLK modülle (örn. fişe belge/foto ekleme) birlikte uygulanacak. Kapsam genişletilmedi. (v0.2+)
- **Alt hesap / muavin (v0.3, cari modülüyle):** `HesapPlani`'ya nullable self-FK `ust_hesap` eklenecek; kayıt yalnızca **yaprak hesaba** atılacak (ana hesap bakiyesi = alt hesaplar toplamı); **gelir tablosu raporları ana hesaba yuvarlanacak** (gelir tablosu birebir 3 haneli kod listesi kullanıyor — tek kırılgan nokta); **mizanda ana hesap / muavin görünümü**. Model bugün bilinçli olarak düz bırakıldı: sonradan eklemeli (additive) migration temiz, backfill gerekmez; PK `hesap_kodu` metin + max_length=20 olduğundan `120.001` gibi kodlar zaten sığar. (Değerlendirme: 2026-05-30)
