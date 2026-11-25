"""KUR FARKI VİRMANI → DÖVİZ HAVUZU (2026-11-25, BAREN PROJE 2026/2463 vakası).

TRY carisinde (136.10.0001, kur farkı motorunun havuzu DEĞİL) USD 6.500 alındı/iade edildi → USD döviz 0 ama TL 4.433 kaldı. 656 virmanı TRY satırı
yazınca ekstrenin TRY havuzu 4.433 düşüyordu. Yeni: karşı hesap 646/656 iken ``kur_farki_pb`` seçilirse cari satırı o döviz havuzuna DÖVİZ 0 / yalnız
TL bağlanır (kur değerleme satırıyla aynı yapı): TRY havuzu değişmez, USD havuzunun TL karşılığı kapanır, mizan aynı. Kapsam: servis, motor (döviz 0
satır genel "yalnız TL"), form/ekran, ekstre (0,00), kur değerlemeyle çakışmama, tek seferlik yönetim komutu (dry-run varsayılan)."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import cari_virman as cv
from core.services import kur_degerleme as kd
from core.services import kur_farki as kf
from core.services import raporlar
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal
CARI_H = "136.10.0001"


def _havuz(hesap, pb):
    q = v = Dc("0")
    for s in YevmiyeSatir.objects.filter(hesap_id=hesap, islem_pb=pb, silindi=False, fis__silindi=False):
        q += (1 if s.borc > 0 else -1) * s.islem_tutari
        v += s.borc - s.alacak
    return q, v


def _tl_bakiye(hesap):
    return raporlar._devir(hesap, D(2100, 1, 1))[0]


class Temel(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g, usd in ((D(2026, 7, 1), "46"), (D(2026, 7, 20), "46.9858"), (D(2026, 8, 13), "47.6678"), (D(2026, 8, 20), "48")):
            Kur.objects.create(tarih=g, usd_alis=Dc(usd), eur_alis=Dc("50"))
        _hesap("102.01.0001", "TL BANKA")
        _hesap("102.02.0001", "USD BANKA")
        _hesap(CARI_H, "BAREN PROJE")
        _hesap("320.10.0001", "FORMAL USD", kalem="KVYK")
        _hesap("600", "YURTİÇİ SATIŞLAR", kalem="A", grup="GELIR_TABLOSU")
        _hesap("153.10", "MAL")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        cls.su = User.objects.create_superuser("kfv", password="x")
        cls.baren = Cari.objects.create(kod="136-10-0001", unvan="BAREN PROJE", muhasebe_kodu=CARI_H, para_birimi="TRY")
        cls.formal = Cari.objects.create(kod="320-10-0001", unvan="FORMAL", muhasebe_kodu="320.10.0001", para_birimi="USD")
        # TRY hareketi: cari BORÇ 3.675.000 (TL havuzu)
        fis_olustur(tarih=D(2026, 7, 1), aciklama="SATIŞ", satirlar=[
            SatirGirdi(hesap_kodu=CARI_H, taraf="B", islem_tutari="3675000"), SatirGirdi(hesap_kodu="600", taraf="A", islem_tutari="3675000")])

    def _usd_al_ver(self, al_kur="46.9858", ver_kur="47.6678"):
        """2026/351 + 2026/414 benzeri: USD 6.500 carinin hesabına ALACAK (alındı), sonra BORÇ (iade edildi)."""
        for t, kur, cari_taraf in ((D(2026, 7, 20), al_kur, "A"), (D(2026, 8, 13), ver_kur, "B")):
            banka_taraf = "B" if cari_taraf == "A" else "A"
            fis_olustur(tarih=t, aciklama="USD", satirlar=[
                SatirGirdi(hesap_kodu=CARI_H, taraf=cari_taraf, islem_tutari="6500", islem_pb="USD", islem_kuru=kur),
                SatirGirdi(hesap_kodu="102.02.0001", taraf=banka_taraf, islem_tutari="6500", islem_pb="USD", islem_kuru=kur)])

    def _virman(self, cari=None, **kw):
        a = dict(cari=cari or self.baren, tarih=D(2026, 8, 13), tutar="4433", yon="alacak", karsi_hesap_kodu="656", kullanici=self.su)
        a.update(kw)
        return cv.virman_olustur(**a)


class ServisTest(Temel):
    def test_656_usd_havuzuna_baglanir_try_havuzu_degismez(self):
        self._usd_al_ver()
        self.assertEqual(_havuz(CARI_H, "USD"), (Dc("0"), Dc("4433.00")))                            # vaka: döviz 0, TL 4.433 B
        self.assertEqual(cv.varsayilan_kur_farki_pb(self.baren), "USD")
        f = self._virman(kur_farki_pb="USD")
        s = f.satirlar.get(hesap_id=CARI_H)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.alacak), ("USD", Dc("0.00"), Dc("4433.00")))
        self.assertEqual(f.satirlar.get(hesap_id="656").borc, Dc("4433.00"))                         # fiş: 656 B / cari A 4.433
        self.assertEqual(_havuz(CARI_H, "USD"), (Dc("0"), Dc("0.00")))                               # USD havuzunun TL karşılığı kapandı
        self.assertEqual(_havuz(CARI_H, "TRY"), (Dc("3675000.00"), Dc("3675000.00")))                # TRY havuzu ETKİLENMEDİ
        self.assertEqual(_tl_bakiye(CARI_H), Dc("3675000.00"))
        e = raporlar.ekstre_devirli(CARI_H, D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(e.dvz_toplamlar["TRY"]["bakiye"], Dc("3675000.00"))                         # ekstre TRY havuzu 3.675.000 B
        self.assertEqual(e.dvz_toplamlar["USD"]["bakiye"], Dc("0"))
        (satir,) = [x for x in e.satirlar if x.fis_pk == f.pk]
        self.assertTrue(satir.yalniz_tl)
        self.assertEqual((satir.pb, satir.dvz_alacak), ("USD", Dc("0.00")))

    def test_mizan_eski_yontemle_ayni(self):
        self._usd_al_ver()
        f = self._virman(kur_farki_pb="USD")
        yeni = (_tl_bakiye(CARI_H), _tl_bakiye("656"))
        cv.virman_sil(fis=f, cari=self.baren, kullanici=self.su)
        self._virman()                                                                               # eski davranış: TRY satırı
        self.assertEqual((_tl_bakiye(CARI_H), _tl_bakiye("656")), yeni)                              # TL / mizan birebir aynı

    def test_646_kar_yonu(self):
        self._usd_al_ver(al_kur="47.6678", ver_kur="46.9858")                                         # alınan pahalı, iade ucuz → TL 4.433 A
        self.assertEqual(_havuz(CARI_H, "USD"), (Dc("0"), Dc("-4433.00")))
        f = self._virman(yon="borc", karsi_hesap_kodu="646", kur_farki_pb="USD")
        self.assertEqual(f.satirlar.get(hesap_id="646").alacak, Dc("4433.00"))
        self.assertEqual(_havuz(CARI_H, "USD"), (Dc("0"), Dc("0.00")))
        self.assertEqual(_havuz(CARI_H, "TRY")[1], Dc("3675000.00"))

    def test_sadece_try_carisinde_eski_davranis(self):
        self.assertIsNone(cv.varsayilan_kur_farki_pb(self.baren))                                     # döviz havuzu yok
        self.assertEqual(cv.kur_farki_havuzlari(self.baren), [])
        f = self._virman(tutar="100")
        s = f.satirlar.get(hesap_id=CARI_H)
        self.assertEqual((s.islem_pb, s.islem_tutari), ("TRY", Dc("100.00")))
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "USD havuzu yok"):
            self._virman(tutar="100", kur_farki_pb="USD")

    def test_kurallar(self):
        self._usd_al_ver()
        _hesap("770.10", "GİDER", kalem="G", grup="GELIR_TABLOSU")
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "646"):
            self._virman(karsi_hesap_kodu="770.10", kur_farki_pb="USD")                              # 646/656 dışı hesap
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "646"):
            self._virman(karsi_hesap_kodu=None, karsi_cari=self.formal, kur_farki_pb="USD")           # karşı cari
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "elle kur"):
            self._virman(kur_farki_pb="USD", kur="47")
        with self.assertRaisesMessage(cv.CariVirmanHatasi, "döviz olmalı"):
            self._virman(kur_farki_pb="TRL")

    def test_duzenleme_bilgisi_ve_guncelle_havuzu_korur(self):
        self._usd_al_ver()
        f = self._virman(kur_farki_pb="USD")
        b = cv.duzenleme_bilgisi(f, self.baren)
        self.assertEqual((b["kur_farki_pb"], b["tutar"], b["kur"], b["karsi_hesap"].hesap_kodu), ("USD", Dc("4433.00"), None, "656"))
        cv.virman_guncelle(fis=f, cari=self.baren, tarih=D(2026, 8, 13), tutar="4433", yon="alacak", karsi_hesap_kodu="656",
                           kur_farki_pb="USD", kullanici=self.su)
        self.assertEqual(f.satirlar.get(hesap_id=CARI_H, silindi=False).islem_pb, "USD")
        self.assertEqual(_havuz(CARI_H, "TRY")[1], Dc("3675000.00"))


class DovizCariVeMotorTest(Temel):
    """USD carisi (320 → kur farkı motorunun havuzu): döviz 0 satır q'yu değiştirmez, kendine kur farkı satırı üretmez; kur değerlemeyle çakışmaz."""

    def _fatura_ve_odeme(self):
        fis_olustur(tarih=D(2026, 7, 20), aciklama="ALIŞ", satirlar=[
            SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="1000", islem_pb="USD", islem_kuru="40"),
            SatirGirdi(hesap_kodu="153.10", taraf="B", islem_tutari="40000")])
        fis_olustur(tarih=D(2026, 8, 13), aciklama="ÖDEME", satirlar=[
            SatirGirdi(hesap_kodu="320.10.0001", taraf="B", islem_tutari="500", islem_pb="USD", islem_kuru="42"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="21000")])

    def test_kur_farki_virmani_dovizi_degistirmez(self):
        self._fatura_ve_odeme()
        q0, v0 = kf.havuz_bakiyesi("320.10.0001", "USD")
        self.assertEqual(q0, Dc("-500"))
        f = self._virman(cari=self.formal, tutar="300", kur_farki_pb="USD")
        s = f.satirlar.get(hesap_id="320.10.0001")
        self.assertEqual((s.islem_pb, s.islem_tutari, s.alacak), ("USD", Dc("0.00"), Dc("300.00")))
        self.assertFalse(s.kur_farki_satirlari.exists())                                              # motor bu satıra kur farkı üretmez
        q1, v1 = kf.havuz_bakiyesi("320.10.0001", "USD")
        self.assertEqual((q1, v1), (q0, v0 - Dc("300")))                                               # döviz aynı, TL 300 alacak yönde
        fis_olustur(tarih=D(2026, 8, 20), aciklama="KALAN ÖDEME", satirlar=[
            SatirGirdi(hesap_kodu="320.10.0001", taraf="B", islem_tutari="500", islem_pb="USD", islem_kuru="42"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="21000")])
        self.assertEqual(kf.havuz_bakiyesi("320.10.0001", "USD"), (Dc("0"), Dc("0.00")))              # kapanışta TL de tam sıfır

    def test_eski_davranis_secim_yoksa_doviz_satiri(self):
        self._fatura_ve_odeme()
        f = self._virman(cari=self.formal, tutar="420")
        s = f.satirlar.get(hesap_id="320.10.0001")
        self.assertEqual((s.islem_pb, s.islem_tutari), ("USD", Dc("8.81")))                            # 420 TL / 47,6678 (13.08 TCMB) = 8,81 USD (eski: döviz değişir)

    def test_kur_degerleme_ile_cakismaz(self):
        self._fatura_ve_odeme()
        self._virman(cari=self.formal, tutar="300", kur_farki_pb="USD")
        o = kd.onizle(D(2026, 8, 20))
        (r,) = [x for x in o["satirlar"] if x["hesap_kodu"] == "320.10.0001"]
        q, v = kf.havuz_bakiyesi("320.10.0001", "USD")
        self.assertEqual((r["doviz"], r["tl"]), (q, v))                                                # değerleme virmanlı TL'yi görür
        kd.uygula(D(2026, 8, 20), kullanici=self.su)
        q2, v2 = kf.havuz_bakiyesi("320.10.0001", "USD")
        self.assertEqual((q2, v2), (q, (q * Dc("48")).quantize(Dc("0.01"))))                          # değerleme sonrası TL = döviz × kur
        kf.havuzlari_yeniden_hesapla({("320.10.0001", "USD")})
        self.assertEqual(kf.havuz_bakiyesi("320.10.0001", "USD"), (q2, v2))                            # motor yeniden hesapta bozmaz

    def test_motor_saf_fonksiyonu_doviz_sifir_satiri_yalniz_tl(self):
        g = [kf.GirdiSatir(1, -1, Dc("100"), Dc("4000")), kf.GirdiSatir(2, -1, Dc("0"), Dc("50"), degerleme=True),
             kf.GirdiSatir(3, 1, Dc("100"), Dc("4200"))]
        sonuc, q, v = kf.havuz_hesapla(g)
        self.assertEqual((q, v), (Dc("0"), Dc("0.00")))
        self.assertEqual(sonuc[1].fark, Dc("0.00"))


