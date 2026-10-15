"""'Düzenle → değiştirmeden kaydet' (2026-10-15): fişi etkileyen girdi (kalemler, tarih, tip, cari, PB, kur, depo, stopaj, ortak) DEĞİŞMEDİYSE onaylı faturada fiş / kalemler / stok hareketleri /
kur farkı satırları YENİDEN ÜRETİLMEZ (aynı pk'ler) ve proje/kart doğrulaması çalışmaz; yalnız başlık (fatura no, vade, açıklama, faturasız, ek) güncellenir. Fişi etkileyen bir alan
değişirse tam yol ve mevcut korumalar AYNEN çalışır. Fiş yeniden yazılırken kur farkı satırının hedefi (258 + proje / 646-656) korunur. Şahsi alışta 131.01'li kalem + düzeltme komutu."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import (Cari, DuranVarlik, Fatura, FaturaSatir, FaturaTipi, HesapPlani, KdvOrani, Kur, StokHareket, YatirimProjesi,
                         YevmiyeFisi, YevmiyeSatir)
from core.services import fatura as fs
from core.services import kur_farki as kf
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur
from core.services.yatirim_projesi import proje_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests import test_donemsel_gider as tdg
from core.tests import test_duran_hesap as tdh
from core.tests import test_fatura_form_fis_karsilastirma as kt
from core.tests import test_sahsi_alis as tsa

D = datetime.date
Dc = Decimal


def girdi_of(f):
    """Kayıtlı faturanın Düzenle formunun ön-doldurduğu kalem girdileri (view'in ``_fatura_satir_girdileri`` çıktısıyla aynı anahtarlar)."""
    cikti = []
    for s in f.satirlar.filter(silindi=False).order_by("pk").select_related("stok"):
        tevkifat_yok = bool(s.stok_id and not s.tevkifat_id and s.stok.tevkifat_id)
        if s.donem_baslangic:                                                     # dönemsel gider: form hesap = gider hesabı, gizli alan donem_hesap = faturaya özel 180.xx
            cikti.append({"tur": "HESAP", "hesap_id": s.donem_gider_id, "donemsel": True, "donem_hesap_id": s.hesap_id, "donem_baslangic": s.donem_baslangic,
                          "donem_bitis": s.donem_bitis, "donem_aciklama": s.donem_aciklama, "kdv_id": s.kdv_id, "tevkifat_id": s.tevkifat_id, "miktar": s.miktar,
                          "birim_fiyat": s.birim_fiyat})
            continue
        cikti.append({"tur": s.satir_tipi, "stok_id": s.stok_id, "hesap_id": None if (s.stok_id or s.demirbas_id) else s.hesap_id, "demirbas_id": s.demirbas_id,
                      "kdv_id": s.kdv_id, "tevkifat_id": s.tevkifat_id, "tevkifat_yok": tevkifat_yok, "yatirim_projesi_id": s.yatirim_projesi_id,
                      "miktar": s.miktar, "birim_fiyat": s.birim_fiyat})
    return cikti


def tekrar_kaydet(test, f, satirlar=None, **degisim):
    """Faturayı mevcut değerleriyle (ya da ``degisim`` ile) ``fatura_guncelle``'ye gönderir — Düzenle'de 'değiştirmeden kaydet' eşdeğeri."""
    f = Fatura.objects.get(pk=f.pk)
    arg = dict(tip_id=f.tip_id, cari_id=f.cari_id, tarih=f.tarih, satirlar=girdi_of(f) if satirlar is None else satirlar, fatura_no=f.fatura_no,
               para_birimi=f.para_birimi, depo_id=f.depo_id, aciklama=f.aciklama, vade_tarihi=f.vade_tarihi, kur=(f.kur if f.para_birimi != "TRY" else None),
               sahsi_alis=f.sahsi_alis, sahsi_ortak_id=f.sahsi_ortak_id, gv_stopaj_orani=f.gv_stopaj_orani, kullanici=test.su, faturasiz=f.faturasiz)
    arg.update(degisim)
    fatura_guncelle(f, **arg)
    return Fatura.objects.get(pk=f.pk)


def iz(f):
    """Yeniden üretim izi: fiş + fiş satırları + fatura kalemleri + stok hareketleri + kur farkı çocukları — pk'ler (aynı pk = dokunulmadı)."""
    f = Fatura.objects.get(pk=f.pk)
    return {"fis": f.fis_id,
            "fis_satirlar": sorted(f.fis.satirlar.filter(silindi=False).values_list("pk", "hesap_id", "borc", "alacak")),
            "kalemler": sorted(f.satirlar.filter(silindi=False).values_list("pk", flat=True)),
            "hareketler": sorted(StokHareket.objects.filter(fatura_satir__fatura=f, silindi=False).values_list("pk", flat=True)),
            "kur_farki": sorted(f.fis.satirlar.filter(silindi=False, ana_satir__isnull=False).values_list("pk", "hesap_id", "yatirim_projesi_id"))}


class StokFaturalariDegismedenKaydetTest(kt.KarsilastirmaTaban):
    def _ayni(self, f, **degisim):
        once = iz(f)
        yeni = tekrar_kaydet(self, f, **degisim)
        self.assertEqual(iz(f), once, "fiş/kalem/hareket yeniden üretildi")
        return yeni

    def test_karma_alis_baslik_degisimi_fisi_yeniden_yazmaz(self):
        f = self._kes([self._stok("100", "50"), self._hesap_satir("730.09", "200")], no="DGM-1")
        self.assertEqual(len(iz(f)["hareketler"]), 1)
        yeni = self._ayni(f, aciklama="yeni açıklama", vade_tarihi=D(2026, 5, 1))
        self.assertEqual((yeni.aciklama, yeni.vade_tarihi), ("yeni açıklama", D(2026, 5, 1)))

    def test_fatura_no_degisince_yalniz_baslik_aciklamalari_yenilenir(self):
        f = self._kes([self._stok("100", "50"), self._hesap_satir("730.09", "200")], no="DGM-2")
        once = iz(f)
        yeni = tekrar_kaydet(self, f, fatura_no="DGM-2B")
        self.assertEqual(iz(f), once)                                                            # tutar/hesap/satır pk'leri aynı
        self.assertIn("DGM-2B", yeni.fis.aciklama)
        self.assertNotIn("DGM-2 ", yeni.fis.aciklama + " ")
        (h,) = StokHareket.objects.filter(fatura_satir__fatura=yeni, silindi=False)
        self.assertIn("DGM-2B", h.aciklama)

    def test_faturasiz_isareti_ve_numara_silme(self):
        f = self._kes([self._stok("10", "5")], no="DGM-3")
        yeni = self._ayni(f, fatura_no="", faturasiz=True)
        self.assertEqual((yeni.fatura_no, yeni.faturasiz), ("", True))

    def test_ihrac_kayitli_alis(self):
        f = self._kes([self._stok("5", "200"), self._hesap_satir("730.09", "300")], tip=self.ihrac, no="DGM-4")
        self._ayni(f, aciklama="ihraç")

    def test_gider_ve_yalniz_kdv_ve_smm(self):
        for ad, tip, kalem, ek in (("g", self.gider_tip, [self._hesap_satir("730.09", "1000")], {}),
                                   ("k", self.yalniz_kdv, [self._stok("10", "100")], {}),
                                   ("s", self.smm, [self._hesap_satir("770.03", "5000")], {"gv_stopaj_orani": "20"})):
            f = self._kes(kalem, tip=tip, depo=False, no=f"DGM-5{ad}", **ek)
            self._ayni(f, aciklama=f"a-{ad}")

    def test_alis_satis_iade_tipi_maliyet_fisi(self):
        self.stok_girisi("500", "50")
        f = fatura_olustur(tip_id=self.satis_iade.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="DGM-6", satirlar=[self._stok("4", "120")],
                           depo_id=self.depo.pk, kullanici=self.su)
        once = iz(f)
        maliyet_fisleri = sorted(YevmiyeSatir.objects.filter(fis__kaynak__in=[k for k in YevmiyeFisi.Kaynak.values if k != "FATURA"], silindi=False).values_list("pk", flat=True))
        yeni = tekrar_kaydet(self, f, aciklama="iade")
        self.assertEqual(iz(f), once)
        self.assertEqual(sorted(YevmiyeSatir.objects.filter(fis__kaynak__in=[k for k in YevmiyeFisi.Kaynak.values if k != "FATURA"], silindi=False).values_list("pk", flat=True)), maliyet_fisleri)
        self.assertEqual(yeni.aciklama, "iade")
        # fatura no değişimi: fiş + maliyet fişi YALNIZ başlık metni yenilenir, satır pk'leri ve tutarlar aynı
        maliyet_satir = lambda: sorted(YevmiyeSatir.objects.filter(fis__kaynak="STOK_SATIS", silindi=False).values_list("pk", "hesap_id", "borc", "alacak"))
        m_once = maliyet_satir()
        yeni = tekrar_kaydet(self, f, fatura_no="DGM-6B")
        self.assertEqual(iz(f), once)
        self.assertEqual(maliyet_satir(), m_once)
        fis = YevmiyeSatir.objects.filter(fis__kaynak="STOK_SATIS", silindi=False).first().fis
        self.assertIn("DGM-6B", fis.aciklama)
        self.assertIn("DGM-6B", yeni.fis.aciklama)

    def test_satis_alis_iade_tipi(self):
        self.stok_girisi("500", "50")
        f = fatura_olustur(tip_id=self.alis_iade.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="DGM-7", satirlar=[self._stok("6", "50")],
                           depo_id=self.depo.pk, kullanici=self.su)
        self._ayni(f, aciklama="alış iade")

    def test_satis_ihrac_kayitli(self):
        from core.models import KdvOrani as K
        if not K.objects.filter(oran=0).exists():
            K.objects.create(aciklama="KDV YOK", oran=Dc("0"))
        satis_ihrac = FaturaTipi.objects.create(ad="SATIŞ FATURASI-İHRAÇ KAYITLI", yon=FaturaTipi.Yon.SATIS)
        from core.models import KategoriHesap
        KategoriHesap.objects.create(kategori=self.alt, fatura_tipi=satis_ihrac, hesap=HesapPlani.objects.get(hesap_kodu="600"))
        self.stok_girisi("500", "50")
        f = fatura_olustur(tip_id=satis_ihrac.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="DGM-8", satirlar=[self._stok("10", "100")],
                           depo_id=self.depo.pk, kullanici=self.su)
        self._ayni(f, aciklama="ihracat")

    def test_doviz_alis_kur_ayni(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.eur.pk, tarih=D(2026, 3, 10), fatura_no="DGM-9", para_birimi="EUR",
                           satirlar=[self._stok("12", "13,5")], depo_id=self.depo.pk, kullanici=self.su)
        self._ayni(f, aciklama="eur")

    def test_tevkifatli_satis(self):
        from core.models import Stok
        self.stok_girisi("500", "50")
        Stok.objects.filter(pk=self.stok.pk).update(tevkifat=self.tev)
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="DGM-10", satirlar=[self._stok("20", "120")],
                           depo_id=self.depo.pk, kullanici=self.su)
        self._ayni(f, aciklama="tevkifat")

    def test_kalem_veya_tutar_degisirse_tam_yol_ve_yeniden_uretim(self):
        f = self._kes([self._stok("100", "50"), self._hesap_satir("730.09", "200")], no="DGM-11")
        once = iz(f)
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("55.5")
        tekrar_kaydet(self, f, satirlar=g)
        sonra = iz(f)
        self.assertNotEqual(sonra["kalemler"], once["kalemler"])                                 # kalemler yeniden yazıldı
        self.assertNotEqual(sonra["fis_satirlar"], once["fis_satirlar"])
        fis = {s.hesap_id: s.alacak for s in Fatura.objects.get(pk=f.pk).fis.satirlar.filter(silindi=False)}
        self.assertEqual(fis["320.10.0001"], (Dc("100") * Dc("55.5") + Dc("200")) * Dc("1.2"))

    def test_tarih_cari_tip_degisince_tam_yol(self):
        f = self._kes([self._stok("10", "5")], no="DGM-12")
        once = iz(f)
        Kur.objects.get_or_create(tarih=D(2026, 3, 11), defaults={"usd_alis": Dc("30")})
        tekrar_kaydet(self, f, tarih=D(2026, 3, 11))
        self.assertNotEqual(iz(f)["fis_satirlar"][0][0], None)
        self.assertNotEqual(iz(f)["kalemler"], once["kalemler"])
        self.assertEqual(Fatura.objects.get(pk=f.pk).fis.tarih, D(2026, 3, 11))

    def test_mukerrer_fatura_no_baslik_yolunda_da_engellenir(self):
        self._kes([self._stok("10", "5")], no="DGM-13A")
        f = self._kes([self._stok("10", "5")], no="DGM-13B")
        with self.assertRaises(FaturaHatasi):
            tekrar_kaydet(self, f, fatura_no="DGM-13A")


