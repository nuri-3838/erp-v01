"""0256 tek seferlik veri migration'ı: görev "MESLEK ADI - 9622.02" → meslek_kodu + meslek_adi (yalnız kodu boş kayıtlar), görev değişmez, desene uymayanlara
dokunulmaz ve raporlanır, tekrar çalıştırmak güvenli, "Meslek kodu eksik" uyarısı kalkar."""
import importlib
from contextlib import redirect_stdout
from datetime import date
from io import StringIO

from django.apps import apps
from django.test import TestCase

from core.models import Personel
from core.services import ise_giris as ig
from core.services import ise_giris_muhasebe as mu
from core.services.personel import personel_olustur

GOREV = "BEDEN İŞÇİSİ (GENEL) - 9622.02"


def calistir():
    mig = importlib.import_module("core.migrations.0256_personel_gorevden_meslek_kodu_veri")
    cikti = StringIO()
    with redirect_stdout(cikti):
        mig.doldur(apps, None)
    return cikti.getvalue()


class GorevdenMeslekKoduTest(TestCase):
    def kur(self, ad, gorev, **ek):
        p = personel_olustur(ad=ad, soyad="deneme", ise_giris_tarihi=date(2026, 11, 2), gorev=gorev)
        if ek:
            Personel.objects.filter(pk=p.pk).update(**ek)                                       # servis doğrulamasını atlayan eski/özel durum kurulumu
        p.refresh_from_db()
        return p

    def test_kod_ve_ad_ayrilir_gorev_ayni_kalir(self):
        p = self.kur("a", GOREV)
        guncelleme = p.updated_at
        cikti = calistir()
        p.refresh_from_db()
        self.assertEqual((p.meslek_kodu, p.meslek_adi), ("9622.02", "BEDEN İŞÇİSİ (GENEL)"))
        self.assertEqual(p.gorev, GOREV)                                                        # görev alanı olduğu gibi
        self.assertEqual(p.updated_at, guncelleme)                                              # updated_at değişmez
        self.assertIn("1 personel", cikti)

    def test_tire_bosluk_varyantlari(self):
        for ad, gorev in (("b", "KAYNAKÇI-7212.01"), ("c", "KAYNAKÇI  –  7212.01"), ("d", "KAYNAKÇI — 7212.01 ")):
            self.kur(ad, gorev)
        calistir()
        for ad in ("B", "C", "D"):
            p = Personel.objects.get(ad=ad)
            self.assertEqual((p.meslek_kodu, p.meslek_adi), ("7212.01", "KAYNAKÇI"), ad)

    def test_dolu_kod_ve_dolu_ad_korunur(self):
        dolu = self.kur("e", GOREV, meslek_kodu="1111.11", meslek_adi="ELLE GİRİLMİŞ")
        yalniz_ad = self.kur("f", GOREV, meslek_adi="ELLE AD")
        calistir()
        dolu.refresh_from_db()
        yalniz_ad.refresh_from_db()
        self.assertEqual((dolu.meslek_kodu, dolu.meslek_adi), ("1111.11", "ELLE GİRİLMİŞ"))                 # dolu kod: hiç dokunulmaz
        self.assertEqual((yalniz_ad.meslek_kodu, yalniz_ad.meslek_adi), ("9622.02", "ELLE AD"))            # kod yazılır, mevcut ad korunur

    def test_desene_uymayanlara_dokunulmaz_ve_raporlanir(self):
        uymayanlar = [self.kur("g", "BEDEN İŞÇİSİ"), self.kur("h", ""), self.kur("i", "KAYNAKÇI - 96.02"), self.kur("j", "KAYNAKÇI - 9622"),
                      self.kur("k", "9622.02 - KAYNAKÇI"), self.kur("l", "KAYNAKÇI 9622.02")]
        cikti = calistir()
        for p in uymayanlar:
            p.refresh_from_db()
            self.assertEqual((p.meslek_kodu, p.meslek_adi), ("", ""), p.ad)
            self.assertIn(f"personel #{p.pk} ", cikti)
        self.assertEqual(cikti.count("UYARI (0256)"), len(uymayanlar))
        self.assertNotIn("BİLGİ (0256)", cikti)

    def test_tekrar_calistirmak_guvenli(self):
        p = self.kur("m", GOREV)
        calistir()
        p.refresh_from_db()
        ilk = (p.meslek_kodu, p.meslek_adi, p.updated_at)
        cikti = calistir()
        p.refresh_from_db()
        self.assertEqual((p.meslek_kodu, p.meslek_adi, p.updated_at), ilk)
        self.assertNotIn("1 personel", cikti)

    def test_bos_veritabaninda_sessiz(self):
        self.assertEqual(calistir(), "")

    def test_meslek_kodu_eksik_uyarisi_kalkar(self):
        p = self.kur("n", GOREV)
        p.tc_kimlik_no = "10000000146"
        eksik = lambda: mu.hazirlik(Personel.objects.get(pk=p.pk), ig.kayit_oku(p), bugun=date(2026, 10, 30))["eksikler"]
        self.assertIn("Meslek kodu", eksik())
        calistir()
        self.assertNotIn("Meslek kodu", eksik())
