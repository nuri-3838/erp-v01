"""Fabrika kapısı + OTOMATİK mesai kapatma (2026-10-16): operatör hesaplar fabrika ağı dışında /atolye/ ve /mesai/'ye giremez (yönetici/ofis muaf); dışarıda
açık mesai, tolerans (3 dk, ayarlanır) dolunca fabrikada SON görülmeyle kapanır; heartbeat yalnız son görülmeyi günceller (gündüz "heartbeat yok" kapanışı YOK);
gece kapanışı (23:59 TR; bitiş = son görülme, yoksa 23:59); açık iş oturumu (ekip: üyelik, tek kişi: İptal); rozet + elle düzeltme; ayarlar; cron komutu."""
import datetime
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import EkranYetki, IkAyari, IsOturumu, IsOturumuUyesi, MesaiIzinliAg, MesaiKaydi
from core.services import atolye, mesai_otomatik
from core.services import mesai as mesai_servis
from core.services.mesai_hesap import hesap_olustur
from core.tests.ik_yardimci import personel_kur
from core.tests.test_atolye import AtolyeTaban, IZINLI, IZINSIZ

TR = ZoneInfo("Europe/Istanbul")


def tr(g, saat, dk=0, sn=0, ay=10, yil=2026):
    return datetime.datetime(yil, ay, g, saat, dk, sn, tzinfo=TR)


class OtoTaban(AtolyeTaban):
    def mesai_ac(self, p=None, giris=None, **ek):
        p = p or self.p
        giris = giris or tr(16, 8, 0)
        return MesaiKaydi.objects.create(personel=p, is_tarihi=giris.astimezone(TR).date(), giris_zamani=giris, giris_ip=IZINLI,
                                         son_fabrika_gorulme=ek.pop("son", giris), kaynak=MesaiKaydi.Kaynak.PERSONEL, **ek)

    def get(self, ad="atolye", ip=IZINLI, user=None, **kw):
        self.client.force_login(user or self.u)
        return self.client.get(reverse(f"core:{ad}", **kw), HTTP_X_REAL_IP=ip)