class ProjeKartDemirbasDegismedenKaydetTest(tdh.DuranHesapTestBase):
    def _proje_faturasi(self):
        proje = proje_olustur(ad="merdiven hattı", grup_kodu="258.01", kullanici=self.su)
        f = self._gider(proje.hesap_id, "100000", proje=proje, no="PRJ-1")
        return proje, f

    def _post(self, f, **degisim):
        """Düzenle ekranının POST'u (view): kayıtlı değerlerden form verisi kurar."""
        self.client.force_login(self.su)
        f = Fatura.objects.get(pk=f.pk)
        s = f.satirlar.filter(silindi=False).order_by("pk")
        v = {"tip": f.tip_id, "cari": f.cari_id, "tarih": f.tarih.isoformat(), "para_birimi": f.para_birimi, "fatura_no": f.fatura_no, "depo": "",
             "aciklama": f.aciklama, "vade_tarihi": f.vade_tarihi.isoformat() if f.vade_tarihi else "",
             "form-TOTAL_FORMS": str(s.count()), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, k in enumerate(s):
            v.update({f"form-{i}-hesap": k.hesap_id, f"form-{i}-miktar": str(k.miktar).replace(".", ","), f"form-{i}-birim_fiyat": str(k.birim_fiyat).replace(".", ","),
                      f"form-{i}-kdv": k.kdv_id or "", f"form-{i}-yatirim_projesi": k.yatirim_projesi_id or ""})
        v.update(degisim)
        return self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), v)

    def test_aktiflesmis_projede_baslik_degisimi_kaydedilir_kalem_degisimi_reddedilir(self):
        proje, f = self._proje_faturasi()
        YatirimProjesi.objects.filter(pk=proje.pk).update(durum="AKTIFLESTI")
        once = iz(f)
        r = self._post(f, aciklama="sadece açıklama", vade_tarihi="2030-01-01")
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["fform"].errors)
        f.refresh_from_db()
        self.assertEqual((f.aciklama, f.vade_tarihi), ("sadece açıklama", D(2030, 1, 1)))
        self.assertEqual(iz(f), once)
        # tutar değişirse koruma AYNEN: reddedilir, hiçbir şey değişmez
        r = self._post(f, **{"form-0-birim_fiyat": "120000"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "aktifleşmiş")
        self.assertEqual(iz(f), once)

    def test_kapanmis_projede_baslik_degisimi_kaydedilir(self):
        proje, f = self._proje_faturasi()
        YatirimProjesi.objects.filter(pk=proje.pk).update(durum="KAPANDI")
        r = self._post(f, fatura_no="PRJ-1B")
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["fform"].errors)
        self.assertEqual(Fatura.objects.get(pk=f.pk).fatura_no, "PRJ-1B")
        r = self._post(f, **{"form-0-birim_fiyat": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "kapanmış")

    def test_yeni_faturada_aktiflesmis_proje_secilemez(self):
        proje, _f = self._proje_faturasi()
        YatirimProjesi.objects.filter(pk=proje.pk).update(durum="AKTIFLESTI")
        with self.assertRaises(FaturaHatasi):
            self._gider(proje.hesap_id, "50", proje=proje, no="PRJ-2")

    def test_satilmis_kartli_faturada_baslik_degisimi_kaydedilir_tutar_reddedilir(self):
        f = self._gider("253.01", "10000", ad="testere", no="KRT-1")
        kart = DuranVarlik.objects.get()
        self._fatura([self._dv_satir(kart, "12000")])
        kart.refresh_from_db()
        self.assertEqual(kart.durum, "SATILDI")
        once = iz(f)
        tekrar_kaydet(self, f, aciklama="satılmış kartlı fatura")
        self.assertEqual(iz(f), once)
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("10500")
        with self.assertRaises(FaturaHatasi):
            tekrar_kaydet(self, f, satirlar=g)

    def test_demirbas_satirli_satis_faturasi_degismeden_kaydet(self):
        self._gider("253.01", "10000", ad="testere", no="DV-1")
        kart = DuranVarlik.objects.get()
        f = self._fatura([self._dv_satir(kart, "12000")], no="DV-S")
        once = iz(f)
        tekrar_kaydet(self, f, aciklama="demirbaş satışı")
        self.assertEqual(iz(f), once)
        kart.refresh_from_db()
        self.assertEqual((kart.durum, kart.satis_faturasi_id), ("SATILDI", f.pk))
        # tutar değişirse tam yol: kart yeniden SATILDI işaretlenir
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("13000")
        tekrar_kaydet(self, f, satirlar=g)
        kart.refresh_from_db()
        self.assertEqual((kart.durum, kart.satis_faturasi_id), ("SATILDI", f.pk))
        self.assertEqual(Fatura.objects.get(pk=f.pk).ara_toplam, Dc("13000.00"))

    def test_alis_iade_hesap_satiri(self):
        f = self._fatura([self._hesap_satir("770.01", "1000", self.kdv20)], tip=self.iade, no="IADE-1")
        once = iz(f)
        tekrar_kaydet(self, f, aciklama="alış iade")
        self.assertEqual(iz(f), once)


class SahsiAlisDegismedenKaydetTest(tsa.SahsiAlisTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ortak_yeni = HesapPlani.objects.create(hesap_kodu="500.10.0001", hesap_adi="NURİ ÖZER SERMAYE", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
        cls.su = cls.u

    def _sahsi(self, no="SH-1"):
        return fatura_olustur(tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29), fatura_no=no, satirlar=self._satir("3855.83"),
                              sahsi_alis=True, sahsi_ortak_id=self.ortak_yeni.pk, kullanici=self.u)

    def test_bayat_131_01_satiri_olan_sahsi_alis_degismeden_kaydedilir_fis_ayni(self):
        f = self._sahsi()
        FaturaSatir.objects.filter(fatura=f).update(hesap_id="131.01")                              # 131.01 → 500.10.0001 taşımasından kalan bayat kalem hesabı
        HesapPlani.objects.filter(hesap_kodu="131.01").update(aktif=False)
        once = iz(f)
        yeni = tekrar_kaydet(self, f, satirlar=[{**g, "hesap_id": self.ortak_yeni.pk} for g in girdi_of(f)], aciklama="şahsi")   # tarayıcı JS'i: kalem hesabı = seçili ortak hesabı
        self.assertEqual(iz(f), once)
        self.assertEqual(yeni.aciklama, "şahsi")
        self.assertEqual(sorted(YevmiyeSatir.objects.filter(fis=f.fis, silindi=False).values_list("hesap_id", flat=True)), sorted(["500.10.0001", "191", "320.10.0001", "602.01"]))

    def test_duzeltme_komutu_onizleme_uygula_ve_ikinci_calistirma(self):
        f = self._sahsi()
        FaturaSatir.objects.filter(fatura=f).update(hesap_id="131.01")
        once = iz(f)

        def kos(*a):
            cikti = StringIO()
            call_command("sahsi_alis_satir_hesabi_duzelt", *a, stdout=cikti)
            return cikti.getvalue()
        self.assertIn("Değişecek kalem: 1", kos())
        self.assertEqual(FaturaSatir.objects.get(fatura=f).hesap_id, "131.01")                      # önizleme yazmaz
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("UYGULANDI: 1 kalem", kos("--uygula", "--log-dizini", d))
        self.assertEqual(FaturaSatir.objects.get(fatura=f).hesap_id, "500.10.0001")
        self.assertEqual(iz(f), once)                                                                # fiş dokunulmadı
        self.assertIn("Değişecek kalem: 0", kos())                                                   # ikinci çalıştırma 0 iş


class KurFarkiHedefiKorunurTest(TestCase):
    """Fiş yeniden yazılırken (kalem değişti → tam yol) kur farkı satırının mevcut hesabı + projesi korunur."""

    @classmethod
    def setUpTestData(cls):
        from core.tests.test_banka_hesap_hareketi import _hesap
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Dc("30"))
        Kur.objects.create(tarih=D(2026, 3, 20), usd_alis=Dc("31"))
        Kur.objects.create(tarih=D(2026, 4, 10), usd_alis=Dc("33"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV")
        _hesap("320.10.0001", "YONGKANG")
        _hesap("102.01.0001", "TL BANKA")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("770.01", "GENEL GİDER")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        from core.services import hesap_plani as hp
        hp.hesap_olustur(kod="258.01", ad="MAKİNE-TEÇHİZAT YATIRIMLARI", ust_kodu="258")
        cls.kdv0 = KdvOrani.objects.create(aciklama="KDV YOK", oran=Dc("0"))
        cls.gider = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="YONGKANG", para_birimi="USD", muhasebe_kodu="320.10.0001")
        cls.su = User.objects.create_superuser("kfk", password="x")

    def _fatura_sonra_avans(self, proje, tutar="1000"):
        """Önce fatura (kur 31), sonra GERİYE DÖNÜK avans ödemesi (kur 30): kur farkı satırı FATURA fişinde oluşur (#169 ile aynı biçim)."""
        f = fatura_olustur(tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 20), fatura_no="KF-1", para_birimi="USD", kullanici=self.su,
                           satirlar=[{"hesap_id": proje.hesap_id, "miktar": "1", "birim_fiyat": tutar, "kdv_id": self.kdv0.pk, "yatirim_projesi_id": proje.pk}])
        fis_olustur(tarih=D(2026, 3, 10), satirlar=[SatirGirdi(hesap_kodu="320.10.0001", taraf="B", islem_tutari="1000", islem_pb="USD", islem_kuru="30"),
                                                       SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="30000")])
        return Fatura.objects.get(pk=f.pk)

    def _cocuklar(self, f):
        return [(s.hesap_id, s.yatirim_projesi_id) for s in Fatura.objects.get(pk=f.pk).fis.satirlar.filter(silindi=False, ana_satir__isnull=False)]

    def test_258_deki_kur_farki_cari_ayari_646_656_olsa_da_258_de_kalir(self):
        proje = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        f = self._fatura_sonra_avans(proje)
        self.assertEqual(self._cocuklar(f), [(proje.hesap_id, proje.pk)])
        Cari.objects.filter(pk=self.cari.pk).update(kur_farki_hedefi=Cari.KurFarkiHedefi.HESAP_646_656)       # Ocean vakası: ayar sonradan 646-656'ya alınmış
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("900")                                                                        # kalem değişti → fiş yeniden yazılır
        tekrar_kaydet(self, f, satirlar=g)
        self.assertEqual(self._cocuklar(f), [(proje.hesap_id, proje.pk)])

    def test_656_daki_kur_farki_proje_devam_etse_de_656_da_kalir(self):
        proje = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        Cari.objects.filter(pk=self.cari.pk).update(kur_farki_hedefi=Cari.KurFarkiHedefi.HESAP_646_656)
        f = self._fatura_sonra_avans(proje)
        onceki = self._cocuklar(f)
        self.assertTrue(onceki and all(h in ("646", "656") and p is None for h, p in onceki), onceki)
        Cari.objects.filter(pk=self.cari.pk).update(kur_farki_hedefi=Cari.KurFarkiHedefi.OTOMATIK)           # artık otomatik: motor yeni satırı 258'e yazardı
        self.assertIsNotNone(kf.yatirim_hedefi("320.10.0001"))
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("900")
        tekrar_kaydet(self, f, satirlar=g)
        sonra = self._cocuklar(f)
        self.assertTrue(sonra and all(h in ("646", "656") and p is None for h, p in sonra), sonra)             # geçmişte yazılan hedef değişmez

    def test_degismeden_kaydetmede_kur_farki_satiri_hic_dokunulmaz(self):
        proje = proje_olustur(ad="hat", grup_kodu="258.01", kullanici=self.su)
        f = self._fatura_sonra_avans(proje)
        once = iz(f)
        tekrar_kaydet(self, f, aciklama="sadece açıklama")
        self.assertEqual(iz(f), once)
        self.assertEqual(self._cocuklar(f), [(proje.hesap_id, proje.pk)])