class EkranTest(Temel):
    def setUp(self):
        self.client.force_login(self.su)

    def test_form_varsayilan_havuz_secili_ve_kayit(self):
        self._usd_al_ver()
        r = self.client.get(reverse("core:cari_virman_ekle", args=[self.baren.pk]))
        self.assertContains(r, 'id="kur-farki-alani"')
        self.assertContains(r, '<option value="USD" selected>')
        self.assertContains(r, "USD havuzu — döviz 0,00 · TL 4.433,00 B")
        r = self.client.post(reverse("core:cari_virman_ekle", args=[self.baren.pk]), {
            "tarih": "2026-08-13", "tutar": "4.433,00", "yon": "alacak", "karsi_hesap": "656", "kur_farki_pb": "USD", "sayilan_pb": ""})
        self.assertEqual(r.status_code, 302)
        s = YevmiyeSatir.objects.get(fis__kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN, hesap_id=CARI_H)
        self.assertEqual((s.islem_pb, s.islem_tutari), ("USD", Dc("0.00")))
        r = self.client.get(reverse("core:cari_ekstresi", args=[self.baren.pk]))
        self.assertContains(r, "yalnız TL")
        r = self.client.get(reverse("core:cari_virman_duzenle", args=[self.baren.pk, s.fis_id]))
        self.assertContains(r, '<option value="USD" selected>')

    def test_646_656_disi_hesapta_secim_yok_sayilir(self):
        self._usd_al_ver()
        _hesap("770.10", "GİDER", kalem="G", grup="GELIR_TABLOSU")
        r = self.client.post(reverse("core:cari_virman_ekle", args=[self.baren.pk]), {
            "tarih": "2026-08-13", "tutar": "100", "yon": "alacak", "karsi_hesap": "770.10", "kur_farki_pb": "USD", "sayilan_pb": ""})
        self.assertEqual(r.status_code, 302)
        s = YevmiyeSatir.objects.get(fis__kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN, hesap_id=CARI_H)
        self.assertEqual(s.islem_pb, "TRY")                                                            # gizli varsayılan yok sayıldı

    def test_doviz_havuzu_olmayan_caride_alan_yok(self):
        r = self.client.get(reverse("core:cari_virman_ekle", args=[self.baren.pk]))
        self.assertNotContains(r, 'id="kur-farki-alani"')


