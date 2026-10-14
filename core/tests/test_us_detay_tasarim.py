"""ÜS detay yeniden tasarım (2026-10-14): üst bant, akış şeridi renkleri, sekmeler (?sekme=), Kayıtlar sekmesi (son 50, data-href), ilerleme/yüzde eşitliği ve
SORGU KİLİDİ (tasarım öncesi taban: 46 / 44 / 34 sorgu — yeni sayfa bunu aşmamalı)."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import StokHareket, TeklifSiparis
from core.services import us_detay
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.tests import test_us_eksik_kalan as tek

D = Decimal
TABAN_HIC, TABAN_KISMI, TABAN_SIPARISLI = 46, 44, 34


def _ie(durum, planlanan="10", tamamlanan="0", op=1, ist=1):
    return SimpleNamespace(durum=durum, planlanan=D(planlanan), tamamlanan=D(tamamlanan), operasyon_id=op, istasyon_id=ist)


def _grup(pk, ieler, fason=False):
    return {"istasyon": SimpleNamespace(pk=pk, kod=str(pk), ad=f"IST{pk}"), "fason": fason, "satirlar": [{"ie": ie} for ie in ieler]}


class AkisSeridiBirimTest(SimpleTestCase):
    def renk(self, g, eksik=None):
        return us_detay.akis_seridi([g], eksik or {})[0]

    def test_renkler_ve_oncelik(self):
        self.assertEqual(self.renk(_grup(1, [_ie("BITTI", tamamlanan="10"), _ie("BITTI", tamamlanan="10")]))["renk"], "tamam")
        self.assertEqual(self.renk(_grup(1, [_ie("BEKLIYOR")]))["renk"], "bekliyor")
        self.assertEqual(self.renk(_grup(1, [_ie("BASLADI", tamamlanan="3"), _ie("BEKLIYOR")]))["renk"], "devam")
        self.assertEqual(self.renk(_grup(1, [_ie("BEKLIYOR", tamamlanan="2")]))["renk"], "devam")                       # kısmen üretilmiş
        self.assertEqual(self.renk(_grup(1, [_ie("BEKLIYOR")], fason=True))["renk"], "fason")
        self.assertEqual(self.renk(_grup(1, [_ie("IPTAL")]))["renk"], "iptal")
        # malzeme eksik: bitmemiş gruplarda fason/devam/bekliyor'un önüne geçer; tamamlanmış grupta çıkmaz
        eksik = {1: ["IK-PROFIL"]}
        self.assertEqual(self.renk(_grup(1, [_ie("BASLADI", tamamlanan="3")]), eksik)["renk"], "eksik")
        self.assertEqual(self.renk(_grup(1, [_ie("BEKLIYOR")], fason=True), eksik)["renk"], "eksik")
        self.assertEqual(self.renk(_grup(1, [_ie("BITTI", tamamlanan="10")]), eksik)["renk"], "tamam")
        self.assertIn("IK-PROFIL", self.renk(_grup(1, [_ie("BEKLIYOR")]), eksik)["ipucu"])

    def test_yuzde_tamamlanan_bolu_planlanan_iptal_haric(self):
        k = self.renk(_grup(1, [_ie("BASLADI", planlanan="10", tamamlanan="5"), _ie("BITTI", planlanan="10", tamamlanan="10"), _ie("IPTAL", planlanan="99")]))
        self.assertEqual((k["bitti"], k["toplam"], k["yuzde"]), (1, 2, D("75")))
        self.assertEqual(k["yuzde_css"], "75.0")                                                                       # CSS için noktalı metin

    def test_css_yuzde_sinirlari(self):
        self.assertEqual((us_detay.css_yuzde(D("-5")), us_detay.css_yuzde(D("133.3")), us_detay.css_yuzde(D("33.714"))), ("0.0", "100.0", "33.7"))

    def test_sekme_secimi(self):
        self.assertEqual([us_detay.sekme_sec(x) for x in ("kayitlar", "xx", None, "malzeme")], ["kayitlar", "urunler", "urunler", "malzeme"])
        self.assertEqual(us_detay.sekme_sec(None, hata_var=True), "istasyonlar")
        self.assertEqual(us_detay.sekme_sec("urunler", hata_var=True), "urunler")


class UsDetayEkranTest(tek.KalanEksikTest):
    """profil→kesim→büküm→paketleme; profil stoğu 3, 10 mamul için 5 gerekir → kesim istasyonunda malzeme eksik (kırmızı)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)                                                                               # giriş ölçüme girmesin

    def detay(self, **q):
        return self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk]), q)

    def renkler(self, r):
        return {b["istasyon"].pk: b["renk"] for b in r.context["seritler"]}

    def test_ust_bant_ve_rozet_baglantisi(self):
        r = self.detay()
        self.assertContains(r, "Stok için üretim")
        self.assertContains(r, "0/3 istasyon bitti")
        self.assertContains(r, "Eksik malzeme: 1<")
        self.assertContains(r, 'href="?sekme=malzeme" data-git="malzeme"')                                              # rozet → Malzeme sekmesi
        self.assertEqual(r.context["bant"]["eksik"], 1)

    def test_serit_kirmizi_kesim_digerleri_gri_sonra_devam(self):
        r = self.detay()
        kesim, bukum, pak = (self.ie(op).istasyon_id for op in (self.kesim_op, self.bukum_op, self.pak_op))
        self.assertEqual(self.renkler(r)[kesim], "eksik")
        self.assertIn("IK-PROFIL", [b for b in r.context["seritler"] if b["istasyon"].pk == kesim][0]["eksikler"])
        for ist in {bukum, pak} - {kesim}:
            self.assertEqual(self.renkler(r)[ist], "bekliyor")
        self.assertContains(r, 'class="ud-kutu ud-eksik"')
        self.uret("6", "6", "6")
        r = self.detay()
        for ist in {bukum, pak} - {kesim}:
            self.assertEqual(self.renkler(r)[ist], "devam")

    def test_serit_hepsi_tamam(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=tek.GUN, tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("0"))
        self.uret("10", "10", "10")
        r = self.detay()
        self.assertEqual(set(self.renkler(r).values()), {"tamam"})
        self.assertContains(r, "ud-tamam")

    def test_sekmeler_url_ile_secilir(self):
        r = self.detay()
        h = r.content.decode()
        self.assertEqual(r.context["sekme"], "urunler")
        self.assertIn('id="sekme-urunler" data-sekme="urunler">', h)
        self.assertIn('id="sekme-kayitlar" data-sekme="kayitlar" hidden>', h)
        for ad in ("istasyonlar", "malzeme", "kayitlar"):
            r = self.detay(sekme=ad)
            self.assertEqual(r.context["sekme"], ad)
            self.assertIn(f'id="sekme-{ad}" data-sekme="{ad}">', r.content.decode())
            self.assertIn(f'id="sekme-urunler" data-sekme="urunler" hidden>', r.content.decode())
        self.assertEqual(self.detay(sekme="bozuk").context["sekme"], "urunler")

    def test_acik_gruplar_ustte_biten_kapali_ipucu_var(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=tek.GUN, tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("0"))
        self.uret("10", 0, 0)                                                                                          # yalnız kesim bitti
        r = self.detay(sekme="istasyonlar")
        sirali = r.context["gruplar_sirali"]
        self.assertEqual([g["tamam"] for g in sirali], sorted(g["tamam"] for g in sirali))                             # açıklar önce
        h = r.content.decode()
        self.assertIn('data-ipucu="Net plandaki her operasyon', h)
        self.assertNotIn('<p class="yardim">Net plandaki', h)                                                          # uzun paragraf kalktı
        self.assertContains(r, "Bu istasyonu üret")
        self.assertContains(r, "Tümünü üret")

    def test_ilerleme_ve_yuzde_eski_servislerle_ayni(self):
        for hazirla in (lambda: None, lambda: self.uret("6", "6", "6")):
            hazirla()
            r = self.detay()
            self.assertEqual(r.context["ilerleme"], uretim.uretim_emri_ilerleme(self.us))
            self.assertEqual(r.context["yuzde"], uretim.uretim_emri_yuzde(self.us))

    def test_kayitlar_sekmesi_son_n_ve_data_href(self):
        for _ in range(3):
            uretim.istasyon_emri_uret(self.ie(self.kesim_op), miktar=D("1"), tarih=tek.GUN, kullanici=self.yon)
        r = self.detay(sekme="kayitlar")
        satirlar = r.context["kayitlar"]
        self.assertEqual(len(satirlar), 3)
        self.assertEqual([s["k"].pk for s in satirlar], sorted((s["k"].pk for s in satirlar), reverse=True))            # en yeni üstte
        self.assertEqual(len(us_detay.son_kayitlar(self.us, adet=2)), 2)
        self.assertEqual(us_detay.KAYIT_ADET, 50)
        for s in satirlar:
            self.assertContains(r, f'data-href="{reverse("core:operasyon_kaydi_detay", args=[s["k"].pk])}"')
        self.assertContains(r, f'?uretim_emri={self.us.pk}')                                                           # "Kayıtları gör"

    def test_son_kayitlar_sorgu_sayisi_kayit_sayisindan_bagimsiz(self):
        uretim.istasyon_emri_uret(self.ie(self.kesim_op), miktar=D("1"), tarih=tek.GUN, kullanici=self.yon)
        us_detay.son_kayitlar(self.us)
        with CaptureQueriesContext(connection) as q1:
            us_detay.son_kayitlar(self.us)
        for _ in range(3):
            uretim.istasyon_emri_uret(self.ie(self.kesim_op), miktar=D("1"), tarih=tek.GUN, kullanici=self.yon)
        with CaptureQueriesContext(connection) as q2:
            self.assertEqual(len(us_detay.son_kayitlar(self.us)), 4)
        self.assertEqual(len(q1), len(q2))
        self.assertLessEqual(len(q2), 3)

    def test_sorgu_kilidi_kesim_ve_kismi(self):
        self.detay()                                                                                                    # ısınma
        with CaptureQueriesContext(connection) as q:
            self.detay()
        self.assertLessEqual(len(q), TABAN_HIC, f"{len(q)} sorgu")
        self.uret("6", "6", "6")
        with CaptureQueriesContext(connection) as q:
            self.detay(sekme="kayitlar")
        self.assertLessEqual(len(q), TABAN_KISMI, f"{len(q)} sorgu")


