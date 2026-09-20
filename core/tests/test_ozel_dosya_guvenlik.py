"""Gizli yüklemeler (cari/aday aktivite ekleri, çek/senet görselleri) — özel depo + yetkili
indirme görünümleri. Prod nginx `/media/` yolunu KİMLİK DOĞRULAMASIZ sunar; bu dosyalar oraya
HİÇ konmaz: MEDIA_ROOT dışı özel depoda, UUID adıyla, yalnız ekran yetkisi olan kullanıcıya
Django görünümüyle akıtılır. Ayrıca projedeki HER FileField'ın ya özel depoda ya da açıkça
izinli genel klasörlerde (GENEL_MEDYA_ONEKLERI) olduğu denetlenir."""
import datetime
import io
import os
import stat
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import models
from django.test import SimpleTestCase
from django.urls import reverse
from PIL import Image

from core.models import Cari, CariAktiviteEk, CekSenet, EkranYetki, HesapPlani, Kur
from core.services.aday import (
    aday_aktivite_ek_ekle, aday_aktivite_ekle, aday_cariye_donustur, aday_musteri_olustur,
)
from core.services.cari import aktivite_ek_ekle, aktivite_ekle, cari_olustur
from core.services.cek import cari_giris_bordrosu_olustur, hesap_ayari_kaydet
from core.storage import GENEL_MEDYA_ONEKLERI, OZELE_TASINAN_ONEKLER, OzelDepo
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, png_bayt, yuklenen

UUID_YOL = r"^{onek}/[0-9a-f]{{32}}\.{uzanti}$"


def webp_bayt(boyut=(30, 20), renk=(20, 140, 60)):
    tampon = io.BytesIO()
    Image.new("RGB", boyut, renk).save(tampon, format="WEBP")
    return tampon.getvalue()


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO",
                                     rapor_kalemi="DV", parasal=True)


class OzelEkTemel(OzelDizinTestTemel):
    """Cari eki (PDF + resim), aday eki (PDF) ve çek görseli (ön yüz) olan bir veri seti."""

    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("ozyon", password="x")
        bugun = datetime.date(2026, 9, 1)
        # --- cari + aktivite + ekler ---
        self.cari = cari_olustur(unvan="gizli cari", para_birimi="TRY")
        self.cari_akt = aktivite_ekle(self.cari, tarih=bugun, tur="NOT", aciklama="sozlesme")
        self.cari_pdf = aktivite_ek_ekle(self.cari_akt, dosya=yuklenen("Sozlesme.pdf", PDF_BAYT))
        self.cari_png = aktivite_ek_ekle(self.cari_akt, dosya=yuklenen("foto.png", png_bayt()))
        # --- aday + aktivite + ek ---
        self.aday = aday_musteri_olustur(unvan="gizli aday", para_birimi="TRY")
        self.aday_akt = aday_aktivite_ekle(self.aday, tarih=bugun, tur="NOT", aciklama="teklif")
        self.aday_pdf = aday_aktivite_ek_ekle(
            self.aday_akt, dosya=yuklenen("Teklif.pdf", PDF_BAYT))
        # --- çek/senet bordrosu + ön yüz görseli ---
        Kur.objects.create(tarih=bugun, usd_alis=Decimal("40"))     # her fişin USD karşılığı şart
        _hesap("101.01", "ALINAN ÇEKLER")
        _hesap("121.01", "ALACAK SENETLERİ")
        _hesap("120.01", "ALICILAR")
        hesap_ayari_kaydet({"portfoy_cek": "101.01", "portfoy_senet": "121.01"},
                           kullanici=self.yon)
        self.cek_cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01")
        self.bordro = cari_giris_bordrosu_olustur(
            cari_id=self.cek_cari.pk, tarih=bugun, para_birimi="TRY",
            satirlar=[{"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 12, 1),
                       "on_yuz": ContentFile(webp_bayt(), name="cek_on.webp")}],
            kullanici=self.yon)
        self.cek = self.bordro.cek_senetler.get()

    def cek_gorsel_url(self, yuz="on", pk=None):
        return reverse("core:cek_gorsel", args=[pk or self.cek.pk, yuz])

    def hedefler(self):
        """(ad, url) — özel dosya sunan tüm görünümler."""
        return [
            ("cari pdf", reverse("core:cari_ek_indir", args=[self.cari_pdf.pk])),
            ("cari resim", reverse("core:cari_ek_indir", args=[self.cari_png.pk])),
            ("aday pdf", reverse("core:aday_ek_indir", args=[self.aday_pdf.pk])),
            ("cek on", self.cek_gorsel_url("on")),
        ]


