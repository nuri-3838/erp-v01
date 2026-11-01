"""İşe giriş Dilim 3: muhasebe paketi — hazır mı kontrolü (eksik zorunlu alan + işe girişe kalan gün), SGK 4/A için SADE Excel (tek satır)/PDF bilgi
paketi (işveren/sigortalı/işe giriş/ücret; brüt yok), SGDP ve kısmi süreli dalları, ücret yetkisi, "muhasebeye gönderildi" işareti, GET yazmaz."""
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import IkAyari, IseGirisKaydi
from core.services import ise_giris as ig
from core.services import ise_giris_muhasebe as mu
from core.services.firma import firma_bilgisi_getir
from core.services.personel_ucret import ucret_ekle
from core.tarih import tr_bugun
from core.tests.ik_yardimci import OzelDizinTestTemel, personel_kur
from core.tests.test_ise_giris_paket import dolu_firma, dolu_personel, kullanici

BUGUN = date(2026, 10, 30)


def excel_hucreleri(veri: bytes) -> dict:
    """{alan başlığı: değer}: 5. satır başlıklar, 6. satır TEK veri satırı."""
    ws = load_workbook(BytesIO(veri)).active
    return {ws.cell(row=5, column=c).value: ws.cell(row=6, column=c).value for c in range(1, ws.max_column + 1) if ws.cell(row=5, column=c).value}


def excel_sayfa(veri: bytes):
    return load_workbook(BytesIO(veri)).active


KALDIRILAN = ("Cinsiyet", "Medeni durum", "Çocuk sayısı", "E-posta", "IBAN", "Banka", "Hesap sahibi", "Deneme süresi", "Çalışma günleri",
              "Ücret geçerlilik başlangıcı", "Ücret ödeme şekli/günü", "Departman", "Haftalık çalışma saati", "Ücret")


class HazirlikTest(TestCase):
    def test_eksik_zorunlu_alanlar_ve_onerilenler(self):
        bos = personel_kur(ad="a", soyad="b", ise_giris_tarihi=BUGUN + timedelta(days=10))
        h = mu.hazirlik(bos, ig.kayit_oku(bos), bugun=BUGUN)
        self.assertFalse(h["hazir"])
        self.assertEqual(set(h["eksikler"]), {"TC kimlik no", "Doğum tarihi", "Doğum yeri", "Baba adı", "Ana adı", "Adres", "Görev", "Meslek kodu"})
        self.assertEqual(set(h["onerilen_eksikler"]), {"Telefon"})                              # IBAN artık istenmez (bildirgeyle ilgisi yok)
        dolu = dolu_personel(ise_giris_tarihi=BUGUN + timedelta(days=10))
        h = mu.hazirlik(dolu, ig.kayit_oku(dolu), bugun=BUGUN)
        self.assertTrue(h["hazir"])
        self.assertEqual((h["eksikler"], h["onerilen_eksikler"]), ([], []))

    def test_kismi_sureli_haftalik_saat_zorunlu(self):
        p = dolu_personel(ise_giris_tarihi=BUGUN + timedelta(days=10), telefon="05321112233")
        ig.kayit_kaydet(p, calisma_sekli="KISMI")                                               # saat girilmedi (firma varsayılanı 45 tam süreyi gösterir)
        h = mu.hazirlik(p, ig.kayit_oku(p), bugun=BUGUN)
        self.assertFalse(h["hazir"])
        self.assertIn("Haftalık çalışma saati (kısmi süreli)", h["eksikler"])
        ig.kayit_kaydet(p, calisma_sekli="KISMI", haftalik_calisma_saati="20")
        self.assertTrue(mu.hazirlik(p, ig.kayit_oku(p), bugun=BUGUN)["hazir"])
        ig.kayit_kaydet(p, calisma_sekli="TAM")
        self.assertTrue(mu.hazirlik(p, ig.kayit_oku(p), bugun=BUGUN)["hazir"])

    def test_kalan_gun_durumlari(self):
        for fark, durum, parca in ((10, "normal", "10 gün var"), (1, "acil", "1 gün kaldı"), (0, "acil", "bugün"), (-3, "gecmis", "3 gün önce işe başladı")):
            p = personel_kur(ad="k", soyad=f"s{fark}", ise_giris_tarihi=BUGUN + timedelta(days=fark))
            h = mu.hazirlik(p, ig.kayit_oku(p), bugun=BUGUN)
            self.assertEqual((h["durum"], h["kalan_gun"]), (durum, fark))
            self.assertIn(parca, h["uyari"])

    def test_gonderildiyse_uyari_yok(self):
        p = personel_kur(ad="g", soyad="g", ise_giris_tarihi=BUGUN + timedelta(days=1))
        mu.gonderildi_isaretle(p, tarih=BUGUN, bugun=BUGUN)
        h = mu.hazirlik(p, ig.kayit_oku(p), bugun=BUGUN)
        self.assertEqual((h["uyari"], h["gonderildi"]), ("", BUGUN))


class PaketIcerigiTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = dolu_personel(cinsiyet="KADIN", medeni_durum="EVLI", cocuk_sayisi=2, eposta="a@b.com", ogrenim_durumu="LISE",
                               telefon="05321112233")
        dolu_firma()
        self.firma = firma_bilgisi_getir()
        self.kayit = ig.kayit_oku(self.p)
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 11, 2), tip="NET", net_tutar=Decimal("40000"))

    def bolum(self, **kw):
        return {b: {e: d for e, d, _z in a} for b, a in mu.bolumler(self.p, self.firma, self.kayit, bugun=BUGUN, **kw)}

    def test_sade_bolumler_ve_alanlar(self):
        b = mu.bolumler(self.p, self.firma, self.kayit, ucret_gorebilir=False, bugun=BUGUN)
        self.assertEqual([x for x, _ in b], ["İşveren", "Sigortalı", "İşe Giriş"])              # ücret yetkisiz: Ücret bölümü yok
        etiketler = {x: [e for e, _d, _z in a] for x, a in b}
        self.assertEqual(etiketler["İşveren"], ["Unvan", "SGK işyeri sicil no", "Vergi dairesi", "Vergi no"])
        self.assertEqual(etiketler["Sigortalı"], ["T.C. Kimlik No", "Adı", "Soyadı", "Doğum tarihi", "Doğum yeri", "Baba adı", "Ana adı", "Uyruk",
                                                  "Öğrenim durumu", "Adres", "Telefon"])
        self.assertEqual(etiketler["İşe Giriş"], ["İşe giriş tarihi", "Meslek kodu", "Meslek adı", "Görev / Departman", "Sigorta kolu", "Çalışma şekli",
                                                  "Sözleşme türü"])
        tum = {e for _x, a in b for e, _d, _z in a}
        for kaldirilan in KALDIRILAN:
            self.assertNotIn(kaldirilan, tum, kaldirilan)

    def test_alan_degerleri(self):
        b = self.bolum()
        self.assertEqual((b["İşveren"]["Unvan"], b["İşveren"]["SGK işyeri sicil no"], b["İşveren"]["Vergi no"]), ("SEMTA ALÜMİNYUM A.Ş.", "1.2345.67", "1234567890"))
        s = b["Sigortalı"]
        self.assertEqual((s["T.C. Kimlik No"], s["Adı"], s["Soyadı"], s["Doğum tarihi"], s["Doğum yeri"]), ("10000000146", "AYŞE", "DEMİR", "12.04.1995", "KAYSERİ"))
        self.assertEqual((s["Baba adı"], s["Ana adı"], s["Uyruk"], s["Öğrenim durumu"], s["Telefon"]), ("HASAN", "FATMA", "T.C.", "Lise", self.p.telefon))
        self.assertTrue(s["Telefon"])
        self.assertIn("ORGANİZE SANAYİ", s["Adres"])
        i = b["İşe Giriş"]
        self.assertEqual((i["İşe giriş tarihi"], i["Meslek kodu"], i["Meslek adı"], i["Görev / Departman"]), ("02.11.2026", "7212.01", "KAYNAKÇI", "KAYNAKÇI / İMALAT"))
        self.assertEqual((i["Sigorta kolu"], i["Çalışma şekli"], i["Sözleşme türü"]), ("Tüm sigorta kolları (01)", "Tam süreli", "Belirsiz süreli"))

    def test_uyruk_varsayilan_ve_ozel(self):
        self.assertEqual(self.bolum()["Sigortalı"]["Uyruk"], "T.C.")
        self.p.uyruk = ""
        self.assertEqual(self.bolum()["Sigortalı"]["Uyruk"], "T.C.")                           # boşsa T.C.
        self.p.uyruk = "ALMAN"
        self.assertEqual(self.bolum()["Sigortalı"]["Uyruk"], "ALMAN")

    def test_sgdp_dali(self):
        self.assertEqual(self.bolum()["İşe Giriş"]["Sigorta kolu"], "Tüm sigorta kolları (01)")
        self.p.emekli_sgdp = True
        self.assertEqual(self.bolum()["İşe Giriş"]["Sigorta kolu"], "SGDP (02)")

    def test_kismi_sureli_dali(self):
        ig.kayit_kaydet(self.p, calisma_sekli="KISMI", haftalik_calisma_saati="20")
        self.kayit = ig.kayit_oku(self.p)
        self.assertEqual(self.bolum()["İşe Giriş"]["Çalışma şekli"], "Kısmi süreli (haftalık 20 saat)")
        ig.kayit_kaydet(self.p, calisma_sekli="KISMI", haftalik_calisma_saati="22,5")
        self.kayit = ig.kayit_oku(self.p)
        self.assertEqual(self.bolum()["İşe Giriş"]["Çalışma şekli"], "Kısmi süreli (haftalık 22,5 saat)")
        ig.kayit_kaydet(self.p, calisma_sekli="KISMI")                                          # saat girilmedi: uyarı metni (hazırlıkta da eksik)
        self.kayit = ig.kayit_oku(self.p)
        self.assertEqual(self.bolum()["İşe Giriş"]["Çalışma şekli"], "Kısmi süreli (haftalık saat girilmedi)")
        ig.kayit_kaydet(self.p, calisma_sekli="TAM", haftalik_calisma_saati="40")               # tam süreli: haftalık saat yazılmaz
        self.kayit = ig.kayit_oku(self.p)
        self.assertEqual(self.bolum()["İşe Giriş"]["Çalışma şekli"], "Tam süreli")
        with self.assertRaises(ig.IseGirisHatasi):
            ig.kayit_kaydet(self.p, calisma_sekli="YARIM")

    def test_ucret_net_asgari_ve_kayitsiz_brut_yok(self):
        u = self.bolum(ucret_gorebilir=True)["Ücret"]
        self.assertEqual(u, {"Aylık net ücret": "40.000,00 TL", "Asgari ücret": "Hayır"})
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 12, 1), tip="ASGARI")
        asgari = {b: {e: d for e, d, _z in a} for b, a in mu.bolumler(self.p, self.firma, self.kayit, ucret_gorebilir=True, bugun=date(2026, 12, 5))}["Ücret"]
        self.assertEqual(asgari, {"Aylık net ücret": "Asgari ücret", "Asgari ücret": "Evet"})
        yok = personel_kur(ad="u", soyad="yok")
        kayitsiz = {b: {e: d for e, d, _z in a} for b, a in mu.bolumler(yok, self.firma, ig.kayit_oku(yok), ucret_gorebilir=True, bugun=BUGUN)}["Ücret"]
        self.assertEqual(kayitsiz, {"Aylık net ücret": "", "Asgari ücret": ""})
        for bolum in (u, asgari, kayitsiz):
            self.assertFalse([k for k in bolum if "rüt" in k])                                  # brüt YOK

    def test_excel_tek_satir_alanlar_bant_ve_alt_not(self):
        ws = excel_sayfa(mu.excel(self.p, self.firma, self.kayit, ucret_gorebilir=False, bugun=BUGUN))
        h = excel_hucreleri(mu.excel(self.p, self.firma, self.kayit, ucret_gorebilir=False, bugun=BUGUN))
        self.assertEqual(list(h), ["Unvan", "SGK işyeri sicil no", "Vergi dairesi", "Vergi no", "T.C. Kimlik No", "Adı", "Soyadı", "Doğum tarihi", "Doğum yeri",
                                   "Baba adı", "Ana adı", "Uyruk", "Öğrenim durumu", "Adres", "Telefon", "İşe giriş tarihi", "Meslek kodu", "Meslek adı",
                                   "Görev / Departman", "Sigorta kolu", "Çalışma şekli", "Sözleşme türü"])
        self.assertEqual((h["T.C. Kimlik No"], h["Adı"], h["Soyadı"], h["Uyruk"], h["Meslek kodu"]), ("10000000146", "AYŞE", "DEMİR", "T.C.", "7212.01"))
        self.assertEqual((h["Sigorta kolu"], h["Çalışma şekli"], h["Görev / Departman"]), ("Tüm sigorta kolları (01)", "Tam süreli", "KAYNAKÇI / İMALAT"))
        for kaldirilan in KALDIRILAN:
            self.assertNotIn(kaldirilan, h, kaldirilan)
        self.assertEqual(ws.max_row, 8)                                                         # başlık, tarih, bant, alan başlıkları, TEK veri satırı, alt not
        self.assertFalse(any(ws.cell(row=7, column=c).value for c in range(1, ws.max_column + 1)))
        self.assertEqual(ws.cell(row=8, column=1).value, "SGK işe giriş bildirgesi işe başlama tarihinden en geç 1 gün önce verilmelidir.")
        bantlar = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1) if ws.cell(row=4, column=c).value]
        self.assertEqual(bantlar, ["İŞVEREN", "SİGORTALI", "İŞE GİRİŞ"])

    def test_excel_ucret_yetkisi(self):
        h = excel_hucreleri(mu.excel(self.p, self.firma, self.kayit, ucret_gorebilir=True, bugun=BUGUN))
        self.assertEqual((h["Aylık net ücret"], h["Asgari ücret"]), ("40.000,00 TL", "Hayır"))
        self.assertEqual(list(h)[-2:], ["Aylık net ücret", "Asgari ücret"])
        self.assertNotIn("Aylık net ücret", excel_hucreleri(mu.excel(self.p, self.firma, self.kayit, ucret_gorebilir=False, bugun=BUGUN)))

    def test_excel_eksik_zorunlu_alan_isaretlenir(self):
        bos = personel_kur(ad="Veli", soyad="Kaya")
        h = excel_hucreleri(mu.excel(bos, self.firma, ig.kayit_oku(bos), bugun=BUGUN))
        for etiket in ("T.C. Kimlik No", "Doğum tarihi", "Doğum yeri", "Baba adı", "Ana adı", "Adres", "Görev / Departman", "Meslek kodu"):
            self.assertEqual(h[etiket], "EKSİK", etiket)
        self.assertEqual(h["Telefon"], None)                                                    # zorunlu olmayan boş alan boş kalır, EKSİK yazmaz
        self.assertEqual((h["Adı"], h["Uyruk"]), ("VELİ", "T.C."))

    def test_excel_tc_metin_olarak_saklanir(self):
        ws = excel_sayfa(mu.excel(self.p, self.firma, self.kayit, bugun=BUGUN))
        hucre = next(ws.cell(row=6, column=c) for c in range(1, ws.max_column + 1) if ws.cell(row=5, column=c).value == "T.C. Kimlik No")
        self.assertEqual((hucre.value, hucre.data_type), ("10000000146", "s"))

    def test_pdf_belgesi_imzasiz_alt_not_ve_ucret_kurali(self):
        b = mu.pdf_belgesi(self.p, self.firma, self.kayit, ucret_gorebilir=False, bugun=BUGUN)
        html = render_to_string("core/ise_giris_paket_pdf.html", {"firma": self.firma, "logo_b64": None, "belgeler": [b], "belge_tarihi": BUGUN, "personel": self.p})
        self.assertIn("İŞE GİRİŞ BİLGİ FORMU (SGK 4/A — MUHASEBE İÇİN)", html)
        self.assertIn("10000000146", html)
        self.assertIn("7212.01", html)
        self.assertIn("Tüm sigorta kolları (01)", html)
        self.assertIn("SGK işe giriş bildirgesi işe başlama tarihinden en geç 1 gün önce verilmelidir.", html)
        self.assertNotIn('class="imza"', html)                                                  # imzasız: işçi/işveren kutuları yok
        self.assertNotIn("40.000", html)
        for kaldirilan in ("Cinsiyet", "Medeni durum", "Çocuk sayısı", "E-posta", "IBAN", "Deneme süresi", "Çalışma günleri", "geçerlilik", "ödeme şekli", "a@b.com"):
            self.assertNotIn(kaldirilan, html, kaldirilan)
        b = mu.pdf_belgesi(self.p, self.firma, self.kayit, ucret_gorebilir=True, bugun=BUGUN)
        self.assertEqual([x for x, _ in b["bolumler"]], ["İşveren", "Sigortalı", "İşe Giriş", "Ücret"])
        self.assertIn("40.000,00 TL", str(b["bolumler"][-1][1]))
        self.assertNotIn("rüt", str(b["bolumler"]))

    def test_dosya_adi_turkce_karakter_temiz(self):
        p = personel_kur(ad="özgür", soyad="çağlayan şahin")
        self.assertEqual(mu.dosya_adi(p, "xlsx", BUGUN), "ise_giris_muhasebe_ozgur_caglayan_sahin_20261030.xlsx")


