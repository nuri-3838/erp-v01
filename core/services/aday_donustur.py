"""Cariye Dönüştür — Aday'ı gerçek Cari'ye dönüştürme: (A) yeni cari aç, (B) mevcut cariye
bağla + olası eşleşme bulma. AdayYetkili/CariYetkili ve AdayAktivite/CariAktivite BİREBİR
aynı alan şekline sahip olduğu için aktarım basit bir alan kopyası (bkz. models.py dosya
başı ilke — ileride model şekli değişirse burası da güncellenmeli).

Kategori önerisi ve kod_yolu araması hariç, muhasebe hesabı açma / cari kodu atama işini
BU MODÜL yapmaz — (A) her zaman core.services.cari.cari_olustur'u çağırır (paralel yol
yok); (B) muhasebe hesabına hiç dokunmaz (spec kararı)."""
from __future__ import annotations

import re

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (
    AdayAktiviteEk, AdayAsamaTanim, AdayMusteri, CariAktivite, CariAktiviteEk, CariYetkili)
from core.services import cari as cari_servis
from core.tarih import tr_bugun


class AdayDonusturHatasi(ValueError):
    """Cariye dönüştürme kural ihlali (Türkçe mesaj)."""


_ALAN_ETIKET = {
    "telefon": "Telefon", "telefon_2": "Telefon 2", "eposta": "E-posta", "web": "Web",
    "adres": "Adres", "ilgili_kisi": "İlgili Kişi",
}

# Şirket türü ekleri + bağlaç — unvan eşleşmesinden ATILIR (spec listesi + "VE" bağlacı,
# aksi halde "SAN ⟨VE⟩ TİC" içindeki VE tek başına kalıp gürültü katar).
_UNVAN_EK_KALIPLARI = [
    r"\bLTD\b", r"\bŞTİ\b", r"\bSAN\b", r"\bTİC\b", r"\bAŞ\b", r"\bLLC\b", r"\bCO\b",
    r"\bGMBH\b", r"\bDOO\b", r"\bVE\b",
]
_UNVAN_ANAHTAR_KELIME_SAYISI = 4   # "ilk anlamlı kısım" — normalize sonrası ilk N kelime


def _unvan_anahtar(unvan):
    """Eşleşme anahtarı: TR büyük harf, parantez içi ve noktalama atılır, bilinen şirket
    ekleri çıkarılır, kalan metnin İLK ANLAMLI KISMI (ilk birkaç kelime) alınır — spec
    örneği: '5G GROUP PLASTİK METAL SAN. VE TİC. LTD. ŞTİ. (ELMOP)' ve '5G GROUP PLASTİK
    METAL SAN VE TİC LTD ŞTİ.' aynı anahtara ('5G GROUP PLASTİK METAL') indirgenir."""
    b = buyuk_harf_tr(unvan or "")
    b = re.sub(r"\([^)]*\)", " ", b)               # parantez içi (ör. ticari takma ad)
    b = re.sub(r"[.,/\\\-]", " ", b)                 # noktalama -> boşluk
    for kalip in _UNVAN_EK_KALIPLARI:
        b = re.sub(kalip, " ", b)
    kelimeler = re.sub(r"\s+", " ", b).strip().split(" ")
    kelimeler = [k for k in kelimeler if k]
    return " ".join(kelimeler[:_UNVAN_ANAHTAR_KELIME_SAYISI])


def _telefon_son9(tel):
    rakam = re.sub(r"\D", "", tel or "")
    return rakam[-9:] if len(rakam) >= 9 else None


def _aday_gecerli_epostalar(aday):
    """(birinci, ikinci) — geçersiz işaretli veya boş adres None olur; 'geçersiz işaretli
    adres asla taşınmaz' kuralı TEK burada uygulanır."""
    e1 = aday.eposta.lower() if aday.eposta and not aday.eposta_gecersiz else None
    e2 = aday.eposta_2.lower() if aday.eposta_2 and not aday.eposta_2_gecersiz else None
    return e1, e2


def _web_alan_adi(web):
    w = (web or "").strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    return w.rstrip("/")


