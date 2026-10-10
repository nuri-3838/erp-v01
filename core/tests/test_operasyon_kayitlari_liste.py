"""Operasyon Kayıtları listesi (sade tasarım): Taslak/Onaylı/Tümü sekmeleri, özet çipleri, tek "Kaynak" sütunu (ÜS / IE, Fason FD, bağımsız boş), tek satır süzgeç ve
50/sayfa sayfalama (tüm kayıtlar sayfalarda görünür)."""
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.models import Cari, Depo, OperasyonKaydi
from core.services import fason_donus as fd
from core.services.hareket import hareket_ekle
from core.services.uretim import istasyon_emri_kayit_ac, operasyon_kaydi_olustur, operasyon_kaydi_onayla
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class KayitListeTest(kt.KapanisTaban):
    def bagimsiz(self, n=1, tarih=None):
        return [operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=tarih or kt.GUN, hedef_cikti_miktari=D("1")) for _ in range(n)]

    def liste(self, **q):
        return self.client.get(reverse("core:operasyon_kayitlari"), q)

    def test_sekmeler_ve_cipler(self):
        taslak = self.bagimsiz()[0]
        onayli = self.bagimsiz()[0]
        operasyon_kaydi_onayla(onayli)
        r = self.liste()                                                                           # varsayılan: Taslak
        self.assertContains(r, taslak.no)
        self.assertNotContains(r, onayli.no)
        self.assertEqual(r.context["cipler"], {"taslak": 1, "bugun": 1, "hafta": 1})
        self.assertContains(r, "bekleyen taslak")
        self.assertContains(r, "bugün onaylanan")
        self.assertContains(r, "bu hafta onaylanan")
        o = self.liste(sekme="onayli")
        self.assertContains(o, onayli.no)
        self.assertNotContains(o, taslak.no)
        t = self.liste(sekme="tumu")
        self.assertContains(t, taslak.no)
        self.assertContains(t, onayli.no)
        OperasyonKaydi.objects.filter(pk=onayli.pk).update(updated_at=timezone.now() - timedelta(days=30))
        self.assertEqual(self.liste().context["cipler"], {"taslak": 1, "bugun": 0, "hafta": 0})

    def test_kaynak_sutunu_us_ie_fason_ve_bagimsiz(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        ie = emir.istasyon_emirleri.get()
        kayit_ie = istasyon_emri_kayit_ac(ie)
        bag = self.bagimsiz()[0]
        fasoncu = Cari.objects.create(kod="FSNC", unvan="FASONCU LISTE")
        fdepo = Depo.objects.create(kod="FD1", ad="FASON LISTE", fason_cari=fasoncu)

        hareket_ekle(stok_id=self.ham.pk, depo_id=fdepo.pk, tarih=date(2026, 1, 5), tur="GIRIS", miktar=D("50"), giris_tutar_try=D("50"))
        donus = fd.donus_olustur(cari_id=fasoncu.pk, depo_id=self.depo.pk, tarih=kt.GUN, satirlar=[(self.op.pk, D("2"))])
        fkayit = OperasyonKaydi.objects.get(fason_donus=donus)
        r = self.liste(sekme="tumu")
        html = r.content.decode()
        self.assertContains(r, f"{emir.no}</a> /")
        self.assertContains(r, reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, f">{ie.no}</a>")
        self.assertContains(r, f"Fason <a class=\"mono\" href=\"{reverse('core:fason_donus_detay', args=[donus.pk])}\">{donus.no}</a>")
        satir = lambda no: html.split(no)[1].split("</tr>")[0]                                      # kaydın satırı
        self.assertNotIn("<a class=\"mono\" href=\"/uretim/emirler/", satir(bag.no))                 # bağımsızda kaynak boş
        self.assertNotIn("Fason", satir(bag.no))
        self.assertIn("Fason", satir(fkayit.no))
        self.assertIn(">KPN<", satir(bag.no))                                                      # istasyon kısa rozet
        self.assertContains(self.liste(ara=donus.no, sekme="tumu"), fkayit.no)
        self.assertNotContains(self.liste(ara=donus.no, sekme="tumu"), bag.no)
        self.assertContains(self.liste(ara=ie.no, sekme="tumu"), kayit_ie.no)

    def test_suzgecler(self):
        yeni = self.bagimsiz(tarih=date(2026, 7, 10))[0]
        eski = self.bagimsiz(tarih=date(2026, 5, 10))[0]
        r = self.liste(bas="2026-07-01", bit="2026-07-31")
        self.assertContains(r, yeni.no)
        self.assertNotContains(r, eski.no)
        self.assertNotContains(self.liste(istasyon=self.op.istasyon_id + 999), yeni.no)
        self.assertContains(self.liste(istasyon=self.op.istasyon_id), yeni.no)
        emir = self.emir_ac(self.siparis("3"))
        k = istasyon_emri_kayit_ac(emir.istasyon_emirleri.get())
        self.assertContains(self.liste(us=emir.no), k.no)
        self.assertNotContains(self.liste(us=emir.no), yeni.no)
        self.assertEqual(self.liste(bas="gecersiz").status_code, 200)                              # bozuk tarih sessizce yok sayılır

    def test_emirden_gelen_baglanti_tumunu_gosterir(self):
        emir = self.emir_ac(self.siparis("3"))
        ie = emir.istasyon_emirleri.get()
        k = istasyon_emri_kayit_ac(ie)
        operasyon_kaydi_onayla(k)
        self.assertContains(self.liste(istasyon_emri=ie.pk), k.no)                                 # onaylı da görünür (sekme verilmedi)
        self.assertNotContains(self.liste(), k.no)                                                 # düz liste: taslak sekmesi

    def test_sayfalama_tum_kayitlar_gorunur(self):
        kayitlar = self.bagimsiz(61)
        s1 = self.liste(sekme="tumu")
        self.assertContains(s1, "Sayfa 1 / 2")
        self.assertContains(s1, "61 kayıt")
        s2 = self.liste(sekme="tumu", sayfa=2)
        gorunen = {k.no for k in s1.context["kayitlar"]} | {k.no for k in s2.context["kayitlar"]}
        self.assertEqual(gorunen, {k.no for k in kayitlar})                                        # 50 + 11 = tümü
        self.assertEqual((len(s1.context["kayitlar"]), len(s2.context["kayitlar"])), (50, 11))
        self.assertContains(s1, "sekme=tumu&amp;sayfa=2")                                         # süzgeç sayfa bağlantısında korunur