class DonemselGiderDegismedenKaydetTest(tdg.DonemselFaturaBase):
    def test_baslik_degisimi_fisi_ve_dagitimi_yeniden_uretmez(self):
        from core.models import DonemselDagitim
        from core.services import donemsel_gider as dg
        f = self._donemsel("12000", kdv=self.kdv20)
        dg.uret(D(2026, 9, 30))                                                            # 3 dağıtım fişi oluştu
        dagitim = sorted(DonemselDagitim.objects.filter(fatura=f).values_list("pk", "fis_id"))
        once = iz(f)
        yeni = tekrar_kaydet(self, f, aciklama="dönemsel", vade_tarihi=D(2026, 8, 1))
        self.assertEqual(iz(f), once)
        self.assertEqual(sorted(DonemselDagitim.objects.filter(fatura=f).values_list("pk", "fis_id")), dagitim)
        self.assertEqual((yeni.aciklama, yeni.vade_tarihi), ("dönemsel", D(2026, 8, 1)))

    def test_dagitim_alani_degisirse_tam_yol(self):
        f = self._donemsel("12000")
        once = iz(f)
        g = girdi_of(f)
        g[0]["birim_fiyat"] = Dc("13200")
        tekrar_kaydet(self, f, satirlar=g)
        self.assertNotEqual(iz(f)["kalemler"], once["kalemler"])
        self.assertEqual(Fatura.objects.get(pk=f.pk).ara_toplam, Dc("13200.00"))

    def test_fatura_no_degisirse_tam_yol(self):
        f = self._donemsel("12000")
        once = iz(f)
        tekrar_kaydet(self, f, fatura_no="GR-2026-00005B")
        self.assertNotEqual(iz(f)["kalemler"], once["kalemler"])                           # 180 hesabı adı / dağıtım metni fatura no taşır → tam yol