def eslesen_cariler(aday):
    """Aynı firmanın zaten cari olup olmadığını gösteren olası eşleşmeler — normalize unvan,
    telefon (son 9 hane), geçerli e-posta, web alan adı. Aktif (silinmemiş) cariler taranır
    (canlıda ~20 kayıt — DB'de değil Python'da karşılaştırmak makul, spec'te sorgu sayısı
    şartı yok). [{"cari":, "sebep": "..."}] döner, unvana göre sıralı."""
    aday_anahtar = _unvan_anahtar(aday.unvan)
    aday_tel = {t for t in (_telefon_son9(aday.telefon), _telefon_son9(aday.telefon_2)) if t}
    aday_e1, aday_e2 = _aday_gecerli_epostalar(aday)
    aday_epostalar = {e for e in (aday_e1, aday_e2) if e}
    aday_web = _web_alan_adi(aday.web)

    sonuclar = []
    for cari in cari_servis.aktif_cariler():
        sebepler = []
        if aday_anahtar and _unvan_anahtar(cari.unvan) == aday_anahtar:
            sebepler.append("Unvan benzer")
        cari_tel = {t for t in (_telefon_son9(cari.telefon), _telefon_son9(cari.telefon_2)) if t}
        if aday_tel & cari_tel:
            sebepler.append("Telefon eşleşiyor")
        if aday_epostalar and cari.eposta and cari.eposta.lower() in aday_epostalar:
            sebepler.append("E-posta eşleşiyor")
        if aday_web and _web_alan_adi(cari.web) == aday_web:
            sebepler.append("Web adresi eşleşiyor")
        if sebepler:
            sonuclar.append({"cari": cari, "sebep": ", ".join(sebepler)})
    return sonuclar


def eslesen_adaylar(aday):
    """eslesen_cariler ile AYNI eşleşme mantığı (unvan/telefon/e-posta/web), hedef DİĞER
    aktif ADAYLAR — mükerrer aday kaydı uyarısı için (core/views.py aday_musteri_ekle/
    duzenle, core.services.aday.eslesen_kayitlar). Kendisi (pk) hariç tutulur — yeni
    (kaydedilmemiş) bir aday taslağında ``aday.pk`` None olduğu için ``exclude(pk=None)``
    hiçbir satırı elemez, doğru davranış."""
    aday_anahtar = _unvan_anahtar(aday.unvan)
    aday_tel = {t for t in (_telefon_son9(aday.telefon), _telefon_son9(aday.telefon_2)) if t}
    aday_e1, aday_e2 = _aday_gecerli_epostalar(aday)
    aday_epostalar = {e for e in (aday_e1, aday_e2) if e}
    aday_web = _web_alan_adi(aday.web)

    sonuclar = []
    for diger in AdayMusteri.objects.filter(silindi=False).exclude(pk=aday.pk):
        sebepler = []
        if aday_anahtar and _unvan_anahtar(diger.unvan) == aday_anahtar:
            sebepler.append("Unvan benzer")
        diger_tel = {t for t in (_telefon_son9(diger.telefon), _telefon_son9(diger.telefon_2)) if t}
        if aday_tel & diger_tel:
            sebepler.append("Telefon eşleşiyor")
        diger_e1, diger_e2 = _aday_gecerli_epostalar(diger)
        diger_epostalar = {e for e in (diger_e1, diger_e2) if e}
        if aday_epostalar & diger_epostalar:
            sebepler.append("E-posta eşleşiyor")
        if aday_web and _web_alan_adi(diger.web) == aday_web:
            sebepler.append("Web adresi eşleşiyor")
        if sebepler:
            sonuclar.append({"aday": diger, "sebep": ", ".join(sebepler)})
    return sonuclar


