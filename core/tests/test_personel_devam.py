"""İNSAN KAYNAKLARI > Devam / Yoklama — durum türetme (izinden), günlük ızgara + kaydetme (idempotent,
soft delete, izinli/uygunsuz/gelecek), DB kısıtı, aylık özet (Pazar/izin/giriş-çıkış/bugün sınırları),
personel silme koruması + view/yetki. Saat/mesai/ücret hesabı YOK."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import EkranYetki, Personel, PersonelDevam
from core.services.personel import PersonelHatasi, personel_sil
from core.services.personel_devam import (
    PersonelDevamHatasi, aylik_ozet, aylik_toplam, gun_ozeti, gun_satirlari, gun_uygun_personel,
    gunluk_kaydet, turet_durum,
)
from core.services.personel_izin import izin_ekle, izin_sil
from core.tests.ik_yardimci import personel_kur

BUGUN = date(2026, 9, 19)             # Cumartesi; 2026-09-14 Pazartesi, 09-20 Pazar
GUN = date(2026, 9, 14)               # Pazartesi
AY_SONU = date(2026, 9, 30)           # Eylül 2026'da 4 Pazar: 6, 13, 20, 27


def _kayit(p, tarih, durum="GELDI", notlar=""):
    return PersonelDevam.objects.create(personel=p, tarih=tarih, durum=durum, notlar=notlar)


class BugunSabitTemel(TestCase):
    """Servisin 'bugün'ünü BUGUN'e sabitler (gelecek-tarih kontrolü gerçek saate bağlı olmasın)."""

    def setUp(self):
        super().setUp()
        yama = mock.patch("core.services.personel_devam.tr_bugun", return_value=BUGUN)
        yama.start()
        self.addCleanup(yama.stop)


class TuretDurumTest(SimpleTestCase):
    def test_izin_kayittan_once_gelir(self):
        self.assertEqual(turet_durum("GELDI", "YILLIK"), "IZINLI")
        self.assertEqual(turet_durum("", "MAZERET"), "IZINLI")
        self.assertEqual(turet_durum("GELMEDI", "UCRETSIZ"), "IZINLI")
        self.assertEqual(turet_durum("GELDI", "RAPOR"), "RAPORLU")

    def test_izin_yoksa_kayit_o_da_yoksa_bos(self):
        self.assertEqual(turet_durum("YARIM_GUN", None), "YARIM_GUN")
        self.assertEqual(turet_durum("GELDI", None), "GELDI")
        self.assertEqual(turet_durum("", None), "")
        self.assertEqual(turet_durum(None, None), "")


class UygunPersonelTest(TestCase):
    def test_giris_ve_cikis_sinirlari_dahil(self):
        p = personel_kur(ad="ali", ise_giris_tarihi=date(2026, 9, 10), isten_cikis_tarihi=date(2026, 9, 20))
        self.assertNotIn(p, gun_uygun_personel(date(2026, 9, 9)))      # girişten önce yok
        self.assertIn(p, gun_uygun_personel(date(2026, 9, 10)))        # giriş günü dahil
        self.assertIn(p, gun_uygun_personel(date(2026, 9, 20)))        # çıkış günü dahil
        self.assertNotIn(p, gun_uygun_personel(date(2026, 9, 21)))     # çıkıştan sonra yok

    def test_silinmis_kart_yok_cikissiz_hep_var(self):
        a = personel_kur(ad="ali")
        b = personel_kur(ad="veli")
        personel_sil(b)
        uygun = list(gun_uygun_personel(GUN))
        self.assertIn(a, uygun)
        self.assertNotIn(b, uygun)


