"""KDV dönem mahsubu (191/391/190/360.30): alt hesap bazında kapama, devreden/ödenecek senaryoları, fark ± yönleri, önceki 190 açılışı, aynı dönem engeli, düzenle/sil,
sonraki dönem kilidi, mahsup sonrası değişiklik uyarısı, 360.10 (KDV2) hariç, ekranlar ve fiş kilidi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, KdvMahsup, Kur, SilmeKaydi, YevmiyeFisi, YevmiyeSatir
from core.services import kdv_mahsup as km
from core.services import raporlar
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class KdvBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        g = D(2026, 1, 1)
        Kur.objects.bulk_create([Kur(tarih=g + datetime.timedelta(days=i), usd_alis=Dc("40")) for i in range(400)])
        _hesap("190", "DEVREDEN KDV")
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV")
        for ust in ("191", "391"):
            for oran in ("01", "10", "20"):
                _hesap(f"{ust}.{oran}", f"%{oran} KDV", kalem="DV" if ust == "191" else "KVYK")
        _hesap("360", "ÖDENECEK VERGİ VE FONLAR", kalem="KVYK")
        _hesap("360.10", "KDV TEVKİFATLARI", kalem="KVYK")
        _hesap("360.10.0210", "2/10 TEVKİFAT", kalem="KVYK")
        _hesap("360.30", "ÖDENECEK KDV", kalem="KVYK")
        _hesap("689", "DİĞER OLAĞANDIŞI GİDER", kalem="I", grup="GELIR_TABLOSU")
        _hesap("770", "GENEL YÖNETİM", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.11", "VERGİ ÖDEMELERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.01", "DİĞER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("659", "DİĞER OLAĞAN GİDER", kalem="F", grup="GELIR_TABLOSU")
        _hesap("320.01", "SATICI", kalem="KVYK")
        _hesap("120.01", "MÜŞTERİ")
        cls.su = User.objects.create_superuser("kdv", password="x")
        cls.sade = User.objects.create_user("kdvd", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="kdv_mahsup")

    def indirilecek(self, kod, tutar, tarih):
        fis_olustur(tarih=tarih, aciklama="ALIŞ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu=kod, taraf="B", islem_tutari=tutar), SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari=tutar)])

    def hesaplanan(self, kod, tutar, tarih):
        fis_olustur(tarih=tarih, aciklama="SATIŞ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu="120.01", taraf="B", islem_tutari=tutar), SatirGirdi(hesap_kodu=kod, taraf="A", islem_tutari=tutar)])

    def canli_benzeri(self):
        """Canlı veri kopyasındaki 31.08.2026 bakiyeleri (alt hesap bazında)."""
        self.indirilecek("191.20", "560896.83", D(2026, 3, 10))
        self.indirilecek("191.01", "166.71", D(2026, 5, 5))
        self.indirilecek("191.10", "497145.36", D(2026, 8, 20))
        self.hesaplanan("391.20", "15526.13", D(2026, 1, 1))                  # açılış tarihli
        self.hesaplanan("391.01", "12772.28", D(2026, 6, 6))
        self.hesaplanan("391.10", "500000.00", D(2026, 8, 31))

    def satirlar(self, fis):
        return sorted((s.hesap_id, "B" if s.borc else "A", s.borc or s.alacak) for s in fis.satirlar.filter(silindi=False))

    def olustur(self, yil=2026, ay=8, dev="528720,87", ode="0", **kw):
        return km.olustur(yil=yil, ay=ay, beyan_devreden=dev, beyan_odenecek=ode, kullanici=self.su, **kw)


class HesapTest(KdvBase):
    def test_canli_kontrol_senaryosu_08_2026(self):
        self.canli_benzeri()
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="528.720,87", beyan_odenecek="0")
        self.assertEqual((h["t191"], h["t391"], h["a190"], h["erp_devreden"], h["fark"]),
                         (Dc("1058208.90"), Dc("528298.41"), Dc("0.00"), Dc("529910.49"), Dc("1189.62")))
        self.assertEqual(h["donem_sonu"], D(2026, 8, 31))
        self.assertEqual(sorted((s["hesap"], s["taraf"], s["tutar"]) for s in h["satirlar"]),
                         sorted([("391.20", "B", Dc("15526.13")), ("391.01", "B", Dc("12772.28")), ("391.10", "B", Dc("500000.00")),
                                 ("191.20", "A", Dc("560896.83")), ("191.01", "A", Dc("166.71")), ("191.10", "A", Dc("497145.36")),
                                 ("190", "B", Dc("528720.87")), ("689", "B", Dc("1189.62"))]))
        self.assertEqual((h["borc"], h["alacak"], h["dengeli"]), (Dc("1058208.90"), Dc("1058208.90"), True))
        self.assertIn("1.189,62", h["uyari"])
        self.assertIn("FAZLA", h["uyari"])

    def test_olustur_fis_ve_bakiyeler(self):
        self.canli_benzeri()
        m = self.olustur()
        fis = km.fis_of(m)
        self.assertEqual((fis.kaynak, fis.tarih, fis.kdv_mahsup_id), ("KDV_MAHSUP", D(2026, 8, 31), m.pk))
        self.assertEqual(fis.aciklama, "AĞUSTOS 2026 KDV DÖNEM MAHSUBU")
        self.assertEqual(sum(x.borc for x in fis.satirlar.all()), Dc("1058208.90"))
        self.assertEqual((_bak("191"), _bak("391"), _bak("190"), _bak("689")), (Dc("0"), Dc("0"), Dc("528720.87"), Dc("1189.62")))
        self.assertEqual((m.erp_191, m.erp_391, m.erp_devreden, m.fark), (Dc("1058208.90"), Dc("528298.41"), Dc("529910.49"), Dc("1189.62")))

    def test_odenecek_senaryosu_360_30(self):
        self.indirilecek("191.20", "1000", D(2026, 8, 5))
        self.hesaplanan("391.20", "3000", D(2026, 8, 6))
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="0", beyan_odenecek="2000")
        self.assertEqual((h["erp_devreden"], h["fark"]), (Dc("-2000.00"), Dc("0.00")))
        self.assertEqual(sorted((s["hesap"], s["taraf"], s["tutar"]) for s in h["satirlar"]),
                         sorted([("391.20", "B", Dc("3000.00")), ("191.20", "A", Dc("1000.00")), ("360.30", "A", Dc("2000.00"))]))
        self.assertEqual(h["uyari"], "")
        m = self.olustur(dev="0", ode="2000")
        self.assertEqual(_bak("360.30"), Dc("-2000.00"))
        self.assertEqual(m.fark, Dc("0.00"))

    def test_fark_eksik_yonu_alacak_ve_fark_hesabi_secimi(self):
        self.indirilecek("191.10", "800", D(2026, 8, 5))
        self.hesaplanan("391.10", "300", D(2026, 8, 6))                          # ERP devreden 500
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="520", beyan_odenecek="0", fark_hesap_kodu="770.11")
        self.assertEqual(h["fark"], Dc("-20.00"))
        self.assertIn(("770.11", "A", Dc("20.00")), [(s["hesap"], s["taraf"], s["tutar"]) for s in h["satirlar"]])
        self.assertIn("EKSİK", h["uyari"])
        self.assertEqual((h["borc"], h["alacak"]), (Dc("820.00"), Dc("820.00")))      # 391 300 + 190 520 | 191 800 + fark 20
        with self.assertRaises(km.KdvMahsupHatasi):
            km.hesapla(yil=2026, ay=8, beyan_devreden="520", beyan_odenecek="0", fark_hesap_kodu="770.01")
        km.hesapla(yil=2026, ay=8, beyan_devreden="500", beyan_odenecek="0", fark_hesap_kodu="770.01")     # fark yoksa hesap önemsiz

    def test_alt_hesap_bazinda_kapama_ters_bakiye(self):
        self.indirilecek("191.10", "100", D(2026, 8, 5))
        self.indirilecek("191.20", "50", D(2026, 8, 5))
        fis_olustur(tarih=D(2026, 8, 7), aciklama="İADE", kullanici=self.su, satirlar=[                       # 191.20 ters (alacak) bakiye: 70 alacak
            SatirGirdi(hesap_kodu="320.01", taraf="B", islem_tutari="120"), SatirGirdi(hesap_kodu="191.20", taraf="A", islem_tutari="120")])
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="80", beyan_odenecek="0")
        s = {(x["hesap"], x["taraf"]): x["tutar"] for x in h["satirlar"]}
        self.assertEqual((s[("191.10", "A")], s[("191.20", "B")]), (Dc("100.00"), Dc("70.00")))              # ters bakiye BORÇ ile sıfırlanır
        self.assertEqual(h["t191"], Dc("30.00"))
        km.olustur(yil=2026, ay=8, beyan_devreden="30", beyan_odenecek="0", kullanici=self.su)
        self.assertEqual((_bak("191.10"), _bak("191.20")), (Dc("0"), Dc("0")))

    def test_360_10_kdv2_tevkifat_hariç(self):
        self.indirilecek("191.20", "1000", D(2026, 8, 5))
        fis_olustur(tarih=D(2026, 8, 6), aciklama="TEVKİFAT", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu="320.01", taraf="B", islem_tutari="400"), SatirGirdi(hesap_kodu="360.10.0210", taraf="A", islem_tutari="400")])
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="1000", beyan_odenecek="0")
        self.assertFalse(any(s["hesap"].startswith("360.10") for s in h["satirlar"]))
        self.assertEqual((h["t191"], h["erp_devreden"], h["fark"]), (Dc("1000.00"), Dc("1000.00"), Dc("0.00")))
        self.olustur(dev="1000")
        self.assertEqual(_bak("360.10"), Dc("-400.00"))                                                       # KDV2 dokunulmadı

    def test_gecersiz_girdiler(self):
        self.indirilecek("191.20", "100", D(2026, 8, 5))
        for kw in (dict(beyan_devreden="10", beyan_odenecek="5"), dict(beyan_devreden="-1", beyan_odenecek="0"), dict(beyan_devreden="x", beyan_odenecek="0")):
            with self.assertRaises(km.KdvMahsupHatasi):
                km.hesapla(yil=2026, ay=8, **kw)
        for yil, ay in ((2026, 13), (1999, 5), ("x", 1)):
            with self.assertRaises(km.KdvMahsupHatasi):
                km.hesapla(yil=yil, ay=ay, beyan_devreden="0", beyan_odenecek="0")
        with self.assertRaises(km.KdvMahsupHatasi):                                                           # kapatılacak bir şey yok
            km.olustur(yil=2026, ay=3, beyan_devreden="0", beyan_odenecek="0", kullanici=self.su)


class DonemlerTest(KdvBase):
    def test_onceki_190_acilisi_sonraki_donem(self):
        self.canli_benzeri()
        self.olustur()                                                                                        # 08/2026: 190 B 528.720,87
        self.indirilecek("191.20", "5000", D(2026, 9, 10))
        self.hesaplanan("391.20", "2000", D(2026, 9, 12))
        h = km.hesapla(yil=2026, ay=9, beyan_devreden="531.720,87", beyan_odenecek="0")
        self.assertEqual((h["a190"], h["t191"], h["t391"], h["erp_devreden"], h["fark"]), (Dc("528720.87"), Dc("5000.00"), Dc("2000.00"), Dc("531720.87"), Dc("0.00")))
        s = {(x["hesap"], x["taraf"]): x["tutar"] for x in h["satirlar"]}
        self.assertEqual((s[("190", "A")], s[("190", "B")]), (Dc("528720.87"), Dc("531720.87")))              # önceki kapanır, yeni devreden yazılır
        m2 = km.olustur(yil=2026, ay=9, beyan_devreden="531.720,87", beyan_odenecek="0", kullanici=self.su)
        self.assertEqual(_bak("190"), Dc("531720.87"))
        self.assertEqual((m2.erp_190, m2.fark), (Dc("528720.87"), Dc("0.00")))

    def test_ayni_donem_ve_onceki_donem_engeli(self):
        self.indirilecek("191.20", "100", D(2026, 8, 5))
        self.olustur(dev="100")
        with self.assertRaises(km.KdvMahsupHatasi) as cm:
            self.olustur(dev="100")
        self.assertIn("zaten var", str(cm.exception))
        self.indirilecek("191.20", "50", D(2026, 9, 5))
        self.olustur(ay=9, dev="150")
        with self.assertRaises(km.KdvMahsupHatasi) as cm2:                                                   # 07/2026: 09/2026 var
            self.olustur(ay=7, dev="0")
        self.assertIn("önceki bir dönem eklenemez", str(cm2.exception))
        self.assertEqual(KdvMahsup.objects.filter(silindi=False).count(), 2)

    def test_duzenle_ayni_fis_numarasi_ve_yeniden_hesap(self):
        self.canli_benzeri()
        m = self.olustur()
        fis = km.fis_of(m)
        no, sayac = (fis.yil, fis.fis_no), YevmiyeFisi.objects.filter(silindi=False).count()
        km.guncelle(m, beyan_devreden="529.910,49", beyan_odenecek="0", fark_hesap_kodu="689", aciklama="düzeltme", kullanici=self.su)
        fis.refresh_from_db()
        m.refresh_from_db()
        self.assertEqual(((fis.yil, fis.fis_no), fis.silindi, fis.tarih), (no, False, D(2026, 8, 31)))
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), sayac)
        self.assertNotIn("689", [x[0] for x in self.satirlar(fis)])                                          # fark sıfırlandı
        self.assertEqual((_bak("190"), _bak("689"), _bak("191"), _bak("391")), (Dc("529910.49"), Dc("0"), Dc("0"), Dc("0")))
        self.assertEqual((m.fark, m.beyan_devreden, m.aciklama), (Dc("0.00"), Dc("529910.49"), "düzeltme"))
        self.assertIn("DÜZELTME", fis.aciklama)

    def test_duzenle_hata_fisi_degistirmez(self):
        self.canli_benzeri()
        m = self.olustur()
        onceki = self.satirlar(km.fis_of(m))
        with self.assertRaises(km.KdvMahsupHatasi):
            km.guncelle(m, beyan_devreden="10", beyan_odenecek="5", kullanici=self.su)
        self.assertEqual(self.satirlar(km.fis_of(m)), onceki)

    def test_sonraki_donem_varsa_duzenle_ve_sil_engellenir(self):
        self.indirilecek("191.20", "100", D(2026, 8, 5))
        m8 = self.olustur(dev="100")
        self.indirilecek("191.20", "50", D(2026, 9, 5))
        m9 = self.olustur(ay=9, dev="150")
        self.assertEqual(km.sonraki_mahsup(m8).pk, m9.pk)
        self.assertIsNone(km.sonraki_mahsup(m9))
        with self.assertRaises(km.KdvMahsupHatasi) as cm:
            km.guncelle(m8, beyan_devreden="100", beyan_odenecek="0", kullanici=self.su)
        self.assertIn("09/2026", str(cm.exception))
        with self.assertRaises(km.KdvMahsupHatasi):
            km.sil(m8, kullanici=self.su)
        km.sil(m9, kullanici=self.su)                                                                         # en yeniden geriye
        km.sil(m8, kullanici=self.su)

    def test_sil_fisle_birlikte_kalici_yalniz_super_kullanici(self):
        self.canli_benzeri()
        m = self.olustur()
        fis = km.fis_of(m)
        with self.assertRaises(km.KdvMahsupHatasi):
            km.sil(m, kullanici=self.sade)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
        km.sil(m, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
        m.refresh_from_db()
        self.assertTrue(m.silindi)
        self.assertEqual((_bak("191"), _bak("391"), _bak("190")), (Dc("1058208.90"), Dc("-528298.41"), Dc("0")))     # bakiyeler eski hâline döndü
        self.assertEqual(SilmeKaydi.objects.get(kaynak="KDV_MAHSUP").veri["kdv_mahsup"]["donem"], "08/2026")
        self.olustur()                                                                                         # silinen dönem yeniden yapılabilir

    def test_mahsup_sonrasi_degisiklik_uyarisi(self):
        self.indirilecek("191.10", "100", D(2026, 8, 5))
        m = self.olustur(dev="100")
        self.assertFalse(km.degisiklik_var(m))
        self.indirilecek("191.10", "10", D(2026, 9, 5))                                                       # dönem sonrası: uyarı yok
        self.assertFalse(km.degisiklik_var(m))
        self.indirilecek("191.10", "7", D(2026, 8, 15))                                                       # dönem içine sonradan kayıt
        self.assertTrue(km.degisiklik_var(m))

    def test_ilk_mahsupta_baslangictan_tum_kayitlar(self):
        self.hesaplanan("391.20", "1000", D(2026, 1, 1))                                                       # açılış
        self.indirilecek("191.20", "1500", D(2026, 2, 3))
        h = km.hesapla(yil=2026, ay=8, beyan_devreden="500", beyan_odenecek="0")
        self.assertEqual((h["t191"], h["t391"]), (Dc("1500.00"), Dc("1000.00")))


class EkranTest(KdvBase):
    def setUp(self):
        self.client.force_login(self.su)

    def post(self, islem, **kw):
        v = {"yil": "2026", "ay": "8", "beyan_devreden": "528.720,87", "beyan_odenecek": "", "fark_hesap": "689", "aciklama": "", "islem": islem, **kw}
        return v

    def test_liste_onizleme_kaydet_duzenle_sil(self):
        self.canli_benzeri()
        self.assertContains(self.client.get(reverse("core:kdv_mahsup_listesi")), "Henüz KDV dönem mahsubu yok")
        self.assertContains(self.client.get(reverse("core:kdv_mahsup_ekle")), "Beyanname")
        r = self.client.post(reverse("core:kdv_mahsup_ekle"), self.post("onizle"))                                # önizleme: kayıt yazmaz
        self.assertContains(r, "1.058.208,90")
        self.assertContains(r, "529.910,49")
        self.assertContains(r, "1.189,62")
        self.assertContains(r, "Onayla ve fişi yaz")
        self.assertEqual(KdvMahsup.objects.count(), 0)
        pdf = SimpleUploadedFile("kdv1.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        r = self.client.post(reverse("core:kdv_mahsup_ekle"), self.post("kaydet", dosya=pdf))
        self.assertEqual(r.status_code, 302)
        m = KdvMahsup.objects.get()
        self.assertTrue(m.dosya.name.startswith("kdv_mahsup/"))
        liste = self.client.get(reverse("core:kdv_mahsup_listesi"))
        self.assertContains(liste, "08/2026")
        self.assertContains(liste, "1.189,62")
        self.assertEqual(self.client.get(reverse("core:kdv_mahsup_dosya", args=[m.pk]))["Content-Type"], "application/pdf")
        self.assertEqual(self.client.post(reverse("core:kdv_mahsup_ekle"), self.post("kaydet")).status_code, 200)   # aynı dönem: form hatası
        r = self.client.get(reverse("core:kdv_mahsup_duzenle", args=[m.pk]))
        self.assertContains(r, "528.720,87")
        r = self.client.post(reverse("core:kdv_mahsup_duzenle", args=[m.pk]), self.post("kaydet", beyan_devreden="529.910,49", yil="1999", ay="1"))
        self.assertEqual(r.status_code, 302)
        m.refresh_from_db()
        self.assertEqual((m.yil, m.ay, m.beyan_devreden, m.fark), (2026, 8, Dc("529910.49"), Dc("0.00")))         # dönem değiştirilemez
        sil = reverse("core:kdv_mahsup_sil", args=[m.pk])
        self.assertContains(self.client.get(sil), "KDV")
        self.assertEqual(self.client.post(sil).status_code, 302)
        m.refresh_from_db()
        self.assertTrue(m.silindi)

    def test_sonraki_donem_kilidi_ve_uyari_listede(self):
        self.indirilecek("191.20", "100", D(2026, 8, 5))
        m8 = self.olustur(dev="100")
        self.indirilecek("191.20", "50", D(2026, 9, 5))
        self.olustur(ay=9, dev="150")
        self.indirilecek("191.20", "5", D(2026, 8, 20))                                                       # 08/2026 içine sonradan kayıt
        liste = self.client.get(reverse("core:kdv_mahsup_listesi"))
        self.assertContains(liste, "Mahsup sonrası 191/391 değişikliği var")
        self.assertContains(liste, "düzenleme kilitli")
        r = self.client.get(reverse("core:kdv_mahsup_duzenle", args=[m8.pk]))
        self.assertEqual(r.status_code, 302)
        r = self.client.get(reverse("core:kdv_mahsup_sil", args=[m8.pk]))
        self.assertEqual(r.status_code, 302)

    def test_ham_fis_kilidi_yetki(self):
        self.indirilecek("191.20", "100", D(2026, 8, 5))
        m = self.olustur(dev="100")
        fis = km.fis_of(m)
        self.assertEqual(self.client.get(reverse("core:fis_duzenle", args=[fis.pk]))["Location"], reverse("core:kdv_mahsup_listesi"))
        self.assertEqual(self.client.get(reverse("core:fis_sil", args=[fis.pk]))["Location"], reverse("core:kdv_mahsup_listesi"))
        self.assertContains(self.client.get(reverse("core:fis_detay", args=[fis.pk])), "KDV Dönem Mahsubu")
        self.client.force_login(self.sade)
        self.assertEqual(self.client.get(reverse("core:kdv_mahsup_listesi")).status_code, 200)
        self.client.force_login(User.objects.create_user("yok", password="x"))
        self.assertNotEqual(self.client.get(reverse("core:kdv_mahsup_listesi")).status_code, 200)
