"""tasi_ozel_dosyalar: MEDIA_ROOT → özel depo veri taşıma (ve geri alma).

Kaynak dizinler geçici MEDIA_ROOT'ta eski (herkese açık) yerleşimi taklit eder. Doğrulanan
sözleşme: göreli yollar aynı kalır → DB satırlarına DOKUNULMAZ; kopya SHA-256 ile doğrulanır;
kaynak yalnız doğrulanmış kopyadan sonra ve --sil ile silinir; hedefte farklı içerik ASLA
ezilmez; tekrar çalıştırılabilir; genel klasörlere (stok/banka/firma) dokunulmaz."""
import hashlib
import os
import shutil
import stat
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from core.models import CariAktiviteEk
from core.services import ozel_tasima
from core.services.cari import aktivite_ekle, cari_olustur
from core.tests.ik_yardimci import OzelDizinTestTemel


def sha(yol):
    return hashlib.sha256(yol.read_bytes()).hexdigest()


class TasimaTemel(OzelDizinTestTemel):
    """self.medya = eski (genel) kök, self.ozel = yeni (özel) kök."""

    def setUp(self):
        super().setUp()
        self.icerik = {
            "cari_aktivite/eski1.webp": b"RIFF-cari-1",
            "cari_aktivite/eski2.pdf": b"%PDF-cari-2",
            "aday_aktivite/aday1.webp": b"RIFF-aday-1",
            "cek_senet/cek_on.webp": b"RIFF-cek-on",
            "cek_senet/cek_on_ABCDEFG.webp": b"RIFF-cek-on-2",
        }
        for goreli, veri in self.icerik.items():
            yol = self.medya / goreli
            yol.parent.mkdir(parents=True, exist_ok=True)
            yol.write_bytes(veri)
        # Genel klasörler: taşımaya KONU DEĞİL.
        for goreli in ("stok_gorsel/a21.webp", "banka_logo/ziraat.webp", "firma_logo/logo.webp"):
            yol = self.medya / goreli
            yol.parent.mkdir(parents=True, exist_ok=True)
            yol.write_bytes(b"GENEL-" + goreli.encode())

    def komut(self, *args):
        cikti = StringIO()
        call_command("tasi_ozel_dosyalar", *args, stdout=cikti)
        return cikti.getvalue()

    def genel_dosyalar(self):
        return sorted(str(p.relative_to(self.medya)) for p in self.medya.rglob("*") if p.is_file())


