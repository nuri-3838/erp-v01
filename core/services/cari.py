"""Cari (CARİLER) servis katmanı — cari kartı CRUD + otomatik kod.

- Kod: kategori varsa ``kod_yolu-NNNN`` (örn. 320-10-0001), yoksa ``CAR-NNNN``. Taşımada
  eski kod korunur (kod parametresi verilir).
- UPPER alanlar (unvan/kısa ad/vergi dairesi/adres) TR büyük harfe çevrilir.
- VKN/TCKN ve Tax ID dolu ise silinmemişler arası benzersiz.
- Sevk (teslimat) adresleri artık ayrı ``CariSevkAdresi`` tablosunda (çoklu, banka
  hesapları/yetkili kişilerle aynı desen). ``cari_olustur`` cari'nin kendi adresi
  doluysa "Merkez Adres" adıyla otomatik + varsayılan bir sevk adresi de açar.
- Silme: soft-delete — AMA hareketli/belgeli cari SİLİNEMEZ (``silme_engelleri``); onun yerine PASİFE alınır (``Cari.aktif``).
- PASİF cari: yeni belge/harekette seçilemez (``secilebilir_cariler`` + ``pasif_kontrol``), geçmiş belge/ekstre/rapor etkilenmez.
- Silinmiş (soft-delete) carinin kodu ve muhasebe hesabı yeni cariye VERİLMEZ (``_kod_hesap_cakisma``; kod üretimi silinmişleri de sayar).
- VKN/TCKN: aynı numarayla ikinci aktif cari uyarıyla engellenir, yönetici "yine de kaydet" der (``CariMukerrerVKN``).
- Hareketli carinin ana para birimi yalnız yönetici + havuz bakiyesi onayıyla değişir (``CariPbOnayGerekli``).
"""
from __future__ import annotations

import calendar
import datetime
import os

from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.utils import timezone

from core import gorsel
from core.dogrulama import telefon_normalize, web_normalize
from core.metin import buyuk_harf_tr
from core.models import (
    Cari, CariAktivite, CariAktiviteEk, CariBanka, CariKategori, CariSevkAdresi, CariYetkili,
    HesapPlani, Sehir,
    Ulke,
)
from core.sayi import SayiHatasi, parse_tr
from core.services import hesap_plani as hp

AKTIVITE_IZINLI_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
AKTIVITE_MAKS_BOYUT = 10 * 1024 * 1024   # 10 MB


class CariHatasi(ValueError):
    """Cari kural ihlali (Türkçe mesaj)."""


class CariSilmeEngeli(CariHatasi):
    """Cari silinemez: ``nedenler`` = [(etiket, adet)] (muhasebe hareketi, fatura, teklif/sipariş, çek/senet, fason belgesi, numune, açık stok ayırması…)."""

    def __init__(self, mesaj, nedenler):
        super().__init__(mesaj)
        self.nedenler = nedenler


class CariMukerrerVKN(CariHatasi):
    """Aynı VKN/TCKN ile başka aktif cari var: ``mevcutlar`` = o cariler (ekranda bağlantı); yönetici ``mukerrer_onayi`` ile yine de kaydedebilir."""

    def __init__(self, mesaj, mevcutlar):
        super().__init__(mesaj)
        self.mevcutlar = mevcutlar


class CariPbOnayGerekli(CariHatasi):
    """Hareketli carinin ana para birimi değişiyor: yönetici onayı gerekir; ``havuzlar`` = mevcut PB havuz bakiyeleri (onay ekranında gösterilir)."""

    def __init__(self, mesaj, havuzlar, eski, yeni):
        super().__init__(mesaj)
        self.havuzlar, self.eski, self.yeni = havuzlar, eski, yeni


def aktif_cariler():
    """Silinmemiş TÜM cariler (aktif + pasif) — liste/rapor/filtre/ekstre için."""
    return (Cari.objects.filter(silindi=False)
            .select_related("kategori", "ulke", "sehir").order_by("unvan"))


def secilebilir_cariler(ek=()):
    """Yeni belge/hareket ekranlarında SEÇİLEBİLEN cariler: silinmemiş ve AKTİF. ``ek`` = düzenlenen kaydın mevcut cari pk'ları (pasif olsa da
    listede kalır — eski belge düzenlenirken seçim boşalmasın)."""
    ek = [int(x) for x in ek if str(x).isdigit()]
    return Cari.objects.filter(silindi=False).filter(Q(aktif=True) | Q(pk__in=ek)).order_by("unvan")


def pasif_kontrol(cari, hata_sinifi, *, mevcut_cari_id=None, ne="Yeni belge/hareket"):
    """PASİF cariye yeni belge/hareket girilemez (servis katmanı). ``mevcut_cari_id``: düzenlenen kaydın zaten bağlı olduğu cari (değişmediyse serbest)."""
    if cari is not None and not cari.aktif and cari.pk != mevcut_cari_id:
        raise hata_sinifi(f"{cari.kod} — {cari.unvan} carisi PASİF; {ne} girilemez. Önce cariyi aktifleştirin.")


def sonraki_cari_kodu(kategori) -> str:
    """Kategori kod yolundan sıradaki kod. Silinmiş (soft-delete) cariler ve hesap planındaki (silinmiş dahil) hesap kodları da sayılır —
    kullanılmış bir kod/hesap bir daha verilmez."""
    if kategori is not None and kategori.kod_yolu:
        on = kategori.kod_yolu + "-"
        son = 0
        for k in Cari.objects.filter(kod__startswith=on).values_list("kod", flat=True):
            p = k[len(on):]
            if p.isdigit():
                son = max(son, int(p))
        noktali = on.replace("-", ".")
        for k in HesapPlani.objects.filter(hesap_kodu__startswith=noktali).values_list("hesap_kodu", flat=True):
            p = k[len(noktali):]
            if p.isdigit():
                son = max(son, int(p))
        return f"{on}{str(son + 1).zfill(4)}"
    n = 1
    while Cari.objects.filter(kod=f"CAR-{str(n).zfill(4)}").exists():
        n += 1
    return f"CAR-{str(n).zfill(4)}"


