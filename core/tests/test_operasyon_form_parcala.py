"""Operasyon formu/listesi/kopyala × PARÇALA: tür seçici, çıktı tablosu (pc formset, ilk satır referans), pay anahtarı; ekle/düzenle/tür değiştirme/kopyala
akışları normal servisten geçer; listede 'parçala · N çıktı' rozeti ve '1 BOY → 64 × SAĞ + 64 × SOL' özeti; ÜRET formu değişmedi."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Operasyon
from core.services.uretim import operasyon_olustur, tanim_ciktilari
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
PARCALA, URET = Operasyon.Tur.PARCALA, Operasyon.Tur.URET


class FormBase(TamBoyBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("opf_y", password="x"))
        self.profil = self.stok("150-20-0002", self.boy, satinalma=True)
        self.sag, self.sol, self.uc = self.stok("151-20-0014"), self.stok("151-20-0015"), self.stok("151-20-0016")

    def govde(self, pc, girdiler=None, **ek):
        girdiler = girdiler or [(self.profil, "1")]
        v = {"istasyon": self.kesim.pk, "tur": "PARCALA", "pay_anahtari": "ESIT", "tam_boy_var": "1",
             "satir-TOTAL_FORMS": str(len(girdiler)), "satir-INITIAL_FORMS": "0", "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0",
             "pc-TOTAL_FORMS": str(len(pc)), "pc-INITIAL_FORMS": "0"}
        for i, (g, m) in enumerate(girdiler):
            v.update({f"satir-{i}-girdi": g.pk, f"satir-{i}-miktar": m})
        for i, (st, m, b, y) in enumerate(pc):
            v.update({f"pc-{i}-stok": st.pk if st else "", f"pc-{i}-miktar": m, f"pc-{i}-boy_mm": b or "", f"pc-{i}-yuzde": y or ""})
        v.update(ek)
        return v

    def parcala_op(self):
        return operasyon_olustur(istasyon_id=self.kesim.pk, satirlar=[(self.profil, D("1"))], tur=PARCALA, pay_anahtari=Operasyon.PayAnahtari.ESIT,
                                 tam_boy=False, ciktilar=[(self.sag, D("64"), None, None), (self.sol, D("64"), None, None)])


class EkleTest(FormBase):
    def test_get_tur_secici_ve_parcala_bolumu(self):
        r = self.client.get(reverse("core:operasyon_ekle"))
        h = r.content.decode()
        for parca in ('name="tur"', 'id="parcala-bolum" hidden', 'name="pay_anahtari"', 'pc-TOTAL_FORMS', 'id="bos-pc"', "Parçala — 1 girdi"):
            self.assertIn(parca, h)

    def test_post_parcala_olusturur(self):
        r = self.client.post(reverse("core:operasyon_ekle"), self.govde([(self.sag, "64", None, None), (self.sol, "64", None, None)]))
        self.assertEqual(r.status_code, 302)
        op = Operasyon.objects.get(cikti=self.sag, silindi=False)
        self.assertEqual((op.tur, op.pay_anahtari, op.tam_calistirma, op.cikti_miktar), (PARCALA, "ESIT", False, D("64.000")))
        self.assertEqual([(c.stok.kod, c.miktar, c.surucu) for c in tanim_ciktilari(op)], [("151-20-0014", D("64"), True), ("151-20-0015", D("64"), True)])
        self.assertEqual(op.girdiler.filter(silindi=False).count(), 1)

    def test_post_hatalar_sayfada(self):
        r = self.client.post(reverse("core:operasyon_ekle"), self.govde([(self.sag, "64", None, None)], girdiler=[(self.profil, "1"), (self.uc, "1")]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "tek girdi")
        r = self.client.post(reverse("core:operasyon_ekle"), self.govde([(self.sag, "12", "300", None), (self.sol, "4", "", None)], pay_anahtari="BOY"))
        self.assertContains(r, "boy (mm) girin")
        r = self.client.post(reverse("core:operasyon_ekle"), self.govde([(self.sag, "12", None, "60"), (self.sol, "4", None, "60")], pay_anahtari="YUZDE"))
        self.assertContains(r, "toplamı %100")
        r = self.client.post(reverse("core:operasyon_ekle"), self.govde([(None, "12", None, None)]))
        self.assertContains(r, "Çıktı stoğunu seçin")
        self.assertEqual(Operasyon.objects.filter(silindi=False, tur=PARCALA).count(), 0)

    def test_uret_formu_degismedi(self):
        c = self.stok("ANA")
        v = {"istasyon": self.kesim.pk, "cikti": c.pk, "cikti_miktar": "3", "tur": "URET", "tam_boy_var": "1", "tam_boy": "1",
             "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
             "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0", "pc-TOTAL_FORMS": "0", "pc-INITIAL_FORMS": "0"}
        self.assertEqual(self.client.post(reverse("core:operasyon_ekle"), v).status_code, 302)
        op = Operasyon.objects.get(cikti=c, silindi=False)
        self.assertEqual((op.tur, op.cikti_miktar, op.tam_calistirma), (URET, D("3.000"), True))


class DuzenleKopyalaListeTest(FormBase):
    def test_duzenle_get_referans_sabit_ve_secili_tur(self):
        op = self.parcala_op()
        r = self.client.get(reverse("core:operasyon_duzenle", args=[op.pk]))
        h = r.content.decode()
        self.assertIn('<option value="PARCALA" selected>', h)
        self.assertIn(f'<input type="hidden" name="pc-0-stok" value="{self.sag.pk}">', h)
        self.assertIn("referans", h)
        self.assertIn('name="pc-1-stok"', h)
        self.assertNotIn('id="parcala-bolum" hidden', h)

    def test_duzenle_post_cikti_ekle_ve_miktar_degistir(self):
        op = self.parcala_op()
        v = self.govde([(self.sag, "64", None, None), (self.sol, "32", None, None), (self.uc, "8", None, None)])
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), v)
        self.assertEqual(r.status_code, 302)
        self.assertEqual([(c.stok.kod, c.miktar) for c in tanim_ciktilari(op)], [("151-20-0014", D("64")), ("151-20-0015", D("32")), ("151-20-0016", D("8"))])
        # referans değiştirilemez
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), self.govde([(self.sol, "32", None, None), (self.sag, "64", None, None)]), follow=True)
        self.assertContains(r, "referans çıktısı")

    def test_uretten_parcalaya_ve_geri(self):
        ana = self.stok("ANA")
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))])
        r = self.client.get(reverse("core:operasyon_duzenle", args=[op.pk]))
        self.assertIn(f'<input type="hidden" name="pc-0-stok" value="{ana.pk}">', r.content.decode())        # ÜRET'te de referans satırı hazır
        v = self.govde([(ana, "3", None, None), (self.sol, "1", None, None)])
        self.assertEqual(self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), v).status_code, 302)
        op.refresh_from_db()
        self.assertEqual((op.tur, [c.stok.kod for c in tanim_ciktilari(op)]), (PARCALA, ["ANA", "151-20-0015"]))
        v = {"istasyon": self.kesim.pk, "cikti_miktar": "4", "tur": "URET", "tam_boy_var": "1",
             "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
             "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0", "pc-TOTAL_FORMS": "0", "pc-INITIAL_FORMS": "0"}
        self.assertEqual(self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), v).status_code, 302)
        op.refresh_from_db()
        self.assertEqual((op.tur, op.cikti_miktar, [c.stok.kod for c in tanim_ciktilari(op)]), (URET, D("4.000"), ["ANA"]))

    def test_kopyala_parcala_kaynak(self):
        op = self.parcala_op()
        r = self.client.get(reverse("core:operasyon_ekle"), {"kopya": op.pk})
        h = r.content.decode()
        self.assertIn('<option value="PARCALA" selected>', h)
        self.assertIn('name="pc-0-stok"', h)
        self.assertNotIn(f'<option value="{self.sag.pk}" selected', h)                                        # çıktı stokları boş
        self.assertIn('name="pc-1-miktar" value="64,000"', h)

    def test_liste_rozet_ve_ozet(self):
        self.parcala_op()
        r = self.client.get(reverse("core:operasyon_tanimlari"))
        self.assertContains(r, "parçala · 2 çıktı")
        self.assertContains(r, "1 BOY 150-20-0002 → 64 × 151-20-0014 + 64 × 151-20-0015")
        self.assertNotContains(r, "yan çıktı</span>")