class TasimaServisTest(TasimaTemel):
    def test_kopyalar_dogrular_kaynagi_silmez(self):
        s = ozel_tasima.tasi()
        self.assertTrue(s.temiz)
        self.assertEqual((s.kopyalanan, s.zaten_var, s.silinen), (5, 0, 0))
        self.assertEqual(s.bayt, sum(len(v) for v in self.icerik.values()))
        for goreli, veri in self.icerik.items():
            self.assertEqual((self.ozel / goreli).read_bytes(), veri, goreli)
            self.assertEqual(sha(self.ozel / goreli), sha(self.medya / goreli), goreli)
            self.assertTrue((self.medya / goreli).exists(), goreli)        # kaynak durur

    def test_sil_dogrulanmis_kaynagi_siler_bos_dizinleri_kaldirir(self):
        s = ozel_tasima.tasi(sil=True)
        self.assertTrue(s.temiz)
        self.assertEqual((s.kopyalanan, s.silinen), (5, 5))
        for goreli, veri in self.icerik.items():
            self.assertFalse((self.medya / goreli).exists(), goreli)
            self.assertEqual((self.ozel / goreli).read_bytes(), veri, goreli)
        for onek in ("cari_aktivite", "aday_aktivite", "cek_senet"):
            self.assertFalse((self.medya / onek).exists(), onek)           # boşalan dizin gitti

    def test_genel_klasorlere_dokunmaz(self):
        onceki = {g: (self.medya / g).read_bytes() for g in self.genel_dosyalar()
                  if g.split("/")[0] in ("stok_gorsel", "banka_logo", "firma_logo")}
        ozel_tasima.tasi(sil=True)
        for g, veri in onceki.items():
            self.assertEqual((self.medya / g).read_bytes(), veri, g)
        self.assertEqual([g for g in self.ozel.rglob("*") if g.is_file() and
                          g.relative_to(self.ozel).parts[0] in
                          ("stok_gorsel", "banka_logo", "firma_logo")], [])

    def test_izinler_ozel_kok_altinda_0600_0700(self):
        if os.name != "posix":
            self.skipTest("POSIX izinleri")
        ozel_tasima.tasi()
        for goreli in self.icerik:
            self.assertEqual(stat.S_IMODE((self.ozel / goreli).stat().st_mode), 0o600, goreli)
        for onek in ("cari_aktivite", "aday_aktivite", "cek_senet"):
            self.assertEqual(stat.S_IMODE((self.ozel / onek).stat().st_mode), 0o700, onek)

    def test_kuru_calisma_hicbir_sey_yazmaz_silmez(self):
        s = ozel_tasima.tasi(kuru=True, sil=True)
        self.assertTrue(s.temiz)
        self.assertEqual((s.kopyalanan, s.silinen), (5, 0))
        self.assertEqual([p for p in self.ozel.rglob("*") if p.is_file()], [])
        for goreli in self.icerik:
            self.assertTrue((self.medya / goreli).exists(), goreli)

    def test_tekrar_calistirilabilir_idempotent(self):
        ozel_tasima.tasi()
        s = ozel_tasima.tasi()
        self.assertEqual((s.kopyalanan, s.zaten_var), (0, 5))
        s = ozel_tasima.tasi(sil=True)                     # ayrı adımda silme (önce kopya vardı)
        self.assertEqual((s.kopyalanan, s.zaten_var, s.silinen), (0, 5, 5))
        s = ozel_tasima.tasi(sil=True)                     # kaynak kalmadı → yapacak iş yok
        self.assertEqual((s.kopyalanan, s.zaten_var, s.silinen), (0, 0, 0))
        self.assertTrue(s.temiz)

    def test_hedefte_farkli_icerik_ezilmez_kaynak_silinmez(self):
        cakisan = self.ozel / "cari_aktivite" / "eski1.webp"
        cakisan.parent.mkdir(parents=True)
        cakisan.write_bytes(b"BASKA-ICERIK")
        s = ozel_tasima.tasi(sil=True)
        self.assertFalse(s.temiz)
        self.assertEqual(s.catisma, ["cari_aktivite/eski1.webp"])
        self.assertEqual(cakisan.read_bytes(), b"BASKA-ICERIK")             # ezilmedi
        self.assertTrue((self.medya / "cari_aktivite/eski1.webp").exists())  # silinmedi
        self.assertEqual(s.silinen, 4)                                     # diğerleri taşındı

    def test_symlink_atlanir_izlenmez(self):
        if os.name != "posix":
            self.skipTest("symlink")
        hedef_disari = self.ozel.parent / "disari_hassas.txt"
        hedef_disari.write_text("dis dosya")
        self.addCleanup(hedef_disari.unlink)
        (self.medya / "cari_aktivite" / "link.pdf").symlink_to(hedef_disari)
        s = ozel_tasima.tasi(sil=True)
        self.assertEqual(s.atlanan, ["cari_aktivite/link.pdf"])
        self.assertFalse((self.ozel / "cari_aktivite" / "link.pdf").exists())
        self.assertTrue((self.medya / "cari_aktivite" / "link.pdf").is_symlink())   # silinmedi
        self.assertTrue(hedef_disari.exists())

    def test_alt_dizin_yapisi_korunur(self):
        yol = self.medya / "cek_senet" / "2026" / "09" / "x.webp"
        yol.parent.mkdir(parents=True)
        yol.write_bytes(b"ic-ice")
        ozel_tasima.tasi(sil=True)
        self.assertEqual((self.ozel / "cek_senet/2026/09/x.webp").read_bytes(), b"ic-ice")
        self.assertFalse((self.medya / "cek_senet").exists())

    def test_geri_yon_ozelden_genele_kopyalar(self):
        ozel_tasima.tasi(sil=True)
        s = ozel_tasima.tasi(geri=True, sil=True)
        self.assertTrue(s.temiz)
        self.assertEqual((s.kopyalanan, s.silinen), (5, 5))
        for goreli, veri in self.icerik.items():
            self.assertEqual((self.medya / goreli).read_bytes(), veri, goreli)
            self.assertFalse((self.ozel / goreli).exists(), goreli)
        if os.name == "posix":                                # nginx okuyabilsin
            self.assertEqual(stat.S_IMODE((self.medya / "cari_aktivite/eski1.webp").stat().st_mode),
                             0o644)

    def test_ozel_dizin_medya_altindaysa_reddedilir(self):
        with override_settings(IK_OZEL_DIR=self.medya / "ozel"):
            with self.assertRaises(ozel_tasima.OzelTasimaHatasi):
                ozel_tasima.tasi()

    def test_ana_dizinler_yoksa_sorunsuz(self):
        for onek in ("cari_aktivite", "aday_aktivite", "cek_senet"):
            shutil.rmtree(self.medya / onek)
        s = ozel_tasima.tasi(sil=True)
        self.assertTrue(s.temiz)
        self.assertEqual((s.kopyalanan, s.zaten_var, s.silinen), (0, 0, 0))


