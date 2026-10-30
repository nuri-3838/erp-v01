"""İşe giriş Dilim 1: personel işe giriş alanları, firma işveren alanları, düzenlenebilir belge şablonları (Ayarlar), özlük kontrol listesi,
işe giriş kaydı (sözleşme parametreleri) ve personel kartı "İşe Giriş" sekmesi."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import BelgeSablonu, EkranYetki, IseGirisKaydi, Personel, PersonelBelge
from core.services import belge_sablon as bs
from core.services import ise_giris as ig
from core.services.firma import firma_bilgisi_getir, firma_bilgisi_kaydet
from core.services.personel import PersonelHatasi, personel_guncelle, personel_olustur
from core.services.personel_belge import belge_ekle
from core.tarih import tr_bugun
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, personel_kur, yuklenen

T = PersonelBelge.Tur


def kullanici(ad, *kodlar):
    u = User.objects.create_user(ad, password="x")
    for k in kodlar:
        EkranYetki.objects.create(kullanici=u, ekran_kod=k)
    return u


class PersonelIseGirisAlanlariTest(TestCase):
    def kur(self, **kw):
        veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2026, 11, 2)}
        veri.update(kw)
        return personel_olustur(**veri)

    def test_alanlar_buyuk_harf_ve_kaydedilir(self):
        p = self.kur(dogum_yeri="  şişli ", baba_adi="mehmet", ana_adi="ayşe", cinsiyet="ERKEK", medeni_durum="EVLI",
                     cocuk_sayisi="2", ogrenim_durumu="LISE")
        p.refresh_from_db()
        self.assertEqual((p.dogum_yeri, p.baba_adi, p.ana_adi), ("ŞİŞLİ", "MEHMET", "AYŞE"))
        self.assertEqual((p.cinsiyet, p.medeni_durum, p.cocuk_sayisi, p.ogrenim_durumu), ("ERKEK", "EVLI", 2, "LISE"))

    def test_bos_ve_gecersiz_degerler(self):
        p = self.kur()
        self.assertEqual((p.dogum_yeri, p.cinsiyet, p.medeni_durum, p.cocuk_sayisi, p.ogrenim_durumu), ("", "", "", None, ""))
        for ek in ({"cinsiyet": "X"}, {"medeni_durum": "?"}, {"ogrenim_durumu": "ÜNİVERSİTE"}, {"cocuk_sayisi": "-1"},
                   {"cocuk_sayisi": "31"}, {"cocuk_sayisi": "abc"}):
            with self.assertRaises(PersonelHatasi, msg=ek):
                self.kur(**ek)

    def test_guncelle(self):
        p = self.kur(dogum_yeri="kayseri")
        personel_guncelle(p, ad="ali", soyad="veli", ise_giris_tarihi=date(2026, 11, 2), dogum_yeri="", baba_adi="osman", cocuk_sayisi=0)
        p.refresh_from_db()
        self.assertEqual((p.dogum_yeri, p.baba_adi, p.cocuk_sayisi), ("", "OSMAN", 0))

    def test_form_ve_kart(self):
        yetkili = kullanici("igpersonel", "personel")
        self.client.force_login(yetkili)
        r = self.client.get(reverse("core:personel_ekle"))
        for ad in ("dogum_yeri", "baba_adi", "ana_adi", "cinsiyet", "medeni_durum", "cocuk_sayisi", "ogrenim_durumu"):
            self.assertContains(r, f'name="{ad}"')
        r = self.client.post(reverse("core:personel_ekle"), {
            "ad": "ahmet", "soyad": "yılmaz", "ise_giris_tarihi": "2026-11-02", "dogum_yeri": "kayseri", "baba_adi": "hasan", "ana_adi": "fatma",
            "cinsiyet": "ERKEK", "medeni_durum": "BEKAR", "cocuk_sayisi": "0", "ogrenim_durumu": "LISANS"})
        p = Personel.objects.get(ad="AHMET")
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))                 # yalnız personel yetkisi: İşe Giriş sekmesi yok
        kart = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        for metin in ("Doğum Yeri", "KAYSERİ", "Baba Adı", "HASAN", "Ana Adı", "FATMA", "Erkek", "Bekâr", "Lisans"):
            self.assertContains(kart, metin)


class FirmaIsverenAlanlariTest(TestCase):
    def test_kaydet_ve_eski_cagri_degeri_korur(self):
        firma_bilgisi_kaydet(unvan="semta", isveren_vekili_adi="nuri özer", isveren_vekili_unvani="yönetim kurulu başkanı",
                             mersis_no="0123456789012345", sgk_isyeri_sicil_no="1.2345.67.89.0123456.034")
        f = firma_bilgisi_getir()
        self.assertEqual((f.isveren_vekili_adi, f.isveren_vekili_unvani), ("NURİ ÖZER", "YÖNETİM KURULU BAŞKANI"))
        self.assertEqual((f.mersis_no, f.sgk_isyeri_sicil_no), ("0123456789012345", "1.2345.67.89.0123456.034"))
        firma_bilgisi_kaydet(unvan="semta 2")                                                 # eski çağrı: yeni alanlar silinmez
        f.refresh_from_db()
        self.assertEqual((f.unvan, f.isveren_vekili_adi, f.mersis_no), ("SEMTA 2", "NURİ ÖZER", "0123456789012345"))
        firma_bilgisi_kaydet(unvan="semta 2", mersis_no="")                                   # bilinçli boşaltma çalışır
        f.refresh_from_db()
        self.assertEqual(f.mersis_no, "")

    def test_ekran_alanlari(self):
        yon = User.objects.create_superuser("figyon", password="x")
        self.client.force_login(yon)
        r = self.client.get(reverse("core:firma_bilgileri"))
        for ad in ("isveren_vekili_adi", "isveren_vekili_unvani", "mersis_no", "sgk_isyeri_sicil_no"):
            self.assertContains(r, f'name="{ad}"')
        self.assertContains(r, "İşe Giriş Evrakları İçin")


class BelgeSablonServisTest(TestCase):
    def test_hazirla_dort_sablon_olusturur_ve_tekrar_cogaltmaz(self):
        self.assertEqual(BelgeSablonu.objects.count(), 0)
        liste = bs.hazirla()
        self.assertEqual([s.kod for s in liste], ["SOZLESME", "KVKK_AYDINLATMA", "KVKK_ACIK_RIZA", "FAZLA_CALISMA"])
        bs.hazirla()
        self.assertEqual(BelgeSablonu.objects.filter(silindi=False).count(), 4)
        self.assertTrue(all(s.aktif and s.metin.strip() for s in liste))

    def test_varsayilan_metinlerde_tanimsiz_yer_tutucu_yok(self):
        for kod, v in bs.VARSAYILAN.items():
            self.assertEqual(bs.bilinmeyen_yer_tutucular(v["metin"]), [], kod)

    def test_doldur_bilinen_bos_ve_tanimsiz(self):
        sonuc = bs.doldur("Sayın {ad_soyad}, TC {tc}, görev {gorev}. {yok_boyle}", {"ad_soyad": "ALİ VELİ", "tc": "", "gorev": None})
        self.assertEqual(sonuc, f"Sayın ALİ VELİ, TC {bs.BOS}, görev {bs.BOS}. {{yok_boyle}}")

    def test_sablon_dili_calistirilmaz(self):
        girdi = "{{ 7*7 }} {% load core_extras %} {{ request }} {ad_soyad}"
        self.assertEqual(bs.doldur(girdi, {"ad_soyad": "A"}), "{{ 7*7 }} {% load core_extras %} {{ request }} A")
        self.assertEqual(bs.doldur("{ad_soyad}", {"ad_soyad": "{{ 7*7 }} {tc}"}), "{{ 7*7 }} {tc}")            # değer yeniden işlenmez

    def test_kaydet_dogrulama(self):
        s = bs.getir("SOZLESME")
        bs.kaydet(s, baslik="  yeni   başlık ", metin="Merhaba {ad_soyad}\r\nSatır", aktif=False)
        s.refresh_from_db()
        self.assertEqual((s.baslik, s.metin, s.aktif), ("yeni başlık", "Merhaba {ad_soyad}\nSatır", False))
        for ek in ({"baslik": ""}, {"metin": "  "}, {"metin": "Merhaba {adsoyad}"}):
            with self.assertRaises(bs.SablonHatasi, msg=ek):
                bs.kaydet(s, **{"baslik": "b", "metin": "m", **ek})
        with self.assertRaises(bs.SablonHatasi) as e:
            bs.kaydet(s, baslik="b", metin="{adsoyad} ve {tcno}")
        self.assertIn("{adsoyad}", str(e.exception))

    def test_varsayilana_don(self):
        s = bs.getir("KVKK_ACIK_RIZA")
        bs.kaydet(s, baslik="x", metin="y", aktif=False)
        bs.varsayilana_don(s)
        s.refresh_from_db()
        self.assertEqual((s.baslik, s.metin), (bs.VARSAYILAN["KVKK_ACIK_RIZA"]["baslik"], bs.VARSAYILAN["KVKK_ACIK_RIZA"]["metin"].strip()))
        self.assertFalse(s.aktif)                                                              # "pakete dahil" tercihi korunur, yalnız metin sıfırlanır


class BelgeSablonEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("bsyon", password="x")
        cls.sade = kullanici("bssade", "personel", "personel_belgeleri")

    def test_yalniz_yonetici(self):
        s = bs.hazirla()[0]
        urller = [reverse("core:belge_sablonlari"), reverse("core:belge_sablonu_duzenle", args=[s.pk])]
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 302)
        self.client.force_login(self.sade)
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 403)
        self.assertEqual(self.client.post(reverse("core:belge_sablonu_varsayilan", args=[s.pk])).status_code, 403)

    def test_liste_duzenle_varsayilan(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:belge_sablonlari"))
        self.assertContains(r, "taslak")
        self.assertContains(r, "Belirsiz Süreli İş Sözleşmesi")
        self.assertContains(r, "{ad_soyad}")
        s = BelgeSablonu.objects.get(kod="FAZLA_CALISMA")
        url = reverse("core:belge_sablonu_duzenle", args=[s.pk])
        self.assertContains(self.client.get(url), s.baslik)
        r = self.client.post(url, {"baslik": "FAZLA ÇALIŞMA ONAYI", "metin": "{ad_soyad} onaylar.", "aktif": "on"})
        self.assertRedirects(r, reverse("core:belge_sablonlari"))
        s.refresh_from_db()
        self.assertEqual((s.baslik, s.metin), ("FAZLA ÇALIŞMA ONAYI", "{ad_soyad} onaylar."))
        r = self.client.post(url, {"baslik": "x", "metin": "{yanlis_alan}", "aktif": "on"})
        self.assertContains(r, "Tanımsız yer tutucu")
        s.refresh_from_db()
        self.assertEqual(s.baslik, "FAZLA ÇALIŞMA ONAYI")
        self.client.get(reverse("core:belge_sablonu_varsayilan", args=[s.pk]))                  # GET sıfırlamaz
        s.refresh_from_db()
        self.assertEqual(s.baslik, "FAZLA ÇALIŞMA ONAYI")
        self.client.post(reverse("core:belge_sablonu_varsayilan", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.baslik, bs.VARSAYILAN["FAZLA_CALISMA"]["baslik"])

    def test_menude_ayarlar_altinda(self):
        from core.yetki import kullanici_menusu
        kodlar = [e.kod for m in kullanici_menusu(self.yon) if m.kod == "AYARLAR" for e in m.ekranlar]
        self.assertIn("belge_sablonlari", kodlar)


class KontrolListesiTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()

    def yukle(self, tur, bitis=None, ad="a.pdf"):
        return belge_ekle(self.p, tur=tur, dosya=yuklenen(ad, PDF_BAYT), bitis_tarihi=bitis)

    def test_tanim_tutarli(self):
        self.assertTrue(all(len(v) <= 20 for v in PersonelBelge.Tur.values))                  # tur kolonu max_length=20
        turler = [tur.value for _k, tur, _a, _z in ig.KONTROL_LISTESI]
        self.assertEqual(len(turler), len(set(turler)))
        self.assertTrue(all(t in PersonelBelge.Tur.values for t in turler))
        self.assertTrue(all(k in ig.KAYNAKLAR for k, _t, _a, _z in ig.KONTROL_LISTESI))

    def test_bos_personel_hepsi_eksik(self):
        kl = ig.kontrol_listesi(self.p)
        self.assertEqual(kl["toplam"], len(ig.KONTROL_LISTESI))
        self.assertEqual((kl["yuklu"], kl["eksik"]), (0, kl["toplam"]))
        self.assertEqual(kl["eksik_zorunlu"], sum(1 for _k, _t, _a, z in ig.KONTROL_LISTESI if z))
        self.assertTrue(all(s["durum"] == "EKSIK" and s["belge"] is None for s in kl["satirlar"]))

    def test_yuklenince_dolar_ve_eski_surum_susar(self):
        once = ig.kontrol_listesi(self.p)["eksik_zorunlu"]
        self.yukle(T.KIMLIK)
        kl = ig.kontrol_listesi(self.p)
        satir = next(s for s in kl["satirlar"] if s["tur"] == "KIMLIK")
        self.assertEqual((satir["durum"], kl["eksik_zorunlu"], kl["yuklu"]), ("YUKLU", once - 1, 1))
        self.assertIsNotNone(satir["belge"])

    def test_suresi_dolan_ve_yaklasan(self):
        bugun = tr_bugun()
        self.yukle(T.SAGLIK_RAPORU, bitis=bugun - timedelta(days=2))
        self.yukle(T.ADLI_SICIL, bitis=bugun + timedelta(days=10))
        kl = ig.kontrol_listesi(self.p)
        durum = {s["tur"]: s["durum"] for s in kl["satirlar"]}
        self.assertEqual((durum["SAGLIK_RAPORU"], durum["ADLI_SICIL"]), ("SURESI_DOLDU", "YAKLASIYOR"))
        self.assertTrue(kl["uyari"])
        self.yukle(T.SAGLIK_RAPORU, bitis=bugun + timedelta(days=300), ad="yeni.pdf")             # yenilenince eski susar
        self.assertEqual({s["tur"]: s["durum"] for s in ig.kontrol_listesi(self.p)["satirlar"]}["SAGLIK_RAPORU"], "YUKLU")

    def test_esdeger_turler(self):
        self.yukle(T.ISG_EGITIMI)                                                              # eski tür yeni "İSG tutanağı" satırını da karşılar
        self.yukle(T.OPERATOR_BELGESI)
        durum = {s["tur"]: s["durum"] for s in ig.kontrol_listesi(self.p)["satirlar"]}
        self.assertEqual((durum["ISG_TUTANAK"], durum["MYK_BELGESI"]), ("YUKLU", "YUKLU"))

    def test_belgeler_disaridan_verilirse_sorgu_yok(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from core.services.personel_belge import belge_durumlari
        self.yukle(T.KIMLIK)
        liste = belge_durumlari(self.p)
        with CaptureQueriesContext(connection) as c:
            ig.kontrol_listesi(self.p, belgeler=liste)
        self.assertEqual(len(c), 0)


class IseGirisKaydiServisTest(TestCase):
    def setUp(self):
        self.p = personel_kur()

    def test_oku_kaydetmez_kaydet_olusturur_ve_gunceller(self):
        k = ig.kayit_oku(self.p)
        self.assertIsNone(k.pk)
        self.assertEqual(IseGirisKaydi.objects.count(), 0)
        ig.kayit_kaydet(self.p, deneme_suresi_ay="2", haftalik_calisma_saati="45", calisma_gunleri="  Pazartesi –  Cuma ",
                        calisma_saatleri="08:00 – 17:30", ucret_odeme="Her ayın 5'i", notlar=" not ")
        k = IseGirisKaydi.objects.get(personel=self.p)
        self.assertEqual((k.deneme_suresi_ay, str(k.haftalik_calisma_saati), k.calisma_gunleri, k.notlar), (2, "45.0", "Pazartesi – Cuma", "not"))
        ig.kayit_kaydet(self.p, deneme_suresi_ay="", haftalik_calisma_saati="")
        self.assertEqual(IseGirisKaydi.objects.filter(personel=self.p).count(), 1)
        k.refresh_from_db()
        self.assertEqual((k.deneme_suresi_ay, k.haftalik_calisma_saati, k.calisma_gunleri), (None, None, ""))

    def test_dogrulama(self):
        for ek in ({"deneme_suresi_ay": "5"}, {"deneme_suresi_ay": "x"}, {"haftalik_calisma_saati": "46"}, {"haftalik_calisma_saati": "0"},
                   {"haftalik_calisma_saati": "abc"}):
            with self.assertRaises(ig.IseGirisHatasi, msg=ek):
                ig.kayit_kaydet(self.p, **ek)
        self.assertEqual(IseGirisKaydi.objects.count(), 0)

    def test_paket_ve_muhasebe_alanlarina_dokunmaz(self):
        ig.kayit_kaydet(self.p, deneme_suresi_ay="2")
        IseGirisKaydi.objects.filter(personel=self.p).update(muhasebe_gonderim_tarihi=date(2026, 11, 1))
        ig.kayit_kaydet(self.p, deneme_suresi_ay="1")
        self.assertEqual(IseGirisKaydi.objects.get(personel=self.p).muhasebe_gonderim_tarihi, date(2026, 11, 1))


class IseGirisSekmesiTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("isyon", password="x")
        self.belgeci = kullanici("isbelgeci", "personel", "personel_belgeleri")
        self.sade = kullanici("issade", "personel")
        self.p = personel_kur()
        self.url = reverse("core:personel_detay", args=[self.p.pk])

    def sekme(self, kisi=None):
        self.client.force_login(kisi or self.belgeci)
        return self.client.get(self.url, {"sekme": "ise_giris"})

    def test_sekme_yalniz_belge_yetkilisine(self):
        r = self.sekme()
        self.assertEqual(r.context["sekme"], "ise_giris")
        self.assertIn("İşe Giriş", [t["ad"] for t in r.context["sekmeler"]])
        r = self.sekme(self.sade)
        self.assertEqual(r.context["sekme"], "genel")
        self.assertNotIn("İşe Giriş", [t["ad"] for t in r.context["sekmeler"]])

    def test_icerik_kontrol_listesi_ve_parametre_formu(self):
        r = self.sekme()
        self.assertContains(r, "Özlük Kontrol Listesi")
        self.assertContains(r, "Belirsiz süreli iş sözleşmesi (imzalı)")
        self.assertContains(r, "İSG işe giriş eğitimi tutanağı")
        self.assertContains(r, "Sözleşme Bilgileri")
        self.assertContains(r, 'name="deneme_suresi_ay"')
        self.assertContains(r, reverse("core:ise_giris_kaydet", args=[self.p.pk]))
        self.assertContains(r, f"personel={self.p.pk}&amp;tur=KIMLIK&amp;sonraki=ise_giris")
        self.assertEqual(r.context["kontrol"]["toplam"], len(ig.KONTROL_LISTESI))
        self.assertEqual(IseGirisKaydi.objects.count(), 0)                                      # GET yazmaz

    def test_rozet_eksik_zorunlu_sayisi_ve_uyari(self):
        r = self.sekme()
        s = {t["ad"]: t for t in r.context["sekmeler"]}
        beklenen = sum(1 for _k, _t, _a, z in ig.KONTROL_LISTESI if z)
        self.assertEqual((s["İşe Giriş"]["sayi"], s["İşe Giriş"]["uyari"]), (beklenen, True))
        for _k, tur, _a, z in ig.KONTROL_LISTESI:
            if z:
                belge_ekle(self.p, tur=tur, dosya=yuklenen("a.pdf", PDF_BAYT))
        s = {t["ad"]: t for t in self.sekme().context["sekmeler"]}
        self.assertEqual((s["İşe Giriş"]["sayi"], s["İşe Giriş"]["uyari"]), (0, False))

    def test_yukle_baglantisi_turu_hazir_getirir_ve_ise_girise_doner(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "ADLI_SICIL", "sonraki": "ise_giris"})
        self.assertRegex(r.content.decode(), r'<option value="ADLI_SICIL" selected')
        r = self.client.get(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "GECERSIZ"})
        self.assertNotRegex(r.content.decode(), r'<option value="GECERSIZ"')
        r = self.client.post(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "ADLI_SICIL", "sonraki": "ise_giris",
                                                          "dosya": yuklenen("sabika.pdf", PDF_BAYT)})
        self.assertRedirects(r, self.url + "?sekme=ise_giris")
        self.assertEqual({s["tur"]: s["durum"] for s in self.sekme().context["kontrol"]["satirlar"]}["ADLI_SICIL"], "YUKLU")
        r = self.client.post(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "KIMLIK", "sonraki": "personel",
                                                          "dosya": yuklenen("k.pdf", PDF_BAYT)})
        self.assertRedirects(r, self.url + "?sekme=belgeler")                                      # eski dönüş aynen

    def test_parametre_kaydet_ve_hata(self):
        self.client.force_login(self.belgeci)
        url = reverse("core:ise_giris_kaydet", args=[self.p.pk])
        r = self.client.post(url, {"deneme_suresi_ay": "2", "haftalik_calisma_saati": "45", "calisma_gunleri": "Pazartesi – Cuma"})
        self.assertRedirects(r, self.url + "?sekme=ise_giris")
        k = IseGirisKaydi.objects.get(personel=self.p)
        self.assertEqual((k.deneme_suresi_ay, k.calisma_gunleri, k.created_by, k.updated_by), (2, "Pazartesi – Cuma", self.belgeci, self.belgeci))
        r = self.client.post(url, {"deneme_suresi_ay": "2", "haftalik_calisma_saati": "50"}, follow=True)
        self.assertContains(r, "en çok 45")
        k.refresh_from_db()
        self.assertEqual(str(k.haftalik_calisma_saati), "45.0")                                    # hatalı kayıt değiştirmedi
        r = self.client.post(url, {"deneme_suresi_ay": "9"}, follow=True)
        self.assertContains(r, "Kayıt yapılamadı")
        k = self.client.get(self.url, {"sekme": "ise_giris"}).context["ise_giris_form"]
        self.assertEqual(k.initial["calisma_gunleri"], "Pazartesi – Cuma")

    def test_parametre_yetkisi(self):
        url = reverse("core:ise_giris_kaydet", args=[self.p.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        for kisi in (self.sade, kullanici("isyalnizbelge", "personel_belgeleri")):               # her iki yetki de gerekli
            self.client.force_login(kisi)
            self.assertEqual(self.client.post(url, {"deneme_suresi_ay": "2"}).status_code, 403)
        self.assertEqual(IseGirisKaydi.objects.count(), 0)

    def test_yeni_personel_ise_giris_sekmesine_yonlenir(self):
        veri = {"ad": "yeni", "soyad": "kişi", "ise_giris_tarihi": "2026-11-02"}
        self.client.force_login(self.belgeci)
        r = self.client.post(reverse("core:personel_ekle"), veri)
        p = Personel.objects.get(ad="YENİ")
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]) + "?sekme=ise_giris")
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:personel_ekle"), {**veri, "ad": "başka"})
        p2 = Personel.objects.get(ad="BAŞKA")
        self.assertRedirects(r, reverse("core:personel_detay", args=[p2.pk]))

    def test_personel_kartlari_aktif_ve_eski_belge_ekrani_calisir(self):
        r = self.sekme()
        self.assertEqual((r.context["aktif_modul_kod"], r.context["aktif_ekran"]), ("IK", "core:personeller"))
        self.client.force_login(self.belgeci)
        self.assertEqual(self.client.get(reverse("core:belgeler")).status_code, 200)
