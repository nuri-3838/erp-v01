"""ÜS detay TOPLU YÜKLEYİCİ (2026-10-14): (1) eski yol ile BİREBİR aynı çıktı (istasyon emri grupları, eksik malzeme, silinebilirlik), (2) GET hiçbir kayıt oluşturmaz
(varyant dahil; bulunamayan varyant yer tutucu ile metinle gösterilir), (3) sorgu sayısı emir sayısından bağımsız ve 30'un altında."""
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Stok, StokVaryant
from core.services import planlama_varyant as pv
from core.services import us_detay, uretim
from core.services import us_detay_yukle as yukle_servis
from core.services import varyant as vs
from core.tests.test_varyant_planlama import GUN, PlanlamaTaban

D = Decimal
SORGU_UST = 30            # istek başına (oturum + kullanıcı dahil) — "30'un altında"


def grup_ozet(gruplar):
    return [(g["istasyon"].pk, g["toplam"], g["bitti"], g["iptal"], g["acik_adet"], g["fason"], g["uretilebilir"], g["tamam"],
             [(s["ie"].pk, s["ref"], s["planlanan"], s["tamamlanan"], s["acik"], s["uretilebilir"], s["hata"], s["fason_acilabilir"], s["varyant"], s["fazla_yasak"])
              for s in g["satirlar"]]) for g in gruplar]


def eksik_ozet(eksikler):
    return [(e["tur"], e["stok"].pk, e["varyant_etiketi"], e["gerekli"], e["kullanilabilir"], e["eksik"], e.get("stoklu"), e.get("siparis_bekleyen"),
             [x[:2] for x in e.get("siparisler", [])]) for e in eksikler]


class YuklemeTaban(PlanlamaTaban):
    def setUp(self):
        super().setUp()
        pv.anahtar_ayarla(True, self.yon)
        self.emir = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))

    def uret(self, op, miktar, etiket=None, emir=None):
        emir = emir or self.emir
        uretim.istasyon_emri_uret(self.ie(emir, op, etiket), miktar=D(miktar), tarih=GUN, kullanici=self.yon)

    def esdeger(self, emir=None, hatalar=None):
        emir = emir or self.emir
        emir = type(emir).objects.get(pk=emir.pk)
        yeni = yukle_servis.Yukleme(emir)
        yg, ye = yeni.gruplar(hatalar), yeni.eksikler()
        self.assertEqual(grup_ozet(yg), grup_ozet(uretim.istasyon_emri_gruplari(emir, hatalar)))
        self.assertEqual(eksik_ozet(ye), eksik_ozet(uretim.uretim_emri_eksikleri(emir)))
        kayit = us_detay.kayit_sayilari(emir)
        self.assertEqual(us_detay.silinebilir(emir, yeni.ieler, kayit), uretim.uretim_emri_silinebilir(emir))
        self.assertEqual(us_detay.ilerleme(yg, kayit), uretim.uretim_emri_ilerleme(emir))
        return yg, ye


