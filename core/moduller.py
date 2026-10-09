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
    ana_url_adi: str = ""           # modül adına tıklayınca açılan özet/dashboard sayfası (boşsa yalnız menüyü açar)


MODULLER = (
    Modul("MUHASEBE", "Muhasebe", (
        Ekran("fis_listesi", "Yevmiye Fişleri", "core:fis_listesi"),
        Ekran("kurlar", "Kurlar", "core:kurlar"),
        Ekran("kur_degerleme", "Kur Değerleme", "core:kur_degerleme"),
        Ekran("donemsel_dagitim", "Dönemsel Dağıtım (180)", "core:donemsel_dagitim"),
        Ekran("kdv_mahsup", "KDV Dönem Mahsubu", "core:kdv_mahsup_listesi"),
        Ekran("hesap_plani", "Hesap Planı", "core:hesap_plani"),
        Ekran("yatirim_projeleri", "Yatırım Projeleri", "core:yatirim_projeleri"),
        Ekran("duran_varliklar", "Duran Varlıklar", "core:duran_varliklar"),
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
        Ekran("satis_proformalari", "Satış Proformaları", "core:satis_proformalari"),
        Ekran("satis_siparisleri", "Satış Siparişleri", "core:satis_siparisleri"),
    )),
    Modul("STOKLAR", "Stoklar", (
        Ekran("stoklar", "Stoklar", "core:stoklar"),
        Ekran("kategoriler", "Kategoriler", "core:kategoriler"),
        Ekran("fatura_tipleri", "Fatura Tipleri", "core:fatura_tipleri"),
        Ekran("birimler", "Birimler", "core:birimler"),
        Ekran("depolar", "Depolar", "core:depolar"),
    )),
    Modul("CRM", "CRM", (
        Ekran("aday_musteriler", "Aday Müşteriler", "core:aday_musteriler"),
        Ekran("aday_kategoriler", "Kaynaklar", "core:aday_kategoriler"),
        Ekran("aday_tipleri", "Tipler", "core:aday_tipleri"),
        Ekran("aday_potansiyelleri", "Potansiyeller", "core:aday_potansiyelleri"),
        Ekran("aday_asamalari", "Aşamalar", "core:aday_asamalari"),
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
    ), ana_url_adi="core:finans_ozet"),
    Modul("IK", "İnsan Kaynakları", (
        Ekran("personel", "Personel Kartları", "core:personeller"),
        Ekran("personel_izinleri", "İzinler", "core:izinler"),
        Ekran("personel_belgeleri", "Özlük Belgeleri", "core:belgeler"),
        Ekran("personel_devam", "Devam / Yoklama", "core:yoklama"),
        Ekran("resmi_tatil", "Resmî Tatiller", "core:resmi_tatiller"),
        Ekran("mesai_kayitlari", "Mesai Kayıtları", "core:mesai_kayitlari"),
        Ekran("personel_ucret", "Personel Ücretleri", "core:personeller"),
        Ekran("bordro", "Aylık Bordro", "core:bordro_listesi"),
        Ekran("yemek_takibi", "Yemek Takibi", "core:yemek_takibi"),
    )),
    Modul("FASON", "Fason", (
        Ekran("fason_hesapla", "Kesim Listesi Hesapla", "core:fason_hesapla"),
        Ekran("fason_kayitlari", "Kesim Kayıtları", "core:fason_kayitlari"),
        Ekran("fason_kesim_tanimlari", "Kesim Tanımları", "core:fason_kesim_tanimlari"),
        Ekran("fason_donusleri", "Fason Dönüşler", "core:fason_donusleri"),
        Ekran("fason_fiyatlari", "Fason Fiyatları", "core:fason_fiyatlari"),
    )),
    Modul("URETIM", "Üretim", (
        Ekran("uretim_emirleri", "Üretim Emirleri", "core:uretim_emirleri"),
        Ekran("ihtiyac_hesapla", "İhtiyaç Hesapla", "core:ihtiyac_hesapla"),
        Ekran("urun_agaci", "Ürün Ağacı", "core:urun_agaci"),
        Ekran("operasyon_kayitlari", "Operasyon Kayıtları", "core:operasyon_kayitlari"),
        Ekran("operasyon_tanimlari", "Operasyon Tanımları", "core:operasyon_tanimlari"),
        Ekran("is_istasyonlari", "İş İstasyonları", "core:is_istasyonlari"),
    )),
    Modul("AYARLAR", "Ayarlar", (
        Ekran("kullanicilar", "Kullanıcılar", "core:kullanici_listesi"),
        Ekran("kullanici_yetkileri", "Kullanıcı Yetkileri", "core:kullanici_yetkileri"),
        Ekran("firma_bilgileri", "Firma Bilgileri", "core:firma_bilgileri"),
        Ekran("tanim_listeleri", "Tanım Listeleri", "core:tanim_listeleri"),
        Ekran("yedek", "Yedek", "core:yedek"),
        Ekran("mesai_ayarlari", "Mesai Ayarları", "core:mesai_ayarlari"),
    ), yonetici_modulu=True),
)


def menu_moduller(yonetici: bool):
    """Kullanıcıya göre görünür modüller (yönetici modülleri yalnızca yöneticiye)."""
    return [m for m in MODULLER if not m.yonetici_modulu or yonetici]
