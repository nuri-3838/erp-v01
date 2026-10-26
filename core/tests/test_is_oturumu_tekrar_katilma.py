"""İş oturumunda ekipten AYRILIP AYNI açık oturuma TEKRAR KATILMA (birden çok kez): her katılım ayrı üyelik satırı (önceki kapalı satırlar kalır; ``uq_is_oturumu_uyesi_aktif``
yalnız AÇIK üyeliği tekil tutar). Süre/işçilik: satırların katılım aralıkları toplanır (adam-saat), ekip/kişi sayısı kişi bazında tekil, İş Takibi'nde kişi başına tek pay
(oturum/sağlam bir kez; süre ve çizelge blokları aralık sayısınca). Emirli ve karma kesim oturumu."""
import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from core.models import IsOturumu, IsOturumuUyesi
from core.services import is_oturumu, is_takibi
from core.tarih import tr_bugun
from core.tests import test_karma_oturum as ko
from core.tests.test_atolye import IZINLI

D = Decimal


@contextmanager
def an(t):
    """Servis çağrısını verilen ANDA yapılmış gibi çalıştırır (django.utils.timezone.now sabitlenir)."""
    with patch("django.utils.timezone.now", return_value=t):
        yield


class TekrarKatilmaTaban(ko.KarmaOturumTaban):
    def kur(self):
        """errahman (sorumlu) + mehmet (ekip) mesaide; zaman çizelgesi için 4 saat önce sabit taban."""
        self.baslat(self.u3)
        self.baslat()
        self.base = timezone.now() - datetime.timedelta(hours=4)

    def dk(self, n):
        return self.base + datetime.timedelta(minutes=n)

    def katil_ayril_katil_ayril_katil(self, o):
        """mehmet: 10'da katılır, 40'ta ayrılır (1800 sn); 60'ta katılır, 70'te ayrılır (600 sn); 100'de katılır (açık kalır)."""
        for dakika, islem in ((10, "katil"), (40, "ayril"), (60, "katil"), (70, "ayril"), (100, "katil")):
            with an(self.dk(dakika)):
                getattr(is_oturumu, islem)(self.p3, o)
            self.assertEqual(is_oturumu.aktif_uyelik(self.p3) is not None, islem == "katil", (dakika, islem))

    def p3_satirlari(self, o):
        return list(IsOturumuUyesi.objects.filter(oturum=o, personel=self.p3, silindi=False).order_by("pk"))


