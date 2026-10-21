"""ATÖLYE iş oturumu — TEK EKİP + SORUMLU modeli (2026-10-21): emirde en çok BİR açık oturum; ilk başlatan sorumlu (adet/fire, Bitir, İptal, Devret yalnız onda); ikinci kişi
"Ekibe katıl" der (adet giremez, yalnız "Ekipten ayrıl"); sorumlu Bitir/İptal deyince tüm ekip kapanır; devretme; mesai kapanışında (elle / ofis / fabrika dışı / gece / süpürme)
otomatik devir, ekip boşsa "Adet bekliyor" (İPTAL DEĞİL) ve sonradan adet girişi; yönetici işlemleri (denetim kaydı, not zorunlu); ofis/ÜS kaydı engeli (yalnız süper kullanıcı
notla aşar); bitmiş emirde açık iş kartı; ofis mesai kapatınca üyelik kapanır; fabrika dışı ilk IP kaydı."""
import datetime
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import IsOturumu, IsOturumuOlay, IsOturumuUyesi, IstasyonEmri, MesaiKaydi, OperasyonKaydi, Profil
from core.services import is_oturumu, mesai as mesai_servis, mesai_otomatik, uretim
from core.services.mesai import acik_kayit
from core.tarih import tr_bugun
from core.tests import test_is_oturumu as tio
from core.tests.test_atolye import IZINLI

D = Decimal


