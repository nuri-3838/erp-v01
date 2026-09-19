# CLAUDE.md — ERP v0.1 (Alüminyum Merdiven İmalatı)

Bu dosya her oturumda otomatik okunur. Aşağıdaki kurallar **bağlayıcıdır.**

## Bu proje nedir
Muhasebe çekirdeği (yevmiye motoru + TDHP/7-A hesap planı + manuel fiş + canlı mizan/bilanço/gelir tablosu + USD raporlama) üstüne, `docs/ERP_v0.1_kapsam.md`'nin kendi yol haritasında (§6) öngördüğü sırayla inşa edilen bir ERP. Yığın: **Django + PostgreSQL** (UTF-8 + Türkçe collation).

**Şu an neredeyiz (2026-09-15):** v0.1 (muhasebe çekirdeği), v0.2 (Stok) ve v0.3 (Cari + satış/satınalma + KDV) tamamlandı ve kapsam bunların ötesine de genişledi — Faturalar, Finans (Kasa/Banka/Kredi/Kredi Kartı/Çek-Senet), Teklif&Sipariş&İrsaliye, bağımsız Satış Teklifi, Yemek Takibi, FASON (kesim tanımları + kayıtları) canlıda çalışıyor. Ayrıntı ve kalıcı desenler için memory'deki "İnşa durumu" kaydına bak. **v0.4 (üretim kaydı) İş İstasyonu + Operasyon (rota) modeliyle teslim edildi** (bkz. kapsam dosyası §6) — düz "Ürün Ağacı" tasarımı 2026-09-16'da kullanıcı tarafından reddedilip kaldırıldı (`e3e9053`), geri getirme; ÜRETİM > Ürün Ağacı yalnız `ihtiyac_hesapla()` üzerinden çizilen salt-okunur bir görünümdür (yeni tablo yok). Maliyet yansıtması yine ay sonu manuel kalır, yazılım yalnız operasyonel veriyi (hangi bileşen ne kadar tüketildi, hangi mamul ne kadar üretildi) toplar.

## Tek doğruluk kaynağı
- `docs/ERP_v0.1_kapsam.md` — mimari, veri modeli, temel kurallar, "bitti" tanımı, yol haritası.
- `docs/hesap_plani_seed.csv` — hesap planı seed'i.
Çelişki olursa **spec geçerlidir.** Bu dosya sadece özet + disiplindir.

## ALTIN KURAL — kapsam disiplini (en kritik)
- Her zaman **o an üstünde çalışılan konunun dışına** çıkma — "şunu da ekleyelim / es geçmeyelim" deme, her ek **koda değil, yol haritasına not** olur.
- Aynı anda birden çok iş yapma. **En ince uçtan uca dilim** önce (local test → deploy → doğrula, sonra sıradaki dilim).
- Yeni bir modül/büyük özellik, kullanıcının o oturumda açıkça istediği veya `docs/ERP_v0.1_kapsam.md` §6 yol haritasında zaten planlı olan şeyin ötesine geçmemeli.
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

## İnşa sırası (v0.1 — tamamlandı, tarihsel kayıt)
1. İskelet + PostgreSQL (UTF-8/TR) + temel kurallar modülü + testleri.
2. HESAP_PLANI + CSV seed.
3. YEVMIYE_FISI + YEVMIYE_SATIR + KUR + dengeli fiş kuralı.
4. Manuel fiş giriş ekranı (kur alanı dahil).
5. Mizan → bilanço + gelir tablosu (6'lı türeme haritası) → USD görünümü.

Sıradaki adımlar için `docs/ERP_v0.1_kapsam.md` §6 (Yol haritası) geçerli.

## ŞİMDİ YAPMA (kesin kapsam dışı — kapsam dosyasının kendi kararı)
e-Fatura / e-Arşiv / e-İrsaliye / e-Defter / beyanname üretimi. Bu ERP resmi/sertifikalı kanala hiç girmez, yalnız iç yönetim + muhasebe sistemidir (bkz. kapsam dosyası §0). Otomatik ay sonu yansıtma (7xx→6xx) da bilinçli olarak hep manuel kalacak.

## Dokunma
Eski `semta_erp` projesiyle ilgisi yok. Buraya hiçbir şey kopyalama; o sadece ayrı bir referans.

## Kapsam genişletme notu (bilinçli, 2026-05-30)
Giriş + kullanıcı bazlı ekran yetkisi v0.1'e **bilinçli** eklendi (normalde v0.7). Gerekçe: sistem ekipçe kullanılacak. Mimari: modül (MUHASEBE) → ekran (7 rapor/giriş ekranı) → **kullanıcı bazlı** erişim (sabit rol yok). Şimdilik yalnızca erişim düzeyi (görür/göremez); "görür ama değiştiremez" ileride. Yeni modüller (Stok/Cari/Üretim) aynı mantıkla eklenecek.
