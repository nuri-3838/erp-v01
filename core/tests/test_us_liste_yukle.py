"""ÜS LİSTESİ toplu yükleyici (2026-10-14): (1) eski yolla BİREBİR aynı rozet sayıları / Mamul % / İş % / Sevk bekliyor / ilerleme (varyantlı + kart bazlı planlı ÜS'ler,
satınalma ve fason siparişi, sevk), (2) GET hiçbir kayıt yazmaz (varyant dahil), (3) sorgu sayısı ÜS sayısından bağımsız ve 30'un altında."""
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import UretimEmri, UretimEmriKalemi
from core.services import fason_siparis as fss
from core.services import planlama_varyant as pv
from core.services import us_detay, uretim
from core.services import us_detay_yukle as yukle_servis
from core.services import us_liste_yukle as liste_servis
from core.services import urun_agaci as urun_agaci_servis
from core.services import varyant as vs
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_fason_siparis import FSBase
from core.tests.test_us_detay_yukle import YuklemeTaban, eksik_ozet
from core.tests.test_uretim_siparisi_kapanis import GUN, KapanisTaban

D = Decimal
SORGU_UST = 30


def yukle(emirler_qs):
    return list(emirler_qs.select_related("depo", "kaynak_siparis__cari", "kaynak_siparis__aday_musteri")
                .prefetch_related(Prefetch("kalemler", queryset=UretimEmriKalemi.objects.filter(silindi=False).select_related("hedef_urun", *yukle_servis.derin("varyant")))))


class EsdegerlikMixin:
    def kontrol(self, ara=None):
        """Yükleyici düzeyi: her açık ÜS için eksik listesi eski yolla aynı; görünüm düzeyi: liste satırları eski hesaplarla aynı."""
        emirler = yukle(UretimEmri.objects.filter(silindi=False))
        toplu = liste_servis.ListeYukleme(emirler)
        graf = urun_agaci_servis.graf_yukle()
        sayac = 0
        for e in emirler:
            if e.durum != UretimEmri.Durum.ACIK:
                continue
            yeni = uretim.uretim_emri_eksikleri(e, graf=graf, yukle=toplu.icin(e))
            eski = uretim.uretim_emri_eksikleri(e, graf=graf)
            self.assertEqual(eksik_ozet(yeni), eksik_ozet(eski), e.no)
            sayac += 1
        self.assertTrue(sayac)
        r = self.client.get(reverse("core:uretim_emirleri"), {"durum": "TUMU"})
        self.assertEqual(r.status_code, 200)
        for s in r.context["emirler"]:
            e = s["e"]
            eksik = uretim.uretim_emri_eksikleri(e, graf=graf)
            self.assertEqual((s["eksik_sayisi"], s["fason_bekleyen"], s["fason_emri_gerekli"], s["satinalma_bekleyen"]),
                             (sum(1 for x in eksik if x["tur"] in ("SATINALMA", "STOKLU")), sum(1 for x in eksik if x["tur"] == "FASON_SIPARIS"),
                              sum(1 for x in eksik if x["tur"] == "FASON_EMRI_GEREKLI"), sum(1 for x in eksik if x["tur"] == "SATINALMA_SIPARIS")), e.no)
            ilerleme = uretim.uretim_emri_ilerleme(e)
            self.assertEqual(s["ilerleme"], ilerleme, e.no)
            self.assertEqual(s["sevk_bekliyor"], uretim.sevk_bekliyor_mu(e, ilerleme), e.no)
            if e.durum != UretimEmri.Durum.IPTAL:
                self.assertEqual(s["yuzde"], uretim.uretim_emri_yuzde(e), e.no)
                self.assertEqual(s["is_yuzde"], us_detay.is_yuzdesi(yukle_servis.Yukleme(e).gruplar()), e.no)
        return r


