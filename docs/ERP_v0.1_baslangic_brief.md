# ERP v0.1 — Claude Code Başlangıç Brief'i

> Bu belge **yönlendirme** içindir. Detayların **tek doğruluk kaynağı** ekli iki dosyadır:
> 1. `ERP_v0.1_kapsam.md` — mimari, veri modeli, temel kurallar, "bitti" tanımı, yol haritası
> 2. `hesap_plani_seed.csv` — TDHP + 7/A hesap planı seed'i
>
> Code: önce bu iki dosyayı oku, sonra aşağıdaki sırayı izle. Spec ile bu brief çelişirse **spec geçerlidir**.

---

## 0. Bu oturumun amacı
Sıfırdan, temiz bir **muhasebe çekirdeği (v0.1)** kur. Eski sistemle (`semta_erp`) ilgisi yok; ona DOKUNMA — o ayrı bir ortamda, sadece referans olarak duruyor. Yeni, **boş bir repo**da çalış.

## 1. ALTIN KURAL — kapsam disiplini (bu projenin geçmişte battığı yer)
- v0.1 kapsamı **DONDURULMUŞTUR.** Spec'teki "İÇERİDE" listesi dışına **çıkma.**
- "Şunu da ekleyelim / es geçmeyelim" deme. Aklına gelen her ek, koda değil **yol haritasına not** olarak gider.
- Her şeyi aynı anda yapma. **En ince uçtan uca dilim** önce.
- Spec'teki **"bitti" tanımı** dolmadan bir sonraki konuya/sürüme geçme.
- Kararsız kaldığında: kapsamı **genişletme**, sor.

## 2. Ne kuruluyor (özet — detay spec'te)
Çift taraflı **yevmiye motoru** + TDHP/**7-A** hesap planı + **manuel fiş** girişi + **canlı mizan / bilanço / gelir tablosu** + **USD raporlama** görünümü. Stok/cari/üretim/e-belge YOK (yol haritasında).

## 3. Teknik
- **Önerilen yığın:** Django + PostgreSQL (önceki deneyiminle uyumlu). 
- Yeni, boş repo. PostgreSQL **UTF-8 + Türkçe collation** ile kurulur.

## 4. ÖNCE temel kuralları kur (spec bölüm 0b) — invariant'lar
Bunlar **model/servis katmanında** zorlanır, UI'ya bırakılmaz. İlk iş bunlar + testleri:
- **Sayı/para:** tek parser + tek formatter, DECIMAL, ROUND_HALF_UP. Test: `10,35` `1.035,00` `-1.234.567,89` …
- **TR büyük harf:** tek fonksiyon (i→İ, ı→I). Test: istanbul→İSTANBUL, ışık→IŞIK. İstisna: şifre, e-posta, URL, sistem kimlikleri.
- **Görsel yükleme:** en uzun kenar ~1600px, ~%80 JPEG/WebP, EXIF temizle.
- **Dönem kilidi · tarih disiplini (UTC sakla/TR göster, muhasebe tarihi ayrı) · müteselsil no + ayrı PK · audit (created/updated by/at) + soft delete.**

## 5. Veri modeli (spec bölüm 2)
`HESAP_PLANI`, `YEVMIYE_FISI`, `YEVMIYE_SATIR`, `KUR`. Hesap planını **ekli CSV'den seed'le.**

## 6. Motor kuralları (spec bölüm 3) — pazarlık yok
- **Dengeli fiş zorunlu:** `SUM(borc) = SUM(alacak)` değilse fiş kaydedilmez.
- **Bakiyeleri HESAPLA, saklama:** mizan/bilanço/gelir tablosu her zaman yevmiye satırlarından hesaplanır; hiçbir yerde "mevcut bakiye" alanı tutulmaz.

## 7. İnşa sırası (her adım bitince göster, onay al, sonra ilerle)
1. Proje iskeleti + PostgreSQL (UTF-8/TR) + **temel kurallar modülü + testleri** (Bölüm 4).
2. `HESAP_PLANI` modeli + CSV seed.
3. `YEVMIYE_FISI` + `YEVMIYE_SATIR` + `KUR` modelleri + dengeli fiş kuralı.
4. **Manuel fiş giriş ekranı** (kur alanı dahil).
5. **Mizan** → **bilanço + gelir tablosu** (spec'teki 6'lı türeme haritası) → **USD görünümü**.

## 8. ŞİMDİ YAPILMAYACAKLAR (kapsam dışı — eklersen kapsamı bozarsın)
Stok · cari · satınalma/satış · üretim · ürün ağacı · **otomatik yansıtma** (ay sonu manuel) · e-Fatura/e-Arşiv/e-İrsaliye/e-Defter/beyanname · çoklu kullanıcı yetki ekranları · fire/kesim. Hepsi yol haritasında; sırası gelince.

## 9. Bitti kontrolü
Spec bölüm 5'teki "bitti" kutuları dolduğunda v0.1 tamamdır. O ana kadar v0.2 yok.