class KapiTest(OtoTaban):
    def test_operator_dis_ip_403_fabrika_ici_200(self):
        r = self.get(ip=IZINSIZ)
        self.assertContains(r, "Fabrika Wi-Fi", status_code=403)
        self.assertEqual(self.get(ip=IZINLI).status_code, 200)
        self.assertEqual(self.get("atolye_istasyon", ip=IZINSIZ, kwargs={"pk": self.lazer.pk}).status_code, 403)
        self.client.force_login(self.u2)                                                         # salt-mesai hesabı da operatör sayılır
        self.assertEqual(self.client.get(reverse("core:mesaim"), HTTP_X_REAL_IP=IZINSIZ).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:mesaim"), HTTP_X_REAL_IP=IZINLI).status_code, 200)

    def test_yonetici_ve_ofis_dis_ip_muaf(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:atolye"), HTTP_X_REAL_IP=IZINSIZ).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]), HTTP_X_REAL_IP=IZINSIZ).status_code, 200)
        p3 = personel_kur(ad="ofis", soyad="kisi", ise_giris_tarihi=datetime.date(2020, 1, 1))
        u3 = hesap_olustur(p3, kullanici_adi="ofiskisi", sifre="cokGizli!2026", ilk_giris_sifre=False)
        atolye.operator_ayarla(p3, True)
        EkranYetki.objects.create(kullanici=u3, ekran_kod="mesai_kayitlari")                     # atölye dışında yetki → ofis
        self.client.force_login(u3)
        self.assertEqual(self.client.get(reverse("core:atolye"), HTTP_X_REAL_IP=IZINSIZ).status_code, 200)
        self.mesai_ac(p3)
        self.client.get(reverse("core:atolye"), HTTP_X_REAL_IP=IZINSIZ)
        self.assertIsNone(MesaiKaydi.objects.get(personel=p3).dis_ip_ilk_gorulme)               # ofis mesaisi dışarıda sayaç bile başlatmaz

    def test_ag_listesi_bos_ya_da_ayar_kapali_kapi_devre_disi(self):
        self.assertEqual(self.get(ip=IZINSIZ).status_code, 403)
        a = IkAyari.get()
        a.fabrika_kisiti_acik = False
        a.save()
        self.assertEqual(self.get(ip=IZINSIZ).status_code, 200)
        a.fabrika_kisiti_acik = True
        a.save()
        MesaiIzinliAg.objects.all().delete()                                                      # liste boş → özellik kapalı (kimse kilitlenmez)
        self.assertEqual(self.get(ip=IZINSIZ).status_code, 200)

    def test_cikis_ve_anonim_etkilenmez(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:atolye"), HTTP_X_REAL_IP=IZINSIZ).status_code, 302)       # anonim → giriş (kapı kullanıcı görmeden geçer)
        self.client.force_login(self.u)
        self.assertEqual(self.client.post(reverse("logout"), HTTP_X_REAL_IP=IZINSIZ).status_code, 302)           # çıkış serbest


class HeartbeatTest(OtoTaban):
    def test_ping_fabrikada_son_gorulmeyi_gunceller_dis_ip_403_json(self):
        k = self.mesai_ac(giris=timezone.now() - datetime.timedelta(hours=2), son=timezone.now() - datetime.timedelta(hours=1))
        self.client.force_login(self.u)
        r = self.client.post(reverse("core:atolye_ping"), HTTP_X_REAL_IP=IZINLI)
        self.assertEqual(r.status_code, 204)
        k.refresh_from_db()
        self.assertGreater(k.son_fabrika_gorulme, timezone.now() - datetime.timedelta(seconds=30))
        r = self.client.post(reverse("core:atolye_ping"), HTTP_X_REAL_IP=IZINSIZ)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"fabrika_disi": True, "mesai_kapandi": False})
        self.assertEqual(self.client.get(reverse("core:atolye_ping"), HTTP_X_REAL_IP=IZINLI).status_code, 405)    # yalnız POST

    def test_sayfa_istegi_de_son_gorulmeyi_gunceller_ve_dis_ip_sayacini_sifirlar(self):
        k = self.mesai_ac(giris=timezone.now() - datetime.timedelta(hours=2), son=timezone.now() - datetime.timedelta(hours=1),
                          dis_ip_ilk_gorulme=timezone.now() - datetime.timedelta(minutes=2))
        self.get(ip=IZINLI)
        k.refresh_from_db()
        self.assertIsNone(k.dis_ip_ilk_gorulme)
        self.assertGreater(k.son_fabrika_gorulme, timezone.now() - datetime.timedelta(seconds=30))

    def test_sayfaya_heartbeat_ayari_yalniz_operatorde_gomulur(self):
        r = self.get(ip=IZINLI)
        self.assertContains(r, reverse("core:atolye_ping"))
        self.assertContains(r, "SN = 120")
        self.assertNotContains(self.get(ip=IZINSIZ, user=self.yon), "SN = ")                     # yönetici izleme: heartbeat yok
        a = IkAyari.get()
        a.heartbeat_sn = 90
        a.save()
        self.assertContains(self.get(ip=IZINLI), "SN = 90")

    def test_gunduz_heartbeat_yok_diye_kapanmaz(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 8, 5))                                  # 6 saattir ping yok (telefon cepte)
        self.assertEqual(mesai_otomatik.toplu_kapat(simdi=tr(16, 14, 30)), [])
        k.refresh_from_db()
        self.assertIsNone(k.cikis_zamani)

    def test_baslat_son_gorulmeyi_girise_esitler(self):
        self.baslat()
        k = MesaiKaydi.objects.get(personel=self.p)
        self.assertEqual(k.son_fabrika_gorulme, k.giris_zamani)


