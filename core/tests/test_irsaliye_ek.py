"""İrsaliye ekleri (2026-10-15): yükleme / indirme / KALICI silme (kayıt + dosya), XML reddi, yetki, faturaya dönüşümde eklerin irsaliyede kalması + fatura detayında kaynak irsaliye
ekleri; fatura ekindeki irsaliye PDF'lerini irsaliyeye taşıyan `irsaliye_ek_tasi` komutu (önizleme / --uygula / idempotent / şüphelileri atlama / fatura PDF'i korunur)."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from core.models import Cari, EkranYetki, Fatura, FaturaEk, IrsaliyeEk, StokHareket, TeklifSiparis, YevmiyeFisi
from core.services import irsaliye_ek as ek_servis
from core.services.teklif_siparis import (
    irsaliyeyi_faturaya_cevir, siparisi_irsaliyeye_cevir, teklif_siparis_onayla, teklifi_siparise_cevir,
)
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, png_bayt, yuklenen
from core.tests import test_teklif_siparis as tts      # modül olarak (sınıfı içe almak onun testlerini bu modülde de koşturur)

XML = b'<?xml version="1.0"?><DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"/>'


class IrsaliyeTaban(OzelDizinTestTemel):
    """SatinalmaZinciriTest fixture'ını (stok, cari, depo, alış fatura tipi) DEVRALMADAN kullanır — o sınıfın testleri bu modülde yeniden koşmasın."""

    @classmethod
    def setUpTestData(cls):
        tts.SatinalmaZinciriTest.setUpTestData.__func__(cls)

    _teklif = tts.SatinalmaZinciriTest._teklif

    def irsaliye(self, e_no="", onayli=True):
        teklif = self._teklif()
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        siparis = teklifi_siparise_cevir(teklif, tarih=self.tarih, kullanici=self.yon)
        teklif_siparis_onayla(siparis, kullanici=self.yon)
        irs = siparisi_irsaliyeye_cevir(siparis, tarih=self.tarih, depo_id=self.depo.pk, kullanici=self.yon)
        if onayli:
            teklif_siparis_onayla(irs, kullanici=self.yon)
        TeklifSiparis.objects.filter(pk=irs.pk).update(irsaliye_no=e_no)
        irs.refresh_from_db()
        return irs

    def fatura_ile(self, irs, fatura_no):
        irsaliyeyi_faturaya_cevir(irs, kullanici=self.yon)
        irs.refresh_from_db()
        Fatura.objects.filter(pk=irs.fatura_id).update(fatura_no=fatura_no)
        irs.fatura.refresh_from_db()
        return irs.fatura

    def yukle(self, irs, *dosyalar):
        self.client.force_login(self.yon)
        return self.client.post(reverse("core:irsaliye_ek_ekle", args=[irs.pk]), {"dosyalar": list(dosyalar)}, follow=True)

    def diskte(self, ek):
        return ek.dosya.storage.exists(ek.dosya.name)


