"""TÜRKİYE GÜNÜ (TIME_ZONE=UTC, saklama UTC, iş tarihi TR): saat TR 00:30 (UTC'de bir önceki gün 21:30) iken iş tarihi TR gününü vermeli. ``timezone.localdate()`` /
``date.today()`` / ``{% now %}`` / ORM ``__date`` UTC gününe bakar (TR 00:00-03:00 arası bir önceki gün) → bunlar yerine ``core.tarih.tr_bugun`` / ``tr_gun_araligi``.
Zaman ``mock.patch("django.utils.timezone.now")`` ile SABİTLENİR (freezegun kurulu değil; ``tr_bugun`` ``timezone.now()`` üzerinden çalışır)."""
import datetime
import inspect
import re
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from django import forms as django_forms
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

import core
from core import forms as core_forms
from core.models import Cari, MesaiKaydi, SilmeKaydi
from core.tarih import tr_bugun, tr_gun_araligi
from core.tests.test_belge_sablon_dinamik import SablonEkranTest
from core.tests.test_mesai_otomatik import OtoTaban, tr

UTC = datetime.timezone.utc
SIMDI = datetime.datetime(2026, 11, 17, 21, 30, tzinfo=UTC)          # = 18.11.2026 00:30 TR
TR_GUN = datetime.date(2026, 11, 18)
UTC_GUN = datetime.date(2026, 11, 17)


@contextmanager
def gece():
    with mock.patch("django.utils.timezone.now", return_value=SIMDI):
        yield


class YardimciTest(SimpleTestCase):
    def test_tr_bugun_ve_gun_araligi(self):
        with gece():
            self.assertEqual(tr_bugun(), TR_GUN)
        bas, bit = tr_gun_araligi(TR_GUN)
        self.assertEqual((bas, bit), (datetime.datetime(2026, 11, 17, 21, 0, tzinfo=UTC), datetime.datetime(2026, 11, 18, 21, 0, tzinfo=UTC)))
        self.assertEqual(tr_gun_araligi(TR_GUN, datetime.date(2026, 11, 20))[1], datetime.datetime(2026, 11, 20, 21, 0, tzinfo=UTC))   # çok günlü: yarı açık
        self.assertTrue(bas <= SIMDI < bit)                                                                                              # 00:30 TR, 18 Kasım gününün içinde



class FormVarsayilanTest(TestCase):
    """(DB'ye bakan bazı callable varsayılanlar da çağrıldığından SimpleTestCase değil TestCase.)"""

    def test_tum_formlarda_tarih_varsayilani_tr_gunu(self):
        """core.forms'taki callable ``initial`` tarih varsayılanlarının HEPSİ 00:30 TR'de TR gününü verir (eskiden ``timezone.localdate`` → bir önceki gün)."""
        sayac = tam = 0
        with gece():
            for ad, sinif in inspect.getmembers(core_forms, inspect.isclass):
                if not issubclass(sinif, django_forms.BaseForm) or not hasattr(sinif, "base_fields"):
                    continue
                for alan_adi, alan in sinif.base_fields.items():
                    if callable(alan.initial):
                        deger = alan.initial()
                        if isinstance(deger, datetime.date) and not isinstance(deger, datetime.datetime):
                            self.assertGreaterEqual(deger, TR_GUN, f"{ad}.{alan_adi}: {deger} (UTC günü {UTC_GUN} olsa bir gün erken olurdu)")   # "bugün" ya da bilerek ileri tarih (ör. bugün + 15 gün)
                            sayac += 1
                            tam += deger == TR_GUN
        self.assertGreater(tam, 20)                                                                         # çoğu varsayılan tam TR bugünü; tarama gerçekten alan buldu
        self.assertGreaterEqual(sayac, tam)