class UsDetaySiparisliTest(tek.SiparisliUsTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)

    def detay(self):
        return self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))

    def test_termin_kalan_gun_ve_gecmis_kirmizi(self):
        bugun = timezone.localdate()
        sip = self.emir.kaynak_siparis
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=bugun + timedelta(days=5))
        r = self.detay()
        self.assertEqual((r.context["bant"]["kalan_gun"], r.context["bant"]["termin_gecmis"]), (5, False))
        self.assertContains(r, "5 gün kaldı")
        self.assertNotContains(r, "ud-termin gecmis")
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=bugun - timedelta(days=3))
        r = self.detay()
        self.assertTrue(r.context["bant"]["termin_gecmis"])
        self.assertContains(r, "3 gün geçti")
        self.assertContains(r, "ud-termin gecmis")
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=None)
        self.assertNotContains(self.detay(), "gün geçti")

    def test_musteri_kaynak_siparis_baglantisi(self):
        r = self.detay()
        sip = self.emir.kaynak_siparis
        self.assertContains(r, reverse("core:teklif_siparis_detay", args=[sip.pk]))

    def test_sorgu_kilidi_siparisli(self):
        self.detay()
        with CaptureQueriesContext(connection) as q:
            self.detay()
        self.assertLessEqual(len(q), TABAN_SIPARISLI, f"{len(q)} sorgu")
