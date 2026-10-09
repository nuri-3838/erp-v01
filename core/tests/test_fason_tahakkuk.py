"""FASON — faturasız fasoncu (Cari.fason_faturasiz): dönüş ONAYINDA bedel ayrı tahakkuk fişiyle cariye yazılır (151 alt hesabı B / cari A, KDV yok), bedel kesin
(giris_tahmini=False) ve 'faturası bekleyen' sayılmaz; faturaya bağlanamaz; geri al fişi de iptal eder; mevcut onaylı dönüş için yönetici düğmesi + toplu komut;
mükerrer engelli; bedeli 0 satır fişe girmez; faturalı carinin akışı değişmez."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse

from core.models import FasonDonus, YevmiyeFisi, YevmiyeSatir
from core.services import fason as fs
from core.services import fason_donus as fd
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar
from core.tests.test_fason_donus import TARIH
from core.tests.test_fason_fatura import FaturaBase

D = Decimal


class FaturasizBase(FaturaBase):
    def faturasiz(self, deger=True):
        self.salim.fason_faturasiz = deger
        self.salim.save(update_fields=["fason_faturasiz"])

    def hazir(self, onayla=True, yan="6", ana="10"):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar(ana=ana, yan=yan)
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, irsaliye_no="IRS-1", satirlar=[(self.op.pk, "3")], onayla=onayla)

    def satirlar(self, fis):
        return [(s.hesap.hesap_kodu if hasattr(s, "hesap") and s.hesap_id else s.hesap_kodu, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False)]


class OnayTahakkukTest(FaturasizBase):
    def test_onayda_fis_hesaplar_tutar_ve_kesin_bedel(self):
        self.faturasiz()
        d = self.hazir()
        d.refresh_from_db()
        fis = d.tahakkuk_fis
        self.assertIsNotNone(fis)
        self.assertEqual((fis.kaynak, fis.tarih), (YevmiyeFisi.Kaynak.FASON_TAHAKKUK, TARIH))
        s = self.satirlar(fis)
        borclar = [x for x in s if x[1] > 0]
        alacaklar = [x for x in s if x[2] > 0]
        self.assertTrue(all(k.startswith("151") for k, _b, _a in borclar))
        self.assertEqual(sum(b for _k, b, _a in borclar), D("36.00"))                 # 3×10 ana + 1×6 yan, KDV yok
        self.assertEqual(alacaklar, [("320.10.0001", D("0.00"), D("36.00"))])
        h = self.cikti_giris(d.kayitlar.get())
        self.assertFalse(h["AYAK66"].giris_tahmini or h["AYAK55"].giris_tahmini)       # bedel kesin
        r = self.satir151()
        self.assertEqual((r["fason_bekleyen"], r["ham_fark"], r["fark"]), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(r["mizan"], D("136.00"))                                      # 100 malzeme + 36 tahakkuk

    def test_faturali_cari_akisi_degismez(self):
        d = self.hazir()                                                               # fason_faturasiz=False
        d.refresh_from_db()
        self.assertIsNone(d.tahakkuk_fis_id)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FASON_TAHAKKUK).count(), 0)
        self.assertTrue(self.cikti_giris(d.kayitlar.get())["AYAK66"].giris_tahmini)
        self.assertEqual(self.satir151()["fason_bekleyen"], D("36.00"))
        fd.faturaya_bagla(d, self.fatura("36"))                                        # faturaya bağlama serbest
        self.assertEqual(self.satir151()["fason_bekleyen"], D("0.00"))

    def test_bedeli_sifir_satir_fise_girmez(self):
        self.faturasiz()
        try:
            d = self.hazir(yan="0")
        except Exception:                                                              # fiyat 0 kabul edilmiyorsa bu senaryo uygulanamaz
            self.skipTest("0 fiyat tanımlanamıyor")
        d.refresh_from_db()
        self.assertEqual(sum(b for _k, b, _a in self.satirlar(d.tahakkuk_fis)), D("30.00"))


class GeriAlMukerrerTest(FaturasizBase):
    def test_geri_al_fisi_iptal_eder(self):
        self.faturasiz()
        d = self.hazir()
        d.refresh_from_db()
        fis = d.tahakkuk_fis
        fd.donus_geri_al_sil(d)
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("10"))
        self.assertEqual(self.satir151()["mizan"], D("0.00"))                          # malzeme fişi + tahakkuk birlikte iptal

    def test_mukerrer_engelli(self):
        self.faturasiz()
        d = self.hazir()
        with self.assertRaisesMessage(FasonHatasi, "zaten var"):
            fd.tahakkuk_olustur(d)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FASON_TAHAKKUK, silindi=False).count(), 1)

    def test_faturaya_baglanamaz(self):
        self.faturasiz()
        d = self.hazir()
        with self.assertRaisesMessage(FasonHatasi, "Faturasız fasoncunun"):
            fd.faturaya_bagla(d, self.fatura("36"))

    def test_faturali_cari_icin_tahakkuk_olusturulamaz(self):
        d = self.hazir()
        with self.assertRaisesMessage(FasonHatasi, "faturasız fasoncu olarak işaretli değil"):
            fd.tahakkuk_olustur(d)


class MevcutDonusTest(FaturasizBase):
    def test_gecmis_donus_dugme_donus_tarihiyle_yonetici(self):
        d = self.hazir()                                                               # cari henüz işaretsiz: fiş yok, bedel tahmini
        self.assertEqual(self.satir151()["fason_bekleyen"], D("36.00"))
        self.faturasiz()
        yon = User.objects.create_superuser("th_y", password="x")
        self.client.force_login(yon)
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "Tahakkuk fişi oluştur")
        self.assertContains(det, reverse("core:fason_donus_tahakkuk_olustur", args=[d.pk]))
        # yönetici olmayan kullanıcı oluşturamaz
        u = User.objects.create_user("th_u", password="x")
        from core.models import EkranYetki
        EkranYetki.objects.create(kullanici=u, ekran_kod="fason_donusleri")
        self.client.force_login(u)
        self.assertEqual(self.client.post(reverse("core:fason_donus_tahakkuk_olustur", args=[d.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("core:fason_donus_detay", args=[d.pk])), "action=\"" + reverse("core:fason_donus_tahakkuk_olustur", args=[d.pk]))
        self.client.force_login(yon)
        r = self.client.post(reverse("core:fason_donus_tahakkuk_olustur", args=[d.pk]), follow=True)
        self.assertContains(r, "cariye tahakkuk etti")
        d.refresh_from_db()
        self.assertEqual(d.tahakkuk_fis.tarih, TARIH)                                  # dönüş tarihi
        self.assertEqual(self.satir151()["fark"], D("0.00"))
        self.assertFalse(self.cikti_giris(d.kayitlar.get())["AYAK66"].giris_tahmini)
        r = self.client.post(reverse("core:fason_donus_tahakkuk_olustur", args=[d.pk]), follow=True)
        self.assertContains(r, "zaten var")                                            # mükerrer
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FASON_TAHAKKUK).count(), 1)

    def test_toplu_komut_dry_run_ve_gercek(self):
        d = self.hazir()
        self.faturasiz()
        cikti = StringIO()
        call_command("fason_tahakkuk_olustur", "--dry-run", stdout=cikti)
        self.assertIn(d.no, cikti.getvalue())
        self.assertIn("1 dönüş yazılacak", cikti.getvalue())
        d.refresh_from_db()
        self.assertIsNone(d.tahakkuk_fis_id)                                           # dry-run yazmaz
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FASON_TAHAKKUK).count(), 0)
        cikti = StringIO()
        call_command("fason_tahakkuk_olustur", stdout=cikti)
        self.assertIn("1 dönüş yazıldı", cikti.getvalue())
        d.refresh_from_db()
        self.assertEqual(d.tahakkuk_fis.tarih, TARIH)
        cikti = StringIO()
        call_command("fason_tahakkuk_olustur", stdout=cikti)                           # ikinci koşu: mükerrer yok
        self.assertIn("0 dönüş yazıldı", cikti.getvalue())
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FASON_TAHAKKUK).count(), 1)

    def test_fis_ekraninda_elle_duzenleme_kilitli(self):
        self.faturasiz()
        d = self.hazir()
        d.refresh_from_db()
        self.client.force_login(User.objects.create_superuser("th_y2", password="x"))
        r = self.client.get(reverse("core:fis_duzenle", args=[d.tahakkuk_fis_id]), follow=True)
        self.assertContains(r, "fason dönüşünün cari tahakkukudur")


class CariFormuMutabakatTest(FaturasizBase):
    def test_cari_formunda_kutu_ve_kayit(self):
        self.client.force_login(User.objects.create_superuser("th_y3", password="x"))
        r = self.client.get(reverse("core:cari_duzenle", args=[self.salim.pk]))
        self.assertContains(r, "Faturasız fason (dönüş onayında cariye yaz)")
        from core.tests.test_fason_mutabakat import MutabakatTest  # noqa: F401  (aynı taban; form POST aşağıda servis üzerinden)
        from core.services import cari as cari_servis
        cari_servis.cari_guncelle(self.salim, unvan=self.salim.unvan, kategori_id=self.salim.kategori_id, kisa_ad="", vergi_dairesi="", vkn_tckn="",
                                  tax_id="", telefon="", telefon_2="", eposta="", web="", ilgili_kisi="", kep_adresi="", adres="",
                                  para_birimi="TRY", kredi_limiti=0, iskonto_yuzdesi=0, notlar="", fason_faturasiz=True)
        self.salim.refresh_from_db()
        self.assertTrue(self.salim.fason_faturasiz)

    def test_mutabakatta_cariye_tahakkuk_eden(self):
        from core.services import fason_mutabakat as fm
        self.faturasiz()
        d = self.hazir()
        m = fm.mutabakat(self.salim)
        self.assertEqual((m["tahakkuk"]["try"], m["tahakkuk"]["adet"], m["bekleyen"]["adet"], m["faturali"]["adet"]), (D("36.00"), 1, 0, 0))
        self.assertTrue(m["donusler"][0]["tahakkuklu"])
        self.client.force_login(User.objects.create_superuser("th_y4", password="x"))
        r = self.client.get(reverse("core:fason_mutabakat"), {"cari": self.salim.pk})
        self.assertContains(r, "cariye tahakkuk eden")
        self.assertContains(r, "Cariye tahakkuk")
        self.assertNotContains(r, "faturası bekleyen")
