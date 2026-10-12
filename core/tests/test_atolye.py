"""ATÖLYE operatör ekranı (/atolye/): yetki/yönlendirme (operatör → /atolye/, yalnız mesai hesabı → /mesai/, yetkisiz 403), mesai açık değilken
üretim yok, istasyon listesi (fason gizli), açık emirler, Başla → Bitir (tam sayı, fabrika ağı, personel + mesai kaydı izi, fazla üretim uyarısı),
bugünkü kendi son kaydını geri alma, personel kartından operatör yetkisi, kayıt listesinde "Giren". Oturum kuralları: test_is_oturumu."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, IsIstasyonu, IsOturumu, OperasyonKaydi, StokHareket
from core.services import atolye
from core.services.mesai import acik_kayit
from core.tarih import tr_bugun
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

    def basla(self, ie, ekip=(), ip=IZINLI, follow=True):
        return self.client.post(reverse("core:atolye_oturum_baslat", args=[ie.pk]), {"ekip": [str(p) for p in ekip]}, HTTP_X_REAL_IP=ip, follow=follow)

    def bitir(self, oturum, miktar, ip=IZINLI, follow=True, **ek):
        return self.client.post(reverse("core:atolye_oturum_bitir", args=[oturum.pk]), {"miktar": miktar, **ek}, HTTP_X_REAL_IP=ip, follow=follow)

    def uret(self, ie, miktar, ip=IZINLI, follow=True):
        """Başla + Bitir kısa yolu (ekipsiz)."""
        self.basla(ie, ip=ip)
        o = IsOturumu.objects.filter(istasyon_emri=ie, durum="ACIK").latest("pk")
        return self.bitir(o, miktar, ip=ip, follow=follow)

    @staticmethod
    def acik_oturum(ie):
        return IsOturumu.objects.filter(istasyon_emri=ie, durum="ACIK").order_by("-pk").first()

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

    def test_yonetici_personel_karti_olmadan_izleme_ve_operator_dugmesi(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:atolye"))                                                  # personel kartı yok → yönetici görüntüleme
        self.assertContains(r, "Yönetici görüntüleme")
        self.assertNotContains(r, "MESAİYİ BAŞLAT")
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, self.emir.no)
        self.assertNotContains(r, "BAŞLA")
        self.assertNotContains(r, "Mesaiyi Bitir")
        r = self.basla(self.ie_kesim)                                                                 # izlemede üretim/mesai yok
        self.assertTrue(any("personel kartı yok" in m for m in self.mesajlar(r)))
        r = self.client.post(reverse("core:atolye_mesai"), {"eylem": "baslat"}, HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("personel kartı yok" in m for m in self.mesajlar(r)))
        bos = User.objects.create_user("bosyetkili", password="x")                                  # yönetici olmayan, personelsiz, yetkili → 403
        EkranYetki.objects.create(kullanici=bos, ekran_kod="atolye")
        self.client.force_login(bos)
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 403)
        self.client.force_login(self.yon)
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
        self.assertContains(r, "İş Oturumları")

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
        r = self.basla(self.ie_kesim)
        self.assertTrue(any("Önce mesainizi başlatın" in m for m in self.mesajlar(r)))
        self.assertFalse(IsOturumu.objects.exists())
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
        self.assertContains(r, "BAŞLA")
        self.assertNotContains(r, "ÜRETİLDİ")                                                         # süresiz hızlı giriş atölyeden kaldırıldı
        self.assertNotContains(r, "Son kaydım")
        self.assertContains(r, "Mesaiyi Bitir")

    def test_uretildi_tam_sayi_ip_personel_mesai_izi_ve_fazla_uyarisi(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        r = self.bitir(o, "2,5")
        self.assertTrue(any("tam sayı" in m for m in self.mesajlar(r)))
        r = self.bitir(o, "4", ip=IZINSIZ)
        self.assertTrue(any("fabrika Wi-Fi" in m for m in self.mesajlar(r)))
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")                                                             # hata: oturum açık kaldı
        r = self.bitir(o, "4")
        self.assertTrue(any("4 adet yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        k = OperasyonKaydi.objects.get(istasyon_emri=self.ie_kesim, silindi=False)
        self.assertEqual((k.durum, k.personel_id, k.mesai_kaydi_id, k.created_by_id, k.tarih), ("ONAYLI", self.p.pk, acik_kayit(self.p).pk, self.u.pk, tr_bugun()))
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=k, tur="GIRIS").miktar, D("4"))
        self.assertContains(r, "Son kaydım")
        self.assertContains(r, k.no)
        o.refresh_from_db()
        self.assertEqual((o.durum, o.kayit_id, o.miktar, o.bitiren_id), ("BITTI", k.pk, D("4"), self.p.pk))
        r = self.uret(self.ie_kesim, "20")                                                           # kesimde fazlası serbest: uyarı
        self.assertTrue(any("kalanını aşıyor" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim, silindi=False).count(), 2)
        # ofis listesi/detayı "Giren" + iş oturumu
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:operasyon_kayitlari") + "?sekme=tumu")
        self.assertContains(r, self.p.ad_soyad)
        r = self.client.get(reverse("core:operasyon_kaydi_detay", args=[k.pk]))
        self.assertContains(r, self.p.ad_soyad)
        self.assertContains(r, "mesai ")
        self.assertContains(r, "İş Oturumu")
        self.assertContains(r, "1 kişi")

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
        self.assertEqual(IsOturumu.objects.get(kayit=son).durum, "BITTI")                             # oturum (süre) kalır
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
            atolye.oturum_baslat(self.yon, self.ie_kesim, [], IZINLI)
        with self.assertRaisesMessage(atolye.AtolyeHatasi, "Önce mesainizi başlatın"):
            atolye.oturum_baslat(self.u, self.ie_kesim, [], IZINLI)
        self.assertEqual(sorted(i.kod for i in atolye.istasyonlar()), ["BUKUM", "LAZER"])                 # fason (10) yok
        self.assertEqual(atolye.acik_emirler(self.lazer)[0]["ie"].pk, self.ie_kesim.pk)
        self.assertEqual(atolye.acik_emirler(self.bukum)[0]["kalan_tam"], "10")
