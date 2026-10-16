"""Stok Sayımları — dilim 2 (ekranlar / menü / yetki / yönlendirme): liste, yeni belge, mobil giriş (toplamsal), KÖR sayımda sistem miktarının HİÇ gönderilmemesi, fark/karar ekranı (yalnız yönetici),
girişi kapat/aç, geçersiz işaretleme, Stoklar menüsünde "Tanımlar" alt menüsü, yalnız ``stok_sayim`` yetkili kullanıcının girişte sayım listesine düşmesi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, StokSayimGirdisi, StokSayimi
from core.moduller import MODULLER
from core.services import stok_sayim as ss
from core.tests.test_stok_sayim import BUGUN, SayimTaban
from core.yetki import kullanici_menusu, sayim_kullanicisi_mi

D = Decimal
SENTINEL = "7777"                                  # kör sayım testinde sistem miktarı olarak kullanılır: HTML'de ASLA görünmemeli


class EkranTaban(SayimTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.personel = User.objects.create_user("sayici", password="x", first_name="Sayıcı", last_name="Kişi")
        EkranYetki.objects.create(kullanici=cls.personel, ekran_kod="stok_sayim")
        cls.baskasi = User.objects.create_user("baskasi", password="x")
        EkranYetki.objects.create(kullanici=cls.baskasi, ekran_kod="stoklar")

    def kur(self, kor=False, miktar="10"):
        a = self.kart("ayak a")
        self.giris(a, miktar, "1000")
        s = self.ac(kor=kor)
        return a, s, self.satir(s, a)

    def giris_gir(self, kullanici):
        self.client.force_login(kullanici)


class MenuTest(EkranTaban):
    def test_stoklar_menusu_sayim_ve_tanimlar_alt_menusu(self):
        stoklar = next(m for m in MODULLER if m.kod == "STOKLAR")
        kodlar = [e.kod for e in stoklar.ekranlar]
        self.assertEqual(kodlar, ["stoklar", "stok_sayim", "kategoriler", "fatura_tipleri", "birimler", "depolar"])
        ogeler = stoklar.ogeler
        self.assertEqual([getattr(o, "kod", None) for o in ogeler if not o.alt_menu], ["stoklar", "stok_sayim"])
        (grup,) = [o for o in ogeler if o.alt_menu]
        self.assertEqual((grup.ad, [e.kod for e in grup.ekranlar]), ("Tanımlar", ["kategoriler", "fatura_tipleri", "birimler", "depolar"]))
        for e in grup.ekranlar:                                      # alt sayfalar (ekle/düzenle/sil) da grubu açık/aktif işaretler
            self.assertTrue(e.ilgili)

    def test_tanim_ekranlari_adresleri_ve_yetkileri_degismedi(self):
        self.client.force_login(self.yon)
        for ad in ("kategoriler", "fatura_tipleri", "birimler", "depolar"):
            self.assertEqual(self.client.get(reverse(f"core:{ad}")).status_code, 200, ad)
        self.assertEqual(reverse("core:depolar"), "/stoklar/depolar/")
        menu = kullanici_menusu(self.personel)                       # yalnız stok_sayim: Stoklar'da yalnız Stok Sayımları
        (stoklar,) = [m for m in menu if m.kod == "STOKLAR"]
        self.assertEqual([e.kod for e in stoklar.ekranlar], ["stok_sayim"])
        (b,) = [m for m in kullanici_menusu(self.baskasi) if m.kod == "STOKLAR"]
        self.assertEqual([e.kod for e in b.ekranlar], ["stoklar"])


class YonlendirmeVeYetkiTest(EkranTaban):
    def test_yalniz_sayim_yetkilisi_girista_listeye_duser(self):
        self.assertTrue(sayim_kullanicisi_mi(self.personel))
        self.assertFalse(sayim_kullanicisi_mi(self.yon))
        self.assertFalse(sayim_kullanicisi_mi(self.baskasi))
        self.client.force_login(self.personel)
        r = self.client.get(reverse("core:pano"))
        self.assertRedirects(r, reverse("core:stok_sayimlari"), fetch_redirect_response=False)
        EkranYetki.objects.create(kullanici=self.personel, ekran_kod="stoklar")      # başka yetkisi de olan normal panoya düşer
        self.assertFalse(sayim_kullanicisi_mi(self.personel))
        self.assertEqual(self.client.get(reverse("core:pano")).status_code, 200)
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:pano")).status_code, 200)

    def test_yetkisiz_403_personel_yonetim_ekranlarinda_403(self):
        a, s, sat = self.kur()
        self.client.force_login(self.baskasi)                         # stok_sayim yetkisi yok
        for url in (reverse("core:stok_sayimlari"), reverse("core:stok_sayim_giris", args=[s.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.personel)
        self.assertEqual(self.client.get(reverse("core:stok_sayimlari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:stok_sayim_giris", args=[s.pk])).status_code, 200)
        for url in (reverse("core:stok_sayim_yeni"), reverse("core:stok_sayim_belge", args=[s.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for ad, arg in (("stok_sayim_karar", s.pk), ("stok_sayim_durum", s.pk)):
            self.assertEqual(self.client.post(reverse(f"core:{ad}", args=[arg]), {}).status_code, 403, ad)
        g = ss.girdi_ekle(sat, kullanici=self.personel, miktar="3")
        self.assertEqual(self.client.post(reverse("core:stok_sayim_girdi_gecersiz", args=[g.pk]), {}).status_code, 403)
        g.refresh_from_db()
        self.assertFalse(g.gecersiz)

    def test_personel_listede_yalniz_acik_belge_ve_yonetim_dugmesi_yok(self):
        a, s, sat = self.kur()
        kapali = self.ac()
        ss.giris_kapat(kapali, self.yon)
        self.client.force_login(self.personel)
        r = self.client.get(reverse("core:stok_sayimlari"))
        self.assertContains(r, s.no)
        self.assertNotContains(r, kapali.no)
        self.assertNotContains(r, "Yeni sayım")
        self.assertNotContains(r, "Fark / Karar")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stok_sayimlari"))
        self.assertContains(r, s.no)
        self.assertContains(r, kapali.no)
        self.assertContains(r, "Yeni sayım")
        self.assertContains(r, "Fark / Karar")
        self.assertContains(self.client.get(reverse("core:stok_sayimlari"), {"durum": "KAPALI"}), kapali.no)
        self.assertNotContains(self.client.get(reverse("core:stok_sayimlari"), {"durum": "KAPALI"}), s.no)


class GirisEkraniTest(EkranTaban):
    def test_giris_toplamsal_ve_kaynak_kullanici(self):
        a, s, sat = self.kur()
        self.giris_gir(self.personel)
        url = reverse("core:stok_sayim_giris_kaydet", args=[s.pk])
        r = self.client.post(url, {f"m_{sat.pk}": "4"}, follow=True)
        self.assertContains(r, "1 satıra sayım girişi kaydedildi")
        self.client.post(url, {f"m_{sat.pk}": "3,5"})
        self.assertEqual(ss.sayilanlar(s), {sat.pk: D("7.5")})
        g = StokSayimGirdisi.objects.first()
        self.assertEqual((g.kullanici_id, g.kaynak), (self.personel.pk, "EKRAN"))
        r = self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]))
        self.assertContains(r, "Benim girdiğim")
        self.assertContains(r, "Toplam sayılan")

    def test_gecersiz_miktar_hata_mesaji_ve_baska_belgenin_satiri_yok_sayilir(self):
        a, s, sat = self.kur()
        diger = self.ac(depo_id=self.depo2.pk)
        self.giris_gir(self.personel)
        r = self.client.post(reverse("core:stok_sayim_giris_kaydet", args=[s.pk]), {f"m_{sat.pk}": "abc"}, follow=True)
        self.assertContains(r, "geçerli bir sayı")
        self.assertEqual(StokSayimGirdisi.objects.count(), 0)
        diger_satir = ss.satir_ekle(diger, stok_id=a.pk, kullanici=self.yon)
        self.client.post(reverse("core:stok_sayim_giris_kaydet", args=[s.pk]), {f"m_{diger_satir.pk}": "5"})
        self.assertEqual(StokSayimGirdisi.objects.count(), 0)                           # başka belgenin satırına yazılmaz

    def test_kapali_belgeye_giris_yok(self):
        a, s, sat = self.kur()
        ss.giris_kapat(s, self.yon)
        self.giris_gir(self.personel)
        r = self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]), follow=True)
        self.assertContains(r, "sayım girişi kapalı")
        r = self.client.post(reverse("core:stok_sayim_giris_kaydet", args=[s.pk]), {f"m_{sat.pk}": "1"}, follow=True)
        self.assertContains(r, "kapalı")
        self.assertEqual(StokSayimGirdisi.objects.count(), 0)

    def test_arama_ve_listede_olmayan_urun_ekleme(self):
        a, s, sat = self.kur()
        yeni = self.kart("zımba tel")
        self.giris_gir(self.personel)
        self.assertContains(self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]), {"ara": "AYAK"}), a.kod)
        self.assertNotContains(self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]), {"ara": "zzzz"}), "name=\"m_")
        r = self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]), {"ekle_ara": "zımba"})
        self.assertContains(r, yeni.kod)
        r = self.client.post(reverse("core:stok_sayim_satir_ekle", args=[s.pk]), {"stok_id": yeni.pk, "varyant_id": ""}, follow=True)
        self.assertContains(r, "sayıma eklendi")
        self.assertTrue(s.satirlar.filter(stok=yeni, eklenen=True).exists())

    def test_kor_sayimda_sistem_miktari_hic_gonderilmez(self):
        a, s, sat = self.kur(kor=True, miktar=SENTINEL)
        self.giris_gir(self.personel)
        r = self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]))
        self.assertContains(r, a.kod)
        self.assertNotContains(r, "Sistem:")
        self.assertNotContains(r, SENTINEL)
        self.assertContains(r, "KÖR")
        yeni = self.kart("yeni kart")
        r = self.client.post(reverse("core:stok_sayim_satir_ekle", args=[s.pk]), {"stok_id": yeni.pk}, follow=True)      # ekleme mesajı da sistem miktarını sızdırmaz
        self.assertNotContains(r, "sistem miktarı")
        self.client.force_login(self.yon)                              # yönetici de giriş ekranında kör belgede sistem miktarını görmez
        self.assertNotContains(self.client.get(reverse("core:stok_sayim_giris", args=[s.pk])), SENTINEL)
        self.assertContains(self.client.get(reverse("core:stok_sayim_belge", args=[s.pk])), "7.777")                    # fark ekranında (yönetici) görür

    def test_kor_olmayan_belgede_sistem_miktari_gorunur(self):
        a, s, sat = self.kur(kor=False, miktar=SENTINEL)
        self.giris_gir(self.personel)
        r = self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]))
        self.assertContains(r, "Sistem:")
        self.assertContains(r, "7.777")                                # tr_miktar_sade binlik nokta


class YonetimEkraniTest(EkranTaban):
    def test_yeni_sayim_acma(self):
        a = self.kart("ayak a")
        self.giris(a, "10", "1000")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stok_sayim_yeni"))
        self.assertContains(r, "Kör sayım")
        r = self.client.post(reverse("core:stok_sayim_yeni"), {"depo": self.depo.pk, "tarih": BUGUN.isoformat(), "kategori": "", "kor": "on", "aciklama": "yıl sonu"}, follow=True)
        s = StokSayimi.objects.get()
        self.assertRedirects(r, reverse("core:stok_sayim_belge", args=[s.pk]))
        self.assertEqual((s.no, s.kor, s.aciklama, s.satirlar.count(), s.created_by_id), (f"SY-{BUGUN.year}-0001", True, "yıl sonu", 1, self.yon.pk))
        self.assertContains(r, "açıldı")
        r = self.client.post(reverse("core:stok_sayim_yeni"), {"depo": self.depo.pk, "tarih": (BUGUN + datetime.timedelta(days=2)).isoformat()})
        self.assertContains(r, "bugünden sonra")
        self.assertEqual(StokSayimi.objects.count(), 1)

    def test_fark_ekrani_karar_gecersiz_ve_durum(self):
        a, s, sat = self.kur()
        g1 = ss.girdi_ekle(sat, kullanici=self.personel, miktar="6")
        ss.girdi_ekle(sat, kullanici=self.baskasi, miktar="5")                    # toplam 11 → fark +1 × 100
        self.client.force_login(self.yon)
        belge = reverse("core:stok_sayim_belge", args=[s.pk])
        r = self.client.get(belge)
        self.assertContains(r, "Fark / Karar")
        d = r.context["satirlar"][0]
        self.assertEqual((d["sayilan"], d["fark"], d["tutar"]), (D("11"), D("1"), D("100.00")))
        self.assertEqual(r.context["ozet"]["fazla_tutar"], D("100.00"))
        # karar kaydet
        r = self.client.post(reverse("core:stok_sayim_karar", args=[s.pk]), {f"karar_{sat.pk}": "DUZELT", f"not_{sat.pk}": "raf B sayıldı", "goster": "hepsi"}, follow=True)
        self.assertContains(r, "kararı kaydedildi")
        sat.refresh_from_db()
        self.assertEqual((sat.karar, sat.karar_notu, sat.karar_veren_id), ("DUZELT", "raf B sayıldı", self.yon.pk))
        self.assertEqual(self.client.get(belge, {"goster": "duzelt"}).context["ozet"]["duzelt_adet"], 1)
        self.assertEqual(len(self.client.get(belge, {"goster": "arastir"}).context["satirlar"]), 0)
        # girişi geçersiz yap → sayılmadı → Düzelt kalır ama fark yok; geçerli yapınca geri gelir
        self.client.post(reverse("core:stok_sayim_girdi_gecersiz", args=[g1.pk]), {"gecersiz": "1"})
        self.assertEqual(self.client.get(belge).context["satirlar"][0]["sayilan"], D("5"))
        self.client.post(reverse("core:stok_sayim_girdi_gecersiz", args=[g1.pk]), {"gecersiz": "0"})
        self.assertEqual(self.client.get(belge).context["satirlar"][0]["sayilan"], D("11"))
        # girişi kapat / aç
        self.client.post(reverse("core:stok_sayim_durum", args=[s.pk]), {"islem": "kapat"})
        s.refresh_from_db()
        self.assertEqual(s.durum, "KAPALI")
        self.assertContains(self.client.get(reverse("core:stok_sayim_giris", args=[s.pk]), follow=True), "kapalı")
        self.client.post(reverse("core:stok_sayim_durum", args=[s.pk]), {"islem": "ac"})
        s.refresh_from_db()
        self.assertEqual(s.durum, "ACIK")

    def test_belirsiz_maliyet_duzelt_hata_mesaji_ve_sil(self):
        f = self.kart("fiyatsiz")
        self.giris(f, "5", tutar=None)
        s = self.ac()
        sat = self.satir(s, f)
        ss.girdi_ekle(sat, kullanici=self.personel, miktar="3")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:stok_sayim_karar", args=[s.pk]), {f"karar_{sat.pk}": "DUZELT", "goster": "hepsi"}, follow=True)
        self.assertContains(r, "Düzelt verilemez")
        sat.refresh_from_db()
        self.assertEqual(sat.karar, "BEKLIYOR")
        self.assertContains(self.client.get(reverse("core:stok_sayim_belge", args=[s.pk])), "maliyet belirsiz")
        r = self.client.post(reverse("core:stok_sayim_durum", args=[s.pk]), {"islem": "sil"}, follow=True)
        self.assertRedirects(r, reverse("core:stok_sayimlari"))
        s.refresh_from_db()
        self.assertTrue(s.silindi)
        self.assertEqual(self.client.get(reverse("core:stok_sayim_belge", args=[s.pk])).status_code, 404)
