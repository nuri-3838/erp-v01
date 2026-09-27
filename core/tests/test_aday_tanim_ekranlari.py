"""ERP — CRM: 'Kategori' → 'Kaynak' + Tip/Potansiyel/Aşama tanım ekranları.

Kapsam: core.services.aday_tanim (Tip/Potansiyel/Aşama tanım CRUD'u), core/views.py'deki
yeni tanım ekranları + eski /crm/kategoriler/ 301 yönlendirmesi + hiyerarşik kaynak filtresi,
migration 0132'nin tohum verisi + CharField->FK veri taşıması. Cariye Dönüştür'ün kategori
önerisi/engeli testleri core/tests/test_aday_cariye_donustur.py'de zaten var (tabloya taşındı,
burada tekrar edilmez) — burada yalnız "değer TABLODAN geliyor, sabit değil" kanıtı var."""
from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.models import (
    AdayAsamaTanim, AdayMusteri, AdayMusteriKategori, AdayPotansiyelTanim, AdayTipTanim,
    EkranYetki,
)
from core.services import aday_donustur
from core.services import aday_tanim as tanim_servis
from core.services.aday import aday_musteri_olustur


def _tip(kod):
    return AdayTipTanim.objects.get(sistem_kodu=kod)


def _potansiyel(kod):
    return AdayPotansiyelTanim.objects.get(sistem_kodu=kod)


def _asama(kod):
    return AdayAsamaTanim.objects.get(sistem_kodu=kod)


def _aday(unvan="test aday", **kw):
    kw.setdefault("tip_id", _tip("ADAY").pk)
    kw.setdefault("asama_id", _asama("YENI").pk)
    return aday_musteri_olustur(unvan=unvan, para_birimi="TRY", **kw)


# --- Migration 0132: tohum verisi + CharField->FK veri taşıma (gerçek şema geçişiyle) -----
class Migrasyon0132Test(TransactionTestCase):
    """Test DB'sini gerçekten 0131'e indirip eski CharField satırları yazar, sonra 0132'ye
    tekrar çıkarıp tohum verisini + veri taşıma dağılımını doğrular. TransactionTestCase
    zorunlu (şema değişikliği dıştaki atomic sarmalayıcı içinde çalışamaz).
    ``serialized_rollback=True`` ŞART: TransactionTestCase kendi flush'ında tüm tabloları
    boşaltır — bu olmadan migration 0132'nin tohum verisi (AdayTipTanim vb.) SONRAKİ
    testler için kalıcı olarak silinmiş kalır (Django'nun testin başında aldığı serileştirme
    olmadan geri gelmez)."""
    serialized_rollback = True

    def tearDown(self):
        # Şemayı güncel migration'a geri getir (flush + serialized_rollback'ten ÖNCE —
        # aksi halde flush yanlış/eksik tablo kümesine karşı çalışır).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_tohum_verisi_ve_veri_tasima_dogru(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0131_aday_cari_alani")])
        executor.loader.build_graph()
        eski_apps = executor.loader.project_state(("core", "0131_aday_cari_alani")).apps
        AdayMusteriEski = eski_apps.get_model("core", "AdayMusteri")
        eski_a = AdayMusteriEski.objects.create(
            unvan="ESKİ ARACI FİRMA", tip="ARACI", asama="TEMAS", potansiyel="YUKSEK",
            para_birimi="TRY")
        eski_b = AdayMusteriEski.objects.create(
            unvan="ESKİ BOŞ POTANSİYEL", tip="ADAY", asama="KAPALI", potansiyel="",
            kapanis_nedeni="DIGER", para_birimi="TRY")

        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0132_aday_tip_potansiyel_asama_tanim")])
        executor.loader.build_graph()

        # Tohum verisi: sayı + sistem_kodu + sira + rol/sicak (spec tablosu)
        self.assertEqual(AdayTipTanim.objects.filter(silindi=False).count(), 8)
        self.assertEqual(AdayPotansiyelTanim.objects.filter(silindi=False).count(), 3)
        self.assertEqual(AdayAsamaTanim.objects.filter(silindi=False).count(), 7)
        yuksek = _potansiyel("YUKSEK")
        self.assertTrue(yuksek.sicak)
        self.assertEqual(yuksek.sira, 1)
        self.assertFalse(_potansiyel("ORTA").sicak)
        self.assertFalse(_potansiyel("DUSUK").sicak)
        self.assertEqual(_asama("YENI").rol, AdayAsamaTanim.Rol.BASLANGIC)
        self.assertEqual(_asama("KAPALI").rol, AdayAsamaTanim.Rol.KAPALI)
        for kod in ("TEMAS", "ILGILI", "TEKLIF", "NUMUNE", "SIPARIS"):
            self.assertEqual(_asama(kod).rol, AdayAsamaTanim.Rol.ARA)
        self.assertTrue(_tip("ADAY").cariye_donusturulebilir)
        self.assertFalse(_tip("RAKIP").cariye_donusturulebilir)
        self.assertFalse(_tip("PAZAR_BILGISI").cariye_donusturulebilir)

        # Veri taşıma: eski CharField değerleri doğru tanım kayıtlarına eşlenmiş (birebir)
        yeni_a = AdayMusteri.objects.get(pk=eski_a.pk)
        self.assertEqual(yeni_a.tip.sistem_kodu, "ARACI")
        self.assertEqual(yeni_a.asama.sistem_kodu, "TEMAS")
        self.assertEqual(yeni_a.potansiyel.sistem_kodu, "YUKSEK")
        yeni_b = AdayMusteri.objects.get(pk=eski_b.pk)
        self.assertEqual(yeni_b.tip.sistem_kodu, "ADAY")
        self.assertEqual(yeni_b.asama.sistem_kodu, "KAPALI")
        self.assertIsNone(yeni_b.potansiyel)
        self.assertEqual(yeni_b.kapanis_nedeni, "DIGER")

    # Geri alma (0132 -> 0131) uçtan uca canlı SATIR verisiyle burada test EDİLMEDİ: aynı
    # transaction içinde RunPython'un bulk .update()'i ile hemen ardından gelen ALTER TABLE
    # (FK sütunu düşürme) Postgres'te "pending trigger events" hatası veriyor — bu migration
    # mantığının değil, Postgres'in DML+DDL'i aynı transaction'da karıştırma kısıtının bir
    # sonucu (bkz. manage.py migrate ile BOŞ tabloda elle doğrulanan forward/backward geçiş,
    # bu dosyanın yazıldığı oturumda). Gerçek bir geri alma zaten önce yedekten dönmeyi
    # varsayar (bkz. deploy raporu), canlı veriyle in-place şema geri alma değil.