class IrsaliyeEkTest(IrsaliyeTaban):
    def test_yukle_listele_indir(self):
        irs = self.irsaliye("IRS2026000000001")
        r = self.yukle(irs, yuklenen("irsaliye.pdf", PDF_BAYT))
        self.assertContains(r, "1 dosya eklendi.")
        ek = irs.ekler.get()
        self.assertEqual((ek.orijinal_ad, ek.irsaliye_id), ("irsaliye.pdf", irs.pk))
        self.assertTrue(ek.dosya.name.startswith("irsaliye_ek/") and ek.dosya.name.endswith(".pdf"))
        self.assertTrue(self.diskte(ek))
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[irs.pk])), "irsaliye.pdf")
        r = self.client.get(reverse("core:irsaliye_ek_indir", args=[ek.pk]))
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertIn("no-store", r["Cache-Control"])

    def test_resim_webp_olur(self):
        irs = self.irsaliye()
        self.yukle(irs, yuklenen("foto.png", png_bayt()))
        self.assertTrue(irs.ekler.get().dosya.name.endswith(".webp"))

    def test_xml_ve_gecersiz_dosya_reddedilir(self):
        irs = self.irsaliye()
        r = self.yukle(irs, yuklenen("IRS2026000000001.xml", XML))
        self.assertContains(r, "XML dosyaları ek olarak kabul edilmiyor")
        r = self.yukle(irs, yuklenen("sahte.pdf", b"bu pdf degil"))
        self.assertContains(r, "Geçersiz PDF dosyası")
        r = self.yukle(irs, yuklenen("a.exe", b"MZ"))
        self.assertContains(r, "Desteklenmeyen dosya türü")
        r = self.yukle(irs, SimpleUploadedFile("buyuk.pdf", PDF_BAYT + b"0" * (10 * 1024 * 1024)))
        self.assertContains(r, "Dosya çok büyük")
        self.assertEqual(IrsaliyeEk.objects.count(), 0)

    def test_silme_kalici_kayit_ve_dosya(self):
        irs = self.irsaliye()
        self.yukle(irs, yuklenen("a.pdf", PDF_BAYT), yuklenen("b.pdf", PDF_BAYT))
        a, b = irs.ekler.order_by("id")
        ad_a = a.dosya.name
        depo = a.dosya.storage
        self.client.force_login(self.yon)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse("core:irsaliye_ek_sil", args=[a.pk]), follow=True)
        self.assertContains(r, "kalıcı olarak silindi")
        self.assertFalse(IrsaliyeEk.objects.filter(pk=a.pk).exists())                 # soft-delete değil: kayıt fiziksel yok
        self.assertFalse(depo.exists(ad_a))                                          # dosya diskten de gitti
        self.assertTrue(IrsaliyeEk.objects.filter(pk=b.pk).exists() and self.diskte(b))   # diğer ek dokunulmadı
        self.assertEqual(self.client.get(reverse("core:irsaliye_ek_indir", args=[a.pk])).status_code, 404)

    def test_silme_get_ile_silmez(self):
        irs = self.irsaliye()
        self.yukle(irs, yuklenen("a.pdf", PDF_BAYT))
        ek = irs.ekler.get()
        self.client.get(reverse("core:irsaliye_ek_sil", args=[ek.pk]))
        self.assertTrue(IrsaliyeEk.objects.filter(pk=ek.pk).exists() and self.diskte(ek))

    def test_paylasilan_dosya_silinmez(self):
        irs = self.irsaliye()
        fatura = self.fatura_ile(self.irsaliye(), "F-1")
        ek = IrsaliyeEk.objects.create(irsaliye=irs, dosya=ContentFile(PDF_BAYT, name="x.pdf"), orijinal_ad="x.pdf")
        FaturaEk.objects.create(fatura=fatura, dosya=ek.dosya.name, orijinal_ad="x.pdf")        # aynı depolama yolunu başka kayıt kullanıyor
        with self.captureOnCommitCallbacks(execute=True):
            ek_servis.ek_sil(ek)
        self.assertTrue(FaturaEk.objects.get().dosya.storage.exists(FaturaEk.objects.get().dosya.name))

    def test_yalniz_irsaliyeye_ve_silinmemisine(self):
        irs = self.irsaliye()
        siparis = irs.kaynak_siparis
        self.client.force_login(self.yon)
        self.assertEqual(self.client.post(reverse("core:irsaliye_ek_ekle", args=[siparis.pk]), {"dosyalar": yuklenen("a.pdf", PDF_BAYT)}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("core:teklif_siparis_detay", args=[siparis.pk])), 'id="ekler"')
        TeklifSiparis.objects.filter(pk=irs.pk).update(silindi=True)
        irs.refresh_from_db()
        with self.assertRaises(ek_servis.IrsaliyeEkHatasi):
            ek_servis.ek_ekle(irs, dosya=yuklenen("a.pdf", PDF_BAYT))

    def test_yetki(self):
        irs = self.irsaliye()
        self.yukle(irs, yuklenen("a.pdf", PDF_BAYT))
        ek = irs.ekler.get()
        sade = User.objects.create_user("yetkisiz", password="x")
        EkranYetki.objects.create(kullanici=sade, ekran_kod="alis_faturalari")
        self.client.force_login(sade)
        self.assertEqual(self.client.get(reverse("core:irsaliye_ek_indir", args=[ek.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("core:irsaliye_ek_sil", args=[ek.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("core:irsaliye_ek_ekle", args=[irs.pk]), {"dosyalar": yuklenen("b.pdf", PDF_BAYT)}).status_code, 403)
        self.assertTrue(IrsaliyeEk.objects.filter(pk=ek.pk).exists())
        EkranYetki.objects.create(kullanici=sade, ekran_kod="satinalma_irsaliyeleri")
        self.assertEqual(self.client.get(reverse("core:irsaliye_ek_indir", args=[ek.pk])).status_code, 200)


class FaturayaDonusumTest(IrsaliyeTaban):
    def test_ekler_irsaliyede_kalir_faturaya_kopyalanmaz_faturada_link_gorunur(self):
        irs = self.irsaliye("IRS2026000000001")
        self.yukle(irs, yuklenen("e-irsaliye.pdf", PDF_BAYT))
        ek = irs.ekler.get()
        fatura = self.fatura_ile(irs, "FAT-1")
        self.assertEqual((irs.ekler.count(), FaturaEk.objects.count()), (1, 0))
        self.assertTrue(self.diskte(ek))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_detay", args=[fatura.pk]))
        self.assertContains(r, "Kaynak irsaliye ekleri")
        self.assertContains(r, reverse("core:irsaliye_ek_indir", args=[ek.pk]))
        self.assertContains(r, "e-irsaliye.pdf")
        self.assertContains(r, irs.belge_no)
        self.assertContains(r, "IRS2026000000001")

    def test_fatura_detayi_irsaliye_yetkisizde_link_vermez(self):
        irs = self.irsaliye()
        self.yukle(irs, yuklenen("e-irsaliye.pdf", PDF_BAYT))
        ek = irs.ekler.get()
        fatura = self.fatura_ile(irs, "FAT-1")
        sade = User.objects.create_user("faturaci", password="x")
        EkranYetki.objects.create(kullanici=sade, ekran_kod="alis_faturalari")
        self.client.force_login(sade)
        r = self.client.get(reverse("core:fatura_detay", args=[fatura.pk]))
        self.assertContains(r, "e-irsaliye.pdf")
        self.assertNotContains(r, reverse("core:irsaliye_ek_indir", args=[ek.pk]))

    def test_irsaliyesiz_faturada_bolum_yok(self):
        irs = self.irsaliye()
        fatura = self.fatura_ile(irs, "FAT-1")
        TeklifSiparis.objects.filter(pk=irs.pk).update(fatura=None)
        self.client.force_login(self.yon)
        self.assertNotContains(self.client.get(reverse("core:fatura_detay", args=[fatura.pk])), "Kaynak irsaliye ekleri")


class TasimaKomutuTest(IrsaliyeTaban):
    VKN = "7690338712"

    def setUp(self):
        super().setUp()
        self.log = tempfile.mkdtemp()
        Cari.objects.filter(pk=self.cari.pk).update(vkn_tckn=self.VKN)

    def calistir(self, *args):
        cikti = StringIO()
        call_command("irsaliye_ek_tasi", *args, "--log-dizini", self.log, stdout=cikti)
        return cikti.getvalue()

    def fek(self, fatura, ad, bayt=PDF_BAYT):
        return FaturaEk.objects.create(fatura=fatura, dosya=ContentFile(bayt, name=ad), orijinal_ad=ad)

    def kur(self):
        """İrsaliye + bağlı fatura; fatura ekinde: faturanın kendi PDF'i + e-irsaliye PDF'i."""
        self.irs = self.irsaliye("IRS2026000000249")
        self.fatura = self.fatura_ile(self.irs, "SBL2026000000286")
        self.kendi = self.fek(self.fatura, f"SBL2026000000286_{self.VKN}_SILA BAGLANTI.pdf")
        self.irs_pdf = self.fek(self.fatura, f"IRS2026000000249_{self.VKN}_SILA BAGLANTI.pdf")

    def sayilar(self):
        return (Fatura.objects.count(), YevmiyeFisi.objects.count(), StokHareket.objects.count())

    def test_onizleme_dokum_verir_hicbir_sey_degistirmez(self):
        self.kur()
        oncesi = self.sayilar()
        r = self.calistir()
        self.assertIn("ÖNİZLEME", r)
        self.assertIn("TAŞINACAK : 1", r)
        self.assertIn(f"| {self.irs.belge_no} / IRS2026000000249 | IRS2026000000249_{self.VKN}_SILA BAGLANTI.pdf", r)
        self.assertIn("SBL2026000000286 ", r)
        self.assertIn("irsaliye bu faturaya bağlı", r)
        self.assertEqual((FaturaEk.objects.count(), IrsaliyeEk.objects.count()), (2, 0))
        self.assertEqual(list(Path(self.log).iterdir()), [])
        self.assertEqual(self.sayilar(), oncesi)

    def test_uygula_ayni_dosya_irsaliyeye_gecer_fatura_pdfi_kalir_idempotent(self):
        self.kur()
        dosya_adi = self.irs_pdf.dosya.name
        oncesi = self.sayilar()
        r = self.calistir("--uygula")
        self.assertIn("TAŞINDI: 1 ek", r)
        ek = self.irs.ekler.get()
        self.assertEqual((ek.dosya.name, ek.orijinal_ad), (dosya_adi, self.irs_pdf.orijinal_ad))     # AYNI dosya: yeniden yükleme yok
        self.assertTrue(self.diskte(ek))
        self.assertFalse(FaturaEk.objects.filter(pk=self.irs_pdf.pk).exists())                       # faturadan kalktı
        self.assertEqual(list(FaturaEk.objects.values_list("pk", flat=True)), [self.kendi.pk])       # faturanın kendi PDF'i yerinde
        self.assertTrue(self.diskte(self.kendi))
        self.assertEqual(self.sayilar(), oncesi)                                                     # fatura/fiş/stok dokunulmadı
        log = [json.loads(p.read_text(encoding="utf-8")) for p in Path(self.log).iterdir()]
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0][0]["fatura_no"], log[0][0]["irsaliye_no"], log[0][0]["dosya"]), ("SBL2026000000286", "IRS2026000000249", dosya_adi))
        self.assertEqual(len(log[0][0]["sha256"]), 64)
        r2 = self.calistir("--uygula")                                                               # ikinci çalıştırma: 0 iş
        self.assertIn("TAŞINACAK : 0", r2)
        self.assertIn("0 iş", r2)
        self.assertEqual((FaturaEk.objects.count(), IrsaliyeEk.objects.count(), len(list(Path(self.log).iterdir()))), (1, 1, 1))

    def test_vkn_uyusmazligi_atlanir(self):
        self.kur()
        FaturaEk.objects.filter(pk=self.irs_pdf.pk).update(orijinal_ad="IRS2026000000249_9999999999_BASKA.pdf")
        r = self.calistir("--uygula")
        self.assertIn("TAŞINACAK : 0", r)
        self.assertIn("VKN uyuşmuyor", r)
        self.assertEqual((FaturaEk.objects.count(), IrsaliyeEk.objects.count()), (2, 0))

    def test_birden_cok_irsaliyeye_uyan_atlanir(self):
        self.kur()
        self.irsaliye("IRS2026000000249")                                                            # aynı e-irsaliye no + aynı cari
        r = self.calistir("--uygula")
        self.assertIn("birden çok irsaliyeye uyuyor", r)
        self.assertEqual((FaturaEk.objects.count(), IrsaliyeEk.objects.count()), (2, 0))

    def test_irsaliye_baska_faturaya_bagliysa_atlanir(self):
        self.kur()
        diger = self.fatura_ile(self.irsaliye("IRS2026000000777"), "SBL2026000000999")
        fe = self.fek(diger, f"IRS2026000000249_{self.VKN}_X.pdf")                                   # irsaliye SBL…286'ya bağlı, PDF başka faturada
        r = self.calistir("--uygula")
        self.assertIn("başka bir faturaya bağlı (SBL2026000000286)", r)
        self.assertTrue(FaturaEk.objects.filter(pk=fe.pk).exists())

    def test_erp_de_irsaliyesi_olmayan_ve_silinmis_irsaliye_atlanir(self):
        self.kur()
        yok = self.fek(self.fatura, f"ADI2026000000001_{self.VKN}_ADIM.pdf")                         # irsaliyesi ERP'de yok
        TeklifSiparis.objects.filter(pk=self.irs.pk).update(silindi=True)                            # silinmiş irsaliye hedef olamaz
        r = self.calistir("--uygula")
        self.assertIn("TAŞINACAK : 0", r)
        self.assertIn("ADI2026000000001", r)
        self.assertIn("ERP irsaliyelerinde yok", r)
        self.assertTrue(FaturaEk.objects.filter(pk=yok.pk).exists() and FaturaEk.objects.filter(pk=self.irs_pdf.pk).exists())

    def test_baska_faturanin_pdfi_atlanir(self):
        self.kur()
        diger = self.fatura_ile(self.irsaliye("IRS2026000000777"), "SBL2026000000999")
        fe = self.fek(self.fatura, f"SBL2026000000999_{self.VKN}_X.pdf")                             # başka faturanın PDF'i bu faturaya eklenmiş
        r = self.calistir("--uygula")
        self.assertIn("başka bir ERP faturasının PDF'i", r)
        self.assertTrue(FaturaEk.objects.filter(pk=fe.pk).exists())
        self.assertNotEqual(diger.pk, self.fatura.pk)

    def test_dosya_diskte_yoksa_atlanir(self):
        self.kur()
        self.irs_pdf.dosya.storage.delete(self.irs_pdf.dosya.name)
        r = self.calistir("--uygula")
        self.assertIn("dosya diskte yok", r)
        self.assertEqual(IrsaliyeEk.objects.count(), 0)

    def test_resim_ve_kalip_disi_dosyalara_dokunmaz(self):
        self.kur()
        FaturaEk.objects.filter(pk=self.irs_pdf.pk).delete()
        a = self.fek(self.fatura, "Artirma_Sonucu.pdf")
        b = FaturaEk.objects.create(fatura=self.fatura, dosya=ContentFile(png_bayt(), name="x.webp"), orijinal_ad=f"IRS2026000000249_{self.VKN}_FOTO.webp")
        r = self.calistir("--uygula")
        self.assertIn("TAŞINACAK : 0", r)
        self.assertTrue(FaturaEk.objects.filter(pk__in=[a.pk, b.pk]).count() == 2)

    def test_pdfi_olmayan_irsaliyeler_raporlanir(self):
        self.kur()
        bos = self.irsaliye("IRS2026000000555")
        r = self.calistir()
        bolum = r.split("PDF'İ OLMAYAN İRSALİYELER")[1]
        self.assertIn(bos.belge_no, bolum)
        self.assertIn("IRS2026000000555", bolum)
        self.assertNotIn(self.irs.belge_no, bolum)                                                   # taşınacak PDF'i olan listede değil
        self.calistir("--uygula")
        bolum = self.calistir().split("PDF'İ OLMAYAN İRSALİYELER")[1]
        self.assertNotIn(self.irs.belge_no, bolum)                                                   # taşındıktan sonra da PDF'li sayılır
        self.assertIn(bos.belge_no, bolum)
