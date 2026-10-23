"""ÜS "Eksik malzeme" yalnız AÇIK kalan üretime göre (2026-10-13): biten istasyon emirlerinin ürettiği kök mamul ÜS'ye ayrılı olduğundan kalan ihtiyaç
= hedef − sevk − ayrılan; kısmen bitmiş ÜS'de gerekli = açık kalan × reçete, tümü bitmişse eksik yok (liste rozeti de çıkmaz); ÜS sevke kadar AÇIK kalır."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import StokHareket, UretimEmri
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.services.uretim import istasyon_emri_kayit_ac, operasyon_kaydi_onayla, siparisten_uretim_emri_olustur
from core.tests.test_stoktan_karsilama import StoktanTaban
from core.tests.test_uretim_siparisi_kapanis import KapanisTaban

D = Decimal
GUN = date(2026, 1, 12)


class KalanEksikTest(StoktanTaban):
    """profil → kesim (1 profil = 2 kesilmiş) → büküm → paketleme → 152 mamul. Profil stoğu 3: 10 mamul için 5 gerekir → başlangıçta 2 eksik."""

    def setUp(self):
        super().setUp()
        uretim.uretim_emri_iptal(self.emir)                                                              # Taban'ın 10 bükülmüşlük ÜS'si
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.CIKIS, miktar=D("97"))
        self.us = self.ac("10", stoktan={(self.mamul.pk, None): D("0")})

    def ie(self, op):
        return self.us.istasyon_emirleri.get(operasyon=op)

    def uret(self, kesim, buk, pak):
        for op, m in ((self.kesim_op, kesim), (self.bukum_op, buk), (self.pak_op, pak)):
            if m:
                uretim.istasyon_emri_uret(self.ie(op), miktar=D(m), tarih=GUN, kullanici=self.yon)

    def eksik(self):
        return [(e["stok"].kod, e["gerekli"], e["kullanilabilir"], e["eksik"]) for e in uretim.uretim_emri_eksikleri(self.us) if e["tur"] == "SATINALMA"]

    def test_hic_uretilmemis_tam_ihtiyac(self):
        self.assertEqual(self.eksik(), [("IK-PROFIL", D("5"), D("3"), D("2"))])

    def test_kismen_bitmis_yalniz_acik_kalan_sayilir(self):
        self.uret("6", "6", "6")                                                                         # 6 mamul üretildi → ÜS'ye ayrıldı; profil 3'ü bitti
        self.assertEqual(sa.ayrilan_miktar(self.us, self.mamul), D("6"))
        self.assertEqual(self.eksik(), [("IK-PROFIL", D("2"), D("0"), D("2"))])                          # açık kalan 4 mamul → 4 kesilmiş → 2 profil

    def test_kismi_us_liste_rozeti_ve_detay(self):
        self.uret("6", "6", "6")
        self.client.force_login(self.yon)
        self.assertContains(self.client.get(reverse("core:uretim_emirleri")), "Eksik malzeme: 1")
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk])), "IK-PROFIL")


class SiparisliUsTest(KapanisTaban):
    """Siparişli ÜS sevk edilene kadar AÇIK kalır; hammadde stoğu 2, sipariş 3 → başlangıçta 1 eksik."""

    def setUp(self):
        super().setUp()
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.CIKIS, miktar=D("98"))
        self.emir = self.emir_ac(self.siparis("3"))

    def eksik(self):
        return [(e["stok"].kod, e["gerekli"], e["kullanilabilir"], e["eksik"]) for e in uretim.uretim_emri_eksikleri(self.emir) if e["tur"] == "SATINALMA"]

    def uret(self, miktar):
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.emir.istasyon_emirleri.get(), hedef=str(miktar), tarih=GUN))

    def test_kismen_bitmis_acik_kalan_kadar(self):
        self.assertEqual(self.eksik(), [("Sf-HAM", D("3"), D("2"), D("1"))])
        self.uret(1)
        self.assertEqual(self.eksik(), [("Sf-HAM", D("2"), D("1"), D("1"))])                             # açık kalan 2 × reçete 1

    def test_tamami_bitmis_eksik_yok_us_acik_liste_rozeti_yok(self):
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 3), tur=StokHareket.Tur.GIRIS, miktar=D("1"), giris_tutar_try=D("10"))
        self.uret(3)                                                                                    # 3 ham tüketildi, stokta 0 kaldı
        self.emir.refresh_from_db()
        self.assertEqual(self.emir.durum, UretimEmri.Durum.ACIK)                                         # sevk edilene kadar açık
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.stok), D("3"))
        self.assertEqual(uretim.uretim_emri_eksikleri(self.emir), [])
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertNotContains(r, "Eksik malzeme:")
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])), "Eksik malzeme yok")



class ListeRozetTest(SiparisliUsTest):
    """Liste rozeti: "Eksik malzeme: N" yalnız gerçek eksik (SATINALMA/STOKLU); fason siparişinden beklenenler ayrı gri "Fasondan bekleniyor: N"."""

    def liste(self, eksikler):
        from unittest import mock
        with mock.patch("core.services.uretim.uretim_emri_eksikleri", return_value=eksikler):
            return self.client.get(reverse("core:uretim_emirleri")).content.decode()

    def test_gercek_eksik_ve_fason_bekleyen_ayri(self):
        h = self.liste([{"tur": "SATINALMA"}, {"tur": "STOKLU"}] + [{"tur": "FASON_SIPARIS"}] * 5)
        self.assertIn("Eksik malzeme: 2<", h)
        self.assertIn("Fasondan bekleniyor: 5<", h)

    def test_yalniz_fason_bekleyen_eksik_rozeti_yok(self):
        h = self.liste([{"tur": "FASON_SIPARIS"}] * 3)
        self.assertNotIn("Eksik malzeme:", h)
        self.assertIn("Fasondan bekleniyor: 3<", h)

    def test_hicbiri_yoksa_rozet_yok(self):
        h = self.liste([])
        self.assertNotIn("Eksik malzeme:", h)
        self.assertNotIn("Fasondan bekleniyor:", h)

    def test_gercek_eksik_gercek_veri(self):
        h = self.client.get(reverse("core:uretim_emirleri")).content.decode()               # hammadde 2 < 3 → gerçek eksik 1, fason yok
        self.assertIn("Eksik malzeme: 1<", h)
        self.assertNotIn("Fasondan bekleniyor:", h)

    def test_fason_emri_gerekli_turuncu_rozet_liste_ve_detay(self):
        from unittest import mock
        eks = [{"tur": "FASON_EMRI_GEREKLI", "stok": self.stok, "varyant_etiketi": "", "gerekli": D("4"), "kullanilabilir": D("0"), "eksik": D("4")}] * 2
        with mock.patch("core.services.uretim.uretim_emri_eksik_hesabi", return_value=(eks, [])):
            liste = self.client.get(reverse("core:uretim_emirleri")).content.decode()
            detay = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])).content.decode()
        self.assertIn("Fason gerekli: 2<", liste)
        self.assertNotIn("Eksik malzeme:", liste)                                                       # gerçek eksik değil
        self.assertIn("ue-fason-gerekli", liste)
        self.assertIn("rozet-turuncu", detay)
        self.assertIn("Fason gerekli: 2<", detay)

    def test_fason_emri_gerekli_yoksa_rozet_yok(self):
        liste = self.client.get(reverse("core:uretim_emirleri")).content.decode()
        detay = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])).content.decode()
        self.assertNotIn("Fason gerekli:", liste)
        self.assertNotIn("Fason gerekli:", detay)