class GunSatirlariTest(TestCase):
    def test_satirlar_kayit_ve_izin(self):
        a, b, c = personel_kur(ad="ali"), personel_kur(ad="berk"), personel_kur(ad="can")
        _kayit(a, GUN, "YARIM_GUN", "doktor")
        izin_ekle(b, tur="YILLIK", baslangic=GUN, bitis=GUN)
        satirlar = {s.personel.pk: s for s in gun_satirlari(GUN)}
        self.assertEqual((satirlar[a.pk].durum, satirlar[a.pk].notlar, satirlar[a.pk].kilitli),
                         ("YARIM_GUN", "doktor", False))
        self.assertEqual((satirlar[b.pk].durum, satirlar[b.pk].kilitli), ("IZINLI", True))
        self.assertEqual((satirlar[c.pk].durum, satirlar[c.pk].kilitli), ("", False))

    def test_rapor_izinden_onceliklidir(self):
        # Aynı güne değen iki izin: eski çakışma denetimini aşmak için doğrudan kayıt
        from core.models import PersonelIzin
        p = personel_kur()
        for tur in ("YILLIK", "RAPOR"):
            PersonelIzin.objects.create(personel=p, tur=tur, baslangic=GUN, bitis=GUN, gun=1)
        (s,) = gun_satirlari(GUN)
        self.assertEqual(s.durum, "RAPORLU")

    def test_izin_kayittan_gorunur_silinince_kayit_geri_gelir(self):
        p = personel_kur()
        _kayit(p, GUN, "GELDI")
        izin = izin_ekle(p, tur="YILLIK", baslangic=GUN, bitis=GUN)
        (s,) = gun_satirlari(GUN)
        self.assertEqual((s.durum, s.kilitli), ("IZINLI", True))          # izin yoklamayı gölgeler
        izin_sil(izin)
        (s,) = gun_satirlari(GUN)
        self.assertEqual((s.durum, s.kilitli), ("GELDI", False))          # izin silinince kayıt geri görünür

    def test_silinmis_kayit_ve_baska_gun_gorunmez(self):
        p = personel_kur()
        k = _kayit(p, GUN)
        _kayit(p, GUN + timedelta(days=1), "GELMEDI")
        k.silindi = True
        k.save()
        (s,) = gun_satirlari(GUN)
        self.assertEqual(s.durum, "")

    def test_gun_ozeti_sayilari(self):
        a, b, c, d = (personel_kur(ad=x) for x in ("ali", "berk", "can", "deniz"))
        _kayit(a, GUN, "GELDI")
        _kayit(b, GUN, "GELMEDI")
        izin_ekle(c, tur="RAPOR", baslangic=GUN, bitis=GUN)
        self.assertEqual(gun_ozeti(gun_satirlari(GUN)), {
            "geldi": 1, "yarim": 0, "gelmedi": 1, "izinli": 0, "raporlu": 1, "girilmemis": 1})

    def test_sorgu_sayisi_sabit(self):
        for ad in ("ali", "berk", "can", "deniz", "emre"):
            personel_kur(ad=ad)
        with self.assertNumQueries(3):
            gun_satirlari(GUN)


