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
        Ekran("mizan", "Mizan", "core:mizan"),
        Ekran("bilanco", "Bilanço", "core:bilanco"),
        Ekran("gelir_tablosu", "Gelir Tablosu", "core:gelir_tablosu"),
        Ekran("mizan_usd", "Mizan (USD)", "core:mizan_usd"),
        Ekran("bilanco_usd", "Bilanço (USD)", "core:bilanco_usd"),
        Ekran("gelir_tablosu_usd", "Gelir Tablosu (USD)", "core:gelir_tablosu_usd"),
    )),
    Modul("AYARLAR", "Ayarlar", (
        Ekran("kullanicilar", "Kullanıcılar", "core:kullanici_listesi"),
        Ekran("kullanici_yetkileri", "Kullanıcı Yetkileri", "core:kullanici_yetkileri"),
    ), yonetici_modulu=True),
)


def menu_moduller(yonetici: bool):
    """Kullanıcıya göre görünür modüller (yönetici modülleri yalnızca yöneticiye)."""
    return [m for m in MODULLER if not m.yonetici_modulu or yonetici]