class TasimaDbTest(TasimaTemel):
    def kayit_kur(self):
        """DB'de eski (herkese açık dönemden kalma) göreli yolla kayıtlı bir cari eki."""
        cari = cari_olustur(unvan="eski cari", para_birimi="TRY")
        akt = aktivite_ekle(cari, tarih="2026-09-01", tur="NOT", aciklama="x")
        return CariAktiviteEk.objects.create(
            aktivite=akt, dosya="cari_aktivite/eski1.webp", orijinal_ad="eski.png")

    def test_db_satirlarina_dokunulmaz_tasindiktan_sonra_da_acilir(self):
        ek = self.kayit_kur()
        onceki = list(CariAktiviteEk.objects.values())
        ozel_tasima.tasi(sil=True)
        self.assertEqual(list(CariAktiviteEk.objects.values()), onceki)     # updated_at dahil aynı
        ek.refresh_from_db()
        with ek.dosya.open("rb") as f:                                      # özel depodan okur
            self.assertEqual(f.read(), b"RIFF-cari-1")

    def test_eksik_kayitlar_raporlanir(self):
        ek = self.kayit_kur()
        CariAktiviteEk.objects.create(aktivite=ek.aktivite, dosya="cari_aktivite/yok.webp",
                                      orijinal_ad="yok.png")
        # Taşımadan ÖNCE hedefte (özel kök) hiçbiri yok.
        self.assertEqual(len(ozel_tasima.eksik_kayitlar()), 2)
        ozel_tasima.tasi()
        eksik = ozel_tasima.eksik_kayitlar()
        self.assertEqual([(e[0], e[2]) for e in eksik],
                         [("CariAktiviteEk.dosya", "cari_aktivite/yok.webp")])


class TasimaKomutTest(TasimaTemel):
    def test_varsayilan_yalniz_kopyalar(self):
        cikti = self.komut()
        self.assertIn("5 kopyalandı", cikti)
        self.assertIn("tasi_ozel_dosyalar tamam.", cikti)
        self.assertTrue((self.medya / "cari_aktivite/eski1.webp").exists())
        self.assertTrue((self.ozel / "cari_aktivite/eski1.webp").exists())

    def test_kuru_ve_sil_bayraklari(self):
        cikti = self.komut("--kuru")
        self.assertIn("KURU ÇALIŞMA", cikti)
        self.assertIn("5 kopyalanacak", cikti)
        self.assertEqual([p for p in self.ozel.rglob("*") if p.is_file()], [])
        cikti = self.komut("--sil")
        self.assertIn("5 kaynak silindi", cikti)
        self.assertFalse((self.medya / "cari_aktivite").exists())

    def test_geri_bayragi(self):
        self.komut("--sil")
        cikti = self.komut("--geri", "--sil")
        self.assertIn("özel depo → MEDIA_ROOT", cikti)
        self.assertTrue((self.medya / "cari_aktivite/eski1.webp").exists())
        self.assertFalse((self.ozel / "cari_aktivite/eski1.webp").exists())

    def test_cakismada_komut_hata_verir(self):
        cakisan = self.ozel / "aday_aktivite" / "aday1.webp"
        cakisan.parent.mkdir(parents=True)
        cakisan.write_bytes(b"BASKA")
        with self.assertRaises(CommandError) as ctx:
            self.komut("--sil")
        self.assertIn("1 çakışma", str(ctx.exception))
        self.assertTrue((self.medya / "aday_aktivite/aday1.webp").exists())    # dokunulmadı
