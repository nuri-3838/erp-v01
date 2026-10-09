"""FASON > Mutabakat: gönderilen ham profil (transfer + doğrudan alış), dönüşte tüketilen, geri alınan, fason depoda kalan (miktar + değer), gelen parça adetleri,
faturalanmış / faturası bekleyen dönüşler ve tutarları."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki
from core.services import fason_donus as fd
from core.services import fason_mutabakat as fm
from core.services.depo_transfer import depo_transferi_yap
from core.tests.test_fason_fatura import FaturaBase

D = Decimal
TARIH = date(2026, 10, 9)


class MutabakatTest(FaturaBase):
    def hazirla(self):
        """Ana depoya 12 boy (1200 TL) → fasona 10 boy gönder → 1 boy geri al → dönüşte 1 boy tüketilir (3 ana + 1 yan)."""
        self.profil_gir(12, 1200)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=date(2026, 10, 1), miktar=D("10"))
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.fdepo.pk, hedef_depo_id=self.depo.pk, tarih=date(2026, 10, 2), miktar=D("1"))
        self.fiyatlar(ana="10", yan="6")
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, irsaliye_no="IRS-9", satirlar=[(self.op.pk, "3")], onayla=True)

    def test_profil_gonderilen_tuketilen_kalan(self):
        self.hazirla()
        m = fm.mutabakat(self.salim)
        p = {x["stok"].kod: x for x in m["profiller"]}["PROFIL"]
        self.assertEqual((p["gonderilen"], p["geri"], p["tuketilen"], p["kalan"]), (D("10"), D("1"), D("1"), D("8")))
        self.assertEqual(p["fark"], D("0"))
        self.assertEqual(p["kalan_deger"], D("8") * D("100"))                    # ortalama 100 TL/boy
        self.assertEqual(m["kalan_deger"], D("800"))

    def test_elle_cikis_geri_alinana_sayilir_fark_sifir(self):
        self.hazirla()
        from core.models import StokHareket
        from core.services.hareket import hareket_ekle
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.fdepo.pk, tarih=TARIH, tur=StokHareket.Tur.CIKIS, miktar=D("1"))
        p = {x["stok"].kod: x for x in fm.mutabakat(self.salim)["profiller"]}["PROFIL"]
        self.assertEqual((p["kalan"], p["geri"], p["fark"]), (D("7"), D("2"), D("0")))          # elle çıkış da geri alınan (alış iadesi vb.)

    def test_gelen_parcalar_ve_bedeller(self):
        self.hazirla()
        m = fm.mutabakat(self.salim)
        parca = {x["stok"].kod: x for x in m["parcalar"]}
        self.assertEqual((parca["AYAK66"]["adet"], parca["AYAK66"]["tutar_try"]), (D("3"), D("30.00")))
        self.assertEqual((parca["AYAK55"]["adet"], parca["AYAK55"]["tutar_try"]), (D("1"), D("6.00")))
        self.assertEqual((m["toplam"]["try"], m["toplam"]["usd"]), (D("36.00"), D("0.90")))

    def test_faturali_ve_bekleyen(self):
        d1 = self.hazirla()
        self.profil_gir(5, 500)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=date(2026, 10, 3), miktar=D("5"))
        d2 = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "6")], onayla=True)      # 72 TL
        m = fm.mutabakat(self.salim)
        self.assertEqual((m["faturali"]["try"], m["bekleyen"]["try"], m["bekleyen"]["adet"]), (D("0"), D("108.00"), 2))
        f = self.fatura("40")                                                                                                         # d1 için, 4 TL farklı
        fd.faturaya_bagla(d1, f)
        m = fm.mutabakat(self.salim)
        self.assertEqual((m["faturali"]["try"], m["faturali"]["adet"]), (D("36.00"), 1))
        self.assertEqual((m["bekleyen"]["try"], m["bekleyen"]["adet"]), (D("72.00"), 1))
        self.assertEqual(m["toplam"]["try"], D("108.00"))
        self.assertEqual(m["fatura_farki"], D("4.00"))
        satir = {r["donus"].pk: r for r in m["donusler"]}
        self.assertTrue(satir[d1.pk]["faturali"])
        self.assertFalse(satir[d2.pk]["faturali"])

    def test_taslak_donus_hesaba_girmez(self):
        self.hazirla()
        fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        m = fm.mutabakat(self.salim)
        self.assertEqual((len(m["donusler"]), m["taslak_sayisi"], m["toplam"]["try"]), (1, 1, D("36.00")))

    def test_tarih_filtresi(self):
        self.hazirla()
        self.assertEqual(len(fm.mutabakat(self.salim, date(2026, 10, 10), None)["donusler"]), 0)
        self.assertEqual(len(fm.mutabakat(self.salim, None, date(2026, 10, 9))["donusler"]), 1)
        p = {x["stok"].kod: x for x in fm.mutabakat(self.salim, date(2026, 10, 2), None)["profiller"]}["PROFIL"]
        self.assertEqual(p["gonderilen"], D("0"))                                  # 01.10 transferi aralık dışı
        self.assertEqual(p["kalan"], D("8"))                                       # kalan her zaman güncel

    def test_fason_deposu_olmayan_cari(self):
        self.assertIsNone(fm.mutabakat(self.diger)["depo"])
        self.assertEqual(list(fm.fasoncular()), [self.salim])

    def test_ekran(self):
        self.hazirla()
        self.client.force_login(User.objects.create_superuser("fm_y", password="x"))
        r = self.client.get(reverse("core:fason_mutabakat"))                       # tek fasoncu → otomatik seçilir
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SALİM FASON")
        self.assertContains(r, "faturası bekleyen")
        self.assertContains(r, "36,00 TL")
        self.assertContains(r, "IRS-9")
        self.assertContains(r, "Gelen adet")
        r = self.client.get(reverse("core:fason_mutabakat"), {"cari": self.salim.pk, "baslangic": "2026-10-10"})
        self.assertContains(r, "Onaylı fason dönüş yok")
        u = User.objects.create_user("fm_u", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:fason_mutabakat")).status_code, 403)
        EkranYetki.objects.create(kullanici=u, ekran_kod="fason_mutabakat")
        self.assertEqual(self.client.get(reverse("core:fason_mutabakat")).status_code, 200)


class GonderilenKirilimTest(FaturaBase):
    """Gönderilen = fason depoya TÜM girişler (transfer + doğrudan alış + diğer); geri alınan = transfer + alış iadesi vb. çıkışlar (dönüş tüketimi hariç)."""

    def giris(self, miktar, tl, tarih, kaynak):
        from core.models import StokHareket
        from core.services.hareket import hareket_ekle
        return hareket_ekle(stok_id=self.profil.pk, depo_id=self.fdepo.pk, tarih=tarih, tur=StokHareket.Tur.GIRIS, miktar=D(str(miktar)),
                            giris_tutar_try=D(str(tl)), kaynak=kaynak)

    def p(self, **kw):
        return {x["stok"].kod: x for x in fm.mutabakat(self.salim, **kw)["profiller"]}["PROFIL"]

    def test_dogrudan_alisla_giren_stokta_fark_sifir(self):
        from core.models import StokHareket
        self.giris(12, 1200, date(2026, 9, 18), StokHareket.Kaynak.FATURA)             # tedarikçiden doğrudan fason depoya (irsaliye/fatura)
        self.fiyatlar(ana="10", yan="6")
        fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)     # 1 boy tüketilir
        p = self.p()
        self.assertEqual((p["transfer"], p["dogrudan"], p["gonderilen"], p["geri"], p["tuketilen"], p["kalan"]), (D("0"), D("12"), D("12"), D("0"), D("1"), D("11")))
        self.assertEqual(p["fark"], D("0"))

    def test_transfer_ve_dogrudan_karisik_alis_iadesi_geri_alinana_girer(self):
        from core.models import StokHareket
        from core.services.hareket import hareket_ekle
        self.profil_gir(5, 500)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=date(2026, 10, 1), miktar=D("5"))
        self.giris(10, 1000, date(2026, 9, 18), StokHareket.Kaynak.FATURA)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.fdepo.pk, hedef_depo_id=self.depo.pk, tarih=date(2026, 10, 2), miktar=D("2"))
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.fdepo.pk, tarih=date(2026, 10, 3), tur=StokHareket.Tur.CIKIS, miktar=D("3"),
                     kaynak=StokHareket.Kaynak.FATURA)                                  # alış iadesi
        self.fiyatlar(ana="10", yan="6")
        fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        p = self.p()
        self.assertEqual((p["transfer"], p["dogrudan"], p["gonderilen"]), (D("5"), D("10"), D("15")))
        self.assertEqual((p["geri"], p["tuketilen"], p["kalan"]), (D("5"), D("1"), D("9")))        # geri = 2 transfer + 3 iade; tüketim ayrı
        self.assertEqual(p["fark"], D("0"))

    def test_tarih_araliginda_acilis_bakiyesi(self):
        from core.models import StokHareket
        self.giris(10, 1000, date(2026, 9, 18), StokHareket.Kaynak.FATURA)
        self.giris(4, 400, date(2026, 10, 5), StokHareket.Kaynak.FATURA)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.fdepo.pk, hedef_depo_id=self.depo.pk, tarih=date(2026, 10, 6), miktar=D("2"))
        p = self.p(baslangic=date(2026, 10, 1), bitis=date(2026, 10, 31))
        self.assertEqual((p["acilis"], p["gonderilen"], p["geri"], p["kalan"], p["fark"]), (D("10"), D("4"), D("2"), D("12"), D("0")))
        p = self.p(baslangic=date(2026, 10, 6), bitis=date(2026, 10, 31))
        self.assertEqual((p["acilis"], p["gonderilen"], p["geri"], p["kalan"], p["fark"]), (D("14"), D("0"), D("2"), D("12"), D("0")))
        p = self.p(baslangic=date(2026, 9, 1), bitis=date(2026, 9, 30))                   # kalan bitiş tarihi itibarıyla
        self.assertEqual((p["acilis"], p["gonderilen"], p["kalan"], p["fark"]), (D("0"), D("10"), D("10"), D("0")))
        self.assertEqual(self.p()["acilis"], D("0"))

    def test_ekran_kirilim_ve_acilis_sutunu(self):
        from core.models import StokHareket
        self.giris(12, 1200, date(2026, 9, 18), StokHareket.Kaynak.FATURA)
        self.client.force_login(User.objects.create_superuser("fm_y2", password="x"))
        r = self.client.get(reverse("core:fason_mutabakat"))
        self.assertContains(r, "Açılış")
        self.assertContains(r, "Doğrudan alış 12")
        self.assertNotContains(r, "rozet-pasif\" title=\"Açılış")                          # fark 0 → rozet yok
