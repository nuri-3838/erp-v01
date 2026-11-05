"""İrsaliyeden oluşan faturanın DÜZENLE ekranı, varyantlı kartta hiçbir şey değiştirilmeden Kaydet'te "irsaliyeye bağlı satırda varyant değiştirilemez" demesin (2026-11-05, SMT2026000000014).

Kök neden: varyant verilmeyen irsaliye kalemi/fatura satırı NULL saklanır (= kartın varsayılan varyantı); sayfa JS'i (``_varyant_js.html``) boş gizli alana kartın
varsayılan varyantını 'v:…' olarak yazar; doğrulama bunu NULL ile kıyaslayıp "değişti" sayıyordu. Düzeltme: iki taraf da GERÇEK (çözülmüş) varyantla karşılaştırılır; Düzenle
ekranı gizli alanı dolu ön-doldurur; gerçek varyant değişikliği hâlâ reddedilir. Satış (SSI→fatura) ve alış (çoklu irsaliye→fatura) yolları."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Stok, Varyant
from core.services import fatura as fatura_servis
from core.services import varyant as vs
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur
from core.services.teklif_siparis import irsaliyeleri_faturaya_cevir, teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_coklu_irsaliye_fatura import CokluTaban
from core.tests.test_satis_irsaliye import SatisIrsaliyeTaban

D = datetime.date
Dc = Decimal
DORT = ["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"]


def _secim(semta, mop=None, afis=None):
    m = mop or semta
    return f"v:MARKALAMA={m.pk}|KILAVUZ={semta.pk}|AFIS={(afis or semta).pk}|YAN_ETIKET={semta.pk}"


def _varyantli_yap(stok):
    Stok.objects.filter(pk=stok.pk).update(varyantli=True, varyant_boyutlari=DORT)
    stok.refresh_from_db()
    return vs.standart_varyant(stok)


def _post_verisi(fatura, satirlar, varyantlar, **ust):
    v = {"tip": fatura.tip_id, "cari": fatura.cari_id, "tarih": fatura.tarih.isoformat(), "para_birimi": "TRY", "fatura_no": "VRY-1", "depo": fatura.depo_id,
         "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
    v.update(ust)
    for n, s in enumerate(satirlar):
        v.update({f"form-{n}-stok": s.stok_id, f"form-{n}-miktar": s.miktar, f"form-{n}-birim_fiyat": s.birim_fiyat, f"form-{n}-kdv": s.kdv_id,
                  f"form-{n}-irsaliye_kalem": s.irsaliye_kalem_id, f"form-{n}-varyant": varyantlar[n]})
    return v


class SatisVaryantDuzenleTest(SatisIrsaliyeTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.varsayilan = _varyantli_yap(cls.stok)
        cls.semta, cls.mop = Varyant.objects.get(kod="SEMTA"), Varyant.objects.get(kod="MR. MOP")
        cls.admin = User.objects.create_superuser("vyn", password="x")

    def faturali(self, varyant=None):
        """Satış irsaliyesi (kalem varyantı: None → NULL = varsayılan, ya da 'v:…') → taslak satış faturası."""
        v = {} if varyant is None else {"varyant": varyant}
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 5), depo_id=self.da.pk,
                       satirlar=[{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "100", **v}])
        i = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="SATIS", cari_id=self.musteri.pk, tarih=D(2026, 3, 10), depo_id=self.da.pk,
                                   satirlar=[{"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "500", **v}], kullanici=None)
        teklif_siparis_onayla(i)
        i.refresh_from_db()
        f, _ = irsaliyeleri_faturaya_cevir([i])
        f.tip = self.satis
        f.save()
        f.refresh_from_db()
        return i, f

    def duzenle(self, f, varyant):
        s = f.satirlar.get(silindi=False)
        return fatura_guncelle(f, tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=f.tarih, depo_id=self.da.pk, fatura_no="VRY-1",
                               satirlar=[{"stok_id": s.stok_id, "miktar": s.miktar, "birim_fiyat": s.birim_fiyat, "varyant": varyant, "irsaliye_kalem_id": s.irsaliye_kalem_id}])

    # --- NULL (varsayılan) varyantlı irsaliye: asıl hata
    def test_kalem_ve_satir_null_varsayilan_secimi_degistirmeden_kaydet_basarili(self):
        i, f = self.faturali()
        k, s = i.kalemler.get(), f.satirlar.get(silindi=False)
        self.assertEqual((k.varyant_id, s.varyant_id), (None, None))                                   # SMT2026000000014'teki durum
        self.duzenle(f, vs.secim_degeri(self.varsayilan))                                              # JS'in boş alana yazdığı değer
        self.duzenle(f, None)                                                                          # JS'siz POST (boş) eskisi gibi geçer
        self.duzenle(f, "")

    def test_gercekten_baska_varyant_secilirse_hata_surer(self):
        _, f = self.faturali()
        with self.assertRaisesMessage(FaturaHatasi, "irsaliyeye bağlı satırda varyant değiştirilemez"):
            self.duzenle(f, _secim(self.semta, self.mop))                                              # MR. MOP markalama
        with self.assertRaisesMessage(FaturaHatasi, "irsaliyeye bağlı satırda varyant değiştirilemez"):
            self.duzenle(f, _secim(self.semta, afis=Varyant.objects.get(kod="YOK")))                   # afiş YOK

    def test_ekran_gizli_alan_dolu_gelir_degistirmeden_kaydet_302(self):
        i, f = self.faturali()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual(r.status_code, 200)
        ilk = r.context["formset"].forms[0].initial["varyant"]
        self.assertEqual(ilk, vs.secim_degeri(self.varsayilan))
        self.assertTrue(ilk.startswith("v:"))
        self.assertContains(r, f'value="{ilk}"')                                                       # sunucu gizli alanı DOLU basar
        satirlar = list(f.satirlar.filter(silindi=False).order_by("id"))
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), _post_verisi(f, satirlar, [ilk]))
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and (r.context["formset"].errors, list(r.context["fform"].errors.values())))
        self.assertEqual(f.satirlar.get(silindi=False).irsaliye_kalem_id, i.kalemler.get().pk)         # bağ korundu

    def test_ekran_varyant_degistirilirse_hata_gosterir(self):
        _, f = self.faturali()
        self.client.force_login(self.admin)
        satirlar = list(f.satirlar.filter(silindi=False).order_by("id"))
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), _post_verisi(f, satirlar, [_secim(self.semta, self.mop)]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "varyant değiştirilemez")

    # --- özel (standart dışı) varyantlı irsaliye
    def test_ozel_varyantli_kalem_ayni_secim_gecer_varsayilana_donmek_hata(self):
        ozel = _secim(self.semta, self.mop)
        i, f = self.faturali(ozel)
        kalem_v = i.kalemler.get().varyant
        self.assertIsNotNone(kalem_v)
        self.assertEqual(f.satirlar.get(silindi=False).varyant_id, kalem_v.pk)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        ilk = r.context["formset"].forms[0].initial["varyant"]
        self.assertEqual(ilk, vs.secim_degeri(kalem_v))
        satirlar = list(f.satirlar.filter(silindi=False).order_by("id"))
        self.assertEqual(self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), _post_verisi(f, satirlar, [ilk])).status_code, 302)
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), _post_verisi(f, satirlar, [vs.secim_degeri(self.varsayilan)]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "varyant değiştirilemez")

    # --- "değişmediyse fişe dokunma": NULL kayıt ile JS'in doldurduğu varsayılan aynı imza
    def test_degismeyen_satir_imzasi_null_ve_varsayilan_esit(self):
        _, f = self.faturali()
        s = f.satirlar.get(silindi=False)
        g = {"stok_id": s.stok_id, "miktar": s.miktar, "birim_fiyat": s.birim_fiyat, "kdv_id": s.kdv_id, "varyant": vs.secim_degeri(self.varsayilan)}
        girdi = fatura_servis._girdi_satir_imzasi(g, 1, self.satis, sahsi=False)
        kayit = fatura_servis._kayit_satir_imzasi(s, sahsi=False)
        self.assertIsNotNone(girdi)
        self.assertEqual(girdi, kayit)
        self.assertEqual(fatura_servis.satir_etkin_varyant_id(s), self.varsayilan.pk)

    def test_etkin_varyant_varyantsiz_kart_none(self):
        _, f = self.faturali()
        Stok.objects.filter(pk=self.stok.pk).update(varyantli=False)
        s = f.satirlar.get(silindi=False)
        s.stok.refresh_from_db()
        self.assertIsNone(fatura_servis.satir_etkin_varyant_id(s))


class AlisVaryantDuzenleTest(CokluTaban):
    """Alış tarafı: çoklu irsaliye → tek fatura (satır kaleme bağlı), aynı doğrulama."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.varsayilan = _varyantli_yap(cls.stok)
        cls.semta, cls.mop = Varyant.objects.get(kod="SEMTA"), Varyant.objects.get(kod="MR. MOP")

    def faturali(self):
        i1, i2 = self.iki_irsaliye()
        kalem = i1.kalemler.get(stok=self.stok)
        self.assertIsNone(kalem.varyant_id)
        return i1, i2, self.cevir(i1, i2)

    def test_servis_null_kalemde_varsayilan_secim_gecer_baska_secim_reddedilir(self):
        _, _, f = self.faturali()
        sec = vs.secim_degeri(self.varsayilan)
        self.duzenle(f, self.girdiler(f, {0: {"varyant": sec}, 2: {"varyant": sec}}))                  # iki A satırı (stok SO1) varsayılan
        with self.assertRaisesMessage(FaturaHatasi, "irsaliyeye bağlı satırda varyant değiştirilemez"):
            self.duzenle(f, self.girdiler(f, {0: {"varyant": _secim(self.semta, self.mop)}}))

    def test_ekran_varyantli_satir_dolu_gelir_degistirmeden_kaydet_302(self):
        _, _, f = self.faturali()
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        formlar = r.context["formset"].forms
        satirlar = list(f.satirlar.filter(silindi=False).order_by("id"))
        ilk = [fm.initial["varyant"] for fm in formlar]
        for s, v in zip(satirlar, ilk):
            self.assertEqual(bool(v), s.stok_id == self.stok.pk)                                       # yalnız varyantlı kart (SO1) dolu; diğerleri boş
        post = _post_verisi(f, satirlar, ilk, tip=self.tip.pk, cari=self.cari.pk, tarih="2026-06-10")
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), post)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        self.assertEqual(f.satirlar.filter(silindi=False, irsaliye_kalem__isnull=False).count(), 4)
        n = ilk.index(vs.secim_degeri(self.varsayilan))
        post[f"form-{n}-varyant"] = _secim(self.semta, self.mop)                                       # gerçek değişiklik
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), post)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "varyant değiştirilemez")
