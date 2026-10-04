"""Duran varlık / yatırım hesaplarının varlık-proje bazında detaylandırılması: grup → 000N hesabı, kalem=kart, mevcut karta ekle,
proje hesabına yazım, aktifleştirme, demirbaş satışı ve taşıma komutu (toplamları korur)."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import DuranVarlik, FaturaSatir, HesapPlani, YatirimProjesi, YevmiyeFisi, YevmiyeSatir
from core.services import duran_hesap as dh
from core.services import duran_varlik as dv_servis
from core.services import fatura as fs
from core.services import hesap_plani as hp
from core.services import raporlar
from core.services.yatirim_projesi import proje_aktiflestir, proje_olustur, proje_toplami
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_satis_hesap_demirbas import SatisHesapDemirbasTestBase

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class DuranHesapTestBase(SatisHesapDemirbasTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from core.models import Kur
        for g in (D(2026, 3, 11), D(2026, 3, 12), D(2026, 3, 13)):
            Kur.objects.create(tarih=g, usd_alis=Dc("30"))
        # gruplar (253/258 hareketsiz yaprak → grup açılabilir)
        for ust, gruplar in (("253", [("253.01", "ÜRETİM MAKİNELERİ"), ("253.02", "KALIPLAR")]),
                             ("258", [("258.01", "MAKİNE-TEÇHİZAT YATIRIMLARI"), ("258.03", "DEMİRBAŞ YATIRIMLARI")])):
            for kod, ad in gruplar:
                hp.hesap_olustur(kod=kod, ad=ad, ust_kodu=ust)
        hp.hesap_olustur(kod="255", ad="DEMİRBAŞLAR", rapor_grubu="BILANCO", rapor_kalemi="DDV")
        hp.hesap_olustur(kod="255.01", ad="OFİS MOBİLYA VE DONANIMLARI", ust_kodu="255")

    def _gider(self, hesap_id, tutar, *, proje=None, ad="", no="G1", tarih=D(2026, 3, 10)):
        satir = {"hesap_id": hesap_id, "miktar": "1", "birim_fiyat": tutar, "kdv_id": self.kdv0.pk,
                 "yatirim_projesi_id": proje.pk if proje else None, "varlik_adi": ad}
        return fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=tarih, fatura_no=no,
                                 satirlar=[satir], kullanici=self.su)


class ProjeHesabiTest(DuranHesapTestBase):
    def test_proje_grup_secilince_hesap_acilir_ve_baglanir(self):
        p1 = proje_olustur(ad="merdiven hattı", grup_kodu="258.01", kullanici=self.su)
        p2 = proje_olustur(ad="kaynak makinası", grup_kodu="258.01", kullanici=self.su)
        p3 = proje_olustur(ad="totem", grup_kodu="258.03", kullanici=self.su)
        self.assertEqual((p1.hesap_id, p2.hesap_id, p3.hesap_id), ("258.01.0001", "258.01.0002", "258.03.0001"))
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="258.01.0001").hesap_adi, "MERDİVEN HATTI")

    def test_proje_grubu_gecersizse_reddedilir(self):
        from core.services.yatirim_projesi import YatirimProjesiHatasi
        for kod in ("253.01", "258", "999.01"):
            with self.assertRaises(YatirimProjesiHatasi):
                proje_olustur(ad="x", grup_kodu=kod, kullanici=self.su)
        self.assertFalse(YatirimProjesi.objects.exists())

    def test_fatura_258_satiri_projenin_hesabina_yazilir_secilen_hesabi_ezer(self):
        p1 = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        p2 = proje_olustur(ad="totem", grup_kodu="258.03", kullanici=self.su)
        f = self._gider(p2.hesap_id, "5000", proje=p1)             # p2'nin hesabını seçti ama projesi p1
        self.assertEqual(f.fis.satirlar.filter(hesap__hesap_kodu__startswith="258.").get().hesap_id, p1.hesap_id)
        self.assertEqual(f.satirlar.get().hesap_id, p1.hesap_id)   # kaynak kayıt da yeni hesabı gösterir
        self.assertEqual(_bak(p1.hesap_id), Dc("5000.00"))
        self.assertEqual(_bak(p2.hesap_id), Dc("0"))
        self.assertEqual(proje_toplami(p1), _bak(p1.hesap_id))

    def test_258_projesiz_hala_zorunlu(self):
        proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        with self.assertRaises(fs.FaturaHatasi):
            self._gider("258.01.0001", "100")

    def test_fis_satiri_258_ve_proje_projenin_hesabina_gider(self):
        p = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        fis = fis_olustur(tarih=D(2026, 3, 10), aciklama="gümrük", kaynak=YevmiyeFisi.Kaynak.MANUEL, satirlar=[
            SatirGirdi(hesap_kodu="258.01.0001", taraf="B", islem_tutari="700", yatirim_projesi_id=p.pk),
            SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari="700")])
        self.assertEqual(fis.satirlar.get(yatirim_projesi=p).hesap_id, "258.01.0001")
        self.assertEqual(proje_toplami(p), Dc("700.00"))


class KalemKartTest(DuranHesapTestBase):
    def test_grup_secilince_her_kalem_icin_kart_ve_hesap_acilir(self):
        f = self._gider("253.01", "10000", ad="ısı tüneli")
        kart = DuranVarlik.objects.get()
        self.assertEqual((kart.hesap_id, kart.ad, kart.kaynak, kart.maliyet), ("253.01.0001", "ISI TÜNELİ", "FATURA", Dc("10000.00")))
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="253.01.0001").hesap_adi, "ISI TÜNELİ")
        self.assertEqual(f.fis.satirlar.filter(hesap_id="253.01.0001").count(), 1)
        self.assertEqual(f.satirlar.get().hesap_id, "253.01.0001")
        self.assertEqual(list(kart.fatura_satirlari.all()), list(f.satirlar.all()))
        self.assertEqual(_bak("253.01.0001"), kart.maliyet)
        # ikinci kalem → ikinci kart
        self._gider("253.01", "2000", ad="", no="G2")
        self.assertEqual(sorted(DuranVarlik.objects.values_list("hesap_id", flat=True)), ["253.01.0001", "253.01.0002"])
        self.assertEqual(DuranVarlik.objects.get(hesap_id="253.01.0002").ad, "ÜRETİM MAKİNELERİ")   # ad boş → grup adı

    def test_mevcut_karta_ekle_yeni_kart_acilmaz_maliyet_artar(self):
        self._gider("253.01", "10000", ad="ısı tüneli")
        f2 = self._gider("253.01.0001", "1500", no="G2", tarih=D(2026, 4, 10))      # nakliye, aynı varlık
        self.assertEqual(DuranVarlik.objects.count(), 1)
        kart = DuranVarlik.objects.get()
        self.assertEqual(kart.maliyet, Dc("11500.00"))
        self.assertEqual(_bak("253.01.0001"), Dc("11500.00"))
        self.assertEqual(kart.fatura_satirlari.count(), 2)
        self.assertEqual(f2.satirlar.get().hesap_id, "253.01.0001")

    def test_ek_maliyet_faturasi_duzenlenince_ve_silinince_kart_maliyeti_dogru(self):
        self._gider("253.01", "10000", ad="ısı tüneli")
        f2 = self._gider("253.01.0001", "1500", no="G2")
        s = f2.satirlar.get()
        fs.fatura_guncelle(f2, tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=f2.tarih, fatura_no="G2",
                           satirlar=[{"hesap_id": "253.01.0001", "miktar": "1", "birim_fiyat": "2500", "kdv_id": self.kdv0.pk}],
                           kullanici=self.su)
        self.assertEqual(DuranVarlik.objects.get().maliyet, Dc("12500.00"))
        self.assertEqual(_bak("253.01.0001"), Dc("12500.00"))
        fs.fatura_sil(f2, kullanici=self.su)
        self.assertEqual(DuranVarlik.objects.get().maliyet, Dc("10000.00"))

    def test_fatura_silinince_kart_silinir_hesap_kalir_ve_kod_yeniden_verilmez(self):
        f = self._gider("253.01", "10000", ad="ısı tüneli")
        fs.fatura_sil(f, kullanici=self.su)
        self.assertFalse(DuranVarlik.objects.filter(silindi=False).exists())
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="253.01.0001", silindi=False).exists())      # hesap KALIR
        self._gider("253.01", "300", no="G2")
        self.assertEqual(DuranVarlik.objects.get(silindi=False).hesap_id, "253.01.0002")                  # yeni karta verilmez

    def test_satilmis_karta_ek_maliyet_yazilamaz(self):
        self._gider("253.01", "10000", ad="testere")
        kart = DuranVarlik.objects.get()
        self._fatura([self._dv_satir(kart, "12000")])                        # satış
        kart.refresh_from_db()
        self.assertEqual(kart.durum, "SATILDI")
        with self.assertRaises(fs.FaturaHatasi):
            self._gider("253.01.0001", "50", no="G2")

    def test_demirbas_satisi_dogru_alt_hesaba_alacak(self):
        self._gider("253.01", "10000", ad="testere")
        self._gider("253.02", "4000", ad="kalıp", no="G2")
        kart = DuranVarlik.objects.get(ad="KALIP")
        f = self._fatura([self._dv_satir(kart, "5000")])
        s = self._fis(f)
        self.assertEqual(s[("253.02.0001", "A")][0], Dc("4000.00"))
        self.assertEqual(s[("679", "A")][0], Dc("1000.00"))
        self.assertEqual(_bak("253.02.0001"), Dc("0"))                      # satılınca hesap 0, KALIR
        self.assertEqual(_bak("253.01.0001"), Dc("10000.00"))               # diğer kart etkilenmez

    def test_manuel_kart_grup_secilince_hesap_acilir(self):
        v = dv_servis.duran_varlik_olustur(ad="mobilya", grup_kodu="255.01", aktiflestirme_tarihi=D(2026, 1, 1),
                                           maliyet=Dc("500"), kullanici=self.su)
        self.assertEqual((v.hesap_id, v.kaynak), ("255.01.0001", "ACILIS"))
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.duran_varlik_olustur(ad="x", grup_kodu="258.01", aktiflestirme_tarihi=D(2026, 1, 1),
                                           maliyet=Dc("1"), kullanici=self.su)                      # 258 grubu kart değil
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.duran_varlik_olustur(ad="x", grup_kodu="255.01", aktiflestirme_tarihi=D(2026, 1, 1),
                                           maliyet=Dc("1"), fatura_satirlari=[1], kullanici=self.su)


class AktiflestirmeHesapTest(DuranHesapTestBase):
    def test_aktiflestirme_grup_secilince_kart_ve_hesap_acilir(self):
        p = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        self._gider(p.hesap_id, "9000", proje=p)
        proje_aktiflestir(p, tarih=D(2026, 4, 10), kullanici=self.su, satirlar=[
            {"grup_kodu": "253.01", "varlik_adi": "makine a", "tutar": Dc("6000")},
            {"grup_kodu": "253.02", "varlik_adi": "kalıp b", "tutar": Dc("3000")}])
        a, b = DuranVarlik.objects.order_by("hesap_id")
        self.assertEqual((a.hesap_id, a.kaynak, a.maliyet), ("253.01.0001", "PROJE", Dc("6000")))
        self.assertEqual((b.hesap_id, b.maliyet), ("253.02.0001", Dc("3000")))
        self.assertEqual(_bak("253.01.0001"), Dc("6000.00"))
        self.assertEqual(_bak(p.hesap_id), Dc("0"))                         # 258 hesabı boşalır, KALIR
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu=p.hesap_id, silindi=False).exists())


class TasimaKomutuTest(DuranHesapTestBase):
    """Eski (düz) yapıdaki satırları taşır: 253 → kart hesabı, 258 → proje hesabı; toplamlar/kaynak kayıtlar tutarlı."""

    def _eski_yapi(self):
        # düz yapı: 253/258 üst hesaplara dönmeden ÖNCE satır yazılmış gibi — grupları kaldır, düz hesaplarda hareket yarat
        HesapPlani.objects.filter(hesap_kodu__regex=r"^(253|258)\.").delete()
        p = YatirimProjesi.objects.create(kod="YP-0001", ad="HAT", created_by=self.su, updated_by=self.su)
        f1 = fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=D(2026, 3, 10), fatura_no="A1",
                               satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "10000", "kdv_id": self.kdv0.pk}],
                               kullanici=self.su)
        dv = dv_servis.duran_varlik_olustur(ad="ısı tüneli", hesap_id="253", aktiflestirme_tarihi=D(2026, 3, 10),
                                            maliyet=Dc("0"), fatura_satirlari=[f1.satirlar.get()], kullanici=self.su)
        f2 = fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=D(2026, 3, 11), fatura_no="A2",
                               satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "7000", "kdv_id": self.kdv0.pk,
                                          "yatirim_projesi_id": p.pk}], kullanici=self.su)
        fis_olustur(tarih=D(2026, 3, 12), aciklama="gümrük", kaynak=YevmiyeFisi.Kaynak.MANUEL, satirlar=[
            SatirGirdi(hesap_kodu="258", taraf="B", islem_tutari="300", yatirim_projesi_id=p.pk),
            SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari="300")])
        return dv, p, f1, f2

    def _komut(self, *ek):
        out = StringIO()
        d = DuranVarlik.objects.get()
        patches = (mock.patch.dict("core.management.commands.duran_hesap_detaylandir.KART_ESLEME", {}, clear=True),
                   mock.patch.dict("core.management.commands.duran_hesap_detaylandir.PROJE_ESLEME", {}, clear=True))
        with patches[0], patches[1]:
            from core.management.commands import duran_hesap_detaylandir as m
            m.KART_ESLEME["253.01.0001"] = d.demirbas_kodu
            m.PROJE_ESLEME["258.01.0001"] = "YP-0001"
            with mock.patch.object(m, "BEKLENEN", {"253": Dc("10000.00"), "255": Dc("0.00"), "258": Dc("7300.00"),
                                                   "260": Dc("0.00")}):
                call_command("duran_hesap_detaylandir", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_tasir_toplamlari_korur(self):
        dv, p, f1, f2 = self._eski_yapi()
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="253").count(), 1)               # dry-run yazmadı
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="253.01.0001").exists())
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertIn("Eşleşmeyen/bağlantısız satır: 0", c)
        dv.refresh_from_db(); p.refresh_from_db()
        self.assertEqual((dv.hesap_id, p.hesap_id), ("253.01.0001", "258.01.0001"))
        self.assertEqual(_bak("253.01.0001"), Dc("10000.00"))
        self.assertEqual(_bak("258.01.0001"), Dc("7300.00"))                                    # fatura 7000 + manuel 300
        self.assertEqual(_bak("253"), Dc("10000.00"))
        self.assertEqual(_bak("258"), Dc("7300.00"))
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id__in=("253", "258")).count(), 0)   # üst hesapta satır kalmadı
        self.assertEqual(f1.satirlar.get().hesap_id, "253.01.0001")                             # kaynak kayıtlar yeni hesabı gösterir
        self.assertEqual(f2.satirlar.get().hesap_id, "258.01.0001")
        self.assertEqual(proje_toplami(p), _bak("258.01.0001"))
        # tekrar çalıştırmak zararsız
        self.assertIn("UYGULANDI", self._komut("--uygula"))

    def test_eslesmeyen_satir_varsa_uygula_iptal(self):
        dv, p, f1, f2 = self._eski_yapi()
        fis_olustur(tarih=D(2026, 3, 13), aciklama="bağsız", kaynak=YevmiyeFisi.Kaynak.MANUEL, satirlar=[
            SatirGirdi(hesap_kodu="253", taraf="B", islem_tutari="50"), SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari="50")])
        with self.assertRaises(CommandError):
            self._komut("--uygula")
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="253.01.0001").exists())          # hiçbir şey yazılmadı
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="253").count(), 2)