class GunlukKaydetTest(BugunSabitTemel):
    def setUp(self):
        super().setUp()
        self.a = personel_kur(ad="ali")
        self.b = personel_kur(ad="berk")
        self.kul = User.objects.create_user("yoklamaci", password="x")

    def _kaydet(self, satirlar, tarih=GUN):
        return gunluk_kaydet(tarih, satirlar, kullanici=self.kul)

    def test_yeni_kayit_ve_audit(self):
        self.assertEqual(self._kaydet({self.a.pk: ("GELDI", ""), self.b.pk: ("GELMEDI", "  raporsuz  ")}),
                         (2, 0, 0))
        k = PersonelDevam.objects.get(personel=self.b, tarih=GUN)
        self.assertEqual((k.durum, k.notlar, k.created_by, k.updated_by),
                         ("GELMEDI", "raporsuz", self.kul, self.kul))

    def test_iki_kez_kaydetmek_tek_satir_idempotent(self):
        veri = {self.a.pk: ("GELDI", "not")}
        self.assertEqual(self._kaydet(veri), (1, 0, 0))
        self.assertEqual(self._kaydet(veri), (0, 0, 0))                    # değişiklik yok
        self.assertEqual(PersonelDevam.objects.filter(personel=self.a, silindi=False).count(), 1)

    def test_guncelleme_ayni_satiri_degistirir(self):
        self._kaydet({self.a.pk: ("GELDI", "")})
        ilk = PersonelDevam.objects.get(personel=self.a)
        self.assertEqual(self._kaydet({self.a.pk: ("YARIM_GUN", "öğleden sonra izin")}), (1, 0, 0))
        ilk.refresh_from_db()
        self.assertEqual((ilk.durum, ilk.notlar, ilk.silindi), ("YARIM_GUN", "öğleden sonra izin", False))
        self.assertEqual(PersonelDevam.objects.filter(personel=self.a).count(), 1)

    def test_bos_durum_mevcut_kaydi_soft_siler(self):
        self._kaydet({self.a.pk: ("GELDI", "x")})
        self.assertEqual(self._kaydet({self.a.pk: ("", "yok sayılır")}), (0, 1, 0))
        k = PersonelDevam.objects.get(personel=self.a)
        self.assertTrue(k.silindi)
        self.assertIsNotNone(k.silindi_at)
        self.assertEqual(self._kaydet({self.a.pk: ("", "")}), (0, 0, 0))   # kayıt yokken boş → işlem yok
        self.assertEqual(PersonelDevam.objects.filter(personel=self.a, silindi=False).count(), 0)

    def test_soft_delete_sonrasi_ayni_gun_yeniden_eklenebilir(self):
        self._kaydet({self.a.pk: ("GELDI", "")})
        self._kaydet({self.a.pk: ("", "")})
        self.assertEqual(self._kaydet({self.a.pk: ("GELMEDI", "")}), (1, 0, 0))
        self.assertEqual(PersonelDevam.objects.filter(personel=self.a).count(), 2)        # biri silinmiş
        self.assertEqual(PersonelDevam.objects.get(personel=self.a, silindi=False).durum, "GELMEDI")

    def test_izinli_personel_kaydedilmez_atlanir(self):
        izin_ekle(self.a, tur="YILLIK", baslangic=GUN, bitis=GUN)
        self.assertEqual(self._kaydet({self.a.pk: ("GELDI", ""), self.b.pk: ("GELDI", "")}), (1, 0, 1))
        self.assertFalse(PersonelDevam.objects.filter(personel=self.a).exists())

    def test_uygun_olmayan_personel_atlanir(self):
        gelecek_giris = personel_kur(ad="yeni", ise_giris_tarihi=GUN + timedelta(days=1))
        ayrilan = personel_kur(ad="eski", isten_cikis_tarihi=GUN - timedelta(days=1))
        silinen = personel_kur(ad="silik")
        personel_sil(silinen)
        sonuc = self._kaydet({gelecek_giris.pk: ("GELDI", ""), ayrilan.pk: ("GELDI", ""),
                              silinen.pk: ("GELDI", ""), 999999: ("GELDI", "")})
        self.assertEqual(sonuc, (0, 0, 4))
        self.assertEqual(PersonelDevam.objects.count(), 0)

    def test_gelecek_tarih_reddedilir(self):
        with self.assertRaisesMessage(PersonelDevamHatasi, "Gelecek tarih"):
            self._kaydet({self.a.pk: ("GELDI", "")}, tarih=BUGUN + timedelta(days=1))
        self.assertEqual(PersonelDevam.objects.count(), 0)
        self.assertEqual(self._kaydet({self.a.pk: ("GELDI", "")}, tarih=BUGUN), (1, 0, 0))    # bugün serbest

    def test_gecersiz_durum_ve_uzun_not_hicbir_sey_yazmaz(self):
        with self.assertRaisesMessage(PersonelDevamHatasi, "Geçersiz devam durumu"):
            self._kaydet({self.a.pk: ("GELDI", ""), self.b.pk: ("IZINLI", "")})       # IZINLI elle girilemez
        with self.assertRaisesMessage(PersonelDevamHatasi, "Not en fazla 200"):
            self._kaydet({self.a.pk: ("GELDI", "x" * 201)})
        self.assertEqual(PersonelDevam.objects.count(), 0)

    def test_pazar_serbest(self):
        pazar = date(2026, 9, 13)
        self.assertEqual(self._kaydet({self.a.pk: ("GELDI", "fazla mesai")}, tarih=pazar), (1, 0, 0))

    def test_gonderilmeyen_personele_dokunulmaz(self):
        self._kaydet({self.a.pk: ("GELDI", ""), self.b.pk: ("GELMEDI", "")})
        self._kaydet({self.a.pk: ("YARIM_GUN", "")})                       # b gönderilmedi
        self.assertEqual(PersonelDevam.objects.get(personel=self.b, silindi=False).durum, "GELMEDI")

    def test_tek_transaction_hata_geri_alir(self):
        gercek = PersonelDevam.objects.create
        cagri = {"n": 0}

        def ikinci_cagrida_patla(**kw):
            cagri["n"] += 1
            if cagri["n"] == 2:
                raise RuntimeError("boom")
            return gercek(**kw)

        with mock.patch.object(PersonelDevam.objects, "create", side_effect=ikinci_cagrida_patla):
            with self.assertRaises(RuntimeError):
                self._kaydet({self.a.pk: ("GELDI", ""), self.b.pk: ("GELDI", "")})
        self.assertEqual(cagri["n"], 2)
        self.assertEqual(PersonelDevam.objects.count(), 0)          # ilk kayıt da geri alındı


