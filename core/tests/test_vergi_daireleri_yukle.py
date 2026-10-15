"""GİB vergi daireleri (2026-10-15): resmî liste CSV'si + `vergi_daireleri_yukle` komutu (önizleme / --uygula / idempotent / mevcut cari bağları), cari formunda
"İL – AD (KOD)" etiketi + arama + seçim, Tanımlar ekranında il filtresi / arama / sayfalama."""
import csv
import tempfile
from collections import Counter
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.management.commands import vergi_daireleri_yukle as cmd
from core.models import Cari, EkranYetki, Sehir, Ulke, VergiDairesi
from core.services import vergi_dairesi as vds
from core.tests.test_cari_faz3 import Taban


def calistir(*args, csv_yolu=None):
    out = StringIO()
    kw = {"csv": str(csv_yolu)} if csv_yolu else {}
    call_command("vergi_daireleri_yukle", *args, stdout=out, **kw)
    return out.getvalue()


def mini_csv(satirlar):
    """[(plaka, il, kod, resmi_ad)] → geçici CSV yolu (yorum satırı dahil)."""
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    f.write("# deneme\n")
    w = csv.writer(f)
    w.writerow(["il_plaka", "il", "ilce", "kod", "resmi_ad"])
    for plaka, il, kod, ad in satirlar:
        w.writerow([plaka, il, "Merkez", kod, ad])
    f.close()
    return Path(f.name)


class ResmiListeDosyasiTest(TestCase):
    def test_dosya_butunlugu(self):
        yorumlar, satirlar = cmd.csv_oku(cmd.VARSAYILAN_CSV)
        self.assertTrue(any("gib.gov.tr" in y for y in yorumlar), "kaynak adresi başlıkta olmalı")
        self.assertTrue(any("2026-10-15" in y for y in yorumlar), "indirme tarihi başlıkta olmalı")
        self.assertEqual(len(satirlar), 860)
        self.assertEqual(len({s["il"] for s in satirlar}), 81)
        kodlar = [s["kod"] for s in satirlar]
        self.assertEqual(len(set(kodlar)), 860)
        self.assertTrue(all(len(k) == 5 and k.isdigit() for k in kodlar))
        yeni = next(s for s in satirlar if s["kod"] == "34224")
        self.assertEqual((yeni["il"], yeni["resmi_ad"]), ("İSTANBUL", "Yenikapı Vergi Dairesi Müdürlüğü"))

    def test_kisa_ad_ve_cakisan_adlar(self):
        self.assertEqual(cmd.kisa_ad("Yenikapı Vergi Dairesi Müdürlüğü"), "YENİKAPI")
        self.assertEqual(cmd.kisa_ad("Ereğli Vergi Dairesi Müdürlüğü"), "EREĞLİ")