def eslesen_kayitlar(aday):
    """eslesen_cariler + eslesen_adaylar birleşik sonucu — bir aday KAYDEDİLMEDEN ÖNCE
    mükerrer kayıt uyarısı için TEK giriş noktası (core.services.aday.aday_musteri_olustur/
    guncelle çağırır). Cariye zaten dönüştüğü cari varsa (aday.cari_id) sonuçtan çıkarılır
    (kendi dönüşümüyle eşleşmiş gibi görünmesin). [{"tur": "Aday"|"Cari", "nesne":,
    "sebep": "..."}] döner, unvana göre sıralı."""
    cari_sonuclar = [
        {"tur": "Cari", "nesne": e["cari"], "sebep": e["sebep"]}
        for e in eslesen_cariler(aday) if e["cari"].pk != aday.cari_id
    ]
    aday_sonuclar = [
        {"tur": "Aday", "nesne": e["aday"], "sebep": e["sebep"]}
        for e in eslesen_adaylar(aday)
    ]
    return sorted(cari_sonuclar + aday_sonuclar, key=lambda x: x["nesne"].unvan)


def donusturme_engeli_var_mi(aday):
    """Dönüştürmeyi tamamen engelleyen bir durum varsa açıklayıcı Türkçe mesaj, yoksa None.
    Engel artık aday.tip.cariye_donusturulebilir'den okunur (bkz. CRM > Tipler ekranı,
    core.models.AdayTipTanim) — sabit kod listesi YOK, düzenlenebilir tanım tablosu."""
    if not aday.tip.cariye_donusturulebilir:
        return (f"'{aday.tip.ad}' tipindeki adaylar cariye dönüştürülemez "
                f"(gerçek müşteri/tedarikçi adayı değil).")
    return None


def kategori_onerisi(aday):
    """aday.tip.cari_kategori_yurtici/yurtdisi'den CariKategori önerisi; tanımlı değilse None
    (kullanıcı elle seçer). 'Yurtdışı' = ülke seçili VE kodu TR değil; ülke boşsa güvenli
    taraf seçilir (yurtdışı, VKN zorunlu kılınmaz)."""
    turkiye_mi = bool(aday.ulke_id) and aday.ulke.kod == "TR"
    return aday.tip.cari_kategori_yurtici if turkiye_mi else aday.tip.cari_kategori_yurtdisi


def yeni_cari_baslangic_degerleri(aday):
    """(A) Yeni cari aç formunun adaydan ÖNCEDEN DOLDURULMUŞ başlangıç değerleri (spec
    tablosu) — CariForm alan adlarıyla birebir eşleşir, view doğrudan initial= olarak geçer."""
    e1, e2 = _aday_gecerli_epostalar(aday)
    eposta = e1 or e2 or ""
    notlar = f"İkinci e-posta: {e2}" if (e2 and eposta != e2) else ""
    return dict(
        unvan=aday.unvan, kategori=kategori_onerisi(aday),
        telefon=aday.telefon, telefon_whatsapp=aday.telefon_whatsapp,
        telefon_2=aday.telefon_2, telefon_2_whatsapp=aday.telefon_2_whatsapp,
        eposta=eposta, web=aday.web, adres=aday.adres,
        ulke=aday.ulke, sehir=aday.sehir, ilgili_kisi=aday.ilgili_kisi,
        para_birimi=aday.para_birimi, iskonto_yuzdesi=aday.iskonto_yuzdesi,
        notlar=notlar,
    )


def doldurulacak_alanlar(aday, cari):
    """(B) önizlemesi: caride BOŞ olan, adayda DOLU olan alanların {alan: değer} sözlüğü —
    carinin dolu alanları ASLA ezilmez (spec kuralı). whatsapp işareti yalnız kendi telefon
    alanıyla BİRLİKTE doldurulur (numarasız işaret anlamsız)."""
    doldurulacak = {}
    if not cari.telefon and aday.telefon:
        doldurulacak["telefon"] = aday.telefon
        doldurulacak["telefon_whatsapp"] = aday.telefon_whatsapp
    if not cari.telefon_2 and aday.telefon_2:
        doldurulacak["telefon_2"] = aday.telefon_2
        doldurulacak["telefon_2_whatsapp"] = aday.telefon_2_whatsapp
    if not cari.eposta:
        e1, e2 = _aday_gecerli_epostalar(aday)
        gecerli = e1 or e2
        if gecerli:
            doldurulacak["eposta"] = gecerli
    if not cari.web and aday.web:
        doldurulacak["web"] = aday.web
    if not cari.adres and aday.adres:
        doldurulacak["adres"] = aday.adres
        if not cari.ulke_id and aday.ulke_id:
            doldurulacak["ulke_id"] = aday.ulke_id
        if not cari.sehir_id and aday.sehir_id:
            doldurulacak["sehir_id"] = aday.sehir_id
    if not cari.ilgili_kisi and aday.ilgili_kisi:
        doldurulacak["ilgili_kisi"] = aday.ilgili_kisi
    return doldurulacak