class EsdegerlikTest(YuklemeTaban):
    def test_yeni_acilmis_us(self):
        yg, ye = self.esdeger()
        self.assertGreater(len(yg), 5)
        self.assertTrue(ye)

    def test_kismen_uretilmis_ve_taslak_kayit(self):
        self.uret(self.kesim_on, "4", "YOK")
        self.uret(self.kesim_sag, "6")
        uretim.istasyon_emri_kayit_ac(self.ie(self.emir, self.kesim_sol, "YOK"), hedef="3", tarih=GUN)                 # taslak kayıt: açık kalandan düşer
        yg, _ = self.esdeger()
        sol = [s for g in yg for s in g["satirlar"] if s["ie"].operasyon_id == self.kesim_sol.pk][0]
        self.assertEqual(sol["acik"], D("17"))                                                                           # 20 − 3 taslak

    def test_hata_mesaji_satira_tasinir(self):
        ie = self.ie(self.emir, self.kesim_sag)
        yg, _ = self.esdeger(hatalar={str(ie.pk): "stok yetersiz"})
        self.assertEqual([s["hata"] for g in yg for s in g["satirlar"] if s["ie"].pk == ie.pk], ["stok yetersiz"])

    def test_iptal_ve_tamam_gruplar(self):
        for op, et in ((self.kesim_on, "YOK"), (self.kesim_sag, None), (self.kesim_sol, "YOK")):
            ie = self.ie(self.emir, op, et)
            uretim.istasyon_emri_uret(ie, miktar=ie.planlanan, tarih=GUN, kullanici=self.yon)
        yg, _ = self.esdeger()
        self.assertTrue(any(g["tamam"] for g in yg))

    def test_kapali_plan(self):
        uretim.uretim_emri_iptal(self.emir, self.yon)
        pv.anahtar_ayarla(False, self.yon)
        emir = self.ac((self.mamul_a, "10"))
        self.assertFalse(emir.varyantli_plan)
        self.esdeger(emir)

    def test_iptal_edilmis_us_ve_stok_karsilama_bos(self):
        uretim.uretim_emri_iptal(self.emir, self.yon)
        self.esdeger()                                                                                                   # açık değil → eksik boş, silinebilir False