# --- Tanım servisi: ortak kurallar (sistem kaydı, kullanım guard, pasif yap) --------------
class TanimServisTest(TestCase):
    def test_ad_tr_buyuk_harf_ve_benzersiz(self):
        t = tanim_servis.tip_olustur(ad="özel tip")
        self.assertEqual(t.ad, "ÖZEL TİP")
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.tip_olustur(ad="ÖZEL TİP")

    def test_sistem_kaydi_silinemez(self):
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.tanim_sil(_tip("ADAY"))

    def test_sistem_kaydinin_adi_degistirilebilir(self):
        t = tanim_servis.tip_guncelle(
            _tip("ARACI"), ad="aracı yeni ad", sira=3, aktif=True, renk="MAVI",
            cari_kategori_yurtici=None, cari_kategori_yurtdisi=None,
            cariye_donusturulebilir=True)
        self.assertEqual(t.ad, "ARACI YENİ AD")
        self.assertEqual(t.sistem_kodu, "ARACI")   # kod DEĞİŞMEDİ

    def test_kullanilan_kayit_silinemez_pasif_yapilabilir(self):
        ozel = tanim_servis.tip_olustur(ad="kullanılan tip", cariye_donusturulebilir=False)
        _aday(unvan="kullanan aday", tip_id=ozel.pk)
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.tanim_sil(ozel)
        tanim_servis.tanim_pasif_yap(ozel, aktif=False)
        ozel.refresh_from_db()
        self.assertFalse(ozel.aktif)

    def test_kullanilmayan_ozel_kayit_silinebilir(self):
        ozel = tanim_servis.potansiyel_olustur(ad="kullanılmayan potansiyel")
        tanim_servis.tanim_sil(ozel)
        ozel.refresh_from_db()
        self.assertTrue(ozel.silindi)

    def test_asama_tam_bir_aktif_baslangic_olmali(self):
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.asama_olustur(ad="ikinci başlangıç", rol=AdayAsamaTanim.Rol.BASLANGIC)

    def test_asama_en_az_bir_aktif_kapali_olmali(self):
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.asama_pasif_yap(_asama("KAPALI"), aktif=False)

    def test_tek_baslangici_pasif_yapmak_engellenir(self):
        # Tam olarak bir aktif BASLANGIC kuralı HİÇBİR ara adımda bozulamaz — mevcut tek
        # BASLANGIC'i pasif yapmak, yerine yenisi henüz yokken bile reddedilir.
        with self.assertRaises(tanim_servis.AdayTanimHatasi):
            tanim_servis.asama_pasif_yap(_asama("YENI"), aktif=False)