def _yetkilileri_aktar(aday, cari, *, atlama_kontrolu, kullanici=None):
    mevcut = set()
    if atlama_kontrolu:
        mevcut = set(cari.yetkililer.filter(silindi=False).values_list("ad_soyad", "telefon"))
    aktarilan = []
    for y in aday.yetkililer.filter(silindi=False):
        if atlama_kontrolu and (y.ad_soyad, y.telefon) in mevcut:
            continue
        CariYetkili.objects.create(
            cari=cari, ad_soyad=y.ad_soyad, unvan=y.unvan, telefon=y.telefon,
            eposta=y.eposta, notlar=y.notlar, whatsapp=y.whatsapp,
            created_by=kullanici, updated_by=kullanici)
        aktarilan.append(y.ad_soyad)
    return aktarilan


def _aktiviteleri_kopyala(aday, cari, *, kullanici=None):
    """AdayAktivite -> CariAktivite KOPYASI (taşıma değil — aday tarafı silinmez, iz kalır).
    Ek dosyalar da kopyalanır: aynı dosya yolunu iki kayıt PAYLAŞMAZ (aday tarafı silinirse
    cari tarafı kaybolmasın)."""
    aktiviteler = aday.aktiviteler.filter(silindi=False).prefetch_related(
        Prefetch("ekler", queryset=AdayAktiviteEk.objects.filter(silindi=False)))
    for aktivite in aktiviteler:
        yeni = CariAktivite.objects.create(
            cari=cari, tarih=aktivite.tarih, tur=aktivite.tur, aciklama=aktivite.aciklama,
            created_by=kullanici, updated_by=kullanici)
        for ek in aktivite.ekler.all():
            with ek.dosya.open("rb") as f:
                icerik = ContentFile(f.read(), name=ek.dosya.name.rsplit("/", 1)[-1])
            CariAktiviteEk.objects.create(
                aktivite=yeni, dosya=icerik, orijinal_ad=ek.orijinal_ad,
                created_by=kullanici, updated_by=kullanici)


def _kullanici_adi(kullanici):
    if not kullanici:
        return "Bilinmeyen kullanıcı"
    return kullanici.get_full_name() or kullanici.username


def _not_aktivitesi_ekle(cari, aday, *, kullanici=None, ek_satirlar=None):
    bugun = tr_bugun()
    satirlar = [f"Aday kaydından dönüştürüldü — Aday #{aday.pk} {aday.unvan}, "
               f"{bugun.strftime('%d.%m.%Y')}, {_kullanici_adi(kullanici)}."]
    satirlar.extend(ek_satirlar or [])
    CariAktivite.objects.create(
        cari=cari, tarih=bugun, tur=CariAktivite.Tur.NOT, aciklama="\n".join(satirlar),
        created_by=kullanici, updated_by=kullanici)


def _donustur_on_kontrol(aday):
    if aday.silindi:
        raise AdayDonusturHatasi("Silinmiş aday dönüştürülemez.")
    if aday.cari_id:
        raise AdayDonusturHatasi("Bu aday zaten bir cariye dönüştürülmüş.")
    engel = donusturme_engeli_var_mi(aday)
    if engel:
        raise AdayDonusturHatasi(engel)


def _cari_rollu_asama():
    """Aktif tek CARI rollü aşama, yoksa None — spec kararı: hiç yoksa cariye dönüşümünde
    aday.asama değişmez (bkz. core.services.aday_tanim._asama_gecerlilik_kontrol, en fazla
    bir aktif CARI rolü zaten orada zorlanıyor)."""
    return AdayAsamaTanim.objects.filter(
        silindi=False, aktif=True, rol=AdayAsamaTanim.Rol.CARI).first()


