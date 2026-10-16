"""Fatura formunda satırda seçilen KDV oranı kaydedilir (2026-10-16, fix/satis-kdv-secimi).
Eski hata: FaturaSatirForm.clean stoklu satırda seçilen KDV'yi siliyordu (cd["kdv"] = None) -> servis hep stok kartının KDV'sini uyguluyordu (#243: %0 seçildi, %20 yazıldı).
Kural: stok kartının KDV'si yalnız satır ilk seçilirken formda varsayılan gelir (JS; sunucu da seçileni AYNEN kaydeder); alış ve satışta aynı; faturasız faturada varsayılan %0.
Muhasebe/fiş üretimi DEĞİŞMEDİ (fiş karşılaştırma testleri değişmeden geçer)."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Fatura, HesapPlani, KdvOrani
from core.services.fatura import fatura_olustur
from core.tests.test_fatura_liste import ListeTaban

D = datetime.date
Dc = Decimal


class KdvTaban(ListeTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        k191, k391 = HesapPlani.objects.get(hesap_kodu="191"), HesapPlani.objects.get(hesap_kodu="391")
        cls.oran = {20: cls.kdv}
        for o in (0, 1, 10):
            cls.oran[o] = KdvOrani.objects.create(aciklama=f"%{o:02d} KDV", oran=Dc(o), hesap_borc=k191, hesap_alacak=k391)

    def veri(self, tip, cari, satirlar=(("1", "100", None),), **ek):
        v = {"tip": tip.pk, "cari": cari.pk, "tarih": "2026-03-10", "para_birimi": "TRY", "fatura_no": "F-1",
             "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, (m, f, kdv) in enumerate(satirlar):
            v.update({f"form-{i}-stok": self.stok.pk, f"form-{i}-miktar": m, f"form-{i}-birim_fiyat": f})
            if kdv is not None:
                v[f"form-{i}-kdv"] = self.oran[kdv].pk
        v.update(ek)
        return v

    def satis_ekle(self, kdv, **ek):
        self.client.force_login(self.su)
        return self.client.post(reverse("core:satis_fatura_ekle"), self.veri(self.satis, self.musteri, (("1", "2000", kdv),), **ek))

    def alis_ekle(self, kdv, **ek):
        self.client.force_login(self.su)
        return self.client.post(reverse("core:alis_fatura_ekle"), self.veri(self.alis, self.tedarikci, (("1", "2000", kdv),), **ek))

    def kdv_hesap_toplami(self, fatura, kod):
        fatura.refresh_from_db()
        return sum((s.alacak + s.borc for s in fatura.fis.satirlar.filter(silindi=False, hesap__hesap_kodu=kod)), Dc("0"))

    def satir_oran(self, fatura):
        (s,) = fatura.satirlar.filter(silindi=False)
        return s.kdv.oran


class SecilenKdvKaydedilirTest(KdvTaban):
    def test_satis_ekle_secilen_oran_oldugu_gibi_kaydedilir(self):
        for oran in (0, 1, 10, 20):
            with self.subTest(oran=oran):
                Fatura.objects.all().delete()
                r = self.satis_ekle(oran, fatura_no=f"S-{oran}")
                self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
                f = Fatura.objects.get()
                self.assertEqual(self.satir_oran(f), Dc(oran))                                         # stok kartı %20 olsa da
                self.assertEqual(self.kdv_hesap_toplami(f, "391"), (Dc("2000") * oran / 100).quantize(Dc("0.01")))

    def test_alis_ekle_ayni_davranir(self):
        for oran in (0, 1, 10, 20):
            with self.subTest(oran=oran):
                Fatura.objects.all().delete()
                self.assertEqual(self.alis_ekle(oran, fatura_no=f"A-{oran}").status_code, 302)
                f = Fatura.objects.get()
                self.assertEqual(self.satir_oran(f), Dc(oran))
                self.assertEqual(self.kdv_hesap_toplami(f, "191"), (Dc("2000") * oran / 100).quantize(Dc("0.01")))

    def test_satis_duzenle_secilen_oran_kaydedilir_ve_fis_yenilenir(self):
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="D-1",
                           satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "2000"}])
        self.assertEqual((self.satir_oran(f), self.kdv_hesap_toplami(f, "391")), (Dc("20"), Dc("400.00")))
        self.client.force_login(self.su)
        for oran in (0, 1, 10, 20):
            with self.subTest(oran=oran):
                r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(self.satis, self.musteri, (("1", "2000", oran),), fatura_no="D-1"))
                self.assertEqual(r.status_code, 302)
                f.refresh_from_db()
                self.assertEqual(self.satir_oran(f), Dc(oran))
                self.assertEqual(self.kdv_hesap_toplami(f, "391"), (Dc("2000") * oran / 100).quantize(Dc("0.01")))

    def test_alis_duzenle_secilen_oran_kaydedilir(self):
        f = self.a_fatura(no="AD-1", miktar="1", fiyat="2000")
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(self.alis, self.tedarikci, (("1", "2000", 10),), fatura_no="AD-1"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual((self.satir_oran(f), self.kdv_hesap_toplami(f, "191")), (Dc("10"), Dc("200.00")))

    def test_ayni_faturada_satirlar_farkli_oran_alabilir(self):
        self.client.force_login(self.su)
        v = self.veri(self.satis, self.musteri, (("1", "1000", 0), ("1", "1000", 10)), fatura_no="K-1")
        self.assertEqual(self.client.post(reverse("core:satis_fatura_ekle"), v).status_code, 302)
        f = Fatura.objects.get()
        self.assertEqual(sorted(s.kdv.oran for s in f.satirlar.filter(silindi=False)), [Dc("0"), Dc("10")])
        self.assertEqual(self.kdv_hesap_toplami(f, "391"), Dc("100.00"))

    def test_stok_karti_kdv_sonradan_degisse_de_kayitli_satir_degismeden_kaydedilir(self):
        f = self.a_fatura(no="DK-1", miktar="1", fiyat="1000")
        fis_pk, ilk_kdv = f.fis_id, self.kdv_hesap_toplami(f, "191")
        self.stok.kdv = self.oran[10]
        self.stok.save(update_fields=["kdv"])
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual(r.context["formset"].forms[0].initial["kdv"], self.kdv.pk)                    # form satırın KENDİ KDV'siyle gelir
        self.client.post(reverse("core:fatura_duzenle", args=[f.pk]),
                         self.veri(self.alis, self.tedarikci, (("1", "1000", 20),), fatura_no="DK-1"))
        f.refresh_from_db()
        self.assertEqual((f.fis_id, self.satir_oran(f), self.kdv_hesap_toplami(f, "191")), (fis_pk, Dc("20"), ilk_kdv))


class VarsayilanTest(KdvTaban):
    def test_faturasiz_satista_kdv_gelmezse_varsayilan_sifir(self):
        r = self.satis_ekle(None, fatura_no="", faturasiz="on")
        self.assertEqual(r.status_code, 302)
        f = Fatura.objects.get()
        self.assertEqual((f.faturasiz, self.satir_oran(f)), (True, Dc("0")))
        self.assertEqual(self.kdv_hesap_toplami(f, "391"), Dc("0"))

    def test_faturasiz_olmayanda_kdv_gelmezse_stok_karti(self):
        self.assertEqual(self.satis_ekle(None, fatura_no="N-1").status_code, 302)
        self.assertEqual(self.satir_oran(Fatura.objects.get()), Dc("20"))

    def test_faturasizda_acik_secim_varsayilani_ezer(self):
        self.assertEqual(self.satis_ekle(10, fatura_no="", faturasiz="on").status_code, 302)
        self.assertEqual(self.satir_oran(Fatura.objects.get()), Dc("10"))

    def test_faturasiz_duzenlemede_de_kdv_gelmezse_sifir(self):
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="", faturasiz=True,
                           satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "2000", "kdv_id": self.kdv.pk}])
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(self.satis, self.musteri, (("1", "2000", None),), fatura_no="", faturasiz="on"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.satir_oran(f), Dc("0"))

    def test_forma_stok_ve_sifir_kdv_varsayilan_haritalari_gelir(self):
        self.client.force_login(self.su)
        for ad in ("satis_fatura_ekle", "alis_fatura_ekle"):
            r = self.client.get(reverse(f"core:{ad}"))
            self.assertEqual(r.context["stok_kdv_id"][str(self.stok.pk)], self.kdv.pk)               # stok kartı KDV'si -> satır ilk seçilirken varsayılan
            self.assertEqual(r.context["kdv_sifir_id"], self.oran[0].pk)                              # faturasız varsayılanı
            html = r.content.decode()
            self.assertIn('id="stok-kdv-id"', html)
            self.assertIn('id="kdv-sifir-id"', html)
            self.assertIn("function kdvOnDoldur", html)
            self.assertIn("kdv-secim { display:block; }", html)                                      # stoklu satırda da KDV seçici görünür

    def test_kdv_secici_stoklu_satirda_form_alani_olarak_render_edilir(self):
        self.client.force_login(self.su)
        html = self.client.get(reverse("core:satis_fatura_ekle")).content.decode()
        self.assertIn('name="form-0-kdv"', html)