class GonderildiTest(TestCase):
    def test_isaretle_geri_al_ve_diger_alanlara_dokunmaz(self):
        p = personel_kur(ad="g", soyad="h")
        ig.paket_basildi(p)
        once = IseGirisKaydi.objects.get(personel=p).paket_basim_zamani
        k = mu.gonderildi_isaretle(p, tarih=BUGUN - timedelta(days=1), bugun=BUGUN)
        self.assertEqual(k.muhasebe_gonderim_tarihi, BUGUN - timedelta(days=1))
        k = IseGirisKaydi.objects.get(personel=p)
        self.assertEqual((k.muhasebe_gonderim_tarihi, k.paket_basim_zamani), (BUGUN - timedelta(days=1), once))
        mu.gonderildi_isaretle(p, geri_al=True, bugun=BUGUN)
        self.assertIsNone(IseGirisKaydi.objects.get(personel=p).muhasebe_gonderim_tarihi)

    def test_tarih_bos_bugun_ve_gelecek_reddedilir(self):
        p = personel_kur(ad="g", soyad="h")
        self.assertEqual(mu.gonderildi_isaretle(p, bugun=BUGUN).muhasebe_gonderim_tarihi, BUGUN)
        with self.assertRaises(ig.IseGirisHatasi):
            mu.gonderildi_isaretle(p, tarih=BUGUN + timedelta(days=1), bugun=BUGUN)

    def test_kayit_yokken_olusturur(self):
        p = personel_kur(ad="y", soyad="z")
        self.assertEqual(IseGirisKaydi.objects.count(), 0)
        mu.gonderildi_isaretle(p, bugun=BUGUN)
        self.assertEqual(IseGirisKaydi.objects.count(), 1)


class MuhasebeGorunumTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("muyon", password="x")
        self.belgeci = kullanici("mubelgeci", "personel", "personel_belgeleri")
        self.ucretli = kullanici("muucretli", "personel", "personel_belgeleri", "personel_ucret")
        self.sade = kullanici("musade", "personel")
        self.p = dolu_personel(ise_giris_tarihi=tr_bugun() + timedelta(days=5))
        dolu_firma()
        self.xlsx = reverse("core:ise_giris_muhasebe_indir", args=[self.p.pk, "xlsx"])
        self.pdf = reverse("core:ise_giris_muhasebe_indir", args=[self.p.pk, "pdf"])
        self.gonder = reverse("core:ise_giris_muhasebe_gonderildi", args=[self.p.pk])
        self.sekme = reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=ise_giris"

    def test_indirmeler_ve_yazmama(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(self.xlsx)
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("ise_giris_muhasebe_ayse_demir_", r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        r = self.client.get(self.pdf)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertEqual(IseGirisKaydi.objects.count(), 0)                                      # indirme gönderim sayılmaz, kayıt oluşturmaz

    def test_gecersiz_bicim_404(self):
        self.client.force_login(self.belgeci)
        self.assertEqual(self.client.get(self.xlsx.replace("xlsx", "doc")).status_code, 404)

    def test_yetki(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.xlsx).status_code, 302)
        self.assertEqual(self.client.post(self.gonder, {}).status_code, 302)
        for kisi in (self.sade, kullanici("muyalnizbelge", "personel_belgeleri")):               # personel + personel_belgeleri ikisi de gerekli
            self.client.force_login(kisi)
            for url in (self.xlsx, self.pdf):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(self.gonder, {}).status_code, 403)
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(self.xlsx).status_code, 200)

    def test_excel_ucret_yalniz_ucret_yetkilisine(self):
        ucret_ekle(self.p, gecerlilik_baslangic=tr_bugun() + timedelta(days=5), tip="NET", net_tutar=Decimal("40000"))
        self.client.force_login(self.belgeci)
        self.assertNotIn("Aylık net ücret", excel_hucreleri(self.client.get(self.xlsx).content))
        self.client.force_login(self.ucretli)
        h = excel_hucreleri(self.client.get(self.xlsx).content)
        self.assertEqual((h["Aylık net ücret"], h["Asgari ücret"]), ("40.000,00 TL", "Hayır"))

    def test_gonderildi_isaretle_ve_geri_al(self):
        self.client.force_login(self.belgeci)
        self.assertEqual(self.client.get(self.gonder).status_code, 302)                         # GET yazmaz
        self.assertEqual(IseGirisKaydi.objects.count(), 0)
        r = self.client.post(self.gonder, {"tarih": tr_bugun().isoformat()}, follow=True)
        self.assertRedirects(r, self.sekme + "#muhasebe-paketi", fetch_redirect_response=False)
        self.assertEqual(IseGirisKaydi.objects.get(personel=self.p).muhasebe_gonderim_tarihi, tr_bugun())
        html = self.client.get(self.sekme).content.decode()
        self.assertIn("Muhasebeye gönderildi: " + tr_bugun().strftime("%d.%m.%Y"), html)
        self.assertIn("Gönderildi işaretini kaldır", html)
        self.client.post(self.gonder, {"geri_al": "1"})
        self.assertIsNone(IseGirisKaydi.objects.get(personel=self.p).muhasebe_gonderim_tarihi)

    def test_gelecek_ve_gecersiz_tarih_mesaj(self):
        self.client.force_login(self.belgeci)
        r = self.client.post(self.gonder, {"tarih": (tr_bugun() + timedelta(days=2)).isoformat()}, follow=True)
        self.assertContains(r, "Gönderim tarihi gelecekte olamaz")
        r = self.client.post(self.gonder, {"tarih": "yanlis"}, follow=True)
        self.assertContains(r, "Geçersiz tarih")
        self.assertEqual(IseGirisKaydi.objects.count(), 0)

    def test_sekme_bolumu_eksik_ve_kalan_gun(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(self.sekme)
        self.assertContains(r, "Muhasebe Paketi")
        self.assertContains(r, self.xlsx)
        self.assertContains(r, self.pdf)
        self.assertContains(r, "İşe girişe 5 gün var")
        self.assertContains(r, "Hazır")
        self.assertNotContains(r, "Muhasebe için eksik bilgiler")
        self.assertContains(r, "Ücret görme yetkiniz olmadığı için pakete ücret girmez")
        bos = personel_kur(ad="Veli", soyad="Kaya")
        r = self.client.get(reverse("core:personel_detay", args=[bos.pk]) + "?sekme=ise_giris")
        self.assertContains(r, "Muhasebe için eksik bilgiler")
        self.assertContains(r, "Eksik bilgi var")
        self.assertContains(r, "TC kimlik no")
        self.assertContains(r, "gün önce işe başladı")                                          # 2020'de işe girmiş: bildirge gecikmiş uyarısı
