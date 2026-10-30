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
    yonetici: bool = False   # True ise (modül herkese açık olsa da) yalnızca yöneticiye görünür; görünüm de yonetici_gerekli ile korunur
    grup: str = ""           # 3. SEVİYE: doluysa ekran, modülün altında bu adlı açılır alt menünün içinde görünür (aynı ad = aynı alt menü; sırası ilk üyesinin yeri)
    ilgili: tuple = ()       # bu ekranın alt sayfalarının (ekle/düzenle/sil…) URL adları: bunlardan birindeyken ekran + alt menü + modül açık/aktif işaretlenir
    menude: bool = True      # False ise menüde GÖRÜNMEZ ama yetki kodu (Kullanıcı Yetkileri, ekran_gerekli) ve URL aynen çalışır (örn. personel kartı sekmesine taşınan ekranlar)

    @property
    def alt_menu(self) -> bool:
        return False

    def eslesir(self, view_adi: str) -> bool:
        return bool(view_adi) and (self.url_adi == view_adi or view_adi in self.ilgili)


@dataclass(frozen=True)
class MenuGrup:
    """Modül altında açılır alt menü (3. seviye). ``Modul.ogeler`` üretir; ``ekranlar`` YALNIZ yetkili üyelerdir (boş grup hiç üretilmez)."""
    kod: str       # "<MODÜL>:<ad>" — açık/aktif eşlemesi için benzersiz
    ad: str
    ekranlar: tuple

    @property
    def alt_menu(self) -> bool:
        return True


