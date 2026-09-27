"""ERP — CRM: Liste satırında hızlı işlemler (+ Aktivite penceresi, 🟢 WhatsApp butonu) +
Cariye dönüşünce otomatik aşama'nın ("CARI" rolü) liste görünürlüğü bu dosyada DEĞİL,
test_aday_cariye_donustur.py / test_aday_musteri_sekme_filtre.py'de (ayrı spec, ayrı dosya).

Kapsam: core/views.py::aday_aktivite_ekle'nin next= desteği (açık yönlendirme koruması
dahil), core/views.py::aday_musteriler'in whatsapp_secenekleri/liste_url context'i,
core/templates/core/_aday_hizli_islemler.html'in görünürlük koşulları. Liste sorgu sayısı
testi zaten test_aday_musteri_sekme_filtre.py::SorguSayisiTest'te (12'ye güncellendi)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import AdayAsamaTanim, AdayPotansiyelTanim, AdayTipTanim, Cari
from core.services import aday_donustur
from core.services.aday import aday_musteri_olustur as _aday_musteri_olustur_ham
from core.services.aday import aday_yetkili_ekle
from core.tests.aday_yardimci import varsayilan_kaynak_id


def aday_musteri_olustur(*, tip=None, potansiyel=None, asama=None, **kw):
    kw["tip_id"] = AdayTipTanim.objects.get(sistem_kodu=tip or "ADAY").pk
    kw["potansiyel_id"] = (AdayPotansiyelTanim.objects.get(sistem_kodu=potansiyel).pk
                           if potansiyel else None)
    kw["asama_id"] = AdayAsamaTanim.objects.get(sistem_kodu=asama or "YENI").pk
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    kw.setdefault("farkli_firma_onay", True)
    return _aday_musteri_olustur_ham(**kw)


class _Taban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("hizliyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def _liste(self, **params):
        return self.client.get(reverse("core:aday_musteriler"), params)


# --- Hızlı aktivite POST'u: next= desteği + açık yönlendirme koruması -------------------
class HizliAktiviteNextTest(_Taban):
    def setUp(self):
        super().setUp()
        self.aday = aday_musteri_olustur(unvan="hizli aktivite adayi")

    def _post(self, **extra):
        veri = {"tarih": "2026-09-27", "tur": "GORUSME", "aciklama": "hızlı not",
                "sonraki_adim": "", "sonraki_adim_tarihi": ""}
        veri.update(extra)
        return self.client.post(
            reverse("core:aday_aktivite_ekle", args=[self.aday.pk]), veri)

    def test_gecerli_next_ile_dogru_url_e_doner(self):
        hedef = "/crm/adaylar/?gorunum=tumu#aday-%s" % self.aday.pk
        r = self._post(next=hedef)
        self.assertRedirects(r, hedef, fetch_redirect_response=False)

    def test_next_yoksa_aday_detayina_doner(self):
        r = self._post()
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))

    def test_disaridan_gelen_next_reddedilir(self):
        r = self._post(next="http://evil.example.com/")
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))

    def test_protokol_bagimsiz_next_reddedilir(self):
        r = self._post(next="//evil.example.com/")
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))

    def test_next_ile_sonraki_adim_guncellenir(self):
        hedef = "/crm/adaylar/?gorunum=tumu#aday-%s" % self.aday.pk
        self._post(next=hedef, sonraki_adim="fiyat teklifi gonder",
                   sonraki_adim_tarihi="2026-10-01")
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.sonraki_adim, "fiyat teklifi gonder")
        self.assertEqual(str(self.aday.sonraki_adim_tarihi), "2026-10-01")

    def test_dogrulama_hatasinda_tam_sayfa_render_edilir_next_yoksayilir(self):
        # aciklama zorunlu — boş gönderilince kaydetmez, next'e de gitmez (tam sayfa formu).
        hedef = "/crm/adaylar/?gorunum=tumu#aday-%s" % self.aday.pk
        r = self._post(next=hedef, aciklama="")
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "core/aday_aktivite_form.html")
        self.assertContains(r, "zorunlu")


# --- Liste satırında görünürlük: + Aktivite / WhatsApp -----------------------------------
class HizliIslemGorunurlukTest(_Taban):
    def test_normal_adayda_aktivite_butonu_gorunur(self):
        aday = aday_musteri_olustur(unvan="normal aday")
        r = self._liste(gorunum="tumu")
        self.assertContains(r, "+ Aktivite")
        self.assertContains(r, f'data-aday-pk="{aday.pk}"')

    def test_donusmus_adayda_butonlar_gizli(self):
        # "+ Aktivite" ve data-aday-pk yalnız satıra özgü butonlarda geçer (sayfanın geri
        # kalanında — CSS/dialog dahil — hiç yok), bu yüzden güvenilir işaretleyici.
        cari = Cari.objects.create(kod="HZ1", unvan="DONUSMUS CARI", para_birimi="TRY")
        aday = aday_musteri_olustur(unvan="donusmus aday hizli")
        aday_donustur.mevcut_cariye_bagla(aday, cari, kullanici=self.yon)
        r = self._liste(gorunum="tumu")
        self.assertNotContains(r, "+ Aktivite")
        self.assertNotContains(r, f'data-aday-pk="{aday.pk}"')

    # WhatsApp görünürlük testlerinde "wa.me" bağlantısının VARLIĞI/YOKLUĞU kontrol edilir —
    # "ad-wa-buton"/"ad-wa-menu" CSS sınıf adları sayfanın <style> bloğunda HER ZAMAN geçtiği
    # için (satırdan bağımsız) güvenilir bir işaretleyici DEĞİL. Numaralar bilinçli olarak
    # KISA/geçersiz ("+90111" gibi) seçildi: telefon_normalize bunları parse edemediği için
    # DEĞİŞTİRMEDEN bırakır (bkz. core.dogrulama), kaydedilen değer testte yazılanla aynı kalır.
    def test_whatsapp_isaretsiz_numarada_buton_gorunmez(self):
        aday_musteri_olustur(unvan="isaretsiz numara", telefon="+90111",
                             telefon_whatsapp=False)
        r = self._liste(gorunum="tumu")
        self.assertNotContains(r, "https://wa.me/")

    def test_whatsapp_artisiz_numarada_buton_gorunmez(self):
        # "+" ile başlamayan numara wa_link None döner (spec: telefon formatı ayrı adım).
        aday_musteri_olustur(unvan="artisiz numara", telefon="123", telefon_whatsapp=True)
        r = self._liste(gorunum="tumu")
        self.assertNotContains(r, "https://wa.me/")

    def test_whatsapp_isaretli_arti_numarada_buton_gorunur(self):
        # Tek aday -> her satır tablo+kart görünümünde bir kez daha çizilir (bkz.
        # test_coklu_numarada_menu_gorunur notu); title niteliği hangi numara olduğunu
        # gösterir (tek numarada görünür etiket yok, spec: doğrudan wa.me linki).
        aday = aday_musteri_olustur(unvan="isaretli arti numara", telefon="+90111",
                                    telefon_whatsapp=True)
        r = self._liste(gorunum="tumu")
        self.assertContains(r, "https://wa.me/90111", count=2)
        self.assertContains(r, f'title="Kart: {aday.telefon}"', count=2)

    def test_whatsapp_yetkili_numarasinda_buton_gorunur(self):
        aday = aday_musteri_olustur(unvan="yetkili numarali aday")
        yetkili = aday_yetkili_ekle(aday, ad_soyad="veli yetkili", telefon="+90222",
                                    whatsapp=True)
        r = self._liste(gorunum="tumu")
        self.assertContains(r, "https://wa.me/90222", count=2)
        self.assertContains(r, f'title="VELİ YETKİLİ: {yetkili.telefon}"', count=2)

    def test_yetkilinin_isaretsiz_numarasi_gorunmez(self):
        aday = aday_musteri_olustur(unvan="yetkili isaretsiz")
        aday_yetkili_ekle(aday, ad_soyad="isaretsiz yetkili", telefon="+90333",
                          whatsapp=False)
        r = self._liste(gorunum="tumu")
        self.assertNotContains(r, "https://wa.me/")

    def test_coklu_numarada_menu_gorunur(self):
        # Görünür "Kart: ..." etiketi yalnız ÇOKLU durumda (açılır menüde) basılır; tek
        # numarada yalnız title niteliği kullanılır (bkz. yukarıdaki testler).
        aday = aday_musteri_olustur(unvan="coklu numara aday", telefon="+90111",
                                    telefon_whatsapp=True)
        yetkili = aday_yetkili_ekle(aday, ad_soyad="ikinci numara", telefon="+90222",
                                    whatsapp=True)
        r = self._liste(gorunum="tumu")
        self.assertContains(r, f"Kart: {aday.telefon}", count=2)
        self.assertContains(r, f"İKİNCİ NUMARA: {yetkili.telefon}", count=2)
        # Tablo + kart görünümü ikisi de HER ZAMAN render edilir (CSS medya sorgusu yalnız
        # görünürlüğü değiştirir) -> 2 aday x 2 görünüm = 4.
        self.assertEqual(r.content.decode().count("https://wa.me/"), 4)