def _kod_hesap_cakisma(kod, *, haric_pk=None):
    """Kod ya da muhasebe hesabı başka bir cariye (SİLİNMİŞ dahil) aitse yeniden verilmez."""
    noktali = kod.replace("-", ".")
    qs = Cari.objects.filter(Q(kod=kod) | Q(muhasebe_kodu=noktali))
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    c = qs.order_by("silindi").first()
    if c is None:
        return
    if not c.silindi and c.kod == kod:
        raise CariHatasi(f"Cari kodu zaten kayıtlı: {kod}")
    raise CariHatasi(f"{kod} kodu / {noktali} muhasebe hesabı daha önce {'silinmiş ' if c.silindi else ''}{c.kod} — {c.unvan} carisine ait; "
                     "yeniden kullanılamaz.")


def _ulke(ulke_id):
    if not ulke_id:
        return None
    u = Ulke.objects.filter(pk=ulke_id, silindi=False).first()
    if u is None:
        raise CariHatasi("Ülke bulunamadı.")
    return u


def _sehir(sehir_id):
    if not sehir_id:
        return None
    s = Sehir.objects.filter(pk=sehir_id, silindi=False).first()
    if s is None:
        raise CariHatasi("Şehir bulunamadı.")
    return s


def _vergi_benzersiz(vkn_tckn, tax_id, *, haric_pk=None, mukerrer_onayi=False, kullanici=None):
    """Tax ID kesin benzersiz. VKN/TCKN: aynı numarayla başka AKTİF (silinmemiş) cari varsa ``CariMukerrerVKN`` (mevcutlarıyla); ``mukerrer_onayi``
    + yönetici ise ikinci karta izin verilir (aynı firma hem müşteri hem tedarikçi)."""
    d = (tax_id or "").strip()
    if d:
        qs = Cari.objects.filter(silindi=False, tax_id=d)
        if haric_pk is not None:
            qs = qs.exclude(pk=haric_pk)
        if qs.exists():
            raise CariHatasi(f"Bu Tax ID zaten kayıtlı: {d}")
    d = (vkn_tckn or "").strip()
    if d:
        qs = Cari.objects.filter(silindi=False, vkn_tckn=d)
        if haric_pk is not None:
            qs = qs.exclude(pk=haric_pk)
        mevcut = list(qs.order_by("kod"))
        if mevcut:
            from core.yetki import yonetici_mi
            if mukerrer_onayi and yonetici_mi(kullanici):
                return
            raise CariMukerrerVKN("Bu VKN/TCKN zaten kayıtlı: " + d + " (" + ", ".join(f"{c.kod} — {c.unvan}" for c in mevcut) + ")", mevcut)


def _para_dogrula(deger, etiket):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise CariHatasi(f"{etiket} negatif olamaz.")
    return d


def _telefon_isle(deger, iso2, etiket, uyarilar):
    sonuc, tamam = telefon_normalize(deger, iso2)
    if not tamam and sonuc:
        uyarilar.append(f"{etiket} numarası doğrulanamadı; ülke kodu ile (+..) girin: {sonuc}")
    return sonuc


def _odeme_kosulu_dogrula(odeme_kosulu, odeme_gunu):
    """(odeme_kosulu, odeme_gunu) normalize edilmiş çiftini döner. Boş koşul -> (None, None).
    PEŞİN -> gün anlamsız, sessizce temizlenir. GÜN_SONRA/SONRAKİ_AY_GÜNÜ -> gün zorunlu +
    aralık kontrolü (bkz. Cari.clean() ile aynı kural, servis katmanında ayrıca zorlanır)."""
    odeme_kosulu = (odeme_kosulu or "").strip() or None
    if odeme_kosulu is None:
        return None, None
    if odeme_kosulu not in Cari.OdemeKosulu.values:
        raise CariHatasi("Geçersiz ödeme koşulu.")
    if odeme_kosulu == Cari.OdemeKosulu.PESIN:
        return odeme_kosulu, None
    if odeme_gunu in (None, ""):
        raise CariHatasi("Bu ödeme koşulu için gün sayısı zorunludur.")
    try:
        gun = int(odeme_gunu)
    except (TypeError, ValueError):
        raise CariHatasi("Ödeme günü geçerli bir sayı olmalı.")
    if odeme_kosulu == Cari.OdemeKosulu.SONRAKI_AY_GUNU and not (1 <= gun <= 31):
        raise CariHatasi("Sonraki ayın günü 1-31 arasında olmalı.")
    if odeme_kosulu == Cari.OdemeKosulu.GUN_SONRA and not (0 <= gun <= 365):
        raise CariHatasi("Gün sayısı 0-365 arasında olmalı.")
    return odeme_kosulu, gun


def vade_hesapla(cari, fatura_tarihi):
    """Carinin ödeme koşulu tanımlıysa fatura tarihine göre vade tarihini hesaplar;
    koşul tanımlı değilse ``None`` döner (vade elle girilir). ``SONRAKI_AY_GUNU``'nde
    gün o ayda yoksa ayın son gününe kırpılır (ör. 31 -> 30 Kasım); Aralık'ta kesilen
    fatura bir sonraki yılın Ocak'ına düşer."""
    kosul = cari.odeme_kosulu
    if not kosul:
        return None
    if kosul == Cari.OdemeKosulu.PESIN:
        return fatura_tarihi
    if kosul == Cari.OdemeKosulu.GUN_SONRA:
        return fatura_tarihi + datetime.timedelta(days=cari.odeme_gunu)
    if kosul == Cari.OdemeKosulu.SONRAKI_AY_GUNU:
        yil = fatura_tarihi.year + (1 if fatura_tarihi.month == 12 else 0)
        ay = 1 if fatura_tarihi.month == 12 else fatura_tarihi.month + 1
        son_gun = calendar.monthrange(yil, ay)[1]
        return datetime.date(yil, ay, min(cari.odeme_gunu, son_gun))
    return None


