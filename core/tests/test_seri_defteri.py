"""SERİ DEFTERİ (core.services.seri): bitmiş ürün serileri TEK yerde (A / C / CS); Operasyon Tanımları etiketi + filtresi + dağılımı ve Ürün Ağacı ürün seçici /
Karşılaştır / Nerede kullanılıyor aynı defteri okur. CS (152-23-*) artık 'Diğer' değil; A+C+CS'nin ortak parçaları (≥2 seri) ORTAK; bilinmeyen 152-* hâlâ DIGER;
defterdeki satır eklenince yeni seri her yerde görünür (kod değişikliği olmadan)."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse

from core.services import seri
from core.services import urun_agaci as ua
from core.services.uretim import operasyon_liste, operasyon_serileri
from core.tests.test_operasyon_liste import ListeBase

D = Decimal


class DefterTest(SimpleTestCase):
    def test_seri_of_kod(self):
        self.assertEqual([seri.seri_of_kod(k) for k in ("152-10-0001", "152-22-0006", "152-23-0003", "152-77-0001", "151-10-0001", "")],
                         ["A", "C", "CS", "DIGER", "DIGER", "DIGER"])

    def test_sinifla(self):
        self.assertEqual(seri.sinifla(["152-23-0001", "152-23-0002"]), "CS")
        self.assertEqual(seri.sinifla(["152-22-0001", "152-23-0001"]), "ORTAK")                       # C + CS
        self.assertEqual(seri.sinifla(["152-10-0001", "152-22-0001", "152-23-0001"]), "ORTAK")        # üç seri
        self.assertEqual(seri.sinifla(["152-10-0001", "152-77-0001"]), "A")                           # tanımlı seri + bilinmeyen → tanımlı (eskisi gibi)
        self.assertEqual(seri.sinifla(["152-77-0001"]), "DIGER")
        self.assertEqual(seri.sinifla([]), "BAGLANTISIZ")

    def test_filtre_ve_secici_listeleri(self):
        self.assertEqual(seri.filtre_secenekleri(), [("", "Tüm seriler"), ("A", "A tipi"), ("C", "C tipi"), ("CS", "CS tipi"), ("ORTAK", "Ortak"),
                                                     ("BAGLANTISIZ", "Bağlantısız")])
        self.assertEqual([k for k, _ in seri.karsilastir_secenekleri()], ["A", "C", "CS"])
        self.assertEqual(list(seri.LISTE_ETIKET), ["A", "C", "CS", "ORTAK", "DIGER", "BAGLANTISIZ"])
        self.assertEqual(seri.URUN_SIRA, ("A", "C", "CS", "DIGER"))
        self.assertEqual(ua.SERI_AD["CS"], "CS tipi")                                                 # Ürün Ağacı aynı defterden

    def test_yeni_seri_eklemek_tek_satir(self):
        yeni = seri.Seri("D", "D tipi", "D tipi", "D tipi (152-30)", "152-30-")
        with mock.patch.object(seri, "SERILER", seri.SERILER + (yeni,)):
            self.assertEqual(seri.seri_of_kod("152-30-0001"), "D")
            self.assertEqual(seri.sinifla(["152-30-0001"]), "D")
            self.assertEqual(seri.sinifla(["152-30-0001", "152-10-0001"]), "ORTAK")


class CsSerisiBase(ListeBase):
    """ListeBase (A: 152-10-0001, C: 152-22-0001) + CS: 151-10-0063 (çelik ayak kesim) → 151-80-0017 (birleştirilmiş ön ayak CS) → 152-23-0001; boyalı menteşe
    151-20-0016 hem C hem CS ürününde kullanılır (ORTAK); CS'ye özgü ham menteşe değil."""

    def setUp(self):
        super().setUp()
        k = {kod: self._s(kod) for kod in ("151-10-0063", "151-80-0017", "152-23-0001", "151-20-0016")}
        self.cs = k
        self.cops = {
            "cs_kesim": self._op(self.kesim, k["151-10-0063"], 10, [(self.profil, 1)]),
            "cs_asm": self._op(self.birl, k["151-80-0017"], 1, [(k["151-10-0063"], 1)]),
            "mentese": self._op(self.kesim, k["151-20-0016"], 8, [(self.profil, 1)]),
            "cs_kok": self._op(self.montaj, k["152-23-0001"], 1, [(k["151-80-0017"], 1), (k["151-20-0016"], 2)]),
        }
        self._op(self.montaj, self._s("152-22-0002"), 1, [(self.k["151-80-0011"], 1), (k["151-20-0016"], 2)])        # C ürünü de boyalı menteşeyi kullanır


