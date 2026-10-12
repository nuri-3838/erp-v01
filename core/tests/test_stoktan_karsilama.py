"""ÜS açılış/revizede "stoktan karşıla" sorusu (2026-10-12): bitmiş ürün (152) serbest stoğu artık kendiliğinden düşülmez — kalem başına ön izleme
(hedef / serbest / stoktan / üretilecek) ve seçim (stoktan karşıla = serbest kadar, tamamını üret, elle 0..min(hedef, serbest)); seçim onaylanmadan
kayıt yok; ara mamul (151) ve hammadde bugünkü gibi otomatik; özel varyantlı kalemde seçenek yok (tamamı üretilir); seçilen miktar planlama motoruna
tavan olarak geçer (ayırma yalnız o kadar). Dört ekran: ÜS aç, siparişten ÜS aç, ÜS revize, sipariş revize."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import StokHareket, UretimEmri
from core.services import planlama_varyant as pv
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.services.uretim import UretimHatasi, operasyon_olustur, stoktan_karsilama_onizleme, stoktan_secimleri_coz, uretim_emri_olustur
from core.tests import test_uretim_siparisi_kapanis as kt
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_uretim import _istasyon, _stok
from core.tests.test_varyant_planlama import GUN as VGUN
from core.tests.test_varyant_planlama import PlanlamaTaban

D = Decimal


class StoktanTaban(Taban):
    """Zincir: profil → kesim (LAZER) → kesilmiş → büküm (BUKUM) → bükülmüş (151 gibi, kod IK-) → paketleme (PAK) → 152-10-0001 mamul."""
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pak = _istasyon("PAK")
        cls.mamul = _stok(cls.kat, cls.birim, kod="152-10-0001", ad="A 3+1 MAMUL", satis=True)
        cls.pak_op = operasyon_olustur(istasyon_id=cls.pak.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"), satirlar=[(cls.bukulmus, D("1"))])
        cls.yon = User.objects.create_superuser("skyon", password="x")

    def setUp(self):
        super().setUp()
        hareket_ekle(stok_id=self.mamul.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("4"))   # serbest bitmiş: 4

    def ac(self, hedef="10", stoktan=None):
        return uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": hedef}], depo_id=self.depo.pk, tarih=date(2026, 1, 10),
                                   kullanici=self.yon, stoktan=stoktan)

    @staticmethod
    def planlanan(emir, op):
        ie = emir.istasyon_emirleri.filter(silindi=False, operasyon=op).exclude(durum="IPTAL").first()
        return ie.planlanan if ie else D("0")


class ServisTest(StoktanTaban):
    def test_onizleme_satirlari(self):
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "10"}, {"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": "2"}])
        m, b = rows
        self.assertEqual((m["urun"].pk, m["hedef"], m["serbest"], m["soru"], m["ozel"], m["ust_sinir"], m["varsayilan"], m["secim_mod"], m["anahtar"]),
                         (self.mamul.pk, D("10"), D("4"), True, False, D("4"), D("4"), "stok", (self.mamul.pk, None)))
        self.assertEqual((b["soru"], b["ozel"], b["ust_sinir"], b["mamul"]), (False, False, D("0"), False))        # ara mamul kökü: soru yok
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "3"}])
        self.assertEqual((rows[0]["ust_sinir"], rows[0]["serbest"]), (D("3"), D("4")))                              # hedef < serbest
        self.assertTrue(uretim.stoktan_sorusu_var(rows))
        self.assertFalse(uretim.stoktan_sorusu_var(stoktan_karsilama_onizleme([{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": "2"}])))

    def test_secim_tavani_ayirma_ve_plan(self):
        emir = self.ac()                                                                                            # stoktan=None: bugünkü otomatik
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, sa.ayrilan_miktar(emir, self.mamul), self.planlanan(emir, self.pak_op)), (D("4"), D("4"), D("6")))
        uretim.uretim_emri_iptal(emir)
        emir = self.ac(stoktan={(self.mamul.pk, None): D("1")})                                                     # elle 1
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, sa.ayrilan_miktar(emir, self.mamul), self.planlanan(emir, self.pak_op)), (D("1"), D("1"), D("9")))
        uretim.uretim_emri_iptal(emir)
        emir = self.ac(stoktan={(self.mamul.pk, None): D("0")})                                                     # tamamını üret
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, sa.ayrilan_miktar(emir, self.mamul), self.planlanan(emir, self.pak_op)), (D("0"), D("0"), D("10")))
        uretim.uretim_emri_iptal(emir)
        emir = self.ac(stoktan={(self.mamul.pk, None): D("99")})                                                    # tavan serbesti aşamaz: yine 4
        self.assertEqual(emir.kalemler.get().eldeki_ayrilan, D("4"))

    def test_ara_mamul_stogu_sorudan_etkilenmez(self):
        hareket_ekle(stok_id=self.bukulmus.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"))
        emir = self.ac(stoktan={(self.mamul.pk, None): D("0")})
        self.assertEqual((self.planlanan(emir, self.pak_op), sa.ayrilan_miktar(emir, self.bukulmus), self.planlanan(emir, self.bukum_op)),
                         (D("10"), D("3"), D("7")))                                                                  # 151 bükülmüş 3 otomatik düşüldü

    def test_secim_cozumleme_ve_hatalar(self):
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "10"}, {"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": "2"}])
        k = (self.mamul.pk, None)
        self.assertEqual(stoktan_secimleri_coz(rows, [("stok", ""), ("x", "")]), {k: D("4")})                     # ara mamul satırı sözlükte yok
        self.assertEqual(stoktan_secimleri_coz(rows, [("uret", ""), ("", "")]), {k: D("0")})
        self.assertEqual(stoktan_secimleri_coz(rows, [("elle", "2,5"), ("", "")]), {k: D("2.5")})
        with self.assertRaisesMessage(UretimHatasi, "0 ile 4 arasında"):
            stoktan_secimleri_coz(rows, [("elle", "5"), ("", "")])
        with self.assertRaisesMessage(UretimHatasi, "seçimi yapılmadı"):
            stoktan_secimleri_coz(rows, [("", ""), ("", "")])
        with self.assertRaisesMessage(UretimHatasi, "kalem sayısıyla uyuşmuyor"):
            stoktan_secimleri_coz(rows, [("stok", "")])
        # aynı ürün iki kalemde (siparişten): toplam serbesti aşamaz
        sk = [SimpleNamespace(varyant=None), SimpleNamespace(varyant=None)]
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "3"}, {"hedef_urun_id": self.mamul.pk, "hedef_miktar": "3"}],
                                          siparis_kalemleri=sk)
        self.assertEqual([r["ust_sinir"] for r in rows], [D("3"), D("3")])
        self.assertEqual(stoktan_secimleri_coz(rows, [("stok", ""), ("elle", "1")]), {k: D("4")})
        with self.assertRaisesMessage(UretimHatasi, "serbest stoğu (4) aşıyor"):
            stoktan_secimleri_coz(rows, [("stok", ""), ("stok", "")])

    def test_revize_kendi_ayirmasi_serbest_sayilir(self):
        emir = self.ac(stoktan={(self.mamul.pk, None): D("1")})
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "12"}], haric_emir=emir)
        self.assertEqual((rows[0]["serbest"], rows[0]["ust_sinir"]), (D("4"), D("4")))                              # kendi 1'i de serbest görünür
        uretim.uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "12"}], kullanici=self.yon,
                                  stoktan={(self.mamul.pk, None): D("3")})
        self.assertEqual((emir.kalemler.get(silindi=False).eldeki_ayrilan, sa.ayrilan_miktar(emir, self.mamul), self.planlanan(emir, self.pak_op)),
                         (D("3"), D("3"), D("9")))


class EkranTest(StoktanTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)

    def veri(self, hedef="10", **ek):
        v = {"depo": self.depo.pk, "tarih": "2026-01-10", "aciklama": "", "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "1",
             "satir-MAX_NUM_FORMS": "1000", "satir-0-hedef_urun": self.mamul.pk, "satir-0-hedef_miktar": hedef}
        v.update(ek)
        return v

    def test_us_ac_onizleme_ve_onay(self):
        url = reverse("core:uretim_emri_ekle")
        r = self.client.post(url, self.veri())                                                                      # 1. adım: ön izleme, kayıt yok
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "stoktan mı karşılansın")
        self.assertContains(r, "Stoktan karşıla (4)")
        self.assertContains(r, "Tamamını üret")
        self.assertContains(r, "Hepsini stoktan karşıla")
        self.assertContains(r, "Hepsini üret")
        self.assertContains(r, "Seçimi Onayla ve Siparişi Aç")
        self.assertContains(r, 'name="secim_onay" value="1"')
        self.assertEqual(UretimEmri.objects.count(), 1)                                                   # yalnız fixture ÜS
        r = self.client.post(url, self.veri(secim_onay="1", stoktan_mod_0="elle", stoktan_miktar_0="9"))            # üst sınırı aşan elle miktar
        self.assertContains(r, "0 ile 4 arasında")
        self.assertContains(r, 'value="9"')                                                                           # girilen değer korunur
        self.assertEqual(UretimEmri.objects.count(), 1)                                                   # yalnız fixture ÜS
        r = self.client.post(url, self.veri(secim_onay="1", stoktan_mod_0="elle", stoktan_miktar_0="2"), follow=True)
        emir = UretimEmri.objects.latest("pk")
        self.assertContains(r, emir.no)
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, self.planlanan(emir, self.pak_op)), (D("2"), D("8")))
        uretim.uretim_emri_iptal(emir)
        self.client.post(url, self.veri(secim_onay="1", stoktan_mod_0="uret"))
        emir = UretimEmri.objects.latest("pk")
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, self.planlanan(emir, self.pak_op)), (D("0"), D("10")))
        uretim.uretim_emri_iptal(emir)
        self.client.post(url, self.veri(secim_onay="1", stoktan_mod_0="stok"))
        emir = UretimEmri.objects.latest("pk")
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, self.planlanan(emir, self.pak_op)), (D("4"), D("6")))
        # bitmiş ürün olmayan kök (ara mamul): soru yok, tek adımda açılır
        uretim.uretim_emri_iptal(emir)
        r = self.client.post(url, self.veri(**{"satir-0-hedef_urun": self.bukulmus.pk, "satir-0-hedef_miktar": "2"}), follow=True)
        self.assertEqual(UretimEmri.objects.latest("pk").kalemler.get().hedef_urun_id, self.bukulmus.pk)
        self.assertNotContains(r, "stoktan mı karşılansın")

    def test_us_revize_onizleme_ve_onay(self):
        emir = self.ac(stoktan={(self.mamul.pk, None): D("1")})
        url = reverse("core:uretim_emri_revize", args=[emir.pk])
        v = self.veri("12")
        v["satir-INITIAL_FORMS"] = "1"
        r = self.client.post(url, v)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Seçimi Onayla ve Revize Et")
        emir.refresh_from_db()
        self.assertEqual(emir.revizyon_no, 0)
        v.update(secim_onay="1", stoktan_mod_0="elle", stoktan_miktar_0="3")
        r = self.client.post(url, v, follow=True)
        emir.refresh_from_db()
        self.assertEqual((emir.revizyon_no, emir.kalemler.get(silindi=False).eldeki_ayrilan, self.planlanan(emir, self.pak_op)), (1, D("3"), D("9")))


class SiparisEkranTest(kt.KapanisTaban):
    """Siparişten ÜS açma ve sipariş revize ekranlarında soru (ürün kodu 152- yapıldı; serbest bitmiş stok 2)."""
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stok.kod = "152-10-0077"
        cls.stok.save(update_fields=["kod"])

    def setUp(self):
        super().setUp()
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("2"), giris_tutar_try=D("100"))

    def test_siparisten_ac_ve_siparis_revize(self):
        sip = self.siparis("3")
        kalem = sip.kalemler.get(silindi=False)
        url = reverse("core:siparis_uretim_emrine_cevir", args=[sip.pk])
        v = {"depo": self.depo.pk, "tarih": "2026-06-20", "aciklama": "", "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0",
             "form-MAX_NUM_FORMS": "1000", "form-0-kalem_id": kalem.pk, "form-0-hedef_miktar": "3"}
        r = self.client.post(url, v)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Stoktan karşıla (2)")
        self.assertContains(r, "Seçimi Onayla ve Üretim Emri Aç")
        self.assertFalse(UretimEmri.objects.exists())
        v.update(secim_onay="1", stoktan_mod_0="uret")
        r = self.client.post(url, v, follow=True)
        emir = UretimEmri.objects.get()
        self.assertEqual((emir.kalemler.get().eldeki_ayrilan, emir.istasyon_emirleri.get().planlanan, sa.ayrilan_miktar(emir, self.stok)), (D("0"), D("3"), D("0")))
        # sipariş revize: 3 → 5, stoktan 2
        url = reverse("core:siparis_revize", args=[sip.pk])
        v = {"tarih": "2026-06-29", "aciklama": "artış", "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
             "form-0-stok": self.stok.pk, "form-0-miktar": "5", "form-0-birim_fiyat": "50"}
        r = self.client.post(url, v)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Stoktan karşıla (2)")
        self.assertContains(r, "Seçimi Onayla ve Revize Et")
        emir.refresh_from_db()
        self.assertEqual(emir.revizyon_no, 0)
        v.update(secim_onay="1", stoktan_mod_0="stok")
        r = self.client.post(url, v, follow=True)
        emir.refresh_from_db()
        self.assertEqual((emir.revizyon_no, emir.kalemler.get(silindi=False).eldeki_ayrilan, emir.istasyon_emirleri.get().planlanan), (1, D("2"), D("3")))
        self.assertContains(r, "revize edildi")


class VaryantliTest(PlanlamaTaban):
    def test_ozel_varyant_secenek_yok_standart_sorulur(self):
        pv.anahtar_ayarla(True, self.yon)
        self.giris(self.mamul_a, "3", varyant=self.std_a)                                                          # standart A 4+1 stoğu 3
        rows = stoktan_karsilama_onizleme([{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "10"},
                                           {"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "5", "varyant_id": self.ozel_a.pk}],
                                          siparis_kalemleri=[SimpleNamespace(varyant=None), SimpleNamespace(varyant=self.ozel_a)], kullanici=self.yon)
        std, ozel = rows
        self.assertEqual((std["soru"], std["ozel"], std["serbest"], std["ust_sinir"], std["anahtar"]), (True, False, D("3"), D("3"), (self.mamul_a.pk, self.std_a.pk)))
        self.assertEqual((ozel["soru"], ozel["ozel"], ozel["ust_sinir"], ozel["anahtar"]), (False, True, D("0"), (self.mamul_a.pk, self.ozel_a.pk)))
        stoktan = stoktan_secimleri_coz(rows, [("elle", "2"), ("", "")])
        self.assertEqual(stoktan, {(self.mamul_a.pk, self.std_a.pk): D("2"), (self.mamul_a.pk, self.ozel_a.pk): D("0")})
        emir = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "10"}], depo_id=self.depo.pk, tarih=VGUN,
                                          kullanici=self.yon, stoktan={(self.mamul_a.pk, self.std_a.pk): D("2")})
        k = emir.kalemler.get()
        self.assertEqual((k.varyant_id, k.eldeki_ayrilan, sa.ayrilan_miktar(emir, self.mamul_a, varyant=self.std_a.pk), self.ie(emir, self.pak_a).planlanan),
                         (self.std_a.pk, D("2"), D("2"), D("8")))
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:uretim_emri_ekle"), {
            "depo": self.depo.pk, "tarih": "2026-06-10", "aciklama": "", "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "1",
            "satir-MAX_NUM_FORMS": "1000", "satir-0-hedef_urun": self.mamul_a.pk, "satir-0-hedef_miktar": "4"})
        self.assertContains(r, "Stoktan karşıla (1)")                                                                 # 3 − 2 (ilk ÜS ayırdı) = 1 serbest