class DevamDBKisitTest(TestCase):
    def test_ayni_gunde_iki_aktif_kayit_olmaz(self):
        p = personel_kur()
        _kayit(p, GUN)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _kayit(p, GUN, "GELMEDI")

    def test_silinmis_kayit_engellemez_baska_gun_ve_baska_kisi_serbest(self):
        p, q = personel_kur(ad="ali"), personel_kur(ad="veli")
        k = _kayit(p, GUN)
        k.silindi = True
        k.save()
        _kayit(p, GUN, "GELMEDI")                        # silinmişin yerine
        _kayit(p, GUN + timedelta(days=1))
        _kayit(q, GUN)
        self.assertEqual(PersonelDevam.objects.count(), 4)

    def test_gecersiz_durum_secenegi_model_dogrulamasinda_yakalanir(self):
        from django.core.exceptions import ValidationError
        k = PersonelDevam(personel=personel_kur(), tarih=GUN, durum="IZINLI")
        with self.assertRaises(ValidationError):
            k.full_clean()


class PersonelSilKorumasiTest(TestCase):
    def test_devam_kaydi_olan_personel_silinemez(self):
        p = personel_kur()
        k = _kayit(p, GUN)
        with self.assertRaisesMessage(PersonelHatasi, "devam/yoklama kaydı var"):
            personel_sil(p)
        k.silindi = True
        k.save()
        personel_sil(p)                                   # kayıt silinince serbest
        p.refresh_from_db()
        self.assertTrue(p.silindi)