class YuklemeKomutuTest(TestCase):
    def test_onizleme_yazmaz(self):
        cikti = calistir()
        self.assertEqual(VergiDairesi.objects.count(), 0)
        self.assertIn("ÖNİZLEME", cikti)
        self.assertIn("860 daire, 81 il", cikti)
        self.assertIn("Eklenen                : 860", cikti)

    def test_uygula_ve_idempotent(self):
        calistir("--uygula")
        self.assertEqual(VergiDairesi.objects.filter(silindi=False).count(), 860)
        self.assertEqual(VergiDairesi.objects.exclude(kod="").values("kod").distinct().count(), 860)
        iller = Counter(VergiDairesi.objects.values_list("il", flat=True))
        self.assertEqual(len(iller), 81)
        ilk = list(VergiDairesi.objects.order_by("pk").values_list("pk", "ad", "il", "kod", "resmi_ad", "aktif"))
        cikti = calistir("--uygula")                                                                  # 2. çalıştırma: 0 değişiklik
        self.assertIn("TOPLAM DEĞİŞİKLİK      : 0", cikti)
        self.assertIn("Değişmeyen             : 860", cikti)
        self.assertEqual(list(VergiDairesi.objects.order_by("pk").values_list("pk", "ad", "il", "kod", "resmi_ad", "aktif")), ilk)
        self.assertIn("TOPLAM DEĞİŞİKLİK      : 0", calistir())                                       # önizleme de 0

    def test_cakisan_kisa_adlar_il_ile_ayrilir(self):
        calistir("--uygula")
        golbasi = set(VergiDairesi.objects.filter(ad__startswith="GÖLBAŞI").values_list("ad", flat=True))
        self.assertIn("GÖLBAŞI (ANKARA)", golbasi)
        self.assertIn("GÖLBAŞI (ADIYAMAN)", golbasi)
        self.assertEqual(VergiDairesi.objects.get(kod="34224").ad, "YENİKAPI")
        # (ad, il) ve ad tekil → Cari.vergi_dairesi metniyle birebir eşleşebilir
        adlar = list(VergiDairesi.objects.values_list("ad", flat=True))
        self.assertEqual(len(adlar), len(set(adlar)))

    def test_kod_tekrari_hata(self):
        yol = mini_csv([("34", "İSTANBUL", "34224", "Yenikapı Vergi Dairesi Müdürlüğü"), ("34", "İSTANBUL", "34224", "Başka Vergi Dairesi Müdürlüğü")])
        with self.assertRaises(CommandError):
            calistir(csv_yolu=yol)

    def test_mevcut_kayit_ad_aynen_baglanir_pk_ve_cari_bagi_korunur(self):
        eski = VergiDairesi.objects.create(ad="ERCİYES")                                               # il boş, kodsuz (eski/elle)
        cari = Cari.objects.create(unvan="X A.Ş.", vergi_dairesi="ERCİYES")
        yol = mini_csv([("38", "KAYSERİ", "38252", "Erciyes Vergi Dairesi Müdürlüğü"), ("34", "İSTANBUL", "34224", "Yenikapı Vergi Dairesi Müdürlüğü")])
        cikti = calistir("--uygula", csv_yolu=yol)
        self.assertIn("Mevcut kayda bağlanan  : 1", cikti)
        self.assertIn("Eklenen                : 1", cikti)
        eski.refresh_from_db()
        self.assertEqual((eski.ad, eski.il, eski.kod, eski.resmi_ad), ("ERCİYES", "KAYSERİ", "38252", "Erciyes Vergi Dairesi Müdürlüğü"))
        self.assertEqual(VergiDairesi.objects.filter(ad__startswith="ERCİYES").count(), 1)             # çoğalmadı
        cari.refresh_from_db()
        self.assertEqual(cari.vergi_dairesi, "ERCİYES")
        self.assertIn("TOPLAM DEĞİŞİKLİK      : 0", calistir(csv_yolu=yol))

    def test_kodlu_kayitta_ad_degismez_il_ve_resmi_ad_guncellenir(self):
        v = VergiDairesi.objects.create(ad="ELLE ADI", il="", kod="34224", resmi_ad="")
        yol = mini_csv([("34", "İSTANBUL", "34224", "Yenikapı Vergi Dairesi Müdürlüğü")])
        cikti = calistir("--uygula", csv_yolu=yol)
        v.refresh_from_db()
        self.assertEqual((v.ad, v.il, v.resmi_ad), ("ELLE ADI", "İSTANBUL", "Yenikapı Vergi Dairesi Müdürlüğü"))
        self.assertIn("KODU EŞLEŞEN AMA AD FARKLI", cikti)
        self.assertEqual(VergiDairesi.objects.count(), 1)

    def test_ad_farki_onay_bekler_cogaltmaz_eslestirme_dosyasiyla_baglanir(self):
        eski = VergiDairesi.objects.create(ad="MİMARSİNAN")
        Cari.objects.create(unvan="Y LTD", vergi_dairesi="MİMARSİNAN")
        yol = mini_csv([("38", "KAYSERİ", "38251", "Mimar Sinan Vergi Dairesi Müdürlüğü")])
        cikti = calistir("--uygula", csv_yolu=yol)
        self.assertIn("Onay bekleyen eşleşme  : 1", cikti)
        self.assertIn("Eklenen                : 0", cikti)
        self.assertEqual(VergiDairesi.objects.count(), 1)                                              # resmî daire eklenmedi
        eski.refresh_from_db()
        self.assertEqual((eski.kod, eski.il), ("", ""))                                                # onaysız dokunulmadı
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("eski_ad,kod\nMİMARSİNAN,38251\n")
        cikti = calistir("--uygula", "--eslestirme", f.name, csv_yolu=yol)
        self.assertIn("Mevcut kayda bağlanan  : 1", cikti)
        eski.refresh_from_db()
        self.assertEqual((eski.ad, eski.kod, eski.il), ("MİMARSİNAN", "38251", "KAYSERİ"))
        self.assertEqual(VergiDairesi.objects.count(), 1)
        self.assertEqual(Cari.objects.get(unvan="Y LTD").vergi_dairesi, "MİMARSİNAN")

    def test_listede_olmayan_eski_kayit_silinmez_pasife_alinmaz(self):
        v = VergiDairesi.objects.create(ad="OLMAYAN DAİRE")
        yol = mini_csv([("34", "İSTANBUL", "34224", "Yenikapı Vergi Dairesi Müdürlüğü")])
        cikti = calistir("--uygula", csv_yolu=yol)
        self.assertIn("Listede olmayan eski   : 1", cikti)
        v.refresh_from_db()
        self.assertFalse(v.silindi)
        self.assertTrue(v.aktif)

    def test_yukleme_cariyi_degistirmez(self):
        VergiDairesi.objects.create(ad="ERCİYES")
        c = Cari.objects.create(unvan="Z", vergi_dairesi="ERCİYES")
        yol = mini_csv([("38", "KAYSERİ", "38252", "Erciyes Vergi Dairesi Müdürlüğü")])
        calistir("--uygula", csv_yolu=yol)
        c2 = Cari.objects.get(pk=c.pk)
        self.assertEqual((c2.vergi_dairesi, c2.updated_at), (c.vergi_dairesi, c.updated_at))