def _alanlar(*, kisa_ad, vergi_dairesi, vkn_tckn, tax_id, telefon, telefon_2,
            telefon_whatsapp=False, telefon_2_whatsapp=False,
            eposta, web, ilgili_kisi, kep_adresi, adres, para_birimi, kur_tipi=None,
            kur_degerleme=None, kur_farki_hedefi=None,
            kredi_limiti=None, iskonto_yuzdesi, odeme_kosulu=None, odeme_gunu=None,
            notlar, ulke, sehir, fason_faturasiz=None, tip=None):
    """Ortak alan hazırlığı (create/update paylaşır). (dict, uyarılar) döner. ``kur_tipi``
    opsiyonel — verilmezse (mevcut çağıranlar: aday->cari dönüşümü, veri taşıma scriptleri,
    testler) varsayılan MB_ALIS kullanılır."""
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    kur_tipi = kur_tipi or Cari.KurTipi.MB_ALIS
    if kur_tipi not in Cari.KurTipi.values:
        raise CariHatasi("Geçersiz kur tipi.")
    kur_degerleme = kur_degerleme or Cari.DegerlemeKurali.OTOMATIK
    if kur_degerleme not in Cari.DegerlemeKurali.values:
        raise CariHatasi("Geçersiz kur değerleme seçeneği.")
    kur_farki_hedefi = kur_farki_hedefi or Cari.KurFarkiHedefi.OTOMATIK
    if kur_farki_hedefi not in Cari.KurFarkiHedefi.values:
        raise CariHatasi("Geçersiz kur farkı hedefi.")
    web_norm = web_normalize(web)
    if web_norm is None:
        raise CariHatasi(f"Geçersiz web adresi: {(web or '').strip()}")
    odeme_kosulu, odeme_gunu = _odeme_kosulu_dogrula(odeme_kosulu, odeme_gunu)
    iso2 = (ulke.kod if ulke else "") or "TR"
    uyarilar = []
    telefon_deger = _telefon_isle(telefon, iso2, "Telefon", uyarilar)
    telefon_2_deger = _telefon_isle(telefon_2, iso2, "Telefon 2", uyarilar)
    veri = dict(
        kisa_ad=buyuk_harf_tr((kisa_ad or "").strip()),
        vergi_dairesi=buyuk_harf_tr((vergi_dairesi or "").strip()),
        vkn_tckn=(vkn_tckn or "").strip(), tax_id=(tax_id or "").strip(),
        telefon=telefon_deger, telefon_whatsapp=bool(telefon_whatsapp),
        telefon_2=telefon_2_deger, telefon_2_whatsapp=bool(telefon_2_whatsapp),
        eposta=(eposta or "").strip().lower(), web=web_norm,
        ilgili_kisi=buyuk_harf_tr((ilgili_kisi or "").strip()),
        kep_adresi=(kep_adresi or "").strip(),
        ulke=ulke, sehir=sehir, adres=buyuk_harf_tr((adres or "").strip()),
        para_birimi=para_birimi, kur_tipi=kur_tipi, kur_degerleme=kur_degerleme,
        kur_farki_hedefi=kur_farki_hedefi,
        iskonto_yuzdesi=_para_dogrula(iskonto_yuzdesi, "İskonto"),
        odeme_kosulu=odeme_kosulu, odeme_gunu=odeme_gunu,
        notlar=(notlar or "").strip(),
    )
    if fason_faturasiz is not None:                 # verilmezse (aday dönüşümü, scriptler) mevcut değer korunur / varsayılan False
        veri["fason_faturasiz"] = bool(fason_faturasiz)
    if kredi_limiti is not None:                    # Kredi/Risk Limiti ekrandan kalktı (2026-10-13): verilmezse mevcut değer korunur
        veri["kredi_limiti"] = _para_dogrula(kredi_limiti, "Kredi limiti")
    if tip is not None:                             # verilmezse mevcut tip korunur
        if tip not in ("", *Cari.Tip.values):
            raise CariHatasi("Geçersiz cari tipi.")
        hata = tip_vkn_uyumsuzlugu(tip, veri["vkn_tckn"])
        if hata:
            raise CariHatasi(hata)
        veri["tip"] = tip
    return veri, uyarilar


def tip_vkn_uyumsuzlugu(tip, vkn) -> str:
    """Cari tipi ile VKN/TCKN hane sayısı uyumu: TÜZEL → VKN (10 hane), ŞAHIS → TCKN (11 hane). Uyumluysa ya da biri boşsa ''; değilse hata metni."""
    vkn = (vkn or "").strip()
    if not tip or not vkn:
        return ""
    if tip == Cari.Tip.TUZEL and len(vkn) != 10:
        return "Tüzel cari için 10 haneli VKN girilmeli (11 haneli TCKN için Şahıs seçin)."
    if tip == Cari.Tip.SAHIS and len(vkn) != 11:
        return "Şahıs cari için 11 haneli TCKN girilmeli (10 haneli VKN için Tüzel seçin)."
    return ""


def _merkez_adres_esitle(cari: Cari, kullanici=None) -> int:
    """Cari adresi (ülke/şehir/adres) değişince YALNIZ "MERKEZ ADRES" adlı sevk adresi cari adresiyle eşitlenir (başka sevk adreslerine dokunulmaz;
    yoksa açılmaz). Cari adresi tamamen boşaltıldıysa sevk adresi boşaltılmaz. Güncellenen sayıyı döner."""
    if not (cari.ulke_id or cari.sehir_id or (cari.adres or "").strip()):
        return 0
    n = 0
    for sv in CariSevkAdresi.objects.filter(cari=cari, silindi=False, ad="MERKEZ ADRES"):
        sv.ulke_id, sv.sehir_id, sv.adres = cari.ulke_id, cari.sehir_id, cari.adres
        sv.updated_by = kullanici
        sv.save(update_fields=["ulke", "sehir", "adres", "updated_by", "updated_at"])
        n += 1
    return n


def cari_doviz_hareketli_mi(cari: Cari) -> bool:
    """Carinin muhasebe hesabında TL dışı işlem PB'li hareket var mı."""
    if not cari.muhasebe_kodu:
        return False
    from core.models import YevmiyeSatir
    return YevmiyeSatir.objects.filter(hesap_id=cari.muhasebe_kodu, silindi=False, fis__silindi=False).exclude(islem_pb="TRY").exists()


def kur_alanlari_gorunur(cari: Cari, doviz_hareketli=None) -> bool:
    """Kur alanları (Kur Tipi / Değerleme / Farkı Hedefi / Cari Kur Yöntemi) yalnız anlamlıysa görünür: ana PB TL dışı ya da TL carinin döviz hareketi var
    ya da kur ayarı varsayılan dışı (kayıtlı ayar gizlenip unutulmasın). ``doviz_hareketli`` verilirse sorgu yapılmaz."""
    if cari.para_birimi != "TRY":
        return True
    if (cari.kur_tipi, cari.kur_degerleme, cari.kur_farki_hedefi) != (Cari.KurTipi.MB_ALIS, Cari.DegerlemeKurali.OTOMATIK, Cari.KurFarkiHedefi.OTOMATIK):
        return True
    return cari_doviz_hareketli_mi(cari) if doviz_hareketli is None else bool(doviz_hareketli)


