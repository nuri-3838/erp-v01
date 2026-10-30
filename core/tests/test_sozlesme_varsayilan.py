"""Sözleşme çalışma düzeni firma varsayılanı (AYARLAR › Belge Şablonları): her personelin iş sözleşmesine otomatik gelir; kişiye özel değer varsa o geçer;
eski (düzenlenmemiş) varsayılan şablon metinleri güncele yükselir."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from core.models import BelgeSablonu, IkAyari, IseGirisKaydi
from core.services import belge_sablon as bs
from core.services import ise_giris as ig
from core.services.belge_sablon_eski import ESKI_BASLIKLAR, ESKI_METINLER
from core.tests.ik_yardimci import OzelDizinTestTemel, personel_kur
from core.tests.test_ise_giris_paket import dolu_firma, dolu_personel, kullanici


def sozlesme_metni(personel, firma, ayar=None):
    bs.hazirla()
    belgeler = ig.paket_hazirla(personel, firma, ig.kayit_oku(personel), [bs.getir("SOZLESME")], nusha=1, bugun=date(2026, 10, 30), ayar=ayar)
    return "\n\n".join(belgeler[0]["paragraflar"])


class VarsayilanDegerlerTest(TestCase):
    def test_ayar_varsayilanlari_kullanici_degerleri(self):
        a = IkAyari.get()
        self.assertEqual((a.sozlesme_deneme_suresi_ay, a.sozlesme_haftalik_saat, a.sozlesme_calisma_gunleri), (2, Decimal("45"), "Pazartesi – Cuma"))
        self.assertEqual(a.sozlesme_ucret_odeme, "Bir sonraki ayın 10. iş günü")
        satirlar = a.sozlesme_calisma_saatleri.split("\n")
        self.assertEqual(satirlar[0], "Pazartesi – Perşembe:")
        self.assertIn("Cuma:", satirlar)
        self.assertIn("15:45 – 18:00 Çalışma", satirlar)
        self.assertIn("15:45 – 18:30 Çalışma", satirlar)
        self.assertIn("12:00 – 13:00 Öğle arası", satirlar)
        self.assertEqual(sum(1 for s in satirlar if "Çay molası" in s), 4)

    def test_varsayilan_cizelge_haftalik_45_saat_tutar(self):
        """Mola/öğle arası hariç çalışma aralıkları: Pzt–Per ×4 + Cuma = haftalık saat ayarıyla aynı olmalı (tutarlılık bekçisi)."""
        def saat(s):
            h, m = s.split(":")
            return int(h) * 60 + int(m)
        gunler, toplam = {}, None
        for satir in IkAyari.get().sozlesme_calisma_saatleri.split("\n"):
            if satir.endswith(":"):
                gun = satir[:-1]
                gunler[gun] = 0
            else:
                m = re.match(r"(\d\d:\d\d) – (\d\d:\d\d) Çalışma$", satir)
                if m:
                    gunler[gun] += saat(m.group(2)) - saat(m.group(1))
        self.assertEqual(gunler["Pazartesi – Perşembe"], 9 * 60)
        self.assertEqual(gunler["Cuma"], 9 * 60)
        self.assertEqual(Decimal(4 * gunler["Pazartesi – Perşembe"] + gunler["Cuma"]) / 60, IkAyari.get().sozlesme_haftalik_saat)


class EtkinDegerTest(TestCase):
    def test_kisiye_ozel_varsa_o_yoksa_varsayilan(self):
        p = personel_kur()
        ayar = IkAyari.get()
        e = ig.etkin_sozlesme(ig.kayit_oku(p), ayar)
        self.assertEqual((e["deneme_suresi_ay"], e["haftalik_calisma_saati"], e["calisma_gunleri"], e["ucret_odeme"]),
                         (2, Decimal("45"), "Pazartesi – Cuma", "Bir sonraki ayın 10. iş günü"))
        ig.kayit_kaydet(p, deneme_suresi_ay="1", haftalik_calisma_saati="40", calisma_gunleri="Pazartesi – Cumartesi", calisma_saatleri="09:00 – 18:00",
                        ucret_odeme="Ayın 1'i")
        e = ig.etkin_sozlesme(ig.kayit_oku(p), ayar)
        self.assertEqual((e["deneme_suresi_ay"], e["haftalik_calisma_saati"], e["calisma_gunleri"], e["calisma_saatleri"], e["ucret_odeme"]),
                         (1, Decimal("40.0"), "Pazartesi – Cumartesi", "09:00 – 18:00", "Ayın 1'i"))
        ig.kayit_kaydet(p, deneme_suresi_ay="0")                                                 # 0 = deneme süresi yok: varsayılana düşmez
        self.assertEqual(ig.etkin_sozlesme(ig.kayit_oku(p), ayar)["deneme_suresi_ay"], 0)
        ig.kayit_kaydet(p)                                                                       # hepsi boş → tamamen varsayılan
        self.assertEqual(ig.etkin_sozlesme(ig.kayit_oku(p), ayar)["haftalik_calisma_saati"], Decimal("45"))


class CiktiyaYansimaTest(OzelDizinTestTemel):
    def test_yeni_personel_sozlesmesi_varsayilanlarla_dolar(self):
        p, firma = dolu_personel(), dolu_firma()                                                 # işe giriş kaydı YOK: yine de dolu gelir
        self.assertEqual(IseGirisKaydi.objects.count(), 0)
        metin = sozlesme_metni(p, firma)
        for parca in ("2 ay deneme süresi", "Haftalık çalışma süresi 45 saattir", "Çalışma günleri: Pazartesi – Cuma",
                      "Pazartesi – Perşembe:\n08:00 – 10:00 Çalışma\n10:00 – 10:15 Çay molası", "Cuma:\n08:00 – 10:00 Çalışma",
                      "15:45 – 18:30 Çalışma", "Ödeme şekli/günü: Bir sonraki ayın 10. iş günü"):
            self.assertIn(parca, metin)
        self.assertNotIn(bs.BOS, metin.split("MADDE 6")[0].split("MADDE 4")[1])                  # deneme/çalışma bölümünde noktalı çizgi kalmadı
        self.assertEqual(ig.eksik_bilgiler(p, firma, ig.kayit_oku(p)), [])                      # sözleşme bilgileri eksik sayılmaz

    def test_kisiye_ozel_deger_varsayilani_ezer_ve_ayar_degisince_cikti_degisir(self):
        p, firma = dolu_personel(), dolu_firma()
        ig.kayit_kaydet(p, haftalik_calisma_saati="40", calisma_gunleri="Pazartesi – Cumartesi")
        metin = sozlesme_metni(p, firma)
        self.assertIn("Haftalık çalışma süresi 40 saattir", metin)
        self.assertIn("Çalışma günleri: Pazartesi – Cumartesi", metin)
        self.assertIn("2 ay deneme süresi", metin)                                               # alan boş → varsayılan
        ig.sozlesme_ayar_kaydet(deneme_suresi_ay="1", haftalik_calisma_saati="45", calisma_gunleri="Pazartesi – Cuma",
                                calisma_saatleri="08:00 – 17:00", ucret_odeme="Ayın 5'i")
        metin = sozlesme_metni(p, firma)                                                         # kopya yok: ayar değişince çıktı güncel değeri kullanır
        self.assertIn("1 ay deneme süresi", metin)
        self.assertIn("08:00 – 17:00", metin)
        self.assertIn("Ödeme şekli/günü: Ayın 5'i", metin)
        self.assertNotIn("Çay molası", metin)

    def test_pdf_sablonunda_cizelge_satirlari(self):
        p, firma = dolu_personel(), dolu_firma()
        bs.hazirla()
        belgeler = ig.paket_hazirla(p, firma, ig.kayit_oku(p), [bs.getir("SOZLESME")], nusha=1, bugun=date(2026, 10, 30))
        html = render_to_string("core/ise_giris_paket_pdf.html", {"firma": firma, "logo_b64": None, "belgeler": belgeler,
                                                                 "belge_tarihi": date(2026, 10, 30), "personel": p})
        self.assertIn("15:45 – 18:30 Çalışma", html)
        self.assertIn("white-space: pre-line", html)                                              # satır sonları PDF'te korunur

    def test_kayitta_cok_satirli_saat_korunur(self):
        p = personel_kur()
        ig.kayit_kaydet(p, calisma_saatleri="  Pazartesi – Perşembe:  \r\n08:00 –   18:00\n\n  Cuma:\n08:00 – 18:30  ")
        self.assertEqual(IseGirisKaydi.objects.get(personel=p).calisma_saatleri, "Pazartesi – Perşembe:\n08:00 – 18:00\nCuma:\n08:00 – 18:30")


class AyarKaydetServisTest(TestCase):
    def test_kaydet_normalize_ve_dogrulama(self):
        ig.sozlesme_ayar_kaydet(deneme_suresi_ay="2", haftalik_calisma_saati="42,5", calisma_gunleri="  Pazartesi –  Cuma ",
                                calisma_saatleri="A:\r\n 08:00 – 17:00 \n\nB:", ucret_odeme="  Ayın  5'i ")
        a = IkAyari.get()
        self.assertEqual((a.sozlesme_deneme_suresi_ay, str(a.sozlesme_haftalik_saat), a.sozlesme_calisma_gunleri), (2, "42.5", "Pazartesi – Cuma"))
        self.assertEqual((a.sozlesme_calisma_saatleri, a.sozlesme_ucret_odeme), ("A:\n08:00 – 17:00\nB:", "Ayın 5'i"))
        for ek in ({"deneme_suresi_ay": "5"}, {"deneme_suresi_ay": "x"}, {"haftalik_calisma_saati": "46"}, {"haftalik_calisma_saati": "0"}):
            veri = {"deneme_suresi_ay": "2", "haftalik_calisma_saati": "45", "calisma_gunleri": "g", "calisma_saatleri": "s", "ucret_odeme": "u", **ek}
            with self.assertRaises(ig.IseGirisHatasi, msg=ek):
                ig.sozlesme_ayar_kaydet(**veri)
        self.assertEqual(IkAyari.get().sozlesme_calisma_gunleri, "Pazartesi – Cuma")

    def test_bos_sayilar_eski_degeri_korur(self):
        ig.sozlesme_ayar_kaydet(deneme_suresi_ay="", haftalik_calisma_saati="", calisma_gunleri="x", calisma_saatleri="y", ucret_odeme="z")
        a = IkAyari.get()
        self.assertEqual((a.sozlesme_deneme_suresi_ay, a.sozlesme_haftalik_saat), (2, Decimal("45")))


class AyarEkraniTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("svyon", password="x")
        self.sade = kullanici("svsade", "personel", "personel_belgeleri")
        self.url = reverse("core:sozlesme_varsayilan_kaydet")

    def test_yalniz_yonetici(self):
        self.assertEqual(self.client.post(self.url, {}).status_code, 302)
        self.client.force_login(self.sade)
        self.assertEqual(self.client.post(self.url, {"sozlesme_deneme_suresi_ay": "1"}).status_code, 403)
        self.assertEqual(IkAyari.get().sozlesme_deneme_suresi_ay, 2)

    def test_liste_formu_dolu_gelir_kaydeder_ve_hata_gosterir(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:belge_sablonlari"))
        self.assertContains(r, "Varsayılan Sözleşme Bilgileri")
        self.assertContains(r, "Bir sonraki ayın 10. iş günü")
        self.assertContains(r, "15:45 – 18:30 Çalışma")
        veri = {"sozlesme_deneme_suresi_ay": "1", "sozlesme_haftalik_saat": "40", "sozlesme_calisma_gunleri": "Pazartesi – Cuma",
                "sozlesme_calisma_saatleri": "08:00 – 17:00", "sozlesme_ucret_odeme": "Ayın 5'i"}
        r = self.client.post(self.url, veri, follow=True)
        self.assertRedirects(r, reverse("core:belge_sablonlari"))
        self.assertContains(r, "kaydedildi")
        a = IkAyari.get()
        self.assertEqual((a.sozlesme_deneme_suresi_ay, str(a.sozlesme_haftalik_saat), a.sozlesme_calisma_saatleri), (1, "40.0", "08:00 – 17:00"))
        r = self.client.post(self.url, {**veri, "sozlesme_haftalik_saat": "50"}, follow=True)
        self.assertContains(r, "en çok 45")
        self.assertEqual(str(IkAyari.get().sozlesme_haftalik_saat), "40.0")
        r = self.client.post(self.url, {**veri, "sozlesme_deneme_suresi_ay": "9"}, follow=True)
        self.assertContains(r, "Kayıt yapılamadı")

    def test_kart_sekmesi_gecerli_degerleri_ve_ipuclarini_gosterir(self):
        p = personel_kur()
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]) + "?sekme=ise_giris")
        self.assertContains(r, "Geçerli deneme süresi")
        self.assertContains(r, "2 ay")
        self.assertContains(r, "Pazartesi – Cuma")
        self.assertContains(r, 'placeholder="Varsayılan: 2"')
        self.assertContains(r, 'placeholder="Varsayılan: 45"')
        self.assertEqual(r.context["etkin_sozlesme"]["haftalik_calisma_saati"], Decimal("45"))
        self.assertEqual(IseGirisKaydi.objects.count(), 0)                                       # GET yazmaz

    def test_menu_ve_url(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:belge_sablonlari"))
        self.assertEqual((r.context["aktif_modul_kod"], r.context["aktif_ekran"]), ("AYARLAR", "core:belge_sablonlari"))


class SablonYukseltmeTest(TestCase):
    def eski(self, kod, sira=0):
        return ESKI_METINLER[kod][sira]

    def test_duzenlenmemis_eski_varsayilan_guncele_yukselir(self):
        for kod, v in bs.VARSAYILAN.items():
            BelgeSablonu.objects.create(kod=kod, baslik=ESKI_BASLIKLAR[kod][0], metin=self.eski(kod, 0), sira=v["sira"])      # Dilim 1 sürümü
        bs.hazirla()
        for kod, v in bs.VARSAYILAN.items():
            s = BelgeSablonu.objects.get(kod=kod)
            self.assertEqual((s.metin.strip(), s.baslik), (v["metin"].strip(), v["baslik"]), kod)
        self.assertNotIn("{ucret} ücret ödenir", BelgeSablonu.objects.get(kod="SOZLESME").metin)
        self.assertIn("{calisma_saatleri}\nHafta tatili", BelgeSablonu.objects.get(kod="SOZLESME").metin)

    def test_onceki_surum_dilim2_de_yukselir(self):
        BelgeSablonu.objects.create(kod="SOZLESME", baslik="BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ", metin=self.eski("SOZLESME", 1), sira=1)
        bs.hazirla()
        self.assertEqual(BelgeSablonu.objects.get(kod="SOZLESME").metin.strip(), bs.VARSAYILAN["SOZLESME"]["metin"].strip())

    def test_duzenlenmis_metne_ve_ozel_basliga_dokunulmaz(self):
        BelgeSablonu.objects.create(kod="SOZLESME", baslik="BENİM BAŞLIĞIM", metin=self.eski("SOZLESME", 0) + "\nEK MADDE: benim eklediğim.", sira=1)
        BelgeSablonu.objects.create(kod="KVKK_AYDINLATMA", baslik="ÖZEL KVKK BAŞLIĞI", metin=self.eski("KVKK_AYDINLATMA", 0), sira=2)
        bs.hazirla()
        s = BelgeSablonu.objects.get(kod="SOZLESME")
        self.assertEqual(s.baslik, "BENİM BAŞLIĞIM")
        self.assertIn("EK MADDE: benim eklediğim.", s.metin)
        self.assertIn("{ucret} ücret ödenir", s.metin)                                           # düzenlenmiş: eski metin aynen kaldı
        k = BelgeSablonu.objects.get(kod="KVKK_AYDINLATMA")
        self.assertEqual(k.baslik, "ÖZEL KVKK BAŞLIĞI")                                          # metin güncellendi, özel başlık korundu
        self.assertEqual(k.metin.strip(), bs.VARSAYILAN["KVKK_AYDINLATMA"]["metin"].strip())

    def test_guncel_varsayilan_ve_tekrar_cagri_degismez(self):
        bs.hazirla()
        once = {s.kod: s.updated_at for s in BelgeSablonu.objects.all()}
        bs.hazirla()
        self.assertEqual(once, {s.kod: s.updated_at for s in BelgeSablonu.objects.all()})

    def test_varsayilan_metinler_eski_listede_tek_degil_guncel_sabit(self):
        for kod, v in bs.VARSAYILAN.items():
            self.assertEqual(bs.bilinmeyen_yer_tutucular(v["metin"]), [], kod)
            self.assertNotIn(v["metin"].strip(), {m.strip() for m in ESKI_METINLER[kod][:1]}, kod)   # güncel metin Dilim 1 metni değil