class OzelDepoTest(OzelEkTemel):
    def test_dosyalar_ozel_dizinde_uuid_adli_medya_kokunde_degil(self):
        beklenen = ((self.cari_pdf.dosya.name, "cari_aktivite", "pdf"),
                    (self.cari_png.dosya.name, "cari_aktivite", "webp"),
                    (self.aday_pdf.dosya.name, "aday_aktivite", "pdf"),
                    (self.cek.on_yuz.name, "cek_senet", "webp"))
        for ad, onek, uzanti in beklenen:
            self.assertRegex(ad, UUID_YOL.format(onek=onek, uzanti=uzanti))
            self.assertTrue((self.ozel / ad).is_file(), ad)
            self.assertFalse((self.medya / ad).exists(), ad)
        self.assertEqual([p for p in self.medya.rglob("*") if p.is_file()], [])
        self.assertTrue(isinstance(self.cari_pdf.dosya.storage, OzelDepo))

    def test_orijinal_ad_yalniz_kolonda(self):
        self.assertEqual(self.cari_pdf.orijinal_ad, "Sozlesme.pdf")
        self.assertNotIn("Sozlesme", self.cari_pdf.dosya.name)

    def test_url_uretmez(self):
        for alan in (self.cari_pdf.dosya, self.aday_pdf.dosya, self.cek.on_yuz):
            with self.assertRaises(ValueError):
                alan.url

    def test_izinler_yalniz_sahibine(self):
        if os.name != "posix":
            self.skipTest("POSIX izinleri")
        for ad in (self.cari_pdf.dosya.name, self.aday_pdf.dosya.name, self.cek.on_yuz.name):
            self.assertEqual(stat.S_IMODE((self.ozel / ad).stat().st_mode), 0o600, ad)
        for onek in OZELE_TASINAN_ONEKLER:
            self.assertEqual(stat.S_IMODE((self.ozel / onek).stat().st_mode), 0o700, onek)


