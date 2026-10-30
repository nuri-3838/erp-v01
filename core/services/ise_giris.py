"""İNSAN KAYNAKLARI > İşe giriş: özlük kontrol listesi + işe giriş kaydı (personel kartı "İşe Giriş" sekmesi).

Yalnız KAYIT/TAKİP: resmî SGK bildirimini ERP yapmaz; muhasebeye bilgi paketi (Dilim 3) ve evrak basımı (Dilim 2) bu servisin
üstüne kurulur. Kontrol listesi KODDA tanımlıdır (``KONTROL_LISTESI``); mali müşavir/İSG uzmanı ile netleşince buradan düzeltilir.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction

from core.models import IkAyari, IseGirisKaydi, Personel, PersonelBelge
from core.services import personel_belge as belge_servis
from core.tarih import tr_bugun

T = PersonelBelge.Tur

KAYNAKLAR = {
    "IMZALI": "İşverence hazırlanır, personel imzalar",
    "PERSONEL": "Personelden alınır",
    "ISG": "İSG uzmanı / işyeri hekimi",
    "MUHASEBE": "Muhasebe / SGK",
}

# (kaynak, belge türü, görünen ad, zorunlu mu). Zorunlu = işe başlamadan/başlarken özlükte bulunması beklenen temel evrak; listede ZORUNLULAR ÜSTTE, sonra isteğe bağlılar.
KONTROL_LISTESI = (
    ("IMZALI", T.SOZLESME, "Belirsiz süreli iş sözleşmesi", True),
    ("IMZALI", T.KVKK, "KVKK aydınlatma + açık rıza", True),
    ("IMZALI", T.FAZLA_MESAI_ONAYI, "Fazla çalışma muvafakatnamesi", True),
    ("PERSONEL", T.KIMLIK, "Kimlik fotokopisi", True),
    ("PERSONEL", T.IKAMETGAH, "İkametgâh belgesi", True),
    ("PERSONEL", T.VESIKALIK, "Vesikalık fotoğraf", True),
    ("PERSONEL", T.ADLI_SICIL, "Adli sicil kaydı", True),
    ("ISG", T.SAGLIK_RAPORU, "İşe giriş sağlık muayenesi / raporu", True),
    ("ISG", T.ISG_TUTANAK, "İSG işe giriş eğitimi tutanağı", True),
    ("MUHASEBE", T.SGK_GIRIS, "SGK işe giriş bildirgesi", True),
    ("PERSONEL", T.EHLIYET, "Ehliyet", False),
    ("PERSONEL", T.IS_BASVURU, "İş başvuru formu / CV", False),
    ("ISG", T.KKD_TESLIM, "KKD teslim tutanağı", False),
)

# Eski/benzer türler aynı satırı karşılar (ör. eskiden yüklenmiş "İSG Eğitim Belgesi" yeni "İSG tutanağı" satırını da doldurur).
_ESDEGER = {
    T.ISG_TUTANAK: (T.ISG_TUTANAK, T.ISG_EGITIMI),
}

DURUM_ADLARI = {"EKSIK": "Eksik", "YUKLU": "Yüklü", "YAKLASIYOR": "Süresi yaklaşıyor", "SURESI_DOLDU": "Süresi doldu"}


class IseGirisHatasi(ValueError):
    """İşe giriş kaydı kural ihlali (Türkçe mesaj)."""


def kontrol_listesi(personel: Personel, *, belgeler=None, bugun=None) -> dict:
    """Özlük kontrol listesi. ``belgeler`` = ``belge_servis.belge_durumlari(personel)`` çıktısı (verilirse sorgu yapılmaz).
    Döner: {"satirlar": [{tur, ad, kaynak, kaynak_ad, zorunlu, durum, belge, kalan}], "eksik_zorunlu": n, "eksik": n, "yuklu": n, "toplam": n}."""
    bugun = bugun or tr_bugun()
    if belgeler is None:
        belgeler = belge_servis.belge_durumlari(personel, bugun=bugun)
    satirlar = []
    for kaynak, tur, ad, zorunlu in KONTROL_LISTESI:
        turler = _ESDEGER.get(tur, (tur,))
        aday = [(b, d, k) for b, d, k in belgeler if b.tur in turler and d != "ESKI"]     # belgeler yeni → eski sıralı: ilk = en yeni
        if not aday:
            durum, belge, kalan = "EKSIK", None, None
        else:
            belge, d, kalan = aday[0]
            durum = d if d in ("SURESI_DOLDU", "YAKLASIYOR") else "YUKLU"
        satirlar.append({"tur": tur.value, "ad": ad, "kaynak": kaynak, "kaynak_ad": KAYNAKLAR[kaynak], "zorunlu": zorunlu,
                         "durum": durum, "durum_ad": DURUM_ADLARI[durum], "belge": belge, "kalan": kalan})
    eksik = [s for s in satirlar if s["durum"] == "EKSIK"]
    return {"satirlar": satirlar, "eksik_zorunlu": sum(1 for s in eksik if s["zorunlu"]), "eksik": len(eksik),
            "yuklu": len(satirlar) - len(eksik), "toplam": len(satirlar),
            "uyari": any(s["durum"] == "SURESI_DOLDU" for s in satirlar)}


def kayit_oku(personel: Personel) -> IseGirisKaydi:
    """Personelin işe giriş kaydı; yoksa KAYDEDİLMEMİŞ boş nesne döner (GET yazmaz)."""
    return IseGirisKaydi.objects.filter(personel=personel).first() or IseGirisKaydi(personel=personel)      # ilişki önbelleğine güvenilmez (bayat nesne)


def _ondalik(deger, ad):
    if deger in (None, ""):
        return None
    try:
        return Decimal(str(deger).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise IseGirisHatasi(f"{ad} sayı olmalı.")


def _satirlar(metin) -> str:
    """Çok satırlı metin: her satırın baş/son boşluğu ve iç çoklu boşlukları sadeleşir, boş satırlar atılır (satır sonları KORUNUR)."""
    return "\n".join(" ".join(l.split()) for l in (metin or "").replace("\r\n", "\n").split("\n") if l.strip())


def _parametreleri_dogrula(deneme_suresi_ay, haftalik_calisma_saati):
    """(deneme_suresi_ay | None, haftalik_calisma_saati | None) — boş = None. Hukuki sınırlar: deneme ≤ 4 ay (yasal üst sınır 2; TİS ile 4), haftalık ≤ 45."""
    if deneme_suresi_ay in (None, ""):
        deneme = None
    else:
        try:
            deneme = int(deneme_suresi_ay)
        except (TypeError, ValueError):
            raise IseGirisHatasi("Deneme süresi ay olarak sayı olmalı.")
        if not 0 <= deneme <= 4:
            raise IseGirisHatasi("Deneme süresi 0 ile 4 ay arasında olmalı (yasal üst sınır 2 ay; toplu iş sözleşmesiyle 4 ay).")
    saat = _ondalik(haftalik_calisma_saati, "Haftalık çalışma saati")
    if saat is not None and not 0 < saat <= 45:
        raise IseGirisHatasi("Haftalık çalışma saati 0'dan büyük ve en çok 45 olmalı (fazla çalışma ayrıca yazılır).")
    return deneme, saat


def sozlesme_ayari() -> IkAyari:
    """Firma geneli sözleşme varsayılanları (AYARLAR › Belge Şablonları)."""
    return IkAyari.get()


@transaction.atomic
def sozlesme_ayar_kaydet(*, deneme_suresi_ay, haftalik_calisma_saati, calisma_gunleri, calisma_saatleri, ucret_odeme, kullanici=None) -> IkAyari:
    """Varsayılan sözleşme çalışma düzenini kaydeder; kişiye özel değeri olmayan her personel için basımda bu değerler kullanılır."""
    deneme, saat = _parametreleri_dogrula(deneme_suresi_ay, haftalik_calisma_saati)
    ayar = IkAyari.get()
    ayar.sozlesme_deneme_suresi_ay = deneme if deneme is not None else ayar.sozlesme_deneme_suresi_ay
    ayar.sozlesme_haftalik_saat = saat if saat is not None else ayar.sozlesme_haftalik_saat
    ayar.sozlesme_calisma_gunleri = " ".join((calisma_gunleri or "").split())
    ayar.sozlesme_calisma_saatleri = _satirlar(calisma_saatleri)
    ayar.sozlesme_ucret_odeme = " ".join((ucret_odeme or "").split())
    ayar.updated_by = kullanici
    ayar.save()
    return ayar


def etkin_sozlesme(kayit: IseGirisKaydi, ayar: IkAyari) -> dict:
    """Basımda geçerli değerler: personele özel (işe giriş kaydı) doluysa o, boşsa firma varsayılanı (``IkAyari.sozlesme_*``)."""
    return {
        "deneme_suresi_ay": kayit.deneme_suresi_ay if kayit.deneme_suresi_ay is not None else ayar.sozlesme_deneme_suresi_ay,
        "haftalik_calisma_saati": kayit.haftalik_calisma_saati if kayit.haftalik_calisma_saati is not None else ayar.sozlesme_haftalik_saat,
        "calisma_gunleri": kayit.calisma_gunleri or ayar.sozlesme_calisma_gunleri,
        "calisma_saatleri": kayit.calisma_saatleri or ayar.sozlesme_calisma_saatleri,
        "ucret_odeme": kayit.ucret_odeme or ayar.sozlesme_ucret_odeme,
    }


@transaction.atomic
def kayit_kaydet(personel: Personel, *, deneme_suresi_ay=None, haftalik_calisma_saati=None, calisma_gunleri="", calisma_saatleri="",
                 ucret_odeme="", notlar="", kullanici=None) -> IseGirisKaydi:
    """Personele ÖZEL sözleşme parametrelerini kaydeder (boş alan = firma varsayılanı kullanılır; bkz. ``etkin_sozlesme``). Evrak basım ve muhasebe
    gönderim alanlarına DOKUNMAZ."""
    if personel.silindi:
        raise IseGirisHatasi("Silinmiş personel için işe giriş kaydı tutulamaz.")
    deneme, saat = _parametreleri_dogrula(deneme_suresi_ay, haftalik_calisma_saati)
    kayit = kayit_oku(personel)
    kayit.deneme_suresi_ay = deneme
    kayit.haftalik_calisma_saati = saat
    kayit.calisma_gunleri = " ".join((calisma_gunleri or "").split())
    kayit.calisma_saatleri = _satirlar(calisma_saatleri)
    kayit.ucret_odeme = " ".join((ucret_odeme or "").split())
    kayit.notlar = (notlar or "").strip()
    kayit.updated_by = kullanici
    if kayit.pk is None:
        kayit.created_by = kullanici
        kayit.save()
    else:                                                               # yalnız parametre alanları: basım/muhasebe alanlarına dokunulmaz
        kayit.save(update_fields=["deneme_suresi_ay", "haftalik_calisma_saati", "calisma_gunleri", "calisma_saatleri", "ucret_odeme",
                                  "notlar", "updated_by", "updated_at"])
    return kayit


# === Evrak paketi (Dilim 2): şablon yer tutucu değerleri, eksik bilgi uyarıları, paket bağlamı ===========================================

# Şablon kodu -> imza düzeni: iki_taraf (işveren + işçi) / isci (yalnız işçi) ve işçi imzasının üstüne basılan beyan.
IMZA = {
    "SOZLESME": ("iki_taraf", "Bu sözleşmeyi okudum, anladım, bir suretini teslim aldım."),
    "KVKK_AYDINLATMA": ("isci", "Aydınlatma metnini okudum, bir nüshasını teslim aldım."),
    "KVKK_ACIK_RIZA": ("isci", "Açık rızamı özgür irademle veriyorum."),
    "FAZLA_CALISMA": ("iki_taraf", "Fazla çalışmaya yazılı olarak muvafakat ediyorum."),
}


def ucret_metni(personel: Personel, *, bugun=None) -> str:
    """Sözleşmedeki ücret ifadesi: net tutar ("40.000,00 TL") ya da "asgari ücret"; kayıt yoksa boş (çıktıda noktalı çizgi).
    İşe giriş tarihi ileride ise o tarihteki geçerli kayıt alınır."""
    from core.sayi import format_tr
    from core.services import personel_ucret as ucret_servis
    bugun = bugun or tr_bugun()
    tarih = max(bugun, personel.ise_giris_tarihi) if personel.ise_giris_tarihi else bugun
    u = ucret_servis.gecerli_ucret(personel, tarih)
    if u is None:
        return ""
    return "asgari ücret" if u.tip == "ASGARI" else f"{format_tr(u.net_tutar)} TL"


def yer_tutucu_degerleri(personel: Personel, firma, kayit, *, ucret="", bugun=None, ayar=None) -> dict:
    """``belge_sablon.doldur`` için {yer_tutucu: değer}. Boş/bilinmeyen değerler çıktıda noktalı çizgi olur."""
    from core.dogrulama import iban_bicimli
    from core.sayi import format_tr
    bugun = bugun or tr_bugun()
    gun = lambda d: d.strftime("%d.%m.%Y") if d else ""
    e = etkin_sozlesme(kayit, ayar or IkAyari.get())
    saat = e["haftalik_calisma_saati"]
    return {
        "firma_unvan": firma.unvan, "firma_adres": " ".join((firma.adres or "").split()), "firma_vergi_dairesi": firma.vergi_dairesi,
        "firma_vergi_no": firma.vergi_no, "firma_eposta": firma.eposta, "mersis_no": firma.mersis_no, "sgk_sicil_no": firma.sgk_isyeri_sicil_no,
        "vekil_ad": firma.isveren_vekili_adi, "vekil_unvan": firma.isveren_vekili_unvani,
        "ad_soyad": personel.ad_soyad, "tc": personel.tc_kimlik_no, "dogum_tarihi": gun(personel.dogum_tarihi), "dogum_yeri": personel.dogum_yeri,
        "baba_adi": personel.baba_adi, "ana_adi": personel.ana_adi, "adres": " ".join((personel.adres or "").split()),
        "telefon": personel.telefon, "iban": iban_bicimli(personel.iban) if personel.iban else "",
        "gorev": personel.gorev, "departman": personel.departman, "ise_giris": gun(personel.ise_giris_tarihi),
        "deneme_suresi": f"{e['deneme_suresi_ay']} ay" if e["deneme_suresi_ay"] else "",
        "haftalik_saat": format_tr(saat, 1).replace(",0", "") if saat else "",
        "calisma_gunleri": e["calisma_gunleri"], "calisma_saatleri": e["calisma_saatleri"],
        "ucret": ucret, "ucret_odeme": e["ucret_odeme"], "bugun": gun(bugun),
    }


def eksik_bilgiler(personel: Personel, firma, kayit, *, ucret_gorebilir=False, ucret="", ayar=None) -> list:
    """Evrak basılmadan önce kullanıcıya gösterilen eksikler: [{"alan", "nerede": personel|firma|sozlesme|ucret}]. Basımı ENGELLEMEZ
    (eksik değer çıktıda elle doldurulacak noktalı çizgi olur)."""
    out = []
    for alan, ad in (("tc_kimlik_no", "TC kimlik no"), ("dogum_tarihi", "Doğum tarihi"), ("dogum_yeri", "Doğum yeri"), ("baba_adi", "Baba adı"),
                     ("ana_adi", "Ana adı"), ("adres", "Adres"), ("gorev", "Görev"), ("iban", "IBAN")):
        if not getattr(personel, alan):
            out.append({"alan": ad, "nerede": "personel"})
    for alan, ad in (("unvan", "Firma unvanı"), ("adres", "Firma adresi"), ("isveren_vekili_adi", "İşveren vekili adı")):
        if not getattr(firma, alan):
            out.append({"alan": ad, "nerede": "firma"})
    e = etkin_sozlesme(kayit, ayar or IkAyari.get())                  # kişiye özel boşsa firma varsayılanı geçerli: eksik sayılır yalnız ikisi de boşsa
    for alan, ad in (("deneme_suresi_ay", "Deneme süresi"), ("haftalik_calisma_saati", "Haftalık çalışma saati"),
                     ("calisma_gunleri", "Çalışma günleri"), ("calisma_saatleri", "Günlük çalışma saatleri"), ("ucret_odeme", "Ücret ödeme şekli")):
        if e[alan] in (None, ""):
            out.append({"alan": ad, "nerede": "sozlesme"})
    if ucret_gorebilir and not ucret:
        out.append({"alan": "Ücret kaydı", "nerede": "ucret"})
    return out


BILGI_FORMU_KODU = "PERSONEL_BILGI"                  # şablonu olmayan, sistemin kendi ürettiği belge (düzenlenebilir metin değil: personel kartından dolar)
BILGI_FORMU_BASLIK = "PERSONEL BİLGİ FORMU"


def bilgi_formu_bolumleri(personel: Personel, bugun=None) -> list:
    """Personel bilgi formunun bölümleri: [(başlık, [(etiket, değer)])]. Değer boşsa çıktıda noktalı çizgi (elle doldurulur) basılır.
    Ücret BURADA YOKTUR (ücret yetkisi ayrı; form personel imzasına gider)."""
    from core.dogrulama import iban_bicimli
    from core.services.belge_sablon import BOS
    bugun = bugun or tr_bugun()
    gun = lambda d: d.strftime("%d.%m.%Y") if d else ""
    secenek = lambda alan: getattr(personel, f"get_{alan}_display")() if getattr(personel, alan) else ""
    kisi = lambda ad, tel: " — ".join(x for x in (ad, tel) if x)
    bolumler = [
        ("Kimlik Bilgileri", [
            ("Adı Soyadı", personel.ad_soyad), ("T.C. Kimlik No", personel.tc_kimlik_no),
            ("Doğum Tarihi", gun(personel.dogum_tarihi)), ("Doğum Yeri", personel.dogum_yeri),
            ("Baba Adı", personel.baba_adi), ("Ana Adı", personel.ana_adi),
            ("Cinsiyet", secenek("cinsiyet")), ("Medeni Durum", secenek("medeni_durum")),
            ("Çocuk Sayısı", "" if personel.cocuk_sayisi is None else str(personel.cocuk_sayisi)),
            ("Öğrenim Durumu", secenek("ogrenim_durumu")), ("Kan Grubu", personel.kan_grubu),
        ]),
        ("İletişim Bilgileri", [
            ("Adres", " ".join((personel.adres or "").split())), ("Telefon", personel.telefon), ("E-posta", personel.eposta),
            ("Acil Durumda Aranacak Kişi 1", kisi(personel.acil_durum_kisi, personel.acil_durum_telefon)),
            ("Acil Durumda Aranacak Kişi 2", kisi(personel.acil_durum_kisi_2, personel.acil_durum_telefon_2)),
        ]),
        ("Banka Bilgileri", [
            ("IBAN", iban_bicimli(personel.iban) if personel.iban else ""), ("Banka", personel.banka_adi),
            ("Hesap Sahibi", personel.hesap_sahibi or personel.ad_soyad),
        ]),
        ("İşe Giriş Bilgileri", [
            ("İşe Giriş Tarihi", gun(personel.ise_giris_tarihi)), ("Departman", personel.departman), ("Görev", personel.gorev),
        ]),
    ]
    return [(b, [(e, d if d else BOS) for e, d in satirlar]) for b, satirlar in bolumler]


def paket_hazirla(personel: Personel, firma, kayit, sablonlar, *, bilgi_formu=False, ucret="", bugun=None, ayar=None) -> list:
    """Basılacak belgeler: [{kod, baslik, paragraflar, bolumler, imza, imza_notu}]. Önce (``bilgi_formu`` ise) personel bilgi formu, sonra şablonlar
    sıralarıyla; her belge BİR nüsha. Yer tutucular güvenli düz yazı değiştirmeyle doldurulur."""
    from core.services import belge_sablon as bs
    degerler = yer_tutucu_degerleri(personel, firma, kayit, ucret=ucret, bugun=bugun, ayar=ayar)
    belgeler = []
    if bilgi_formu:
        belgeler.append({"kod": BILGI_FORMU_KODU, "baslik": BILGI_FORMU_BASLIK, "bolumler": bilgi_formu_bolumleri(personel, bugun),
                         "paragraflar": ["Yukarıdaki bilgilerin doğru ve eksiksiz olduğunu, bilgilerimde değişiklik olması hâlinde işverene yazılı olarak "
                                         "bildireceğimi beyan ederim."],
                         "imza": "isci", "imza_notu": "Bilgilerin doğruluğunu beyan ederim."})
    for s in sablonlar:
        metin = bs.doldur(s.metin, degerler)
        paragraflar = [p.strip() for p in metin.replace("\r\n", "\n").split("\n\n") if p.strip()]
        imza, imza_notu = IMZA.get(s.kod, ("isci", ""))
        belgeler.append({"kod": s.kod, "baslik": bs.doldur(s.baslik, degerler), "paragraflar": paragraflar, "bolumler": [],
                         "imza": imza, "imza_notu": imza_notu})
    return belgeler


@transaction.atomic
def paket_basildi(personel: Personel, *, kullanici=None) -> IseGirisKaydi:
    """Evrak paketi basım zamanını kaydeder (yalnız o alan; sözleşme parametrelerine/muhasebe alanına dokunmaz)."""
    from django.utils import timezone
    kayit = kayit_oku(personel)
    kayit.paket_basim_zamani = timezone.now()
    kayit.updated_by = kullanici
    if kayit.pk is None:
        kayit.created_by = kullanici
        kayit.save()
    else:
        kayit.save(update_fields=["paket_basim_zamani", "updated_by", "updated_at"])
    return kayit