class BekciTest(SimpleTestCase):
    def test_uretim_kodunda_utc_gunu_ureten_cagri_yok(self):
        """Regresyon bekçisi: iş tarihi üreten kodda UTC gününe bakan çağrılar kullanılmaz (tarih.py = yardımcının kendisi; drive_yedek.py = sunucu/UTC yedek zamanı)."""
        kok = Path(core.__file__).parent
        kalip = re.compile(r"\bdate\.today\(\)|\btimezone\.localdate\b|\bdatetime\.now\(|\butcnow\(|\{%\s*now\b")
        ihlal = []
        for yol in list(kok.rglob("*.py")) + list(kok.rglob("*.html")):
            goreli = str(yol.relative_to(kok))
            if goreli.startswith(("tests/", "migrations/")) or yol.name in {"tarih.py", "drive_yedek.py"}:
                continue
            for no, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), start=1):
                if kalip.search(satir) and not satir.lstrip().startswith("#"):
                    ihlal.append(f"{goreli}:{no}: {satir.strip()[:100]}")
        self.assertEqual(ihlal, [], "UTC gününe bakan çağrılar (core.tarih.tr_bugun kullanın):\n" + "\n".join(ihlal))


class SablonEkranGeceTest(SablonEkranTest):
    """Belge şablonu ekranlarının TÜM testleri saat 00:30 TR'de (UTC günü bir önceki gün) çalışır; ek olarak varsayılan belge tarihi açıkça denetlenir."""

    def setUp(self):
        super().setUp()
        p = mock.patch("django.utils.timezone.now", return_value=SIMDI)
        p.start()
        self.addCleanup(p.stop)

    def test_belge_tarihi_varsayilani_tr_gunudur_utc_gunu_degil(self):
        self.client.force_login(self.belgeci)
        for sekme in ("belgeler", "ise_giris"):
            r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]) + f"?sekme={sekme}")
            self.assertContains(r, f'value="{TR_GUN.isoformat()}" required')
            self.assertNotContains(r, f'value="{UTC_GUN.isoformat()}" required')


class MesaiGeceTest(OtoTaban):
    def test_mesai_kaydi_ve_liste_tr_gununu_kullanir(self):
        k = self.mesai_ac(giris=tr(18, 0, 10, ay=11))                                                       # 18.11.2026 00:10 TR = 17.11 21:10 UTC
        self.assertEqual(k.is_tarihi, TR_GUN)                                                              # iş günü TR günü (UTC değil)
        self.client.force_login(self.yon)
        with gece():
            r = self.client.get(reverse("core:mesai_kayitlari"))
        self.assertEqual(r.context["bit"], TR_GUN)                                                          # varsayılan bitiş = TR bugünü
        self.assertIn(k.pk, [x.pk for x in r.context["sayfa"].object_list])


class EkranGeceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tr_gece_yon", password="x")

    def test_silme_kayitlari_tarih_suzgeci_tr_gunune_gore(self):
        s = SilmeKaydi.objects.create(tur="FATURA", kayit_no="F-GECE", silen=self.yon, created_by=self.yon, updated_by=self.yon)
        SilmeKaydi.objects.filter(pk=s.pk).update(created_at=datetime.datetime(2026, 11, 17, 22, 0, tzinfo=UTC))      # 18.11.2026 01:00 TR
        self.client.force_login(self.yon)
        url = reverse("core:silme_kayitlari")
        r = self.client.get(url, {"bas": "2026-11-18", "bit": "2026-11-18"})
        self.assertContains(r, "F-GECE")                                                                    # TR 18'inde silinmiş: 18 süzgecinde görünür
        r = self.client.get(url, {"bas": "2026-11-17", "bit": "2026-11-17"})
        self.assertNotContains(r, "F-GECE")                                                                 # 17 süzgecinde görünmez (eskiden UTC günü olduğu için görünürdü)

    def test_cari_detay_son_temas_max_tarihi_tr_gunu(self):
        cari = Cari.objects.create(kod="TRG1", unvan="GECE CARİ", created_by=self.yon, updated_by=self.yon)
        self.client.force_login(self.yon)
        with gece():
            r = self.client.get(reverse("core:cari_detay", args=[cari.pk]))
        self.assertContains(r, f'max="{TR_GUN.isoformat()}"')
        self.assertNotContains(r, f'max="{UTC_GUN.isoformat()}"')
