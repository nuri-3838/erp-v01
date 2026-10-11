"""Numune Çıkışı (NC-yyyy-nnnn): onayda stok ortalama maliyetle düşer + TEK fiş (760.06 B / stok hesabı A); yetersiz/ayrılmış stokta açık hata ve rollback;
geri almada stok ve fiş geri döner; fatura/KDV yok; fişten elle düzenleme/silme engelli; ekranlar."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import HesapPlani, Kur, NumuneCikis, StokHareket, YevmiyeFisi
from core.services import kategori as kategori_servis
from core.services import numune as ns
from core.services import stok_ayirma as sa
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.tests import test_uretim_siparisi_kapanis as kt
from core.tests.test_banka_hesap_hareketi import _hesap

D = Decimal
TARIH = date(2026, 10, 10)


class NumuneTaban(kt.KapanisTaban):
    def setUp(self):
        super().setUp()
        _hesap("760.06", "NUMUNE GİDERLERİ", kalem="A", grup="GELIR_TABLOSU")
        Kur.objects.get_or_create(tarih=TARIH, defaults={"usd_alis": D("40")})
        self.stok_hesap = kategori_servis.stok_muhasebe_hesabi(self.ham).hesap_kodu       # ham 100 adet / 1000 TL (ort. 10)

    def nc(self, miktar="4", stok=None, **ek):
        kw = dict(tarih=TARIH, depo_id=self.depo.pk, alici_adi="Gökray", satirlar=[{"stok_id": (stok or self.ham).pk, "miktar": miktar}], kullanici=self.yon)
        kw.update(ek)
        return ns.numune_olustur(**kw)

    def fis_satirlari(self, fis):
        return sorted((s.hesap.hesap_kodu, s.borc, s.alacak) for s in fis.satirlar.all())


class NumuneOnayTest(NumuneTaban):
    def test_olustur_taslak_stok_dusmez_belge_no_ve_alici_zorunlu(self):
        nc = self.nc()
        self.assertEqual((nc.no, nc.durum, nc.fis_id), ("NC-2026-0001", NumuneCikis.Durum.TASLAK, None))
        self.assertEqual(eldeki_miktar(self.ham), D("100"))
        self.assertEqual(self.nc().no, "NC-2026-0002")
        with self.assertRaisesMessage(ns.NumuneHatasi, "Alıcı"):
            self.nc(alici_adi="", cari_id=None)
        self.assertEqual(self.nc(alici_adi="", cari_id=self.musteri.pk).alici, self.musteri.unvan)         # cari yeterli

    def test_onay_stok_duser_fis_760_06_ve_stok_hesabi(self):
        nc = ns.numune_onayla(self.nc("4"), kullanici=self.yon)
        self.assertEqual(nc.durum, NumuneCikis.Durum.ONAYLI)
        self.assertEqual(eldeki_miktar(self.ham), D("96"))
        s = nc.satirlar.get()
        self.assertEqual(s.maliyet_try, D("40.00"))                                                       # 4 × ortalama 10
        self.assertEqual(self.fis_satirlari(nc.fis), sorted([("760.06", D("40.00"), D("0.00")), (self.stok_hesap, D("0.00"), D("40.00"))]))
        self.assertEqual(nc.fis.kaynak, YevmiyeFisi.Kaynak.NUMUNE)
        h = s.hareket
        self.assertEqual((h.kaynak, h.tur, h.tarih), (StokHareket.Kaynak.NUMUNE, "CIKIS", TARIH))
        self.assertIn("NC-2026-0001", h.aciklama)
        self.assertEqual(ns.numune_onayla(nc).fis_id, nc.fis_id)                                          # idempotent
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.NUMUNE).count(), 1)

    def test_yetersiz_stok_hicbir_sey_kaydedilmez(self):
        nc = self.nc("101")
        n_hareket, n_fis = StokHareket.objects.count(), YevmiyeFisi.objects.count()
        with self.assertRaisesMessage(ns.NumuneHatasi, "Yetersiz stok"):
            ns.numune_onayla(nc)
        self.assertEqual((StokHareket.objects.count(), YevmiyeFisi.objects.count()), (n_hareket, n_fis))
        nc.refresh_from_db()
        self.assertEqual((nc.durum, nc.fis_id, eldeki_miktar(self.ham)), (NumuneCikis.Durum.TASLAK, None, D("100")))

    def test_ikinci_satir_yetersizse_birinci_satir_da_geri_alinir(self):
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("1"), giris_tutar_try=D("50"))
        nc = self.nc(satirlar=[{"stok_id": self.ham.pk, "miktar": "5"}, {"stok_id": self.stok.pk, "miktar": "2"}])
        with self.assertRaises(ns.NumuneHatasi):
            ns.numune_onayla(nc)
        self.assertEqual((eldeki_miktar(self.ham), eldeki_miktar(self.stok), YevmiyeFisi.objects.filter(kaynak="NUMUNE").count()), (D("100"), D("1"), 0))

    def test_ayrilan_stok_numuneye_verilmez(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)                                                                          # 3 hammadde üretim siparişine ayrılır
        ayrilan = sa.ayrilan_miktar(emir, self.ham)
        self.assertGreater(ayrilan, 0)
        serbest = D("100") - ayrilan
        with self.assertRaisesMessage(ns.NumuneHatasi, "ayrılan"):
            ns.numune_onayla(self.nc(str(serbest + 1)))
        nc = ns.numune_onayla(self.nc(str(serbest)))                                                      # serbest kadarı verilir
        self.assertEqual(nc.durum, "ONAYLI")
        self.assertEqual(sa.ayrilan_miktar(emir, self.ham), ayrilan)                                      # ayırmaya dokunulmadı

    def test_geri_alma_stok_ve_fis_geri_doner_tekrar_onaylanir(self):
        nc = ns.numune_onayla(self.nc("4"), kullanici=self.yon)
        fis = nc.fis
        nc = ns.numune_geri_al(nc, kullanici=self.yon)
        fis.refresh_from_db()
        self.assertEqual((nc.durum, nc.fis_id, fis.silindi, eldeki_miktar(self.ham)), ("TASLAK", None, True, D("100")))
        self.assertIsNone(nc.satirlar.get().hareket_id)
        nc = ns.numune_onayla(nc)                                                                         # tekrar onay: yeni fiş, stok yine düşer
        self.assertEqual((nc.durum, eldeki_miktar(self.ham), nc.satirlar.get().maliyet_try), ("ONAYLI", D("96"), D("40.00")))

    def test_onayli_duzenlenmez_silinmez_taslak_guncellenir(self):
        nc = self.nc("4")
        ns.numune_guncelle(nc, tarih=TARIH, depo_id=self.depo.pk, alici_adi="X", satirlar=[{"stok_id": self.ham.pk, "miktar": "7"}])
        self.assertEqual(nc.satirlar.filter(silindi=False).get().miktar, D("7"))
        ns.numune_onayla(nc)
        with self.assertRaisesMessage(ns.NumuneHatasi, "düzenlenemez"):
            ns.numune_guncelle(nc, tarih=TARIH, depo_id=self.depo.pk, alici_adi="X", satirlar=[{"stok_id": self.ham.pk, "miktar": "1"}])
        with self.assertRaisesMessage(ns.NumuneHatasi, "silinemez"):
            ns.numune_sil(nc)

    def test_gider_hesabi_ayardan_degisir_ve_yoksa_acik_hata(self):
        _hesap("760.07", "BASKA NUMUNE", kalem="A", grup="GELIR_TABLOSU")
        ns.ayar_kaydet(HesapPlani.objects.get(hesap_kodu="760.07"))
        nc = ns.numune_onayla(self.nc("1"))
        self.assertIn("760.07", [k for k, *_ in self.fis_satirlari(nc.fis)])
        HesapPlani.objects.filter(hesap_kodu="760.07").update(aktif=False)
        with self.assertRaisesMessage(ns.NumuneHatasi, "hesap planında yok"):
            ns.numune_onayla(self.nc("1"))

    def test_stok_hareketi_tek_basina_silinemez_fis_elle_silinemez(self):
        nc = ns.numune_onayla(self.nc("2"))
        with self.assertRaisesMessage(HareketHatasi, "Numune Çıkışı belgesinin"):
            hareket_sil(nc.satirlar.get().hareket)
        r = self.client.post(reverse("core:fis_sil", args=[nc.fis.pk]), follow=True)
        self.assertContains(r, "numune çıkışından oluştu")
        nc.fis.refresh_from_db()
        self.assertFalse(nc.fis.silindi)


class NumuneEkranTest(NumuneTaban):
    def test_liste_form_detay_onay_geri_al(self):
        r = self.client.get(reverse("core:numune_cikislari"))
        self.assertContains(r, "Numune Çıkışları")
        r = self.client.post(reverse("core:numune_ekle"), {
            "tarih": "2026-10-10", "depo": self.depo.pk, "cari": "", "alici_adi": "Gökray", "ulke": "", "belge_no_gumruk": "IRS-1", "aciklama": "",
            "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "1", "satir-MAX_NUM_FORMS": "1000",
            "satir-0-stok": self.ham.pk, "satir-0-miktar": "3"}, follow=True)
        nc = NumuneCikis.objects.get()
        self.assertContains(r, "NC-2026-0001")
        d = self.client.get(reverse("core:numune_detay", args=[nc.pk]))
        self.assertContains(d, "Onayla")
        self.client.post(reverse("core:numune_onayla", args=[nc.pk]))
        nc.refresh_from_db()
        self.assertEqual(nc.durum, "ONAYLI")
        d = self.client.get(reverse("core:numune_detay", args=[nc.pk]))
        self.assertContains(d, f"Fiş {nc.fis.yil}/{nc.fis.fis_no}")
        liste = self.client.get(reverse("core:numune_cikislari"))
        self.assertContains(liste, "GÖKRAY")
        self.assertContains(liste, "30,00")                                                               # maliyet TL
        stok = self.client.get(reverse("core:stok_detay", args=[self.ham.pk]))
        self.assertContains(stok, "Numune Çıkışı")
        self.assertContains(stok, "NC-2026-0001")                                                         # stok kartı hareketinde kaynak
        self.client.post(reverse("core:numune_geri_al", args=[nc.pk]))
        nc.refresh_from_db()
        self.assertEqual((nc.durum, eldeki_miktar(self.ham)), ("TASLAK", D("100")))

    def test_yetkisiz_kullanici_403_yetkili_girer(self):
        from django.contrib.auth.models import User
        from core.models import EkranYetki
        u = User.objects.create_user("sade", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:numune_cikislari")).status_code, 403)
        EkranYetki.objects.create(kullanici=u, ekran_kod="stoklar")
        self.assertEqual(self.client.get(reverse("core:numune_cikislari")).status_code, 200)


class NumuneYukleVeriTest(NumuneTaban):
    def test_toplu_giris_verisi_14_belge_32_adet_ve_komut_stok_yoksa_durur(self):
        from collections import Counter
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from core.management.commands.numune_cikis_yukle import BEKLENEN, BELGELER
        sayac = Counter(m for _, _, _, ms in BELGELER for m in ms)
        self.assertEqual((len(BELGELER), sum(sayac.values()), dict(sayac)), (14, 32, BEKLENEN))
        with self.assertRaises(CommandError):                                                             # depo 150 / stok kartları yok → hiçbir şey yazılmaz
            call_command("numune_cikis_yukle", "--uygula")
        self.assertEqual(NumuneCikis.objects.count(), 0)
