"""Kur çekme güvenliği: (1) YARIN satırı kuralı — bugünün yayını varsa onunla; yayın yoksa ancak 16:00 sonrası son yayınla; iş günü yayın
gelmemişse 16:00 öncesi yazılmaz (yer tutucu yok). (2) Bir tarihin kuru değişecekse ve o tarihli fiş/fatura varsa üzerine YAZILMAZ,
``KurUyarisi`` açılır; Kurlar ekranı ve pano uyarıyı gösterir, "Gördüm" kapatır. Belgesiz tarih üzerine yazılır."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Kur, KurUyarisi, YevmiyeFisi
from core.services import tcmb
from core.services.tcmb import kurlari_guncelle, yarin_kuru_sec

D = Decimal


def _kur(usd):
    return {"USD": {"alis": D(usd), "satis": D(usd) + 1, "efektif_alis": D(usd), "efektif_satis": D(usd) + 1},
            "EUR": {"alis": D(usd) + 10, "satis": D(usd) + 11, "efektif_alis": D(usd) + 10, "efektif_satis": D(usd) + 11},
            "GBP": {"alis": D(usd) + 20, "satis": D(usd) + 21, "efektif_alis": D(usd) + 20, "efektif_satis": D(usd) + 21}}


class YarinKuraliTest(TestCase):
    def test_yarin_kuru_sec(self):
        pzt = datetime.date(2026, 10, 12)
        bugun, son = _kur("49"), _kur("48")
        self.assertEqual(yarin_kuru_sec(pzt, datetime.time(16, 5), bugun, son)[0], bugun)                 # yayın var → bugünün yayını
        self.assertEqual(yarin_kuru_sec(pzt, datetime.time(9, 0), bugun, son)[0], bugun)                  # saat fark etmez
        self.assertIsNone(yarin_kuru_sec(pzt, datetime.time(15, 59), None, son)[0])                        # iş günü, yayın yok, 16:00 öncesi → YAZILMAZ
        self.assertEqual(yarin_kuru_sec(pzt, datetime.time(16, 0), None, son)[0], son)                     # 16:00 sonrası → son yayın
        self.assertEqual(yarin_kuru_sec(datetime.date(2026, 10, 10), datetime.time(18, 0), None, son)[0], son)   # cumartesi 18:00
        self.assertIsNone(yarin_kuru_sec(pzt, datetime.time(18, 0), None, None)[0])                        # hiç yayın yok

    def _komut(self, bitis_yayin, son_yayin, saat):
        yazilan = []

        def sahte_guncelle(bas, bit, **kw):
            return {"yayin": 1, "yazilan": 1, "atlanan": 0, "korunan": 0, "son_yayin": son_yayin, "bitis_yayin": bitis_yayin}

        def sahte_yaz(hedef, kurlar, kullanici, kaynak="elle"):
            yazilan.append((hedef, kurlar, kaynak))
            return "yazildi"

        with (patch("core.management.commands.kur_cek.kurlari_guncelle", side_effect=sahte_guncelle),
              patch("core.management.commands.kur_cek._yaz", side_effect=sahte_yaz),
              patch("core.management.commands.kur_cek.tr_simdi", return_value=saat)):
            call_command("kur_cek")
        return yazilan

    def test_komut_yarin_kurali(self):
        from core.management.commands.kur_cek import tr_bugun
        yarin = tr_bugun() + datetime.timedelta(days=1)
        self.assertEqual(self._komut(_kur("49"), _kur("48"), datetime.time(9, 0)), [(yarin, _kur("49"), "cron")])
        self.assertEqual(self._komut(None, _kur("48"), datetime.time(15, 59)), [])                           # yayın yok, erken → yarın yok
        self.assertEqual(self._komut(None, _kur("48"), datetime.time(16, 0)), [(yarin, _kur("48"), "cron")])


class UzerineYazmaKorumasiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("kkyon", password="x")
        cls.t = datetime.date(2026, 10, 13)

    def cek(self, usd):
        return kurlari_guncelle(self.t, self.t, cekici=lambda gun: _kur(usd) if gun == self.t - datetime.timedelta(days=1) else None,
                                kullanici=self.yon)

    def test_belgesiz_tarih_uzerine_yazilir(self):
        self.cek("48")
        self.assertEqual(Kur.objects.get(tarih=self.t).usd_alis, D("48"))
        ozet = self.cek("49")
        self.assertEqual((Kur.objects.get(tarih=self.t).usd_alis, ozet["korunan"], KurUyarisi.objects.count()), (D("49"), 0, 0))
        ozet = self.cek("49")                                                                             # aynı değer: yazıldı sayılır, uyarı yok
        self.assertEqual((ozet["yazilan"], ozet["korunan"]), (1, 0))

    def test_belgeli_tarih_korunur_ve_uyari(self):
        self.cek("48")
        YevmiyeFisi.objects.create(yil=2026, fis_no=99001, tarih=self.t, kur_usd=D("48"))
        ozet = self.cek("49")
        self.assertEqual((Kur.objects.get(tarih=self.t).usd_alis, ozet["korunan"], ozet["yazilan"]), (D("48"), 1, 0))
        u = KurUyarisi.objects.get()
        self.assertEqual((u.tarih, u.belge_sayisi, u.kaynak, D(u.eski["usd_alis"]), D(u.yeni["usd_alis"]), u.gorulme_zamani), (self.t, 1, "elle", D("48"), D("49"), None))
        self.cek("50")                                                                                    # tekrar: aynı uyarı güncellenir
        self.assertEqual((KurUyarisi.objects.count(), D(KurUyarisi.objects.get().yeni["usd_alis"])), (1, D("50")))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "1</strong> kur uyarısı")
        r = self.client.get(reverse("core:kurlar"))
        self.assertContains(r, "13.10.2026")
        self.assertContains(r, "üzerine YAZILMADI")
        self.assertContains(r, "usd_alis: 48.000000 → 50")
        r = self.client.post(reverse("core:kurlar"), {"eylem": "uyari_gordum", "uyari": u.pk})
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertIsNotNone(u.gorulme_zamani)
        self.assertEqual(Kur.objects.get(tarih=self.t).usd_alis, D("48"))                                   # kur hâlâ değişmedi
        r = self.client.get(reverse("core:pano"))
        self.assertNotContains(r, "kur uyarısı var:")                                                        # pano uyarısı kalktı

    def test_elle_cekme_ekrani_korunan_mesaji(self):
        self.cek("48")
        YevmiyeFisi.objects.create(yil=2026, fis_no=99002, tarih=self.t, kur_usd=D("48"))
        self.client.force_login(self.yon)
        with patch.object(tcmb, "tcmb_gunluk_cek", side_effect=lambda gun: _kur("49") if gun == self.t - datetime.timedelta(days=1) else None):
            r = self.client.post(reverse("core:kurlar"), {"baslangic": "2026-10-13", "bitis": "2026-10-13"}, follow=True)
        self.assertTrue(any("DEĞİŞMEDİ" in str(m) for m in r.context["messages"]))
        self.assertEqual(Kur.objects.get(tarih=self.t).usd_alis, D("48"))