def cari_son_temas_ayarla(cari: Cari, tarih, kullanici=None) -> Cari:
    """Elle girilen son temas tarihi (None = temizle). Gelecek tarih olamaz."""
    if cari.silindi:
        raise CariHatasi("Silinmiş cari düzenlenemez.")
    if tarih is not None and tarih > timezone.localdate():
        raise CariHatasi("Son temas tarihi gelecekte olamaz.")
    cari.son_temas_tarihi = tarih
    cari.updated_by = kullanici
    cari.save(update_fields=["son_temas_tarihi", "updated_by", "updated_at"])
    return cari


def cari_olustur(*, unvan, kategori_id=None, kod=None, kullanici=None, **kw) -> Cari:
    """``mukerrer_onayi`` (kw): yönetici aynı VKN/TCKN'li ikinci karta onay verdi. Kod üretimi kategori satırı kilitlenerek sıraya girer;
    eş zamanlı çakışmada (kod benzersizliği) birkaç kez yeniden denenir, olmazsa anlaşılır mesaj verilir."""
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise CariHatasi("Unvan boş olamaz.")
    mukerrer_onayi = bool(kw.pop("mukerrer_onayi", False))
    kategori = None
    if kategori_id:
        kategori = CariKategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise CariHatasi("Kategori bulunamadı.")
        if kategori.ust_id is None:
            raise CariHatasi("Cari yalnız alt kategoriye bağlanabilir; üst kategori seçilemez.")
    _vergi_benzersiz(kw.get("vkn_tckn"), kw.get("tax_id"), mukerrer_onayi=mukerrer_onayi, kullanici=kullanici)
    veri, uyarilar = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_whatsapp", "telefon_2", "telefon_2_whatsapp",
                        "eposta", "web", "ilgili_kisi", "kep_adresi", "adres",
                        "para_birimi", "kur_tipi", "kur_degerleme", "kur_farki_hedefi", "kredi_limiti",
                        "iskonto_yuzdesi", "odeme_kosulu", "odeme_gunu", "notlar", "fason_faturasiz", "tip")})
    acik_kod = (kod or "").strip()
    for _deneme in range(5):
        try:
            # Cari kaydı + muhasebe hesabı açma TEK transaction'da: hesap açılamazsa cari de sessizce hesapsız kalmasın diye tamamı geri alınır.
            with transaction.atomic():
                if kategori is not None:
                    list(CariKategori.objects.select_for_update().filter(pk=kategori.pk))      # kategori bazında kod üretimi sıraya girer
                kod_ = acik_kod or sonraki_cari_kodu(kategori)
                _kod_hesap_cakisma(kod_)
                cari = Cari.objects.create(kod=kod_, unvan=unvan, kategori=kategori,
                                           created_by=kullanici, updated_by=kullanici, **veri)
                cari.telefon_uyarilari = uyarilar
                if cari.ulke_id or cari.sehir_id or cari.adres:
                    # Cari'nin kendi adresi doluysa, ilk sevk adresi olarak "Merkez Adres" adıyla otomatik + varsayılan eklenir.
                    sevk_adresi_ekle(cari, ad="Merkez Adres", ulke_id=cari.ulke_id, sehir_id=cari.sehir_id,
                                     adres=cari.adres, varsayilan=True, kullanici=kullanici)
                try:
                    muh = muhasebe_hesabi_ac(cari, kullanici=kullanici)   # hesap planında otomatik aç
                except hp.HesapHatasi as e:
                    raise CariHatasi(f"Muhasebe hesabı açılamadı, cari kaydedilmedi: {e}")
                if muh and cari.muhasebe_kodu != muh:
                    cari.muhasebe_kodu = muh
                    cari.save(update_fields=["muhasebe_kodu"])
            return cari
        except IntegrityError:
            if acik_kod:
                raise CariHatasi(f"Cari kodu zaten kayıtlı: {acik_kod}")
            continue                                                            # eş zamanlı çakışma: yeni kodla tekrar dene
    raise CariHatasi("Cari kodu üretilemedi (eş zamanlı kayıt çakışması); lütfen birkaç saniye sonra tekrar deneyin.")


def muhasebe_hesabi_ac(cari: Cari, kullanici=None) -> str:
    """Cari kodundan muhasebe hesabını (eksik ara hesaplarla birlikte) hesap planında
    açar ve noktalı muhasebe kodunu döndürür. ÜST grup/kalem/parasal üstten miras alınır.
    Ara hesap adı = cari kategorisi, yaprak hesap adı = cari unvanı. İdempotent (var
    olanı yeniden açmaz).

    Yalnız kod yapısı hesap planına UYGULANAMIYORSA (kategorisiz cari -> ``CAR-0001``
    gibi rakamsal olmayan kod, ya da kök hesap — 320 vb. — hiç yoksa) boş döner; bu
    cari türü için otomatik hesap baştan beklenmez, HATA DEĞİLDİR. Kod rakamsal VE kök
    mevcutken hesap yine de açılamıyorsa (örn. hp.HesapHatasi) artık burada SESSİZCE
    YUTULMAZ — istisna olduğu gibi çağırana (cari_olustur) yükselir; orada kullanıcıya
    açık bir hata olarak gösterilmeli (bkz. ALTIN KURAL: cari sessizce hesapsız kalmaz)."""
    kod = (cari.kod or "").strip()
    if not kod:
        return ""
    seg = kod.replace("-", ".").split(".")
    if len(seg) < 2 or not all(s.isdigit() for s in seg):
        return ""   # ör. CAR-0001 -> hesap planı kod kuralına uymaz (kategorisiz cari)
    noktali = ".".join(seg)
    if not HesapPlani.objects.filter(hesap_kodu=seg[0], silindi=False).exists():
        return ""   # kök hesap (320 vb.) yoksa açma
    for i in range(1, len(seg)):
        prefix = ".".join(seg[:i + 1])
        ust = ".".join(seg[:i])
        if HesapPlani.objects.filter(hesap_kodu=prefix, silindi=False).exists():
            continue
        leaf = (i == len(seg) - 1)
        ad = cari.unvan if leaf else (cari.kategori.ad if cari.kategori_id else prefix)
        hp.hesap_olustur(kod=prefix, ad=ad, ust_kodu=ust, kullanici=kullanici)
    return noktali