class IndirmeYetkiTest(OzelEkTemel):
    def test_anonim_girise_yonlendirilir(self):
        for etiket, url in self.hedefler():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, etiket)
            self.assertIn("/login/", r["Location"], etiket)

    def test_ekran_yetkisiz_403(self):
        kisit = User.objects.create_user("kisit", password="x")
        EkranYetki.objects.create(kullanici=kisit, ekran_kod="mizan")
        self.client.force_login(kisit)
        for etiket, url in self.hedefler():
            self.assertEqual(self.client.get(url).status_code, 403, etiket)

    def test_ekranlar_birbirinin_dosyasini_acamaz(self):
        # Her dosya yalnız KENDİ ekran yetkisiyle: cariler / aday_musteriler / cek_senet.
        harita = {"cariler": {"cari pdf", "cari resim"}, "aday_musteriler": {"aday pdf"},
                  "cek_senet": {"cek on"}}
        for kod, izinli in harita.items():
            u = User.objects.create_user(f"u_{kod}", password="x")
            EkranYetki.objects.create(kullanici=u, ekran_kod=kod)
            self.client.force_login(u)
            for etiket, url in self.hedefler():
                self.assertEqual(self.client.get(url).status_code,
                                 200 if etiket in izinli else 403, f"{kod} → {etiket}")

    def test_yetkili_icerik_ve_guvenlik_basliklari(self):
        self.client.force_login(self.yon)
        beklenen = {
            "cari pdf": ("application/pdf", PDF_BAYT, "Sozlesme.pdf"),
            "aday pdf": ("application/pdf", PDF_BAYT, "Teklif.pdf"),
            "cari resim": ("image/webp", None, "foto.webp"),        # png → webp'ye çevrilmişti
            "cek on": ("image/webp", None, f"cek-{self.cek.pk}-on.webp"),
        }
        for etiket, url in self.hedefler():
            tur, icerik, ad = beklenen[etiket]
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, etiket)
            self.assertEqual(r["Content-Type"], tur, etiket)
            govde = b"".join(r.streaming_content)
            if icerik is not None:
                self.assertEqual(govde, icerik, etiket)
            else:
                self.assertEqual(Image.open(io.BytesIO(govde)).format, "WEBP", etiket)
            self.assertIn("no-store", r["Cache-Control"], etiket)
            self.assertIn("private", r["Cache-Control"], etiket)
            self.assertEqual(r["X-Content-Type-Options"], "nosniff", etiket)
            self.assertTrue(r["Content-Disposition"].startswith("inline"), etiket)
            self.assertIn(ad, r["Content-Disposition"], etiket)

    def test_silinmis_kayit_404(self):
        self.client.force_login(self.yon)
        CariAktiviteEk.objects.filter(pk=self.cari_pdf.pk).update(silindi=True)
        self.assertEqual(self.client.get(
            reverse("core:cari_ek_indir", args=[self.cari_pdf.pk])).status_code, 404)
        CekSenet.objects.filter(pk=self.cek.pk).update(silindi=True)
        self.assertEqual(self.client.get(self.cek_gorsel_url()).status_code, 404)

    def test_silinmis_aktivite_ya_da_ust_kayit_404(self):
        self.client.force_login(self.yon)
        cari_url = reverse("core:cari_ek_indir", args=[self.cari_png.pk])
        aday_url = reverse("core:aday_ek_indir", args=[self.aday_pdf.pk])
        self.assertEqual(self.client.get(cari_url).status_code, 200)
        self.cari_akt.__class__.objects.filter(pk=self.cari_akt.pk).update(silindi=True)
        self.assertEqual(self.client.get(cari_url).status_code, 404)         # aktivite silindi
        Cari.objects.filter(pk=self.cari.pk).update(silindi=True)
        self.assertEqual(self.client.get(
            reverse("core:cari_ek_indir", args=[self.cari_pdf.pk])).status_code, 404)  # cari silindi
        self.assertEqual(self.client.get(aday_url).status_code, 200)
        self.aday.__class__.objects.filter(pk=self.aday.pk).update(silindi=True)
        self.assertEqual(self.client.get(aday_url).status_code, 404)         # aday silindi

    def test_diskte_olmayan_dosya_404(self):
        self.client.force_login(self.yon)
        (self.ozel / self.cari_pdf.dosya.name).unlink()
        self.assertEqual(self.client.get(
            reverse("core:cari_ek_indir", args=[self.cari_pdf.pk])).status_code, 404)

    def test_beyaz_liste_disi_uzanti_404(self):
        # İçerik türü DEPOLANAN uzantıdan (beyaz liste) belirlenir; tanınmayan uzantı sunulmaz.
        self.client.force_login(self.yon)
        yol = self.ozel / "cari_aktivite" / ("a" * 32 + ".html")
        yol.write_bytes(b"<script>alert(1)</script>")
        ek = CariAktiviteEk.objects.create(
            aktivite=self.cari_akt, dosya=f"cari_aktivite/{yol.name}", orijinal_ad="x.html")
        self.assertEqual(self.client.get(
            reverse("core:cari_ek_indir", args=[ek.pk])).status_code, 404)

    def test_cek_gorsel_gecersiz_yuz_yok_gorsel_ve_bilinmeyen_pk_404(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(self.cek_gorsel_url("yan")).status_code, 404)
        self.assertEqual(self.client.get(self.cek_gorsel_url("arka")).status_code, 404)  # yüklenmedi
        self.assertEqual(self.client.get(self.cek_gorsel_url("on", pk=999999)).status_code, 404)
        self.assertEqual(self.client.get(
            reverse("core:cari_ek_indir", args=[999999])).status_code, 404)