@dataclass(frozen=True)
class Modul:
    kod: str
    ad: str
    ekranlar: tuple
    yonetici_modulu: bool = False   # True ise yalnızca yöneticiye görünür
    ana_url_adi: str = ""           # modül adına tıklayınca açılan özet/dashboard sayfası (boşsa yalnız menüyü açar)

    @property
    def ogeler(self) -> list:
        """Menüde sırayla görünen öğeler: düz ``Ekran`` ya da ``MenuGrup`` (``Ekran.grup`` dolu olanlar tek grupta toplanır). ``ekranlar`` (düz liste) yetki/test
        kullanımı için aynen kalır; yetkiye göre süzülmüş ``ekranlar`` üzerinden hesaplandığından yetkisiz öğe ve boş grup görünmez."""
        ogeler, gruplar = [], {}
        for e in self.ekranlar:
            if not e.grup:
                ogeler.append(e)
            elif e.grup in gruplar:
                gruplar[e.grup].append(e)
            else:
                gruplar[e.grup] = [e]
                ogeler.append(e.grup)                      # yer tutucu; aşağıda MenuGrup'a çevrilir
        return [MenuGrup(f"{self.kod}:{o}", o, tuple(gruplar[o])) if isinstance(o, str) else o for o in ogeler]


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
        Ekran("kdv_listeleri", "KDV Listeleri", "core:kdv_listeleri", grup="Raporlar"),
    )),
    Modul("FATURALAR", "Faturalar ve İrsaliyeler", (
        Ekran("satis_faturalari", "Satış Faturaları", "core:satis_faturalari"),
        Ekran("alis_faturalari", "Alış Faturaları", "core:alis_faturalari"),
        Ekran("satis_irsaliyeleri", "Satış İrsaliyeleri", "core:satis_irsaliyeleri"),
        Ekran("satinalma_irsaliyeleri", "Alış İrsaliyeleri", "core:satinalma_irsaliyeleri"),
    )),
    Modul("SATINALMA", "Satınalma", (
        Ekran("satinalma_teklifleri", "Satınalma Teklifleri", "core:satinalma_teklifleri"),
        Ekran("satinalma_siparisleri", "Satınalma Siparişleri", "core:satinalma_siparisleri"),
    )),
    Modul("SATIS", "Satış", (
        Ekran("satis_teklifleri", "Satış Teklifleri", "core:satis_teklifleri"),
        Ekran("satis_proformalari", "Satış Proformaları", "core:satis_proformalari"),
        Ekran("satis_siparisleri", "Satış Siparişleri", "core:satis_siparisleri"),
        Ekran("numune_cikislari", "Numune Çıkışları", "core:numune_cikislari"),
    )),
    Modul("STOKLAR", "Stoklar", (
        Ekran("stoklar", "Stoklar", "core:stoklar"),
        Ekran("stok_sayim", "Stok Sayımları", "core:stok_sayimlari",
              ilgili=("core:stok_sayim_yeni", "core:stok_sayim_belge", "core:stok_sayim_giris", "core:stok_sayim_giris_kaydet", "core:stok_sayim_satir_ekle", "core:stok_sayim_durum", "core:stok_sayim_karar", "core:stok_sayim_girdi_gecersiz",
                      "core:stok_sayim_onay", "core:stok_sayim_maliyet", "core:stok_sayim_geri_al", "core:stok_sayim_excel_indir", "core:stok_sayim_excel_yukle")),
        Ekran("stok_sayim_raporlari", "Sayım Raporları", "core:stok_sayim_raporlari", yonetici=True, ilgili=("core:stok_sayim_rapor_stok",)),
        Ekran("kategoriler", "Kategoriler", "core:kategoriler", grup="Tanımlar",
              ilgili=("core:kategori_ekle", "core:kategori_duzenle", "core:kategori_sil")),
        Ekran("fatura_tipleri", "Fatura Tipleri", "core:fatura_tipleri", grup="Tanımlar",
              ilgili=("core:fatura_tipi_ekle", "core:fatura_tipi_duzenle", "core:fatura_tipi_sil")),
        Ekran("birimler", "Birimler", "core:birimler", grup="Tanımlar",
              ilgili=("core:birim_ekle", "core:birim_duzenle", "core:birim_sil")),
        Ekran("depolar", "Depolar", "core:depolar", grup="Tanımlar",
              ilgili=("core:depo_ekle", "core:depo_duzenle", "core:depo_sil")),
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
        Ekran("cari_yaslandirma", "Yaşlandırma", "core:cari_yaslandirma", grup="Raporlar"),
        Ekran("cari_vadesi_gecenler", "Vadesi Geçenler", "core:cari_vadesi_gecenler", grup="Raporlar"),
        Ekran("cari_bakiye_tarih", "Tarih İtibarıyla Bakiye", "core:cari_bakiye_tarih", grup="Raporlar"),
        Ekran("cari_ciro", "Cari Ciro Özeti", "core:cari_ciro", grup="Raporlar"),
        Ekran("cari_kategoriler", "Cari Kategorileri", "core:cari_kategoriler", grup="Tanımlar",
              ilgili=("core:cari_kategori_ekle", "core:cari_kategori_duzenle", "core:cari_kategori_sil")),
        Ekran("lokasyonlar", "Ülke / Şehir", "core:lokasyonlar", grup="Tanımlar",
              ilgili=("core:ulke_ekle", "core:ulke_duzenle", "core:ulke_sil", "core:sehir_ekle", "core:sehir_duzenle", "core:sehir_sil")),
        Ekran("vergi_daireleri", "Vergi Daireleri", "core:vergi_daireleri", grup="Tanımlar",
              ilgili=("core:vergi_dairesi_ekle", "core:vergi_dairesi_duzenle", "core:vergi_dairesi_sil")),
    )),
    Modul("FINANS", "Finans", (
        Ekran("kasa", "Kasa", "core:kasalar"),
        Ekran("banka", "Banka", "core:bankalar"),
        Ekran("kredi_karti", "Kredi Kartı", "core:kredi_kartlari"),
        Ekran("kredi", "Kredi", "core:krediler"),
        Ekran("cek_senet", "Çek-Senet", "core:cek_senetler"),
        Ekran("odeme_takvimi", "Ödeme Takvimi", "core:odeme_takvimi", grup="Raporlar"),
        Ekran("kk_nakit_akisi", "Kart Nakit Akışı", "core:kredi_karti_nakit_akisi", grup="Raporlar"),
    ), ana_url_adi="core:finans_ozet"),
    Modul("IK", "İnsan Kaynakları", (
        Ekran("personel", "Personel Kartları", "core:personeller",
              ilgili=("core:personel_ekle", "core:personel_detay", "core:personel_duzenle", "core:personel_sil", "core:ucret_ekle", "core:ucret_duzenle",
                      "core:ucret_sil", "core:belgeler", "core:belge_uyarilari", "core:belge_ekle", "core:belge_duzenle", "core:belge_sil",
                      "core:mesai_hesap_olustur", "core:mesai_sifre_sifirla", "core:ise_giris_kaydet", "core:ise_giris_paket_pdf")),
        Ekran("personel_izinleri", "İzinler", "core:izinler"),
        Ekran("personel_belgeleri", "Özlük Belgeleri", "core:belgeler", menude=False),   # artık Personel Kartı > Belgeler sekmesi; yetki kodu + URL duruyor
        Ekran("personel_devam", "Devam / Yoklama", "core:yoklama"),
        Ekran("resmi_tatil", "Resmî Tatiller", "core:resmi_tatiller"),
        Ekran("mesai_kayitlari", "Mesai Kayıtları", "core:mesai_kayitlari"),
        Ekran("personel_ucret", "Personel Ücretleri", "core:personeller", menude=False),   # artık Personel Kartı > Ücret sekmesi; yetki kodu duruyor
        Ekran("bordro", "Aylık Bordro", "core:bordro_listesi"),
        Ekran("yemek_takibi", "Yemek Takibi", "core:yemek_takibi"),
    )),
    Modul("FASON", "Fason", (
        Ekran("fason_hesapla", "Kesim Listesi Hesapla", "core:fason_hesapla"),
        Ekran("fason_siparisleri", "Fason Siparişleri", "core:fason_siparisleri"),
        Ekran("fason_fiyatlari", "Fason Fiyatları", "core:fason_fiyatlari"),
        Ekran("fason_donusleri", "Fason Dönüşler", "core:fason_donusleri"),
        Ekran("fason_mutabakat", "Fason Mutabakat", "core:fason_mutabakat"),
    )),
    Modul("URETIM", "Üretim", (
        Ekran("uretim_emirleri", "Üretim Siparişleri", "core:uretim_emirleri"),
        Ekran("istasyon_emirleri", "İstasyon Emirleri", "core:istasyon_emirleri"),
        Ekran("urun_agaci", "Ürün Ağacı", "core:urun_agaci"),
        Ekran("operasyon_kayitlari", "Operasyon Kayıtları", "core:operasyon_kayitlari"),
        Ekran("emirsiz_uretim", "Emirsiz Üretim", "core:emirsiz_uretim"),
        Ekran("is_oturumlari", "İş Oturumları", "core:is_oturumlari"),
        Ekran("operasyon_tanimlari", "Operasyon Tanımları", "core:operasyon_tanimlari"),
        Ekran("is_istasyonlari", "İş İstasyonları", "core:is_istasyonlari"),
    )),
    Modul("ATOLYE", "Atölye", (
        Ekran("atolye", "Atölye (Operatör Ekranı)", "core:atolye"),
        Ekran("personel_is_takibi", "Personel İş Takibi", "core:atolye_takip", yonetici=True),
    )),
    Modul("AYARLAR", "Ayarlar", (
        Ekran("kullanicilar", "Kullanıcılar", "core:kullanici_listesi"),
        Ekran("kullanici_yetkileri", "Kullanıcı Yetkileri", "core:kullanici_yetkileri"),
        Ekran("firma_bilgileri", "Firma Bilgileri", "core:firma_bilgileri"),
        Ekran("belge_sablonlari", "Belge Şablonları", "core:belge_sablonlari",
              ilgili=("core:belge_sablonu_duzenle", "core:belge_sablonu_varsayilan", "core:sozlesme_varsayilan_kaydet")),
        Ekran("tanim_listeleri", "Tanım Listeleri", "core:tanim_listeleri"),
        Ekran("varyantlar", "Varyantlar", "core:varyantlar"),
        Ekran("uretim_ayarlari", "Üretim Ayarları", "core:uretim_ayarlari"),
        Ekran("yedek", "Yedek", "core:yedek"),
        Ekran("silme_kayitlari", "Silme Kayıtları", "core:silme_kayitlari"),
        Ekran("mesai_ayarlari", "Mesai Ayarları", "core:mesai_ayarlari"),
    ), yonetici_modulu=True),
)


def menu_moduller(yonetici: bool):
    """Kullanıcıya göre görünür modüller (yönetici modülleri yalnızca yöneticiye)."""
    return [m for m in MODULLER if not m.yonetici_modulu or yonetici]
