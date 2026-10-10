"""Üretim Siparişleri listesi (sade tasarım): ÜS no / müşteri / "Sip. … · N ürün · M adet", depo sütunu yok, Termin, durum rozeti, ilerleme çubuğu + "x/y istasyon",
kaynak siparişsiz "Stok için üretim", arama (müşteri dahil), sabit sorgu sayısı."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import TeklifSiparis
from core.services import uretim
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class ListeTest(kt.KapanisTaban):
    def liste(self, **q):
        return self.client.get(reverse("core:uretim_emirleri"), q)

    def gorunen_satir(self, yanit, emir):
        """Emrin satırının GÖRÜNEN içeriği (data-arama özniteliği hariç)."""
        satir = next(x for x in yanit.content.decode().split('<tr class="ue-satir"')[1:] if emir.no in x)
        return satir.split('onclick="window.location', 1)[1].split("</tr>")[0]

    def test_satir_duzeni_musteri_siparis_ozeti_ve_depo_sutunu_yok(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        r = self.liste()
        html = r.content.decode()
        self.assertContains(r, emir.no)
        self.assertContains(r, self.musteri.unvan)
        self.assertContains(r, f"Sip. {sip.belge_no} · 1 ürün · 3 adet")
        self.assertNotIn("<th>Depo</th>", html)
        self.assertEqual([t for t in ("Üretim Siparişi", "Tarih", "Termin", "Durum", "İlerleme") if f"<th>{t}</th>" in html],
                         ["Üretim Siparişi", "Tarih", "Termin", "Durum", "İlerleme"])
        self.assertNotIn(f"{self.stok.kod} {self.stok.ad}", self.gorunen_satir(r, emir))               # ürün adı satırda görünmez (detayda var)
        self.assertNotIn("kalem daha", html)
        self.assertContains(r, "Satış siparişinden açılan üretimler. Satıra tıkla, istasyon emirlerine git.")
        self.assertContains(r, "müşteri")                                                              # arama placeholder'ı

    def test_kaynak_siparissiz_stok_icin_uretim(self):
        emir = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=kt.GUN)
        r = self.liste()
        self.assertContains(r, "Stok için üretim")
        self.assertContains(r, f"{emir.no}")
        satir = self.gorunen_satir(r, emir)
        self.assertNotIn("Sip. ", satir)                                                               # sipariş yoksa "Sip. …" yok
        self.assertIn("1 ürün · 2 adet", satir)

    def test_arama_musteri_siparis_no_urun_ve_depo(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.assertContains(self.liste(ara=self.musteri.unvan[:5]), emir.no)                           # müşteri adıyla bulur
        self.assertContains(self.liste(ara=sip.belge_no), emir.no)
        self.assertContains(self.liste(ara=self.stok.kod), emir.no)
        self.assertContains(self.liste(ara=self.depo.kod), emir.no)                                    # depo yine aranabilir
        self.assertNotContains(self.liste(ara="YOKBOYLEBIR"), emir.no)

    def test_termin_ilerleme_durum_rozetleri(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        r = self.liste()
        self.assertContains(r, "%0")
        self.assertContains(r, "0/1 istasyon")
        self.assertContains(r, 'class="ue-durum acik">Açık')
        self.assertContains(r, '<span class="pasif">—</span>')                                         # termin yok
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=timezone.localdate() - timedelta(days=3))
        r = self.liste()
        self.assertContains(r, "ue-termin gecmis")                                                     # geçmiş + açık → kırmızı
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=timezone.localdate() + timedelta(days=3))
        self.assertNotContains(self.liste(), "ue-termin gecmis")
        self.assertContains(self.liste(), (timezone.localdate() + timedelta(days=3)).strftime("%d.%m.%Y"))
        uretim.operasyon_kaydi_onayla(uretim.istasyon_emri_kayit_ac(emir.istasyon_emirleri.get()))
        r = self.liste()
        self.assertContains(r, "%100")
        self.assertContains(r, "1/1 istasyon")
        self.assertContains(r, "width:100%")
        uretim.uretim_emri_iptal(emir)
        r = self.liste()
        self.assertContains(r, 'class="ue-durum iptal">İptal')
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=timezone.localdate() - timedelta(days=3))
        self.assertNotContains(self.liste(), "ue-termin gecmis")                                       # açık değilse kırmızı değil

    def test_sabit_sorgu_sayisi_emir_basina_sorgu_yok(self):
        sip = self.siparis("3")
        self.emir_ac(sip)
        uretim.uretim_emri_iptal(sip.uretim_emirleri.get())                                            # iptal ÜS'ler: eksik hesabı yok, yalnız liste sorguları
        def say():
            with CaptureQueriesContext(connection) as c:
                self.assertEqual(self.liste().status_code, 200)
            return len(c)
        bir = say()
        for _ in range(4):
            e = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=kt.GUN)
            uretim.uretim_emri_iptal(e)
        self.assertEqual(say(), bir)                                                                   # 1 → 5 satır: sorgu sayısı aynı
