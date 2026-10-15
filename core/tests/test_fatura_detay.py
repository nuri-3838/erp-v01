"""Faturalar › Fatura DETAY ekranı yeniden tasarımı (2026-10-15, 3/3): iki kolon (sol bilgi, sağ PDF; ek yoksa 'PDF ekle'), başlık kartı (tip rozeti, faturasız rozeti, ödeme durumu), kalem tablosu +
toplam kutusu (KDV kırılımı, döviz TL karşılığı, yalnız KDV'de matrah '—'), ÖDEMELER kartı (listeyle aynı FIFO), MUHASEBE kartı (fiş, kur farkı, satış maliyet fişi, bağlı kayıtlar), boş bölüm yok,
sayfa açılırken veritabanına YAZMA yok."""
import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import connection
from django.urls import reverse

from core.models import Fatura, FaturaEk, FaturaTipi, YevmiyeSatir
from core.services import fatura_odeme
from core.services.fatura import fatura_olustur, fatura_taslak_olustur
from core.tests import test_fatura_form_fis_karsilastirma as kt
from core.tests import test_fatura_liste as tfl
from core.tests.ik_yardimci import PDF_BAYT

D = datetime.date
Dc = Decimal


def yazma_engelli(test, url):
    """GET sırasında INSERT/UPDATE/DELETE yapılırsa test düşer; (yanıt) döner."""
    def engel(execute, sql, params, many, context):
        if sql.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE"):
            raise AssertionError("Sayfa açılırken veritabanına YAZMA denendi: " + sql[:120])
        return execute(sql, params, many, context)
    with connection.execute_wrapper(engel):
        return test.client.get(url)


class DetayTemel(tfl.ListeTaban):
    def detay(self, f):
        self.client.force_login(self.su)
        return yazma_engelli(self, reverse("core:fatura_detay", args=[f.pk]))


