"""Cari virman: isteğe bağlı ELLE kur. Boşsa tarihin TCMB kuru (regresyon); doluysa tutar DÖVİZ cinsindendir, TL = tutar × kur, iki tarafta aynı
döviz/TL (kur farkı satırı doğmaz); karşı TL hesap/cari; düzenlemede kur korunur; TL carisinde yok sayılır."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Cari
from core.services import cari_virman as cv
from core.tests.test_banka_hesap_hareketi import _hesap
from core.tests.test_cari_virman import VirmanBase

D = datetime.date
Dc = Decimal


class VirmanElleKurTest(VirmanBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("320.10.0002", "ARGEMA USD", kalem="KVYK")
        _hesap("320.10.0003", "ÖRNEK EUR", kalem="KVYK")
        _hesap("770.10", "SEYAHAT GİDERİ", kalem="C", grup="GELIR_TABLOSU")
        cls.usd2 = Cari.objects.create(kod="320-10-0002", unvan="ARGEMA", muhasebe_kodu="320.10.0002", para_birimi="USD")
        cls.eur = Cari.objects.create(kod="320-10-0003", unvan="ÖRNEK", muhasebe_kodu="320.10.0003", para_birimi="EUR")

    def _ayrinti(self, fis):
        return {s.hesap_id: (s.borc, s.alacak, s.islem_pb, s.islem_tutari, s.islem_kuru) for s in fis.satirlar.filter(silindi=False)}

    def test_kur_bos_tcmb_kuru_kullanilir_regresyon(self):
        f = cv.virman_olustur(cari=self.formal, karsi_cari=self.nuri, tarih=D(2026, 7, 14), tutar="8400", yon="borc", kullanici=self.su)
        s = f.satirlar.get(hesap_id="320.10.0001")
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru, s.borc), ("USD", Dc("210.00"), Dc("40.000000"), Dc("8400.00")))   # 8.400 / 40 (TCMB)

    def test_usd_usd_iki_tarafta_ayni_tl_ve_usd_kur_farki_yok(self):
        f = cv.virman_olustur(cari=self.formal, karsi_cari=self.usd2, tarih=D(2026, 7, 14), tutar="15.100,00", kur="48,85",
                              yon="alacak", kullanici=self.su)
        a = self._ayrinti(f)
        self.assertEqual(a["320.10.0001"], (Dc("0.00"), Dc("737635.00"), "USD", Dc("15100.00"), Dc("48.850000")))     # formal ALACAK
        self.assertEqual(a["320.10.0002"], (Dc("737635.00"), Dc("0.00"), "USD", Dc("15100.00"), Dc("48.850000")))     # karşı BORÇ
        self.assertEqual(len(a), 2)                                                                                     # 646/656 kur farkı satırı YOK
        self.assertEqual(sum(s.borc for s in f.satirlar.all()), sum(s.alacak for s in f.satirlar.all()))

    def test_usd_cari_ile_tl_hesap(self):
        f = cv.virman_olustur(cari=self.formal, karsi_hesap_kodu="770.10", tarih=D(2026, 7, 14), tutar="1.000", kur="48,85",
                              yon="alacak", kullanici=self.su)
        a = self._ayrinti(f)
        self.assertEqual(a["320.10.0001"], (Dc("0.00"), Dc("48850.00"), "USD", Dc("1000.00"), Dc("48.850000")))
        self.assertEqual(a["770.10"][:3], (Dc("48850.00"), Dc("0.00"), "TRY"))                                          # TL = tutar × kur

    def test_usd_cari_ile_tl_cari(self):
        f = cv.virman_olustur(cari=self.formal, karsi_cari=self.nuri, tarih=D(2026, 7, 14), tutar="100", kur="48,85", yon="borc", kullanici=self.su)
        a = self._ayrinti(f)
        self.assertEqual(a["320.10.0001"][:2], (Dc("4885.00"), Dc("0.00")))
        self.assertEqual(a["500.10.0001"][:3], (Dc("0.00"), Dc("4885.00"), "TRY"))

    def test_farkli_doviz_cari_elle_kurda_reddedilir(self):
        with self.assertRaises(cv.CariVirmanHatasi):
            cv.virman_olustur(cari=self.formal, karsi_cari=self.eur, tarih=D(2026, 7, 14), tutar="100", kur="48,85", yon="alacak", kullanici=self.su)

    def test_tl_carisinde_kur_yok_sayilir(self):
        f = cv.virman_olustur(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 14), tutar="75500", kur="48,85", yon="alacak", kullanici=self.su)
        self.assertEqual(self._satirlar(f), {("320.30.0038", "A"): Dc("75500.00"), ("500.10.0001", "B"): Dc("75500.00")})

    def test_gecersiz_kur(self):
        for k in ("abc", "0", "-5"):
            with self.assertRaises(cv.CariVirmanHatasi):
                cv.virman_olustur(cari=self.formal, karsi_cari=self.usd2, tarih=D(2026, 7, 14), tutar="100", kur=k, yon="alacak", kullanici=self.su)

    def test_duzenle_kur_korunur_ve_yeniden_yazimda_kullanilir(self):
        f = cv.virman_olustur(cari=self.formal, karsi_cari=self.usd2, tarih=D(2026, 7, 14), tutar="15100", kur="48,85", yon="alacak", kullanici=self.su)
        b = cv.duzenleme_bilgisi(f, self.formal)
        self.assertEqual((b["kur"], b["tutar"]), (Dc("48.850000"), Dc("15100.00")))                                     # elle kur + DÖVİZ tutar
        cv.virman_guncelle(fis=f, cari=self.formal, karsi_cari=b["karsi_cari"], tarih=f.tarih, tutar=b["tutar"], yon=b["yon"],
                           kur=b["kur"], kullanici=self.su)
        a = self._ayrinti(f)
        self.assertEqual((a["320.10.0001"][1], a["320.10.0002"][0]), (Dc("737635.00"), Dc("737635.00")))
        # kur boşaltılırsa TCMB'ye döner (TL tutar yorumu)
        cv.virman_guncelle(fis=f, cari=self.formal, karsi_cari=self.usd2, tarih=f.tarih, tutar="8400", yon="alacak", kur=None, kullanici=self.su)
        self.assertEqual(self._ayrinti(f)["320.10.0001"][3:], (Dc("210.00"), Dc("40.000000")))
        self.assertIsNone(cv.duzenleme_bilgisi(f, self.formal)["kur"])

    def test_ekran_kur_alani_ve_duzenle_onceden_dolu(self):
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_virman_ekle", args=[self.formal.pk]))
        self.assertContains(r, 'name="kur"')
        self.assertContains(r, 'id="kur-onizleme"')                                                                 # anlık TL önizleme kutusu (JS)
        self.assertNotContains(self.client.get(reverse("core:cari_virman_ekle", args=[self.kaygun.pk])), 'name="kur"')   # TRY carisinde gizli
        r = self.client.post(reverse("core:cari_virman_ekle", args=[self.formal.pk]), {
            "tarih": "2026-07-14", "tutar": "15.100,00", "kur": "48,85", "yon": "alacak", "karsi_cari": self.usd2.pk,
            "sayilan_pb": "", "aciklama": ""})
        self.assertEqual(r.status_code, 302)
        from core.models import YevmiyeFisi
        fis = YevmiyeFisi.objects.filter(kaynak="CARI_VIRMAN").latest("pk")
        r2 = self.client.get(reverse("core:cari_virman_duzenle", args=[self.formal.pk, fis.pk]))
        self.assertContains(r2, "48,85")
        self.assertContains(r2, "15.100,00")
