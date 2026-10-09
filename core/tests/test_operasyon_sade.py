"""Operasyon tanımı ekranı sadeleştirme: Ad/Açıklama yok; tam çalıştırma girdi birimine göre OTOMATİK (formda yalnız bilgi rozeti); ana çıktı boyu (mm)
yalnız yan çıktı varken görünür/zorunlu (sunucu doğrulaması aynen); OperasyonKaydi.aciklama etkilenmez."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Operasyon, OperasyonKaydi
from core.services.uretim import UretimHatasi, operasyon_guncelle, operasyon_olustur, operasyon_yan_ciktilari
from core.tests.test_uretim_yan_cikti import YanCiktiBase

D = Decimal


class SadeOperasyonTest(YanCiktiBase):
    def setUp(self):
        self.su = User.objects.create_superuser("sade_su", password="x")
        self.client.force_login(self.su)

    # --- model / servis
    def test_model_ad_ve_aciklama_alani_yok_kayit_aciklamasi_duruyor(self):
        alanlar = {f.name for f in Operasyon._meta.get_fields()}
        self.assertFalse({"ad", "aciklama"} & alanlar)
        self.assertIn("aciklama", {f.name for f in OperasyonKaydi._meta.get_fields()})        # OperasyonKaydi'nın kendi alanı ayrı: dokunulmadı

    def test_str_istasyon_ve_cikti_kodu(self):
        self.assertEqual(str(self.op), "KESIM AYAK66")

    def test_yan_ciktisiz_operasyonda_boy_bos_kalabilir_yan_ciktilida_zorunlu(self):
        c1 = self._stok("S1", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=c1.pk, cikti_miktar=D("1"), satirlar=[(self.profil, D("1"))])
        self.assertIsNone(op.boy_mm)
        c2 = self._stok("S2", self.kat_ana)
        with self.assertRaisesMessage(UretimHatasi, "ana çıktının boyu"):
            operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=c2.pk, cikti_miktar=D("1"), satirlar=[(self.profil, D("1"))],
                              yan_ciktilar=[(self.yan, D("1"), D("100"))])
        operasyon_guncelle(self.op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))], yan_ciktilar=[], boy_mm="")
        self.assertIsNone(Operasyon.objects.get(pk=self.op.pk).boy_mm)                         # tüm yan çıktılar silinince boy da temizlenir

    # --- ekran: form alanları
    def test_yeni_operasyon_formunda_ad_aciklama_checkbox_yok_rozet_ve_gizli_boy_var(self):
        r = self.client.get(reverse("core:operasyon_ekle"))
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        for yok in ('name="ad"', 'name="aciklama"', 'name="tam_calistirma"', "Tam çalıştırma zorunlu"):
            self.assertNotIn(yok, h)
        self.assertIn('id="id_tam_boy"', h)
        self.assertIn('id="boy-alan" hidden', h)                                              # yan çıktı yokken gizli
        self.assertIn('id="boy-stok-idler"', h)
        self.assertIn(self.profil.pk, r.context["boy_stok_idler"])                             # BOY birimli stok JS'e verilir
        self.assertNotIn(self.ana.pk, r.context["boy_stok_idler"])

    def test_duzenle_formunda_boy_yan_cikti_varsa_gorunur_yoksa_gizli(self):
        r = self.client.get(reverse("core:operasyon_duzenle", args=[self.op.pk]))              # setUp'taki operasyonun yan çıktısı var
        h = r.content.decode()
        self.assertIn('id="boy-alan">', h)
        self.assertNotIn('id="boy-alan" hidden', h)
        self.assertIn("1.292,60", h)
        for yok in ('name="ad"', 'name="aciklama"', 'name="tam_calistirma"'):
            self.assertNotIn(yok, h)
        yansiz = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self._stok("S3", self.kat_ana).pk, cikti_miktar=D("1"),
                                   satirlar=[(self.profil, D("1"))])
        h2 = self.client.get(reverse("core:operasyon_duzenle", args=[yansiz.pk])).content.decode()
        self.assertIn('id="boy-alan" hidden', h2)
        self.assertIn('id="id_tam_boy" value="1" checked', h2)                    # BOY girdili tanım → "Tam boy zorunlu" kutusu işaretli
        self.assertIn("1 boydan kaç adet", h2)                                                 # tam boyda miktar etiketi

    # --- ekran: POST akışları
    def _satir_post(self, girdi, yan=None, **ek):
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": girdi.pk, "satir-0-miktar": "1",
                "istasyon": self.kesim.pk, "cikti_miktar": "2", **ek}
        yan = yan or []
        veri.update({"yan-TOTAL_FORMS": str(len(yan)), "yan-INITIAL_FORMS": "0"})
        for i, (stok, miktar, boy) in enumerate(yan):
            veri.update({f"yan-{i}-stok": stok.pk, f"yan-{i}-miktar": miktar, f"yan-{i}-boy_mm": boy})
        return veri

    def test_ekle_post_ad_aciklama_olmadan_tam_boy_otomatik(self):
        c = self._stok("S4", self.kat_ana)
        r = self.client.post(reverse("core:operasyon_ekle"), {**self._satir_post(self.profil), "cikti": c.pk})
        self.assertEqual(r.status_code, 302)
        op = Operasyon.objects.get(cikti=c, silindi=False)
        self.assertTrue(op.tam_calistirma)                                                     # BOY girdi → otomatik True
        c2 = self._stok("S5", self.kat_ana)
        adet_girdi = self._stok("ADETGIRDI", self.kat_girdi, satinalma=True)
        self.client.post(reverse("core:operasyon_ekle"), {**self._satir_post(adet_girdi), "cikti": c2.pk})
        self.assertFalse(Operasyon.objects.get(cikti=c2, silindi=False).tam_calistirma)       # ADET girdi → False

    def test_duzenle_post_tam_boy_kutusu_secimi_uygulanir_kutu_yoksa_korunur(self):
        c = self._stok("S6", self.kat_ana)
        adet_girdi = self._stok("ADETGIRDI2", self.kat_girdi, satinalma=True)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=c.pk, cikti_miktar=D("1"), satirlar=[(adet_girdi, D("1"))])
        self.assertFalse(op.tam_calistirma)
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), self._satir_post(self.profil, boy_mm=""))
        self.assertEqual(r.status_code, 302)
        op.refresh_from_db()
        self.assertFalse(op.tam_calistirma)                                                    # kutu alanı yok → mevcut değer korunur
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]),
                             self._satir_post(self.profil, boy_mm="", tam_boy_var="1", tam_boy="1"))
        op.refresh_from_db()
        self.assertTrue(op.tam_calistirma)
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]),
                             self._satir_post(self.profil, boy_mm="", tam_boy_var="1"))      # kutu kaldırıldı (işaret yok, tam_boy gönderilmez)
        op.refresh_from_db()
        self.assertFalse(op.tam_calistirma)

    def test_duzenle_post_yan_cikti_varken_ana_boy_bossa_sunucu_reddeder(self):
        c = self._stok("S7", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=c.pk, cikti_miktar=D("1"), satirlar=[(self.profil, D("1"))])
        r = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]),
                             self._satir_post(self.profil, yan=[(self.yan, "1", "100")], boy_mm=""), follow=True)
        self.assertContains(r, "ana çıktının boyu")
        self.assertEqual(operasyon_yan_ciktilari(op).count(), 0)                               # hiçbir şey kaydedilmedi
        r2 = self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]),
                              self._satir_post(self.profil, yan=[(self.yan, "1", "100")], boy_mm="500"))
        self.assertEqual(r2.status_code, 302)
        op.refresh_from_db()
        self.assertEqual((op.boy_mm, operasyon_yan_ciktilari(op).count()), (D("500.00"), 1))

    def test_liste_cikti_adini_gosterir_tam_boy_rozeti_durur(self):
        r = self.client.get(reverse("core:operasyon_tanimlari"))
        self.assertContains(r, self.ana.ad)
        self.assertContains(r, "tam boy")