class DisIpToleransTest(OtoTaban):
    def test_tolerans_dolunca_son_fabrika_gorulmeyle_kapanir(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 12, 40))
        self.assertFalse(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 0)))             # ilk dış görülme: sayaç başlar
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.dis_ip_ilk_gorulme), (None, tr(16, 13, 0)))
        self.assertFalse(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 2, 59)))         # 3 dk dolmadı
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 3)))              # doldu
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.otomatik_kapandi, k.otomatik_sebep, k.cikis_ip), (tr(16, 12, 40), True, "dis_ip", None))
        self.assertEqual(k.dis_ip_ilk_gorulme, tr(16, 13, 0))                                  # kapanışta SİLİNMEZ (denetim: yanlış pozitif incelemesi)

    def test_son_gorulme_yoksa_giris_saati(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=None)
        MesaiKaydi.objects.filter(pk=k.pk).update(son_fabrika_gorulme=None)
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 0))
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 5))
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.otomatik_sebep), (tr(16, 8, 0), "dis_ip"))

    def test_fabrikadan_gorulunce_sayac_sifirlanir_kapanmaz(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 12, 40))
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 0))
        mesai_otomatik.fabrika_gorulde(self.p, simdi=tr(16, 13, 2))                              # mobil veri anlık geçişi: Wi-Fi'a döndü
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 5))                              # yeni sayaç (2 dk'sı bile dolmadı)
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.dis_ip_ilk_gorulme, k.son_fabrika_gorulme), (None, tr(16, 13, 5), tr(16, 13, 2)))

    def test_tolerans_ayarlanir_sifirda_aninda(self):
        mesai_otomatik.ayar_kaydet(acik=True, tolerans_dk=0, heartbeat_sn=120, gece_saati=datetime.time(23, 59))
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 12, 40))
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 13, 0)))
        k.refresh_from_db()
        self.assertEqual(k.cikis_zamani, tr(16, 12, 40))
        mesai_otomatik.ayar_kaydet(acik=True, tolerans_dk=10, heartbeat_sn=120, gece_saati=datetime.time(23, 59))
        k2 = self.mesai_ac(giris=tr(16, 13, 30), son=tr(16, 13, 31))
        mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 14, 0))
        self.assertFalse(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 14, 9)))             # 10 dk tolerans
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=tr(16, 14, 10)))
        k2.refresh_from_db()
        self.assertEqual(k2.cikis_zamani, tr(16, 13, 31))

    def test_istek_uzerinden_ilk_dis_istek_sayaci_baslatir_ikincisi_tolerans_sonra_kapatir(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 12, 40))
        with mock.patch("core.services.mesai_otomatik.timezone.now", return_value=tr(16, 13, 0)):
            r = self.get(ip=IZINSIZ)
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(r, "otomatik bitirildi", status_code=403)
        with mock.patch("core.services.mesai_otomatik.timezone.now", return_value=tr(16, 13, 4)):
            r = self.get(ip=IZINSIZ)
        self.assertContains(r, "otomatik bitirildi", status_code=403)
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.otomatik_kapandi), (tr(16, 12, 40), True))

    def test_cron_sayac_dolmus_ama_istek_gelmeyen_mesaiyi_kapatir(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 12, 40), dis_ip_ilk_gorulme=tr(16, 13, 0))
        self.assertEqual(mesai_otomatik.toplu_kapat(simdi=tr(16, 13, 2)), [])
        (r,) = mesai_otomatik.toplu_kapat(simdi=tr(16, 13, 5))
        self.assertEqual((r["kayit"].pk, r["sebep"], r["bitis"]), (k.pk, "dis_ip", tr(16, 12, 40)))
        k.refresh_from_db()
        self.assertEqual(k.cikis_zamani, tr(16, 12, 40))