class OperasyonListesiCsTest(CsSerisiBase):
    def test_cs_zinciri_cs_ve_ortak_parca_ortak(self):
        sr = operasyon_serileri()
        pk = lambda n: self.cops[n].pk
        self.assertEqual((sr[pk("cs_kesim")], sr[pk("cs_asm")], sr[pk("cs_kok")]), ("CS", "CS", "CS"))
        self.assertEqual(sr[pk("mentese")], "ORTAK")                                                   # C ve CS ürününde kullanılıyor
        self.assertEqual(sr[self.ops["a1"].pk], "A")                                                   # A/C davranışı değişmedi
        self.assertEqual(sr[self.ops["c1"].pk], "C")
        self.assertEqual(sr[self.ops["ortak"].pk], "ORTAK")

    def test_filtre_ve_dagilim(self):
        kodlar = lambda **kw: {r["op"].cikti.kod for r in operasyon_liste(**kw)["satirlar"]}
        self.assertEqual(kodlar(seri="CS"), {"151-10-0063", "151-80-0017", "152-23-0001"})
        self.assertEqual(kodlar(seri="ORTAK"), {"151-10-0045", "151-20-0016"})
        d = operasyon_liste()["seri_dagilimi"]
        self.assertEqual((d["CS"], d["DIGER"], d["ORTAK"]), (3, 0, 2))
        self.assertEqual(sum(d.values()), len(operasyon_liste()["satirlar"]))

    def test_ekran_etiket_filtre_secenegi_ve_gecersiz_seri(self):
        self.client.force_login(User.objects.create_superuser("cs_su", password="x"))
        h = self.client.get(reverse("core:operasyon_tanimlari"), {"seri": "CS"}).content.decode()
        self.assertIn('<option value="CS" selected>CS tipi</option>', h)
        self.assertIn(">CS tipi</span>", h)                                                            # satır rozeti
        self.assertNotIn(">Diğer</span>", h)
        r = self.client.get(reverse("core:operasyon_tanimlari"), {"seri": "XYZ"})
        self.assertEqual(len(r.context["satirlar"]), len(operasyon_liste()["satirlar"]))               # geçersiz seri yok sayılır


class UrunAgaciCsTest(CsSerisiBase):
    def test_urun_secici_cs_grubu(self):
        gruplar = dict(ua.urun_secenekleri(ua.graf_yukle()))
        self.assertIn("Bitmiş ürünler – CS tipi", gruplar)
        self.assertEqual([s.split()[0] for _pk, s in gruplar["Bitmiş ürünler – CS tipi"]], ["152-23-0001"])
        diger = gruplar.get("Bitmiş ürünler – Diğer", [])                                             # fixture'daki kullanılmayan 151-* uç stoklar Diğer'de kalır
        self.assertFalse([s for _pk, s in diger if s.startswith("152-")])                             # ama hiçbir 152-* mamul (CS dahil) Diğer'de değil

    def test_karsilastir_cs_serisi_ve_ekran(self):
        urunler, uyari = ua.karsilastir_urunleri(ua.graf_yukle(), "CS")
        self.assertEqual(([u.kod for u in urunler], uyari), (["152-23-0001"], ""))
        self.client.force_login(User.objects.create_superuser("cs_ua", password="x"))
        r = self.client.get(reverse("core:urun_agaci"), {"gorunum": "karsilastir", "seri": "CS"})
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        self.assertIn('<option value="CS" selected>CS tipi – çelik ayaklı (152-23)</option>', h)
        self.assertIn("Karşılaştırma — CS tipi serisi", h)
        for kod in ("A", "C"):
            self.assertIn(f'<option value="{kod}">', h)

    def test_nerede_kullaniliyor_cs_grubu(self):
        s = ua.nerede_kullaniliyor(ua.graf_yukle(), self.cs["151-20-0016"])
        self.assertEqual([g["seri"] for g in s["gruplar"]], ["C", "CS"])                              # boyalı menteşe: C ve CS ürünleri, A→C→CS sırası
        self.assertEqual([g["ad"] for g in s["gruplar"]], ["Çift çıkış", "CS tipi"])
