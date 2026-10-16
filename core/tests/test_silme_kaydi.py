"""Silme denetim kaydı (2026-10-16): fatura (+ irsaliye: test_teklif_siparis) kalıcı silinince kullanıcı / tarih-saat / IP / özet / JSON döküm ``SilmeKaydi``na yazılır (silmeyle AYNI transaction);
fiş türü silmeler (manuel fiş, banka/kasa/kart/virman/kesinti) aynı tabloya IP ile yazılır; Ayarlar › Silme Kayıtları SALT-OKUNUR ve yalnız yönetici; normal ekranlarda iz yok."""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse

from core import istek_baglami
from core.models import Depo, Fatura, SilmeKaydi, YevmiyeFisi
from core.services import silme_kaydi
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests import test_fatura_liste as tfl

D = datetime.date
Dc = Decimal
IP = "203.0.113.77"


class FaturaSilmeKaydiTest(tfl.ListeTaban):
    def sil(self, fatura, ip=IP):
        self.client.force_login(self.su)
        return self.client.post(reverse("core:fatura_sil", args=[fatura.pk]), HTTP_X_REAL_IP=ip)

    def test_kullanici_ip_ozet_ve_json_dokum(self):
        f = self.a_fatura(no="SK-1", miktar="10", fiyat="100", vade_tarihi=D(2026, 4, 30), depo_id=Depo.objects.get_or_create(kod="D1", defaults={"ad": "ANA DEPO"})[0].pk)
        fis_pk, fis_no = f.fis_id, f.fis.fis_no
        self.sil(f)
        self.assertFalse(Fatura.objects.filter(pk=f.pk).exists())                                        # silme çalıştı (kalıcı)
        k = SilmeKaydi.objects.get(tur="FATURA")
        self.assertEqual((k.kayit_no, k.silen_id, k.ip_adresi, k.tarih, k.tutar, k.kaynak), ("SK-1", self.su.pk, IP, f.tarih, Dc("1200.00"), "FATURA"))
        self.assertIn(self.tedarikci.unvan, k.aciklama)                                                  # cari özeti
        self.assertGreater((datetime.datetime.now(datetime.timezone.utc) - k.created_at).total_seconds(), -5)   # silme zamanı = kayıt zamanı
        v = k.veri
        self.assertEqual(v["fatura"]["cari"]["unvan"], self.tedarikci.unvan)
        self.assertEqual((v["fatura"]["genel_toplam"], v["fatura"]["fatura_no"]), ("1200.00", "SK-1"))
        self.assertEqual(len(v["satirlar"]), 1)
        self.assertEqual((v["satirlar"][0]["miktar"], v["satirlar"][0]["birim_fiyat"]), ("10.000", "100.000000"))
        self.assertEqual(v["fis"]["fis_pk"], fis_pk)
        self.assertEqual(v["fis"]["no"], f"2026/{fis_no}")
        self.assertEqual(sum(Dc(s["borc"]) for s in v["fis"]["satirlar"]), Dc("1200.00"))                # fiş satırları dökümde (borç = alacak = 1.200)
        self.assertEqual(sum(Dc(s["alacak"]) for s in v["fis"]["satirlar"]), Dc("1200.00"))
        self.assertTrue(v["stok_hareketleri"])                                                           # silinen stok girişi de dökümde
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis_pk).exists())

    def test_kayit_yazilamazsa_silme_geri_alinir(self):
        f = self.a_fatura(no="SK-2")
        self.client.force_login(self.su)
        with mock.patch.object(silme_kaydi, "fatura_yaz", side_effect=RuntimeError("yazim hatasi")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("core:fatura_sil", args=[f.pk]), HTTP_X_REAL_IP=IP)
        self.assertTrue(Fatura.objects.filter(pk=f.pk).exists())                                         # transaction birlikte geri alındı
        self.assertEqual(SilmeKaydi.objects.count(), 0)

    def test_taslak_fatura_da_kaydedilir(self):
        from core.services.fatura import fatura_taslak_olustur
        t = fatura_taslak_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="TSL-1", satirlar=self._satir("2", "100"))
        self.sil(t)
        k = SilmeKaydi.objects.get(tur="FATURA")
        self.assertEqual((k.kayit_no, k.veri["fatura"]["durum"], k.veri["fis"]), ("TSL-1", "TASLAK", None))

    def test_istek_disinda_ip_bos(self):
        self.assertIsNone(istek_baglami.ip())
        f = self.a_fatura(no="SK-3")
        from core.services.fatura import fatura_sil
        fatura_sil(f, kullanici=self.su)
        self.assertIsNone(SilmeKaydi.objects.get(tur="FATURA").ip_adresi)