class BaslikVeKalemTest(DetayTemel):
    def test_sayfa_acilir_baslik_kalem_ve_toplam_kutusu(self):
        f = self.a_fatura(no="DT-1")
        r = self.detay(f)
        self.assertEqual(r.status_code, 200)
        for metin in ("DT-1", "Alış", "TEDARİKÇİ A", "Fatura Tarihi", "Para Birimi", "Satır toplamı", "Matrah", "Birim fiyat", "Genel toplam (KDV dahil)", "Ödenecek (cari tutarı)"):
            self.assertContains(r, metin)
        self.assertContains(r, reverse("core:cari_detay", args=[self.tedarikci.pk]))                   # cari detayına link
        k = r.context["d"]["kutu"]
        self.assertEqual([(x["etiket"], x["tutar"]) for x in k["satirlar"]],
                         [("Matrah", Dc("1000.00")), ("KDV %20", Dc("200.00")), ("Genel toplam (KDV dahil)", Dc("1200.00")), ("Ödenecek (cari tutarı)", Dc("1200.00"))])
        kal = r.context["d"]["kalemler"][0]
        self.assertEqual((kal["matrah"], kal["kdv"], kal["toplam"], kal["birim"], kal["kdv_oran"]), (Dc("1000.00"), Dc("200.00"), Dc("1200.00"), "AD", Dc("20")))

    def test_ham_alan_adi_gorunmez(self):
        r = self.detay(self.a_fatura(no="DT-2"))
        for ham in ("sahsi_alis", "gv_stopaj", "yalniz_kdv", "taslak_kur", "None", "ONAYLI"):
            self.assertNotContains(r, ham)

    def test_faturasiz_rozeti_ve_numarasiz_kayit(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="", faturasiz=True, satirlar=self._satir())
        self.assertContains(self.detay(f), 'title="Belge no yok (faturasız)"')
        Fatura.objects.filter(pk=f.pk).update(faturasiz=False)                                           # eski numarasız kayıt (Karakuş gibi)
        self.assertContains(self.detay(f), "Fatura no girilmemiş")

    def test_yalniz_kdv_tipinde_matrah_tire(self):
        f = fatura_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="KDV-1", satirlar=self._satir("10", "100"))
        r = self.detay(f)
        k = r.context["d"]["kutu"]
        self.assertEqual(k["satirlar"][0], {"etiket": "Matrah", "tutar": None, "tl": None})
        self.assertEqual((k["genel"], k["odenecek"]), (Dc("200.00"), Dc("200.00")))
        kal = r.context["d"]["kalemler"][0]
        self.assertEqual((kal["matrah"], kal["kdv"], kal["toplam"]), (None, Dc("200.00"), Dc("200.00")))
        self.assertContains(r, "Genel toplam (yalnız KDV)")
        self.assertContains(r, "KDV")

    def test_doviz_faturada_her_toplamin_tl_karsiligi(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.usd.pk, tarih=D(2026, 3, 10), fatura_no="USD-1", para_birimi="USD", satirlar=self._satir("10", "100"), depo_id=None)
        r = self.detay(f)
        k = r.context["d"]["kutu"]
        self.assertTrue(k["doviz"])
        tl = {x["etiket"]: x["tl"] for x in k["satirlar"]}
        self.assertEqual((tl["Matrah"], tl["KDV %20"], tl["Genel toplam (KDV dahil)"], tl["Ödenecek (cari tutarı)"]), (Dc("30000.00"), Dc("6000.00"), Dc("36000.00"), Dc("36000.00")))
        self.assertContains(r, "TL karşılığı")
        self.assertContains(r, "kur 30,0000")

    def test_kdv_kirilimi_oranlara_gore(self):
        from core.models import KdvOrani
        kdv10 = KdvOrani.objects.create(aciklama="%10", oran=Dc("10"), hesap_borc=self.kdv.hesap_borc, hesap_alacak=self.kdv.hesap_alacak)
        from core.models import Stok
        Stok.objects.filter(pk=self.stok.pk).update(kdv=kdv10)
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="KR-1", satirlar=[
            {"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "100", "kdv_id": self.kdv.pk}, {"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100", "kdv_id": kdv10.pk}])
        k = self.detay(f).context["d"]["kutu"]
        self.assertEqual([(x["etiket"], x["tutar"]) for x in k["satirlar"][:4]],
                         [("Matrah", Dc("1500.00")), ("KDV %10", Dc("50.00")), ("KDV %20", Dc("200.00")), ("Toplam KDV", Dc("250.00"))])
        self.assertEqual(k["genel"], Dc("1750.00"))

    def test_taslakta_odeme_ve_muhasebe_karti_yok(self):
        t = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), tip_id=self.alis.pk, satirlar=self._satir(), fatura_no="TSL-1")
        r = self.detay(t)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["d"]["odeme"])
        self.assertIsNone(r.context["d"]["muh"])
        self.assertNotContains(r, 'id="fd-odemeler"')
        self.assertNotContains(r, 'id="fd-muhasebe"')
        self.assertContains(r, "Taslak")
        self.assertContains(r, "Onayla")


