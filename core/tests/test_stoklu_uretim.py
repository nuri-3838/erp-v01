"""STOKLU ÜRETİM bayrağı (2026-10-12): işaretli ara mamul başka ÜS'nin zincirinde planlanmaz (alt zincir yok, istasyon emri yok) — ihtiyaç stoktan
ayrılır (StokAyirma), yetmeyen kısım ÜS'nin Eksik Malzeme listesine "stoklu ara mamul" olarak düşer, ÜS yine açılır; kart hedef ürünse kendi
zinciri normal planlanır; açık ÜS'lerin planı değişmez (eksik listesi dahil), yalnız yeni açılan/revize edilen etkilenir; varsayılan kapalı;
kart formu alanları (stoklu üretim, parti miktarı)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Stok, StokHareket
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.services.uretim import ihtiyac_hesapla, uretim_emri_olustur
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_stoktan_karsilama import StoktanTaban

D = Decimal


class StokluTaban(StoktanTaban):
    """profil → kesim → kesilmiş → büküm → BÜKÜLMÜŞ (stoklu adayı) → paketleme → 152 mamul."""

    def us(self, hedef="10", **kw):
        return uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": hedef}], depo_id=self.depo.pk,
                                   tarih=date(2026, 1, 10), kullanici=self.yon, stoktan={(self.mamul.pk, None): D("0")}, **kw)

    def ops(self, emir):
        return {ie.operasyon_id: ie.planlanan for ie in emir.istasyon_emirleri.filter(silindi=False).exclude(durum="IPTAL")}

    def stoklu_yap(self, acik=True):
        Stok.objects.filter(pk=self.bukulmus.pk).update(stoklu_uretim=acik)
        self.bukulmus.refresh_from_db()


class PlanlamaTest(StokluTaban):
    def test_bayrak_kapali_bugunku_gibi(self):
        emir = self.us()
        self.assertEqual(self.ops(emir), {self.pak_op.pk: D("10"), self.bukum_op.pk: D("10"), self.kesim_op.pk: D("5")})
        self.assertEqual([e["stok"].pk for e in uretim.uretim_emri_eksikleri(emir)], [])

    def test_stoklu_parca_planlanmaz_stoktan_ayrilir_eksik_listesi(self):
        self.stoklu_yap()
        hareket_ekle(stok_id=self.bukulmus.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"))
        emir = self.us()                                                                             # 10 mamul: bükülmüş 3 stoktan, 7 eksik
        self.assertEqual(self.ops(emir), {self.pak_op.pk: D("10")})                                  # büküm/kesim emri YOK
        self.assertEqual(sa.ayrilan_miktar(emir, self.bukulmus), D("3"))
        eks = uretim.uretim_emri_eksikleri(emir)
        self.assertEqual([(e["stok"].pk, e["gerekli"], e["kullanilabilir"], e["eksik"], e["stoklu"]) for e in eks],
                         [(self.bukulmus.pk, D("10"), D("3"), D("7"), True)])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, "stoklu ara mamul")
        self.assertContains(r, "IK-BUKULMUS")

    def test_stoklu_parca_hedefse_kendi_zinciri_planlanir(self):
        self.stoklu_yap()
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": "4"}], depo_id=self.depo.pk, tarih=date(2026, 1, 10),
                                   kullanici=self.yon)
        self.assertEqual(self.ops(emir), {self.bukum_op.pk: D("4"), self.kesim_op.pk: D("2")})
        self.assertEqual(uretim.uretim_emri_eksikleri(emir), [])

    def test_acik_us_plani_ve_eksik_listesi_degismez_revize_etkilenir(self):
        emir = self.us()                                                                             # bayrak kapalıyken açıldı
        self.stoklu_yap()                                                                            # sonra işaretlendi
        self.assertEqual(self.ops(emir), {self.pak_op.pk: D("10"), self.bukum_op.pk: D("10"), self.kesim_op.pk: D("5")})
        self.assertEqual(uretim.uretim_emri_eksikleri(emir), [])                                     # mevcut emirler zincirle: eksik yok
        uretim.uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "12"}], kullanici=self.yon,
                                  stoktan={(self.mamul.pk, None): D("0")})
        self.assertEqual(self.ops(emir), {self.pak_op.pk: D("12")})                                  # revize: stoklu parça artık planlanmaz
        self.assertEqual([(e["stok"].pk, e["eksik"], e["stoklu"]) for e in uretim.uretim_emri_eksikleri(emir)], [(self.bukulmus.pk, D("12"), True)])

    def test_ihtiyac_hesapla_varsayilan_brut_hesap_etkilenmez(self):
        self.stoklu_yap()
        s = ihtiyac_hesapla([(self.mamul, D("10"))])                                                 # Ürün Ağacı vb.: bayrak uygulanmaz
        self.assertEqual({p["stok"].pk for p in s["plan"]}, {self.mamul.pk, self.bukulmus.pk, self.kesilmis.pk})
        s = ihtiyac_hesapla([(self.mamul, D("10"))], stoklu_uretim=True)
        self.assertEqual({p["stok"].pk for p in s["plan"]}, {self.mamul.pk})
        s = ihtiyac_hesapla([(self.mamul, D("10"))], stoklu_uretim=True, stoklu_haric={self.bukulmus.pk})
        self.assertEqual({p["stok"].pk for p in s["plan"]}, {self.mamul.pk, self.bukulmus.pk, self.kesilmis.pk})


class KartFormuTest(Taban):
    def test_alanlar_kaydedilir_ve_varsayilan_kapali(self):
        from core.services import stok as stok_servis
        self.assertFalse(self.bukulmus.stoklu_uretim)
        self.assertEqual(self.bukulmus.parti_miktari, D("0"))
        from core.models import KdvOrani
        kdv = KdvOrani.objects.create(oran=D("20"), aciklama="G")
        yon = User.objects.create_superuser("suyon", password="x")
        self.client.force_login(yon)
        r = self.client.get(reverse("core:stok_duzenle", args=[self.bukulmus.pk]))
        self.assertContains(r, "Stoklu üretim")
        self.assertContains(r, "Parti Miktarı")
        stok_servis.stok_guncelle(self.bukulmus, ad=self.bukulmus.ad, uretim_birimi_id=self.birim.pk, fatura_birimi_id=self.birim.pk, cevirici=D("1"),
                                  kdv_id=kdv.pk, uretim_urunu=True, stoklu_uretim=True, parti_miktari="25", kullanici=yon)
        self.bukulmus.refresh_from_db()
        self.assertEqual((self.bukulmus.stoklu_uretim, self.bukulmus.parti_miktari), (True, D("25")))
        with self.assertRaises(Exception):
            stok_servis.stok_guncelle(self.bukulmus, ad=self.bukulmus.ad, uretim_birimi_id=self.birim.pk, fatura_birimi_id=self.birim.pk,
                                      cevirici=D("1"), kdv_id=kdv.pk, uretim_urunu=True, parti_miktari="-1")
        r = self.client.get(reverse("core:stoklar"))
        self.assertContains(r, "📦 stoklu")
