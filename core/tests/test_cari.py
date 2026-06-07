"""Cari (CARİLER Faz 3a) testleri: otomatik kod (kategori kod yolu / CAR-),
TR büyük harf, VKN benzersizlik, sevk temizliği, kod koruma, view + yetki, taşıma."""
import json
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, CariKategori, EkranYetki, HesapPlani, Sehir, Ulke
from core.services.cari import (CariHatasi, cari_guncelle, cari_olustur, cari_sil,
                                muhasebe_hesabi_ac)


def _veri():
    ust = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="320")
    alt = CariKategori.objects.create(ad="HAMMADDE", kod="10", ust=ust)
    tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
    kayseri = Sehir.objects.create(ulke=tr, ad="KAYSERİ", kod="38")
    return ust, alt, tr, kayseri


def _olustur(**kw):
    kw.setdefault("unvan", "test cari")
    kw.setdefault("para_birimi", "TRY")
    return cari_olustur(**kw)


class CariServisTest(TestCase):
    def test_kod_otomatik_kategori(self):
        _, alt, *_ = _veri()
        c1 = _olustur(unvan="formal alüminyum", kategori_id=alt.pk)
        c2 = _olustur(unvan="boyçelik", kategori_id=alt.pk)
        self.assertEqual(c1.kod, "320-10-0001")
        self.assertEqual(c2.kod, "320-10-0002")
        self.assertEqual(c1.unvan, "FORMAL ALÜMİNYUM")     # TR büyük harf

    def test_kod_kategorisiz(self):
        c = _olustur(unvan="çalış a.ş.")
        self.assertEqual(c.kod, "CAR-0001")

    def test_unvan_zorunlu(self):
        with self.assertRaises(CariHatasi):
            _olustur(unvan="   ")

    def test_vkn_benzersiz(self):
        _olustur(unvan="a", vkn_tckn="1234567890")
        with self.assertRaises(CariHatasi):
            _olustur(unvan="b", vkn_tckn="1234567890")

    def test_sevk_temizlenir(self):
        _, alt, tr, kayseri = _veri()
        c = _olustur(unvan="a", kategori_id=alt.pk, sevk_farkli=False,
                     sevk_ulke_id=tr.pk, sevk_adres="bir yer")
        self.assertIsNone(c.sevk_ulke_id)
        self.assertEqual(c.sevk_adres, "")

    def test_sevk_farkli_korunur(self):
        _, alt, tr, kayseri = _veri()
        c = _olustur(unvan="a", kategori_id=alt.pk, sevk_farkli=True,
                     sevk_ulke_id=tr.pk, sevk_sehir_id=kayseri.pk, sevk_adres="depo adres")
        self.assertEqual(c.sevk_ulke_id, tr.pk)
        self.assertEqual(c.sevk_adres, "DEPO ADRES")

    def test_kod_korunur_tasima(self):
        _, alt, *_ = _veri()
        c = _olustur(unvan="x", kategori_id=alt.pk, kod="320-10-0099")
        self.assertEqual(c.kod, "320-10-0099")

    def test_guncelle_kod_sabit(self):
        _, alt, *_ = _veri()
        c = _olustur(unvan="x", kategori_id=alt.pk)
        eski = c.kod
        cari_guncelle(c, unvan="y", kategori_id=alt.pk, para_birimi="USD")
        c.refresh_from_db()
        self.assertEqual((c.kod, c.unvan, c.para_birimi), (eski, "Y", "USD"))

    def test_sil_soft_delete(self):
        c = _olustur(unvan="x")
        cari_sil(c)
        c.refresh_from_db()
        self.assertTrue(c.silindi)


