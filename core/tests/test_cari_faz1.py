"""Cariler FAZ 1 (2026-10-13): silme koruması (hareketli/belgeli cari silinemez, nedenler + Pasife al, hesap_sil hatası yutulmaz), Aktif/PASİF
(listede gizli, yeni belge/harekette seçilemez, geçmiş/ekstre etkilenmez), silinmiş cari kod/hesabı yeniden verilmez, ana PB kilidi + onay ekranı,
VKN/TCKN doğrulama + mükerrer VKN (yönetici "yine de kaydet"), cari banka IBAN + banka adı önerisi, kod üretimi yeniden deneme, escapejs, alt kayıt
ekranlarında üst cari kontrolü."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django import forms
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.dogrulama import iban_gecerli, iban_gecerli_tr, tc_gecerli, vkn_gecerli, vkn_tckn_tipi
from core.forms import _secilebilir_cariler
from core.models import Cari, CariBanka, CariKategori, EkranYetki, HesapPlani, Kur, TeklifSiparis
from core.services import cari as cs
from core.services import cari_kesinti as ck
from core.services import cari_virman as cv
from core.services import hesap_plani as hp
from core.services.cari import (CariHatasi, CariMukerrerVKN, CariPbOnayGerekli, CariSilmeEngeli, cari_aktiflestir, cari_guncelle, cari_olustur,
                                cari_pasife_al, cari_sil, secilebilir_cariler)
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur, fatura_taslak_olustur
from core.services.kasa_hareket import KasaHareketHatasi, hareket_olustur as kasa_hareket_olustur
from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_fatura import FaturaTestTemel, _hesap

D = datetime.date


def gecerli_vkn(govde="123456789"):
    """Tek geçerli kontrol hanesini bulur (algoritma özelliği: 9 haneye karşılık tam 1 geçerli 10. hane)."""
    uygun = [d for d in range(10) if vkn_gecerli(govde + str(d))]
    assert len(uygun) == 1
    return govde + str(uygun[0])


def hatali_vkn(govde="123456789"):
    gecerli = gecerli_vkn(govde)
    return govde + str((int(gecerli[-1]) + 1) % 10)


TC = "10000000146"          # geçerli TCKN (personel testlerinde de kullanılır)
TR_IBAN = "TR330006100519786457841326"
DE_IBAN = "DE89370400440532013000"


def _kategori():
    HesapPlani.objects.create(hesap_kodu="320", hesap_adi="SATICILAR", rapor_grubu="BILANCO", rapor_kalemi="KVYK", parasal=True)
    ust = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="320")
    return CariKategori.objects.create(ad="HAMMADDE", kod="10", ust=ust)


def _hareketli_cari(alt, unvan="HAREKETLI A.Ş."):
    Kur.objects.get_or_create(tarih=D(2026, 1, 1), defaults={"usd_alis": Decimal("30")})
    HesapPlani.objects.get_or_create(hesap_kodu="100", defaults=dict(hesap_adi="KASA", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True))
    c = cari_olustur(unvan=unvan, kategori_id=alt.pk, para_birimi="TRY")
    fis_olustur(tarih=D(2026, 1, 1), kullanici=None, satirlar=[SatirGirdi(c.muhasebe_kodu, "A", "1.000,00"), SatirGirdi("100", "B", "1.000,00")])
    return c


class DogrulamaTest(TestCase):
    def test_vkn_algoritmasi_ve_tip(self):
        v = gecerli_vkn()
        self.assertTrue(vkn_gecerli(v))
        self.assertFalse(vkn_gecerli(hatali_vkn()))
        self.assertFalse(vkn_gecerli("12345"))
        self.assertEqual((vkn_tckn_tipi(v), vkn_tckn_tipi(TC), vkn_tckn_tipi(hatali_vkn()), vkn_tckn_tipi("")), ("VKN", "TCKN", "", ""))
        self.assertTrue(tc_gecerli(TC))                                       # personelle ortak yardımcı (core.dogrulama)

    def test_iban_genel(self):
        self.assertTrue(iban_gecerli_tr(TR_IBAN) and iban_gecerli(TR_IBAN))
        self.assertTrue(iban_gecerli(DE_IBAN) and not iban_gecerli_tr(DE_IBAN))
        self.assertFalse(iban_gecerli("DE89370400440532013001"))
        self.assertFalse(iban_gecerli("TR12"))


class SilmeKorumasiTest(FaturaTestTemel):
    def test_hareketsiz_cari_silinir(self):
        c = Cari.objects.create(kod="320-10-0050", unvan="BOŞ CARİ", para_birimi="TRY")
        self.assertEqual(cs.silme_engelleri(c), [])
        cari_sil(c)
        c.refresh_from_db()
        self.assertTrue(c.silindi)

    def test_faturali_ve_hareketli_cari_silinemez_nedenler_listelenir(self):
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir())
        self.stok.tedarikci = self.tedarikci
        self.stok.save()
        with self.assertRaises(CariSilmeEngeli) as cm:
            cari_sil(self.tedarikci)
        etiketler = dict(cm.exception.nedenler)
        self.assertIn("yevmiye hareketi (muhasebe hesabında)", etiketler)
        self.assertEqual(etiketler["fatura"], 1)
        self.assertEqual(etiketler["stok kartında tedarikçi"], 1)
        self.assertIn("PASİFE alın", str(cm.exception))
        self.tedarikci.refresh_from_db()
        self.assertFalse(self.tedarikci.silindi)
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="320.10.0001").silindi)

    def test_hesap_sil_hatasi_sessizce_yutulmaz(self):
        c = Cari.objects.create(kod="120-10-0002", unvan="HESAPLI", para_birimi="TRY", muhasebe_kodu="120.10.0002")
        _hesap("120.10.0002", "HESAPLI")
        with patch("core.services.cari.hp.hesap_sil", side_effect=hp.HesapHatasi("bağlı tanım var")):
            with self.assertRaisesMessage(CariHatasi, "muhasebe hesabı (120.10.0002) kapatılamadı"):
                cari_sil(c)
        c.refresh_from_db()
        self.assertFalse(c.silindi)                                           # atomik: cari de silinmedi

    def test_detay_ekrani_engel_ve_pasife_al_onerir(self):
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir())
        su = User.objects.create_superuser("silsu", password="x")
        self.client.force_login(su)
        r = self.client.get(reverse("core:cari_detay", args=[self.tedarikci.pk]))
        self.assertContains(r, "🔒 Silinemez")
        self.assertContains(r, "Pasife al")
        self.assertNotContains(r, 'action="%s"' % reverse("core:cari_sil", args=[self.tedarikci.pk]))
        r = self.client.post(reverse("core:cari_sil", args=[self.tedarikci.pk]), follow=True)
        self.assertContains(r, "silinemez")
        self.tedarikci.refresh_from_db()
        self.assertFalse(self.tedarikci.silindi)
        # hareketsiz cari: Sil düğmesi var, escapejs'li onay
        c = Cari.objects.create(kod="320-10-0051", unvan="D'ARTAGNAN LTD", para_birimi="TRY")
        r = self.client.get(reverse("core:cari_detay", args=[c.pk]))
        self.assertContains(r, "D\\u0027ARTAGNAN LTD silinsin mi?")
        self.assertNotContains(r, "confirm('D'ARTAGNAN")


class PasifCariTest(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.su = User.objects.create_superuser("pasifsu", password="x")
        _hesap("100.01", "KASA")

    def test_varsayilan_aktif_ve_gecis(self):
        self.assertTrue(self.tedarikci.aktif)
        cari_pasife_al(self.tedarikci, self.su)
        self.tedarikci.refresh_from_db()
        self.assertFalse(self.tedarikci.aktif)
        cari_aktiflestir(self.tedarikci, self.su)
        self.tedarikci.refresh_from_db()
        self.assertTrue(self.tedarikci.aktif)

    def test_liste_varsayilan_gizli_pasifleri_goster(self):
        p = Cari.objects.create(kod="320-10-0060", unvan="PASIFFF CARI", para_birimi="TRY", aktif=False)
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cariler"))
        self.assertNotContains(r, "PASIFFF CARI")
        self.assertContains(r, "Pasif dahil (1)")
        r = self.client.get(reverse("core:cariler") + "?pasif=1")
        self.assertContains(r, "PASIFFF CARI")
        self.assertContains(r, "crl-pasif-rozet")
        # detay: pasif uyarısı, Aktifleştir, Kesinti/Virman düğmesi yok
        r = self.client.get(reverse("core:cari_detay", args=[p.pk]))
        self.assertContains(r, "Aktifleştir")
        self.assertNotContains(r, "Kesinti / Masraf")
        self.assertNotContains(r, "🔁 Virman")
        r = self.client.post(reverse("core:cari_aktiflestir", args=[p.pk]), follow=True)
        p.refresh_from_db()
        self.assertTrue(p.aktif)

    def test_secilebilir_cariler_ve_form_yardimcisi(self):
        cari_pasife_al(self.tedarikci)
        self.assertNotIn(self.tedarikci, secilebilir_cariler())
        self.assertIn(self.tedarikci, secilebilir_cariler([self.tedarikci.pk]))

        class F(forms.Form):
            cari = forms.IntegerField(required=False)
        self.assertNotIn(self.tedarikci, _secilebilir_cariler(F()))
        self.assertIn(self.tedarikci, _secilebilir_cariler(F(initial={"cari": self.tedarikci.pk})))        # düzenlenen kaydın mevcut carisi kalır
        self.assertIn(self.tedarikci, _secilebilir_cariler(F({"cari": str(self.tedarikci.pk)})))
        self.assertIn(self.musteri, _secilebilir_cariler(F()))
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertNotContains(r, "TEDARİKÇİ A")
        self.assertContains(r, "MÜŞTERİ A")

    def test_yeni_fatura_reddedilir_mevcut_taslak_duzenlenir_gecmis_etkilenmez(self):
        eski = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir())
        taslak = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir(), tip_id=self.alis.pk)
        cari_pasife_al(self.tedarikci)
        with self.assertRaisesMessage(FaturaHatasi, "PASİF"):
            fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir())
        with self.assertRaisesMessage(FaturaHatasi, "PASİF"):
            fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir(), tip_id=self.alis.pk)
        fatura_guncelle(taslak, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 11), satirlar=self._satir("5"))   # mevcut cari serbest
        eski.refresh_from_db()
        self.assertEqual(eski.cari_id, self.tedarikci.pk)
        # ekstre pasif cari için çalışır
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_ekstresi", args=[self.tedarikci.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Dönem")

    def test_diger_servisler_pasif_cariyi_reddeder(self):
        cari_pasife_al(self.tedarikci)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "PASİF"):
            teklif_siparis_olustur(belge_tur=TeklifSiparis.BelgeTur.SIPARIS, yon=TeklifSiparis.Yon.ALIS, cari_id=self.tedarikci.pk,
                                   tarih=D(2026, 3, 10), satirlar=[])
        from core.services.finans import kasa_olustur
        kasa = kasa_olustur(ad="K1", muhasebe_kodu="100.01")
        with self.assertRaisesMessage(KasaHareketHatasi, "PASİF"):
            kasa_hareket_olustur(kasa=kasa, tip="cari_odeme", karsi=self.tedarikci, tutar="10", tarih=D(2026, 3, 10))
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "PASİF"):
            cv.virman_olustur(cari=self.musteri, karsi_cari=self.tedarikci, tarih=D(2026, 3, 10), tutar="10", yon="alacak")
        with self.assertRaisesMessage(ck.CariKesintiHatasi, "PASİF"):
            ck.kesinti_olustur(cari=self.tedarikci, tarih=D(2026, 3, 10), tutar="5")
        # aktifleştirince tekrar çalışır
        cari_aktiflestir(self.tedarikci)
        kasa_hareket_olustur(kasa=kasa, tip="cari_odeme", karsi=self.tedarikci, tutar="10", tarih=D(2026, 3, 10))

    def test_kesinti_virman_ekranlari_ve_alt_kayitlar_pasifte_kilitli(self):
        banka = CariBanka.objects.create(cari=self.tedarikci, banka_adi="X", iban=TR_IBAN)
        cari_pasife_al(self.tedarikci)
        self.client.force_login(self.su)
        for url in (reverse("core:cari_kesinti_ekle", args=[self.tedarikci.pk]), reverse("core:cari_virman_ekle", args=[self.tedarikci.pk]),
                    reverse("core:banka_ekle", args=[self.tedarikci.pk]), reverse("core:banka_duzenle", args=[banka.pk]),
                    reverse("core:sevk_adresi_ekle", args=[self.tedarikci.pk]), reverse("core:yetkili_ekle", args=[self.tedarikci.pk])):
            r = self.client.get(url)
            self.assertRedirects(r, reverse("core:cari_detay", args=[self.tedarikci.pk]), fetch_redirect_response=False, msg_prefix=url)
        r = self.client.post(reverse("core:banka_sil", args=[banka.pk]))
        banka.refresh_from_db()
        self.assertFalse(banka.silindi)                                              # pasif carinin banka hesabı silinemez


class KodHesapTest(TestCase):
    def test_silinmis_carinin_kodu_ve_hesabi_yeniden_verilmez(self):
        alt = _kategori()
        c1 = cari_olustur(unvan="ilk cari", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual((c1.kod, c1.muhasebe_kodu), ("320-10-0001", "320.10.0001"))
        cari_sil(c1)
        c2 = cari_olustur(unvan="ikinci cari", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual((c2.kod, c2.muhasebe_kodu), ("320-10-0002", "320.10.0002"))          # 0001 bir daha verilmedi
        with self.assertRaisesMessage(CariHatasi, "yeniden kullanılamaz"):
            cari_olustur(unvan="üçüncü", kategori_id=alt.pk, para_birimi="TRY", kod="320-10-0001")   # elle/taşıma kodu da engellenir
        with self.assertRaisesMessage(CariHatasi, "Cari kodu zaten kayıtlı"):
            cari_olustur(unvan="dördüncü", kategori_id=alt.pk, para_birimi="TRY", kod="320-10-0002")
        self.assertEqual(Cari.objects.filter(unvan="ÜÇÜNCÜ").count(), 0)

    def test_hesap_plani_kodlari_da_sayilir(self):
        alt = _kategori()
        hp.hesap_olustur(kod="320.10", ad="HAMMADDE", ust_kodu="320") if not HesapPlani.objects.filter(hesap_kodu="320.10").exists() else None
        HesapPlani.objects.create(hesap_kodu="320.10.0007", hesap_adi="ESKİ", rapor_grubu="BILANCO", rapor_kalemi="KVYK", parasal=True, silindi=True)
        c = cari_olustur(unvan="yeni", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual(c.kod, "320-10-0008")                                                  # silinmiş hesap kodu da sayıldı


class PbKilidiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alt = _kategori()
        cls.su = User.objects.create_superuser("pbsu", password="x")
        cls.sade = User.objects.create_user("pbsade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="cariler")

    def test_hareketsiz_cari_pb_serbest(self):
        c = cari_olustur(unvan="hareketsiz", kategori_id=self.alt.pk, para_birimi="TRY")
        cari_guncelle(c, unvan="hareketsiz", kategori_id=self.alt.pk, para_birimi="USD", kullanici=self.sade)
        c.refresh_from_db()
        self.assertEqual(c.para_birimi, "USD")

    def test_hareketli_cari_servis_kurallari(self):
        c = _hareketli_cari(self.alt)
        with self.assertRaisesMessage(CariHatasi, "yalnız yönetici"):
            cari_guncelle(c, unvan=c.unvan, kategori_id=self.alt.pk, para_birimi="USD", kullanici=self.sade)
        with self.assertRaises(CariPbOnayGerekli) as cm:
            cari_guncelle(c, unvan=c.unvan, kategori_id=self.alt.pk, para_birimi="USD", kullanici=self.su)
        self.assertEqual((cm.exception.eski, cm.exception.yeni), ("TRY", "USD"))
        self.assertEqual([(h["pb"], h["net_tl"]) for h in cm.exception.havuzlar], [("TRY", Decimal("-1000.00"))])
        cari_guncelle(c, unvan=c.unvan, kategori_id=self.alt.pk, para_birimi="USD", kullanici=self.su, pb_degisim_onayi=True)
        c.refresh_from_db()
        self.assertEqual(c.para_birimi, "USD")
        # aynı PB ile güncelleme (kilit yok)
        cari_guncelle(c, unvan="yeni ad", kategori_id=self.alt.pk, para_birimi="USD", kullanici=self.sade)

    def _post(self, c, pb, **ek):
        return self.client.post(reverse("core:cari_duzenle", args=[c.pk]), {
            "unvan": c.unvan, "tip": "TUZEL", "para_birimi": pb, "kur_tipi": "MB_ALIS", "iskonto_yuzdesi": "0", **ek})

    def test_yonetici_onay_ekrani_ve_normal_kullanici_kilidi(self):
        c = _hareketli_cari(self.alt)
        self.client.force_login(self.su)
        r = self._post(c, "USD")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Mevcut PB havuz bakiyeleri")
        self.assertContains(r, "yeniden değerlenmez")
        self.assertContains(r, 'name="pb_onay"')
        c.refresh_from_db()
        self.assertEqual(c.para_birimi, "TRY")
        r = self._post(c, "USD", pb_onay="1")
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.para_birimi, "USD")
        # normal kullanıcı: alan kilitli, POST'taki farklı PB yok sayılır
        c2 = _hareketli_cari(self.alt, "IKINCI HAREKETLI")
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_duzenle", args=[c2.pk]))
        self.assertContains(r, "yalnız yönetici değiştirebilir")
        r = self._post(c2, "EUR")
        self.assertEqual(r.status_code, 302)
        c2.refresh_from_db()
        self.assertEqual(c2.para_birimi, "TRY")


class VknTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kat = _kategori()
        cls.su = User.objects.create_superuser("vknsu", password="x")
        cls.sade = User.objects.create_user("vknsade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="cariler")

    def _kayit(self, **ek):
        return self.client.post(reverse("core:cari_ekle"), {"unvan": "vkn test", "tip": "TUZEL", "kategori": str(self.kat.pk),
                                                             "para_birimi": "TRY", "kur_tipi": "MB_ALIS", "iskonto_yuzdesi": "0", **ek})

    def test_yeni_kayitta_hatali_vkn_kaydedilmez_gecerli_kaydedilir(self):
        self.client.force_login(self.sade)
        r = self._kayit(vkn_tckn=hatali_vkn())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Geçersiz VKN/TCKN")
        self.assertFalse(Cari.objects.filter(unvan="VKN TEST").exists())
        r = self._kayit(vkn_tckn=gecerli_vkn())
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Cari.objects.filter(unvan="VKN TEST", vkn_tckn=gecerli_vkn()).exists())
        r = self._kayit(unvan="tc test", vkn_tckn=TC, tip="SAHIS")
        self.assertEqual(r.status_code, 302)
        r = self._kayit(unvan="harf", vkn_tckn="12345abcde")
        self.assertContains(r, "Geçersiz VKN/TCKN")

    def test_mevcut_hatali_kayit_duzenlenirken_uyari_ama_dokunulmazsa_engel_yok(self):
        c = Cari.objects.create(kod="CAR-0001", unvan="ESKI HATALI", para_birimi="TRY", vkn_tckn=hatali_vkn())
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_duzenle", args=[c.pk]))
        self.assertContains(r, "kontrol hanesi hatalı")
        veri = {"unvan": "ESKI HATALI 2", "tip": "TUZEL", "kategori": str(self.kat.pk), "para_birimi": "TRY", "kur_tipi": "MB_ALIS",
                "iskonto_yuzdesi": "0", "vkn_tckn": hatali_vkn()}
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), veri)
        self.assertEqual(r.status_code, 302)                                   # VKN'ye dokunulmadı → engel yok
        veri["vkn_tckn"] = hatali_vkn("987654321")                             # değiştirildi ve hâlâ hatalı → engel
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), veri)
        self.assertContains(r, "Geçersiz VKN/TCKN")

    def test_mukerrer_vkn_uyari_baglanti_ve_yonetici_yine_de_kaydet(self):
        v = gecerli_vkn()
        ilk = cari_olustur(unvan="ilk firma", vkn_tckn=v, para_birimi="TRY")
        with self.assertRaises(CariMukerrerVKN) as cm:
            cari_olustur(unvan="ikinci firma", vkn_tckn=v, para_birimi="TRY")
        self.assertEqual(cm.exception.mevcutlar, [ilk])
        with self.assertRaises(CariMukerrerVKN):                                # normal kullanıcı onay veremez
            cari_olustur(unvan="ikinci firma", vkn_tckn=v, para_birimi="TRY", mukerrer_onayi=True, kullanici=self.sade)
        self.client.force_login(self.sade)
        r = self._kayit(unvan="ikinci firma", vkn_tckn=v)
        self.assertContains(r, "Aynı VKN/TCKN ile kayıtlı cari")
        self.assertContains(r, reverse("core:cari_detay", args=[ilk.pk]))
        self.assertNotContains(r, "Yine de kaydet")
        self.client.force_login(self.su)
        r = self._kayit(unvan="ikinci firma", vkn_tckn=v)
        self.assertContains(r, "Yine de kaydet")
        self.assertEqual(Cari.objects.filter(vkn_tckn=v).count(), 1)
        r = self._kayit(unvan="ikinci firma", vkn_tckn=v, mukerrer_onay="1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Cari.objects.filter(vkn_tckn=v, silindi=False).count(), 2)
        # Tax ID hâlâ kesin benzersiz
        cari_olustur(unvan="yd bir", tax_id="DE123", para_birimi="USD")
        with self.assertRaisesMessage(CariHatasi, "Tax ID"):
            cari_olustur(unvan="yd iki", tax_id="DE123", para_birimi="USD")

    def test_duzenlemede_mukerrer_vkn(self):
        v = gecerli_vkn()
        cari_olustur(unvan="sahip", vkn_tckn=v, para_birimi="TRY")
        diger = cari_olustur(unvan="diger", para_birimi="TRY")
        with self.assertRaises(CariMukerrerVKN):
            cari_guncelle(diger, unvan="diger", vkn_tckn=v, para_birimi="TRY")
        cari_guncelle(diger, unvan="diger", vkn_tckn=v, para_birimi="TRY", mukerrer_onayi=True, kullanici=self.su)
        # ortak VKN'li kart sonradan başka bir alanı değişince engellenmez
        cari_guncelle(diger, unvan="diger yeni", vkn_tckn=v, para_birimi="TRY")


class BankaIbanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser("bnksu", password="x")
        cls.c = cari_olustur(unvan="bankali", para_birimi="TRY")

    def test_iban_dogrulama_ve_banka_adi_onerisi(self):
        self.client.force_login(self.su)
        url = reverse("core:banka_ekle", args=[self.c.pk])
        r = self.client.get(url)
        self.assertContains(r, 'id="banka-kodlari"')
        self.assertContains(r, "00062")                                          # banka kodu → ad tablosu JS'e verildi
        self.assertContains(r, "TR IBAN girilince önerilir")
        r = self.client.post(url, {"banka_adi": "X BANK", "iban": "TR12 3456", "para_birimi": "TRY"})
        self.assertContains(r, "Geçersiz IBAN")
        self.assertEqual(CariBanka.objects.count(), 0)
        r = self.client.post(url, {"banka_adi": "GARANTİ BBVA", "iban": "tr33 0006 1005 1978 6457 8413 26", "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CariBanka.objects.get().iban, TR_IBAN)                 # kanonik (boşluksuz, büyük harf)
        r = self.client.post(url, {"banka_adi": "DEUTSCHE", "iban": DE_IBAN, "para_birimi": "EUR"})
        self.assertEqual(r.status_code, 302)                                    # yurtdışı IBAN (genel mod-97)
        r = self.client.post(url, {"banka_adi": "BOZUK", "iban": "DE89370400440532013001", "para_birimi": "EUR"})
        self.assertContains(r, "Geçersiz IBAN")


class TeknikTest(TestCase):
    def test_kod_uretimi_cakismada_yeniden_dener_sonra_anlasilir_mesaj(self):
        alt = _kategori()
        with patch("core.services.cari._kod_hesap_cakisma", side_effect=[IntegrityError("x"), None]):
            c = cari_olustur(unvan="yeniden denenen", kategori_id=alt.pk, para_birimi="TRY")
        self.assertEqual(c.kod, "320-10-0001")
        with patch("core.services.cari._kod_hesap_cakisma", side_effect=IntegrityError("x")):
            with self.assertRaisesMessage(CariHatasi, "Cari kodu üretilemedi"):
                cari_olustur(unvan="hep çakışan", kategori_id=alt.pk, para_birimi="TRY")
        with patch("core.services.cari._kod_hesap_cakisma", side_effect=IntegrityError("x")):
            with self.assertRaisesMessage(CariHatasi, "Cari kodu zaten kayıtlı"):
                cari_olustur(unvan="elle kodlu", kategori_id=alt.pk, para_birimi="TRY", kod="320-10-0099")
        self.assertEqual(Cari.objects.count(), 1)

    def test_alt_kayit_ekranlari_silinmis_cariye_acilmaz(self):
        su = User.objects.create_superuser("tekniksu", password="x")
        c = cari_olustur(unvan="silinecek", para_birimi="TRY")
        from core.models import CariAktivite, CariSevkAdresi, CariYetkili
        b = cs.banka_ekle(c, banka_adi="x", iban="tr01")
        s = cs.sevk_adresi_ekle(c, ad="depo")
        y = cs.yetkili_ekle(c, ad_soyad="ali")
        a = cs.aktivite_ekle(c, tarih=D(2026, 1, 1), tur="NOT", aciklama="not")
        cari_sil(c)
        self.client.force_login(su)
        for url in (reverse("core:banka_duzenle", args=[b.pk]), reverse("core:sevk_adresi_duzenle", args=[s.pk]),
                    reverse("core:yetkili_duzenle", args=[y.pk]), reverse("core:aktivite_duzenle", args=[a.pk])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in (reverse("core:banka_sil", args=[b.pk]), reverse("core:sevk_adresi_sil", args=[s.pk]),
                    reverse("core:yetkili_sil", args=[y.pk]), reverse("core:aktivite_sil", args=[a.pk])):
            self.assertEqual(self.client.post(url).status_code, 404, url)
