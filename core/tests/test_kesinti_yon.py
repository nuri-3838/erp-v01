"""Kesinti / Masraf: YÖN seçimi (Bakiyeyi kapat / Cari borç / Cari alacak), kaydetmeden önizleme ve imzalı onay.

Vaka (Dibas, 2026-10-14): 320 tedarikçide FAZLA ÖDEME → cari bize borçlu (BORÇ bakiye). Eski ekran hep Cari BORÇ yazdığından bakiyeyi
kapatmak yerine ikiye katladı. Kapatma: Cari ALACAK / Gider BORÇ."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Cari, EkranYetki, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import cari_kesinti as ck
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.kesinti_yardim import onayli_post
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    q = YevmiyeSatir.objects.filter(hesap_id=kod, silindi=False, fis__silindi=False)
    return sum((s.borc - s.alacak for s in q), Dc("0"))


class KesintiYonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (1, 2, 10, 11, 14):
            Kur.objects.create(tarih=D(2026, 10, g), usd_alis=Dc("40"))
        for kod, ad in (("320.50.0010", "DİBAS"), ("320.01", "SATICI"), ("120.01", "MÜŞTERİ"), ("320.02", "FORMAL"), ("102.01", "BANKA"), ("600.01", "SATIŞ"),
                        ("500.10.0001", "ORTAK"), ("335.10.0001", "PERSONEL")):
            _hesap(kod, ad)
        _hesap("770.05", "FİNANSMAN GİDERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.03", "BANKA MASRAF GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        cls.dibas = Cari.objects.create(kod="D1", unvan="DİBAS", muhasebe_kodu="320.50.0010")
        cls.sat = Cari.objects.create(kod="S1", unvan="SATICI", muhasebe_kodu="320.01")
        cls.mus = Cari.objects.create(kod="M1", unvan="MÜŞTERİ", muhasebe_kodu="120.01")
        cls.formal = Cari.objects.create(kod="F1", unvan="FORMAL", muhasebe_kodu="320.02", para_birimi="USD")
        cls.ortak = Cari.objects.create(kod="O1", unvan="ORTAK", muhasebe_kodu="500.10.0001")
        cls.su = User.objects.create_superuser("ky", password="x")

    def _fazla_odeme(self, tutar="150.80"):
        """Tedarikçiye fazla ödeme: Cari BORÇ / Banka ALACAK → cari bakiyesi BORÇ."""
        fis_olustur(tarih=D(2026, 10, 10), satirlar=[SatirGirdi(hesap_kodu="320.50.0010", taraf="B", islem_tutari=tutar),
                                                     SatirGirdi(hesap_kodu="102.01", taraf="A", islem_tutari=tutar)])

    def _s(self, fis):
        return {(s.hesap_id, "B" if s.borc else "A"): s.borc or s.alacak for s in fis.satirlar.filter(silindi=False)}

    # ---- servis: yön ----
    def test_dibas_borc_bakiyeli_tedarikci_kapat_cari_alacak_gider_borc(self):
        self._fazla_odeme()
        self.assertEqual(_bak("320.50.0010"), Dc("150.80"))
        f = ck.kesinti_olustur(cari=self.dibas, tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT,
                               aciklama="DİBAS PERDE - 6 TAKSİT VADE FARKI KAPATMA", kullanici=self.su)
        self.assertEqual(self._s(f), {("770.05", "B"): Dc("150.80"), ("320.50.0010", "A"): Dc("150.80")})
        self.assertEqual((_bak("320.50.0010"), _bak("770.05")), (Dc("0.00"), Dc("150.80")))

    def test_alacak_bakiyeli_tedarikci_kapat_cari_borc(self):
        fis_olustur(tarih=D(2026, 10, 1), satirlar=[SatirGirdi(hesap_kodu="600.01", taraf="B", islem_tutari="500"),
                                                    SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari="500")])
        f = ck.kesinti_olustur(cari=self.sat, tarih=D(2026, 10, 14), tutar="100", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(self._s(f), {("770.05", "A"): Dc("100.00"), ("320.01", "B"): Dc("100.00")})

    def test_musteri_alacak_bakiyeli_kapat_cari_borc_borc_bakiyeli_cari_alacak(self):
        fis_olustur(tarih=D(2026, 10, 1), satirlar=[SatirGirdi(hesap_kodu="102.01", taraf="B", islem_tutari="50"),
                                                    SatirGirdi(hesap_kodu="120.01", taraf="A", islem_tutari="50")])   # müşteri fazla ödedi → ALACAK bakiye
        f = ck.kesinti_olustur(cari=self.mus, tarih=D(2026, 10, 14), tutar="10", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(self._s(f), {("770.03", "A"): Dc("10.00"), ("120.01", "B"): Dc("10.00")})
        fis_olustur(tarih=D(2026, 10, 2), satirlar=[SatirGirdi(hesap_kodu="120.01", taraf="B", islem_tutari="500"),
                                                    SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari="500")])  # şimdi BORÇ bakiye
        f = ck.kesinti_olustur(cari=self.mus, tarih=D(2026, 10, 14), tutar="10", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(self._s(f), {("770.03", "B"): Dc("10.00"), ("120.01", "A"): Dc("10.00")})

    def test_bakiye_sifirken_kapat_cari_turunun_varsayilani(self):
        self.assertEqual(self._s(ck.kesinti_olustur(cari=self.sat, tarih=D(2026, 10, 14), tutar="1", yon_secimi=ck.YON_KAPAT)),
                         {("770.03", "A"): Dc("1.00"), ("320.01", "B"): Dc("1.00")})
        self.assertEqual(self._s(ck.kesinti_olustur(cari=self.mus, tarih=D(2026, 10, 14), tutar="1", yon_secimi=ck.YON_KAPAT)),
                         {("770.03", "B"): Dc("1.00"), ("120.01", "A"): Dc("1.00")})

    def test_acik_yon_secimi_bakiyeden_bagimsiz(self):
        self._fazla_odeme()
        f = ck.kesinti_olustur(cari=self.dibas, tarih=D(2026, 10, 14), tutar="10", yon_secimi=ck.YON_CARI_BORC)
        self.assertEqual(self._s(f), {("770.03", "A"): Dc("10.00"), ("320.50.0010", "B"): Dc("10.00")})
        f = ck.kesinti_olustur(cari=self.sat, tarih=D(2026, 10, 14), tutar="10", yon_secimi=ck.YON_CARI_ALACAK)
        self.assertEqual(self._s(f), {("770.03", "B"): Dc("10.00"), ("320.01", "A"): Dc("10.00")})
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.sat, tarih=D(2026, 10, 14), tutar="10", yon_secimi="x")

    def test_yon_verilmezse_eski_davranis_ve_ortak_carisinda_secim_yok(self):
        self._fazla_odeme()
        f = ck.kesinti_olustur(cari=self.dibas, tarih=D(2026, 10, 14), tutar="10")                  # eski kural: 320 → Cari BORÇ
        self.assertEqual(self._s(f), {("770.03", "A"): Dc("10.00"), ("320.50.0010", "B"): Dc("10.00")})
        f = ck.kesinti_olustur(cari=self.ortak, tarih=D(2026, 10, 14), tutar="10", yon_secimi=ck.YON_CARI_BORC)   # 500: seçim yok sayılır
        self.assertEqual(self._s(f), {("770.03", "B"): Dc("10.00"), ("500.10.0001", "A"): Dc("10.00")})

    def test_doviz_cari_havuz_bazinda_kapatma(self):
        # USD havuzu BORÇ bakiyeli (+100 USD), TL havuzu ALACAK bakiyeli (−300 TL)
        fis_olustur(tarih=D(2026, 10, 10), satirlar=[SatirGirdi(hesap_kodu="320.02", taraf="B", islem_tutari="100", islem_pb="USD", islem_kuru="40"),
                                                     SatirGirdi(hesap_kodu="102.01", taraf="A", islem_tutari="4000")])
        fis_olustur(tarih=D(2026, 10, 11), satirlar=[SatirGirdi(hesap_kodu="600.01", taraf="B", islem_tutari="300"),
                                                     SatirGirdi(hesap_kodu="320.02", taraf="A", islem_tutari="300")])
        self.assertEqual((ck.havuz_net(self.formal, "USD"), ck.havuz_net(self.formal, "TRY")), (Dc("100.00"), Dc("-300.00")))
        usd = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 10, 14), tutar="10", para_birimi="USD", kur="40", yon_secimi=ck.YON_KAPAT)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in usd.satirlar.filter(silindi=False)}, {("770.03", "B"), ("320.02", "A")})
        tl = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 10, 14), tutar="10", para_birimi="TRY", yon_secimi=ck.YON_KAPAT)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in tl.satirlar.filter(silindi=False)}, {("770.03", "A"), ("320.02", "B")})

    def test_duzenleme_fisin_gercek_yonunu_korur(self):
        self._fazla_odeme()
        f = ck.kesinti_olustur(cari=self.dibas, tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_CARI_ALACAK)
        self.assertEqual(ck.duzenleme_bilgisi(f, self.dibas)["yon_secimi"], ck.YON_CARI_ALACAK)
        ck.kesinti_guncelle(fis=f, cari=self.dibas, tarih=D(2026, 10, 14), tutar="100", gider_kodu="770.05")     # yön verilmedi → korunur
        self.assertEqual(self._s(f), {("770.05", "B"): Dc("100.00"), ("320.50.0010", "A"): Dc("100.00")})
        # düzenlerken 'kapat': kendi satırı bakiyeden çıkarılır (150,80 − 0 üzerinden değerlendirilir)
        ck.kesinti_guncelle(fis=f, cari=self.dibas, tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(_bak("320.50.0010"), Dc("0.00"))

    # ---- önizleme ----
    def test_onizleme_hicbir_sey_yazmaz_ve_kayitla_ayni_satirlar(self):
        self._fazla_odeme()
        once = (YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count())
        alan = dict(tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT, aciklama="x")
        with CaptureQueriesContext(connection) as c:
            o = ck.onizle(cari=self.dibas, **alan)
        self.assertFalse([q for q in c.captured_queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))])
        self.assertEqual((YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count()), once)
        self.assertEqual({(r["hesap_kodu"], r["taraf"]): r["borc"] or r["alacak"] for r in o["satirlar"]},
                         {("770.05", "B"): Dc("150.80"), ("320.50.0010", "A"): Dc("150.80")})
        self.assertEqual((o["toplam_borc"], o["toplam_alacak"]), (Dc("150.80"), Dc("150.80")))
        self.assertEqual((o["bakiye_once"], o["bakiye_sonra"], o["uyarilar"]), (Dc("150.80"), Dc("0.00"), []))
        f = ck.kesinti_olustur(cari=self.dibas, **alan)
        self.assertEqual(self._s(f), {(r["hesap_kodu"], r["taraf"]): r["borc"] or r["alacak"] for r in o["satirlar"]})
        self.assertEqual(f.aciklama, o["aciklama"])

    def test_uyari_tutar_bakiyeyi_asar_ve_yon_bakiyeyi_buyutur(self):
        self._fazla_odeme()
        asan = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="200", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(len(asan["uyarilar"]), 1)
        self.assertIn("aşıyor", asan["uyarilar"][0])
        self.assertEqual((asan["bakiye_sonra"], asan["bakiye_sonra_taraf"]), (Dc("-49.20"), "ALACAK"))
        buyuten = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="10", gider_kodu="770.05", yon_secimi=ck.YON_CARI_BORC)
        self.assertIn("büyütür", buyuten["uyarilar"][0])
        tam = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        self.assertEqual(tam["uyarilar"], [])
        sifir = ck.onizle(cari=self.sat, tarih=D(2026, 10, 14), tutar="1", yon_secimi=ck.YON_KAPAT)
        self.assertIn("bakiye yok", sifir["uyarilar"][0])

    def test_imza_satirlar_degisince_degisir(self):
        self._fazla_odeme()
        a = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="100", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        b = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="100", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        c = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="101", gider_kodu="770.05", yon_secimi=ck.YON_KAPAT)
        d = ck.onizle(cari=self.dibas, tarih=D(2026, 10, 14), tutar="100", gider_kodu="770.05", yon_secimi=ck.YON_CARI_BORC)
        self.assertEqual(a["imza"], b["imza"])
        self.assertEqual(len({a["imza"], c["imza"], d["imza"]}), 3)

    # ---- ekran ----
    def _ekran(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        return reverse("core:cari_kesinti_ekle", args=[self.dibas.pk])

    def test_ekran_once_onizleme_sonra_onay(self):
        self._fazla_odeme()
        url = self._ekran()
        r = self.client.get(url)
        self.assertContains(r, 'name="yon"')
        self.assertContains(r, "Bakiyeyi kapat")
        self.assertContains(r, "150,80")                                               # mevcut bakiye gösterilir
        veri = {"tarih": "2026-10-14", "tutar": "150,80", "gider": "770.05", "yon": "kapat", "aciklama": "dibas"}
        # 1) onay olmadan: yalnız önizleme, KAYIT YOK
        n = YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").count()
        r = self.client.post(url, veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fiş önizlemesi")
        self.assertContains(r, "Onayla ve Kaydet")
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").count(), n)
        imza = r.context["onizleme"]["imza"]
        # 2) yanlış imza → kayıt yok, yeni önizleme
        r = self.client.post(url, {**veri, "onayla": "1", "imza": "yanlis"})
        self.assertContains(r, "önizlemeden sonra değişti")
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").count(), n)
        # 3) onaydan sonra alan değişti (imza eski) → kayıt yok
        r = self.client.post(url, {**veri, "tutar": "100,00", "onayla": "1", "imza": imza})
        self.assertContains(r, "önizlemeden sonra değişti")
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").count(), n)
        # 4) doğru imza → kayıt
        r = self.client.post(url, {**veri, "onayla": "1", "imza": imza})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.dibas.pk]))
        f = YevmiyeFisi.objects.get(kaynak="CARI_KESINTI")
        self.assertEqual(self._s(f), {("770.05", "B"): Dc("150.80"), ("320.50.0010", "A"): Dc("150.80")})
        self.assertEqual(_bak("320.50.0010"), Dc("0.00"))

    def test_ekran_asim_uyarisi_gorunur(self):
        self._fazla_odeme()
        url = self._ekran()
        r = self.client.post(url, {"tarih": "2026-10-14", "tutar": "200,00", "gider": "770.05", "yon": "kapat"})
        self.assertContains(r, "mevcut bakiyeyi aşıyor")

    def test_ekran_duzenle_onizleme_ve_yon(self):
        self._fazla_odeme()
        f = ck.kesinti_olustur(cari=self.dibas, tarih=D(2026, 10, 14), tutar="150,80", gider_kodu="770.05", yon_secimi=ck.YON_CARI_ALACAK)
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        d = reverse("core:cari_kesinti_duzenle", args=[self.dibas.pk, f.pk])
        r = self.client.get(d)
        self.assertEqual(r.context["form"].initial["yon"], ck.YON_CARI_ALACAK)             # fişin gerçek yönü
        veri = {"tarih": "2026-10-14", "tutar": "120,00", "gider": "770.05", "yon": "cari_alacak", "aciklama": ""}
        r = self.client.post(d, veri)
        self.assertContains(r, "Fiş önizlemesi")
        self.assertEqual(self._s(YevmiyeFisi.objects.get(pk=f.pk))[("770.05", "B")], Dc("150.80"))     # henüz değişmedi
        r = onayli_post(self.client, d, veri)
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.dibas.pk]))
        self.assertEqual(self._s(YevmiyeFisi.objects.get(pk=f.pk)), {("770.05", "B"): Dc("120.00"), ("320.50.0010", "A"): Dc("120.00")})

    def test_ortak_carisinda_yon_alani_yok(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[self.ortak.pk]))
        self.assertNotContains(r, 'name="yon"')
        self.assertContains(r, "Gider BORÇ / Ortak ALACAK")
