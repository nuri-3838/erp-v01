"""ATÖLYE operatör ekranı (/atolye/): yetki/yönlendirme (operatör → /atolye/, yalnız mesai hesabı → /mesai/, yetkisiz 403), mesai açık değilken
üretim yok, istasyon listesi (fason gizli), açık emirler, "Üretildi" (tam sayı, fabrika ağı, personel + mesai kaydı izi, fazla üretim uyarısı),
bugünkü kendi son kaydını geri alma, personel kartından operatör yetkisi, kayıt listesinde "Giren"."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, IsIstasyonu, OperasyonKaydi, StokHareket
from core.services import atolye
from core.services.mesai import acik_kayit
from core.services.mesai_ag import ag_ekle
from core.services.mesai_hesap import hesap_olustur
from core.tests.ik_yardimci import personel_kur
from core.tests.test_istasyon_emri_kayit import Taban

D = Decimal
IZINLI, IZINSIZ = "10.0.0.5", "8.8.8.8"


class AtolyeTaban(Taban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ag_ekle(cidr="10.0.0.0/24", aciklama="fabrika")
        cls.fason_ist = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER (FASON)", sira=1, fason=True)
        cls.p = personel_kur(ad="errahman", soyad="operator", ise_giris_tarihi=date(2020, 1, 1))
        cls.u = hesap_olustur(cls.p, kullanici_adi="errahman", sifre="cokGizli!2026")
        atolye.operator_ayarla(cls.p, True)
        cls.p2 = personel_kur(ad="ayse", soyad="mesai", ise_giris_tarihi=date(2020, 1, 1))
        cls.u2 = hesap_olustur(cls.p2, kullanici_adi="aysem", sifre="cokGizli!2026")          # yalnız mesai hesabı (atölye yetkisi yok)
        cls.yon = User.objects.create_superuser("atyon", password="x")

    def baslat(self, user=None):
        self.client.force_login(user or self.u)
        return self.client.post(reverse("core:atolye_mesai"), {"eylem": "baslat"}, HTTP_X_REAL_IP=IZINLI)

    def uret(self, ie, miktar, ip=IZINLI, follow=True):
        return self.client.post(reverse("core:atolye_uret", args=[ie.pk]), {"miktar": miktar}, HTTP_X_REAL_IP=ip, follow=follow)

    @staticmethod
    def mesajlar(r):
        return [str(m) for m in r.context["messages"]]


class YetkiYonlendirmeTest(AtolyeTaban):
    def test_operator_panodan_atolyeye_yalniz_mesai_hesabi_mesaime(self):
        self.client.force_login(self.u)
        self.assertRedirects(self.client.get(reverse("core:pano")), reverse("core:atolye"))
        self.client.force_login(self.u2)
        self.assertRedirects(self.client.get(reverse("core:pano")), reverse("core:mesaim"))
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 403)                   # yetkisiz
        self.assertEqual(self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 302)                   # anonim → giriş

    def test_yonetici_personel_karti_olmadan_403_ve_operator_dugmesi(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 403)                   # personel kartı yok
        r = self.client.get(reverse("core:personel_detay", args=[self.p2.pk]))
        self.assertContains(r, "Atölye Operatörü Yap")
        self.client.post(reverse("core:mesai_hesap_atolye", args=[self.p2.pk]))
        self.assertTrue(EkranYetki.objects.filter(kullanici=self.u2, ekran_kod="atolye", silindi=False).exists())
        r = self.client.get(reverse("core:personel_detay", args=[self.p2.pk]))
        self.assertContains(r, "Atölye Yetkisini Kaldır")
        self.client.post(reverse("core:mesai_hesap_atolye", args=[self.p2.pk]))
        self.assertFalse(EkranYetki.objects.filter(kullanici=self.u2, ekran_kod="atolye", silindi=False).exists())
        self.assertTrue(EkranYetki.objects.filter(kullanici=self.u2, ekran_kod="atolye", silindi=True).exists())
        r = self.client.get(reverse("core:kullanici_yetkileri"))
        self.assertContains(r, "Atölye (Operatör Ekranı)")                                          # mevcut yetki ekranında da seçilebilir

    def test_atolye_sayfasi_menusuz_ve_sade(self):
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:atolye"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MESAİYİ BAŞLAT")
        self.assertContains(r, self.p.ad_soyad)
        self.assertNotContains(r, "Muhasebe")
        self.assertNotContains(r, "yan-pano")


class AkisTest(AtolyeTaban):
    def test_mesai_kapaliyken_uretim_yok(self):
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]), follow=True)
        self.assertIn("Önce mesainizi başlatın; mesai açık değilken üretim girilemez.", self.mesajlar(r))
        r = self.uret(self.ie_kesim, "4")
        self.assertTrue(any("Önce mesainizi başlatın" in m for m in self.mesajlar(r)))
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())

    def test_baslat_istasyon_sec_emirler(self):
        r = self.baslat()
        self.assertRedirects(r, reverse("core:atolye") + "?sec=1")
        self.assertIsNotNone(acik_kayit(self.p))
        r = self.client.get(reverse("core:atolye") + "?sec=1")
        self.assertContains(r, "İstasyonunuzu seçin")
        self.assertContains(r, reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertNotContains(r, "BORU LAZER (FASON)")                                             # fason istasyonu listede yok
        self.assertEqual(self.client.get(reverse("core:atolye_istasyon", args=[self.fason_ist.pk])).status_code, 404)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, self.emir.no)
        self.assertContains(r, "IK-KESILMIS")
        self.assertContains(r, "Stok için üretim")
        self.assertContains(r, "ÜRETİLDİ")
        self.assertContains(r, 'value="10"')                                                          # kalan: referans çıktı adedi (5 çalıştırma × 2), tam sayı
        self.assertNotContains(r, "Son kaydım")
        self.assertContains(r, "Mesaiyi Bitir")

    def test_uretildi_tam_sayi_ip_personel_mesai_izi_ve_fazla_uyarisi(self):
        self.baslat()
        r = self.uret(self.ie_kesim, "2,5")
        self.assertTrue(any("tam sayı" in m for m in self.mesajlar(r)))
        r = self.uret(self.ie_kesim, "4", ip=IZINSIZ)
        self.assertTrue(any("fabrika Wi-Fi" in m for m in self.mesajlar(r)))
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())
        r = self.uret(self.ie_kesim, "4")
        self.assertTrue(any("4 adet yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        k = OperasyonKaydi.objects.get(istasyon_emri=self.ie_kesim, silindi=False)
        self.assertEqual((k.durum, k.personel_id, k.mesai_kaydi_id, k.created_by_id, k.tarih), ("ONAYLI", self.p.pk, acik_kayit(self.p).pk, self.u.pk, date.today()))
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=k, tur="GIRIS").miktar, D("4"))
        self.assertContains(r, "Son kaydım")
        self.assertContains(r, k.no)
        r = self.uret(self.ie_kesim, "20")                                                           # kesimde fazlası serbest: uyarı
        self.assertTrue(any("kalanını aşıyor" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim, silindi=False).count(), 2)
        # ofis listesi/detayı "Giren"
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:operasyon_kayitlari") + "?sekme=tumu")
        self.assertContains(r, self.p.ad_soyad)
        r = self.client.get(reverse("core:operasyon_kaydi_detay", args=[k.pk]))
        self.assertContains(r, self.p.ad_soyad)
        self.assertContains(r, "mesai ")

    def test_geri_alma_yalniz_bugunku_kendi_son_kaydi(self):
        self.baslat()
        self.uret(self.ie_kesim, "4")
        ilk = OperasyonKaydi.objects.get(istasyon_emri=self.ie_kesim, silindi=False)
        self.uret(self.ie_kesim, "2")
        son = OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim, silindi=False).latest("pk")
        r = self.client.post(reverse("core:atolye_geri_al", args=[ilk.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("kendi SON kaydınızı" in m for m in self.mesajlar(r)), (self.mesajlar(r), ilk.pk, son.pk))
        ilk.refresh_from_db()
        self.assertFalse(ilk.silindi)
        r = self.client.post(reverse("core:atolye_geri_al", args=[son.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("geri alındı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        son.refresh_from_db()
        self.assertEqual((son.silindi, son.geri_alan_id), (True, self.u.pk))
        self.ie_kesim.refresh_from_db()
        self.assertEqual(self.ie_kesim.tamamlanan, D("2"))                                          # 4 adet = 2 çalıştırma kaldı
        self.assertContains(r, "Son kaydım")                                                          # şimdi ilk kayıt son kayıt → geri alınabilir
        # başka operatör geri alamaz
        atolye.operator_ayarla(self.p2, True)
        self.baslat(self.u2)
        r = self.client.post(reverse("core:atolye_geri_al", args=[ilk.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("kendi SON kaydınızı" in m for m in self.mesajlar(r)))
        # mesai bitince üretim/geri alma yok
        self.client.force_login(self.u)
        self.client.post(reverse("core:atolye_mesai"), {"eylem": "bitir"}, HTTP_X_REAL_IP=IZINLI)
        self.assertIsNone(acik_kayit(self.p))
        r = self.client.post(reverse("core:atolye_geri_al", args=[ilk.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("Önce mesainizi başlatın" in m for m in self.mesajlar(r)))

    def test_servis_kurallari(self):
        with self.assertRaisesMessage(atolye.AtolyeHatasi, "personel kartı yok"):
            atolye.uret(self.yon, self.ie_kesim, "1", IZINLI)
        with self.assertRaisesMessage(atolye.AtolyeHatasi, "Önce mesainizi başlatın"):
            atolye.uret(self.u, self.ie_kesim, "1", IZINLI)
        self.assertEqual(sorted(i.kod for i in atolye.istasyonlar()), ["BUKUM", "LAZER"])                 # fason (10) yok
        self.assertEqual(atolye.acik_emirler(self.lazer)[0]["ie"].pk, self.ie_kesim.pk)
        self.assertEqual(atolye.acik_emirler(self.bukum)[0]["kalan_tam"], "10")