class YazmamaTest(YuklemeTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)                                                                                # giriş (last_login yazar) ölçümün dışında

    def detay(self):
        return self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))

    def test_get_varyant_olusturma_fonksiyonlarini_cagirmaz(self):
        with mock.patch.object(vs, "stok_varyanti_bul_veya_olustur", side_effect=AssertionError("GET'te varyant açılmaz")), \
             mock.patch.object(vs, "standart_varyant", side_effect=AssertionError("GET'te standart varyant açılmaz")):
            r = self.detay()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "LAZER MARKALAMA")

    def test_get_hicbir_yazma_sorgusu_atmaz(self):
        self.detay()
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(self.detay().status_code, 200)
        yazma = [x["sql"][:90] for x in q.captured_queries if x["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and "django_session" not in x["sql"]]
        self.assertEqual(yazma, [])

    def referanssiz_varyantlar(self):
        kullanimda = set(Stok.objects.exclude(varsayilan_varyant=None).values_list("varsayilan_varyant_id", flat=True))
        for r in StokVaryant._meta.related_objects:
            if r.one_to_many or r.one_to_one:
                kullanimda |= set(r.related_model.objects.filter(**{f"{r.field.name}__isnull": False}).values_list(r.field.name, flat=True))
        return [sv for sv in StokVaryant.objects.all() if sv.pk not in kullanimda]

    def test_bulunamayan_varyant_acilmaz_etiketle_gosterilir(self):
        once = {(e["stok"].pk, e["varyant_etiketi"]) for e in uretim.uretim_emri_eksikleri(self.emir)}
        yetim = self.referanssiz_varyantlar()
        self.assertTrue(yetim, "fixture'da referanssız varyant yok — test anlamsız")
        StokVaryant.objects.filter(pk__in=[sv.pk for sv in yetim]).delete()
        sayi = StokVaryant.objects.count()
        r = self.detay()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(StokVaryant.objects.count(), sayi)                                                              # hiçbir varyant açılmadı
        self.assertEqual({(e["stok"].pk, e["varyant_etiketi"]) for e in r.context["eksikler"]}, once)                   # etiketler aynı (metinle)
        self.assertEqual(self.detay().status_code, 200)

    def test_yer_tutucu_negatif_pk_ve_etiket(self):
        yk = yukle_servis.Yukleme(self.emir)
        sayi = StokVaryant.objects.count()
        sv = yk.cozucu.ob.sv_bul(self.mamul_a, {"MARKALAMA": self.mop, "KILAVUZ": self.mop, "AFIS": self.mop, "YAN_ETIKET": self.mop})
        self.assertLess(sv.pk, 0)
        self.assertEqual(vs.varyant_kisa(sv), "MR. MOP/MR. MOP/MR. MOP/MR. MOP")
        self.assertIs(sv, yk.cozucu.ob.sv_bul(self.mamul_a, {"MARKALAMA": self.mop, "KILAVUZ": self.mop, "AFIS": self.mop, "YAN_ETIKET": self.mop}))
        self.assertEqual(StokVaryant.objects.count(), sayi)
        with self.assertRaises(vs.VaryantHatasi):
            yk.cozucu.ob.sv_bul(self.mamul_a, {"MARKALAMA": 999999})


class SorguKilidiTest(YuklemeTaban):
    def sorgu(self, emir):
        self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))                                              # ısınma
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertEqual(r.status_code, 200)
        return len(q)

    def test_sorgu_sayisi_emir_sayisindan_bagimsiz_ve_30un_altinda(self):
        self.client.force_login(self.yon)
        kucuk = self.ac((self.mamul_a, "10"))
        buyuk = self.emir                                                                                                # 2 kalem (A + ÇÇ): çok daha fazla istasyon emri
        for emir in (kucuk, buyuk):
            self.uret(self.kesim_on, "2", "YOK", emir)                                                                   # ikisinde de 1 onaylı kayıt
        ie_kucuk = kucuk.istasyon_emirleri.filter(silindi=False).count()
        ie_buyuk = buyuk.istasyon_emirleri.filter(silindi=False).count()
        self.assertGreater(ie_buyuk, ie_kucuk)
        n_kucuk, n_buyuk = self.sorgu(kucuk), self.sorgu(buyuk)
        self.assertEqual(n_kucuk, n_buyuk, f"küçük ({ie_kucuk} emir) {n_kucuk} sorgu, büyük ({ie_buyuk} emir) {n_buyuk} sorgu")
        self.assertLess(n_buyuk, SORGU_UST, f"{n_buyuk} sorgu")

    def test_kapali_plan_sorgu_sayisi_emir_sayisindan_bagimsiz(self):
        """Eski (kart bazlı) planlı ÜS'ler (canlıda ÜS-2026-0002: 63 emir, eskiden 380 sorgu) da sabit sorguyla açılır."""
        uretim.uretim_emri_iptal(self.emir, self.yon)
        pv.anahtar_ayarla(False, self.yon)
        self.client.force_login(self.yon)
        kucuk = self.ac((self.mamul_a, "10"))
        buyuk = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))
        self.assertFalse(buyuk.varyantli_plan)
        for emir in (kucuk, buyuk):
            self.uret(self.kesim_on, "2", None, emir)
        self.assertGreater(buyuk.istasyon_emirleri.filter(silindi=False).count(), kucuk.istasyon_emirleri.filter(silindi=False).count())
        n_kucuk, n_buyuk = self.sorgu(kucuk), self.sorgu(buyuk)
        self.assertEqual(n_kucuk, n_buyuk)
        self.assertLess(n_buyuk, SORGU_UST, f"{n_buyuk} sorgu")

    def test_kayit_sayisi_artinca_sorgu_artmaz(self):
        self.client.force_login(self.yon)
        self.uret(self.kesim_on, "1", "YOK")
        a = self.sorgu(self.emir)
        for _ in range(4):
            self.uret(self.kesim_on, "1", "YOK")
        self.assertEqual(self.sorgu(self.emir), a)

    def test_ekstra_varyant_ve_ayirma_satiri_sorgu_artirmaz(self):
        self.client.force_login(self.yon)
        a = self.sorgu(self.emir)
        self.giris(self.ayak_on, "5", varyant=self.ayak_semta)
        self.giris(self.bukulmus, "5")
        self.assertLessEqual(self.sorgu(self.emir), a + 1)                                                               # yeni ayırma/hareket tek-sorgu haritalarında kalır