def cari_hareketli_mi(cari: Cari) -> bool:
    """Cari'nin (yaprak) muhasebe hesabında aktif yevmiye hareketi var mı? Kategori
    (dolayısıyla kod+hesap) değişikliğini kilitlemek için kullanılır — hesap_sil'in
    kendi "yevmiyeli silinemez" kuralıyla birebir aynı ölçüt."""
    if not cari.muhasebe_kodu:
        return False
    from core.models import YevmiyeSatir
    return YevmiyeSatir.objects.filter(hesap_id=cari.muhasebe_kodu, silindi=False).exists()


def cari_guncelle(cari: Cari, *, unvan, kategori_id=None, kullanici=None, **kw) -> Cari:
    """Kod SABİTTİR — kategori değişmediği sürece. Kategori değişirse (yalnız cari
    hareketsizken izinli — bkz. cari_hareketli_mi): yeni kategoriye göre kod + muhasebe
    hesabı yeniden üretilir, eski yaprak hesap (hareketsiz olduğu için) soft-delete
    edilir. Hareketli caride kategori değiştirme girişimi açıkça reddedilir (sessizce
    yok sayılmaz) — 2026-10-03 canlı olayı: kategori değişince kod/hesap eski kategoride
    kalıp tutarsızlık oluşturuyordu."""
    if cari.silindi:
        raise CariHatasi("Silinmiş cari düzenlenemez.")
    mukerrer_onayi = bool(kw.pop("mukerrer_onayi", False))
    pb_degisim_onayi = bool(kw.pop("pb_degisim_onayi", False))
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise CariHatasi("Unvan boş olamaz.")
    eski_unvan = cari.unvan
    eski_adres = (cari.ulke_id, cari.sehir_id, cari.adres)
    kategori = None
    if kategori_id:
        kategori = CariKategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise CariHatasi("Kategori bulunamadı.")
        if kategori.ust_id is None:
            raise CariHatasi("Cari yalnız alt kategoriye bağlanabilir; üst kategori seçilemez.")

    kategori_degisti = (kategori.pk if kategori else None) != cari.kategori_id
    if kategori_degisti and cari_hareketli_mi(cari):
        raise CariHatasi("Hareketi olan carinin kategorisi değiştirilemez.")

    eski_vkn = (cari.vkn_tckn or "").strip()
    if (kw.get("vkn_tckn") or "").strip() != eski_vkn:                  # VKN'ye dokunulmadıysa eski (olası ortak) kayıt engel değil
        _vergi_benzersiz(kw.get("vkn_tckn"), None, haric_pk=cari.pk, mukerrer_onayi=mukerrer_onayi, kullanici=kullanici)
    _vergi_benzersiz(None, kw.get("tax_id"), haric_pk=cari.pk)
    veri, uyarilar = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_whatsapp", "telefon_2", "telefon_2_whatsapp",
                        "eposta", "web", "ilgili_kisi", "kep_adresi", "adres",
                        "para_birimi", "kur_tipi", "kur_degerleme", "kur_farki_hedefi", "kredi_limiti",
                        "iskonto_yuzdesi", "odeme_kosulu", "odeme_gunu", "notlar", "fason_faturasiz", "tip")})
    if veri["para_birimi"] != cari.para_birimi and cari_hareketli_mi(cari):
        # Ana para birimi kilidi: hareketli carinin PB'si yalnız yönetici + mevcut havuz bakiyelerini gören onayla değişir.
        from core.yetki import yonetici_mi
        if not yonetici_mi(kullanici):
            raise CariHatasi("Hareketi olan carinin ana para birimi yalnız yönetici tarafından değiştirilebilir.")
        if not pb_degisim_onayi:
            raise CariPbOnayGerekli(f"{cari.kod}: ana para birimi {cari.para_birimi} → {veri['para_birimi']} değişiyor; mevcut havuz bakiyelerini "
                                    "kontrol edip onaylayın.", para_birimi_havuzlari(cari), cari.para_birimi, veri["para_birimi"])

    with transaction.atomic():
        eski_muhasebe_kodu = cari.muhasebe_kodu
        cari.unvan = unvan
        cari.kategori = kategori
        for alan, deger in veri.items():
            setattr(cari, alan, deger)
        if kategori_degisti:
            cari.kod = sonraki_cari_kodu(kategori)
            cari.muhasebe_kodu = ""
        cari.updated_by = kullanici
        cari.save()
        cari.telefon_uyarilari = uyarilar
        if (cari.ulke_id, cari.sehir_id, cari.adres) != eski_adres:
            _merkez_adres_esitle(cari, kullanici)

        if kategori_degisti:
            if eski_muhasebe_kodu:
                try:
                    hp.hesap_sil(kod=eski_muhasebe_kodu, kullanici=kullanici)
                except hp.HesapHatasi:
                    pass   # beklenmez (hareketsizlik yukarıda doğrulandı) — savunmacı
            try:
                muh = muhasebe_hesabi_ac(cari, kullanici=kullanici)
            except hp.HesapHatasi as e:
                raise CariHatasi(f"Yeni muhasebe hesabı açılamadı: {e}")
            if muh and cari.muhasebe_kodu != muh:
                cari.muhasebe_kodu = muh
                cari.save(update_fields=["muhasebe_kodu"])
        elif cari.muhasebe_kodu and unvan != eski_unvan:
            # Muhasebe hesabının (yaprak) adını cari unvanıyla senkron tut.
            try:
                hp.hesap_adi_guncelle(kod=cari.muhasebe_kodu, yeni_ad=unvan,
                                      kullanici=kullanici)
            except hp.HesapHatasi:
                pass   # hesap silinmiş/bulunamamış olabilir — cari yine güncellenir
    return cari


