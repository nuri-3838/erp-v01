"""Personel kartı sekmeli görünüm (yalnız görünüm/menü; veri ve hesap değişmez): sekmeler, yetkiye göre gizlenme, sekme dönüşleri,
menü (iki giriş kalktı, yetki kodları + URL'ler duruyor), listede 'Süresi dolacak belge var' süzgeci ve rozeti, sorgu sayısı."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import EkranYetki, PersonelBelge
from core.moduller import MODULLER
from core.services.mesai_hesap import hesap_olustur
from core.services.personel_belge import belge_ekle
from core.services.personel_izin import izin_ekle
from core.services.personel_ucret import ucret_ekle
from core.tarih import tr_bugun
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, personel_kur, yuklenen
from core.yetki import kullanici_menusu


def kullanici(ad, *kodlar):
    u = User.objects.create_user(ad, password="x")
    for k in kodlar:
        EkranYetki.objects.create(kullanici=u, ekran_kod=k)
    return u


class SekmeTaban(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("skyon", password="x")
        self.tam = kullanici("sktam", "personel", "personel_ucret", "personel_izinleri", "personel_belgeleri", "personel_devam")
        self.sade = kullanici("sksade", "personel")
        self.p = personel_kur(ad="ayşe", soyad="demir", gorev="kaynakçı", departman="İMALAT", notlar="ÖZEL NOT METNİ",
                              telefon="05321112233", acil_durum_kisi="FATMA")
        self.url = reverse("core:personel_detay", args=[self.p.pk])

    def sekme(self, kod=None, kisi=None):
        self.client.force_login(kisi or self.yon)
        return self.client.get(self.url, {"sekme": kod} if kod else {})

    def tab_adlari(self, r):
        return [t["ad"] for t in r.context["sekmeler"]]


class SekmeIcerikTest(SekmeTaban):
    def test_yonetici_alti_sekme_gorur_varsayilan_genel(self):
        r = self.sekme()
        self.assertEqual(r.context["sekme"], "genel")
        self.assertEqual(self.tab_adlari(r), ["Genel", "Ücret", "İzin", "Belgeler", "Devam", "Mesai Hesabı"])
        self.assertContains(r, 'class="ik-sekmeler"')
        self.assertContains(r, 'aria-current="page"')

    def test_genel_sekmesi_icerigi_ve_diger_sekme_icerigi_yok(self):
        r = self.sekme("genel")
        for metin in ("Kimlik", "İletişim", "Acil Durum", "Banka Bilgileri", "İş Bilgileri", "Kıdem", "ÖZEL NOT METNİ", "Fotoğraf Yükle"):
            self.assertContains(r, metin)
        for metin in ("+ Ücret Ekle", "+ İzin Ekle", "+ Belge Ekle", "Aylık Özet", "Mesai Hesabı Oluştur"):
            self.assertNotContains(r, metin)

    def test_foto_ve_kimlik_genel_sekmesinde_baslikta_degil(self):
        r = self.sekme("genel")
        html = r.content.decode()
        baslik = html[html.index('class="detay-baslik-kart"'):html.index('class="ik-sekmeler"')]
        self.assertNotIn("Fotoğraf", baslik)
        self.assertIn("Fotoğraf Yükle", html[html.index('class="ik-sekmeler"'):])
        self.assertIn("AYŞE DEMİR", baslik)
        self.assertIn("KAYNAKÇI", baslik)
        self.assertIn("Çalışıyor", baslik)
        self.assertIn("Düzenle", html[:html.index('class="ik-sekmeler"')])

    def test_her_sekmenin_kendi_icerigi(self):
        beklenen = {"ucret": "+ Ücret Ekle", "izin": "+ İzin Ekle", "belgeler": "+ Belge Ekle", "devam": "Aylık Özet", "mesai": "Mesai Hesabı Oluştur"}
        for kod, metin in beklenen.items():
            r = self.sekme(kod)
            self.assertEqual(r.context["sekme"], kod)
            self.assertContains(r, metin, msg_prefix=kod)
            self.assertNotContains(r, "Banka Bilgileri", msg_prefix=kod)                      # Genel kartları yalnız Genel'de
        r = self.sekme("izin")
        for metin in ("Kalan", "Hak Edilen", "Kullanılan"):
            self.assertContains(r, metin)

    def test_gecersiz_sekme_genele_duser(self):
        for kod in ("yok", "", "GENEL", "../x"):
            self.assertEqual(self.sekme(kod).context["sekme"], "genel", kod)

    def test_yetkisiz_sekme_gizli_ve_genele_duser(self):
        r = self.sekme(kisi=self.sade)
        self.assertEqual(self.tab_adlari(r), ["Genel"])
        for kod in ("ucret", "izin", "belgeler", "devam", "mesai"):
            r = self.sekme(kod, kisi=self.sade)
            self.assertEqual(r.context["sekme"], "genel", kod)
        r = self.sekme(kisi=self.tam)                                                         # yönetici değil: mesai hesabı sekmesi yok
        self.assertEqual(self.tab_adlari(r), ["Genel", "Ücret", "İzin", "Belgeler", "Devam"])
        self.assertEqual(self.sekme("mesai", kisi=self.tam).context["sekme"], "genel")
        kismi = kullanici("skkismi", "personel", "personel_izinleri")
        self.assertEqual(self.tab_adlari(self.sekme(kisi=kismi)), ["Genel", "İzin"])

    def test_anonim_ve_personel_yetkisiz(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(kullanici("skbos"))
        self.assertEqual(self.client.get(self.url, {"sekme": "ucret"}).status_code, 403)
        self.client.force_login(kullanici("skucretsiz", "personel_ucret"))                    # personel ekranı olmadan kart açılmaz
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_sayi_rozetleri(self):
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 2))
        izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 7, 1), bitis=date(2026, 7, 2))
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        sayilar = lambda r: {t["ad"]: (t["sayi"], t["uyari"]) for t in r.context["sekmeler"]}
        for kod in ("genel", "izin", "ucret", "belgeler"):                                    # rozetler açık sekmeden bağımsız
            s = sayilar(self.sekme(kod))
            self.assertEqual((s["İzin"], s["Ücret"], s["Belgeler"]), ((2, False), (1, False), (0, False)), kod)
            self.assertEqual((s["Genel"], s["Devam"], s["Mesai Hesabı"]), ((None, False),) * 3)
        self.assertContains(self.sekme("genel"), '<span class="sayi">2</span>')

    def test_belge_rozeti_suresi_dolan_icin_uyari_sinifi(self):
        bugun = tr_bugun()
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT), bitis_tarihi=bugun - timedelta(days=3))
        s = {t["ad"]: t for t in self.sekme("genel").context["sekmeler"]}
        self.assertEqual((s["Belgeler"]["sayi"], s["Belgeler"]["uyari"]), (1, True))
        self.assertContains(self.sekme("genel"), 'class="sayi uyari">1</span>')
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k2.pdf", PDF_BAYT), bitis_tarihi=bugun + timedelta(days=300))   # yenilendi → uyarı kalkar
        s = {t["ad"]: t for t in self.sekme("genel").context["sekmeler"]}
        self.assertEqual((s["Belgeler"]["sayi"], s["Belgeler"]["uyari"]), (2, False))

    def test_mobil_sekmeler_yatay_kaydirmali(self):
        html = self.sekme("izin").content.decode()
        kural = re.search(r"\.ik-sekmeler \{[^}]*\}", html).group(0)
        self.assertIn("overflow-x:auto", kural)
        self.assertIn("display:flex", kural)
        self.assertIn("white-space:nowrap", re.search(r"\.ik-sekmeler a \{[^}]*\}", html).group(0))
        self.assertIn(".detay-kapsa { min-width:0", html)                                      # sayfa yana taşmaz


class SekmeDonusTest(SekmeTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)

    def donus(self, kod):
        return self.url + ("" if kod == "genel" else f"?sekme={kod}")

    def test_ucret_ekle_duzenle_sil_ucret_sekmesine_doner(self):
        r = self.client.post(reverse("core:ucret_ekle"), {"personel": self.p.pk, "gecerlilik_baslangic": "2026-06-01", "tip": "ASGARI",
                                                          "net_tutar": "", "aciklama": ""})
        self.assertRedirects(r, self.donus("ucret"))
        u = self.p.ucretler.get() if hasattr(self.p, "ucretler") else __import__("core.models", fromlist=["PersonelUcret"]).PersonelUcret.objects.get(personel=self.p)
        r = self.client.post(reverse("core:ucret_duzenle", args=[u.pk]), {"gecerlilik_baslangic": "2026-06-01", "tip": "NET",
                                                                           "net_tutar": "40.000,00", "aciklama": "x"})
        self.assertRedirects(r, self.donus("ucret"))
        r = self.client.post(reverse("core:ucret_sil", args=[u.pk]))
        self.assertRedirects(r, self.donus("ucret"))

    def test_izin_ekle_ve_sil_izin_sekmesine_doner(self):
        r = self.client.post(reverse("core:izin_ekle"), {"personel": self.p.pk, "tur": "YILLIK", "baslangic": "2026-06-01",
                                                         "bitis": "2026-06-05", "gun": "", "aciklama": "", "sonraki": "personel"})
        self.assertRedirects(r, self.donus("izin"))
        i = izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 8, 1), bitis=date(2026, 8, 1))
        r = self.client.post(reverse("core:izin_sil", args=[i.pk]), {"sonraki": "personel"})
        self.assertRedirects(r, self.donus("izin"))
        r = self.client.post(reverse("core:izin_ekle"), {"personel": self.p.pk, "tur": "YILLIK", "baslangic": "2026-09-01",
                                                         "bitis": "2026-09-01", "gun": "", "aciklama": ""})     # kartın dışından: liste
        self.assertRedirects(r, reverse("core:izinler"))

    def test_belge_ekle_ve_sil_belgeler_sekmesine_doner(self):
        r = self.client.post(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "KIMLIK", "sonraki": "personel",
                                                          "dosya": yuklenen("k.pdf", PDF_BAYT)})
        self.assertRedirects(r, self.donus("belgeler"))
        b = PersonelBelge.objects.get(personel=self.p)
        r = self.client.post(reverse("core:belge_sil", args=[b.pk]), {"sonraki": "personel"})
        self.assertRedirects(r, self.donus("belgeler"))

    def test_mesai_hesabi_islemleri_mesai_sekmesine_doner(self):
        r = self.client.post(reverse("core:mesai_hesap_olustur", args=[self.p.pk]), {"kullanici_adi": "ayse.d", "sifre": "cokGizli!2026"})
        self.assertRedirects(r, self.donus("mesai"))
        r = self.client.post(reverse("core:mesai_sifre_sifirla", args=[self.p.pk]), {"sifre": "yeniSifre!2027"})
        self.assertRedirects(r, self.donus("mesai"))
        r = self.client.post(reverse("core:mesai_hesap_durum", args=[self.p.pk]))
        self.assertRedirects(r, self.donus("mesai"))
        r = self.client.post(reverse("core:mesai_hesap_atolye", args=[self.p.pk]))
        self.assertRedirects(r, self.donus("mesai"))

    def test_kart_duzenle_ve_foto_genele_doner(self):
        r = self.client.post(reverse("core:personel_foto_kaldir", args=[self.p.pk]))
        self.assertRedirects(r, self.donus("genel"))
        r = self.client.post(reverse("core:personel_foto_yukle", args=[self.p.pk]), {})
        self.assertRedirects(r, self.donus("genel"))

    def test_form_geri_baglantilari_ilgili_sekmeye(self):
        self.assertContains(self.client.get(reverse("core:ucret_ekle"), {"personel": self.p.pk}), self.donus("ucret"))
        hesap_olustur(self.p, kullanici_adi="ayse.d", sifre="cokGizli!2026")
        self.assertContains(self.client.get(reverse("core:mesai_sifre_sifirla", args=[self.p.pk])), self.donus("mesai"))


class MenuTest(SekmeTaban):
    def ik_kodlari(self, kisi):
        return [e.kod for m in kullanici_menusu(kisi) if m.kod == "IK" for e in m.ekranlar]

    def test_menuden_iki_giris_kalkti_digerleri_ayni(self):
        kodlar = self.ik_kodlari(self.yon)
        self.assertNotIn("personel_ucret", kodlar)
        self.assertNotIn("personel_belgeleri", kodlar)
        self.assertEqual(kodlar, ["personel", "personel_izinleri", "personel_devam", "resmi_tatil", "mesai_kayitlari", "bordro", "yemek_takibi"])
        self.assertEqual(self.ik_kodlari(self.tam), ["personel", "personel_izinleri", "personel_devam"])

    def test_yetki_kodlari_tanimda_duruyor_kullanici_yetkileri_ekraninda_gorunur(self):
        ekranlar = {e.kod: e for m in MODULLER for e in m.ekranlar}
        for kod in ("personel_ucret", "personel_belgeleri"):
            self.assertIn(kod, ekranlar)
            self.assertFalse(ekranlar[kod].menude)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_yetkileri"), {"kullanici": self.tam.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="personel_ucret"')
        self.assertContains(r, 'value="personel_belgeleri"')

    def test_eski_adresler_calismaya_devam_eder(self):
        self.client.force_login(self.yon)
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 2))
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT))
        for ad in ("belgeler", "belge_uyarilari", "belge_ekle", "ucret_ekle", "izinler"):
            self.assertEqual(self.client.get(reverse(f"core:{ad}")).status_code, 200, ad)
        b = PersonelBelge.objects.get(personel=self.p)
        self.assertEqual(self.client.get(reverse("core:belge_indir", args=[b.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:belge_duzenle", args=[b.pk])).status_code, 200)
        # yalnız belge yetkisi olan kullanıcı menüsüz de adresten girer
        belgeci = kullanici("skbelgeci", "personel_belgeleri")
        self.client.force_login(belgeci)
        self.assertEqual(self.client.get(reverse("core:belgeler")).status_code, 200)
        self.assertEqual(self.ik_kodlari(belgeci), [])

    def test_kart_acikken_menude_personel_kartlari_aktif(self):
        self.client.force_login(self.yon)
        for url in (self.url, reverse("core:belgeler"), reverse("core:ucret_ekle")):
            r = self.client.get(url)
            self.assertEqual(r.context["aktif_modul_kod"], "IK", url)
            self.assertEqual(r.context["aktif_ekran"], "core:personeller", url)


class ListeBelgeSuzgeciTest(SekmeTaban):
    def setUp(self):
        super().setUp()
        bugun = tr_bugun()
        self.dolmus = personel_kur(ad="dolmuş", soyad="belge")
        self.yakin = personel_kur(ad="yakın", soyad="belge")
        self.gecerli = personel_kur(ad="geçerli", soyad="belge")
        self.sonra_ayrilan = personel_kur(ad="ayrılan", soyad="belge", isten_cikis_tarihi=bugun - timedelta(days=40))
        belge_ekle(self.dolmus, tur="KIMLIK", dosya=yuklenen("a.pdf", PDF_BAYT), bitis_tarihi=bugun - timedelta(days=2))
        belge_ekle(self.yakin, tur="SAGLIK_RAPORU", dosya=yuklenen("b.pdf", PDF_BAYT), bitis_tarihi=bugun + timedelta(days=10))
        belge_ekle(self.gecerli, tur="KIMLIK", dosya=yuklenen("c.pdf", PDF_BAYT), bitis_tarihi=bugun + timedelta(days=200))
        belge_ekle(self.sonra_ayrilan, tur="KIMLIK", dosya=yuklenen("d.pdf", PDF_BAYT), bitis_tarihi=bugun - timedelta(days=5))

    def adlar(self, r):
        return sorted(p.ad for p in r.context["personeller"])

    def test_suzgec_yalniz_uyarili_calisanlari_listeler(self):
        self.client.force_login(self.tam)
        r = self.client.get(reverse("core:personeller"), {"belge": "uyari"})
        self.assertEqual(self.adlar(r), ["DOLMUŞ", "YAKIN"])
        self.assertTrue(r.context["belge_uyari"])
        self.assertContains(r, 'name="belge" value="uyari" checked')
        r = self.client.get(reverse("core:personeller"))
        self.assertGreaterEqual(len(self.adlar(r)), 4)                                        # süzgeç yok: hepsi
        self.assertContains(r, 'name="belge" value="uyari"')

    def test_uyari_rozeti_satirda_ve_karta_baglanir(self):
        self.client.force_login(self.tam)
        r = self.client.get(reverse("core:personeller"))
        rozetli = sorted(p.ad for p in r.context["personeller"] if p.belge_uyari)
        self.assertEqual(rozetli, ["DOLMUŞ", "YAKIN"])
        self.assertContains(r, "⚠ Belge")
        self.assertContains(r, reverse("core:personel_detay", args=[self.dolmus.pk]) + "?sekme=belgeler")
        self.assertNotContains(r, reverse("core:personel_detay", args=[self.gecerli.pk]) + "?sekme=belgeler")

    def test_belge_yetkisi_olmayan_suzgec_ve_rozet_gormez(self):
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:personeller"), {"belge": "uyari"})
        self.assertNotContains(r, "Süresi dolacak belge var")
        self.assertNotContains(r, "⚠ Belge")
        self.assertGreaterEqual(len(self.adlar(r)), 4)                                        # parametre yok sayılır (bilgi sızmaz)

    def test_mantik_belge_uyarilari_ekraniyla_ayni(self):
        from core.services.personel_belge import suresi_dolacaklar
        self.client.force_login(self.tam)
        r = self.client.get(reverse("core:personeller"), {"belge": "uyari", "durum": "hepsi"})
        self.assertEqual({p.pk for p in r.context["personeller"]}, {u.belge.personel_id for u in suresi_dolacaklar()})

    def test_suzgec_bos_sonuc_mesaji_ve_temizle(self):
        self.client.force_login(self.tam)
        PersonelBelge.objects.update(bitis_tarihi=tr_bugun() + timedelta(days=400))
        r = self.client.get(reverse("core:personeller"), {"belge": "uyari"})
        self.assertContains(r, "Bu süzgeçle eşleşen personel yok")
        self.assertContains(r, "Temizle")


class SorguSayisiTest(SekmeTaban):
    """Eski (tek sayfa) ölçümler: yönetici kart 14 / liste 5; yalnız-personel yetkili kart 11 / liste 9; tüm İK yetkili kart 25 / liste 10.
    Sekmeli kart hiçbir sekmede bunları aşmamalı; liste yalnız belge yetkisi olana +1 (süresi dolacak belge sorgusu) yapar."""
    ESKI_KART = 14
    ESKI_LISTE = 5
    ESKI_KART_SADE, ESKI_LISTE_SADE = 11, 9
    ESKI_KART_TAM, ESKI_LISTE_TAM = 25, 10

    def test_kart_her_sekmede_eskisinden_fazla_sorgu_yapmaz(self):
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 7, 1), bitis=date(2026, 7, 2))
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=Decimal("40000"))
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT))
        hesap_olustur(self.p, kullanici_adi="ayse.d", sifre="cokGizli!2026")
        self.client.force_login(self.yon)
        self.client.get(self.url)                                                             # ısınma
        sonuc = {}
        for kod in ("genel", "ucret", "izin", "belgeler", "devam", "mesai"):
            with CaptureQueriesContext(connection) as c:
                self.assertEqual(self.client.get(self.url, {"sekme": kod}).status_code, 200)
            sonuc[kod] = len(c)
        for kod, n in sonuc.items():
            self.assertLessEqual(n, self.ESKI_KART, f"{kod}: {n} sorgu (sonuçlar {sonuc})")

    def test_liste_en_fazla_bir_sorgu_fazla(self):
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT), bitis_tarihi=tr_bugun() - timedelta(days=1))
        self.client.force_login(self.yon)
        self.client.get(reverse("core:personeller"))
        with CaptureQueriesContext(connection) as c:
            self.client.get(reverse("core:personeller"))
        self.assertLessEqual(len(c), self.ESKI_LISTE + 1, len(c))
        self.client.force_login(self.sade)                                                    # belge yetkisi yok → ek sorgu yok
        self.client.get(reverse("core:personeller"))
        with CaptureQueriesContext(connection) as c2:
            self.client.get(reverse("core:personeller"))
        self.assertLessEqual(len(c2), self.ESKI_LISTE_SADE, len(c2))
        self.client.force_login(self.tam)
        self.client.get(reverse("core:personeller"))
        with CaptureQueriesContext(connection) as c3:
            self.client.get(reverse("core:personeller"))
        self.assertLessEqual(len(c3), self.ESKI_LISTE_TAM + 1, len(c3))

    def test_yetkisiz_kullanicida_kart_eskisinden_az_sorgu(self):
        for kisi, sinir in ((self.sade, self.ESKI_KART_SADE), (self.tam, self.ESKI_KART_TAM)):
            self.client.force_login(kisi)
            self.client.get(self.url)
            with CaptureQueriesContext(connection) as c:
                self.client.get(self.url)
            self.assertLessEqual(len(c), sinir, (kisi.username, len(c)))