class SayfaEntegrasyonTest(OzelEkTemel):
    def test_sayfalar_ozel_url_kullanir_media_yolu_ve_depolanan_ad_yok(self):
        self.client.force_login(self.yon)
        sayfalar = {
            reverse("core:cari_detay", args=[self.cari.pk]): [
                reverse("core:cari_ek_indir", args=[self.cari_pdf.pk]),
                reverse("core:cari_ek_indir", args=[self.cari_png.pk])],
            reverse("core:aktivite_duzenle", args=[self.cari_akt.pk]): [
                reverse("core:cari_ek_indir", args=[self.cari_pdf.pk])],
            reverse("core:aday_musteri_detay", args=[self.aday.pk]): [
                reverse("core:aday_ek_indir", args=[self.aday_pdf.pk])],
            reverse("core:aday_aktivite_duzenle", args=[self.aday_akt.pk]): [
                reverse("core:aday_ek_indir", args=[self.aday_pdf.pk])],
            reverse("core:cek_bordro_detay", args=[self.bordro.pk]): [self.cek_gorsel_url("on")],
        }
        depolanan = [os.path.basename(a.name) for a in (
            self.cari_pdf.dosya, self.cari_png.dosya, self.aday_pdf.dosya, self.cek.on_yuz)]
        for url, ozel_urller in sayfalar.items():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            html = r.content.decode()
            for ozel in ozel_urller:
                self.assertIn(ozel, html, f"{url} → {ozel}")
            self.assertNotIn("/media/", html, url)
            for ad in depolanan:
                self.assertNotIn(ad, html, url)                      # UUID ad sızmaz

    def test_adaydan_cariye_donusum_ekler_ozel_depoda_indirilebilir(self):
        cari = aday_cariye_donustur(self.aday)
        (yeni_ek,) = CariAktiviteEk.objects.filter(aktivite__cari=cari, silindi=False)
        self.assertRegex(yeni_ek.dosya.name, UUID_YOL.format(onek="cari_aktivite", uzanti="pdf"))
        self.assertNotEqual(yeni_ek.dosya.name, self.aday_pdf.dosya.name)   # ayrı kopya
        self.assertTrue((self.ozel / yeni_ek.dosya.name).is_file())
        self.assertFalse((self.medya / yeni_ek.dosya.name).exists())
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cari_ek_indir", args=[yeni_ek.pk]))
        self.assertEqual(b"".join(r.streaming_content), PDF_BAYT)


class TumDosyaAlanlariTest(SimpleTestCase):
    """Yeni bir FileField eklenirse KARAR verilmek zorunda: özel depo mu, açıkça genel mi?"""

    def dosya_alanlari(self):
        for model in apps.get_app_config("core").get_models():
            for alan in model._meta.get_fields():
                if isinstance(alan, models.FileField):          # ImageField dahil
                    yield model, alan

    def test_her_dosya_alani_ozel_depoda_ya_da_acikca_genel(self):
        gorulen = 0
        for model, alan in self.dosya_alanlari():
            gorulen += 1
            etiket = f"{model.__name__}.{alan.name}"
            if isinstance(alan.storage, OzelDepo):
                continue
            self.assertIsInstance(
                alan.upload_to, str, f"{etiket}: özel depo dışında callable upload_to olmaz")
            onek = alan.upload_to.strip("/").split("/")[0]
            self.assertIn(
                onek, GENEL_MEDYA_ONEKLERI,
                f"{etiket}: özel depoda değil ve '{onek}/' genel izin listesinde yok — hassas "
                "dosya nginx /media/ üzerinden girişsiz sunulur. Özel depoya alın "
                "(storage=ozel_depo) ya da bilinçli olarak GENEL_MEDYA_ONEKLERI'ne ekleyin.")
        self.assertGreaterEqual(gorulen, 9)                     # tarama gerçekten çalıştı

    def test_tasinan_klasorler_genel_listede_degil(self):
        self.assertEqual(set(GENEL_MEDYA_ONEKLERI) & set(OZELE_TASINAN_ONEKLER), set())
        for model, alan in self.dosya_alanlari():
            if isinstance(alan.storage, OzelDepo):
                continue
            self.assertNotIn(alan.upload_to.strip("/").split("/")[0], OZELE_TASINAN_ONEKLER)

    def test_cari_aday_cek_alanlari_ozel(self):
        ozel = {(m.__name__, a.name) for m, a in self.dosya_alanlari()
                if isinstance(a.storage, OzelDepo)}
        for beklenen in (("CariAktiviteEk", "dosya"), ("AdayAktiviteEk", "dosya"),
                         ("CekSenet", "on_yuz"), ("CekSenet", "arka_yuz"),
                         ("PersonelBelge", "dosya"), ("Personel", "foto")):
            self.assertIn(beklenen, ozel)