SILME_ETIKETLERI = {
    "faturalar": "fatura", "teklif_siparisler": "teklif/sipariş/irsaliye", "numune_cikislari": "numune çıkışı",
    "fason_depolari": "fason deposu bağlantısı", "fason_fiyatlari": "fason fiyatı", "fason_donusleri": "fason dönüşü",
    "fason_siparisleri": "fason siparişi", "fason_operasyon_kayitlari": "fason operasyon kaydı", "tedarik_stoklari": "stok kartında tedarikçi",
    "kesinti_fisleri": "kesinti/masraf/virman fişi", "virman_karsi_fisleri": "virman fişi (karşı cari)",
}
SILME_ATLANAN = {"banka_hesaplari", "sevk_adresleri", "yetkililer", "aktiviteler", "kaynak_adaylar"}   # cari ile birlikte gider / bilgi amaçlı


def silme_engelleri(cari: Cari) -> list:
    """Cari silinemez nedenleri: [(etiket, adet)]. Boş liste = silinebilir. Muhasebe hesabında (alt hesaplar dahil) yevmiye hareketi, ve Cari'ye
    bağlı TÜM silinmemiş belge/kayıtlar (fatura, teklif/sipariş/irsaliye, çek/senet, fason belgeleri, numune, tedarikçi bağı…; modelin FK'lerinden
    genel olarak türetilir) ile açık stok ayırması."""
    from core.models import YevmiyeSatir
    nedenler = []
    if cari.muhasebe_kodu:
        n = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False)
             .filter(Q(hesap_id=cari.muhasebe_kodu) | Q(hesap__hesap_kodu__startswith=cari.muhasebe_kodu + ".")).count())
        if n:
            nedenler.append(("yevmiye hareketi (muhasebe hesabında)", n))
    # Cari'ye bağlı tüm ilişkiler TEK sorguda (her biri skaler alt sorgu sayımı) — detay sayfasında ~25 ayrı COUNT yerine.
    from django.db.models import OuterRef, Subquery
    iliskiler, ano = [], {}
    for r in Cari._meta.related_objects:
        if not r.one_to_many:
            continue
        acc = r.get_accessor_name()
        if acc is None or acc in SILME_ATLANAN:
            continue
        qs = r.related_model._default_manager.filter(**{r.field.name: OuterRef("pk")})
        if any(f.name == "silindi" for f in r.related_model._meta.get_fields()):
            qs = qs.filter(silindi=False)
        ano[f"n_{len(iliskiler)}"] = Subquery(qs.order_by().values(r.field.name).annotate(c=Count("pk")).values("c")[:1])
        iliskiler.append((acc, r.related_model))
    if ano:
        sayimlar = Cari.objects.filter(pk=cari.pk).annotate(**ano).values(*ano.keys()).first() or {}
        for i, (acc, model) in enumerate(iliskiler):
            n = sayimlar.get(f"n_{i}") or 0
            if n:
                nedenler.append((SILME_ETIKETLERI.get(acc) or str(model._meta.verbose_name), n))
    from core.services import stok_ayirma
    n = stok_ayirma.acik_ayirmalar().filter(uretim_emri__kaynak_siparis__cari=cari).count()
    if n:
        nedenler.append(("açık stok ayırması (carinin açık siparişine bağlı)", n))
    return nedenler


def silme_engeli_metni(nedenler) -> str:
    return ", ".join(f"{n} {e}" for e, n in nedenler)


@transaction.atomic
def cari_sil(cari: Cari, kullanici=None) -> Cari:
    """Soft-delete — yalnız HİÇ hareketi/belgesi olmayan cari. Engel varsa ``CariSilmeEngeli`` (nedenler listesiyle; "Pasife al" önerilir).
    Muhasebe hesabı kapatılamazsa hata SESSİZCE yutulmaz: cari de silinmez (atomik)."""
    if cari.silindi:
        return cari
    nedenler = silme_engelleri(cari)
    if nedenler:
        raise CariSilmeEngeli(f"{cari.kod} silinemez: {silme_engeli_metni(nedenler)}. Bunun yerine carisini PASİFE alın "
                              "(geçmiş belge, ekstre ve raporlar etkilenmez).", nedenler)
    if cari.muhasebe_kodu and HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).exists():
        try:
            hp.hesap_sil(kod=cari.muhasebe_kodu, kullanici=kullanici)
        except hp.HesapHatasi as e:
            raise CariHatasi(f"Cari silinmedi: muhasebe hesabı ({cari.muhasebe_kodu}) kapatılamadı — {e}")
    cari.silindi = True
    cari.silindi_at = timezone.now()
    cari.updated_by = kullanici
    cari.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return cari


def cari_pasife_al(cari: Cari, kullanici=None) -> Cari:
    if cari.silindi:
        raise CariHatasi("Silinmiş cari pasife alınamaz.")
    if cari.aktif:
        cari.aktif = False
        cari.updated_by = kullanici
        cari.save(update_fields=["aktif", "updated_by", "updated_at"])
    return cari


def cari_aktiflestir(cari: Cari, kullanici=None) -> Cari:
    if cari.silindi:
        raise CariHatasi("Silinmiş cari aktifleştirilemez.")
    if not cari.aktif:
        cari.aktif = True
        cari.updated_by = kullanici
        cari.save(update_fields=["aktif", "updated_by", "updated_at"])
    return cari


def para_birimi_havuzlari(cari: Cari) -> list:
    """Carinin muhasebe hesabındaki (alt hesaplar dahil) PB havuz bakiyeleri: [{pb, borc_tl, alacak_tl, net_tl, doviz_net}] (TRY, USD, EUR, GBP sırasıyla;
    yalnız hareketli olanlar). doviz_net = o PB'deki (borç − alacak) işlem tutarı; TL havuzunda TL'nin kendisi."""
    from core.models import YevmiyeSatir
    if not cari.muhasebe_kodu:
        return []
    qs = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False)
          .filter(Q(hesap_id=cari.muhasebe_kodu) | Q(hesap__hesap_kodu__startswith=cari.muhasebe_kodu + "."))
          .values("islem_pb").annotate(tl_borc=Sum("borc"), tl_alacak=Sum("alacak"),
                                       d_borc=Sum("islem_tutari", filter=Q(borc__gt=0)), d_alacak=Sum("islem_tutari", filter=Q(alacak__gt=0))))
    sira = {"TRY": 0, "USD": 1, "EUR": 2, "GBP": 3}
    sonuc = []
    for r in qs:
        b, a = r["tl_borc"] or 0, r["tl_alacak"] or 0
        db, da = r["d_borc"] or 0, r["d_alacak"] or 0
        sonuc.append({"pb": r["islem_pb"], "borc_tl": b, "alacak_tl": a, "net_tl": b - a, "doviz_net": db - da})
    return sorted(sonuc, key=lambda x: sira.get(x["pb"], 9))