class GeceKapanisTest(OtoTaban):
    def test_gece_saatinden_once_dokunmaz_sonra_son_gorulmeyle_kapatir(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 21, 15))
        self.assertEqual(mesai_otomatik.toplu_kapat(simdi=tr(16, 23, 58, 59)), [])
        (r,) = mesai_otomatik.toplu_kapat(simdi=tr(16, 23, 59))
        self.assertEqual((r["sebep"], r["bitis"]), ("gece", tr(16, 21, 15)))
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.otomatik_kapandi, k.otomatik_sebep), (tr(16, 21, 15), True, "gece"))

    def test_son_gorulme_yoksa_gece_saati_ve_onceki_gunden_kalan(self):
        k = self.mesai_ac(giris=tr(15, 9, 48), son=None)
        MesaiKaydi.objects.filter(pk=k.pk).update(son_fabrika_gorulme=None)
        (r,) = mesai_otomatik.toplu_kapat(simdi=tr(16, 9, 0))                                    # dünden kalan: ertesi gün ilk çalışmada
        self.assertEqual((r["sebep"], r["bitis"]), ("gece", tr(15, 23, 59)))

    def test_gece_saati_ayarlanir(self):
        mesai_otomatik.ayar_kaydet(acik=True, tolerans_dk=3, heartbeat_sn=120, gece_saati=datetime.time(20, 30))
        self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 19, 0))
        self.assertEqual(mesai_otomatik.toplu_kapat(simdi=tr(16, 20, 29)), [])
        self.assertEqual(len(mesai_otomatik.toplu_kapat(simdi=tr(16, 20, 30))), 1)

    def test_kuru_yazmaz_ve_ofis_yonetici_mesaisi_kapanmaz(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 17, 0))
        p3 = personel_kur(ad="ofis", soyad="kisi", ise_giris_tarihi=datetime.date(2020, 1, 1))
        u3 = hesap_olustur(p3, kullanici_adi="ofiskisi", sifre="cokGizli!2026", ilk_giris_sifre=False)
        EkranYetki.objects.create(kullanici=u3, ekran_kod="mesai_kayitlari")
        o = self.mesai_ac(p3, giris=tr(16, 8, 0))
        sonuc = mesai_otomatik.toplu_kapat(simdi=tr(17, 0, 5), kuru=True)
        self.assertEqual([r["kayit"].pk for r in sonuc], [k.pk])                                 # yalnız operatör
        k.refresh_from_db()
        self.assertIsNone(k.cikis_zamani)
        mesai_otomatik.toplu_kapat(simdi=tr(17, 0, 5))
        o.refresh_from_db()
        self.assertIsNone(o.cikis_zamani)
        ayar = IkAyari.get()
        ayar.fabrika_kisiti_acik = False
        ayar.save()
        self.assertEqual(mesai_otomatik.toplu_kapat(simdi=tr(17, 0, 5)), [])                    # ayar kapalı → hiçbir şey kapanmaz


class IsOturumuTest(OtoTaban):
    def test_tek_kisilik_oturum_adet_bekliyor_olur_sure_kalir(self):
        self.baslat()
        self.basla(self.ie_kesim)
        o = self.acik_oturum(self.ie_kesim)
        k = MesaiKaydi.objects.get(personel=self.p)
        MesaiKaydi.objects.filter(pk=k.pk).update(son_fabrika_gorulme=k.giris_zamani + datetime.timedelta(minutes=20))
        k.refresh_from_db()
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=k.giris_zamani + datetime.timedelta(minutes=30)) is False)
        self.assertTrue(mesai_otomatik.dis_ip_gorulde(self.p, simdi=k.giris_zamani + datetime.timedelta(minutes=34)))
        o.refresh_from_db()
        k.refresh_from_db()
        self.assertEqual((o.durum, o.bitis_zamani, o.kayit_id), ("ADET", k.cikis_zamani, None))                  # İPTAL OLMAZ: süre işçiliğe sayılır, adet sonra girilir
        self.assertEqual(o.sorumlu_kisi.pk, self.p.pk)
        self.assertEqual(o.bitis_zamani, k.giris_zamani + datetime.timedelta(minutes=20))
        self.assertFalse(IsOturumuUyesi.objects.filter(oturum=o, ayrilma_zamani__isnull=True).exists())

    def test_ekipte_baska_biri_varsa_yalniz_uyelik_kapanir(self):
        atolye.operator_ayarla(self.p2, True)
        mesai_servis.baslat(self.p2, ip=IZINLI)
        self.baslat()
        self.basla(self.ie_kesim, ekip=[self.p2.pk])
        o = self.acik_oturum(self.ie_kesim)
        k = MesaiKaydi.objects.get(personel=self.p)
        mesai_otomatik.kapat(k, sebep="gece", simdi=k.giris_zamani + datetime.timedelta(hours=3))
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")                                                         # oturum diğerleriyle sürer
        kapanan = IsOturumuUyesi.objects.get(oturum=o, personel=self.p)
        self.assertIsNotNone(kapanan.ayrilma_zamani)
        self.assertIsNone(IsOturumuUyesi.objects.get(oturum=o, personel=self.p2).ayrilma_zamani)