class EmirliOturumTest(TekrarKatilmaTaban):
    def test_ayril_tekrar_katil_birden_cok_kez_ve_sure_toplami(self):
        self.kur()
        with an(self.dk(0)):
            o = is_oturumu.baslat(self.p, self.ie_kesim, [])
        self.katil_ayril_katil_ayril_katil(o)
        with an(self.dk(110)), self.assertRaisesMessage(is_oturumu.OturumHatasi, "Bu işin ekibindesiniz"):          # açıkken ikinci katılma: yeni satır AÇILMAZ
            is_oturumu.katil(self.p3, o)
        with an(self.dk(130)):
            is_oturumu.iptal(self.p, o, bitis=self.dk(130))
        satirlar = self.p3_satirlari(o)
        self.assertEqual(len(satirlar), 3)                                                                           # önceki kapalı satırlar kaldı + yeni açıldı
        self.assertEqual([s.katilim_saniye() for s in satirlar], [1800, 600, 1800])                                  # 10-40, 60-70, 100-130
        self.assertTrue(all(s.ayrilma_zamani is not None for s in satirlar))
        self.assertEqual(sum(1 for s in satirlar if s.baslatan), 0)
        o.refresh_from_db()
        oz = is_oturumu.ozet(o)
        self.assertEqual(oz["sure_saniye"], 7800)                                                                    # oturum 0-130
        self.assertEqual(oz["adam_saniye"], 7800 + 1800 + 600 + 1800)                                                # sorumlu + mehmet'in üç aralığı
        self.assertEqual(oz["adam_saat"], (D(12000) / 3600).quantize(D("0.01")))
        self.assertEqual(oz["kisi"], 2)                                                                              # satır 4 ama kişi 2
        self.assertEqual(oz["ekip_metni"].count(self.p3.ad_soyad), 1)
        self.assertEqual(len(oz["uyeler"]), 4)

    def test_acik_oturumda_tekrar_katilan_tek_acik_uye_sayilir(self):
        self.kur()
        with an(self.dk(0)):
            o = is_oturumu.baslat(self.p, self.ie_kesim, [])
        self.katil_ayril_katil_ayril_katil(o)
        o = IsOturumu.objects.get(pk=o.pk)
        oz = is_oturumu.ozet(o)
        self.assertEqual([u.personel_id for u in oz["acik_uyeler"]], [self.p.pk, self.p3.pk])                        # açık üyelik kişi başına bir
        self.assertEqual(oz["kisi"], 2)
        # mehmet'in açık üyeliği yalnız kendi satırı; sorumlu mesai bitirince / yönetici kapatınca tüm açık satırlar kapanır
        with an(self.dk(120)):
            is_oturumu.iptal(self.p, o, bitis=self.dk(120))
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, ayrilma_zamani__isnull=True).count(), 0)

    def test_http_ayril_tekrar_katil_hata_vermez(self):
        self.baslat(self.u2)
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        self.client.force_login(self.u2)
        for adim in range(3):
            r = self.client.post(reverse("core:atolye_oturum_katil", args=[o.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
            self.assertTrue(any("ekibe katıldınız" in m for m in self.mesajlar(r)), (adim, self.mesajlar(r)))
            r = self.client.post(reverse("core:atolye_oturum_ayril", args=[o.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
            self.assertTrue(any("ekipten ayrıldınız" in m for m in self.mesajlar(r)), (adim, self.mesajlar(r)))
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, personel=self.p2).count(), 3)
        r = self.client.post(reverse("core:atolye_oturum_katil", args=[o.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)          # 4. katılım açık kalır
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, personel=self.p2, ayrilma_zamani__isnull=True).count(), 1)

    def test_ayrilmis_kisi_baska_ise_gecip_sonra_geri_dondu(self):
        """mehmet ayrılır, başka bir oturumda çalışır ve bitirir; sonra ilk oturuma yeniden katılır (aktif_uyelik kuralı: aynı anda tek açık oturum)."""
        self.kur()
        with an(self.dk(0)):
            o = is_oturumu.baslat(self.p, self.ie_kesim, [])
        with an(self.dk(10)):
            is_oturumu.katil(self.p3, o)
        with an(self.dk(20)):
            is_oturumu.ayril(self.p3, o)
        with an(self.dk(25)):
            baska = is_oturumu.baslat(self.p3, self.ie_bukum, [])
        with an(self.dk(30)), self.assertRaisesMessage(is_oturumu.OturumHatasi, "başka bir işte"):                  # başka işteyken katılamaz
            is_oturumu.katil(self.p3, o)
        with an(self.dk(35)):
            is_oturumu.iptal(self.p3, baska, bitis=self.dk(35))
        with an(self.dk(40)):
            is_oturumu.katil(self.p3, o)
        self.assertEqual(len(self.p3_satirlari(o)), 2)


class KarmaOturumTekrarTest(TekrarKatilmaTaban):
    def test_karma_ayril_tekrar_katil_bitir_sure_toplami_ve_takip(self):
        self.kur()
        with an(self.dk(0)):
            o = is_oturumu.baslat_karma(self.p, self.lazer, self.kprofil, [])
        self.katil_ayril_katil_ayril_katil(o)
        with an(self.dk(130)):
            karma = is_oturumu.bitir_karma(self.p, o, D("2"), [(self.kparca["KP1"].pk, 12), (self.kparca["KP2"].pk, 8)], bitis=self.dk(130), kullanici=self.u)
        o = IsOturumu.objects.get(pk=o.pk)
        self.assertEqual((o.durum, o.karma_kesim_id), ("BITTI", karma.pk))
        satirlar = self.p3_satirlari(o)
        self.assertEqual([s.katilim_saniye(o.bitis_zamani) for s in satirlar], [1800, 600, 1800])                    # üçüncü satırı Bitir 130'da kapattı
        oz = is_oturumu.ozet(o)
        self.assertEqual((oz["sure_saniye"], oz["adam_saniye"], oz["kisi"]), (7800, 12000, 2))
        self.assertEqual(oz["ekip_metni"].count(self.p3.ad_soyad), 1)
        # İş Takibi: kişi başına TEK pay (sağlam bir kez), süre aralıkların toplamı, çizelgede aralık sayısınca blok
        bas = tr_bugun() - datetime.timedelta(days=2)
        bit = tr_bugun() + datetime.timedelta(days=1)
        an_ = timezone.now()
        gecmis = [s for s in is_takibi.gecmis(baslangic=bas, bitis=bit, simdi=an_) if s["tur"] == "oturum" and s["oturum"].pk == o.pk]
        self.assertEqual(len(gecmis), 1)
        paylar = {p["personel"].pk: p for p in gecmis[0]["paylar"]}
        self.assertEqual(sorted(paylar), sorted([self.p.pk, self.p3.pk]))                                            # satır sayısı kadar değil, kişi kadar pay
        self.assertEqual((paylar[self.p3.pk]["saniye"], len(paylar[self.p3.pk]["araliklar"])), (4200, 3))
        self.assertEqual(paylar[self.p.pk]["saniye"], 7800)
        self.assertAlmostEqual(float(sum(p["oran"] for p in paylar.values())), 1.0, places=6)
        self.assertEqual(len(gecmis[0]["personeller"]), 2)
        mesai = is_takibi.mesai_araliklari(bas, bit, an_)
        kisiler = {k["personel"].pk: k for k in is_takibi.kisi_ozeti(gecmis, baslangic=bas, bitis=bit, simdi=an_, mesai=mesai)}
        self.assertEqual((kisiler[self.p3.pk]["oturum"], kisiler[self.p3.pk]["saglam"], kisiler[self.p3.pk]["is_saniye"]), (1, D("20"), 4200))
        cizelge = is_takibi.zaman_cizelgesi(gecmis, mesai, baslangic=bas, bitis=bit, simdi=an_)
        self.assertEqual(sum(len(g["bloklar"]) for g in cizelge[self.p3.pk]["gunler"]), 3)
        # ofis ekranları: Excel ve liste
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:is_oturumlari"), {"bas": bas.isoformat(), "bit": bit.isoformat()}).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:is_oturumlari"), {"bas": bas.isoformat(), "bit": bit.isoformat(), "xlsx": "1"}).status_code, 200)

    def test_karma_http_ayril_tekrar_katil_ve_adet_bekliyor_sonrasi(self):
        self.baslat(self.u2)
        self.baslat()
        self.karma_basla()
        o = self.karma_oturum()
        self.client.force_login(self.u2)
        for _ in range(3):
            self.client.post(reverse("core:atolye_oturum_katil", args=[o.pk]), HTTP_X_REAL_IP=IZINLI)
            self.client.post(reverse("core:atolye_oturum_ayril", args=[o.pk]), HTTP_X_REAL_IP=IZINLI)
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, personel=self.p2).count(), 3)
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, personel=self.p2, ayrilma_zamani__isnull=True).count(), 0)
        self.client.force_login(self.u)                                                                              # ekipte kimse kalmadı: sorumlu mesai bitirir → "Adet bekliyor"
        self.client.post(reverse("core:atolye_mesai"), {"eylem": "bitir", "bekle": "1"}, HTTP_X_REAL_IP=IZINLI)
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")
        self.assertEqual(IsOturumuUyesi.objects.filter(oturum=o, ayrilma_zamani__isnull=True).count(), 0)
        r = self.client.get(reverse("core:atolye"))                                                                  # ekran hatasız
        self.assertEqual(r.status_code, 200)


class KisitTest(TekrarKatilmaTaban):
    def test_veritabani_yalniz_acik_uyeligi_tekillestirir(self):
        from django.db import IntegrityError, transaction
        self.kur()
        with an(self.dk(0)):
            o = is_oturumu.baslat(self.p, self.ie_kesim, [])
        with an(self.dk(10)):
            is_oturumu.katil(self.p3, o)
        with self.assertRaises(IntegrityError), transaction.atomic():                                                # aynı kişi için ikinci AÇIK satır olamaz
            IsOturumuUyesi.objects.create(oturum=o, personel=self.p3, katilim_zamani=self.dk(20))
        with an(self.dk(30)):
            is_oturumu.ayril(self.p3, o)
        IsOturumuUyesi.objects.create(oturum=o, personel=self.p3, katilim_zamani=self.dk(40))                         # kapalı satır varken yeni açık satır serbest
        self.assertEqual(self.p3_satirlari(o)[-1].ayrilma_zamani, None)
