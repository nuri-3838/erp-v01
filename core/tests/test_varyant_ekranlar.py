"""E-3: giriş/çıkış ekranlarında varyant seçimi — alış/satış faturası satırı (yeni kombinasyon açılır, hareket o varyantla yazılır, yerinde
güncelleme varyantı korur), irsaliye kalemi → giriş hareketi + faturaya dönüşte taşınır, siparişten faturaya taşınır, numune satırı,
elle hareket / depo transferi / sarf formları (varyantlı kartta alan, varyantsızda yok). Varyantsız kartlarda davranış birebir aynı."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Depo, EkranYetki, FaturaSatir, HesapPlani, NumuneCikis, Stok, StokHareket, StokVaryant, TeklifSiparis, Varyant
from core.services import fatura as fs
from core.services import numune as ns
from core.services import varyant as vs
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.stok import stok_olustur
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_fatura import FaturaTestTemel

D = Decimal


class VaryantEkranTaban(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.yon = User.objects.create_superuser("veyon", password="x")
        from core.models import Kur
        for gun in range(1, 13):
            Kur.objects.get_or_create(tarih=date(2026, 3, gun), defaults={"usd_alis": D("30")})
        cls.depo = Depo.objects.create(kod="01", ad="ANA DEPO")
        cls.depo2 = Depo.objects.create(kod="02", ad="YEDEK DEPO")
        cls.semta = Varyant.objects.get(kod="SEMTA")
        cls.yok = Varyant.objects.get(kod="YOK")
        cls.mop = Varyant.objects.get(kod="MR. MOP")
        cls.afis = stok_olustur(ad="afiş a tipi", kategori_id=cls.alt.pk, uretim_birimi_id=cls.stok.uretim_birimi_id,
                                fatura_birimi_id=cls.stok.fatura_birimi_id, cevirici=D("1"), kdv_id=cls.kdv.pk, satinalma_urunu=True,
                                uretim_urunu=False, satis_urunu=True, varyantli=True, varyant_boyutlari=["AFIS"])

    def satir(self, stok, miktar="10", fiyat="1", varyant=None):
        s = {"stok_id": stok.pk, "miktar": miktar, "birim_fiyat": fiyat}
        if varyant is not None:
            s["varyant"] = varyant
        return [s]

    def alis_fatura(self, stok, varyant=None, miktar="10", fiyat="1", tarih=date(2026, 3, 10)):
        return fs.fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=tarih, satirlar=self.satir(stok, miktar, fiyat, varyant),
                                 depo_id=self.depo.pk)


class FaturaVaryantTest(VaryantEkranTaban):
    def test_varyantsiz_kart_birebir_ayni(self):
        f = self.alis_fatura(self.stok)
        s = f.satirlar.get(silindi=False)
        h = StokHareket.objects.get(fatura_satir=s, silindi=False)
        self.assertEqual((s.varyant_id, h.varyant_id), (None, None))
        with self.assertRaisesMessage(fs.FaturaHatasi, "varyantlı bir kart değil"):
            self.alis_fatura(self.stok, varyant="v:AFIS=")

    def test_alis_faturasinda_yeni_varyant_acilir_ve_giris_o_varyanta(self):
        f = self.alis_fatura(self.afis, varyant=f"v:AFIS={self.mop.pk}")                 # Afiş A: MR. MOP — kombinasyon yoktu, açılır
        s = f.satirlar.get(silindi=False)
        sv = s.varyant
        self.assertEqual((sv.afis_id, sv.standart, sv.stok_id), (self.mop.pk, False, self.afis.pk))
        h = StokHareket.objects.get(fatura_satir=s, silindi=False)
        self.assertEqual((h.varyant_id, h.giris_tutar_try), (sv.pk, D("10.00")))
        self.assertEqual((eldeki_miktar(self.afis), eldeki_miktar(self.afis, varyant=sv), eldeki_miktar(self.afis, varyant=self.afis.varsayilan_varyant_id)),
                         (D("10"), D("10"), D("0")))
        f2 = self.alis_fatura(self.afis)                                                 # varyant verilmedi → kartın varsayılanı (SEMTA)
        s2 = f2.satirlar.get(silindi=False)
        self.assertIsNone(s2.varyant_id)
        self.assertEqual(StokHareket.objects.get(fatura_satir=s2, silindi=False).varyant_id, self.afis.varsayilan_varyant_id)
        self.assertEqual(self.alis_fatura(self.afis, varyant=f"v:AFIS={self.mop.pk}").satirlar.get(silindi=False).varyant_id, sv.pk)   # tekrar: aynı satır
        self.assertEqual(StokVaryant.objects.filter(stok=self.afis, silindi=False).count(), 2)

    def test_yerinde_fiyat_guncelleme_varyanti_korur_varyant_degisince_yeniden_yazar(self):
        f = self.alis_fatura(self.afis, varyant=f"v:AFIS={self.mop.pk}")
        sv = f.satirlar.get(silindi=False).varyant
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        fs.fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=f.tarih, depo_id=self.depo.pk,
                           satirlar=self.satir(self.afis, "10", "2", f"v:AFIS={self.mop.pk}"))
        h.refresh_from_db()
        self.assertEqual((h.silindi, h.varyant_id, h.giris_tutar_try), (False, sv.pk, D("20.00")))          # yerinde
        fs.fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=f.tarih, depo_id=self.depo.pk,
                           satirlar=self.satir(self.afis, "10", "2", f"v:AFIS={self.semta.pk}"))
        h.refresh_from_db()
        self.assertTrue(h.silindi)                                                                          # varyant değişti: sil + yeniden yaz
        yeni = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(yeni.varyant_id, self.afis.varsayilan_varyant_id)

    def test_satis_faturasi_varyanttan_duser(self):
        self.alis_fatura(self.afis, varyant=f"v:AFIS={self.mop.pk}", miktar="5")
        self.alis_fatura(self.afis, miktar="3")                                                                     # SEMTA 3
        with self.assertRaisesMessage(fs.FaturaHatasi, "[SEMTA] (eldeki 3, gerekli 4)"):
            fs.fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=date(2026, 3, 11), depo_id=self.depo.pk,
                              satirlar=self.satir(self.afis, "4", "9"))
        f = fs.fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=date(2026, 3, 11), depo_id=self.depo.pk,
                              satirlar=self.satir(self.afis, "4", "9", f"v:AFIS={self.mop.pk}"))
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual((h.tur, h.varyant.afis_id, h.miktar), ("CIKIS", self.mop.pk, D("4")))


class IrsaliyeVeSiparisVaryantTest(VaryantEkranTaban):
    def irsaliye(self, varyant=None):
        teklif = teklif_siparis_olustur(belge_tur="TEKLIF", yon="ALIS", cari_id=self.tedarikci.pk, tarih=date(2026, 3, 1),
                                        satirlar=self.satir(self.afis, "10", "1", varyant), kullanici=self.yon)
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        siparis = teklif.donusen_belgeler.get()
        teklif_siparis_onayla(siparis, kullanici=self.yon)
        irsaliye = siparis.donusen_irsaliyeler.get()
        irsaliye.depo = self.depo
        irsaliye.save(update_fields=["depo"])
        teklif_siparis_onayla(irsaliye, kullanici=self.yon)
        irsaliye.refresh_from_db()
        return teklif, siparis, irsaliye

    def test_alis_zincirinde_kalem_varyanti_tasinir_irsaliye_girisi_ve_taslak_fatura(self):
        teklif, siparis, irsaliye = self.irsaliye(varyant=f"v:AFIS={self.mop.pk}")
        sv = teklif.kalemler.get(silindi=False).varyant
        self.assertEqual((sv.afis_id, siparis.kalemler.get(silindi=False).varyant_id, irsaliye.kalemler.get(silindi=False).varyant_id),
                         (self.mop.pk, sv.pk, sv.pk))
        h = StokHareket.objects.get(stok=self.afis, kaynak="IRSALIYE", silindi=False)
        self.assertEqual(h.varyant_id, sv.pk)
        self.assertEqual(irsaliye.fatura.satirlar.get(silindi=False).varyant_id, sv.pk)                    # taslak faturaya taşındı

    def test_varyantsiz_irsaliye_birebir(self):
        teklif, siparis, irsaliye = self.irsaliye()
        self.assertIsNone(teklif.kalemler.get(silindi=False).varyant_id)
        self.assertEqual(StokHareket.objects.get(stok=self.afis, kaynak="IRSALIYE", silindi=False).varyant_id, self.afis.varsayilan_varyant_id)

    def test_siparisten_faturaya_ekrani_kalem_varyantini_on_doldurur(self):
        EkranYetki.objects.get_or_create(kullanici=self.yon, ekran_kod="satis_siparisleri")
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="SATIS", cari_id=self.musteri.pk, tarih=date(2026, 3, 1),
                                     satirlar=self.satir(self.afis, "2", "9", f"v:AFIS={self.mop.pk}"), kullanici=self.yon)
        teklif_siparis_onayla(sip, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'name="form-0-varyant" value="v:AFIS={self.mop.pk}"')
        self.assertContains(r, 'id="varyant-meta"')


class NumuneVeStokFormVaryantTest(VaryantEkranTaban):
    def setUp(self):
        from core.tests.test_teklif_siparis import _hesap
        _hesap("760.06", "NUMUNE GİDERLERİ", kalem="A", grup="GELIR_TABLOSU")
        self.mop_sv = vs.stok_varyanti_bul_veya_olustur(self.afis, {"AFIS": self.mop})
        hareket_ekle(stok_id=self.afis.pk, depo_id=self.depo.pk, tarih=date(2026, 3, 1), tur="GIRIS", miktar=D("5"), giris_tutar_try=D("5"),
                     varyant=self.mop_sv)
        hareket_ekle(stok_id=self.afis.pk, depo_id=self.depo.pk, tarih=date(2026, 3, 1), tur="GIRIS", miktar=D("8"), giris_tutar_try=D("16"))

    def test_numune_satiri_varyantli(self):
        nc = ns.numune_olustur(tarih=date(2026, 3, 5), depo_id=self.depo.pk, alici_adi="Test",
                               satirlar=[{"stok_id": self.afis.pk, "miktar": "4", "varyant": f"v:AFIS={self.mop.pk}"},
                                         {"stok_id": self.afis.pk, "miktar": "2"}], kullanici=self.yon)
        s1, s2 = nc.satirlar.filter(silindi=False).order_by("id")
        self.assertEqual((s1.varyant_id, s2.varyant_id), (self.mop_sv.pk, None))
        with self.assertRaisesMessage(ns.NumuneHatasi, "varyantlı bir kart değil"):
            ns.numune_olustur(tarih=date(2026, 3, 5), depo_id=self.depo.pk, alici_adi="T", satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "varyant": "v:AFIS="}])
        ns.numune_onayla(nc, kullanici=self.yon)
        hs = {h.varyant_id: h.miktar for h in StokHareket.objects.filter(stok=self.afis, kaynak="NUMUNE", silindi=False)}
        self.assertEqual(hs, {self.mop_sv.pk: D("4"), self.afis.varsayilan_varyant_id: D("2")})
        nc2 = ns.numune_olustur(tarih=date(2026, 3, 6), depo_id=self.depo.pk, alici_adi="T", satirlar=[{"stok_id": self.afis.pk, "miktar": "2", "varyant": f"v:AFIS={self.mop.pk}"}], kullanici=self.yon)
        with self.assertRaisesMessage(ns.NumuneHatasi, "[MR. MOP] (eldeki 1, gerekli 2)"):
            ns.numune_onayla(nc2, kullanici=self.yon)

    def test_elle_hareket_transfer_sarf_formlari(self):
        EkranYetki.objects.get_or_create(kullanici=self.yon, ekran_kod="stoklar")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stok_hareket_ekle", args=[self.afis.pk]))
        self.assertContains(r, 'class="varyant-alan" data-stok="%d"' % self.afis.pk)
        self.assertContains(r, 'name="varyant"')
        r = self.client.get(reverse("core:stok_hareket_ekle", args=[self.stok.pk]))
        self.assertNotContains(r, 'class="varyant-alan"')                                                  # varyantsız kartta alan yok
        r = self.client.post(reverse("core:stok_hareket_ekle", args=[self.afis.pk]), {
            "depo": self.depo.pk, "tur": "CIKIS", "miktar": "3", "tarih": "2026-03-07", "varyant": f"v:AFIS={self.mop.pk}"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(eldeki_miktar(self.afis, varyant=self.mop_sv), D("2"))
        r = self.client.post(reverse("core:stok_depo_transferi", args=[self.afis.pk]), {
            "kaynak_depo": self.depo.pk, "hedef_depo": self.depo2.pk, "miktar": "2", "tarih": "2026-03-08", "varyant": f"v:AFIS={self.mop.pk}"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual((eldeki_miktar(self.afis, self.depo2, varyant=self.mop_sv), eldeki_miktar(self.afis, self.depo, varyant=self.mop_sv)), (D("2"), D("0")))
        HesapPlani.objects.get_or_create(hesap_kodu="770.01", defaults={"hesap_adi": "GİDER", "rapor_grubu": "GELIR_TABLOSU", "rapor_kalemi": "A", "parasal": True, "aktif": True})
        r = self.client.get(reverse("core:stok_sarf_ekle", args=[self.afis.pk]))
        self.assertContains(r, 'class="varyant-alan" data-stok="%d"' % self.afis.pk)
        r = self.client.post(reverse("core:stok_hareket_ekle", args=[self.afis.pk]), {
            "depo": self.depo.pk, "tur": "CIKIS", "miktar": "1", "tarih": "2026-03-09", "varyant": f"v:AFIS={self.mop.pk}"})
        self.assertEqual(r.status_code, 200)                                                               # MR. MOP bu depoda 0 → formda hata
        self.assertContains(r, "[MR. MOP]")
