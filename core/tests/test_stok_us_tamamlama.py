"""Kaynak siparişi OLMAYAN ("stok için üretim") ÜS: tüm istasyon emirleri bitince (%100) otomatik TAMAMLANIR (KAPALI) ve TÜM ayırmalar serbest kalır;
siparişe bağlı ÜS'nin kapanışı değişmez (depolu satış faturası); tamamlanmış stok ÜS'sinde kayıt geri alınırsa ÜS yeniden AÇILIR, mamul ayırması geri
GELMEZ, yalnız kalan üretimin ham madde ayırması kurulur."""
from decimal import Decimal

from django.urls import reverse

from core.models import UretimEmri
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import eldeki_miktar
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class StokUsTamamlamaTest(kt.KapanisTaban):
    def stok_us(self, miktar="3"):
        return uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": miktar}], depo_id=self.depo.pk, tarih=kt.GUN)

    def kayit_onayla(self, emir, hedef=None):
        ie = emir.istasyon_emirleri.get()
        k = uretim.istasyon_emri_kayit_ac(ie, hedef=hedef) if hedef else uretim.istasyon_emri_kayit_ac(ie)
        return uretim.operasyon_kaydi_onayla(k)

    def test_stok_us_yuzde_yuz_kapanir_ve_tum_ayirmalar_serbest(self):
        emir = self.stok_us("3")
        self.assertEqual(sa.ayrilan_miktar(emir, self.ham), D("3"))                                   # ham madde ayrıldı
        self.kayit_onayla(emir)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.kapanis_tarihi is not None), (UretimEmri.Durum.KAPALI, True))
        self.assertEqual(list(sa.emir_ayirmalari(emir)), [])                                          # mamul + ham ayırması yok
        self.assertEqual((sa.kullanilabilir(self.stok), eldeki_miktar(self.stok)), (D("3"), D("3")))   # üretilen mamul serbest
        self.assertEqual(sa.kullanilabilir(self.ham), D("97"))
        rev = emir.revizyonlar.order_by("-no").first()
        self.assertEqual((rev.tur, rev.detay["serbest_birakilan"]), ("KAPANIS", {self.stok.kod: "3"}))   # serbest kalan mamul ayırması (ham tüketilmişti)
        self.assertEqual(uretim.uretim_emri_yuzde(emir), D("100"))
        self.assertContains(self.client.get(reverse("core:uretim_emirleri")), "Tamamlandı")

    def test_serbest_birakilan_ayirmalar_revizyon_detayinda(self):
        emir = self.stok_us("3")
        k = uretim.istasyon_emri_kayit_ac(emir.istasyon_emirleri.get())
        # mamul ayırması onayda kurulur; tamamlanırken serbest bırakılanlar revizyona yazılır
        uretim.operasyon_kaydi_onayla(k)
        emir.refresh_from_db()
        rev = emir.revizyonlar.order_by("-no").first()
        self.assertIn("Tamamlandı", rev.aciklama)
        self.assertIn("serbest_birakilan", rev.detay)

    def test_kismi_uretimde_kapanmaz_ayirmalar_kalir(self):
        emir = self.stok_us("3")
        self.kayit_onayla(emir, hedef=D("1"))
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ACIK)
        self.assertGreater(sa.ayrilan_miktar(emir, self.stok), 0)
        self.assertEqual(sa.kullanilabilir(self.stok), D("0"))                                        # mamul hâlâ ÜS'ye ayrılı
        self.kayit_onayla(emir, hedef=D("2"))
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        self.assertEqual(sa.kullanilabilir(self.stok), D("3"))

    def test_siparisli_us_yuzde_yuzde_kapanmaz(self):
        emir = self.emir_ac(self.siparis("3"))
        self.uret(emir)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ACIK)                                           # kapanış = depolu satış faturası
        self.assertEqual(sa.ayrilan_miktar(emir, self.stok), D("3"))
        self.assertEqual(sa.kullanilabilir(self.stok), D("0"))

    def test_geri_alma_yeniden_acar_mamul_ayirmasi_gelmez_ham_ayirmasi_kurulur(self):
        emir = self.stok_us("3")
        kayit = self.kayit_onayla(emir)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        uretim.operasyon_kaydi_sil(kayit, onayli_geri_al=True)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.kapanis_tarihi), (UretimEmri.Durum.ACIK, None))
        self.assertEqual(sa.ayrilan_miktar(emir, self.stok), D("0"))                                  # mamul ayırması kendiliğinden geri gelmedi
        self.assertEqual(sa.ayrilan_miktar(emir, self.ham), D("3"))                                   # kalan üretimin ham madde ayırması kuruldu
        self.assertEqual((eldeki_miktar(self.stok), eldeki_miktar(self.ham)), (D("0"), D("100")))
        self.assertEqual(emir.revizyonlar.order_by("-no").first().tur, "YENIDEN_ACILIS")
        self.assertEqual(emir.istasyon_emirleri.get().tamamlanan, D("0"))
        self.kayit_onayla(emir)                                                                       # yeniden üretilince tekrar tamamlanır
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        self.assertEqual(list(sa.emir_ayirmalari(emir)), [])


class StokUsTamamlaKomutuTest(kt.KapanisTaban):
    def eski_durum_us(self):
        """Düzeltme öncesi canlı durum: %100 ama ACIK + mamul ayırması duruyor (eski kural)."""
        emir = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "3"}], depo_id=self.depo.pk, tarih=kt.GUN)
        uretim.operasyon_kaydi_onayla(uretim.istasyon_emri_kayit_ac(emir.istasyon_emirleri.get()))
        UretimEmri.objects.filter(pk=emir.pk).update(durum=UretimEmri.Durum.ACIK, kapanis_tarihi=None)
        sa.ayirma_ayarla(emir, self.stok, D("3"))
        emir.refresh_from_db()
        return emir

    def test_komut_kuru_calistirma_geri_alir_uygula_tamamlar(self):
        from io import StringIO
        from django.core.management import call_command
        emir = self.eski_durum_us()
        self.assertEqual(sa.kullanilabilir(self.stok), D("0"))                                        # mamul kilitli
        cikti = StringIO()
        call_command("stok_us_tamamla", "--emir", emir.no, stdout=cikti)
        self.assertIn("KURU ÇALIŞTIRMA", cikti.getvalue())
        self.assertIn("serbest bırakılacak ayırma", cikti.getvalue())
        emir.refresh_from_db()
        self.assertEqual((emir.durum, sa.kullanilabilir(self.stok)), (UretimEmri.Durum.ACIK, D("0")))  # hiçbir şey yazılmadı
        call_command("stok_us_tamamla", "--emir", emir.no, "--uygula", stdout=StringIO())
        emir.refresh_from_db()
        self.assertEqual((emir.durum, sa.kullanilabilir(self.stok)), (UretimEmri.Durum.KAPALI, D("3")))

    def test_siparisli_us_reddedilir_ve_yuzde_yuz_olmayan_tamamlanmaz(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        sip_emir = self.emir_ac(self.siparis("2"))
        with self.assertRaisesMessage(CommandError, "kaynak satış siparişi var"):
            call_command("stok_us_tamamla", "--emir", sip_emir.no, stdout=StringIO())
        yarim = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=kt.GUN)
        with self.assertRaisesMessage(CommandError, "%100 değil"):
            call_command("stok_us_tamamla", "--emir", yarim.no, "--uygula", stdout=StringIO())
        yarim.refresh_from_db()
        self.assertEqual(yarim.durum, UretimEmri.Durum.ACIK)
