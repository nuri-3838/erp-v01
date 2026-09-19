"""İNSAN KAYNAKLARI > Özlük Belgeleri + personel fotoğrafı — servis (dosya doğrulama, resim
küçültme + EXIF yönü, soft delete, süresi dolacaklar mantığı) ve view/yetki testleri.
Dosyalar KVKK hassas: depolama/erişim güvenliği ayrıca test_ik_guvenlik.py'de."""
import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from PIL import Image

from core import gorsel
from core.models import EkranYetki, PersonelBelge
from core.services.personel import PersonelHatasi, personel_sil
from core.services.personel_belge import (
    MAKS_BOYUT, PersonelBelgeHatasi, belge_durumlari, belge_ekle, belge_guncelle, belge_listele,
    belge_sil, durum_haritasi, foto_ayarla, foto_kaldir, suresi_dolacaklar,
)
from core.tests.ik_yardimci import (
    BUGUN, PDF_BAYT, OzelDizinTestTemel, bmp_bayt, jpeg_exif_yon6_bayt, personel_kur, png_bayt,
    yuklenen,
)

BELGE_YOL = re.compile(r"^personel_belge/[0-9a-f]{32}\.(pdf|webp)$")


class DosyaKabulTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()

    def test_pdf_ham_saklanir_uuid_adla(self):
        b = belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("Kimlik Ön Yüz.pdf", PDF_BAYT))
        self.assertRegex(b.dosya.name, BELGE_YOL)
        self.assertTrue(b.dosya.name.endswith(".pdf"))
        self.assertEqual(b.orijinal_ad, "Kimlik Ön Yüz.pdf")
        self.assertNotIn("Kimlik", b.dosya.name)          # özgün ad yalnız kolonda
        self.assertEqual((self.ozel / b.dosya.name).read_bytes(), PDF_BAYT)   # PDF olduğu gibi
        self.assertEqual(self.ozel_dosyalar(), [b.dosya.name])

    def test_resim_webp_e_cevrilir(self):
        b = belge_ekle(self.p, tur="DIPLOMA", dosya=yuklenen("diploma.PNG", png_bayt((60, 40))))
        self.assertRegex(b.dosya.name, BELGE_YOL)
        self.assertTrue(b.dosya.name.endswith(".webp"))
        with Image.open(self.ozel / b.dosya.name) as im:
            self.assertEqual(im.format, "WEBP")
            self.assertEqual(im.size, (60, 40))
        self.assertEqual(b.orijinal_ad, "diploma.PNG")

    def test_buyuk_resim_1600e_kucultulur(self):
        b = belge_ekle(self.p, tur="DIPLOMA", dosya=yuklenen("dev.png", png_bayt((3200, 2000))))
        with Image.open(self.ozel / b.dosya.name) as im:
            self.assertEqual(max(im.size), 1600)

    def test_exif_yonu_duzeltilir(self):
        b = belge_ekle(self.p, tur="DIPLOMA", dosya=yuklenen("telefon.jpg", jpeg_exif_yon6_bayt((200, 100))))
        with Image.open(self.ozel / b.dosya.name) as im:
            self.assertEqual(im.size, (100, 200))        # yan yatık foto dik çevrildi

    def test_kucult_webp_duzelt_yon_varsayilan_kapali(self):
        veri = jpeg_exif_yon6_bayt((200, 100))
        kapali = gorsel.kucult_webp(yuklenen("a.jpg", veri))
        acik = gorsel.kucult_webp(yuklenen("a.jpg", veri), duzelt_yon=True)
        self.assertEqual(Image.open(kapali).size, (200, 100))     # mevcut çağrılar değişmez
        self.assertEqual(Image.open(acik).size, (100, 200))

    def test_uzanti_beyaz_liste(self):
        for ad in ("virus.exe", "sayfa.html", "betik.svg", "uzantisiz", "arsiv.zip"):
            with self.assertRaises(PersonelBelgeHatasi, msg=ad):
                belge_ekle(self.p, tur="DIGER", dosya=yuklenen(ad, PDF_BAYT))
        self.assertEqual(PersonelBelge.objects.count(), 0)
        self.assertEqual(self.ozel_dosyalar(), [])

    def test_sahte_uzanti_reddedilir(self):
        with self.assertRaises(PersonelBelgeHatasi):                 # PNG baytı .pdf adıyla
            belge_ekle(self.p, tur="DIGER", dosya=yuklenen("sahte.pdf", png_bayt()))
        with self.assertRaises(PersonelBelgeHatasi):                 # metin .png adıyla
            belge_ekle(self.p, tur="DIGER", dosya=yuklenen("sahte.png", b"bu bir resim degil"))
        with self.assertRaises(PersonelBelgeHatasi):                 # izinsiz resim biçimi (BMP)
            belge_ekle(self.p, tur="DIGER", dosya=yuklenen("bmp.png", bmp_bayt()))
        self.assertEqual(PersonelBelge.objects.count(), 0)
        self.assertEqual(self.ozel_dosyalar(), [])

    def test_boyut_siniri(self):
        buyuk = yuklenen("buyuk.pdf", b"%PDF-" + b"0" * MAKS_BOYUT)
        with self.assertRaisesMessage(PersonelBelgeHatasi, "çok büyük"):
            belge_ekle(self.p, tur="DIGER", dosya=buyuk)

    def test_gecersiz_tur_ve_bos_dosya(self):
        with self.assertRaises(PersonelBelgeHatasi):
            belge_ekle(self.p, tur="SACMA", dosya=yuklenen("a.pdf", PDF_BAYT))
        with self.assertRaises(PersonelBelgeHatasi):
            belge_ekle(self.p, tur="KIMLIK", dosya=None)

    def test_silinmis_personele_eklenmez(self):
        personel_sil(self.p)
        with self.assertRaises(PersonelBelgeHatasi):
            belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("a.pdf", PDF_BAYT))
        with self.assertRaises(PersonelBelgeHatasi):
            foto_ayarla(self.p, yuklenen("a.png", png_bayt()))


class BelgeYasamDongusuTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()
        self.b = belge_ekle(self.p, tur="SAGLIK_RAPORU", aciklama="  yıllık  ",
                            dosya=yuklenen("rapor.pdf", PDF_BAYT), bitis_tarihi=BUGUN)

    def test_ekle_alanlari(self):
        self.assertEqual(self.b.aciklama, "yıllık")
        self.assertEqual(self.b.bitis_tarihi, BUGUN)
        self.assertEqual(self.b.personel_id, self.p.pk)

    def test_guncelle_dosyaya_dokunmaz(self):
        yol = self.b.dosya.name
        belge_guncelle(self.b, tur="ISG_EGITIMI", aciklama="x", bitis_tarihi=None)
        self.b.refresh_from_db()
        self.assertEqual((self.b.tur, self.b.aciklama, self.b.bitis_tarihi), ("ISG_EGITIMI", "x", None))
        self.assertEqual(self.b.dosya.name, yol)
        self.assertEqual(self.b.orijinal_ad, "rapor.pdf")
        with self.assertRaises(PersonelBelgeHatasi):
            belge_guncelle(self.b, tur="SACMA")

    def test_sil_soft_dosya_diskte_kalir(self):
        belge_sil(self.b)
        self.b.refresh_from_db()
        self.assertTrue(self.b.silindi)
        self.assertTrue((self.ozel / self.b.dosya.name).exists())      # fiziksel silme yok
        self.assertEqual(belge_listele().count(), 0)
        belge_sil(self.b)                                              # idempotent
        with self.assertRaises(PersonelBelgeHatasi):
            belge_guncelle(self.b, tur="KIMLIK")

    def test_personel_sil_korumasi(self):
        with self.assertRaisesMessage(PersonelHatasi, "özlük belgesi var"):
            personel_sil(self.p)
        belge_sil(self.b)
        personel_sil(self.p)                                           # belge silinince serbest
        self.p.refresh_from_db()
        self.assertTrue(self.p.silindi)

    def test_liste_filtreleri_ve_silinmis_personel(self):
        diger = personel_kur(ad="veli")
        belge_ekle(diger, tur="EHLIYET", dosya=yuklenen("e.pdf", PDF_BAYT))
        self.assertEqual(belge_listele().count(), 2)
        self.assertEqual(belge_listele(personel_id=diger.pk).count(), 1)
        self.assertEqual(belge_listele(tur="SAGLIK_RAPORU").count(), 1)
        belge_sil(diger.belgeler.get())
        personel_sil(diger)
        self.assertEqual(belge_listele().count(), 1)


class FotoTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()

    def test_foto_ayarla_ve_degistir(self):
        foto_ayarla(self.p, yuklenen("ben.jpg", png_bayt((90, 90))))
        self.p.refresh_from_db()
        ilk = self.p.foto.name
        self.assertRegex(ilk, r"^personel_foto/[0-9a-f]{32}\.webp$")
        self.assertTrue((self.ozel / ilk).is_file())
        foto_ayarla(self.p, yuklenen("yeni.png", png_bayt((50, 50))))
        self.p.refresh_from_db()
        self.assertNotEqual(self.p.foto.name, ilk)
        self.assertTrue((self.ozel / ilk).is_file())                   # eski dosya diskte kalır

    def test_foto_pdf_reddedilir(self):
        with self.assertRaises(PersonelBelgeHatasi):
            foto_ayarla(self.p, yuklenen("foto.pdf", PDF_BAYT))
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)

    def test_foto_kaldir(self):
        foto_ayarla(self.p, yuklenen("ben.png", png_bayt()))
        foto_kaldir(self.p)
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)
        foto_kaldir(self.p)                                            # foto yokken hata vermez

    def test_foto_yonu_duzeltilir(self):
        foto_ayarla(self.p, yuklenen("telefon.jpg", jpeg_exif_yon6_bayt((200, 100))))
        self.p.refresh_from_db()
        with Image.open(self.ozel / self.p.foto.name) as im:
            self.assertEqual(im.size, (100, 200))


class SuresiDolacaklarTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()

    def _belge(self, tur="SAGLIK_RAPORU", gun=None, personel=None):
        """`gun`: bugüne göre bitiş farkı (None = süresiz)."""
        return belge_ekle(
            personel or self.p, tur=tur, dosya=yuklenen("b.pdf", PDF_BAYT),
            bitis_tarihi=None if gun is None else BUGUN + timedelta(days=gun))

    def _uyarilar(self, **kw):
        return suresi_dolacaklar(bugun=BUGUN, **kw)

    def test_sinir_dahil_bir_sonraki_gun_haric(self):
        icinde = self._belge(tur="SAGLIK_RAPORU", gun=30)
        self._belge(tur="ISG_EGITIMI", gun=31)
        uyarilar = self._uyarilar(gun=30)
        self.assertEqual([u.belge.pk for u in uyarilar], [icinde.pk])
        self.assertEqual(uyarilar[0].kalan_gun, 30)
        self.assertFalse(uyarilar[0].doldu)

    def test_bugun_biten_dolmus_sayilmaz_dun_biten_dolmus(self):
        bugun_biten = self._belge(tur="SAGLIK_RAPORU", gun=0)
        dun_biten = self._belge(tur="ISG_EGITIMI", gun=-1)
        u = {x.belge.pk: x for x in self._uyarilar()}
        self.assertFalse(u[bugun_biten.pk].doldu)
        self.assertTrue(u[dun_biten.pk].doldu)
        self.assertEqual(u[dun_biten.pk].kalan_gun, -1)

    def test_suresi_dolmus_her_zaman_listelenir(self):
        eski = self._belge(gun=-400)
        self.assertEqual([x.belge.pk for x in self._uyarilar(gun=15)], [eski.pk])

    def test_sirali_bitise_gore(self):
        b3 = self._belge(tur="EHLIYET", gun=20)
        b1 = self._belge(tur="SAGLIK_RAPORU", gun=-5)
        b2 = self._belge(tur="ISG_EGITIMI", gun=3)
        self.assertEqual([x.belge.pk for x in self._uyarilar()], [b1.pk, b2.pk, b3.pk])

    def test_yenileme_eskiyi_susturur_silinince_geri_gelir(self):
        eski = self._belge(gun=-10)
        yeni = self._belge(gun=365)
        self.assertEqual(self._uyarilar(), [])
        belge_sil(yeni)
        self.assertEqual([x.belge.pk for x in self._uyarilar()], [eski.pk])

    def test_gec_yuklenen_eski_belge_grubu_bozmaz(self):
        self._belge(gun=400)                       # önce yüklenen, geç bitişli
        self._belge(gun=-30)                       # sonradan yüklenen ama daha eski tarihli
        self.assertEqual(self._uyarilar(), [])

    def test_suresiz_kayit_grubu_susturur(self):
        self._belge(gun=-10)
        self._belge(gun=None)
        self.assertEqual(self._uyarilar(), [])
        self._belge(gun=-20)                       # sonradan eski bir kayıt gelse de grup süresiz kalır
        self.assertEqual(self._uyarilar(), [])

    def test_turler_bagimsiz_gruplar(self):
        rapor = self._belge(tur="SAGLIK_RAPORU", gun=5)
        self._belge(tur="ISG_EGITIMI", gun=500)    # başka türün geçerli belgesi raporu susturmaz
        self.assertEqual([x.belge.pk for x in self._uyarilar()], [rapor.pk])

    def test_ayrilan_personel_uyari_vermez(self):
        ayrilan = personel_kur(ad="ayrilan", isten_cikis_tarihi=BUGUN - timedelta(days=1))
        self._belge(personel=ayrilan, gun=-10)
        gidecek = personel_kur(ad="gidecek", isten_cikis_tarihi=BUGUN + timedelta(days=10))
        b = self._belge(personel=gidecek, gun=-10)          # çıkışı ileri tarihli => hâlâ çalışıyor
        self.assertEqual([x.belge.pk for x in self._uyarilar()], [b.pk])

    def test_silinmis_belge_ve_silinmis_personel_yok(self):
        b = self._belge(gun=-10)
        belge_sil(b)
        kalkan = personel_kur(ad="kalkan")
        b2 = self._belge(personel=kalkan, gun=-10)
        belge_sil(b2)
        personel_sil(kalkan)
        self.assertEqual(self._uyarilar(), [])

    def test_varsayilan_bugun_tr_gunudur(self):
        # bugun verilmezse tr_bugun() kullanılır (çökmemeli)
        self.assertEqual(suresi_dolacaklar(), [])


class DurumHaritasiTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()

    def _b(self, tur, gun):
        return belge_ekle(self.p, tur=tur, dosya=yuklenen("b.pdf", PDF_BAYT),
                          bitis_tarihi=None if gun is None else BUGUN + timedelta(days=gun))

    def test_durumlar(self):
        suresiz = self._b("KIMLIK", None)
        gecerli = self._b("SAGLIK_RAPORU", 200)
        yaklasan = self._b("ISG_EGITIMI", 10)
        dolmus = self._b("EHLIYET", -3)
        h = durum_haritasi([suresiz, gecerli, yaklasan, dolmus], bugun=BUGUN)
        self.assertEqual(h[suresiz.pk], ("SURESIZ", None))
        self.assertEqual(h[gecerli.pk], ("GECERLI", 200))
        self.assertEqual(h[yaklasan.pk], ("YAKLASIYOR", 10))
        self.assertEqual(h[dolmus.pk], ("SURESI_DOLDU", -3))

    def test_yenilenmis_eski_belge(self):
        eski = self._b("SAGLIK_RAPORU", -20)
        yeni = self._b("SAGLIK_RAPORU", 300)
        h = durum_haritasi([eski, yeni], bugun=BUGUN)
        self.assertEqual(h[eski.pk][0], "ESKI")
        self.assertEqual(h[yeni.pk][0], "GECERLI")

    def test_belge_durumlari_kart_icin_yeni_once(self):
        eski = self._b("SAGLIK_RAPORU", -20)
        yeni = self._b("SAGLIK_RAPORU", 300)
        satirlar = belge_durumlari(self.p, bugun=BUGUN)
        self.assertEqual([s[0].pk for s in satirlar], [yeni.pk, eski.pk])
        self.assertEqual([s[1] for s in satirlar], ["GECERLI", "ESKI"])


class BelgeViewTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("bgyon", password="x")
        self.belgeci = User.objects.create_user("bgbelgeci", password="x")
        EkranYetki.objects.create(kullanici=self.belgeci, ekran_kod="personel_belgeleri")
        self.personelci = User.objects.create_user("bgpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.ikisi = User.objects.create_user("bgikisi", password="x")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel_belgeleri")
        self.bos = User.objects.create_user("bgbos", password="x")
        self.p = personel_kur(ad="ahmet")

    def _post_ekle(self, **ek):
        veri = {"personel": self.p.pk, "tur": "KIMLIK", "aciklama": "", "bitis_tarihi": "",
                "dosya": yuklenen("kimlik.pdf", PDF_BAYT)}
        veri.update(ek)
        return self.client.post(reverse("core:belge_ekle"), veri)

    def test_yetki_302_403(self):
        b = belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT))
        urller = [reverse("core:belgeler"), reverse("core:belge_uyarilari"),
                  reverse("core:belge_ekle"), reverse("core:belge_duzenle", args=[b.pk]),
                  reverse("core:belge_sil", args=[b.pk]), reverse("core:belge_indir", args=[b.pk])]
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 302, u)
        for kullanici in (self.bos, self.personelci):
            self.client.force_login(kullanici)
            for u in urller:
                self.assertEqual(self.client.get(u).status_code, 403, (kullanici.username, u))
            self.assertEqual(self.client.post(reverse("core:belge_sil", args=[b.pk])).status_code, 403)
        b.refresh_from_db()
        self.assertFalse(b.silindi)

    def test_ekle_post_kaydeder_ve_listeye_doner(self):
        self.client.force_login(self.belgeci)
        r = self._post_ekle(aciklama="ön yüz", bitis_tarihi="2030-01-31")
        self.assertRedirects(r, reverse("core:belgeler"))
        b = PersonelBelge.objects.get()
        self.assertEqual((b.personel_id, b.tur, b.aciklama, b.orijinal_ad),
                         (self.p.pk, "KIMLIK", "ön yüz", "kimlik.pdf"))
        self.assertEqual(b.created_by, self.belgeci)
        self.assertRegex(b.dosya.name, BELGE_YOL)

    def test_ekle_kartindan_gelindiyse_karta_doner_yetkiye_gore(self):
        self.client.force_login(self.ikisi)
        r = self.client.post(reverse("core:belge_ekle"), {
            "personel": self.p.pk, "tur": "KIMLIK", "sonraki": "personel",
            "dosya": yuklenen("k.pdf", PDF_BAYT)})
        self.assertRedirects(r, reverse("core:personel_detay", args=[self.p.pk]))
        self.client.force_login(self.belgeci)                    # personel ekranı yetkisi yok
        r2 = self.client.post(reverse("core:belge_ekle"), {
            "personel": self.p.pk, "tur": "KIMLIK", "sonraki": "personel",
            "dosya": yuklenen("k.pdf", PDF_BAYT)})
        self.assertRedirects(r2, reverse("core:belgeler"))

    def test_ekle_gecersiz_dosya_hata_gosterir_kayit_olusmaz(self):
        self.client.force_login(self.belgeci)
        r = self._post_ekle(dosya=yuklenen("virus.exe", b"MZ..."))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Desteklenmeyen dosya türü")
        r2 = self._post_ekle(dosya=yuklenen("sahte.pdf", png_bayt()))
        self.assertContains(r2, "Geçersiz PDF")
        r3 = self.client.post(reverse("core:belge_ekle"), {"personel": self.p.pk, "tur": "KIMLIK"})
        self.assertEqual(r3.status_code, 200)                    # dosyasız gönderim
        self.assertEqual(PersonelBelge.objects.count(), 0)

    def test_ekle_formu_personel_secili_gelir_ve_multipart(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:belge_ekle"), {"personel": self.p.pk})
        self.assertContains(r, 'enctype="multipart/form-data"')
        self.assertContains(r, 'accept="image/*,application/pdf"')
        self.assertEqual(r.context["form"].initial["personel"], self.p.pk)

    def test_duzenle_dosya_alani_yok_ve_gunceller(self):
        b = belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT))
        yol = b.dosya.name
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:belge_duzenle", args=[b.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("dosya", r.context["form"].fields)
        r2 = self.client.post(reverse("core:belge_duzenle", args=[b.pk]), {
            "tur": "EHLIYET", "aciklama": "B sınıfı", "bitis_tarihi": "2031-05-01"})
        self.assertRedirects(r2, reverse("core:belgeler"))
        b.refresh_from_db()
        self.assertEqual((b.tur, b.aciklama, str(b.bitis_tarihi)), ("EHLIYET", "B sınıfı", "2031-05-01"))
        self.assertEqual(b.dosya.name, yol)

    def test_sil_yalniz_post(self):
        b = belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("k.pdf", PDF_BAYT))
        self.client.force_login(self.belgeci)
        self.client.get(reverse("core:belge_sil", args=[b.pk]))
        b.refresh_from_db()
        self.assertFalse(b.silindi)
        self.client.post(reverse("core:belge_sil", args=[b.pk]))
        b.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertEqual(self.client.get(reverse("core:belge_duzenle", args=[b.pk])).status_code, 404)

    def test_liste_ve_filtreler(self):
        diger = personel_kur(ad="berk")
        belge_ekle(self.p, tur="KIMLIK", aciklama="AHMET-KIMLIK", dosya=yuklenen("a.pdf", PDF_BAYT))
        belge_ekle(diger, tur="EHLIYET", aciklama="BERK-EHLIYET", dosya=yuklenen("b.pdf", PDF_BAYT))
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:belgeler"))
        self.assertContains(r, "AHMET-KIMLIK")
        self.assertContains(r, "BERK-EHLIYET")
        self.assertEqual(len(self.client.get(reverse("core:belgeler"), {"personel": self.p.pk})
                             .context["satirlar"]), 1)
        self.assertEqual(len(self.client.get(reverse("core:belgeler"), {"tur": "EHLIYET"})
                             .context["satirlar"]), 1)
        r2 = self.client.get(reverse("core:belgeler"), {"tur": "SAÇMA", "personel": "x"})
        self.assertEqual(r2.status_code, 200)                    # geçersiz parametreler yok sayılır
        self.assertEqual(len(r2.context["satirlar"]), 2)

    def test_liste_personel_linki_yalniz_personel_yetkilisine(self):
        belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("a.pdf", PDF_BAYT))
        detay = reverse("core:personel_detay", args=[self.p.pk])
        self.client.force_login(self.belgeci)
        self.assertNotContains(self.client.get(reverse("core:belgeler")), detay)
        self.client.force_login(self.ikisi)
        self.assertContains(self.client.get(reverse("core:belgeler")), detay)

    def test_uyarilar_gun_secenekleri(self):
        from core.tarih import tr_bugun
        bugun = tr_bugun()
        belge_ekle(self.p, tur="SAGLIK_RAPORU", dosya=yuklenen("a.pdf", PDF_BAYT),
                   bitis_tarihi=bugun + timedelta(days=45))
        belge_ekle(self.p, tur="ISG_EGITIMI", dosya=yuklenen("b.pdf", PDF_BAYT),
                   bitis_tarihi=bugun - timedelta(days=2))
        self.client.force_login(self.belgeci)
        r30 = self.client.get(reverse("core:belge_uyarilari"))
        self.assertEqual(r30.context["gun"], 30)
        self.assertEqual(len(r30.context["uyarilar"]), 1)         # yalnız süresi dolmuş
        r60 = self.client.get(reverse("core:belge_uyarilari"), {"gun": "60"})
        self.assertEqual(len(r60.context["uyarilar"]), 2)
        self.assertContains(r60, "2 gün önce doldu")
        r_kotu = self.client.get(reverse("core:belge_uyarilari"), {"gun": "7"})
        self.assertEqual(r_kotu.context["gun"], 30)               # izinli olmayan değer -> 30
        r_kotu2 = self.client.get(reverse("core:belge_uyarilari"), {"gun": "abc"})
        self.assertEqual(r_kotu2.context["gun"], 30)

    def test_kartta_belge_bolumu_yalniz_belge_yetkilisine(self):
        belge_ekle(self.p, tur="KIMLIK", aciklama="ON-YUZ", dosya=yuklenen("k.pdf", PDF_BAYT))
        url = reverse("core:personel_detay", args=[self.p.pk])
        self.client.force_login(self.personelci)                  # yalnız personel yetkisi
        r = self.client.get(url)
        self.assertNotContains(r, "Özlük Belgeleri")
        self.assertNotContains(r, "ON-YUZ")
        self.client.force_login(self.ikisi)
        r2 = self.client.get(url)
        self.assertContains(r2, "Özlük Belgeleri")
        self.assertContains(r2, "ON-YUZ")


class FotoViewTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.personelci = User.objects.create_user("ftpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.belgeci = User.objects.create_user("ftbelgeci", password="x")
        EkranYetki.objects.create(kullanici=self.belgeci, ekran_kod="personel_belgeleri")
        self.p = personel_kur()

    def test_yukle_kaldir_akisi(self):
        self.client.force_login(self.personelci)
        detay = reverse("core:personel_detay", args=[self.p.pk])
        r = self.client.post(reverse("core:personel_foto_yukle", args=[self.p.pk]),
                             {"dosya": yuklenen("ben.png", png_bayt())})
        self.assertRedirects(r, detay)
        self.p.refresh_from_db()
        self.assertRegex(self.p.foto.name, r"^personel_foto/[0-9a-f]{32}\.webp$")
        self.assertContains(self.client.get(detay), reverse("core:personel_foto", args=[self.p.pk]))
        self.client.post(reverse("core:personel_foto_kaldir", args=[self.p.pk]))
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)

    def test_yukle_get_islem_yapmaz_gecersiz_dosya_mesaj(self):
        self.client.force_login(self.personelci)
        self.client.get(reverse("core:personel_foto_yukle", args=[self.p.pk]))
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)
        r = self.client.post(reverse("core:personel_foto_yukle", args=[self.p.pk]),
                             {"dosya": yuklenen("foto.pdf", PDF_BAYT)}, follow=True)
        self.assertContains(r, "Desteklenmeyen dosya türü")
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)
        r2 = self.client.post(reverse("core:personel_foto_yukle", args=[self.p.pk]), follow=True)
        self.assertContains(r2, "Fotoğraf seçilmedi")

    def test_belge_yetkisi_foto_yuklemeye_yetmez(self):
        self.client.force_login(self.belgeci)
        r = self.client.post(reverse("core:personel_foto_yukle", args=[self.p.pk]),
                             {"dosya": yuklenen("ben.png", png_bayt())})
        self.assertEqual(r.status_code, 403)
        self.p.refresh_from_db()
        self.assertFalse(self.p.foto)
