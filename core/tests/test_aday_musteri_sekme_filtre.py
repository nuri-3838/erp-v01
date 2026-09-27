"""Aday Müşteri listesi revizyonu: Sekmeler + Katlanan filtreler + Son aktivite + Sıralama.

Bugün hesabı her yerde core.tarih.tr_bugun() (Europe/Istanbul). Bu dosya yalnız liste
ekranını (core/views.py::aday_musteriler) kapsar; model alanı testleri (tip/potansiyel/
asama/takip/eposta_gecersiz) test_aday_musteri_tip_alanlari.py ve
test_aday_musteri_takip.py'de zaten var, burada tekrar edilmez."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import AdayAktivite, AdayAsama, AdayAsamaTanim, AdayPotansiyel, AdayPotansiyelTanim, AdayTipTanim
from core.services.aday import (
    aday_aktivite_ekle, aday_aktivite_sil, aday_yetkili_ekle,
    aday_musteri_olustur as _aday_musteri_olustur_ham,
)
from core.tarih import tr_bugun
from core.tests.aday_yardimci import varsayilan_kaynak_id
from core.views import _ADAY_GORUNUMLER

UTC = dt_timezone.utc


def aday_musteri_olustur(*, tip=None, potansiyel=None, asama=None, **kw):
    """tip/potansiyel/asama artık FK — bu dosyada hâlâ sistem_kodu STRİNGİ (AdayTip.XXX vb.)
    ile çağrılabilsin diye ince bir çeviri katmanı (verilmezse eski CharField varsayılanları
    ADAY/YENİ)."""
    kw["tip_id"] = AdayTipTanim.objects.get(sistem_kodu=tip or "ADAY").pk
    kw["potansiyel_id"] = (AdayPotansiyelTanim.objects.get(sistem_kodu=potansiyel).pk
                           if potansiyel else None)
    kw["asama_id"] = AdayAsamaTanim.objects.get(sistem_kodu=asama or "YENI").pk
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    return _aday_musteri_olustur_ham(**kw)


def _sekme(r, kod):
    return next(s for s in r.context["sekmeler"] if s["kod"] == kod)


class _Taban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def _get(self, **params):
        return self.client.get(reverse("core:aday_musteriler"), params)


# --- 1. Sekme kuralları --------------------------------------------------------------
class SekmeKurallariTest(_Taban):
    def test_takip_sekmesi_yakin_gecmis_ve_kapali_haric(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="takip sinirda", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=7))
        aday_musteri_olustur(unvan="takip disinda", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=8))
        aday_musteri_olustur(unvan="takip gecmiste", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun - timedelta(days=30))
        aday_musteri_olustur(unvan="takip ama kapali", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun, asama=AdayAsama.KAPALI,
                             kapanis_nedeni="DIGER")
        aday_musteri_olustur(unvan="tarihi yok")
        r = self._get(gorunum="takip")
        self.assertContains(r, "TAKİP SİNİRDA")
        self.assertContains(r, "TAKİP GECMİSTE")
        self.assertNotContains(r, "TAKİP DİSİNDA")
        self.assertNotContains(r, "TAKİP AMA KAPALİ")
        self.assertNotContains(r, "TARİHİ YOK")
        self.assertEqual(_sekme(r, "takip")["sayi"], 2)

    def test_sicak_sekmesi_yuksek_potansiyel_ve_kapali_haric(self):
        aday_musteri_olustur(unvan="sicak firma", potansiyel=AdayPotansiyel.YUKSEK)
        aday_musteri_olustur(unvan="orta firma", potansiyel=AdayPotansiyel.ORTA)
        aday_musteri_olustur(unvan="sicak ama kapali", potansiyel=AdayPotansiyel.YUKSEK,
                             asama=AdayAsama.KAPALI, kapanis_nedeni="DIGER")
        r = self._get(gorunum="sicak")
        self.assertContains(r, "SİCAK FİRMA")
        self.assertNotContains(r, "ORTA FİRMA")
        self.assertNotContains(r, "SİCAK AMA KAPALİ")
        self.assertEqual(_sekme(r, "sicak")["sayi"], 1)

    def test_temas_yok_sekmesi_yalniz_yeni_asama(self):
        aday_musteri_olustur(unvan="yeni firma", asama=AdayAsama.YENI)
        aday_musteri_olustur(unvan="temasli firma", asama=AdayAsama.TEMAS)
        r = self._get(gorunum="temas_yok")
        self.assertContains(r, "YENİ FİRMA")
        self.assertNotContains(r, "TEMASLİ FİRMA")
        self.assertEqual(_sekme(r, "temas_yok")["sayi"], 1)

    def test_kapali_sekmesi_yalniz_kapali_asama(self):
        aday_musteri_olustur(unvan="kapanan firma", asama=AdayAsama.KAPALI,
                             kapanis_nedeni="ILGISIZ")
        aday_musteri_olustur(unvan="acik firma", asama=AdayAsama.SIPARIS)
        r = self._get(gorunum="kapali")
        self.assertContains(r, "KAPANAN FİRMA")
        self.assertNotContains(r, "ACİK FİRMA")
        self.assertEqual(_sekme(r, "kapali")["sayi"], 1)

    def test_tumu_sekmesi_filtresiz(self):
        aday_musteri_olustur(unvan="herhangi firma", asama=AdayAsama.KAPALI,
                             kapanis_nedeni="DIGER")
        r = self._get(gorunum="tumu")
        self.assertContains(r, "HERHANGİ FİRMA")
        self.assertEqual(_sekme(r, "tumu")["sayi"], 1)

    def test_cari_rollu_asamaya_elle_alinan_donusmemis_aday_takip_sicak_temasyoktan_duser(self):
        # Migration 0138 tohumu 'Sipariş'i CARI rolüne aldı — elle bu aşamaya alınmış ama
        # HENÜZ cariye dönüşmemiş (cari_id boş) bir aday da Takibim/Sıcak/Temas yok'tan
        # düşmeli (KAPALI gibi), ama Kapalı sekmesine de girmemeli; Tümü'nde görünmeye
        # devam etmeli (spec: hızlı işlemler/otomatik aşama, CARI rolü KAPALI gibi davranmaz).
        a = aday_musteri_olustur(unvan="cari rolu ama donusmedi", asama=AdayAsama.SIPARIS,
                                 sonraki_adim="x", sonraki_adim_tarihi=tr_bugun(),
                                 potansiyel=AdayPotansiyel.YUKSEK)
        for gorunum in ("takip", "sicak", "temas_yok", "kapali"):
            r = self._get(gorunum=gorunum)
            self.assertNotContains(r, a.unvan)
        r_tumu = self._get(gorunum="tumu")
        self.assertContains(r_tumu, a.unvan)


# --- 2. Sekme sayıları filtre uygulanmış halde hesaplanır -----------------------------
class SekmeSayilariFiltreliTest(_Taban):
    def test_kategori_filtresi_sekme_sayilarini_daraltir(self):
        from core.services.aday_kategori import aday_kategori_olustur
        irak = aday_kategori_olustur(ad="Irak", kod="IRAK")
        diger = aday_kategori_olustur(ad="Diğer", kod="DIGER")
        aday_musteri_olustur(unvan="irak sicak", potansiyel=AdayPotansiyel.YUKSEK,
                             kategori_id=irak.pk)
        aday_musteri_olustur(unvan="diger sicak", potansiyel=AdayPotansiyel.YUKSEK,
                             kategori_id=diger.pk)
        r = self._get(kategori=irak.pk)
        self.assertEqual(_sekme(r, "sicak")["sayi"], 1)
        self.assertEqual(_sekme(r, "tumu")["sayi"], 1)

    def test_arama_sekme_sayilarini_daraltir(self):
        aday_musteri_olustur(unvan="alfa firma", asama=AdayAsama.YENI)
        aday_musteri_olustur(unvan="beta firma", asama=AdayAsama.YENI)
        r = self._get(ara="alfa")
        self.assertEqual(_sekme(r, "temas_yok")["sayi"], 1)


# --- 3. Varsayılan sekme seçimi -------------------------------------------------------
class VarsayilanSekmeTest(_Taban):
    def test_takip_sayisi_sifirsa_tumu_acilir(self):
        aday_musteri_olustur(unvan="tarihsiz firma")
        r = self._get()
        self.assertEqual(r.context["gorunum"], "tumu")

    def test_takip_sayisi_pozitifse_takip_acilir(self):
        aday_musteri_olustur(unvan="yakin firma", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun())
        r = self._get()
        self.assertEqual(r.context["gorunum"], "takip")

    def test_gecersiz_gorunum_degeri_varsayilana_duser(self):
        aday_musteri_olustur(unvan="tarihsiz firma 2")
        r = self._get(gorunum="uydurma")
        self.assertEqual(r.context["gorunum"], "tumu")


# --- 4. Sekme/filtre/arama değişince sayfa 1'e döner ----------------------------------
class SekmeGecisKorumaTest(_Taban):
    def test_sekme_linki_arama_parametresini_korur(self):
        aday_musteri_olustur(unvan="alfa firma", potansiyel=AdayPotansiyel.YUKSEK)
        r = self._get(ara="alfa", gorunum="tumu")
        sicak_url = _sekme(r, "sicak")["url"]
        self.assertIn("ara=alfa", sicak_url)
        self.assertIn("gorunum=sicak", sicak_url)

    def test_sekme_linki_sayfa_parametresini_tasimaz(self):
        r = self._get(sayfa="3", gorunum="tumu")
        sicak_url = _sekme(r, "sicak")["url"]
        self.assertNotIn("sayfa=", sicak_url)

    def test_sekme_linki_siralamayi_tasimaz_diger_filtreler_korunur(self):
        r = self._get(gorunum="tumu", sirala="-son_akt", tip="ARACI", ara="deneme")
        for kod in _ADAY_GORUNUMLER:
            url = _sekme(r, kod)["url"]
            self.assertNotIn("sirala=", url, f"{kod} sekmesi sirala tasimamali")
            self.assertIn("tip=ARACI", url)
            self.assertIn("ara=deneme", url)

    def test_gecikmis_ve_tumunu_goster_linkleri_de_siralamayi_tasimaz(self):
        aday_musteri_olustur(unvan="gecikmis firma", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun() - timedelta(days=1))
        r = self._get(gorunum="kapali", sirala="unvan")
        self.assertNotIn("sirala=", r.context["gecikmis_url"])
        self.assertNotIn("sirala=", r.context["tumu_url"])

    def test_sekmeye_gecince_kendi_varsayilan_siralamasi_uygulanir(self):
        # Onceki istekte sirala=unvan secilmis olsa BILE, Sicak sekmesine gecince kendi
        # varsayilani (son aktivite azalan) uygulanmali — sekme linki sirala tasimadigi icin.
        bugun = tr_bugun()
        eski = aday_musteri_olustur(unvan="sicak eski aktivite", potansiyel=AdayPotansiyel.YUKSEK)
        aday_aktivite_ekle(eski, tarih=bugun - timedelta(days=10), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        yeni = aday_musteri_olustur(unvan="sicak yeni aktivite", potansiyel=AdayPotansiyel.YUKSEK)
        aday_aktivite_ekle(yeni, tarih=bugun, tur=AdayAktivite.Tur.NOT, aciklama="x")

        onceki = self._get(gorunum="tumu", sirala="unvan")
        sicak_url = _sekme(onceki, "sicak")["url"]
        self.assertNotIn("sirala=", sicak_url)

        r = self.client.get(reverse("core:aday_musteriler") + sicak_url)
        icerik = r.content.decode("utf-8")
        # sicak varsayilani: son aktivite azalan -> yeni once
        self.assertLess(icerik.index("SİCAK YENİ AKTİVİTE"), icerik.index("SİCAK ESKİ AKTİVİTE"))


# --- 5. Boş sekme -> "Tümünü göster" ---------------------------------------------------
class BosSekmeTest(_Taban):
    def test_bos_sekmede_tumunu_goster_linki(self):
        aday_musteri_olustur(unvan="tek firma", asama=AdayAsama.YENI)
        r = self._get(gorunum="kapali")
        self.assertContains(r, "Tümünü göster")
        self.assertContains(r, 'href="{}"'.format(r.context["tumu_url"]))


# --- 6. Katlanan filtreler + aktif filtre sayısı --------------------------------------
class KatlananFiltreSayisiTest(_Taban):
    def test_ara_sayilmaz_diger_filtreler_sayilir(self):
        r = self._get(ara="x", tip="ARACI", kategori="", asama=AdayAsama.YENI)
        self.assertEqual(r.context["aktif_filtre_sayisi"], 2)

    def test_hicbir_filtre_yoksa_sifir(self):
        r = self._get()
        self.assertEqual(r.context["aktif_filtre_sayisi"], 0)


# --- 7. Aktif filtre çipleri: ✕ yalnız o parametreyi kaldırır -------------------------
class CipKaldirmaTest(_Taban):
    def test_tip_cipi_yalniz_tip_parametresini_kaldirir(self):
        r = self._get(tip="ARACI", asama=AdayAsama.YENI, ara="deneme")
        etiketler = {c["etiket"]: c["url"] for c in r.context["cipler"]}
        tip_url = next(u for e, u in etiketler.items() if e.startswith("Tip:"))
        self.assertNotIn("tip=", tip_url)
        self.assertIn("asama=YENI", tip_url)
        self.assertIn("ara=deneme", tip_url)

    def test_arama_cipi_var_ve_yalniz_aramayi_kaldirir(self):
        r = self._get(ara="deneme", tip="ARACI")
        etiketler = {c["etiket"]: c["url"] for c in r.context["cipler"]}
        arama_url = next(u for e, u in etiketler.items() if e.startswith("Arama:"))
        self.assertNotIn("ara=", arama_url)
        self.assertIn("tip=ARACI", arama_url)

    def test_tumunu_temizle_her_zaman_kok(self):
        r = self._get(ara="deneme", tip="ARACI")
        self.assertContains(r, 'href="?">Tümünü temizle')


# --- 8. Son aktivite: annotate + son_akt filtresi (30/90/eski/yok) --------------------
class SonAktiviteFiltresiTest(_Taban):
    def test_son_30_gun_siniri_29_gun_icerir_30_gun_disarida(self):
        bugun = tr_bugun()
        a29 = aday_musteri_olustur(unvan="aktivite yirmidokuz")
        aday_aktivite_ekle(a29, tarih=bugun - timedelta(days=29), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        a30 = aday_musteri_olustur(unvan="aktivite otuz")
        aday_aktivite_ekle(a30, tarih=bugun - timedelta(days=30), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        r = self._get(son_akt="30", gorunum="tumu")
        self.assertContains(r, "AKTİVİTE YİRMİDOKUZ")
        self.assertNotContains(r, "AKTİVİTE OTUZ")

    def test_31_90_gun_araligi(self):
        bugun = tr_bugun()
        a30 = aday_musteri_olustur(unvan="aktivite otuz b")
        aday_aktivite_ekle(a30, tarih=bugun - timedelta(days=30), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        a89 = aday_musteri_olustur(unvan="aktivite seksendokuz")
        aday_aktivite_ekle(a89, tarih=bugun - timedelta(days=89), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        a90 = aday_musteri_olustur(unvan="aktivite doksan")
        aday_aktivite_ekle(a90, tarih=bugun - timedelta(days=90), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        r = self._get(son_akt="90", gorunum="tumu")
        self.assertContains(r, "AKTİVİTE OTUZ B")
        self.assertContains(r, "AKTİVİTE SEKSENDOKUZ")
        self.assertNotContains(r, "AKTİVİTE DOKSAN")

    def test_90_gunden_eski(self):
        bugun = tr_bugun()
        a90 = aday_musteri_olustur(unvan="aktivite doksan b")
        aday_aktivite_ekle(a90, tarih=bugun - timedelta(days=90), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        r = self._get(son_akt="eski", gorunum="tumu")
        self.assertContains(r, "AKTİVİTE DOKSAN B")

    def test_hic_aktivite_yok(self):
        aday_musteri_olustur(unvan="aktivitesiz firma")
        a = aday_musteri_olustur(unvan="aktiviteli firma")
        aday_aktivite_ekle(a, tarih=tr_bugun(), tur=AdayAktivite.Tur.NOT, aciklama="x")
        r = self._get(son_akt="yok", gorunum="tumu")
        self.assertContains(r, "AKTİVİTESİZ FİRMA")
        self.assertNotContains(r, "AKTİVİTELİ FİRMA")

    def test_silinmis_aktivite_son_aktiviteye_sayilmaz(self):
        bugun = tr_bugun()
        a = aday_musteri_olustur(unvan="silinen aktivite firma")
        eski = aday_aktivite_ekle(a, tarih=bugun - timedelta(days=95), tur=AdayAktivite.Tur.NOT,
                                  aciklama="eski")
        yeni = aday_aktivite_ekle(a, tarih=bugun, tur=AdayAktivite.Tur.NOT, aciklama="yeni")
        aday_aktivite_sil(yeni, kullanici=self.yon)
        # yalnız (silinmemiş) 95 gün önceki aktivite kalır -> "eski" kovasında olmalı
        r = self._get(son_akt="eski", gorunum="tumu")
        self.assertContains(r, "SİLİNEN AKTİVİTE FİRMA")
        r2 = self._get(son_akt="30", gorunum="tumu")
        self.assertNotContains(r2, "SİLİNEN AKTİVİTE FİRMA")


# --- 9. Sıralama: 7 değer + nulls_last -------------------------------------------------
class SiralamaTest(_Taban):
    def test_unvan_artan_ve_azalan(self):
        aday_musteri_olustur(unvan="alfa")
        aday_musteri_olustur(unvan="beta")
        r = self._get(sirala="unvan", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("ALFA"), icerik.index("BETA"))
        r2 = self._get(sirala="-unvan", gorunum="tumu")
        icerik2 = r2.content.decode("utf-8")
        self.assertLess(icerik2.index("BETA"), icerik2.index("ALFA"))

    def test_sonraki_artan_bos_tarihler_sonda(self):
        aday_musteri_olustur(unvan="tarihsiz aday")
        aday_musteri_olustur(unvan="yakin aday", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun() + timedelta(days=1))
        r = self._get(sirala="sonraki", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("YAKİN ADAY"), icerik.index("TARİHSİZ ADAY"))

    def test_sonraki_azalan_bos_tarihler_yine_sonda(self):
        aday_musteri_olustur(unvan="tarihsiz aday b")
        aday_musteri_olustur(unvan="uzak aday", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun() + timedelta(days=30))
        r = self._get(sirala="-sonraki", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("UZAK ADAY"), icerik.index("TARİHSİZ ADAY B"))

    def test_son_akt_artan_ve_azalan_bos_sonda(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="aktivitesiz c")
        eski = aday_musteri_olustur(unvan="eski aktiviteli")
        aday_aktivite_ekle(eski, tarih=bugun - timedelta(days=10), tur=AdayAktivite.Tur.NOT,
                          aciklama="x")
        yeni = aday_musteri_olustur(unvan="yeni aktiviteli")
        aday_aktivite_ekle(yeni, tarih=bugun, tur=AdayAktivite.Tur.NOT, aciklama="x")
        r = self._get(sirala="son_akt", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("ESKİ AKTİVİTELİ"), icerik.index("YENİ AKTİVİTELİ"))
        self.assertLess(icerik.index("YENİ AKTİVİTELİ"), icerik.index("AKTİVİTESİZ C"))
        r2 = self._get(sirala="-son_akt", gorunum="tumu")
        icerik2 = r2.content.decode("utf-8")
        self.assertLess(icerik2.index("YENİ AKTİVİTELİ"), icerik2.index("ESKİ AKTİVİTELİ"))
        self.assertLess(icerik2.index("ESKİ AKTİVİTELİ"), icerik2.index("AKTİVİTESİZ C"))

    def test_potansiyel_yuksek_orta_dusuk_bos(self):
        aday_musteri_olustur(unvan="pot bos")
        aday_musteri_olustur(unvan="pot dusuk", potansiyel=AdayPotansiyel.DUSUK)
        aday_musteri_olustur(unvan="pot orta", potansiyel=AdayPotansiyel.ORTA)
        aday_musteri_olustur(unvan="pot yuksek", potansiyel=AdayPotansiyel.YUKSEK)
        r = self._get(sirala="potansiyel", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        sira = sorted(["POT YUKSEK", "POT ORTA", "POT DUSUK", "POT BOS"],
                     key=lambda s: icerik.index(s))
        self.assertEqual(sira, ["POT YUKSEK", "POT ORTA", "POT DUSUK", "POT BOS"])

    def test_sirala_parametresi_sekme_varsayilanini_ezer(self):
        aday_musteri_olustur(unvan="zeta")
        aday_musteri_olustur(unvan="alfa2")
        r = self._get(sirala="unvan", gorunum="tumu")
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("ALFA2"), icerik.index("ZETA"))


# --- 10. tr_bugun sınırı (Europe/Istanbul gece yarısı) --------------------------------
class TrBugunSiniriTest(_Taban):
    def test_23_30_utc_ertesi_gun_sayilir(self):
        # 23:30 UTC = 02:30 TR (bir sonraki gün) -> "bugün" TR takvimine göre hesaplanmalı;
        # takip sekmesi/son_akt sınırları hep bu değeri kullanır (tek kaynak core.tarih).
        with mock.patch("django.utils.timezone.now") as sahte_now:
            sahte_now.return_value = datetime(2026, 3, 10, 23, 30, tzinfo=UTC)
            self.assertEqual(tr_bugun(), date(2026, 3, 11))


# --- 11. Sorgu sayısı sabit (N+1 yok) --------------------------------------------------
class SorguSayisiTest(_Taban):
    def test_liste_sayfasi_sabit_sorgu_sayisi(self):
        bugun = tr_bugun()
        for i in range(8):
            aday = aday_musteri_olustur(unvan=f"firma {i}", sonraki_adim="x",
                                        sonraki_adim_tarihi=bugun + timedelta(days=i))
            aday_aktivite_ekle(aday, tarih=bugun - timedelta(days=i), tur=AdayAktivite.Tur.NOT,
                              aciklama="x")
        # 11 sorgu: session+user (2) + sekme sayaçları (1, tek Count(filter=) sorgusu) +
        # gecikmiş sayaç (1) + kaynaklar/ülkeler (2, şehirler ülke seçilmeden hiç
        # sorgulanmaz) + tip/potansiyel/aşama filtre seçenekleri (3, her biri TEK sorgu —
        # aday sayısından bağımsız, satır arttıkça artmaz) + sayfalama count (1) + sayfa
        # satırları (1, son aktivite tarih/tür korele Subquery ile AYNI sorguda — N+1 yok).
        with self.assertNumQueries(11):
            self._get(gorunum="tumu")

    def test_arama_web_ve_yetkiliyle_sorgu_sayisi_ayni_kalir(self):
        """Arama kutusu web + adres + yetkili (ad_soyad/telefon/eposta) + aktivite aciklama
        alanlarını da kapsar (spec: Web/Adres/Yetkililer/WhatsApp/aktiviteyle sonraki adım,
        madde 6) — hepsi Exists ile (JOIN + distinct değil), sorgu sayısı artmaz."""
        hedef = aday_musteri_olustur(unvan="bulunacak firma", web="akc.ae",
                                     adres="bulunacak adres bilgisi")
        aday_yetkili_ekle(hedef, ad_soyad="bulunacak yetkili")
        aday_aktivite_ekle(hedef, tarih=tr_bugun(), tur=AdayAktivite.Tur.NOT,
                           aciklama="bulunacak aktivite aciklamasi")
        for i in range(5):
            aday_musteri_olustur(unvan=f"dolgu firma {i}")
        with self.assertNumQueries(11):
            self._get(ara="bulunacak", gorunum="tumu")
