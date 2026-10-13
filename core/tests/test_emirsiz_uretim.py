"""EMİRSİZ ÜRETİM (2026-10-13): iş emrine / üretim siparişine bağlı olmayan operasyon kaydı. MEVCUT servislerle açılıp onaylanır (``operasyon_kaydi_olustur`` +
``operasyon_kaydi_onayla``): IE'li üretimle birebir aynı stok hareketi / maliyet / fire dağıtımı; ÜS-IE-iş oturumu OLUŞMAZ, açık ÜS'ye dokunmaz, parça serbest
stoğa girer; fason istasyonu ve standart dışı varyant (DÖNÜŞTÜR / 152- mamul) reddedilir; stok yetersizse hiçbir şey kalmaz; açık ÜS'e ayrılı stok için onay
kutusu; formda planlı-ÜS uyarısı; liste süzgeci (İstasyon emirli / Emirsiz / Fason) + rozet; yeni ``emirsiz_uretim`` ekran yetkisi."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Cari, EkranYetki, FaturaTipi, HesapPlani, IsIstasyonu, IsOturumu, IstasyonEmri, Kategori, KategoriHesap, Kur, OperasyonKaydi, StokHareket, UretimEmri, YevmiyeSatir,
)
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    AyrilmisStokOnayGerekli, UretimHatasi, emirsiz_planli_uyari, emirsiz_uret, emirsiz_varyant_kontrol, istasyon_emri_uret, operasyon_kaydi_olustur,
    operasyon_kaydi_sil, operasyon_olustur, uretim_emri_ilerleme, uretim_emri_olustur,
)
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_uretim import _stok

D = Decimal
BUGUN = timezone.localdate()


class EmirsizServisTest(Taban):
    """Taban: profil (100) → kesim (1 profil = 2 kesilmiş) → büküm; ÜS: 10 bükülmüş (kesim emri 5 koşu, ÜS'ye 5 profil ayrılı)."""

    def uret(self, saglam="4", fire=None, **kw):
        return emirsiz_uret(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam=saglam, fire=fire, **kw)

    def test_kayit_emirsiz_serbest_stoga_girer_us_ie_oturum_olusmaz(self):
        ie_sayisi, ilerleme = IstasyonEmri.objects.count(), uretim_emri_ilerleme(self.emir)
        profil_once, kesilmis_once = eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis)
        kayit, planli = self.uret("4")
        self.assertEqual((kayit.durum, kayit.uretim_emri_id, kayit.istasyon_emri_id, kayit.fason_cari_id, kayit.fason_donus_id), ("ONAYLI", None, None, None, None))
        self.assertTrue(kayit.emirsiz)
        self.assertEqual(IstasyonEmri.objects.count(), ie_sayisi)                                    # gizli IE açılmadı
        self.assertEqual(IsOturumu.objects.count(), 0)
        self.assertEqual(eldeki_miktar(self.kesilmis) - kesilmis_once, D("4"))                       # parça serbest stoğa girdi
        self.assertEqual(profil_once - eldeki_miktar(self.profil), D("2"))                           # 4 kesilmiş = 2 koşu × 1 profil
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.profil), D("5"))                          # ÜS ayırması aynen
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.kesilmis), D("0"))                        # üretilen parça ÜS'ye AYRILMADI
        self.ie_kesim.refresh_from_db()
        self.assertEqual((self.ie_kesim.tamamlanan, self.ie_kesim.durum), (D("0"), "BEKLIYOR"))
        self.assertEqual(uretim_emri_ilerleme(self.emir), ilerleme)
        self.emir.refresh_from_db()
        self.assertEqual(self.emir.durum, UretimEmri.Durum.ACIK)
        self.assertEqual([x["adet"] for x in planli], [D("10")])                                     # açık ÜS'te kesim emri 5 koşu × 2 = 10 planlı (uyarı)

    def test_fire_sagliga_dagitilir_atolye_bitir_ile_ayni_mantik(self):
        kayit, _ = self.uret("4", fire="1")
        g = StokHareket.objects.get(operasyon_kaydi=kayit, tur="GIRIS")
        c = StokHareket.objects.get(operasyon_kaydi=kayit, tur="CIKIS")
        self.assertEqual((g.miktar, c.miktar), (D("4"), D("2.5")))                                  # girişte yalnız sağlam, girdi (4+1)/2 profil
        self.assertEqual(kayit.hedef_cikti_miktari, D("5"))
        self.assertEqual(kayit.gelen, {str(self.kesilmis.pk): "4"})

    def test_ie_li_uretimle_birebir_ayni_hareket_maliyet_fis(self):
        """Aynı operasyon / miktar / fire: IE'li (``istasyon_emri_uret``) ve emirsiz kayıt aynı stok hareketini, maliyeti ve çıktı satırlarını yazar."""
        Kur.objects.get_or_create(tarih=BUGUN, defaults={"usd_alis": D("30")})                    # her fişin USD karşılığı zorunlu
        fatura_tipi = FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS")
        kat_ham, kat_mamul = Kategori.objects.create(kod="HM", ad="HAM TEST"), Kategori.objects.create(kod="MM", ad="MAMUL TEST")
        for kat, kod in ((kat_ham, "150.10"), (kat_mamul, "151.10")):
            KategoriHesap.objects.create(kategori=kat, fatura_tipi=fatura_tipi, hesap=HesapPlani.objects.create(
                hesap_kodu=kod, hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True))
        ham = _stok(kat_ham, self.birim, kod="IK-HAM2", ad="ham 2", satinalma=True)
        mamul = _stok(kat_mamul, self.birim, kod="IK-MAMUL2", ad="mamul 2")
        op = operasyon_olustur(istasyon_id=self.lazer.pk, cikti_id=mamul.pk, cikti_miktar=D("1"), satirlar=[(ham, D("2"))])
        hareket_ekle(stok_id=ham.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 3), tur="GIRIS", miktar=D("100"), giris_tutar_try=D("1000"))
        emir2 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": mamul.pk, "hedef_miktar": D("10")}], depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 10))
        ie2 = emir2.istasyon_emirleri.get(operasyon=op)

        def ozet(kayit):
            h = [(x.stok.kod, x.tur, x.miktar, x.tutar_try, x.maliyet_durumu) for x in StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False).select_related("stok")]
            fis = sorted((s.hesap_id, s.borc, s.alacak) for s in YevmiyeSatir.objects.filter(fis=kayit.fis, silindi=False)) if kayit.fis_id else []
            ciktilar = sorted((c.stok.kod, c.miktar, c.beklenen_miktar, c.pay_orani) for c in kayit.ciktilar.filter(silindi=False))
            girdiler = sorted((g.girdi.kod, g.planlanan_miktar, g.gerceklesen_miktar) for g in kayit.girdi_satirlari.filter(silindi=False))
            return sorted(h), fis, ciktilar, girdiler, kayit.hedef_cikti_miktari

        sonuc = []
        for yol in ("ie", "emirsiz"):
            sid = transaction.savepoint()
            if yol == "ie":
                kayit, _ = istasyon_emri_uret(ie2, miktar=D("4"), tarih=BUGUN, fire=D("1"))
            else:
                kayit, _ = emirsiz_uret(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam="4", fire="1")
            sonuc.append(ozet(kayit))
            transaction.savepoint_rollback(sid)
        self.assertEqual(sonuc[0], sonuc[1])
        self.assertTrue(any(t is not None and t > 0 for (_k, _t, _m, t, _d) in sonuc[0][0]))         # gerçek (sıfırdan büyük) maliyet karşılaştırıldı
        self.assertTrue(sonuc[0][1])                                                                  # 150→151 maliyet aktarım fişi satırları da aynı

    def test_fason_istasyonunda_reddedilir(self):
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)
        sayi = OperasyonKaydi.objects.count()
        with self.assertRaisesMessage(UretimHatasi, "Fason istasyonunda emirsiz üretim yapılamaz"):
            self.uret("4")
        self.assertEqual(OperasyonKaydi.objects.count(), sayi)

    def test_stok_yetersizse_reddedilir_hicbir_sey_kalmaz(self):
        sayi, hareket = OperasyonKaydi.objects.count(), StokHareket.objects.count()
        with self.assertRaisesMessage(UretimHatasi, "yetersiz"):
            self.uret("500")                                                                           # 250 profil gerekir, 100 var
        self.assertEqual((OperasyonKaydi.objects.count(), StokHareket.objects.count()), (sayi, hareket))

    def test_ayrilmis_stok_icin_onay_gerekir(self):
        sayi = OperasyonKaydi.objects.count()
        with self.assertRaises(AyrilmisStokOnayGerekli) as cm:
            self.uret("192")                                                                           # 96 profil: eldeki 100 yeter, kullanılabilir 95 (5'i ÜS'ye ayrılı)
        self.assertEqual(OperasyonKaydi.objects.count(), sayi)
        g = cm.exception.satirlar[0]
        self.assertEqual((g["stok"].pk, g["gerekli"], g["eldeki"], g["ayrilan"], g["kullanilabilir"]), (self.profil.pk, D("96"), D("100"), D("5"), D("95")))
        kayit, _ = self.uret("192", ayrilmis_onay=True)
        self.assertEqual(kayit.durum, "ONAYLI")
        self.assertEqual(eldeki_miktar(self.profil), D("4"))

    def test_gelecek_tarih_ve_gecersiz_miktar_reddedilir(self):
        with self.assertRaisesMessage(UretimHatasi, "ileri olamaz"):
            emirsiz_uret(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=BUGUN + datetime.timedelta(days=1), saglam="4")
        for kotu in ("0", "-3", "abc"):
            with self.assertRaises(UretimHatasi):
                self.uret(kotu)
        with self.assertRaisesMessage(UretimHatasi, "Fire adedi negatif"):
            self.uret("4", fire="-1")

    def test_varyant_kurali_donustur_ve_mamulde_standart_disi_reddedilir(self):
        def op(tur="URET", kod="151-10-0001", cikti_id=7):
            return SimpleNamespace(tur=tur, cikti=SimpleNamespace(kod=kod), cikti_id=cikti_id)
        ozel, standart = SimpleNamespace(stok_id=7, standart=False), SimpleNamespace(stok_id=7, standart=True)
        emirsiz_varyant_kontrol(op(), None)
        emirsiz_varyant_kontrol(op(), ozel)                                                           # sıradan ara mamul: özel varyant serbest
        emirsiz_varyant_kontrol(op("DONUSTUR"), standart)                                            # standart (SEMTA) serbest
        emirsiz_varyant_kontrol(op(kod="152-10-0002"), standart)
        with self.assertRaisesMessage(UretimHatasi, "standart dışı varyant"):
            emirsiz_varyant_kontrol(op("DONUSTUR"), ozel)
        with self.assertRaisesMessage(UretimHatasi, "standart dışı varyant"):
            emirsiz_varyant_kontrol(op(kod="152-10-0002"), ozel)
        with self.assertRaisesMessage(UretimHatasi, "çıktı kartına ait değil"):
            emirsiz_varyant_kontrol(op(), SimpleNamespace(stok_id=99, standart=True))

    def test_planli_uyari_ust_isim_ve_adet(self):
        uyari = emirsiz_planli_uyari(self.kesim_op)
        self.assertEqual([(x["us"], x["ie"], x["adet"]) for x in uyari], [(self.emir.no, self.ie_kesim.no, D("10"))])
        self.assertEqual(emirsiz_planli_uyari(self.bukum_op)[0]["adet"], D("10"))
        istasyon_emri_uret(self.ie_kesim, miktar=D("10"), tarih=BUGUN)                              # emir bitince planlı kalmaz
        self.assertEqual(emirsiz_planli_uyari(self.kesim_op), [])

    def test_geri_al_stok_ve_kayit_geri_sarilir(self):
        profil, kesilmis = eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis)
        kayit, _ = self.uret("4")
        operasyon_kaydi_sil(kayit, onayli_geri_al=True)
        kayit.refresh_from_db()
        self.assertTrue(kayit.silindi)
        self.assertEqual((eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis)), (profil, kesilmis))


