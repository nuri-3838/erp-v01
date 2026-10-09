"""FASON dönüş: "Geri al ve sil" — yalnız yönetici; onaylı belge tüm satırlarıyla ATOMİK geri alınır (stok hareketleri, maliyet fişi); faturaya bağlıysa önce
fatura bağı kaldırılmalı; çıktı sonradan kullanıldıysa engellenir ve hiçbir şey değişmez."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, FasonDonus, OperasyonKaydi, StokHareket
from core.services import fason_donus as fd
from core.services.depo_transfer import depo_transferi_yap
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar
from core.tests.test_fason_donus import TARIH
from core.tests.test_fason_fatura import FaturaBase

D = Decimal


class GeriAlTest(FaturaBase):
    def onayli(self, satirlar=None):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=satirlar or [(self.op.pk, "3")], onayla=True)

    def stoklar(self):
        return (eldeki_miktar(self.profil, self.fdepo), eldeki_miktar(self.ana, self.depo), eldeki_miktar(self.yan, self.depo))

    def test_onayli_donus_geri_alinir(self):
        d = self.onayli()
        k = d.kayitlar.get()
        self.assertEqual(self.stoklar(), (D("9"), D("3"), D("1")))
        fd.donus_geri_al_sil(d)
        d.refresh_from_db()
        k.refresh_from_db()
        self.assertTrue(d.silindi and k.silindi)
        self.assertEqual(self.stoklar(), (D("10"), D("0"), D("0")))                   # girdi geri geldi, çıktılar çıktı
        self.assertFalse(StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).exists())
        self.assertTrue(k.fis.silindi)                                                 # maliyet aktarım fişi iptal
        self.assertEqual(self.satir151()["fason_bekleyen"], D("0.00"))                 # bekleyen fason bedeli de kalmadı
        for st in (self.ana, self.yan):
            st.refresh_from_db()
            self.assertEqual(st.maliyet_miktar, D("0"))
            self.assertEqual(st.maliyet_deger_try, D("0.00"))

    def test_cok_satir_atomik(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3"), (self.op.pk, "6")], onayla=True)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("7"))
        # ikinci (son) kaydın çıktılarından birini tüket → o satır geri alınamaz → HİÇBİR satır geri alınmaz
        depo_transferi_yap(stok_id=self.ana.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=TARIH, miktar=D("8"))
        with self.assertRaises(FasonHatasi):
            fd.donus_geri_al_sil(d)
        d.refresh_from_db()
        self.assertFalse(d.silindi)
        self.assertEqual(d.kayitlar.filter(silindi=False).count(), 2)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("7"))               # değişmedi

    def test_cikti_kullanilmissa_engellenir(self):
        d = self.onayli()
        depo_transferi_yap(stok_id=self.yan.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=TARIH, miktar=D("1"))   # yan çıktı gitti
        with self.assertRaises(FasonHatasi) as c:
            fd.donus_geri_al_sil(d)
        self.assertIn("geri alınamadı", str(c.exception))
        d.refresh_from_db()
        self.assertFalse(d.silindi)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("9"))

    def test_faturaya_bagliysa_once_bag_kaldirilmali(self):
        d = self.onayli()
        fd.faturaya_bagla(d, self.fatura("36"))
        with self.assertRaises(FasonHatasi) as c:
            fd.donus_geri_al_sil(d)
        self.assertIn("fatura bağını kaldırın", str(c.exception))
        d.refresh_from_db()
        self.assertFalse(d.silindi)
        fd.faturadan_kopar(d)
        d.refresh_from_db()
        fd.donus_geri_al_sil(d)
        d.refresh_from_db()
        self.assertTrue(d.silindi)
        self.assertEqual(self.stoklar(), (D("10"), D("0"), D("0")))

    def test_taslak_belgede_silmeyle_ayni(self):
        self.fason_profil(10, 1000)
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        fd.donus_geri_al_sil(d)
        d.refresh_from_db()
        self.assertTrue(d.silindi)
        self.assertEqual(OperasyonKaydi.objects.filter(silindi=False, fason_cari=self.salim).count(), 0)

    def test_geri_alinan_belge_tekrar_girilebilir(self):
        d = self.onayli()
        fd.donus_geri_al_sil(d)
        d2 = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        self.assertEqual(self.stoklar(), (D("9"), D("3"), D("1")))
        self.assertNotEqual(d2.pk, d.pk)

    def test_gelen_adetli_donus_geri_alinir(self):
        self.fason_profil(10, 1000)
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3", "2", {self.yan.pk: "0"})], onayla=True)
        fd.donus_geri_al_sil(d)
        self.assertEqual(self.stoklar(), (D("10"), D("0"), D("0")))


class GeriAlEkranTest(FaturaBase):
    def hazir(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)

    def test_yalniz_yonetici_dugme_ve_islem(self):
        d = self.hazir()
        u = User.objects.create_user("gal_u", password="x")
        EkranYetki.objects.create(kullanici=u, ekran_kod="fason_donusleri")
        self.client.force_login(u)
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertEqual(det.status_code, 200)
        self.assertNotContains(det, "Geri al ve sil")                                   # ekran yetkili ama yönetici değil
        self.assertEqual(self.client.post(reverse("core:fason_donus_geri_al_sil", args=[d.pk])).status_code, 403)
        d.refresh_from_db()
        self.assertFalse(d.silindi)
        self.client.force_login(User.objects.create_superuser("gal_y", password="x"))
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "Geri al ve sil")

    def test_yonetici_geri_alir(self):
        d = self.hazir()
        self.client.force_login(User.objects.create_superuser("gal_y2", password="x"))
        r = self.client.post(reverse("core:fason_donus_geri_al_sil", args=[d.pk]))
        self.assertRedirects(r, reverse("core:fason_donusleri"))
        d.refresh_from_db()
        self.assertTrue(d.silindi)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("10"))

    def test_faturali_donusde_dugme_yerine_aciklama_ve_hata(self):
        d = self.hazir()
        fd.faturaya_bagla(d, self.fatura("36"))
        self.client.force_login(User.objects.create_superuser("gal_y3", password="x"))
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertNotContains(det, "Geri al ve sil")
        self.assertContains(det, "önce faturadan ayırın")
        r = self.client.post(reverse("core:fason_donus_geri_al_sil", args=[d.pk]), follow=True)
        self.assertContains(r, "fatura bağını kaldırın")
        d.refresh_from_db()
        self.assertFalse(d.silindi)

    def test_get_islem_yapmaz(self):
        d = self.hazir()
        self.client.force_login(User.objects.create_superuser("gal_y4", password="x"))
        self.client.get(reverse("core:fason_donus_geri_al_sil", args=[d.pk]))
        d.refresh_from_db()
        self.assertFalse(d.silindi)
