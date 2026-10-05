"""Aylık bordro tahakkuku: tek fiş (gider BORÇ / 335 ALACAK net / 360.20 / 361), kontrol (brüt − kesintiler = net), yeni personel carisi, düzenle,
sil (kalıcı; iptal yok), PDF eki, ekranlar ve fiş kilidi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.models import (Cari, CariKategori, EkranYetki, Kur, PersonelBordro, PersonelBordroSatir, SilmeKaydi, YevmiyeFisi)
from core.services import banka_hareket as bh
from core.services import bordro as bd
from core.services import cari as cari_servis
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal
TOPLAM = dict(brut=Dc("111947.79"), sgk_isci_iss=Dc("16792.17"), gv=Dc("2979.54"), dv=Dc("89.23"), net=Dc("92086.85"),
              sgk_isveren=Dc("10367.52"), iss_isveren=Dc("2238.96"))


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class BordroBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 9, 29), D(2026, 9, 30)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        Kur.objects.create(tarih=D(2026, 10, 5), usd_alis=Dc("41"))
        _hesap("102", "BANKALAR")
        _hesap("102.01.0001", "TL BANKA")
        _hesap("335", "PERSONELE BORÇLAR", kalem="KVYK")
        _hesap("360", "ÖDENECEK VERGİ VE FONLAR", kalem="KVYK")
        _hesap("360.20", "ÖDENECEK GELİR VERGİSİ STOPAJI", kalem="KVYK")
        _hesap("361", "ÖDENECEK SOSYAL GÜVENLİK KESİNTİLERİ", kalem="KVYK")
        _hesap("730", "GENEL ÜRETİM GİDERLERİ", kalem="C", grup="MALIYET")
        _hesap("730.06", "PERSONEL MAAŞ GİDERİ", kalem="C", grup="MALIYET")
        _hesap("770", "GENEL YÖNETİM GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.02", "ALINAN HİZMET GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("120", "ALICILAR")
        ust = CariKategori.objects.create(kod="335", ad="PERSONELLER")
        cls.kat = CariKategori.objects.create(kod="10", ad="PERSONELE BORÇLAR", ust=ust)
        cls.su = User.objects.create_superuser("bsu", password="x")
        cls.sade = User.objects.create_user("bsd", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="bordro")
        cls.p = [cari_servis.cari_olustur(unvan=ad, kategori_id=cls.kat.pk, para_birimi="TRY", kullanici=cls.su)
                 for ad in ("ALİ VELİ", "AYŞE FATMA", "MEHMET CAN", "ZEYNEP KAYA", "HASAN DEMİR")]
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")

    def satir(self, cari, brut, sgk, iss, gv, dv, sgkisv, issisv, gider="730.06", net=None):
        brut, sgk, iss, gv, dv = (Dc(str(x)) for x in (brut, sgk, iss, gv, dv))
        return {"cari": cari, "yeni_ad": "", "gider_hesap_kodu": gider, "brut": brut, "sgk_isci": sgk, "issizlik_isci": iss, "gelir_vergisi": gv,
                "damga_vergisi": dv, "net": brut - sgk - iss - gv - dv if net is None else Dc(str(net)),
                "sgk_isveren": Dc(str(sgkisv)), "issizlik_isveren": Dc(str(issisv))}

    def ornek_satirlar(self):
        """5 kişi; toplamlar kullanıcı örneğiyle birebir (son satır kalan)."""
        ilk = [self.satir(self.p[i], b, s, n, g, d, sv, iv) for i, (b, s, n, g, d, sv, iv) in enumerate([
            ("22000.00", "3080.00", "220.00", "600.00", "17.00", "2000.00", "440.00"),
            ("21000.00", "2940.00", "210.00", "550.00", "16.00", "1900.00", "420.00"),
            ("23000.00", "3220.00", "230.00", "650.00", "18.00", "2100.00", "460.00"),
            ("24000.00", "3360.00", "240.00", "700.00", "19.00", "2200.00", "480.00")])]
        T = lambda a: sum((x[a] for x in ilk), Dc("0"))
        sgk_iss_toplam = TOPLAM["sgk_isci_iss"]
        kalan_sgk_iss = sgk_iss_toplam - T("sgk_isci") - T("issizlik_isci")
        iss_son = Dc("1119.48") - T("issizlik_isci")
        son = self.satir(self.p[4], TOPLAM["brut"] - T("brut"), kalan_sgk_iss - iss_son, iss_son, TOPLAM["gv"] - T("gelir_vergisi"),
                         TOPLAM["dv"] - T("damga_vergisi"), TOPLAM["sgk_isveren"] - T("sgk_isveren"), TOPLAM["iss_isveren"] - T("issizlik_isveren"))
        return ilk + [son]

    def olustur(self, satirlar=None, **kw):
        kw.setdefault("yil", 2026), kw.setdefault("ay", 9), kw.setdefault("tahakkuk_tarihi", D(2026, 9, 30))
        return bd.bordro_olustur(satirlar=satirlar if satirlar is not None else self.ornek_satirlar(), kullanici=self.su, **kw)

    def fis_satirlari(self, fis):
        return [(s.hesap_id, "B" if s.borc else "A", s.borc or s.alacak) for s in fis.satirlar.filter(silindi=False).order_by("id")]


class BordroServisTest(BordroBase):
    def test_ornek_toplamlar_ve_fis_yapisi(self):
        b = self.olustur()
        fis = bd.bordro_fisi(b)
        self.assertEqual((fis.kaynak, fis.personel_bordro_id, fis.tarih), ("BORDRO", b.pk, D(2026, 9, 30)))
        self.assertEqual(fis.aciklama, "EYLÜL 2026 PERSONEL BORDRO TAHAKKUKU")
        satirlar = self.fis_satirlari(fis)
        gider = [t for h, tr, t in satirlar if h == "730.06"]
        self.assertEqual(len(gider), 5)
        self.assertEqual(sum(gider), Dc("124554.27"))                                    # brüt + SGK işveren net + işsizlik işveren
        self.assertEqual([t for h, tr, t in satirlar if h == "360.20"], [Dc("3068.77")])  # GV + DV
        self.assertEqual([t for h, tr, t in satirlar if h == "361"], [Dc("29398.65")])    # SGK + işsizlik (işçi + işveren)
        net = [t for h, tr, t in satirlar if h.startswith("335.")]
        self.assertEqual((len(net), sum(net)), (5, Dc("92086.85")))
        self.assertTrue(all(tr == "A" for h, tr, t in satirlar if h != "730.06"))
        self.assertEqual(sum(x.borc for x in fis.satirlar.all()), sum(x.alacak for x in fis.satirlar.all()))
        t = bd.ozet(b)
        self.assertEqual((t["kisi"], t["brut"], t["net"], t["gv_dv"], t["sgk_toplam"], t["maliyet"]),
                         (5, Dc("111947.79"), Dc("92086.85"), Dc("3068.77"), Dc("29398.65"), Dc("124554.27")))

    def test_personel_ekstresinde_tahakkuk_ve_odeme_kapatir(self):
        b = self.olustur()
        s = bd.satirlar(b)[0]
        e = raporlar.ekstre_devirli(s.cari.muhasebe_kodu, D(2026, 1, 1), D(2026, 12, 31))
        self.assertTrue(any(x.fis_pk == bd.bordro_fisi(b).pk and x.alacak == s.net for x in e.satirlar))
        self.assertEqual(_bak(s.cari.muhasebe_kodu), -s.net)                              # personele borcumuz
        bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=s.cari, tutar=str(s.net), tarih=D(2026, 10, 5), kullanici=self.su)
        self.assertEqual(_bak(s.cari.muhasebe_kodu), Dc("0"))

    def test_kontrol_tutmazsa_kaydedilmez(self):
        sat = self.ornek_satirlar()
        sat[2]["net"] += Dc("0.01")
        n_cari = Cari.objects.count()
        with self.assertRaises(bd.BordroHatasi) as cm:
            self.olustur(sat)
        self.assertIn("tutmuyor", str(cm.exception))
        self.assertEqual((PersonelBordro.objects.count(), YevmiyeFisi.objects.count(), Cari.objects.count()), (0, 0, n_cari))

    def test_yeni_personel_carisi_acilir_335_10(self):
        sat = self.ornek_satirlar()
        sat[4] = {**sat[4], "cari": None, "yeni_ad": "veli ışık"}
        b = self.olustur(sat)
        yeni = Cari.objects.get(unvan="VELİ IŞIK")
        self.assertEqual((yeni.kod, yeni.muhasebe_kodu, yeni.kategori_id), ("335-10-0006", "335.10.0006", self.kat.pk))
        self.assertEqual(bd.satirlar(b)[4].cari_id, yeni.pk)
        self.assertEqual(_bak("335.10.0006"), -sat[4]["net"])

    def test_yeni_personel_hatalari_hicbir_sey_acmaz(self):
        n = Cari.objects.count()
        for yeni, kwargs in (("TEK", {}), ("ALİ VELİ", {})):                              # tek sözcük / zaten var
            sat = self.ornek_satirlar()
            sat[4] = {**sat[4], "cari": None, "yeni_ad": yeni}
            with self.assertRaises(bd.BordroHatasi):
                self.olustur(sat)
        sat = self.ornek_satirlar()                                                       # hem cari hem yeni ad
        sat[4] = {**sat[4], "yeni_ad": "BAŞKA KİŞİ"}
        with self.assertRaises(bd.BordroHatasi):
            self.olustur(sat)
        sat = self.ornek_satirlar()                                                       # geçerli yeni + sonraki satır hatalı → cari AÇILMAZ
        sat[3] = {**sat[3], "cari": None, "yeni_ad": "YENİ KİŞİ"}
        sat[4]["net"] += 1
        with self.assertRaises(bd.BordroHatasi):
            self.olustur(sat)
        self.assertEqual(Cari.objects.count(), n)

    def test_gider_hesabi_satirda_degisir_ve_dogrulanir(self):
        sat = self.ornek_satirlar()
        sat[0]["gider_hesap_kodu"] = "770.02"
        b = self.olustur(sat)
        self.assertEqual(bd.satirlar(b)[0].gider_hesap_id, "770.02")
        kodlar = [h for h, tr, t in self.fis_satirlari(bd.bordro_fisi(b))]
        self.assertEqual((kodlar.count("770.02"), kodlar.count("730.06")), (1, 4))
        for kod in ("120", "999.99", "730"):                                              # gider değil / yok / yaprak değil
            s2 = self.ornek_satirlar()
            s2[0]["gider_hesap_kodu"] = kod
            with self.assertRaises(bd.BordroHatasi):
                self.olustur(s2)

    def test_gecersiz_satirlar(self):
        for degis in ({"brut": Dc("0"), "net": Dc("0")}, {"sgk_isci": Dc("-1")}):
            sat = self.ornek_satirlar()
            sat[0].update(degis)
            with self.assertRaises(bd.BordroHatasi):
                self.olustur(sat)
        sat = self.ornek_satirlar()
        sat[1]["cari"] = sat[0]["cari"]                                                   # aynı personel iki kez
        with self.assertRaises(bd.BordroHatasi):
            self.olustur(sat)
        musteri = Cari.objects.create(kod="120-01", unvan="MÜŞTERİ", muhasebe_kodu="120", para_birimi="TRY")
        sat = self.ornek_satirlar()
        sat[0]["cari"] = musteri                                                          # 335 değil
        with self.assertRaises(bd.BordroHatasi):
            self.olustur(sat)
        with self.assertRaises(bd.BordroHatasi):
            self.olustur([])
        with self.assertRaises(bd.BordroHatasi):
            self.olustur(ay=13)

    def test_sifir_vergili_satir_360_20_ve_361_yazilmaz(self):
        b = self.olustur([self.satir(self.p[0], "1000", "0", "0", "0", "0", "0", "0")])
        kodlar = [h for h, tr, t in self.fis_satirlari(bd.bordro_fisi(b))]
        self.assertEqual(sorted(kodlar), sorted(["730.06", "335.10.0001"]))

    def test_pdf_eki(self):
        pdf = SimpleUploadedFile("bordro eylul.pdf", b"%PDF-1.4\n...", content_type="application/pdf")
        b = self.olustur(dosya=pdf)
        self.assertTrue(b.dosya.name.startswith("personel_bordro/") and b.dosya.name.endswith(".pdf"))
        self.assertEqual(b.orijinal_ad, "bordro eylul.pdf")
        for ad, icerik in (("x.exe", b"%PDF-1"), ("x.pdf", b"MZ....")):
            with self.assertRaises(bd.BordroHatasi):
                self.olustur(dosya=SimpleUploadedFile(ad, icerik))


class BordroDuzenleSilTest(BordroBase):
    def test_duzenle_fis_yeniden_yazilir(self):
        b = self.olustur()
        fis = bd.bordro_fisi(b)
        no, n_fis = (fis.yil, fis.fis_no), YevmiyeFisi.objects.filter(silindi=False).count()
        sat = self.ornek_satirlar()
        sat[0] = self.satir(self.p[0], "30000", "4200", "300", "900", "25", "2500", "600")
        sat = sat[:1] + [s for s in sat[1:3]]                                              # 3 kişiye indir
        bd.bordro_guncelle(b, yil=2026, ay=9, tahakkuk_tarihi=D(2026, 9, 29), satirlar=sat, aciklama="düzeltme", kullanici=self.su)
        fis.refresh_from_db()
        self.assertEqual(((fis.yil, fis.fis_no), fis.tarih, fis.silindi), (no, D(2026, 9, 29), False))
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), n_fis)
        self.assertEqual(len(bd.satirlar(b)), 3)
        self.assertEqual(PersonelBordroSatir.objects.filter(bordro=b).count(), 8)         # eski 5 pasif geçmiş + 3 yeni
        self.assertIn("DÜZELTME", fis.aciklama)
        t = bd.ozet(b)
        credit = {h: sum(s for hh, tr, s in self.fis_satirlari(fis) if hh == h) for h in ("360.20", "361")}
        self.assertEqual(credit["360.20"], t["gv_dv"])
        self.assertEqual(credit["361"], t["sgk_toplam"])
        self.assertEqual(sum(x.borc for x in fis.satirlar.filter(silindi=False)), sum(x.alacak for x in fis.satirlar.filter(silindi=False)))

    def test_duzenle_hata_hicbir_seyi_degistirmez(self):
        b = self.olustur()
        onceki = self.fis_satirlari(bd.bordro_fisi(b))
        sat = self.ornek_satirlar()
        sat[0]["net"] += 1
        with self.assertRaises(bd.BordroHatasi):
            bd.bordro_guncelle(b, yil=2026, ay=9, tahakkuk_tarihi=D(2026, 9, 30), satirlar=sat, kullanici=self.su)
        with self.assertRaises(bd.BordroHatasi):                                           # fiş yılı değişemez
            bd.bordro_guncelle(b, yil=2026, ay=9, tahakkuk_tarihi=D(2027, 1, 5), satirlar=self.ornek_satirlar(), kullanici=self.su)
        self.assertEqual(self.fis_satirlari(bd.bordro_fisi(b)), onceki)
        self.assertEqual(len(bd.satirlar(b)), 5)

    def test_sil_yalniz_super_kullanici_fis_kalici_silinir(self):
        b = self.olustur()
        fis = bd.bordro_fisi(b)
        with self.assertRaises(bd.BordroHatasi):
            bd.bordro_sil(b, kullanici=self.sade)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
        bd.bordro_sil(b, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
        b.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertEqual(bd.satirlar(b), [])
        k = SilmeKaydi.objects.get(kaynak="BORDRO")
        self.assertEqual((k.veri["bordro"]["pk"], len(k.veri["bordro"]["satirlar"])), (b.pk, 5))
        for p in self.p:                                                                    # personel bakiyeleri sıfırlandı
            self.assertEqual(_bak(p.muhasebe_kodu), Dc("0"))
        with self.assertRaises(bd.BordroHatasi):
            bd.bordro_sil(b, kullanici=self.su)

    def test_iptal_durumu_yok(self):
        self.assertFalse(any("iptal" in f.name for f in PersonelBordro._meta.get_fields()))


class BordroEkranTest(BordroBase):
    def post_verisi(self, satirlar, **baslik):
        v = {"yil": "2026", "ay": "9", "tahakkuk_tarihi": "2026-09-30", "aciklama": "", **baslik,
             "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            v[f"form-{i}-cari"] = s["cari"].pk if s.get("cari") else ""
            v[f"form-{i}-yeni_ad"] = s.get("yeni_ad", "")
            v[f"form-{i}-gider_hesap"] = s.get("gider_hesap_kodu", "730.06")
            for a in bd.TUTAR_ALANLARI:
                v[f"form-{i}-{a}"] = f"{s[a]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return v

    def test_ekle_listele_detay_duzenle_sil(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:bordro_ekle"))
        self.assertContains(r, "Personel Satırları")
        self.assertContains(r, "730.06")                                                   # gider hesabı varsayılanı
        v = self.post_verisi(self.ornek_satirlar())
        v["dosya"] = SimpleUploadedFile("b.pdf", b"%PDF-1.7 x", content_type="application/pdf")
        r = self.client.post(reverse("core:bordro_ekle"), v)
        self.assertEqual(r.status_code, 302)
        b = PersonelBordro.objects.get()
        self.assertEqual(r["Location"], reverse("core:bordro_detay", args=[b.pk]))
        r = self.client.get(reverse("core:bordro_listesi"))
        self.assertContains(r, "Eylül 2026")
        self.assertContains(r, "124.554,27")
        r = self.client.get(reverse("core:bordro_detay", args=[b.pk]))
        self.assertContains(r, "ALİ VELİ")
        self.assertContains(r, "92.086,85")
        r = self.client.get(reverse("core:bordro_dosya", args=[b.pk]))
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        # düzenle ekranı mevcut satırları doldurur
        r = self.client.get(reverse("core:bordro_duzenle", args=[b.pk]))
        self.assertContains(r, "22.000,00")
        sat = self.ornek_satirlar()[:2]
        r = self.client.post(reverse("core:bordro_duzenle", args=[b.pk]), self.post_verisi(sat, aciklama="ek"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(bd.satirlar(b)), 2)
        # sil: onay sayfası + POST
        sil_url = reverse("core:bordro_sil", args=[b.pk])
        self.assertContains(self.client.get(sil_url), "Bordro")
        r = self.client.post(sil_url)
        self.assertEqual(r.status_code, 302)
        b.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertEqual(self.client.get(reverse("core:bordro_detay", args=[b.pk])).status_code, 404)

    def test_kontrol_hatasi_formda_gosterilir(self):
        self.client.force_login(self.su)
        sat = self.ornek_satirlar()
        sat[0]["net"] += 5
        r = self.client.post(reverse("core:bordro_ekle"), self.post_verisi(sat))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "tutmuyor")
        self.assertEqual(PersonelBordro.objects.count(), 0)

    def test_bos_satir_yok_sayilir_ve_yeni_personel(self):
        self.client.force_login(self.su)
        sat = [self.satir(None, "1000", "140", "10", "0", "0", "100", "20")]
        sat[0]["yeni_ad"] = "yeni personel"
        sos = self.post_verisi(sat)
        sos.update({"form-TOTAL_FORMS": "2", "form-1-cari": "", "form-1-yeni_ad": "", "form-1-gider_hesap": "730.06"})
        r = self.client.post(reverse("core:bordro_ekle"), sos)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Cari.objects.filter(unvan="YENİ PERSONEL", muhasebe_kodu="335.10.0006").exists())

    def test_yetki_ve_ham_fis_kilidi(self):
        b = self.olustur()
        fis = bd.bordro_fisi(b)
        self.client.force_login(self.sade)
        self.assertEqual(self.client.get(reverse("core:bordro_listesi")).status_code, 200)
        self.client.logout()
        yetkisiz = User.objects.create_user("yok", password="x")
        self.client.force_login(yetkisiz)
        self.assertNotEqual(self.client.get(reverse("core:bordro_listesi")).status_code, 200)
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fis_duzenle", args=[fis.pk]))
        self.assertEqual(r["Location"], reverse("core:bordro_detay", args=[b.pk]))
        r = self.client.get(reverse("core:fis_sil", args=[fis.pk]))
        self.assertEqual(r["Location"], reverse("core:bordro_detay", args=[b.pk]))
        self.assertContains(self.client.get(reverse("core:fis_detay", args=[fis.pk])), "Bordroya git")
