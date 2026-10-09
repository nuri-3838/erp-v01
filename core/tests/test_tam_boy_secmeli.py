"""Operasyonda 'Tam boy zorunlu' SEÇMELİ (Operasyon.tam_calistirma) + miktar zincirinde 6 ondalık: varsayılan BOY girdisi varsa işaretli ama
kullanıcı kaldırabilir; kapalıysa onayda/ihtiyaçta yuvarlama yok, girdi kesirli düşer (1/64 = 0,015625 saklanır); açıkken mevcut davranış."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Operasyon, OperasyonGirdi, StokHareket
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, ihtiyac_hesapla, kaydi_girdi_satirlari, operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur,
)
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
TARIH = date(2026, 10, 9)


class SecimTest(TamBoyBase):
    def test_varsayilan_boy_girdisi_varsa_true_kullanici_kapatabilir(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        adet = self.stok("ADETG", self.adet, satinalma=True)
        self.assertTrue(operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("K1").pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))]).tam_calistirma)
        self.assertFalse(operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("K2").pk, cikti_miktar=D("1"),
                                           satirlar=[(profil, D("1"))], tam_boy=False).tam_calistirma)
        self.assertTrue(operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("K3").pk, cikti_miktar=D("1"),
                                          satirlar=[(adet, D("1"))], tam_boy=True).tam_calistirma)

    def test_formda_kutu_ve_post(self):
        self.client.force_login(User.objects.create_superuser("tb_y", password="x"))
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        r = self.client.get(reverse("core:operasyon_ekle"))
        self.assertContains(r, 'name="tam_boy"')
        self.assertContains(r, "Tam boy zorunlu")
        cikti = self.stok("KESIM1")
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": profil.pk, "satir-0-miktar": "0,015625",
                "istasyon": self.kesim.pk, "cikti": cikti.pk, "cikti_miktar": "1", "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0",
                "tam_boy_var": "1"}                                                     # kutu işaretsiz gönderildi
        self.assertEqual(self.client.post(reverse("core:operasyon_ekle"), veri).status_code, 302)
        op = Operasyon.objects.get(cikti=cikti, silindi=False)
        self.assertFalse(op.tam_calistirma)
        self.assertEqual(op.girdiler.get().miktar, D("0.015625"))
        duz = self.client.get(reverse("core:operasyon_duzenle", args=[op.pk]))
        self.assertNotContains(duz, 'id="id_tam_boy" value="1" checked')               # kutu işaretsiz
        self.assertContains(duz, "0,015625")                                           # 6 ondalık kısaltılmadan/yuvarlanmadan gösterilir
        veri2 = {**veri, "satir-0-miktar": "0,015625", "tam_boy": "1"}
        self.client.post(reverse("core:operasyon_duzenle", args=[op.pk]), veri2)
        op.refresh_from_db()
        self.assertTrue(op.tam_calistirma)
        self.assertContains(self.client.get(reverse("core:operasyon_duzenle", args=[op.pk])), 'id="id_tam_boy" value="1" checked')

    def test_liste_rozeti_yalniz_kutu_isaretliyse(self):
        self.client.force_login(User.objects.create_superuser("tb_y2", password="x"))
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("KA").pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))], tam_boy=False)
        r = self.client.get(reverse("core:operasyon_tanimlari"))
        self.assertNotContains(r, 'class="rozet rozet-notr" title="Çalıştırma sayısı tam sayı')
        operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("KB").pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))], tam_boy=True)
        self.assertContains(self.client.get(reverse("core:operasyon_tanimlari")), 'title="Çalıştırma sayısı tam sayı')


class KesirliTest(TamBoyBase):
    def kur(self, tam_boy, cikti_miktar="1", girdi="0.015625"):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        parca = self.stok("PARCA")
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=parca.pk, cikti_miktar=D(cikti_miktar),
                               satirlar=[(profil, D(girdi))], tam_boy=tam_boy)                    # varsayılan: 1/64 boy → 1 parça
        hareket_ekle(stok_id=profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("10"),
                     giris_tutar_try=D("6400"))                                                   # 640 TL / boy
        return profil, parca, op

    def test_alti_ondalik_saklanir(self):
        profil, parca, op = self.kur(False)
        self.assertEqual(OperasyonGirdi.objects.get(pk=op.girdiler.get().pk).miktar, D("0.015625"))
        h = hareket_ekle(stok_id=profil.pk, depo_id=self.depo.pk, tarih=TARIH, tur=StokHareket.Tur.GIRIS, miktar=D("0.015625"), giris_tutar_try=D("10"))
        h.refresh_from_db()
        self.assertEqual(h.miktar, D("0.015625"))

    def test_tam_boy_kapali_kesirli_onay_stok_ve_maliyet(self):
        profil, parca, op = self.kur(False, "64", "1")                                             # 1 boydan 64 parça
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("3"))
        self.assertEqual(k.hedef_cikti_miktari, D("3"))                                           # 64'e yükseltilmedi
        self.assertEqual(kaydi_girdi_satirlari(k).get(girdi=profil).planlanan_miktar, D("0.046875"))
        operasyon_kaydi_onayla(k)                                                                  # kesirli BOY girdisi engellenmez
        self.assertEqual(eldeki_miktar(profil, self.depo), D("9.953125"))
        self.assertEqual(eldeki_miktar(parca, self.depo), D("3"))
        parca.refresh_from_db()
        self.assertEqual((parca.maliyet_miktar, parca.maliyet_deger_try, parca.ort_maliyet_try), (D("3"), D("30.00"), D("10.00")))   # 0,046875 × 640

    def test_tam_boy_kapali_ihtiyac_hesabinda_yuvarlama_yok(self):
        profil, parca, op = self.kur(False, "64", "1")
        o = self.ozet(ihtiyac_hesapla([(parca, D("3"))]))
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("0.046875"))                              # 3/64 boy, yukarı yuvarlanmaz
        self.assertEqual(self.ozet(ihtiyac_hesapla([(parca, D("3"))], boy_yuvarla=True))["PROFIL"]["toplam_miktar"], D("0.046875"))

    def test_tam_boy_kapali_yetersiz_stok_yine_engellenir(self):
        profil, parca, op = self.kur(False, "64", "1")
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("1000"))   # 15,625 boy > 10
        with self.assertRaises(Exception):
            operasyon_kaydi_onayla(k)
        self.assertEqual(eldeki_miktar(profil, self.depo), D("10"))

    def test_tam_boy_acik_mevcut_davranis(self):
        profil, parca, op = self.kur(True, "64", "1")
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("3"))
        self.assertEqual(k.hedef_cikti_miktari, D("64"))                                          # çıktı miktarının katına yükselir
        self.assertEqual(kaydi_girdi_satirlari(k).get(girdi=profil).planlanan_miktar, D("1"))
        from core.models import OperasyonKaydiGirdi
        OperasyonKaydiGirdi.objects.filter(kayit=k, girdi=profil).update(gerceklesen_miktar=D("0.046875"))
        with self.assertRaisesMessage(UretimHatasi, "tam boy kesilmelidir"):
            operasyon_kaydi_onayla(k)                                                              # kesirli BOY girdisi hâlâ reddedilir
        o = self.ozet(ihtiyac_hesapla([(parca, D("3"))]))
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("1"))                                    # tam boya yukarı yuvarlanır