class OdemelerKartiTest(DetayTemel):
    def test_kismi_odeme_listeyle_birebir(self):
        f = self.a_fatura(no="OD-1")
        self.odeme("500")
        r = self.detay(f)
        o = r.context["d"]["odeme"]
        liste = fatura_odeme.odeme_durumlari([self.tedarikci])[f.pk]
        self.assertEqual((o["durum"], o["tutar"], o["kalan"], o["gecikme"]), (liste["durum"], liste["tutar"], liste["kalan"], liste["gecikme"]))
        self.assertEqual((o["durum"], o["tutar"], o["odenen"], o["kalan"]), ("KISMI", Dc("1200.00"), Dc("500.00"), Dc("700.00")))
        self.assertEqual([(h["kaynak_ad"], h["tutar"], h["kismi"]) for h in o["hareketler"]], [("Kasa", Dc("500.00"), False)])
        self.assertContains(r, 'class="ft-odeme kismi"')
        self.assertContains(r, "kalan 700,00 TRY")
        self.assertContains(r, reverse("core:fis_detay", args=[o["hareketler"][0]["fis_pk"]]))
        self.assertContains(r, reverse("core:kasa_detay", args=[self.kasa.pk]))                         # kaynak belge bağlantısı

    def test_iki_fatura_fifo_dagilimi_liste_ve_ekstreyle_tutar(self):
        f1 = self.a_fatura(tarih=D(2026, 3, 10), no="OD-A")
        f2 = self.a_fatura(tarih=D(2026, 3, 12), miktar="10", fiyat="80", no="OD-B")
        self.odeme("700", D(2026, 3, 20))
        self.odeme("600", D(2026, 3, 21))
        o1 = self.detay(f1).context["d"]["odeme"]
        o2 = self.detay(f2).context["d"]["odeme"]
        self.assertEqual([h["tutar"] for h in o1["hareketler"]], [Dc("700.00"), Dc("500.00")])
        self.assertEqual([h["tutar"] for h in o2["hareketler"]], [Dc("100.00")])
        self.assertTrue(o1["hareketler"][1]["kismi"])
        self.assertEqual((o1["durum"], o1["kalan"], o2["durum"], o2["kalan"]), ("KAPANDI", Dc("0"), "KISMI", Dc("860.00")))
        liste = fatura_odeme.odeme_durumlari([self.tedarikci])
        for f, o in ((f1, o1), (f2, o2)):
            self.assertEqual(sum(h["tutar"] for h in o["hareketler"]), o["tutar"] - o["kalan"])
            self.assertEqual((o["durum"], o["kalan"]), (liste[f.pk]["durum"], liste[f.pk]["kalan"]))
        # cari ekstresi: ödemelerin toplamı hareketlerin toplamıyla tutar
        odemeler = YevmiyeSatir.objects.filter(hesap_id=self.tedarikci.muhasebe_kodu, silindi=False, borc__gt=0).values_list("borc", flat=True)
        self.assertEqual(sum(odemeler), Dc("1300.00"))
        self.assertEqual(sum(h["tutar"] for h in o1["hareketler"]) + sum(h["tutar"] for h in o2["hareketler"]), Dc("1300.00"))

    def test_kapanan_fatura_ve_vadesi_gecen(self):
        f = self.a_fatura(no="OD-K")
        self.odeme("1200")
        r = self.detay(f)
        self.assertContains(r, 'class="ft-odeme kapandi"')
        self.assertEqual(r.context["d"]["odeme"]["kalan"], Dc("0"))
        g = self.a_fatura(tarih=D(2026, 3, 12), no="OD-V", vade_tarihi=D(2020, 2, 1))                 # FIFO: ödeme eski faturayı kapattı; bu açık kaldı ve vadesi geçmiş
        r2 = self.detay(g)
        self.assertContains(r2, 'class="ft-odeme gecti"')
        self.assertContains(r2, "Vadesi geçti")

    def test_odemesiz_fatura_karti_kalan_tutarla_gorunur(self):
        f = self.a_fatura(no="OD-0")
        r = self.detay(f)
        self.assertEqual(r.context["d"]["odeme"]["hareketler"], [])
        self.assertContains(r, "Bu faturaya düşen ödeme / tahsilat yok")
        self.assertContains(r, 'class="ft-odeme acik"')

    def test_doviz_fatura_odemesi(self):
        from core.models import Kur
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.usd.pk, tarih=D(2026, 3, 10), fatura_no="OD-USD", para_birimi="USD", satirlar=self._satir("10", "100"))
        kasa_hareket = __import__("core.services.kasa_hareket", fromlist=["hareket_olustur"]).hareket_olustur
        self.assertTrue(Kur.objects.filter(tarih=D(2026, 3, 20)).exists())
        o0 = self.detay(f).context["d"]["odeme"]
        self.assertEqual((o0["pb"], o0["tutar"], o0["kalan"]), ("USD", Dc("1200.00"), Dc("1200.00")))
        self.assertEqual(sum(h["tutar"] for h in o0["hareketler"]), Dc("0"))