# --- Banka hesapları ---------------------------------------------------------
def aktif_bankalar(cari):
    return cari.banka_hesaplari.filter(silindi=False).order_by("-varsayilan", "banka_adi")


def _diger_varsayilanlari_kapat(cari, haric):
    cari.banka_hesaplari.filter(silindi=False, varsayilan=True).exclude(
        pk=haric.pk).update(varsayilan=False)


def banka_ekle(cari, *, banka_adi, hesap_sahibi="", iban="", swift="",
               para_birimi="TRY", aciklama="", varsayilan=False, kullanici=None) -> CariBanka:
    banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    if not banka_adi:
        raise CariHatasi("Banka adı boş olamaz.")
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    ilk = not cari.banka_hesaplari.filter(silindi=False).exists()
    b = CariBanka.objects.create(
        cari=cari, banka_adi=banka_adi,
        hesap_sahibi=buyuk_harf_tr((hesap_sahibi or "").strip()),
        iban=(iban or "").strip().upper().replace(" ", ""),
        swift=(swift or "").strip().upper(),
        para_birimi=para_birimi, aciklama=(aciklama or "").strip(),
        varsayilan=bool(varsayilan) or ilk,
        created_by=kullanici, updated_by=kullanici)
    if b.varsayilan:
        _diger_varsayilanlari_kapat(cari, b)
    return b


def banka_guncelle(banka: CariBanka, *, banka_adi, hesap_sahibi="", iban="", swift="",
                   para_birimi="TRY", aciklama="", varsayilan=False, kullanici=None) -> CariBanka:
    if banka.silindi:
        raise CariHatasi("Silinmiş banka hesabı düzenlenemez.")
    banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    if not banka_adi:
        raise CariHatasi("Banka adı boş olamaz.")
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    idi_varsayilan = banka.varsayilan
    banka.banka_adi = banka_adi
    banka.hesap_sahibi = buyuk_harf_tr((hesap_sahibi or "").strip())
    banka.iban = (iban or "").strip().upper().replace(" ", "")
    banka.swift = (swift or "").strip().upper()
    banka.para_birimi = para_birimi
    banka.aciklama = (aciklama or "").strip()
    banka.varsayilan = bool(varsayilan)
    banka.updated_by = kullanici
    banka.save()
    if banka.varsayilan:
        _diger_varsayilanlari_kapat(banka.cari, banka)
    elif idi_varsayilan and not banka.cari.banka_hesaplari.filter(
            silindi=False, varsayilan=True).exists():
        # Tek/son varsayılan hesabın işareti kaldırıldı -> kalan ilk hesabı varsayılan yap
        # (banka_sil'deki "her zaman bir varsayılan olsun" invaryantıyla aynı desen).
        kalan = banka.cari.banka_hesaplari.filter(silindi=False).exclude(
            pk=banka.pk).order_by("banka_adi").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return banka


def banka_sil(banka: CariBanka, kullanici=None) -> CariBanka:
    if banka.silindi:
        return banka
    cari = banka.cari
    idi_varsayilan = banka.varsayilan
    banka.silindi = True
    banka.silindi_at = timezone.now()
    banka.varsayilan = False
    banka.updated_by = kullanici
    banka.save(update_fields=["silindi", "silindi_at", "varsayilan",
                              "updated_by", "updated_at"])
    if idi_varsayilan:   # kalan ilk hesabı varsayılan yap
        kalan = cari.banka_hesaplari.filter(silindi=False).order_by("banka_adi").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return banka


# --- Sevk (teslimat) adresleri -----------------------------------------------
def aktif_sevk_adresleri(cari):
    return cari.sevk_adresleri.filter(silindi=False).order_by("-varsayilan", "ad")


def _diger_sevk_varsayilanlari_kapat(cari, haric):
    cari.sevk_adresleri.filter(silindi=False, varsayilan=True).exclude(
        pk=haric.pk).update(varsayilan=False)


def sevk_adresi_ekle(cari, *, ad, ulke_id=None, sehir_id=None, adres="",
                     varsayilan=False, kullanici=None) -> CariSevkAdresi:
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise CariHatasi("Adres adı boş olamaz.")
    ilk = not cari.sevk_adresleri.filter(silindi=False).exists()
    s = CariSevkAdresi.objects.create(
        cari=cari, ad=ad, ulke=_ulke(ulke_id), sehir=_sehir(sehir_id),
        adres=buyuk_harf_tr((adres or "").strip()),
        varsayilan=bool(varsayilan) or ilk,
        created_by=kullanici, updated_by=kullanici)
    if s.varsayilan:
        _diger_sevk_varsayilanlari_kapat(cari, s)
    return s


def sevk_adresi_guncelle(sevk: CariSevkAdresi, *, ad, ulke_id=None, sehir_id=None, adres="",
                         varsayilan=False, kullanici=None) -> CariSevkAdresi:
    if sevk.silindi:
        raise CariHatasi("Silinmiş sevk adresi düzenlenemez.")
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise CariHatasi("Adres adı boş olamaz.")
    idi_varsayilan = sevk.varsayilan
    sevk.ad = ad
    sevk.ulke = _ulke(ulke_id)
    sevk.sehir = _sehir(sehir_id)
    sevk.adres = buyuk_harf_tr((adres or "").strip())
    sevk.varsayilan = bool(varsayilan)
    sevk.updated_by = kullanici
    sevk.save()
    if sevk.varsayilan:
        _diger_sevk_varsayilanlari_kapat(sevk.cari, sevk)
    elif idi_varsayilan and not sevk.cari.sevk_adresleri.filter(
            silindi=False, varsayilan=True).exists():
        # Tek/son varsayılan adresin işareti kaldırıldı -> kalan ilk adresi varsayılan yap
        # (banka_guncelle/banka_sil'deki "her zaman bir varsayılan olsun" invaryantıyla aynı desen).
        kalan = sevk.cari.sevk_adresleri.filter(silindi=False).exclude(
            pk=sevk.pk).order_by("ad").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return sevk