class VaryantliVeKapaliPlanTest(EsdegerlikMixin, YuklemeTaban):
    """YuklemeTaban: varyantlı plan AÇIK, ÜS self.emir (A+ÇÇ). Üstüne ikinci varyantlı ÜS, kısmen üretim, ayırmalar ve kart bazlı planlı (eski) ÜS."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)
        self.giris(self.ayak_on, "3", varyant=self.ayak_semta)
        self.giris(self.bukulmus, "2")
        self.emir2 = self.ac((self.mamul_a, "4"))
        self.uret(self.kesim_on, "4", "YOK")
        self.uret(self.kesim_sag, "6")
        pv.anahtar_ayarla(False, self.yon)
        self.emir3 = self.ac((self.mamul_a, "3"))                                                                           # kart bazlı (eski) planlı
        self.uret(self.kesim_on, "1", None, self.emir3)

    def test_rozet_sayilari_yuzdeler_ve_durum_eski_yolla_ayni(self):
        r = self.kontrol()
        self.assertEqual({s["e"].varyantli_plan for s in r.context["emirler"]}, {True, False})                             # iki plan türü bir listede
        self.assertGreater(sum(s["eksik_sayisi"] for s in r.context["emirler"]), 0)

    def test_iptal_ve_kapali_us_listede_sorunsuz(self):
        uretim.uretim_emri_iptal(self.emir2, self.yon)
        self.kontrol()

    def test_get_hicbir_yazma_yapmaz_varyant_acilmaz(self):
        self.client.get(reverse("core:uretim_emirleri"))
        with mock.patch.object(vs, "stok_varyanti_bul_veya_olustur", side_effect=AssertionError("GET'te varyant açılmaz")), \
             mock.patch.object(vs, "standart_varyant", side_effect=AssertionError("GET'te standart varyant açılmaz")):
            with CaptureQueriesContext(connection) as q:
                r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertEqual(r.status_code, 200)
        yazma = [x["sql"][:90] for x in q.captured_queries if x["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and "django_session" not in x["sql"]]
        self.assertEqual(yazma, [])

    def sorgu(self):
        self.client.get(reverse("core:uretim_emirleri"))                                                                    # ısınma
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertEqual(r.status_code, 200)
        return len(q), len(r.context["emirler"])

    def test_sorgu_sayisi_us_sayisindan_bagimsiz_ve_30un_altinda(self):
        n1, us1 = self.sorgu()
        self.assertLess(n1, SORGU_UST, f"{n1} sorgu")
        pv.anahtar_ayarla(True, self.yon)
        for _ in range(3):
            self.ac((self.mamul_a, "1"))
        pv.anahtar_ayarla(False, self.yon)
        for _ in range(3):
            self.ac((self.mamul_a, "1"))
        n2, us2 = self.sorgu()
        self.assertGreater(us2, us1 + 4)
        self.assertEqual(n1, n2, f"{us1} ÜS: {n1} sorgu, {us2} ÜS: {n2} sorgu")
        self.assertLess(n2, SORGU_UST)

    def test_sevk_bekleyen_ve_iptal_listelerde_de_sabit_sorgu(self):
        self.sorgu()
        with CaptureQueriesContext(connection) as q:
            self.client.get(reverse("core:uretim_emirleri"), {"durum": "TUMU"})
        self.assertLess(len(q), SORGU_UST)


class SatinalmaVeSevkTest(EsdegerlikMixin, KapanisTaban):
    """Siparişli ÜS: hammadde kıt (eksik), açık alış siparişi (satınalmadan bekleniyor), sevk faturası (kapanan ÜS) ve üretimi biten (Sevk bekliyor) ÜS."""

    def setUp(self):
        super().setUp()
        from core.models import StokHareket
        from core.services.hareket import hareket_ekle
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=GUN, tur=StokHareket.Tur.CIKIS, miktar=D("90"))      # ham 10'a düştü
        self.sip_a = self.siparis("12")                                                                                       # 12 gerekir → 2 eksik
        self.emir_a = self.emir_ac(self.sip_a)
        alis = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.musteri.pk, tarih=GUN,
                                      satirlar=[{"stok_id": self.ham.pk, "miktar": "10", "birim_fiyat": "10"}], kullanici=self.yon)
        teklif_siparis_onayla(alis, kullanici=self.yon)                                                                       # 10 adet bekleniyor
        self.emir_d = self.emir_ac(self.siparis("15"))                                                                        # eksik 15 > sipariş 10 → kısmen karşılanır
        self.sip_b = self.siparis("3")
        self.emir_b = self.emir_ac(self.sip_b)
        self.uret(self.emir_b)                                                                                                # üretim bitti → Sevk bekliyor
        self.sip_c = self.siparis("2")
        self.emir_c = self.emir_ac(self.sip_c)
        self.uret(self.emir_c)
        self.fatura_kes(self.sip_c, miktar="2")                                                                               # sevk → ÜS kapanır

    def test_satinalma_sevk_ve_kapanis_eski_yolla_ayni(self):
        r = self.kontrol()
        satirlar = {s["e"].pk: s for s in r.context["emirler"]}
        self.assertEqual((satirlar[self.emir_a.pk]["satinalma_bekleyen"], satirlar[self.emir_a.pk]["eksik_sayisi"]), (1, 0))   # A'nın eksiği (B/C üretimi ham tüketti) siparişten tamamen bekleniyor
        self.assertEqual((satirlar[self.emir_d.pk]["satinalma_bekleyen"], satirlar[self.emir_d.pk]["eksik_sayisi"]), (0, 1))   # kısmi: eksik satırı kalır
        self.assertTrue(satirlar[self.emir_b.pk]["sevk_bekliyor"])
        self.emir_c.refresh_from_db()
        self.assertEqual(self.emir_c.durum, UretimEmri.Durum.KAPALI)
        self.assertFalse(satirlar[self.emir_c.pk]["sevk_bekliyor"])

    def test_sevk_edilen_harita_ve_sorgu_kilidi(self):
        toplu = liste_servis.ListeYukleme(yukle(UretimEmri.objects.filter(silindi=False)))
        for e in yukle(UretimEmri.objects.filter(silindi=False, durum=UretimEmri.Durum.ACIK)):                              # yükleyici yalnız açık ÜS'leri kapsar
            self.assertEqual(toplu.sevk_haritasi(e), uretim.sevk_edilen_haritasi(e), e.no)
        self.client.get(reverse("core:uretim_emirleri"))
        with CaptureQueriesContext(connection) as q:
            self.client.get(reverse("core:uretim_emirleri"))
        self.assertLess(len(q), SORGU_UST, f"{len(q)} sorgu")


class FasonSiparisiTest(EsdegerlikMixin, FSBase):
    """Açık fason siparişi ve ÜS rezervleri: açık kalan haritası ve ÜS rezervleri (kendi rezervi çıkarılarak) eski okumalarla aynı."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)
        self.s1 = self.fs(adet="10", termin=GUN)
        self.u1 = self.us("6")
        self.u2 = self.us("5")

    def test_fason_harita_ve_rezervler_eski_okumalarla_ayni(self):
        toplu = liste_servis.ListeYukleme(yukle(UretimEmri.objects.filter(silindi=False)))
        for e in (self.u1, self.u2):
            e = UretimEmri.objects.get(pk=e.pk)
            self.assertEqual(toplu.fs_harita(e), fss.acik_kalan_haritasi(haric_emir=e), e.no)
            self.assertEqual([r.pk for r in toplu.fs_rezervleri(e)], [r.pk for r in fss.emir_rezervleri(e)], e.no)
        self.assertEqual(toplu.fs_harita(None), fss.acik_kalan_haritasi())
        self.assertTrue(toplu.fs_satirlar)

    def test_fasondan_bekleniyor_rozeti_eski_yolla_ayni(self):
        r = self.kontrol()
        self.assertGreater(sum(s["fason_bekleyen"] for s in r.context["emirler"]), 0)
