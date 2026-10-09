"""hatali_kayit_sil: dry-run hiçbir şey silmez; --uygula iptal fişleri ve boş duran varlık kartlarını siler;
aktif fiş / bağlı kayıt (fatura satırı) olanlara ve cariye dokunmaz.

Komut canlıdaki sabit kayıtlara (fiş pk 260/266/263, banka hesap pk 4/12/13) bağlıdır; test bu sabitleri VARSAYMAZ: kendi oluşturduğu
nesnelerin (doğal) pk'lerini komutun FISLER / BANKA_HESAPLARI sabitlerine mock ile verir → veritabanı dizisi (sequence) durumundan
bağımsızdır (--parallel / --keepdb güvenli)."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.management.commands import hatali_kayit_sil as hks
from core.models import Cari, DuranVarlik, YevmiyeFisi, YevmiyeSatir
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
# (fiş no, tutar, tarih, açıklama, banka hesabı ROLÜ) — 4/12/13 yalnız ROL anahtarıdır (pk değil)
_FIS = [(189, "134757.98", D(2026, 1, 5), "İCRA DAİRESİ GELEN BEDEL (TEMİNAT İADESİ)", 4),
        (195, "6928.64", D(2026, 1, 19), "İCRA DAİRESİ GELEN BEDEL (TEMİNAT İADESİ)", 4),
        (192, "1849.35", D(2026, 1, 6), "VADELİ HESAP FAİZ GELİRİ", 13),
        (423, "6871.24", D(2026, 1, 1), "GERÇEK TARİH: 17.12.2025. VADELİ AK003367 NET FAİZ", 12)]


def _fis(no, tutar, tarih, ack, banka, silindi=True):
    f = YevmiyeFisi.objects.create(yil=2026, fis_no=no, tarih=tarih, aciklama=ack, kaynak="BANKA",
                                   banka_hesap=banka, silindi=silindi)
    t = Decimal(tutar)
    for kod, b, a in (("102.01.0001", t, 0), ("131.01", 0, t)):
        YevmiyeSatir.objects.create(fis=f, hesap_id=kod, borc=b, alacak=a, islem_tutari=t, islem_kuru=1)
    return f


class HataliKayitSilTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("102.01.0001", "BANKA")
        _hesap("131.01", "KARŞI")
        _hesap("253.01", "DV")
        b = banka_olustur(ad="vakif")
        cls.h = {}                                       # rol (4/12/13) -> BankaHesap (doğal pk)
        for rol in (4, 12, 13):
            cls.h[rol] = banka_hesap_olustur(banka=b, ad=f"h{rol}", para_birimi="TRY", muhasebe_kodu="102.01.0001")

    def setUp(self):
        self.fis = {}                                    # fiş no -> YevmiyeFisi (doğal pk)
        for no, tutar, tarih, ack, rol in _FIS:
            self.fis[no] = _fis(no, tutar, tarih, ack, self.h[rol])
        for kod in ("DV-0023", "DV-0024"):
            DuranVarlik.objects.create(demirbas_kodu=kod, ad="X", hesap_id="253.01", durum="PASIF", maliyet=0,
                                       aktiflestirme_tarihi=D(2026, 1, 1), kaynak="ACILIS", silindi=True)
        Cari.objects.create(kod="320-30-0025", muhasebe_kodu="320.30.0025", unvan="SAKARYA VD")
        # Komutun canlı sabitleri (pk 260/266/263 + banka hesap 4/12/13) bu testin gerçek pk'lerine çevrilir; tarih/tutar/açıklama sabitleri AYNEN kalır.
        rol_pk = {rol: h.pk for rol, h in self.h.items()}
        fisler = [(self.fis[no].pk if pk else None, yil, no, tutar, tarih, rol_pk[bh], ack)
                  for pk, yil, no, tutar, tarih, bh, ack in hks.FISLER]
        for patcher in (mock.patch.object(hks, "FISLER", fisler),
                        mock.patch.object(hks, "BANKA_HESAPLARI", tuple(h.pk for h in self.h.values()))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _komut(self, *ek):
        out = StringIO()
        call_command("hatali_kayit_sil", *ek, stdout=out)
        return out.getvalue()

    def _say(self):
        return (YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), DuranVarlik.objects.count(),
                Cari.objects.count())

    def test_dry_run_hicbir_sey_silmez_uygula_siler_cariye_dokunmaz(self):
        once = self._say()
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(self._say(), once)
        self.assertIn("DEĞİŞMEDİ", c)
        self._komut("--uygula")
        self.assertEqual(self._say(), (0, 0, 0, 1))

    def test_aktif_fis_silinmez(self):
        YevmiyeFisi.objects.filter(pk=self.fis[423].pk).update(silindi=False)
        c = self._komut("--uygula")
        self.assertIn("SİLİNMEZ", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=self.fis[423].pk).exists())
        self.assertFalse(YevmiyeFisi.objects.filter(pk=self.fis[192].pk).exists())

    def test_bulunamayan_raporlanir(self):
        DuranVarlik.objects.filter(demirbas_kodu="DV-0024").delete()
        self.assertIn("DV-0024: BULUNAMADI", self._komut())