class KomutTest(Temel):
    def _eski_virman(self):
        self._usd_al_ver()
        return self._virman()                                                                          # 2026/2463 gibi: TRY satırı

    def test_dry_run_yazmaz_uygula_yazar_idempotent(self):
        f = self._eski_virman()
        no = f"{f.yil}/{f.fis_no}"
        once = sorted(f.satirlar.filter(silindi=False).values_list("hesap_id", "borc", "alacak"))
        out = StringIO()
        call_command("virman_kur_farki_havuz_ata", "--fis", no, stdout=out)
        self.assertIn("DRY-RUN", out.getvalue())
        self.assertIn("TRY 4.433,00", out.getvalue())
        self.assertEqual(f.satirlar.get(hesap_id=CARI_H).islem_pb, "TRY")                             # hiçbir şey yazılmadı
        self.assertEqual(_havuz(CARI_H, "TRY")[1], Dc("3670567.00"))                                   # vakadaki yanlış TRY havuzu
        out = StringIO()
        call_command("virman_kur_farki_havuz_ata", "--fis", no, "--uygula", stdout=out)
        self.assertIn("UYGULANDI", out.getvalue())
        s = f.satirlar.get(hesap_id=CARI_H)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.alacak), ("USD", Dc("0.00"), Dc("4433.00")))
        self.assertEqual(sorted(f.satirlar.filter(silindi=False).values_list("hesap_id", "borc", "alacak")), once)   # fiş TL'si aynı
        self.assertEqual(_havuz(CARI_H, "TRY")[1], Dc("3675000.00"))
        self.assertEqual(_havuz(CARI_H, "USD"), (Dc("0"), Dc("0.00")))
        out = StringIO()
        call_command("virman_kur_farki_havuz_ata", "--fis", no, "--uygula", stdout=out)
        self.assertIn("Zaten USD", out.getvalue())

    def test_646_656_disi_virmanda_reddeder(self):
        self._usd_al_ver()
        _hesap("770.10", "GİDER", kalem="G", grup="GELIR_TABLOSU")
        f = self._virman(karsi_hesap_kodu="770.10", tutar="10")
        with self.assertRaisesMessage(CommandError, "646/656 değil"):
            call_command("virman_kur_farki_havuz_ata", "--fis", f"{f.yil}/{f.fis_no}", stdout=StringIO())
