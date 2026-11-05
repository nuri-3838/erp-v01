"""Üretim formlarında (Tümünü üret / Bu istasyonu üret / Üretildi) bağlı ONAYSIZ satış irsaliyesi tarih uyarısı: ÜS'nin kaynak siparişine bağlı taslak irsaliyenin tarihi üretim
tarihinden ÖNCEYSE uyarı gösterilir (üretimi ENGELLEMEZ); tarih seçicilerde istemci tarafı onay için veri özniteliği taşınır. Varsayılan tarih bugündür."""
from datetime import date

from django.urls import reverse

from core.models import TeklifSiparis
from core.services import uretim
from core.tests.test_uretim_siparisi_ekranlar import EkranTaban

IRS_TARIH = date(2026, 6, 20)                # sipariş/ÜS GUN = 28.06.2026'dan ÖNCE (üretim tarihi sonrası seçilince uyarı)


class IrsaliyeTarihUyariTest(EkranTaban):
    def irsaliye(self, durum="TASLAK", tarih=IRS_TARIH, silindi=False):
        return TeklifSiparis.objects.create(
            belge_tur="IRSALIYE", yon="SATIS", cari=self.sip.cari, tarih=tarih, para_birimi="TRY", durum=durum, silindi=silindi,
            kaynak_siparis=self.sip, belge_no="SSI-TEST-1", depo=self.emir.depo, created_by=self.yon, updated_by=self.yon)

    def test_servis_taslak_irsaliye_ve_uyari_kosullari(self):
        self.kur()
        self.assertEqual(uretim.bagli_taslak_irsaliyeler([self.emir]), {})                       # irsaliye yok
        self.assertEqual(uretim.irsaliye_tarih_uyarisi(self.emir, date(2026, 7, 1)), "")
        irs = self.irsaliye()
        self.assertEqual(uretim.bagli_taslak_irsaliyeler([self.emir]), {self.emir.pk: ("SSI-TEST-1", IRS_TARIH)})
        uyari = uretim.irsaliye_tarih_uyarisi(self.emir, date(2026, 7, 1))
        self.assertIn("Bağlı irsaliye SSI-TEST-1 20.06.2026 tarihli; üretim tarihi sonrası seçildi (01.07.2026)", uyari)
        self.assertEqual(uretim.irsaliye_tarih_uyarisi(self.emir, IRS_TARIH), "")                # aynı gün: uyarı yok
        self.assertEqual(uretim.irsaliye_tarih_uyarisi(self.emir, date(2026, 6, 1)), "")         # öncesi: uyarı yok
        irs.durum = "ONAYLI"
        irs.save(update_fields=["durum"])
        self.assertEqual(uretim.bagli_taslak_irsaliyeler([self.emir]), {})                       # onaylı irsaliye için uyarı yok
        self.assertEqual(uretim.irsaliye_tarih_uyarisi(self.emir, date(2026, 7, 1)), "")

    def test_en_erken_taslak_irsaliye_ve_silinmis_irsaliye_yok_sayilir(self):
        self.kur()
        self.irsaliye(tarih=date(2026, 6, 25))
        TeklifSiparis.objects.create(
            belge_tur="IRSALIYE", yon="SATIS", cari=self.sip.cari, tarih=date(2026, 6, 10), para_birimi="TRY", durum="TASLAK", silindi=True,
            kaynak_siparis=self.sip, belge_no="SSI-SILINMIS", created_by=self.yon, updated_by=self.yon)
        self.assertEqual(uretim.bagli_taslak_irsaliyeler([self.emir]), {self.emir.pk: ("SSI-TEST-1", date(2026, 6, 25))})

    def test_siparissiz_us_icin_uyari_yok(self):
        self.kur()
        self.irsaliye()
        self.emir.kaynak_siparis = None
        self.assertEqual(uretim.bagli_taslak_irsaliyeler([self.emir]), {})

    def test_detay_ve_liste_tarih_seciciye_veri_ozniteligi_koyar(self):
        self.kur()
        d = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertNotContains(d, "data-irs-tarih")                                              # irsaliye yok
        self.irsaliye()
        d = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertContains(d, 'data-irs-tarih="2026-06-20"')
        self.assertContains(d, "Bağlı irsaliye SSI-TEST-1 20.06.2026 tarihli")
        self.assertContains(d, "return ieGonder(this);")
        self.assertContains(d, f'value="{d.context["bugun"]}"')                                  # varsayılan tarih bugün (değişmedi)
        liste = self.client.get(reverse("core:istasyon_emirleri"))
        self.assertContains(liste, 'data-irs-tarih="2026-06-20"')
        self.assertContains(liste, "return ieGonder(this);")

    def test_uret_gorunumleri_uyari_verir_ama_uretimi_engellemez(self):
        self.kur()
        self.irsaliye()
        mesajlar = lambda r: [str(m) for m in r.context["messages"]]
        r = self.client.post(reverse("core:istasyon_emri_uret", args=[self.ie.pk]), {"miktar": "", "tarih": "2026-06-29"}, follow=True)
        m = mesajlar(r)
        self.assertTrue(any("üretildi" in x for x in m), m)                                      # üretim yapıldı
        self.assertTrue(any("Bağlı irsaliye SSI-TEST-1 20.06.2026 tarihli; üretim tarihi sonrası seçildi (29.06.2026)" in x for x in m), m)
        self.ie.refresh_from_db()
        self.assertEqual(self.ie.durum, "BITTI")

    def test_toplu_uret_gorunumu_uyari_verir(self):
        self.kur()
        self.irsaliye()
        r = self.client.post(reverse("core:uretim_emri_toplu_uret", args=[self.emir.pk]), {"tarih": "2026-06-29"}, follow=True)
        m = [str(x) for x in r.context["messages"]]
        self.assertTrue(any("istasyon emri üretildi" in x for x in m), m)
        self.assertTrue(any("Bağlı irsaliye SSI-TEST-1" in x and "(29.06.2026)" in x for x in m), m)

    def test_uyari_yoksa_mesaj_yok(self):
        self.kur()                                                                               # taslak irsaliye yok
        r = self.client.post(reverse("core:istasyon_emri_uret", args=[self.ie.pk]), {"miktar": "", "tarih": "2026-06-29"}, follow=True)
        self.assertFalse(any("Bağlı irsaliye" in str(x) for x in r.context["messages"]))