class CariFormuVergiDairesiTest(Taban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        yol = mini_csv([("34", "İSTANBUL", "34224", "Yenikapı Vergi Dairesi Müdürlüğü"), ("38", "KAYSERİ", "38252", "Erciyes Vergi Dairesi Müdürlüğü"),
                        ("06", "ANKARA", "06280", "Gölbaşı Vergi Dairesi Müdürlüğü"), ("02", "ADIYAMAN", "02203", "Gölbaşı Vergi Dairesi Müdürlüğü")])
        calistir("--uygula", csv_yolu=yol)

    def test_etiket_il_ad_kod(self):
        self.assertEqual(vds.etiket(VergiDairesi.objects.get(kod="34224")), "İSTANBUL – YENİKAPI (34224)")
        self.assertEqual(vds.etiket(VergiDairesi.objects.get(kod="06280")), "ANKARA – GÖLBAŞI (06280)")        # "(ANKARA)" tekrarlanmaz
        self.assertEqual(vds.etiket(VergiDairesi(ad="ELLE", il="", kod="")), "ELLE")

    def test_formda_etiket_arama_icin_il_ad_kod(self):
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_ekle"))
        self.assertContains(r, "İSTANBUL – YENİKAPI (34224)")
        self.assertContains(r, "KAYSERİ – ERCİYES (38252)")
        self.assertContains(r, 'value="GÖLBAŞI (ANKARA)"')
        self.assertContains(r, 'value="GÖLBAŞI (ADIYAMAN)"')

    def test_formda_cok_kelimeli_arama_ve_il_one_cikarma_istemcisi(self):
        self.client.force_login(self.sade)
        html = self.client.get(reverse("core:cari_ekle")).content.decode()
        self.assertIn("kelimeler.every", html)                                                          # sırasız çok kelimeli arama (il / ad / kod)
        self.assertIn("vergi daireleri", html)                                                          # "<İL> vergi daireleri" grubu
        self.assertIn("'Diğer iller'", html)

    def test_secim_ve_kayit(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(vergi_dairesi="YENİKAPI"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Cari.objects.get(unvan="DENEME FİRMA").vergi_dairesi, "YENİKAPI")

    def test_cakisan_adli_daire_secilebilir(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(vergi_dairesi="GÖLBAŞI (ANKARA)"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Cari.objects.get(unvan="DENEME FİRMA").vergi_dairesi, "GÖLBAŞI (ANKARA)")

    def test_pasif_daire_formda_secilemez_ama_kartta_korunur(self):
        v = VergiDairesi.objects.get(kod="34224")
        c = self.cari(vergi_dairesi="YENİKAPI")
        v.aktif = False
        v.save()
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_duzenle", args=[c.pk]))
        self.assertContains(r, "YENİKAPI (listede yok)")                                                # kartta kayıtlı değer görünür kalır
        self.assertNotContains(self.client.get(reverse("core:cari_ekle")), "İSTANBUL – YENİKAPI (34224)")


class TanimlarEkraniTest(Taban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        calistir("--uygula")

    def liste(self, **q):
        self.client.force_login(self.su)
        return self.client.get(reverse("core:vergi_daireleri"), q)

    def test_il_filtresi(self):
        r = self.liste(il="KAYSERİ")
        gorunen = {d.il for d in r.context["daireler"]}
        self.assertEqual(gorunen, {"KAYSERİ"})
        self.assertEqual(r.context["toplam"], VergiDairesi.objects.filter(il="KAYSERİ").count())
        self.assertEqual(len(r.context["iller"]), 81)

    def test_arama_ad_kod_ve_cok_kelime(self):
        self.assertContains(self.liste(ara="yenikapı"), "Yenikapı Vergi Dairesi Müdürlüğü")
        r = self.liste(ara="34224")
        self.assertEqual([d.kod for d in r.context["daireler"]], ["34224"])
        r = self.liste(ara="istanbul yenikapı")                                                        # sırasız çok kelime: il + ad
        self.assertEqual([d.kod for d in r.context["daireler"]], ["34224"])
        r = self.liste(ara="gölbaşı", il="ANKARA")
        self.assertEqual([d.ad for d in r.context["daireler"]], ["GÖLBAŞI (ANKARA)"])

    def test_sayfalama_ve_filtre_korunur(self):
        r = self.liste()
        self.assertEqual(r.context["toplam"], 860)
        self.assertEqual(len(r.context["daireler"]), 100)
        self.assertEqual(r.context["daireler"].paginator.num_pages, 9)
        s2 = self.liste(sayfa=2)
        self.assertNotEqual([d.pk for d in s2.context["daireler"]], [d.pk for d in r.context["daireler"]])
        s = self.liste(ara="vergi", durum="aktif", sayfa=2)                                            # 860 eşleşme → sayfa bağları süzgeci taşımalı
        self.assertEqual(s.context["daireler"].number, 2)
        self.assertContains(s, "?ara=vergi&amp;durum=aktif&amp;sayfa=3")

    def test_durum_filtresi_ve_il_yok(self):
        VergiDairesi.objects.filter(kod="34224").update(aktif=False)
        self.assertEqual([d.kod for d in self.liste(durum="pasif").context["daireler"]], ["34224"])
        VergiDairesi.objects.create(ad="ELLE")
        self.assertEqual([d.ad for d in self.liste(il="-").context["daireler"]], ["ELLE"])

    def test_kod_ile_ekle_duzenle(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:vergi_dairesi_ekle"), {"ad": "yeni daire", "il": "ankara", "kod": "06999", "aktif": "on"})
        self.assertEqual(r.status_code, 302)
        v = VergiDairesi.objects.get(kod="06999")
        self.assertEqual((v.ad, v.il, v.aktif), ("YENİ DAİRE", "ANKARA", True))
        r = self.client.post(reverse("core:vergi_dairesi_ekle"), {"ad": "baska", "il": "ankara", "kod": "06999"})                    # aynı kod
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("core:vergi_dairesi_ekle"), {"ad": "baska", "il": "ankara", "kod": "12"})                       # 5 hane değil
        self.assertEqual(r.status_code, 200)
        self.client.post(reverse("core:vergi_dairesi_duzenle", args=[v.pk]), {"ad": "yeni daire", "il": "ankara", "kod": "06999"})   # aktif işaretsiz → pasif
        v.refresh_from_db()
        self.assertFalse(v.aktif)