def sevk_adresi_sil(sevk: CariSevkAdresi, kullanici=None) -> CariSevkAdresi:
    if sevk.silindi:
        return sevk
    cari = sevk.cari
    idi_varsayilan = sevk.varsayilan
    sevk.silindi = True
    sevk.silindi_at = timezone.now()
    sevk.varsayilan = False
    sevk.updated_by = kullanici
    sevk.save(update_fields=["silindi", "silindi_at", "varsayilan",
                             "updated_by", "updated_at"])
    if idi_varsayilan:   # kalan ilk adresi varsayılan yap
        kalan = cari.sevk_adresleri.filter(silindi=False).order_by("ad").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return sevk


# --- Yetkili kişiler ---------------------------------------------------------
def aktif_yetkililer(cari):
    return cari.yetkililer.filter(silindi=False).order_by("ad_soyad")


def yetkili_ekle(cari, *, ad_soyad, unvan="", telefon="", eposta="", notlar="",
                 whatsapp=False, kullanici=None) -> CariYetkili:
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise CariHatasi("Ad soyad boş olamaz.")
    iso2 = (cari.ulke.kod if cari.ulke_id else "") or "TR"
    uyarilar = []
    telefon_deger = _telefon_isle(telefon, iso2, "Telefon", uyarilar)
    yetkili = CariYetkili.objects.create(
        cari=cari, ad_soyad=ad_soyad, unvan=buyuk_harf_tr((unvan or "").strip()),
        telefon=telefon_deger, eposta=(eposta or "").strip().lower(),
        notlar=(notlar or "").strip(), whatsapp=bool(whatsapp),
        created_by=kullanici, updated_by=kullanici)
    yetkili.telefon_uyarilari = uyarilar
    return yetkili


def yetkili_guncelle(yetkili: CariYetkili, *, ad_soyad, unvan="", telefon="",
                     eposta="", notlar="", whatsapp=False, kullanici=None) -> CariYetkili:
    if yetkili.silindi:
        raise CariHatasi("Silinmiş yetkili düzenlenemez.")
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise CariHatasi("Ad soyad boş olamaz.")
    iso2 = (yetkili.cari.ulke.kod if yetkili.cari.ulke_id else "") or "TR"
    uyarilar = []
    telefon_deger = _telefon_isle(telefon, iso2, "Telefon", uyarilar)
    yetkili.ad_soyad = ad_soyad
    yetkili.unvan = buyuk_harf_tr((unvan or "").strip())
    yetkili.telefon = telefon_deger
    yetkili.eposta = (eposta or "").strip().lower()
    yetkili.notlar = (notlar or "").strip()
    yetkili.whatsapp = bool(whatsapp)
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["ad_soyad", "unvan", "telefon", "eposta", "notlar", "whatsapp",
                                "updated_by", "updated_at"])
    yetkili.telefon_uyarilari = uyarilar
    return yetkili


def yetkili_sil(yetkili: CariYetkili, kullanici=None) -> CariYetkili:
    if yetkili.silindi:
        return yetkili
    yetkili.silindi = True
    yetkili.silindi_at = timezone.now()
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return yetkili


# --- Aktiviteler (görüşme/temas kayıtları) -----------------------------------
def aktif_aktiviteler(cari):
    return (cari.aktiviteler.filter(silindi=False)
            .select_related("created_by")
            .prefetch_related(Prefetch(
                "ekler", queryset=CariAktiviteEk.objects.filter(silindi=False)))
            .order_by("-tarih", "-id"))


def aktivite_ekle(cari, *, tarih, tur, aciklama, kullanici=None) -> CariAktivite:
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise CariHatasi("Açıklama boş olamaz.")
    if tur not in CariAktivite.Tur.values:
        raise CariHatasi("Geçersiz aktivite türü.")
    return CariAktivite.objects.create(
        cari=cari, tarih=tarih, tur=tur, aciklama=aciklama,
        created_by=kullanici, updated_by=kullanici)


def aktivite_guncelle(aktivite: CariAktivite, *, tarih, tur, aciklama,
                      kullanici=None) -> CariAktivite:
    if aktivite.silindi:
        raise CariHatasi("Silinmiş aktivite düzenlenemez.")
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise CariHatasi("Açıklama boş olamaz.")
    if tur not in CariAktivite.Tur.values:
        raise CariHatasi("Geçersiz aktivite türü.")
    aktivite.tarih = tarih
    aktivite.tur = tur
    aktivite.aciklama = aciklama
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["tarih", "tur", "aciklama", "updated_by", "updated_at"])
    return aktivite


def aktivite_sil(aktivite: CariAktivite, kullanici=None) -> CariAktivite:
    if aktivite.silindi:
        return aktivite
    aktivite.silindi = True
    aktivite.silindi_at = timezone.now()
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return aktivite


def aktivite_ek_ekle(aktivite: CariAktivite, *, dosya, kullanici=None) -> CariAktiviteEk:
    """Aktiviteye tek dosya ekler (çoklu yükleme view katmanında döngüyle bu fonksiyonu çağırır).
    Resim ise WebP'ye küçültülür (spec görsel invariant'ı: en uzun kenar ~1600px, ~%80 kalite);
    PDF olduğu gibi saklanır."""
    if aktivite.silindi:
        raise CariHatasi("Silinmiş aktiviteye dosya eklenemez.")
    ad = dosya.name or "dosya"
    uzanti = os.path.splitext(ad)[1].lower()
    if uzanti not in AKTIVITE_IZINLI_UZANTI:
        raise CariHatasi(f"Desteklenmeyen dosya türü: {ad} (yalnız resim veya PDF).")
    if dosya.size > AKTIVITE_MAKS_BOYUT:
        raise CariHatasi(f"Dosya çok büyük (10 MB üzeri): {ad}")
    if uzanti == ".pdf":
        saklanan = dosya
    else:
        try:
            saklanan = gorsel.kucult_webp(dosya, max_kenar=1600, kalite=80, ad="cari_aktivite")
        except Exception:
            raise CariHatasi(f"Geçersiz resim dosyası: {ad}")
    return CariAktiviteEk.objects.create(
        aktivite=aktivite, dosya=saklanan, orijinal_ad=ad,
        created_by=kullanici, updated_by=kullanici)


def aktivite_ek_sil(ek: CariAktiviteEk, kullanici=None) -> CariAktiviteEk:
    if ek.silindi:
        return ek
    ek.silindi = True
    ek.silindi_at = timezone.now()
    ek.updated_by = kullanici
    ek.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ek