class TekEkipTaban(tio.OturumTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haydar = User.objects.create_user("haydaryon", password="x")                           # yönetici profilli ama SÜPER KULLANICI DEĞİL (Haydar'ın yönetici hesabı gibi)
        Profil.objects.create(kullanici=cls.haydar, yonetici=True)

    def post(self, user, ad, pk, veri=None, ip=IZINLI):
        self.client.force_login(user)
        return self.client.post(reverse(ad, args=[pk]), veri or {}, HTTP_X_REAL_IP=ip, follow=True)

    def katil(self, oturum, user):
        return self.post(user, "core:atolye_oturum_katil", oturum.pk)

    def ayril(self, oturum, user):
        return self.post(user, "core:atolye_oturum_ayril", oturum.pk)

    def devret(self, oturum, user, yeni, ayril=False):
        return self.post(user, "core:atolye_oturum_devret", oturum.pk, {"yeni": yeni.pk, **({"ayril": "1"} if ayril else {})})

    def adet_gir(self, oturum, user, miktar, fire=""):
        return self.post(user, "core:atolye_oturum_adet_gir", oturum.pk, {"miktar": miktar, "fire": fire})

    def yonetici(self, oturum, user, **veri):
        self.client.force_login(user)
        return self.client.post(reverse("core:is_oturumu_yonetici_islem", args=[oturum.pk]), veri, HTTP_X_REAL_IP=IZINLI, follow=True)

    def oto_kapat(self, personel, sebep="gece"):
        k = MesaiKaydi.objects.get(personel=personel, cikis_zamani__isnull=True)
        return mesai_otomatik.kapat(k, sebep=sebep, simdi=timezone.now())

    def turler(self, oturum):
        return list(oturum.olaylar.values_list("tur", flat=True))


class TekAcikOturumTest(TekEkipTaban):
    def test_ikinci_kisi_yalniz_katilabilir_basla_reddedilir(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)                                                                      # errahman (sorumlu)
        o = self.acik_oturum(self.ie_kesim)
        self.assertEqual((o.sorumlu_id, o.baslatan_id), (self.p.pk, self.p.pk))
        self.client.force_login(self.u3)
        r = self.basla(self.ie_kesim)                                                                  # mehmet Başla diyemez
        self.assertTrue(any("sorumluluğunda bir ekip çalışıyor" in m and "Ekibe katıl" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(IsOturumu.objects.filter(istasyon_emri=self.ie_kesim, durum="ACIK").count(), 1)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "EKİBE KATIL")
        self.assertContains(r, "ERRAHMAN OPERATOR")                                                    # sorumlu adı + ekip görünür
        self.assertNotContains(r, "▶ İŞİ BAŞLAT")
        r = self.katil(o, self.u3)
        self.assertTrue(any("ekibe katıldınız" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(sorted(u.personel_id for u in o.uyeler.filter(ayrilma_zamani__isnull=True)), sorted([self.p.pk, self.p3.pk]))
        self.assertEqual(self.turler(o), ["KATILDI"])

    def test_veritabani_emirde_tek_acik_oturumu_zorlar(self):
        self.baslat()
        self.basla(self.ie_kesim)
        with self.assertRaises(IntegrityError), transaction.atomic():
            IsOturumu.objects.create(istasyon_emri=self.ie_kesim, uretim_emri=self.emir, istasyon=self.lazer, baslatan=self.p3, sorumlu=self.p3, is_tarihi=tr_bugun(),
                                     baslangic_zamani=timezone.now())

    def test_katilan_meşgulse_ya_da_mesaisizse_katilamaz(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        r = self.katil(o, self.u3)                                                                     # mehmet mesaide değil
        self.assertTrue(any("mesainizi başlatın" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.baslat(self.u3)
        self.basla(self.ie_bukum)                                                                      # (girdi yok: üretim hatası olmaz, yalnız oturum açılır)
        r = self.katil(o, self.u3)
        self.assertTrue(any("başka bir işte" in m for m in self.mesajlar(r)), self.mesajlar(r))


class KatilanYetkisiTest(TekEkipTaban):
    def kur(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        return o

    def test_katilan_adet_giremez_bitir_iptal_gormez_yalniz_ayrilir(self):
        o = self.kur()
        self.client.force_login(self.u3)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertNotContains(r, "BİTİR")
        self.assertNotContains(r, "İptal (iş yapılmadı)")
        self.assertContains(r, "Ekipten ayrıl")
        r = self.client.get(reverse("core:atolye"))
        self.assertContains(r, "Açık işiniz var")
        self.assertContains(r, "Ekipten ayrıl")
        r = self.bitir(o, "4")
        self.assertTrue(any("yalnızca sorumlu kişiye aittir" in m for m in self.mesajlar(r)), self.mesajlar(r))
        r = self.post(self.u3, "core:atolye_oturum_iptal", o.pk)
        self.assertTrue(any("yalnızca sorumlu kişiye aittir" in m for m in self.mesajlar(r)), self.mesajlar(r))
        with self.assertRaises(is_oturumu.OturumHatasi):
            is_oturumu.bitir(self.p3, o, D("4"))
        o.refresh_from_db()
        self.assertEqual((o.durum, OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).count()), ("ACIK", 0))

    def test_katilan_ayrilir_sure_sayilir_sonra_baska_ise_girebilir(self):
        o = self.kur()
        r = self.ayril(o, self.u3)
        self.assertTrue(any("ekipten ayrıldınız" in m for m in self.mesajlar(r)), self.mesajlar(r))
        u = IsOturumuUyesi.objects.get(oturum=o, personel=self.p3)
        self.assertIsNotNone(u.ayrilma_zamani)
        self.assertEqual(self.turler(o), ["KATILDI", "AYRILDI"])
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p3))
        self.assertEqual(is_oturumu.ozet(IsOturumu.objects.prefetch_related("uyeler__personel").get(pk=o.pk))["kisi"], 2)      # süresi (adam-saat) raporda kalır

    def test_sorumlu_ayrilamaz_devretmeli(self):
        o = self.kur()
        r = self.ayril(o, self.u)
        self.assertTrue(any("Sorumlu ekipten ayrılamaz" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:atolye"))
        self.assertNotContains(r, "Ekipten ayrıl")
        self.assertContains(r, "Sorumluluğu devret")


class BitirKapatirTest(TekEkipTaban):
    def test_sorumlu_bitirince_tum_ekip_ayni_anda_kapanir(self):
        self.baslat(self.u2)
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim, ekip=[self.p2.pk])
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        r = self.bitir(o, "4")                                                                         # sorumlu errahman (client mehmet'le girişliydi → yeniden giriş)
        self.assertTrue(any("sorumlu kişiye aittir" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.client.force_login(self.u)
        r = self.bitir(o, "4")
        self.assertTrue(any("4 adet yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.bitiren_id, o.kayit.personel_id), ("BITTI", self.p.pk, self.p.pk))
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 0)
        self.assertEqual({u.ayrilma_zamani for u in o.uyeler.all()}, {o.bitis_zamani})                  # üç kişi AYNI ANDA
        for p in (self.p, self.p2, self.p3):
            self.assertIsNone(is_oturumu.aktif_uyelik(p))

    def test_sorumlu_iptal_edince_tum_ekip_kapanir(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        self.post(self.u, "core:atolye_oturum_iptal", o.pk)
        o.refresh_from_db()
        self.assertEqual((o.durum, o.uyeler.filter(ayrilma_zamani__isnull=True).count()), ("IPTAL", 0))
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p3))


class DevretTest(TekEkipTaban):
    def kur(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        return o

    def test_devret_devreden_ekipte_kalir_yeni_sorumlu_bitirir(self):
        o = self.kur()
        r = self.devret(o, self.u, self.p3)
        self.assertTrue(any("sorumluluk MEHMET USTA kişisine devredildi" in m.upper() or "devredildi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.sorumlu_id, self.p3.pk)
        self.assertEqual(o.baslatan_id, self.p.pk)                                                     # ilk başlatan iz olarak kalır
        self.assertIsNone(IsOturumuUyesi.objects.get(oturum=o, personel=self.p).ayrilma_zamani)        # devreden ekipte katılan olarak kaldı
        self.assertEqual(self.turler(o), ["KATILDI", "DEVIR"])
        r = self.bitir(o, "4")                                                                         # artık errahman (login: u3 → hata beklenir, aşağıda doğru kullanıcı)
        self.client.force_login(self.u3)
        r = self.bitir(o, "4")
        self.assertTrue(any("4 adet yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.kayit.personel_id, self.p3.pk)                                              # kayıt personeli = yeni sorumlu

    def test_devret_ve_ayril_kutusu(self):
        o = self.kur()
        self.devret(o, self.u, self.p3, ayril=True)
        self.assertIsNotNone(IsOturumuUyesi.objects.get(oturum=o, personel=self.p).ayrilma_zamani)
        self.assertEqual(o.olaylar.get(tur="DEVIR").aciklama, "devreden ayrıldı")
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p))

    def test_devret_kurallari(self):
        o = self.kur()
        r = self.devret(o, self.u3, self.p)                                                            # sorumlu olmayan devredemez
        self.assertTrue(any("yalnızca sorumlu kişiye aittir" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.baslat(self.u2)
        r = self.devret(o, self.u, self.p2)                                                            # ekipte olmayana devredilemez
        self.assertTrue(any("ekibinde değil" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.sorumlu_id, self.p.pk)

    def test_tek_basina_sorumlu_devret_secenegi_yok(self):
        self.baslat()
        self.basla(self.ie_kesim)
        r = self.client.get(reverse("core:atolye"))
        self.assertNotContains(r, "Sorumluluğu devret")


class OtomatikDevirTest(TekEkipTaban):
    def kur_uclu(self):
        self.baslat(self.u2)
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim, ekip=[self.p2.pk])                                                   # p sorumlu, p2 baştan ekipte
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)                                                                         # p3 sonradan
        return o

    def test_sorumlunun_mesaisi_kapaninca_en_uzun_sureden_ekipte_olana_gecer(self):
        o = self.kur_uclu()
        self.oto_kapat(self.p, "gece")
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ACIK", self.p2.pk))                                 # p2 (oturum başından beri) p3'ten eski
        self.assertIsNotNone(IsOturumuUyesi.objects.get(oturum=o, personel=self.p).ayrilma_zamani)
        e = o.olaylar.get(tur="OTOMATIK_DEVIR")
        self.assertEqual((e.personel_id, e.hedef_id), (self.p.pk, self.p2.pk))
        self.oto_kapat(self.p2, "dis_ip")                                                              # p2 de kapanınca p3'e geçer
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ACIK", self.p3.pk))

    def test_elle_mesai_bitiren_sorumlu_ekip_varsa_devreder(self):
        o = self.kur_uclu()
        r = self.mesai_bitir(self.u)
        self.assertTrue(any("Mesai bitirildi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ACIK", self.p2.pk))

    def test_ofis_mesaiyi_kapatinca_uyelik_kapanir_sorumluysa_devir(self):
        o = self.kur_uclu()
        k = acik_kayit(self.p)
        mesai_servis.duzelt(k, giris_zamani=k.giris_zamani, cikis_zamani=timezone.now(), duzeltme_notu="ofis kapattı", kullanici=self.yon)
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ACIK", self.p2.pk))
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p))
        k3 = acik_kayit(self.p3)                                                                        # üye olan p3'ün mesaisini ofis SİLERSE de üyelik kapanır
        mesai_servis.sil(k3, kullanici=self.yon)
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p3))
        o.refresh_from_db()
        self.assertEqual(o.sorumlu_id, self.p2.pk)


class AdetBekliyorTest(TekEkipTaban):
    def test_ekip_bossa_mesai_kapaninca_adet_bekliyor_iptal_degil(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        k = self.oto_kapat(self.p, "gece")
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id, o.kayit_id, o.miktar), ("ADET", self.p.pk, None, None))
        self.assertGreaterEqual(o.bitis_zamani, k.cikis_zamani)                                         # süre korunur (üyelik katılımından önceye gitmez)
        self.assertEqual(self.turler(o), ["ADET_BEKLIYOR"])
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p))
        self.assertEqual(is_oturumu.ozet(IsOturumu.objects.prefetch_related("uyeler__personel").get(pk=o.pk))["kisi"], 1)
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())

    def test_sorumlu_sonraki_giriste_karti_gorur_adedi_girer_uretim_kaydi_olusur(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.oto_kapat(self.p, "gece")
        o.refresh_from_db()
        bitis = o.bitis_zamani
        r = self.client.get(reverse("core:atolye"))                                                    # mesai KAPALI iken de kart görünür
        self.assertContains(r, "ADET BEKLİYOR")
        self.assertContains(r, "ADEDİ GİR")
        self.baslat()
        r = self.adet_gir(o, self.u, "4", "1")
        self.assertTrue(any("4 sağlam + 1 fire yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.miktar, o.fire, o.bitiren_id, o.bitis_zamani), ("BITTI", D("4"), D("1"), self.p.pk, bitis))
        self.assertIsNotNone(o.kayit_id)
        self.assertEqual(o.kayit.personel_id, self.p.pk)
        self.assertEqual(self.turler(o), ["ADET_BEKLIYOR", "ADET_GIRILDI"])
        self.assertEqual(IstasyonEmri.objects.get(pk=self.ie_kesim.pk).tamamlanan, D("2"))             # 4 sağlam / 2 = 2 çalıştırma

    def test_adet_bekleyen_varken_ayni_emre_yeni_oturum_acilabilir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        self.oto_kapat(self.p, "gece")
        self.baslat(self.u3)
        r = self.basla(self.ie_kesim, follow=False) if False else None
        self.client.force_login(self.u3)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "Adet bekliyor")
        self.assertContains(r, "Yeni iş açabilirsiniz")
        r = self.basla(self.ie_kesim)
        self.assertTrue(any("iş başladı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(IsOturumu.objects.filter(istasyon_emri=self.ie_kesim, durum="ACIK").count(), 1)
        self.assertEqual(IsOturumu.objects.filter(istasyon_emri=self.ie_kesim, durum="ADET").count(), 1)

    def test_baska_sorumlu_adet_giremez(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.oto_kapat(self.p, "gece")
        self.baslat(self.u3)
        r = self.adet_gir(o, self.u3, "4")
        self.assertTrue(any("yalnızca sorumlu" in m for m in self.mesajlar(r)), self.mesajlar(r))

    def test_uretim_hatasinda_adet_bekliyor_kalir(self):
        self.baslat()
        self.basla(self.ie_bukum)                                                                      # girdi (kesilmiş) yok
        o = self.acik_oturum(self.ie_bukum)
        self.oto_kapat(self.p, "gece")
        self.baslat()
        r = self.adet_gir(o, self.u, "3")
        self.assertTrue(any("adet bekliyor durumunda kaldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")


class MesaiBitirAdetTest(TekEkipTaban):
    def test_tek_basina_sorumlu_adet_girerse_oturum_normal_bitir_ile_kapanir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, reverse("core:atolye_mesai_bitir"))                                      # üstteki Mesaiyi Bitir → adet sayfası
        r = self.client.get(reverse("core:atolye_mesai_bitir"))
        self.assertContains(r, "Adet girmeden bırak")
        r = self.client.post(reverse("core:atolye_mesai_bitir"), {}, HTTP_X_REAL_IP=IZINLI, follow=True)                # hiçbiri: mesai bitmez
        self.assertTrue(any("Açık işiniz var" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertIsNotNone(acik_kayit(self.p))
        r = self.client.post(reverse("core:atolye_mesai_bitir"), {"miktar": "4", "fire": "0"}, HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("Mesai bitirildi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.miktar, o.bitiren_id), ("BITTI", D("4"), self.p.pk))
        self.assertIsNone(acik_kayit(self.p))

    def test_adet_girmeden_onay_kutusuyla_adet_bekliyor(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        r = self.client.post(reverse("core:atolye_mesai_bitir"), {"bekle": "1"}, HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("Mesai bitirildi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ADET", self.p.pk))
        self.assertIsNone(acik_kayit(self.p))
        self.assertEqual(self.turler(o), ["ADET_BEKLIYOR"])

    def test_ekipli_sorumlu_ve_uye_icin_adet_kutusu_yok(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        self.client.force_login(self.u3)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertNotContains(r, reverse("core:atolye_mesai_bitir"))                                   # üye: doğrudan mesai bitir
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertNotContains(r, reverse("core:atolye_mesai_bitir"))                                   # ekipli sorumlu: otomatik devir (adet gerekmez)


class YoneticiTest(TekEkipTaban):
    def kur(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        return o

    def test_sorumlu_degistir(self):
        o = self.kur()
        r = self.yonetici(o, self.haydar, eylem="sorumlu", yeni=self.p3.pk, aciklama="vardiya değişti")
        self.assertTrue(any("sorumlu" in m and "değiştirildi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.sorumlu_id, self.p3.pk)
        e = o.olaylar.get(tur="YONETICI_SORUMLU")
        self.assertEqual((e.personel_id, e.hedef_id, e.kullanici_id, e.aciklama), (self.p.pk, self.p3.pk, self.haydar.pk, "vardiya değişti"))
        r = self.yonetici(o, self.haydar, eylem="sorumlu", yeni=self.p2.pk)                             # ekipte olmayan
        self.assertTrue(any("ekibinde değil" in m for m in self.mesajlar(r)), self.mesajlar(r))

    def test_adet_bekliyor_olarak_kapat_not_zorunlu_sure_sayilir(self):
        o = self.kur()
        r = self.yonetici(o, self.haydar, eylem="adet_bekliyor", aciklama="")
        self.assertTrue(any("Not zorunlu" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")
        self.yonetici(o, self.haydar, eylem="adet_bekliyor", aciklama="vardiya bitti, adet sonra")
        o.refresh_from_db()
        self.assertEqual((o.durum, o.uyeler.filter(ayrilma_zamani__isnull=True).count(), o.bitiren_id), ("ADET", 0, None))
        self.assertIn("vardiya bitti", o.aciklama)
        self.assertEqual(self.turler(o), ["KATILDI", "YONETICI_ADET_BEKLIYOR"])
        self.assertEqual(o.olaylar.get(tur="YONETICI_ADET_BEKLIYOR").kullanici_id, self.haydar.pk)

    def test_iptal_et_not_zorunlu_tum_ekip_kapanir(self):
        o = self.kur()
        self.yonetici(o, self.haydar, eylem="iptal", aciklama=" ")
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")
        self.yonetici(o, self.haydar, eylem="iptal", aciklama="yanlış başlatıldı")
        o.refresh_from_db()
        self.assertEqual((o.durum, o.uyeler.filter(ayrilma_zamani__isnull=True).count()), ("IPTAL", 0))
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p3))
        self.assertEqual(self.turler(o)[-1], "YONETICI_IPTAL")

    def test_adet_bekleyene_yonetici_adet_girer_ya_da_iptal_eder(self):
        o = self.kur()
        self.yonetici(o, self.haydar, eylem="adet_bekliyor", aciklama="adet sonra")
        r = self.yonetici(o, self.haydar, eylem="adet_gir", miktar="4", fire="0")
        self.assertTrue(any("4 sağlam" in m and "yazıldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.miktar, o.bitiren_id), ("BITTI", D("4"), None))
        self.assertEqual(o.kayit.personel_id, self.p.pk)
        self.assertEqual(self.turler(o)[-1], "ADET_GIRILDI")
        self.assertIsNone(o.olaylar.get(tur="ADET_GIRILDI").personel_id)                                # yönetici girdi
        o2 = IsOturumu.objects.get(pk=o.pk)
        self.baslat(self.u3) if acik_kayit(self.p3) is None else None
        self.client.force_login(self.u3)
        self.basla(self.ie_kesim)
        o3 = self.acik_oturum(self.ie_kesim)
        self.yonetici(o3, self.haydar, eylem="adet_bekliyor", aciklama="bekle")
        r = self.yonetici(o3, self.haydar, eylem="iptal", aciklama=" ")
        self.assertTrue(any("Not zorunlu" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.yonetici(o3, self.haydar, eylem="iptal", aciklama="emir başka yoldan bitti")
        o3.refresh_from_db()
        self.assertEqual(o3.durum, "IPTAL")
        self.assertIsNotNone(o2)

    def test_sorumlu_degistir_adet_bekleyende_gecmis_uyelerden(self):
        o = self.kur()
        self.yonetici(o, self.haydar, eylem="adet_bekliyor", aciklama="bekle")
        self.yonetici(o, self.haydar, eylem="sorumlu", yeni=self.p3.pk)
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ADET", self.p3.pk))

    def test_operator_yonetici_islemi_yapamaz_ve_ekranlar_acilir(self):
        o = self.kur()
        self.client.force_login(self.u)
        r = self.client.post(reverse("core:is_oturumu_yonetici_islem", args=[o.pk]), {"eylem": "iptal", "aciklama": "x"}, HTTP_X_REAL_IP=IZINLI)
        self.assertIn(r.status_code, (302, 403))
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:atolye_takip") + "?sekme=simdi")
        self.assertContains(r, "Sorumlu:")
        self.assertContains(r, "Adet bekliyor olarak kapat")
        self.assertContains(r, "Sorumluyu değiştir")
        r = self.client.get(reverse("core:is_oturumlari") + "?durum=ACIK")
        self.assertContains(r, "Yönet")
        self.yonetici(o, self.yon, eylem="adet_bekliyor", aciklama="listeden")
        r = self.client.get(reverse("core:atolye_takip") + "?sekme=simdi")
        self.assertContains(r, "Adet bekleyen işler")
        self.assertContains(r, "Adet gir")
        r = self.client.get(reverse("core:is_oturumlari") + "?durum=ADET")
        self.assertContains(r, "Adet bekliyor")
        self.assertContains(r, "Adet gir")
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "adedi girilmeyi bekliyor")


class OfisEngeliTest(TekEkipTaban):
    def uret_ofis(self, user, ie, miktar=None, asma=""):
        self.client.force_login(user)
        veri = {"miktar": miktar or "", "tarih": "", "geri": ""}
        if asma:
            veri["asma_notu"] = asma
        return self.client.post(reverse("core:istasyon_emri_uret", args=[ie.pk]), veri, HTTP_X_REAL_IP=IZINLI, follow=True)

    def test_acik_oturum_varken_ofis_kaydi_engellenir_sure_kullanici_asamaz(self):
        self.baslat()
        self.basla(self.ie_kesim)
        r = self.uret_ofis(self.yon, self.ie_kesim)                                                     # süper kullanıcı notsuz: engel
        self.assertContains(r, "ekibi çalışıyor")
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())
        r = self.uret_ofis(self.haydar, self.ie_kesim, asma="denedim")                                  # yönetici profilli ama süper DEĞİL: not da işe yaramaz
        self.assertContains(r, "ekibi çalışıyor")
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())
        with self.assertRaises(uretim.UretimHatasi):                                                    # servis de kendisi korur
            uretim.istasyon_emri_uret(self.ie_kesim, kullanici=self.haydar, oturum_asma=(self.haydar, "not"))

    def test_toplu_uretim_de_engellenir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:uretim_emri_toplu_uret", args=[self.emir.pk]), {"istasyon": self.lazer.pk}, HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("Toplu üretim yapılmadı" in m and "ekibi çalışıyor" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertFalse(OperasyonKaydi.objects.filter(uretim_emri=self.emir).exists())                  # atomik: hiçbir şey yazılmadı

    def test_super_kullanici_notla_asar_olay_kaydi_emir_tamamlaninca_oturum_otomatik_iptal(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.uret_ofis(self.yon, self.ie_kesim, asma="üretim müdürü onayladı")                          # tüm kalan 10 → emir BITTI
        k = OperasyonKaydi.objects.get(istasyon_emri=self.ie_kesim)
        self.assertIn("üretim müdürü onayladı", k.aciklama)
        o.refresh_from_db()
        self.assertEqual(o.durum, "IPTAL")
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 0)
        self.assertEqual(self.turler(o), ["OFIS_ASILDI", "OFIS_ASMA_IPTAL"])
        self.assertEqual(o.olaylar.get(tur="OFIS_ASILDI").kullanici_id, self.yon.pk)
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p))                                              # personel serbest

    def test_kismi_ofis_kaydinda_oturum_acik_kalir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.uret_ofis(self.yon, self.ie_kesim, miktar="4", asma="kısmi")
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")
        self.assertEqual(self.turler(o), ["OFIS_ASILDI"])

    def test_adet_bekleyen_oturum_da_ofis_kaydini_engeller_ve_asilirsa_dokunulmaz(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.oto_kapat(self.p, "gece")
        r = self.uret_ofis(self.yon, self.ie_kesim)
        self.assertContains(r, "adet bekliyor")
        self.assertFalse(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim).exists())
        self.uret_ofis(self.yon, self.ie_kesim, asma="elle girildi")
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")                                                               # adet bekleyene dokunulmaz

    def test_oturumsuz_emirde_ofis_kaydi_eskisi_gibi(self):
        self.uret_ofis(self.haydar, self.ie_kesim)
        self.assertTrue(OperasyonKaydi.objects.filter(istasyon_emri=self.ie_kesim, durum="ONAYLI").exists())

    def test_asma_notu_alani_yalniz_super_kullaniciya_gorunur(self):
        self.client.force_login(self.yon)
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]) + "?sekme=istasyonlar"), "asma_notu")
        self.client.force_login(self.haydar)
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]) + "?sekme=istasyonlar"), "asma_notu")


class AcikIsKartiTest(TekEkipTaban):
    def test_bitmis_emirde_acik_oturum_karti_gorunur_ve_iptal_edilebilir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        IstasyonEmri.objects.filter(pk=self.ie_kesim.pk).update(durum="BITTI")                         # emir başka yoldan tamamlandı (eski sorun)
        for url in (reverse("core:atolye"), reverse("core:atolye_istasyon", args=[self.bukum.pk]), reverse("core:atolye_istasyon", args=[self.lazer.pk])):
            r = self.client.get(url)
            self.assertContains(r, "Açık işiniz var")
            self.assertContains(r, "Bu emir tamamlandı / kapandı")
            self.assertContains(r, "İptal (iş yapılmadı)")
            self.assertNotContains(r, "⏹ BİTİR")                                                       # adet girilemez
        r = self.post(self.u, "core:atolye_oturum_iptal", o.pk)
        o.refresh_from_db()
        self.assertEqual(o.durum, "IPTAL")
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p))

    def test_mesai_kapaliyken_de_kart_gorunur_ve_iptal_edilir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        MesaiKaydi.objects.filter(personel=self.p, cikis_zamani__isnull=True).update(cikis_zamani=timezone.now())      # mesai kapandı ama üyelik açık (eski Errahman durumu)
        r = self.client.get(reverse("core:atolye"))
        self.assertContains(r, "MESAİYİ BAŞLAT")
        self.assertContains(r, "Açık işiniz var")
        self.assertContains(r, "İptal (iş yapılmadı)")
        r = self.post(self.u, "core:atolye_oturum_iptal", o.pk)
        o.refresh_from_db()
        self.assertEqual(o.durum, "IPTAL")

    def test_supurme_mesaisi_kapali_acik_uyeligi_toplar(self):
        self.baslat(self.u3)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.katil(o, self.u3)
        k = acik_kayit(self.p3)
        cikis = timezone.now()
        MesaiKaydi.objects.filter(pk=k.pk).update(cikis_zamani=cikis)                                   # ofis düzeltmesini atlayan eski/bozuk veri
        kuru = is_oturumu.supurme(kuru=True)
        self.assertEqual([x["uye"].personel_id for x in kuru], [self.p3.pk])
        self.assertIsNotNone(is_oturumu.aktif_uyelik(self.p3))                                          # kuru: yazmadı
        sonuc = is_oturumu.supurme()
        self.assertEqual(len(sonuc), 1)
        self.assertIsNone(is_oturumu.aktif_uyelik(self.p3))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.sorumlu_id), ("ACIK", self.p.pk))
        self.assertEqual(is_oturumu.supurme(), [])                                                      # ikinci koşuda yapılacak şey yok
        cikti = StringIO()
        call_command("mesai_otomatik_kapat", "--kuru", stdout=cikti)                                    # komut kırılmaz
        self.assertTrue(cikti.getvalue())

    def test_supurme_sorumluyu_da_toplar_ekip_bossa_adet_bekliyor(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        MesaiKaydi.objects.filter(personel=self.p, cikis_zamani__isnull=True).update(cikis_zamani=timezone.now())
        is_oturumu.supurme()
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")


class FabrikaDisiIpTest(TekEkipTaban):
    def test_ilk_dis_ip_kaydedilir_kapanista_silinmez_fabrikada_sifirlanir(self):
        self.baslat()
        k = MesaiKaydi.objects.get(personel=self.p)
        simdi = timezone.now()
        self.assertFalse(mesai_otomatik.dis_ip_gorulde(self.p, simdi=simdi, ip="78.1.2.3"))
        k.refresh_from_db()
        self.assertEqual((k.dis_ip_ilk_gorulme, k.dis_ip_ilk_ip), (simdi, "78.1.2.3"))
        self.assertFalse(mesai_otomatik.dis_ip_gorulde(self.p, simdi=simdi + datetime.timedelta(minutes=1), ip="99.9.9.9"))     # ilk IP korunur
        k.refresh_from_db()
        self.assertEqual(k.dis_ip_ilk_ip, "78.1.2.3")
        mesai_otomatik.fabrika_gorulde(self.p, simdi=simdi + datetime.timedelta(minutes=2))               # fabrikaya döndü: sayaç ve IP sıfırlanır
        k.refresh_from_db()
        self.assertEqual((k.dis_ip_ilk_gorulme, k.dis_ip_ilk_ip), (None, None))
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=simdi + datetime.timedelta(minutes=10), ip="85.1.1.1")
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=simdi + datetime.timedelta(minutes=14), ip="85.1.1.2"))   # tolerans doldu → kapandı
        k.refresh_from_db()
        self.assertEqual((k.otomatik_kapandi, k.otomatik_sebep, k.dis_ip_ilk_ip), (True, "dis_ip", "85.1.1.1"))
        self.assertIsNotNone(k.dis_ip_ilk_gorulme)

    def test_mesai_listesinde_ip_rozet_ipucunda(self):
        self.baslat()
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=timezone.now() - datetime.timedelta(minutes=10), ip="78.1.2.3")
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=timezone.now(), ip="78.1.2.4")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:mesai_kayitlari"))
        self.assertContains(r, "78.1.2.3")