class CariTasimaTest(TestCase):
    def test_tasima(self):
        _veri()
        veri = {"cariler": [{
            "kod": "320-10-0001", "unvan": "FORMAL ALÜMİNYUM", "kisa_ad": "",
            "kategori": "320-10", "vergi_dairesi": "", "vkn_tckn": "111", "tax_id": "",
            "telefon": "", "telefon_2": "", "eposta": "", "web": "", "kep_adresi": "",
            "ulke": "TR", "sehir": "KAYSERİ", "adres": "", "posta_kodu": "",
            "sevk_farkli": False, "sevk_ulke": None, "sevk_sehir": None,
            "sevk_adres": "", "sevk_posta_kodu": "", "para_birimi": "USD",
            "kredi_limiti": "0", "iskonto_yuzdesi": "0", "notlar": ""}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False)
            yol = f.name
        call_command("tasi_cari", yol)
        call_command("tasi_cari", yol)                     # idempotent
        c = Cari.objects.get(kod="320-10-0001")
        self.assertEqual(c.unvan, "FORMAL ALÜMİNYUM")
        self.assertEqual(c.kategori.kod_yolu, "320-10")
        self.assertEqual(c.ulke.kod, "TR")
        self.assertEqual(c.sehir.ad, "KAYSERİ")
        self.assertEqual(Cari.objects.filter(silindi=False).count(), 1)


class CariViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="cariler")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.ust, cls.alt, cls.tr, cls.kayseri = _veri()

    def test_liste_ve_ara(self):
        _olustur(unvan="formal alüminyum", kategori_id=self.alt.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cariler"))
        self.assertContains(r, "FORMAL ALÜMİNYUM")
        self.assertContains(r, "320-10-0001")
        r2 = self.client.get(reverse("core:cariler"), {"ara": "boyçelik"})
        self.assertNotContains(r2, "FORMAL ALÜMİNYUM")

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_ekle"), {
            "unvan": "yeni cari", "kategori": str(self.alt.pk), "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 302)
        c = Cari.objects.get(unvan="YENİ CARİ")
        self.assertEqual(c.kod, "320-10-0001")

    def test_form_yalniz_alt_kategori(self):
        """Kategori seçiminde üst (ana) kategori yok; yalnız alt seçilebilir."""
        from core.forms import CariForm
        qs = list(CariForm().fields["kategori"].queryset)
        self.assertIn(self.alt, qs)
        self.assertNotIn(self.ust, qs)

    def test_ekle_ust_kategori_reddedilir(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_ekle"), {
            "unvan": "x", "kategori": str(self.ust.pk), "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 200)                    # formda kalır
        self.assertFalse(Cari.objects.filter(unvan="X").exists())

    def test_detay_render(self):
        c = _olustur(unvan="formal", kategori_id=self.alt.pk)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:cari_detay", args=[c.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "320-10-0001")
        self.assertContains(r, "Bilgiler")
        self.assertContains(r, "Kayıt Bilgisi")

    def test_duzenle_post(self):
        c = _olustur(unvan="formal", kategori_id=self.alt.pk)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), {
            "unvan": "formal güncel", "kategori": str(self.alt.pk), "para_birimi": "USD"})
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual((c.unvan, c.para_birimi, c.kod),
                         ("FORMAL GÜNCEL", "USD", "320-10-0001"))

    def test_sil_post(self):
        c = _olustur(unvan="formal", kategori_id=self.alt.pk)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_sil", args=[c.pk]))
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertTrue(c.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:cariler")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:cari_ekle")).status_code, 403)


class CariMuhasebeTest(TestCase):
    def _kur(self):
        HesapPlani.objects.create(hesap_kodu="320", hesap_adi="SATICILAR",
                                  rapor_grubu="BILANCO", rapor_kalemi="KVYK", parasal=True)
        ust = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="320")
        alt = CariKategori.objects.create(ad="HAMMADDE", kod="10", ust=ust)
        return alt

    def test_muhasebe_hesabi_otomatik(self):
        alt = self._kur()
        c = cari_olustur(unvan="formal", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual((c.kod, c.muhasebe_kodu), ("320-10-0001", "320.10.0001"))
        ara = HesapPlani.objects.get(hesap_kodu="320.10")
        self.assertEqual(ara.hesap_adi, "HAMMADDE")          # ara = kategori adı
        yaprak = HesapPlani.objects.get(hesap_kodu="320.10.0001")
        self.assertEqual(yaprak.hesap_adi, "FORMAL")          # yaprak = cari unvanı
        self.assertEqual(yaprak.rapor_kalemi, "KVYK")          # üstten miras

    def test_ikinci_cari_ayni_ara_tek(self):
        alt = self._kur()
        cari_olustur(unvan="a", kategori_id=alt.pk, para_birimi="TRY")
        c2 = cari_olustur(unvan="b", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual(c2.muhasebe_kodu, "320.10.0002")
        self.assertEqual(HesapPlani.objects.filter(hesap_kodu="320.10").count(), 1)

    def test_kok_yoksa_muhasebe_bos(self):
        ust = CariKategori.objects.create(ad="T", kod="320")
        alt = CariKategori.objects.create(ad="H", kod="10", ust=ust)
        c = cari_olustur(unvan="x", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual(c.muhasebe_kodu, "")                 # kök hesap yok
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="320.10").exists())

    def test_backfill_komutu(self):
        alt = self._kur()
        c = cari_olustur(unvan="formal", kategori_id=alt.pk, para_birimi="TRY")
        c.muhasebe_kodu = ""        # eski kayıt taklidi
        c.save(update_fields=["muhasebe_kodu"])
        call_command("cari_muhasebe_ac")
        c.refresh_from_db()
        self.assertEqual(c.muhasebe_kodu, "320.10.0001")

    def test_unvan_degisince_hesap_adi_senkron(self):
        # #4: cari adı değişince yaprak muhasebe hesabının adı da güncellenir.
        alt = self._kur()
        c = cari_olustur(unvan="eski unvan", kategori_id=alt.pk, para_birimi="TRY")
        cari_guncelle(c, unvan="yeni unvan", kategori_id=alt.pk, para_birimi="TRY")
        yaprak = HesapPlani.objects.get(hesap_kodu="320.10.0001")
        self.assertEqual(yaprak.hesap_adi, "YENİ UNVAN")

    def test_sil_hareketsiz_hesabi_gizler(self):
        # #4: hareketsiz cari silinince yaprak muhasebe hesabı da soft-delete olur.
        alt = self._kur()
        c = cari_olustur(unvan="silinecek", kategori_id=alt.pk, para_birimi="TRY")
        cari_sil(c)
        yaprak = HesapPlani.objects.get(hesap_kodu="320.10.0001")
        self.assertTrue(yaprak.silindi)
        # ara hesap (320.10) korunur — başka cariler paylaşabilir
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="320.10").silindi)