class FisSilmeKaydiIPTest(tfl.ListeTaban):
    def test_manuel_fis_silme_ip_ile_yazilir(self):
        fis = fis_olustur(tarih=D(2026, 3, 11), satirlar=[SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari="50"), SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="50")])
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fis_sil", args=[fis.pk]), HTTP_X_REAL_IP=IP)
        self.assertIn(r.status_code, (200, 302))
        k = SilmeKaydi.objects.get(tur="FIS")
        self.assertEqual((k.silen_id, k.ip_adresi, k.tutar), (self.su.pk, IP, Dc("50.00")))
        self.assertEqual(len(k.veri["satirlar"]), 2)


class SilmeKayitlariEkraniTest(tfl.ListeTaban):
    def setUp(self):
        silme_kaydi.yaz(SilmeKaydi.Tur.FATURA, kayit_no="F-9", tarih=D(2026, 3, 10), tutar=Dc("120.00"), aciklama="TEDARİKÇİ A · ALIS", kaynak="FATURA", kullanici=self.su, veri={"a": 1})
        silme_kaydi.yaz(SilmeKaydi.Tur.IRSALIYE, kayit_no="SAI-1", tarih=D(2026, 3, 11), tutar=None, aciklama="İRS", kaynak="IRSALIYE", kullanici=None, veri={})

    def test_yonetici_listeler_ve_suzer_salt_okunur(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:silme_kayitlari"))
        self.assertContains(r, "F-9")
        self.assertContains(r, "SAI-1")
        self.assertContains(r, "JSON dökümü")
        self.assertNotContains(r, "Geri al")                                                            # düzenle / sil / geri al düğmesi yok (salt okunur)
        self.assertNotContains(r, ">Sil<")
        r = self.client.get(reverse("core:silme_kayitlari"), {"tur": "IRSALIYE"})
        self.assertNotContains(r, "F-9")
        self.assertContains(r, "SAI-1")
        r = self.client.get(reverse("core:silme_kayitlari"), {"ara": "TEDARİKÇİ"})
        self.assertContains(r, "F-9")
        self.assertEqual(self.client.post(reverse("core:silme_kayitlari"), {}).status_code, 200)         # POST hiçbir şeyi değiştirmez
        self.assertEqual(SilmeKaydi.objects.count(), 2)

    def test_yonetici_olmayan_403_ve_menude_yok(self):
        u = User.objects.create_user("siradan", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:silme_kayitlari")).status_code, 403)
        from core.moduller import MODULLER
        ayarlar = next(m for m in MODULLER if m.kod == "AYARLAR")
        self.assertIn("silme_kayitlari", [e.kod for e in ayarlar.ekranlar])
        self.assertTrue(ayarlar.yonetici_modulu)

    def test_normal_ekranlarda_iz_yok(self):
        """Silinen fatura listede/detayda hiç görünmez (iptal/silindi rozeti yok)."""
        f = self.a_fatura(no="IZ-1")
        self.client.force_login(self.su)
        self.client.post(reverse("core:fatura_sil", args=[f.pk]), HTTP_X_REAL_IP=IP)
        r = self.client.get(reverse("core:alis_faturalari"))
        self.assertNotContains(r, "IZ-1")
        self.assertEqual(self.client.get(f"/faturalar/{f.pk}/").status_code, 404)