class DuzeltmeVeRozetTest(OtoTaban):
    def test_rozet_listede_ve_elle_duzeltince_kalkar(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 17, 0))
        mesai_otomatik.toplu_kapat(simdi=tr(17, 0, 5))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:mesai_kayitlari"))
        self.assertContains(r, "Otomatik · Gece kapanışı")
        k.refresh_from_db()
        mesai_servis.duzelt(k, giris_zamani=k.giris_zamani, cikis_zamani=tr(16, 18, 30), duzeltme_notu="gerçek çıkış 18:30")
        k.refresh_from_db()
        self.assertEqual((k.otomatik_kapandi, k.otomatik_sebep, k.kaynak), (False, "gece", "YONETICI"))
        self.assertNotContains(self.client.get(reverse("core:mesai_kayitlari")), "Otomatik ·")


class AyarVeKomutTest(OtoTaban):
    def test_ayar_dogrulama(self):
        h = mesai_otomatik.MesaiOtomatikHatasi
        t = datetime.time(23, 59)
        for tolerans, hb in ((-1, 120), (61, 120), ("x", 120), (3, 10), (3, 601)):
            with self.assertRaises(h):
                mesai_otomatik.ayar_kaydet(acik=True, tolerans_dk=tolerans, heartbeat_sn=hb, gece_saati=t)
        a = mesai_otomatik.ayar_kaydet(acik=False, tolerans_dk="5", heartbeat_sn="60", gece_saati=datetime.time(22, 0, 30))
        self.assertEqual((a.fabrika_kisiti_acik, a.dis_ip_tolerans_dk, a.heartbeat_sn, a.gece_kapanis_saati), (False, 5, 60, datetime.time(22, 0)))

    def test_ayar_ekrani_yalniz_yonetici_kaydeder(self):
        url = reverse("core:mesai_otomatik_ayari_kaydet")
        veri = {"acik": "on", "tolerans_dk": "7", "heartbeat_sn": "90", "gece_saati": "23:30"}
        self.client.force_login(self.u)
        self.assertEqual(self.client.post(url, veri, HTTP_X_REAL_IP=IZINLI).status_code, 403)
        self.client.force_login(self.yon)
        r = self.client.post(url, veri, follow=True)
        self.assertContains(r, "ayarları kaydedildi")
        a = IkAyari.get()
        self.assertEqual((a.fabrika_kisiti_acik, a.dis_ip_tolerans_dk, a.heartbeat_sn, a.gece_kapanis_saati), (True, 7, 90, datetime.time(23, 30)))
        self.assertContains(r, "Fabrika Kısıtı ve Otomatik Mesai Kapatma")
        r = self.client.post(url, {**veri, "tolerans_dk": "99"}, follow=True)
        self.assertContains(r, "Ayarlar geçersiz")
        self.assertEqual(IkAyari.get().dis_ip_tolerans_dk, 7)

    def test_komut_kuru_ve_gercek(self):
        k = self.mesai_ac(giris=tr(16, 8, 0), son=tr(16, 17, 0))
        cikti = StringIO()
        with mock.patch("django.utils.timezone.now", return_value=tr(17, 0, 10)):
            call_command("mesai_otomatik_kapat", "--kuru", stdout=cikti)
        self.assertIn("(KURU)", cikti.getvalue())
        self.assertIn(f"#{k.pk}", cikti.getvalue())
        self.assertIn("kapanacak", cikti.getvalue())
        k.refresh_from_db()
        self.assertIsNone(k.cikis_zamani)
        cikti = StringIO()
        with mock.patch("django.utils.timezone.now", return_value=tr(17, 0, 10)):
            call_command("mesai_otomatik_kapat", stdout=cikti)
        self.assertIn("kapatıldı", cikti.getvalue())
        k.refresh_from_db()
        self.assertEqual((k.cikis_zamani, k.otomatik_sebep), (tr(16, 17, 0), "gece"))
        cikti = StringIO()
        call_command("mesai_otomatik_kapat", stdout=cikti)
        self.assertIn("kapanacak mesai yok", cikti.getvalue())