class AylikOzetTest(TestCase):
    def _ozet(self, **kw):
        veri = {"bugun": AY_SONU}
        veri.update(kw)
        return aylik_ozet(2026, 9, **veri)

    def _tek(self, **kw):
        (s,) = self._ozet(**kw)
        return s

    def _sayilar(self, s):
        return (s.geldi, s.yarim, s.gelmedi, s.izinli, s.raporlu, s.girilmemis)

    def test_kayitsiz_ay_pazarlar_sayilmaz(self):
        personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        self.assertEqual(self._sayilar(self._tek()), (0, 0, 0, 0, 0, 26))       # 30 gün - 4 Pazar

    def test_durum_sayilari_ve_pazar_kaydi_sayilir(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        _kayit(p, date(2026, 9, 1), "GELDI")
        _kayit(p, date(2026, 9, 2), "GELDI")
        _kayit(p, date(2026, 9, 3), "YARIM_GUN")
        _kayit(p, date(2026, 9, 4), "GELMEDI")
        _kayit(p, date(2026, 9, 6), "GELDI")                                    # Pazar, kayıt var → sayılır
        self.assertEqual(self._sayilar(self._tek()), (3, 1, 1, 0, 0, 22))

    def test_izin_ve_rapor_gunleri_pazar_haric(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 9, 7), bitis=date(2026, 9, 9))     # Pzt-Çar
        izin_ekle(p, tur="RAPOR", baslangic=date(2026, 9, 10), bitis=date(2026, 9, 11))
        izin_ekle(p, tur="MAZERET", baslangic=date(2026, 9, 12), bitis=date(2026, 9, 14))  # Cmt,(Paz),Pzt
        self.assertEqual(self._sayilar(self._tek()), (0, 0, 0, 3 + 2, 2, 26 - 3 - 2 - 2))

    def test_izin_ayni_gundeki_yoklamayi_golgeler_silinince_geri_gelir(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        _kayit(p, date(2026, 9, 8), "GELDI")
        izin = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 9, 7), bitis=date(2026, 9, 9))
        self.assertEqual(self._sayilar(self._tek()), (0, 0, 0, 3, 0, 23))
        izin_sil(izin)
        self.assertEqual(self._sayilar(self._tek()), (1, 0, 0, 0, 0, 25))

    def test_ay_ortasinda_giren_yalniz_giristen_sayilir(self):
        personel_kur(ise_giris_tarihi=date(2026, 9, 15))
        self.assertEqual(self._sayilar(self._tek()), (0, 0, 0, 0, 0, 16 - 2))   # 15-30: Pazarlar 20, 27

    def test_ay_ortasinda_ayrilan_cikis_gunune_kadar_sayilir(self):
        personel_kur(ise_giris_tarihi=date(2020, 1, 1), isten_cikis_tarihi=date(2026, 9, 10))
        self.assertEqual(self._sayilar(self._tek()), (0, 0, 0, 0, 0, 10 - 1))   # 1-10: Pazar 6

    def test_ay_basindan_once_ayrilan_ve_sonra_girecek_listelenmez(self):
        personel_kur(ad="eski", ise_giris_tarihi=date(2020, 1, 1), isten_cikis_tarihi=date(2026, 8, 31))
        personel_kur(ad="yeni", ise_giris_tarihi=date(2026, 10, 1))
        self.assertEqual(self._ozet(), [])

    def test_bugunden_sonrasi_sayilmaz(self):
        personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        self.assertEqual(self._sayilar(self._tek(bugun=date(2026, 9, 10))), (0, 0, 0, 0, 0, 10 - 1))
        self.assertEqual(self._sayilar(self._tek(bugun=date(2026, 9, 1))), (0, 0, 0, 0, 0, 1))
        self.assertEqual(self._ozet(bugun=date(2026, 8, 31)), [])           # ay hiç başlamadı

    def test_gelecek_gunluk_kayit_sayilmaz(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        _kayit(p, date(2026, 9, 20), "GELDI")                                # bugünden sonra
        self.assertEqual(self._sayilar(self._tek(bugun=date(2026, 9, 19))), (0, 0, 0, 0, 0, 19 - 2))

    def test_silinmis_kayit_ve_silinmis_personel_sayilmaz(self):
        p = personel_kur(ad="ali", ise_giris_tarihi=date(2020, 1, 1))
        k = _kayit(p, date(2026, 9, 1), "GELDI")
        k.silindi = True
        k.save()
        q = personel_kur(ad="veli", ise_giris_tarihi=date(2020, 1, 1))
        personel_sil(q)
        (s,) = self._ozet()
        self.assertEqual((s.personel.pk, s.geldi), (p.pk, 0))

    def test_personel_ids_filtresi_ve_sirali_liste(self):
        b = personel_kur(ad="berk", ise_giris_tarihi=date(2020, 1, 1))
        a = personel_kur(ad="ali", ise_giris_tarihi=date(2020, 1, 1))
        self.assertEqual([s.personel.pk for s in self._ozet()], [a.pk, b.pk])         # ad sırası
        self.assertEqual([s.personel.pk for s in self._ozet(personel_ids=[b.pk])], [b.pk])
        self.assertEqual(self._ozet(personel_ids=[]), [])

    def test_toplam(self):
        a = personel_kur(ad="ali", ise_giris_tarihi=date(2020, 1, 1))
        b = personel_kur(ad="berk", ise_giris_tarihi=date(2020, 1, 1))
        _kayit(a, date(2026, 9, 1), "GELDI")
        _kayit(b, date(2026, 9, 1), "GELMEDI")
        _kayit(b, date(2026, 9, 2), "GELMEDI")
        t = aylik_toplam(self._ozet())
        self.assertEqual((t.geldi, t.gelmedi, t.girilmemis, t.personel), (1, 2, 26 - 1 + 26 - 2, None))

    def test_sorgu_sayisi_sabit(self):
        for ad in ("ali", "berk", "can", "deniz"):
            p = personel_kur(ad=ad, ise_giris_tarihi=date(2020, 1, 1))
            _kayit(p, date(2026, 9, 1))
        with self.assertNumQueries(3):
            self._ozet()


class YoklamaViewTest(BugunSabitTemel):
    def setUp(self):
        super().setUp()
        yama = mock.patch("core.views.tr_bugun", return_value=BUGUN)
        yama.start()
        self.addCleanup(yama.stop)
        self.yon = User.objects.create_superuser("ykyon", password="x")
        self.devamci = User.objects.create_user("ykdevamci", password="x")
        EkranYetki.objects.create(kullanici=self.devamci, ekran_kod="personel_devam")
        self.personelci = User.objects.create_user("ykpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.ikisi = User.objects.create_user("ykikisi", password="x")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel_devam")
        self.bos = User.objects.create_user("ykbos", password="x")
        self.a = personel_kur(ad="ahmet", gorev="operator")
        self.b = personel_kur(ad="berk")

    def _url(self, tarih=None):
        u = reverse("core:yoklama")
        return f"{u}?tarih={tarih}" if tarih else u

    def _post(self, tarih="2026-09-14", **alanlar):
        veri = {"tarih": tarih}
        veri.update(alanlar)
        return self.client.post(reverse("core:yoklama"), veri)

    def test_yetki_302_403(self):
        urller = [reverse("core:yoklama"), reverse("core:yoklama_aylik")]
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 302, u)
        for kullanici in (self.bos, self.personelci):
            self.client.force_login(kullanici)
            for u in urller:
                self.assertEqual(self.client.get(u).status_code, 403, (kullanici.username, u))
            self.assertEqual(self._post().status_code, 403)
        self.assertEqual(PersonelDevam.objects.count(), 0)

    def test_izgara_personeli_ve_alan_adlarini_gosterir(self):
        _kayit(self.a, GUN, "YARIM_GUN", "doktor")
        self.client.force_login(self.devamci)
        r = self.client.get(self._url("2026-09-14"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AHMET VELİ")
        self.assertContains(r, "BERK VELİ")
        self.assertContains(r, f'name="durum_{self.a.pk}"')
        self.assertContains(r, f'name="not_{self.b.pk}"')
        self.assertContains(r, 'value="doktor"')
        self.assertContains(r, "14 Eylül 2026")
        self.assertContains(r, 'id="bosgeldi"')
        satir = {s.personel.pk: s for s in r.context["satirlar"]}
        self.assertEqual(satir[self.a.pk].durum, "YARIM_GUN")
        self.assertEqual(r.context["ozet"], {"geldi": 0, "yarim": 1, "gelmedi": 0, "izinli": 0,
                                              "raporlu": 0, "girilmemis": 1})

    def test_izinli_satir_kilitli_giris_yok(self):
        izin_ekle(self.a, tur="YILLIK", baslangic=GUN, bitis=GUN)
        c = personel_kur(ad="cemil")
        izin_ekle(c, tur="RAPOR", baslangic=GUN, bitis=GUN)
        self.client.force_login(self.devamci)
        r = self.client.get(self._url("2026-09-14"))
        self.assertNotContains(r, f'name="durum_{self.a.pk}"')
        self.assertNotContains(r, f'name="durum_{c.pk}"')
        self.assertContains(r, f'name="durum_{self.b.pk}"')
        self.assertContains(r, "🌴 İzinli")
        self.assertContains(r, "🩺 Raporlu")

    def test_post_kaydeder_ve_tarihle_doner(self):
        self.client.force_login(self.devamci)
        r = self._post(**{f"durum_{self.a.pk}": "GELDI", f"not_{self.a.pk}": "geç geldi",
                          f"durum_{self.b.pk}": ""})
        self.assertRedirects(r, self._url("2026-09-14"), fetch_redirect_response=False)
        k = PersonelDevam.objects.get()
        self.assertEqual((k.personel, k.tarih, k.durum, k.notlar, k.created_by),
                         (self.a, GUN, "GELDI", "geç geldi", self.devamci))
        sayfa = self.client.get(self._url("2026-09-14"))
        self.assertContains(sayfa, "1 kayıt eklendi/güncellendi, 0 temizlendi")
        # aynı POST tekrar: değişiklik yok, tek satır
        self._post(**{f"durum_{self.a.pk}": "GELDI", f"not_{self.a.pk}": "geç geldi"})
        self.assertEqual(PersonelDevam.objects.count(), 1)
        self.assertContains(self.client.get(self._url("2026-09-14")), "Değişiklik yok")

    def test_post_bos_secim_kaydi_temizler(self):
        _kayit(self.a, GUN, "GELMEDI")
        self.client.force_login(self.devamci)
        self._post(**{f"durum_{self.a.pk}": ""})
        self.assertTrue(PersonelDevam.objects.get(personel=self.a).silindi)

    def test_post_izinli_ayrilan_ve_gonderilmeyen_isleme_girmez(self):
        izin_ekle(self.a, tur="YILLIK", baslangic=GUN, bitis=GUN)
        ayrilan = personel_kur(ad="eski", isten_cikis_tarihi=GUN - timedelta(days=1))
        _kayit(self.b, GUN, "GELDI")
        self.client.force_login(self.devamci)
        self._post(**{f"durum_{self.a.pk}": "GELDI", f"durum_{ayrilan.pk}": "GELDI"})   # b hiç gönderilmedi
        self.assertFalse(PersonelDevam.objects.filter(personel__in=[self.a, ayrilan]).exists())
        self.assertEqual(PersonelDevam.objects.get(personel=self.b, silindi=False).durum, "GELDI")

    def test_post_gecersiz_durum_hata_mesaji_kayit_yok(self):
        self.client.force_login(self.devamci)
        r = self._post(**{f"durum_{self.a.pk}": "IZINLI"})
        self.assertEqual(PersonelDevam.objects.count(), 0)
        self.assertContains(self.client.get(r["Location"]), "Geçersiz devam durumu")

    def test_gelecek_tarih_izgara_yok_post_reddedilir(self):
        self.client.force_login(self.devamci)
        r = self.client.get(self._url("2026-09-25"))
        self.assertContains(r, "Gelecek tarih için yoklama girilemez")
        self.assertNotContains(r, f'name="durum_{self.a.pk}"')
        self._post(tarih="2026-09-25", **{f"durum_{self.a.pk}": "GELDI"})
        self.assertEqual(PersonelDevam.objects.count(), 0)

    def test_varsayilan_ve_gecersiz_tarih_bugune_duser(self):
        self.client.force_login(self.devamci)
        for u in (self._url(), self._url("gecersiz"), self._url("1850-01-01"), self._url("2026-13-45")):
            r = self.client.get(u)
            self.assertEqual(r.status_code, 200, u)
            self.assertEqual(r.context["tarih"], BUGUN, u)
        self.assertEqual(self.client.get(self._url("2026-09-14")).context["tarih"], GUN)

    def test_onceki_sonraki_baglantilari_ve_pazar_notu(self):
        self.client.force_login(self.devamci)
        r = self.client.get(self._url("2026-09-19"))                 # bugün: sonraki yok
        self.assertIsNone(r.context["sonraki"])
        self.assertEqual(r.context["onceki"], date(2026, 9, 18))
        self.assertNotContains(r, "Pazar günü hafta tatili")
        r2 = self.client.get(self._url("2026-09-13"))                # Pazar
        self.assertEqual(r2.context["sonraki"], date(2026, 9, 14))
        self.assertContains(r2, "Pazar günü hafta tatili")

    def test_menude_yalniz_yetkili_gorur(self):
        self.client.force_login(self.devamci)
        r = self.client.get(self._url())
        self.assertContains(r, reverse("core:yoklama"))
        self.assertContains(r, "Devam / Yoklama")
        self.client.force_login(self.personelci)
        self.assertNotContains(self.client.get(reverse("core:personeller")), reverse("core:yoklama"))

    def test_personel_linki_yalniz_personel_yetkilisine(self):
        detay = reverse("core:personel_detay", args=[self.a.pk])
        self.client.force_login(self.devamci)
        self.assertNotContains(self.client.get(self._url("2026-09-14")), detay)
        self.client.force_login(self.ikisi)
        self.assertContains(self.client.get(self._url("2026-09-14")), detay)

    def test_personel_yokken_bos_durum_mesaji(self):
        Personel.objects.all().delete()
        self.client.force_login(self.devamci)
        self.assertContains(self.client.get(self._url("2026-09-14")), "yoklaması alınacak personel yok")


class YoklamaAylikViewTest(BugunSabitTemel):
    def setUp(self):
        super().setUp()
        yama = mock.patch("core.views.tr_bugun", return_value=BUGUN)
        yama.start()
        self.addCleanup(yama.stop)
        self.devamci = User.objects.create_user("aydevamci", password="x")
        EkranYetki.objects.create(kullanici=self.devamci, ekran_kod="personel_devam")
        self.ikisi = User.objects.create_user("aylikisi", password="x")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel_devam")
        self.a = personel_kur(ad="ahmet", ise_giris_tarihi=date(2020, 1, 1))
        _kayit(self.a, date(2026, 9, 1), "GELDI")
        _kayit(self.a, date(2026, 9, 2), "GELMEDI")

    def _get(self, **q):
        return self.client.get(reverse("core:yoklama_aylik"), q)

    def test_sayilar_ve_toplam(self):
        self.client.force_login(self.devamci)
        r = self._get(ay="2026-09")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Eylül 2026")
        (s,) = r.context["satirlar"]
        # bugün 19 Eylül: 1-19 arası 19 gün - 3 Pazar (6, 13; 19'a kadar 2 Pazar) => 17; 2'si girilmiş
        self.assertEqual((s.geldi, s.gelmedi, s.girilmemis), (1, 1, 19 - 2 - 2))
        self.assertEqual(r.context["toplam"].geldi, 1)
        self.assertContains(r, "AHMET VELİ")

    def test_onceki_sonraki_ay_ve_varsayilan(self):
        self.client.force_login(self.devamci)
        r = self._get()                                             # varsayılan: bugünün ayı
        self.assertEqual(r.context["ay_param"], "2026-09")
        self.assertEqual(r.context["onceki_ay"], "2026-08")
        self.assertIsNone(r.context["sonraki_ay"])                  # bu aydan sonrası gösterilmez
        r2 = self._get(ay="2026-01")
        self.assertEqual((r2.context["onceki_ay"], r2.context["sonraki_ay"]), ("2025-12", "2026-02"))
        r3 = self._get(ay="2026-08")
        self.assertEqual(r3.context["sonraki_ay"], "2026-09")

    def test_gecersiz_ay_varsayilana_duser(self):
        self.client.force_login(self.devamci)
        for ay in ("gecersiz", "2026-13", "1850-01", "", "2026/09"):
            r = self._get(ay=ay)
            self.assertEqual(r.status_code, 200, ay)
            self.assertEqual(r.context["ay_param"], "2026-09", ay)

    def test_gelecek_ay_bos(self):
        self.client.force_login(self.devamci)
        r = self._get(ay="2026-12")
        self.assertEqual(list(r.context["satirlar"]), [])
        self.assertContains(r, "devam bilgisi gösterilecek personel yok")

    def test_personel_linki_yetkiye_gore(self):
        detay = reverse("core:personel_detay", args=[self.a.pk])
        self.client.force_login(self.devamci)
        self.assertNotContains(self._get(ay="2026-09"), detay)
        self.client.force_login(self.ikisi)
        self.assertContains(self._get(ay="2026-09"), detay)


class PersonelKartiDevamTest(BugunSabitTemel):
    def setUp(self):
        super().setUp()
        yama = mock.patch("core.views.tr_bugun", return_value=BUGUN)
        yama.start()
        self.addCleanup(yama.stop)
        self.personelci = User.objects.create_user("kdpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.ikisi = User.objects.create_user("kdikisi", password="x")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=self.ikisi, ekran_kod="personel_devam")
        self.p = personel_kur(ad="ahmet", ise_giris_tarihi=date(2020, 1, 1))
        _kayit(self.p, date(2026, 9, 1), "GELDI")
        _kayit(self.p, date(2026, 9, 2), "YARIM_GUN")
        _kayit(self.p, date(2026, 8, 31), "GELMEDI")                # önceki ay: sayılmaz
        self.url = reverse("core:personel_detay", args=[self.p.pk])

    def test_kart_yalniz_devam_yetkilisine(self):
        self.client.force_login(self.personelci)
        r = self.client.get(self.url)
        self.assertNotContains(r, "Devam · ")
        self.assertNotIn("devam_ozet", r.context)
        self.client.force_login(self.ikisi)
        r2 = self.client.get(self.url)
        self.assertContains(r2, "Devam · Eylül 2026")
        self.assertContains(r2, reverse("core:yoklama_aylik") + "?ay=2026-09")
        o = r2.context["devam_ozet"]
        self.assertEqual((o.geldi, o.yarim, o.gelmedi), (1, 1, 0))

    def test_ay_basinda_ayrilan_icin_kart_bos_mesaj(self):
        ayrilan = personel_kur(ad="eski", ise_giris_tarihi=date(2020, 1, 1),
                               isten_cikis_tarihi=date(2026, 8, 20))
        self.client.force_login(self.ikisi)
        r = self.client.get(reverse("core:personel_detay", args=[ayrilan.pk]))
        self.assertIsNone(r.context["devam_ozet"])
        self.assertContains(r, "Bu ay için devam bilgisi yok")


class YoklamaTrGunuTest(TestCase):
    def test_varsayilan_tarih_tr_gunudur_utc_degil(self):
        kul = User.objects.create_user("trgunu", password="x")
        EkranYetki.objects.create(kullanici=kul, ekran_kod="personel_devam")
        personel_kur(ad="ahmet")
        self.client.force_login(kul)
        an = datetime(2026, 9, 19, 22, 30, tzinfo=dt_timezone.utc)          # = 20 Eylül 01:30 (TR)
        with mock.patch("django.utils.timezone.now", return_value=an):
            r = self.client.get(reverse("core:yoklama"))
        self.assertEqual(r.context["tarih"], date(2026, 9, 20))
        self.assertContains(r, "20 Eylül 2026")
        self.assertContains(r, "Pazar günü hafta tatili")