class EmirsizEkranTest(Taban):
    def setUp(self):
        super().setUp()
        self.su = User.objects.create_superuser("emsu", password="x")
        self.yetkili = User.objects.create_user("emyetkili", password="x")
        EkranYetki.objects.create(kullanici=self.yetkili, ekran_kod="emirsiz_uretim")
        EkranYetki.objects.create(kullanici=self.yetkili, ekran_kod="operasyon_kayitlari")
        self.sadece_liste = User.objects.create_user("emliste", password="x")
        EkranYetki.objects.create(kullanici=self.sadece_liste, ekran_kod="operasyon_kayitlari")

    def veri(self, **ek):
        v = {"operasyon": str(self.kesim_op.pk), "depo": str(self.depo.pk), "tarih": BUGUN.isoformat(), "saglam": "4", "fire": "", "personel": "", "aciklama": "deneme"}
        v.update(ek)
        return v

    def test_yetki_ve_dugme(self):
        self.client.force_login(self.sadece_liste)
        self.assertEqual(self.client.get(reverse("core:emirsiz_uretim")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:emirsiz_onizleme")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("core:operasyon_kayitlari")), 'href="' + reverse("core:emirsiz_uretim") + '"')
        self.client.force_login(self.yetkili)
        self.assertContains(self.client.get(reverse("core:operasyon_kayitlari")), 'href="' + reverse("core:emirsiz_uretim") + '"')
        r = self.client.get(reverse("core:emirsiz_uretim"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Emirsiz Üretim")
        self.assertContains(r, "Sağlam adet")
        self.assertContains(r, "Fire adet")
        self.assertContains(r, "Çıktı varyantı")
        self.assertNotContains(r, "FASON")
        self.client.force_login(self.su)
        self.assertEqual(self.client.get(reverse("core:emirsiz_uretim")).status_code, 200)                 # yönetici hep girer

    def test_post_kaydeder_ve_detayda_emirsiz_rozeti(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:emirsiz_uretim"), self.veri())
        self.assertEqual(r.status_code, 302)
        kayit = OperasyonKaydi.objects.get(aciklama="deneme")
        self.assertTrue(kayit.emirsiz)
        self.assertEqual(kayit.durum, "ONAYLI")
        self.assertRedirects(r, reverse("core:operasyon_kaydi_detay", args=[kayit.pk]))
        self.assertContains(self.client.get(reverse("core:operasyon_kaydi_detay", args=[kayit.pk])), "Emirsiz")

    def test_post_hatalari_formda_kalir(self):
        self.client.force_login(self.yetkili)
        sayi = OperasyonKaydi.objects.count()
        r = self.client.post(reverse("core:emirsiz_uretim"), self.veri(saglam="500"))                      # stok yetersiz
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "yetersiz")
        r = self.client.post(reverse("core:emirsiz_uretim"), self.veri(saglam="192"))                      # ayrılmış stok → onay kutusu
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AYRILMIŞ")
        self.assertNotContains(r, 'id="em-onay-alan" hidden')
        self.assertEqual(OperasyonKaydi.objects.count(), sayi)
        r = self.client.post(reverse("core:emirsiz_uretim"), self.veri(saglam="192", ayrilmis_onay="on"))
        self.assertEqual(r.status_code, 302)
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)                                    # fason istasyon formda hiç seçilemez
        r = self.client.post(reverse("core:emirsiz_uretim"), self.veri())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Geçerli bir seçenek")

    def test_onizleme_json_planli_girdi_ve_ayrilmis(self):
        self.client.force_login(self.yetkili)
        j = self.client.get(reverse("core:emirsiz_onizleme"), {"operasyon": self.kesim_op.pk, "saglam": "4", "fire": "1"}).json()
        self.assertEqual(j["hata"], "")
        self.assertEqual(j["planli"][0]["us"], self.emir.no)
        self.assertEqual(j["planli_toplam"], "10")
        g = j["girdiler"][0]
        self.assertEqual((g["kod"], g["gerekli"], g["eldeki"], g["ayrilan"], g["kullanilabilir"]), (self.profil.kod, "2.5", "100", "5", "95"))
        self.assertFalse(j["ayrilmis_gerekli"])
        self.assertEqual(j["varyantlar"], [])
        j = self.client.get(reverse("core:emirsiz_onizleme"), {"operasyon": self.kesim_op.pk, "saglam": "192"}).json()
        self.assertTrue(j["ayrilmis_gerekli"])
        j = self.client.get(reverse("core:emirsiz_onizleme"), {"operasyon": self.kesim_op.pk, "saglam": "-1"}).json()
        self.assertIn("sıfırdan büyük", j["hata"])
        self.assertEqual(OperasyonKaydi.objects.count(), 0)                                               # önizleme hiçbir şey yazmaz (taslak geri alındı)

    def test_liste_suzgec_ie_emirsiz_fason_ve_rozet(self):
        emirsiz, _ = emirsiz_uret(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam="4")
        ie_kayit, _ = istasyon_emri_uret(self.ie_kesim, miktar=D("4"), tarih=BUGUN)
        fasoncu = Cari.objects.create(kod="FSNC", unvan="FASONCU")
        fason = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=BUGUN, hedef_cikti_miktari=D("2"), fason_cari=fasoncu)
        self.client.force_login(self.su)

        def liste(**q):
            return self.client.get(reverse("core:operasyon_kayitlari"), {"sekme": "tumu", **q}).content.decode()
        h = liste()
        self.assertTrue(all(k.no in h for k in (emirsiz, ie_kayit, fason)))
        self.assertIn("Emirsiz", h)
        h = liste(emir="emirsiz")
        self.assertTrue(emirsiz.no in h and ie_kayit.no not in h and fason.no not in h)
        h2 = liste(emir="bagimsiz")                                                                       # eski bağlantı adı aynı sonucu verir
        self.assertTrue(emirsiz.no in h2 and ie_kayit.no not in h2 and fason.no not in h2)
        h = liste(emir="ie")
        self.assertTrue(ie_kayit.no in h and emirsiz.no not in h and fason.no not in h)
        h = liste(emir="fason")
        self.assertTrue(fason.no in h and emirsiz.no not in h and ie_kayit.no not in h)
        self.assertContains(self.client.get(reverse("core:operasyon_kayitlari")), 'name="emir"')