def _asama_degisim_satiri(aday, yeni_asama):
    if yeni_asama is None or yeni_asama.pk == aday.asama_id:
        return []
    return [f"Aşama: {aday.asama.ad} → {yeni_asama.ad}."]


def _aday_cariye_isaretle(aday, cari, kullanici, *, yeni_asama=None):
    aday.cari = cari
    aday.cariye_donusum_tarihi = timezone.now()
    aday.updated_by = kullanici
    alanlar = ["cari", "cariye_donusum_tarihi", "updated_by", "updated_at"]
    if yeni_asama is not None and yeni_asama.pk != aday.asama_id:
        aday.asama = yeni_asama
        alanlar.append("asama")
    aday.save(update_fields=alanlar)


@transaction.atomic
def yeni_cari_ac(aday, *, kullanici=None, **cari_alanlar):
    """(A) Yeni cari aç — cari_alanlar CariForm.cleaned_data'dan (view'daki _cari_form_kw
    ile) üretilir; kod ataması + muhasebe hesabı otomatik açılışı cari_servis.cari_olustur
    ZATEN yapar (paralel yol yazılmaz, spec kararı) — Merkez Adres de aynı fonksiyonda
    (adres doluysa) otomatik açılır. Herhangi bir adım hata verirse (transaction.atomic)
    hiçbir şey kaydedilmez."""
    _donustur_on_kontrol(aday)
    cari = cari_servis.cari_olustur(kullanici=kullanici, **cari_alanlar)
    _yetkilileri_aktar(aday, cari, atlama_kontrolu=False, kullanici=kullanici)
    _aktiviteleri_kopyala(aday, cari, kullanici=kullanici)
    yeni_asama = _cari_rollu_asama()
    _not_aktivitesi_ekle(cari, aday, kullanici=kullanici,
                         ek_satirlar=_asama_degisim_satiri(aday, yeni_asama))
    _aday_cariye_isaretle(aday, cari, kullanici, yeni_asama=yeni_asama)
    return cari


@transaction.atomic
def mevcut_cariye_bagla(aday, cari, *, kullanici=None):
    """(B) Mevcut cariye bağla — carinin DOLU alanlarının üzerine YAZILMAZ, yalnız BOŞ
    alanlar doldurulur (bkz. doldurulacak_alanlar). Muhasebe hesabı açılmaz, kategori
    değişmez. Aynı ad_soyad+telefon'lu yetkili caride zaten varsa atlanır."""
    _donustur_on_kontrol(aday)
    if cari.silindi:
        raise AdayDonusturHatasi("Silinmiş cariye bağlanamaz.")
    doldurulan = doldurulacak_alanlar(aday, cari)
    if doldurulan:
        for alan, deger in doldurulan.items():
            setattr(cari, alan, deger)
        cari.updated_by = kullanici
        cari.save(update_fields=list(doldurulan.keys()) + ["updated_by", "updated_at"])
    if (not cari.sevk_adresleri.filter(silindi=False).exists()
            and (cari.adres or cari.ulke_id or cari.sehir_id)):
        cari_servis.sevk_adresi_ekle(
            cari, ad="Merkez Adres", ulke_id=cari.ulke_id, sehir_id=cari.sehir_id,
            adres=cari.adres, varsayilan=True, kullanici=kullanici)
    _yetkilileri_aktar(aday, cari, atlama_kontrolu=True, kullanici=kullanici)
    _aktiviteleri_kopyala(aday, cari, kullanici=kullanici)
    ek_satirlar = []
    if doldurulan:
        etiketler = ", ".join(
            _ALAN_ETIKET[a] for a in doldurulan if a in _ALAN_ETIKET)
        if etiketler:
            ek_satirlar.append(f"Doldurulan alanlar: {etiketler}.")
    yeni_asama = _cari_rollu_asama()
    ek_satirlar += _asama_degisim_satiri(aday, yeni_asama)
    _not_aktivitesi_ekle(cari, aday, kullanici=kullanici, ek_satirlar=ek_satirlar)
    _aday_cariye_isaretle(aday, cari, kullanici, yeni_asama=yeni_asama)
    return cari
