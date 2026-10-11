"""Varyant tanımları (E-1, docs/varyant_kararlari.md): seed SEMTA(varsayılan)/YOK/MR. MOP; servis CRUD + tek varsayılan + silme koruması;
stok kartında varyantlı işareti + boyutlar → standart StokVaryant; AYARLAR › Varyantlar ekranları + yetki; varyantsız kart davranışı aynı."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, KdvOrani, Stok, StokVaryant, Varyant
from core.services import varyant as vs
from core.services.birim import birim_olustur
from core.services.kategori import kategori_olustur
from core.services.stok import StokHatasi, stok_guncelle, stok_kopyala, stok_olustur


def _kdv():
    k, _ = KdvOrani.objects.get_or_create(oran=Decimal("20"), silindi=False, defaults={"aciklama": "ORAN"})
    return k


class VaryantTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("vyon", password="x")
        cls.bos = User.objects.create_user("vbos", password="x")
        ust = kategori_olustur(ad="hammadde", kod="150")
        cls.alt = kategori_olustur(ad="ambalaj", kod="50", ust_id=ust.pk)
        cls.adet = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        cls.kdv = _kdv()

    def stok(self, ad="afiş", **ek):
        kw = dict(ad=ad, kategori_id=self.alt.pk, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk,
                  cevirici=Decimal("1"), kdv_id=self.kdv.pk, satinalma_urunu=True, uretim_urunu=False)
        kw.update(ek)
        return stok_olustur(**kw)


class VaryantServisTest(VaryantTaban):
    def test_seed_uc_deger_semta_varsayilan(self):
        self.assertEqual(list(Varyant.objects.filter(silindi=False).order_by("sira").values_list("kod", flat=True)),
                         ["SEMTA", "YOK", "MR. MOP"])
        self.assertEqual(vs.varsayilan_varyant().kod, "SEMTA")
        self.assertTrue(all(v.aktif for v in Varyant.objects.all()))

    def test_olustur_buyuk_harf_benzersiz(self):
        v = vs.varyant_olustur(kod="nova", ad="Nova etiketi", ad_en="NOVA label", sira=40)
        self.assertEqual((v.kod, v.ad, v.ad_en, v.varsayilan), ("NOVA", "NOVA ETİKETİ", "NOVA label", False))   # TR büyük harf
        with self.assertRaisesMessage(vs.VaryantHatasi, "zaten kayıtlı"):
            vs.varyant_olustur(kod="NOVA", ad="x")
        with self.assertRaisesMessage(vs.VaryantHatasi, "Kod boş"):
            vs.varyant_olustur(kod=" ", ad="x")

    def test_tek_varsayilan(self):
        mop = Varyant.objects.get(kod="MR. MOP")
        vs.varyant_varsayilan_yap(mop)
        self.assertEqual(vs.varsayilan_varyant().kod, "MR. MOP")
        self.assertFalse(Varyant.objects.get(kod="SEMTA").varsayilan)
        semta = Varyant.objects.get(kod="SEMTA")
        vs.varyant_guncelle(semta, kod="SEMTA", ad="SEMTA", sira=10, varsayilan=True)
        self.assertEqual(Varyant.objects.filter(varsayilan=True, silindi=False).count(), 1)
        with self.assertRaisesMessage(vs.VaryantHatasi, "Varsayılan işareti kaldırılamaz"):
            vs.varyant_guncelle(semta, kod="SEMTA", ad="SEMTA", sira=10, varsayilan=False)
        v = vs.varyant_guncelle(semta, kod="SEMTA", ad="SEMTA", sira=10, aktif=False, varsayilan=True)   # varsayılan pasif olamaz
        self.assertTrue(v.aktif)

    def test_silme_korumasi(self):
        with self.assertRaisesMessage(vs.VaryantHatasi, "Varsayılan"):
            vs.varyant_sil(Varyant.objects.get(kod="SEMTA"))
        s = self.stok(varyantli=True, varyant_boyutlari=["AFIS"])
        mop = Varyant.objects.get(kod="MR. MOP")
        vs.stok_varyanti_bul_veya_olustur(s, {"AFIS": mop})
        with self.assertRaisesMessage(vs.VaryantHatasi, "kullanılıyor"):
            vs.varyant_sil(mop)
        yok = Varyant.objects.get(kod="YOK")
        vs.varyant_sil(yok)
        self.assertTrue(Varyant.objects.get(pk=yok.pk).silindi)


class StokVaryantTest(VaryantTaban):
    def test_varyantsiz_kart_bugunku_gibi(self):
        s = self.stok()
        self.assertEqual((s.varyantli, s.varyant_boyutlari, StokVaryant.objects.filter(stok=s).count()), (False, [], 0))
        with self.assertRaisesMessage(vs.VaryantHatasi, "varyantlı bir kart değil"):
            vs.stok_varyanti_bul_veya_olustur(s, {})

    def test_varyantli_kart_boyut_zorunlu_ve_standart_varyant_acilir(self):
        with self.assertRaisesMessage(StokHatasi, "en az bir varyant boyutu"):
            self.stok(varyantli=True)
        s = self.stok(varyantli=True, varyant_boyutlari=["YAN_ETIKET", "MARKALAMA"])      # sıra sabitlenir
        self.assertEqual(s.varyant_boyutlari, ["MARKALAMA", "YAN_ETIKET"])
        sv = StokVaryant.objects.get(stok=s)
        semta = Varyant.objects.get(kod="SEMTA")
        self.assertEqual((sv.standart, sv.markalama_id, sv.yan_etiket_id, sv.afis_id, sv.anahtar),
                         (True, semta.pk, semta.pk, None, f"MARKALAMA={semta.pk}|YAN_ETIKET={semta.pk}"))
        self.assertEqual(vs.varyant_etiketi(sv), "Markalama: SEMTA · Yan etiket: SEMTA")
        self.assertEqual(vs.standart_varyant(s).pk, sv.pk)                                 # idempotent
        mop = Varyant.objects.get(kod="MR. MOP")
        sv2 = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": mop, "YAN_ETIKET": None})           # boş boyut = YOK (karar 9)
        self.assertEqual((sv2.standart, vs.varyant_etiketi(sv2)), (False, "Markalama: MR. MOP · Yan etiket: YOK"))
        self.assertEqual(vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": mop.pk}).pk, sv2.pk)
        self.assertEqual(StokVaryant.objects.filter(stok=s, silindi=False).count(), 2)
        with self.assertRaisesMessage(StokHatasi, "Geçersiz varyant boyutu"):
            self.stok(ad="x", varyantli=True, varyant_boyutlari=["RENK"])

    def guncelle(self, s, **ek):
        kw = dict(ad=s.ad, uretim_birimi_id=s.uretim_birimi_id, fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                  kdv_id=s.kdv_id, satinalma_urunu=True, uretim_urunu=False, varyantli=s.varyantli,
                  varyant_boyutlari=list(s.varyant_boyutlari))
        kw.update(ek)
        return stok_guncelle(s, **kw)

    def test_guncelleme_boyut_degisince_esitlenir_varyantsiz_olunca_kapanir(self):
        s = self.stok(varyantli=True, varyant_boyutlari=["AFIS", "KILAVUZ"])
        mop = Varyant.objects.get(kod="MR. MOP")
        semta = Varyant.objects.get(kod="SEMTA")
        vs.stok_varyanti_bul_veya_olustur(s, {"AFIS": mop, "KILAVUZ": semta})
        self.guncelle(s, varyant_boyutlari=["AFIS"])                    # kılavuz boyutu kalktı → anahtarlar yeniden
        s.refresh_from_db()
        satirlar = {sv.anahtar: sv.standart for sv in StokVaryant.objects.filter(stok=s, silindi=False)}
        self.assertEqual(satirlar, {f"AFIS={semta.pk}": True, f"AFIS={mop.pk}": False})
        with self.assertRaises(StokHatasi):
            self.guncelle(s, varyantli=True, varyant_boyutlari=[])
        self.assertTrue(StokVaryant.objects.filter(stok=s, silindi=False).exists())   # hata → değişmedi
        self.guncelle(s, varyantli=False, varyant_boyutlari=[])
        s.refresh_from_db()
        self.assertEqual((s.varyantli, s.varyant_boyutlari, StokVaryant.objects.filter(stok=s, silindi=False).count()), (False, [], 0))

    def test_kopyada_varyant_ayari_tasinir(self):
        s = self.stok(varyantli=True, varyant_boyutlari=["AFIS"])
        k = stok_kopyala(s)
        self.assertEqual((k.varyantli, k.varyant_boyutlari), (True, ["AFIS"]))
        self.assertTrue(StokVaryant.objects.filter(stok=k, standart=True, silindi=False).exists())


class VaryantEkranTest(VaryantTaban):
    def test_liste_ekle_duzenle_varsayilan_sil(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:varyantlar"))
        self.assertContains(r, "MR. MOP")
        self.assertContains(r, "★ varsayılan")
        self.assertContains(self.client.get(reverse("core:tanim_listeleri")), "Varyantlar")
        r = self.client.post(reverse("core:varyant_ekle"), {"sira": "40", "kod": "nova", "ad": "Nova", "ad_en": "NOVA", "aktif": "on"})
        self.assertEqual(r.status_code, 302)
        v = Varyant.objects.get(kod="NOVA")
        r = self.client.post(reverse("core:varyant_duzenle", args=[v.pk]), {"sira": "41", "kod": "NOVA", "ad": "Nova Etiket", "aktif": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Varyant.objects.get(pk=v.pk).ad, "NOVA ETİKET")
        r = self.client.post(reverse("core:varyant_ekle"), {"sira": "1", "kod": "NOVA", "ad": "x", "aktif": "on"})
        self.assertContains(r, "zaten kayıtlı")                                      # formda kalır
        self.client.post(reverse("core:varyant_varsayilan_yap", args=[v.pk]))
        self.assertEqual(vs.varsayilan_varyant().pk, v.pk)
        r = self.client.post(reverse("core:varyant_sil", args=[v.pk]), follow=True)
        self.assertTrue(any("silinemez" in str(m) for m in r.context["messages"]))
        yok = Varyant.objects.get(kod="YOK")
        self.client.post(reverse("core:varyant_sil", args=[yok.pk]))
        self.assertTrue(Varyant.objects.get(pk=yok.pk).silindi)

    def test_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:varyantlar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:varyant_ekle")).status_code, 403)

    def test_stok_formu_varyantli_kaydeder_ve_detayda_gorunur(self):
        EkranYetki.objects.get_or_create(kullanici=self.bos, ekran_kod="stoklar")
        self.client.force_login(self.bos)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "afiş a tipi", "kategori": str(self.alt.pk), "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.adet.pk),
            "cevirici": "1", "kdv": str(self.kdv.pk), "satinalma_urunu": "on", "varyantli": "on", "varyant_boyutlari": ["AFIS"]})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="AFİŞ A TİPİ")
        self.assertEqual((s.varyantli, s.varyant_boyutlari), (True, ["AFIS"]))
        d = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertContains(d, "Varyantlı Kart")
        self.assertContains(d, "Afiş: SEMTA")
        self.assertContains(d, "★ standart")
        f = self.client.get(reverse("core:stok_duzenle", args=[s.pk]))
        self.assertContains(f, 'value="AFIS" id="id_varyant_boyutlari_2" checked')     # düzenleme formunda boyut işaretli gelir
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "boyutsuz", "kategori": str(self.alt.pk), "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.adet.pk),
            "cevirici": "1", "kdv": str(self.kdv.pk), "satinalma_urunu": "on", "varyantli": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "en az bir boyut")
        self.assertFalse(Stok.objects.filter(ad="BOYUTSUZ").exists())
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "perçin", "kategori": str(self.alt.pk), "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.adet.pk),
            "cevirici": "1", "kdv": str(self.kdv.pk), "satinalma_urunu": "on"})
        self.assertEqual(r.status_code, 302)
        p = Stok.objects.get(ad="PERÇİN")
        self.assertEqual((p.varyantli, p.varyant_boyutlari), (False, []))
        self.assertNotContains(self.client.get(reverse("core:stok_detay", args=[p.pk])), "Varyantlı Kart")