# --- Kategori önerisi TABLODAN gelir (sabit kod değil) ------------------------------------
class KategoriOnerisiTablodanGelirTest(TestCase):
    def test_tip_tanimindaki_esleme_degisince_oneri_degisir(self):
        from core.models import CariKategori
        ust = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="999")
        yeni_kategori = CariKategori.objects.create(ad="ÖZEL", kod="1", ust=ust)
        tanim_servis.tip_guncelle(
            _tip("ARACI"), ad="ARACI", sira=3, aktif=True, renk="GRI",
            cari_kategori_yurtici=yeni_kategori, cari_kategori_yurtdisi=yeni_kategori,
            cariye_donusturulebilir=True)
        a = _aday(unvan="tablo testi", tip_id=_tip("ARACI").pk)
        self.assertEqual(aday_donustur.kategori_onerisi(a).pk, yeni_kategori.pk)

    def test_donusturulebilir_false_yapilinca_engellenir(self):
        ozel = tanim_servis.tip_olustur(ad="özel engelli tip", cariye_donusturulebilir=True)
        a = _aday(unvan="engelsiz once", tip_id=ozel.pk)
        self.assertIsNone(aday_donustur.donusturme_engeli_var_mi(a))
        tanim_servis.tip_guncelle(
            ozel, ad=ozel.ad, sira=0, aktif=True, renk="GRI", cari_kategori_yurtici=None,
            cari_kategori_yurtdisi=None, cariye_donusturulebilir=False)
        a.refresh_from_db()
        self.assertIsNotNone(aday_donustur.donusturme_engeli_var_mi(a))


# --- Sıcak sekmesi potansiyel.sicak bayrağına bağlı (sabit YUKSEK değil) ------------------
class SicakBayragiVeriTabanliTest(TestCase):
    def setUp(self):
        self.yon = User.objects.create_superuser("sicakyon", password="x")
        self.client.force_login(self.yon)

    def test_sicak_isareti_kaldirilinca_sekmeden_duser(self):
        _aday(unvan="sicak firma", potansiyel_id=_potansiyel("YUKSEK").pk)
        r = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "sicak"})
        self.assertContains(r, "SİCAK FİRMA")
        tanim_servis.potansiyel_guncelle(
            _potansiyel("YUKSEK"), ad="YÜKSEK", sira=1, aktif=True, renk="GRI", sicak=False)
        r2 = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "sicak"})
        self.assertNotContains(r2, "SİCAK FİRMA")

    def test_baska_potansiyele_sicak_verilince_sekmeye_girer(self):
        _aday(unvan="orta ama sicak", potansiyel_id=_potansiyel("ORTA").pk)
        r = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "sicak"})
        self.assertNotContains(r, "ORTA AMA SİCAK")
        tanim_servis.potansiyel_guncelle(
            _potansiyel("ORTA"), ad="ORTA", sira=2, aktif=True, renk="GRI", sicak=True)
        r2 = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "sicak"})
        self.assertContains(r2, "ORTA AMA SİCAK")


