"""0257 tek seferlik veri migration'ı: görevdeki "- NNNN.NN" eki yalnız meslek_kodu dolu + kod eşleşen kayıtlarda temizlenir; farklı kod/boş meslek kodu/desensiz görev
korunur, tekrar çalıştırmak güvenli, meslek kodu/adı ve updated_at değişmez; muhasebe formundaki görev temiz görünür."""
import importlib
from contextlib import redirect_stdout
from datetime import date
from io import StringIO

from django.apps import apps
from django.test import TestCase

from core.models import Personel
from core.services import ise_giris as ig
from core.services import ise_giris_muhasebe as mu
from core.services.firma import firma_bilgisi_getir
from core.services.personel import personel_olustur

GOREV = "BEDEN İŞÇİSİ (GENEL) - 9622.02"


def calistir():
    mig = importlib.import_module("core.migrations.0257_personel_gorev_kod_eki_temizle_veri")
    cikti = StringIO()
    with redirect_stdout(cikti):
        mig.temizle(apps, None)
    return cikti.getvalue()


class GorevKodEkiTemizleTest(TestCase):
    def kur(self, ad, gorev, kod="9622.02", meslek_adi="BEDEN İŞÇİSİ (GENEL)"):
        p = personel_olustur(ad=ad, soyad="deneme", ise_giris_tarihi=date(2026, 11, 2), gorev=gorev)
        Personel.objects.filter(pk=p.pk).update(meslek_kodu=kod, meslek_adi=meslek_adi)         # 0256 sonrası durum
        p.refresh_from_db()
        return p

    def test_eslesen_kod_eki_temizlenir_diger_alanlar_ayni(self):
        p = self.kur("a", GOREV)
        guncelleme = p.updated_at
        cikti = calistir()
        p.refresh_from_db()
        self.assertEqual(p.gorev, "BEDEN İŞÇİSİ (GENEL)")
        self.assertEqual((p.meslek_kodu, p.meslek_adi, p.updated_at), ("9622.02", "BEDEN İŞÇİSİ (GENEL)", guncelleme))
        self.assertIn("1 personel", cikti)
        self.assertNotIn("UYARI", cikti)

    def test_tire_ve_bosluk_varyantlari(self):
        for ad, gorev in (("b", "KAYNAKÇI-9622.02"), ("c", "KAYNAKÇI  –  9622.02"), ("d", "KAYNAKÇI — 9622.02 ")):
            self.kur(ad, gorev)
        calistir()
        for ad in ("B", "C", "D"):
            self.assertEqual(Personel.objects.get(ad=ad).gorev, "KAYNAKÇI", ad)

    def test_farkli_kod_dokunulmaz_ve_raporlanir(self):
        p = self.kur("e", "KAYNAKÇI - 7212.01", kod="9622.02")
        cikti = calistir()
        p.refresh_from_db()
        self.assertEqual(p.gorev, "KAYNAKÇI - 7212.01")
        self.assertIn(f"personel #{p.pk} ", cikti)
        self.assertEqual(cikti.count("UYARI (0257)"), 1)
        self.assertNotIn("BİLGİ (0257)", cikti)

    def test_meslek_kodu_bos_veya_gorev_desensiz_dokunulmaz(self):
        bos_kod = self.kur("f", GOREV, kod="")
        desensiz = self.kur("g", "BEDEN İŞÇİSİ (GENEL)")
        bos_gorev = self.kur("h", "")
        cikti = calistir()
        for p, beklenen in ((bos_kod, GOREV), (desensiz, "BEDEN İŞÇİSİ (GENEL)"), (bos_gorev, "")):
            p.refresh_from_db()
            self.assertEqual(p.gorev, beklenen, p.ad)
        self.assertEqual(cikti, "")                                                             # hiçbiri raporlanmaz (kod yok / desen yok)

    def test_tekrar_calistirmak_guvenli(self):
        p = self.kur("i", GOREV)
        calistir()
        p.refresh_from_db()
        ilk = (p.gorev, p.meslek_kodu, p.updated_at)
        cikti = calistir()
        p.refresh_from_db()
        self.assertEqual((p.gorev, p.meslek_kodu, p.updated_at), ilk)
        self.assertEqual(cikti, "")

    def test_bos_veritabaninda_sessiz(self):
        self.assertEqual(calistir(), "")

    def test_muhasebe_formunda_gorev_temiz(self):
        p = self.kur("j", GOREV)

        def deger():
            kayit = ig.kayit_oku(p)
            return {e: d for _x, a in mu.bolumler(Personel.objects.get(pk=p.pk), firma_bilgisi_getir(), kayit) for e, d, _z in a}
        Personel.objects.filter(pk=p.pk).update(departman="ÜRETİM")
        self.assertEqual(deger()["Görev / Departman"], "BEDEN İŞÇİSİ (GENEL) - 9622.02 / ÜRETİM")
        calistir()
        s = deger()
        self.assertEqual((s["Görev / Departman"], s["Meslek kodu"], s["Meslek adı"]), ("BEDEN İŞÇİSİ (GENEL) / ÜRETİM", "9622.02", "BEDEN İŞÇİSİ (GENEL)"))
