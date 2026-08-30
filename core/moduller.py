"""Modül → ekran yapısı (tek doğruluk kaynağı).

Menü bu yapıdan üretilir; kullanıcı bazlı EKRAN yetkisi (Adım 3) de bu ``kod``ların
üstüne oturacak. Yeni modül/ekran eklemek için yalnızca buraya eklemek yeterli.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Ekran:
    kod: str       # benzersiz anahtar (yetki sisteminin kullanacağı)
    ad: str        # menüde görünen ad
    url_adi: str   # Django URL adı (namespace dahil)


@dataclass(frozen=True)
class Modul:
    kod: str
    ad: str
    ekranlar: tuple
    yonetici_modulu: bool = False   # True ise yalnızca yöneticiye görünür


MODULLER = (
    Modul("MUHASEBE", "Muhasebe", (
        Ekran("fis_listesi", "Yevmiye Fişleri", "core:fis_listesi"),
        Ekran("kurlar", "Kurlar", "core:kurlar"),
        Ekran("hesap_plani", "Hesap Planı", "core:hesap_plani"),
        Ekran("mizan", "Mizan", "core:mizan"),
        Ekran("bilanco", "Bilanço", "core:bilanco"),
        Ekran("gelir_tablosu", "Gelir Tablosu", "core:gelir_tablosu"),
        Ekran("mizan_usd", "Mizan (USD)", "core:mizan_usd"),
        Ekran("bilanco_usd", "Bilanço (USD)", "core:bilanco_usd"),
        Ekran("gelir_tablosu_usd", "Gelir Tablosu (USD)", "core:gelir_tablosu_usd"),
    )),
    Modul("FATURALAR", "Faturalar", (
        Ekran("alis_faturalari", "Alış Faturaları", "core:alis_faturalari"),
        Ekran("satis_faturalari", "Satış Faturaları", "core:satis_faturalari"),
    )),
    Modul("SATINALMA", "Satınalma", (
        Ekran("satinalma_teklifleri", "Satınalma Teklifleri", "core:satinalma_teklifleri"),
        Ekran("satinalma_siparisleri", "Satınalma Siparişleri", "core:satinalma_siparisleri"),
        Ekran("satinalma_irsaliyeleri", "Satınalma İrsaliyeleri", "core:satinalma_irsaliyeleri"),
    )),
    Modul("SATIS", "Satış", (
        Ekran("satis_teklifleri", "Satış Teklifleri", "core:satis_teklifleri"),
        Ekran("satis_siparisleri", "Satış Siparişleri", "core:satis_siparisleri"),
    )),
    Modul("STOKLAR", "Stoklar", (
        Ekran("stoklar", "Stoklar", "core:stoklar"),
        Ekran("kategoriler", "Kategoriler", "core:kategoriler"),
        Ekran("fatura_tipleri", "Fatura Tipleri", "core:fatura_tipleri"),
        Ekran("birimler", "Birimler", "core:birimler"),
        Ekran("depolar", "Depolar", "core:depolar"),
    )),
    Modul("CARILER", "Cariler", (
        Ekran("cariler", "Cariler", "core:cariler"),
        Ekran("cari_kategoriler", "Cari Kategorileri", "core:cari_kategoriler"),
        Ekran("lokasyonlar", "Ülke / Şehir", "core:lokasyonlar"),
    )),
    Modul("FINANS", "Finans", (
        Ekran("kasa", "Kasa", "core:kasalar"),
        Ekran("banka", "Banka", "core:bankalar"),
        Ekran("kredi_karti", "Kredi Kartı", "core:kredi_kartlari"),
        Ekran("kredi", "Kredi", "core:krediler"),
        Ekran("cek_senet", "Çek-Senet", "core:cek_senetler"),
    )),
    Modul("AYARLAR", "Ayarlar", (
        Ekran("kullanicilar", "Kullanıcılar", "core:kullanici_listesi"),
        Ekran("kullanici_yetkileri", "Kullanıcı Yetkileri", "core:kullanici_yetkileri"),
        Ekran("tanim_listeleri", "Tanım Listeleri", "core:tanim_listeleri"),
        Ekran("yedek", "Yedek", "core:yedek"),
    ), yonetici_modulu=True),
)


def menu_moduller(yonetici: bool):
    """Kullanıcıya göre görünür modüller (yönetici modülleri yalnızca yöneticiye)."""
    return [m for m in MODULLER if not m.yonetici_modulu or yonetici]