# --- Yeni eklenen tanım formda seçilebilir + listede görünür ------------------------------
class YeniTanimSecilebilirTest(TestCase):
    def setUp(self):
        self.yon = User.objects.create_superuser("yenitanimyon", password="x")
        self.client.force_login(self.yon)

    def test_yeni_tip_ekle_formunda_secilebilir(self):
        r = self.client.post(reverse("core:aday_tipi_ekle"), {
            "ad": "yepyeni tip", "sira": "9", "aktif": "on", "renk": "MOR",
            "cari_kategori_yurtici": "", "cari_kategori_yurtdisi": "",
            "cariye_donusturulebilir": "on"})
        self.assertEqual(r.status_code, 302)
        yeni = AdayTipTanim.objects.get(ad="YEPYENİ TİP")
        r2 = self.client.get(reverse("core:aday_musteri_ekle"))
        self.assertContains(r2, f'value="{yeni.pk}"')
        self.assertContains(r2, "YEPYENİ TİP")

    def test_pasif_tip_formda_secilemez_ama_mevcut_adayda_gorunur(self):
        ozel = tanim_servis.tip_olustur(ad="pasif olacak tip")
        a = _aday(unvan="pasif tip adayi", tip_id=ozel.pk)
        tanim_servis.tanim_pasif_yap(ozel, aktif=False)
        r_ekle = self.client.get(reverse("core:aday_musteri_ekle"))
        self.assertNotContains(r_ekle, f'value="{ozel.pk}"')
        r_duzenle = self.client.get(reverse("core:aday_musteri_duzenle", args=[a.pk]))
        self.assertContains(r_duzenle, f'value="{ozel.pk}"')


# --- Ekranlar: liste/ekle/düzenle/sil/pasif-yap + yetki -----------------------------------
class TanimEkranYetkiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("ekranyon", password="x")
        cls.bos = User.objects.create_user("ekranbos", password="x")

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        for url_adi in ("core:aday_tipleri", "core:aday_potansiyelleri", "core:aday_asamalari"):
            self.assertEqual(self.client.get(reverse(url_adi)).status_code, 403)

    def test_tip_listesi_200_ve_kayit_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_tipleri"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Aday")

    def test_asama_sil_engellenince_mesaj_doner(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_asamasi_sil", args=[_asama("YENI").pk]))
        self.assertRedirects(r, reverse("core:aday_asamalari"))
        self.assertTrue(AdayAsamaTanim.objects.filter(pk=_asama("YENI").pk, silindi=False).exists())


# --- Eski /crm/kategoriler/ -> /crm/kaynaklar/ 301 + eski GET parametreleri ---------------
class EskiUrlVeParametreTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("eskiurlyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_eski_kategoriler_url_301(self):
        r = self.client.get("/crm/kategoriler/")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.url, "/crm/kaynaklar/")

    def test_eski_kategoriler_alt_url_301(self):
        k = AdayMusteriKategori.objects.create(ad="ESKİ URL KAYNAK", kod="EUK")
        r = self.client.get(f"/crm/kategoriler/{k.pk}/duzenle/")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.url, f"/crm/kaynaklar/{k.pk}/duzenle/")

    def test_eski_metin_kodu_ile_asama_filtresi_calisir(self):
        _aday(unvan="eski kod ile filtrelenen", asama_id=_asama("TEMAS").pk)
        r = self.client.get(reverse("core:aday_musteriler"), {"asama": "TEMAS"})
        self.assertContains(r, "ESKİ KOD İLE FİLTRELENEN")

    def test_eski_metin_kodu_ile_potansiyel_filtresi_calisir(self):
        _aday(unvan="eski potansiyel kodu", potansiyel_id=_potansiyel("DUSUK").pk)
        r = self.client.get(reverse("core:aday_musteriler"), {"potansiyel": "DUSUK"})
        self.assertContains(r, "ESKİ POTANSİYEL KODU")


# --- Hiyerarşik kaynak filtresi: üst seçilince alt kaynaklardaki adaylar da gelir ---------
class HiyerarsikKaynakFiltresiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("hiyerarsiyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_ust_secilince_alt_kaynaktaki_adaylar_da_gelir(self):
        from core.services.aday_kategori import aday_kategori_olustur
        ust = aday_kategori_olustur(ad="DATA", kod="01")
        alt = aday_kategori_olustur(ad="ESKİ FİRMA", kod="01", ust_id=ust.pk)
        _aday(unvan="ust duzeyde aday", kategori_id=ust.pk)
        _aday(unvan="alt duzeyde aday", kategori_id=alt.pk)
        _aday(unvan="ilgisiz aday")
        r = self.client.get(reverse("core:aday_musteriler"), {"kaynak": ust.pk, "gorunum": "tumu"})
        self.assertContains(r, "UST DUZEYDE ADAY")
        self.assertContains(r, "ALT DUZEYDE ADAY")
        self.assertNotContains(r, "İLGİSİZ ADAY")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"kaynak": alt.pk, "gorunum": "tumu"})
        self.assertContains(r2, "ALT DUZEYDE ADAY")
        self.assertNotContains(r2, "UST DUZEYDE ADAY")
