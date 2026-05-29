# CLAUDE.md — ERP v0.1 (Alüminyum Merdiven İmalatı)

Bu dosya her oturumda otomatik okunur. Aşağıdaki kurallar **bağlayıcıdır.**

## Bu proje nedir
Sıfırdan kurulan bir **muhasebe çekirdeği (v0.1):** çift taraflı yevmiye motoru + TDHP/7-A hesap planı + manuel fiş + canlı mizan/bilanço/gelir tablosu + USD raporlama. Yığın: **Django + PostgreSQL** (UTF-8 + Türkçe collation).

## Tek doğruluk kaynağı
- `docs/ERP_v0.1_kapsam.md` — mimari, veri modeli, temel kurallar, "bitti" tanımı, yol haritası.
- `docs/hesap_plani_seed.csv` — hesap planı seed'i.
Çelişki olursa **spec geçerlidir.** Bu dosya sadece özet + disiplindir.

## ALTIN KURAL — kapsam disiplini (en kritik)
- v0.1 kapsamı **DONDURULMUŞ.** Spec'teki "İÇERİDE" listesi dışına **çıkma.**
- "Şunu da ekleyelim / es geçmeyelim" deme. Her ek **koda değil, yol haritasına not** olur.
- Aynı anda birden çok iş yapma. **En ince uçtan uca dilim** önce.
- Spec'teki **"bitti" tanımı** dolmadan bir sonraki konuya geçme.
- Kararsız kalınca kapsamı **genişletme → DUR ve sor.**

## Çiğnenmeyecek invariant'lar (model/servis katmanında zorlanır, UI'a güvenilmez)
- **Sayı/para:** tek parser + tek formatter, DECIMAL (asla float), ROUND_HALF_UP. TR: nokta=binlik, virgül=ondalık. Testler zorunlu.
- **TR büyük harf:** tek fonksiyon, i→İ ı→I. İstisna: şifre, e-posta, URL, sistem kimlikleri. Testler zorunlu.
- **Dengeli fiş:** SUM(borc)=SUM(alacak) değilse fiş kaydedilmez.
- **Bakiyeler HESAPLANIR, saklanmaz:** mizan/bilanço/gelir tablosu her zaman yevmiye satırlarından; "mevcut bakiye" alanı tutulmaz.
- **Para birimi:** TL fonksiyonel; USD raporlama; EUR/GBP işlem para birimi. Her fişe TCMB kuru.
- **Tarih:** muhasebe tarihi (kullanıcı) ≠ kayıt zamanı (sistem). UTC sakla, TR göster.
- **Numara:** insana görünen fiş no boşluksuz/müteselsil; iç PK ayrı.
- **Audit + soft delete:** her tabloda created/updated by/at; fiziksel silme yok.
- **Görsel:** yüklemede en uzun kenar ~1600px, ~%80 JPEG/WebP.

## İnşa sırası (her adım bitince GÖSTER, onay al, sonra ilerle)
1. İskelet + PostgreSQL (UTF-8/TR) + temel kurallar modülü + testleri.
2. HESAP_PLANI + CSV seed.
3. YEVMIYE_FISI + YEVMIYE_SATIR + KUR + dengeli fiş kuralı.
4. Manuel fiş giriş ekranı (kur alanı dahil).
5. Mizan → bilanço + gelir tablosu (6'lı türeme haritası) → USD görünümü.

## ŞİMDİ YAPMA (kapsam dışı — eklersen kapsamı bozarsın)
Stok · cari · satınalma/satış · üretim · ürün ağacı · otomatik yansıtma (ay sonu manuel) · e-Fatura/e-Arşiv/e-İrsaliye/e-Defter/beyanname · çoklu kullanıcı yetki ekranları · fire/kesim.

## Dokunma
Eski `semta_erp` projesiyle ilgisi yok. Buraya hiçbir şey kopyalama; o sadece ayrı bir referans.