class MuhasebeKartiTest(kt.KarsilastirmaTaban):
    def detay(self, f):
        self.client.force_login(self.su)
        return yazma_engelli(self, reverse("core:fatura_detay", args=[f.pk]))

    def test_karma_alis_fis_satirlari_ve_toplamlar_fisle_tutar(self):
        f = self._kes([self._stok("100", "50"), self._hesap_satir("730.09", "200")], no="MH-1")
        r = self.detay(f)
        m = r.context["d"]["muh"]
        self.assertEqual(m["fis"].pk, f.fis_id)
        self.assertEqual((m["borc"], m["alacak"]), (Dc("6240.00"), Dc("6240.00")))
        self.assertContains(r, reverse("core:fis_detay", args=[f.fis_id]))
        cari = [s for s in m["satirlar"] if s.hesap_id == "320.10.0001"][0]
        self.assertEqual(r.context["d"]["kutu"]["odenecek"], cari.alacak)                                 # toplam kutusu = fişteki cari tutarı
        self.assertEqual(r.context["d"]["kutu"]["genel"], Dc("6240.00"))
        self.assertContains(r, "Hesap kodu")
        self.assertNotContains(r, "Kur farkı satırları")                                                   # boş bölüm görünmez
        self.assertNotContains(r, "Satış maliyeti fişi")

    def test_satis_maliyet_fisi_ayri_baslikla(self):
        self.stok_girisi("500", "50")
        s = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="MH-S", satirlar=[self._stok("10", "100")], depo_id=self.depo.pk, kullanici=self.su)
        r = self.detay(s)
        m = r.context["d"]["muh"]
        self.assertEqual(len(m["maliyet_fisleri"]), 1)
        self.assertEqual(m["maliyet_fisleri"][0]["baslik"], "Satış maliyeti fişi")
        self.assertContains(r, "Satış maliyeti fişi")
        mf = m["maliyet_fisleri"][0]
        self.assertEqual(mf["borc"], mf["alacak"])
        self.assertEqual(r.context["d"]["kutu"]["odenecek"], [x for x in m["satirlar"] if x.hesap_id == "120.10.0001"][0].borc)

    def test_alis_satis_iadesinde_odeme_karti_yok(self):
        self.stok_girisi("500", "50")
        f = fatura_olustur(tip_id=self.satis_iade.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="MH-I", satirlar=[self._stok("4", "120")], depo_id=self.depo.pk, kullanici=self.su)
        r = self.detay(f)
        self.assertIsNone(r.context["d"]["odeme"])
        self.assertNotContains(r, 'id="fd-odemeler"')
        self.assertContains(r, 'id="fd-muhasebe"')
        self.assertEqual(r.context["d"]["tip_etiket"], "İade")

    def test_stopajli_tevkifatli_gider_toplam_kutusu(self):
        f = self._kes([self._hesap_satir("770.03", "5000")], tip=self.smm, depo=False, no="MH-SMM", gv_stopaj_orani="20")
        k = self.detay(f).context["d"]["kutu"]
        et = {x["etiket"]: x["tutar"] for x in k["satirlar"]}
        self.assertEqual((et["Matrah"], et["GV stopajı (−) %20"], et["Ödenecek (cari tutarı)"]), (Dc("5000.00"), Dc("1000.00"), Dc("5000.00") + Dc("1000.00") - Dc("1000.00")))


class PdfPaneliTest(DetayTemel):
    def test_ek_yoksa_pdf_ekle_kutusu(self):
        f = self.a_fatura(no="EK-0")
        r = self.detay(f)
        self.assertContains(r, "PDF ekle")
        self.assertContains(r, reverse("core:fatura_ek_ekle", args=[f.pk]))
        self.assertNotContains(r, "<iframe")

    def test_tek_ek_onizleme_cok_ek_sekmeli(self):
        f = self.a_fatura(no="EK-1")
        e1 = FaturaEk.objects.create(fatura=f, dosya=ContentFile(PDF_BAYT, name="a.pdf"), orijinal_ad="fatura.pdf")
        r = self.detay(f)
        self.assertContains(r, f'<iframe title="fatura.pdf" src="{reverse("core:fatura_ek_indir", args=[e1.pk])}"')
        self.assertNotContains(r, 'class="fd-sekme')
        e2 = FaturaEk.objects.create(fatura=f, dosya=ContentFile(PDF_BAYT, name="b.pdf"), orijinal_ad="irsaliye.pdf")
        r = self.detay(f)
        self.assertContains(r, 'class="fd-sekme aktif"')
        self.assertContains(r, f'data-src="{reverse("core:fatura_ek_indir", args=[e2.pk])}"')              # ikinci PDF sekme açılınca yüklenir
        self.assertContains(r, reverse("core:fatura_ek_sil", args=[e1.pk]))                                # mevcut ek silme korunur

    def test_islem_dugmeleri(self):
        r = self.detay(self.a_fatura(no="EK-2"))
        for metin in ("Düzenle", "PDF / Ek ekle", "Yazdır", "Sil"):
            self.assertContains(r, metin)
        self.assertContains(r, "window.print()")

    def test_silinmis_faturada_pdf_paneli_yok(self):
        f = self.a_fatura(no="EK-3")
        Fatura.objects.filter(pk=f.pk).update(silindi=True)
        r = self.detay(f)
        self.assertContains(r, "iptal edilmiş")
        self.assertNotContains(r, 'id="fd-pdf"')
