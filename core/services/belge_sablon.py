"""AYARLAR > Belge Şablonları servis katmanı — işe giriş evraklarının DÜZENLENEBİLİR metinleri (bkz. core.models.BelgeSablonu).

Metin düz yazıdır (boş satır = paragraf); ``{ad_soyad}`` gibi yer tutucular basımda ``doldur`` ile değiştirilir. Django şablonu
ÇALIŞTIRILMAZ (yalnız ``str`` değiştirme; enjeksiyon yok). Varsayılan metinler genel TASLAKTIR — kullanmadan önce hukuk
danışmanı / mali müşavir onayı gerekir; bu yüzden ekranda uyarı gösterilir. Kayıtlar ilk kullanımda ``hazirla()`` ile kendiliğinden
oluşur (veri migration'ı gerekmez; test/boş veritabanında da çalışır).
"""
from __future__ import annotations

import re

from django.db import transaction

from core.models import BelgeSablonu, PersonelBelge

BOS = "……………………"                                       # boş/bilinmeyen değer: elle doldurulabilir noktalı çizgi

# Yer tutucu -> açıklama (düzenleme ekranında liste olarak gösterilir). Değerler core.services.ise_giris'te üretilir.
YER_TUTUCULAR = {
    "firma_unvan": "Firma unvanı", "firma_adres": "Firma adresi", "firma_vergi_dairesi": "Vergi dairesi", "firma_vergi_no": "Vergi no",
    "firma_eposta": "Firma e-postası", "mersis_no": "MERSİS no", "sgk_sicil_no": "SGK işyeri sicil no",
    "vekil_ad": "İşveren vekili adı soyadı", "vekil_unvan": "İşveren vekili unvanı",
    "ad_soyad": "Personelin adı soyadı", "tc": "TC kimlik no", "dogum_tarihi": "Doğum tarihi", "dogum_yeri": "Doğum yeri",
    "baba_adi": "Baba adı", "ana_adi": "Ana adı", "adres": "Personel adresi", "telefon": "Personel telefonu", "iban": "IBAN",
    "gorev": "Görev", "departman": "Departman", "ise_giris": "İşe giriş tarihi", "deneme_suresi": "Deneme süresi (örn. 2 ay)",
    "haftalik_saat": "Haftalık çalışma saati", "calisma_gunleri": "Çalışma günleri", "calisma_saatleri": "Günlük çalışma saatleri",
    "ucret": "Ücret (net tutar ya da \"asgari ücret\"; ücret yetkisi yoksa boş çizgi)", "ucret_odeme": "Ücret ödeme şekli/günü", "bugun": "Belge tarihi (basım günü)",
    "firma_kep": "Firma KEP adresi",
}

# Eş anlamlı adlar: {yeni_ad: eski_ad} — İKİSİ DE KALICI olarak çalışır (mevcut şablon metinleri eski adları kullanır). Değer eski adınkiyle aynıdır
# (core.services.ise_giris.yer_tutucu_degerleri doldurur).
ALIASLAR = {"tckn": "tc", "ise_giris_tarihi": "ise_giris", "gorev_unvani": "gorev", "personel_adres": "adres", "personel_tel": "telefon"}
YER_TUTUCULAR.update({a: f"{YER_TUTUCULAR[e]} — {{{e}}} ile aynı" for a, e in ALIASLAR.items()})

_YER_TUTUCU_RE = re.compile(r"\{([a-z_0-9]+)\}")

_SOZLESME = """\
MADDE 1 – TARAFLAR
İŞVEREN: {firma_unvan}
Adres: {firma_adres}
Vergi Dairesi / No: {firma_vergi_dairesi} / {firma_vergi_no}
MERSİS No: {mersis_no}  —  SGK İşyeri Sicil No: {sgk_sicil_no}
Temsilcisi: {vekil_ad} ({vekil_unvan})

İŞÇİ: {ad_soyad}
T.C. Kimlik No: {tc}
Doğum Yeri ve Tarihi: {dogum_yeri} / {dogum_tarihi}
Baba Adı / Ana Adı: {baba_adi} / {ana_adi}
Adres: {adres}
Telefon: {telefon}

MADDE 2 – SÖZLEŞMENİN TÜRÜ VE BAŞLANGICI
İşbu sözleşme belirsiz süreli olup {ise_giris} tarihinde yürürlüğe girer.

MADDE 3 – İŞİN TANIMI VE İŞYERİ
İşçi, işverenin {departman} biriminde "{gorev}" olarak çalışacaktır. İşveren, işin gereklerine göre ve iş mevzuatının izin verdiği ölçüde işçinin görev ve çalışma yerini değiştirebilir.

MADDE 4 – DENEME SÜRESİ
Taraflar arasında {deneme_suresi} deneme süresi öngörülmüştür. Deneme süresi içinde taraflar sözleşmeyi bildirim süresi vermeksizin ve tazminatsız feshedebilir (4857 sayılı İş Kanunu m. 15).

MADDE 5 – ÇALIŞMA SÜRESİ
Haftalık çalışma süresi {haftalik_saat} saattir. Çalışma günleri: {calisma_gunleri}. Günlük çalışma saatleri ve ara dinlenmeleri:
{calisma_saatleri}
Hafta tatili, ulusal bayram ve genel tatil günleri ile yıllık ücretli izin hakları ilgili kanun hükümlerine göre kullandırılır.

MADDE 6 – ÜCRET
İşçiye aylık net {ucret} tutarında ücret ödenir. Ödeme şekli/günü: {ucret_odeme}. Ücret, işçinin bildirdiği banka hesabına (IBAN: {iban}) yatırılır. Yasal kesintiler işverence yapılır.

MADDE 7 – FAZLA ÇALIŞMA
Fazla çalışma, işçinin yazılı onayı ile ve yıllık yasal sınırlar içinde yapılır; ayrıca imzalanan "Fazla Çalışma Muvafakatnamesi" bu sözleşmenin ekidir. Fazla çalışmanın karşılığı 4857 sayılı Kanun m. 41 hükümlerine göre ödenir veya serbest zaman olarak kullandırılır.

MADDE 8 – İŞ SAĞLIĞI VE GÜVENLİĞİ
İşçi, 6331 sayılı Kanun ve işyerinde duyurulan iş sağlığı ve güvenliği kurallarına, eğitimlere ve talimatlara uymayı, verilen kişisel koruyucu donanımı kullanmayı kabul eder. İşveren sağlık gözetimi ve eğitimleri sağlar.

MADDE 9 – İŞYERİ DÜZENİ VE GİZLİLİK
İşçi, işyeri iç düzen kurallarına uymayı; çalışması sırasında öğrendiği işverene ve müşterilere ait ticari sır ve bilgileri hizmet ilişkisi sırasında ve sonrasında üçüncü kişilere açıklamamayı kabul eder.

MADDE 10 – FESİH
Sözleşmenin feshinde bildirim süreleri, kıdem ve ihbar tazminatı ile diğer haklar 4857 sayılı İş Kanunu ve ilgili mevzuata göre uygulanır.

MADDE 11 – KİŞİSEL VERİLERİN KORUNMASI
İşçiye, 6698 sayılı Kişisel Verilerin Korunması Kanunu kapsamında "Aydınlatma Metni" ayrıca verilmiştir.

MADDE 12 – TEBLİGAT
Tarafların yukarıda yazılı adresleri tebligat adresidir. Adres değişikliği yazılı olarak bildirilmedikçe bu adrese yapılan tebligat geçerlidir.

MADDE 13 – YÜRÜRLÜK
İşbu sözleşme {bugun} tarihinde düzenlenmiş, taraflarca okunup imzalanmış ve bir sureti işçiye verilmiştir.
"""

_KVKK_AYDINLATMA = """\
1. Veri Sorumlusu
6698 sayılı Kişisel Verilerin Korunması Kanunu ("KVKK") uyarınca kişisel verileriniz, veri sorumlusu sıfatıyla {firma_unvan} ({firma_adres}) tarafından aşağıda açıklanan kapsamda işlenmektedir.

2. İşlenen Kişisel Veriler
Kimlik bilgileriniz (ad soyad, T.C. kimlik no, doğum yeri/tarihi, ana-baba adı, medeni durum), iletişim bilgileriniz (adres, telefon, e-posta, acil durum kişileri), özlük bilgileriniz (işe giriş, görev, ücret, izin, devam, disiplin ve eğitim kayıtları), finansal bilgileriniz (IBAN), sağlık bilgileriniz (işe giriş muayenesi, sağlık raporları), fotoğrafınız, mesai giriş-çıkış kayıtlarınız ve işyeri sistemlerine erişimde oluşan bağlantı (IP) kayıtlarınız.

3. İşleme Amaçları
İş sözleşmesinin kurulması ve ifası; ücret ve yan hakların ödenmesi; SGK, vergi ve diğer yasal yükümlülüklerin yerine getirilmesi; iş sağlığı ve güvenliği faaliyetlerinin yürütülmesi; çalışma sürelerinin, izin ve devam takibinin yapılması; işyeri güvenliğinin ve operasyonel sürekliliğin sağlanması; hukuki taleplerin karşılanması.

4. Hukuki Sebepler
Kişisel verileriniz KVKK m. 5/2 uyarınca sözleşmenin kurulması ve ifası, hukuki yükümlülüğün yerine getirilmesi, bir hakkın tesisi/kullanılması/korunması ve veri sorumlusunun meşru menfaati hukuki sebeplerine; açık rızanızın gerektiği hâllerde açık rızanıza dayanılarak işlenmektedir.

5. Aktarım
Kişisel verileriniz; yasal zorunluluk ve amaçla sınırlı olarak Sosyal Güvenlik Kurumu, vergi daireleri, bankalar, mali müşavirlik, iş sağlığı ve güvenliği uzmanı/işyeri hekimi, yetkili kamu kurum ve kuruluşları ile hukuki danışmanlara aktarılabilir.

6. Saklama Süresi
Kişisel verileriniz ilgili mevzuatta öngörülen süreler boyunca, bu sürenin sonunda silinir, yok edilir veya anonim hâle getirilir.

7. Haklarınız
KVKK m. 11 uyarınca; verilerinizin işlenip işlenmediğini öğrenme, bilgi talep etme, amacına uygun kullanılıp kullanılmadığını öğrenme, aktarıldığı üçüncü kişileri bilme, eksik/yanlış işlenmişse düzeltilmesini isteme, silinmesini veya yok edilmesini isteme, bu işlemlerin üçüncü kişilere bildirilmesini isteme, otomatik sistemlerle analiz sonucu aleyhinize bir sonuç çıkmasına itiraz etme ve kanuna aykırı işleme nedeniyle zarara uğramanız hâlinde zararın giderilmesini talep etme haklarına sahipsiniz.

8. Başvuru
Haklarınıza ilişkin taleplerinizi yazılı olarak {firma_adres} adresine veya {firma_eposta} e-posta adresine iletebilirsiniz.

Yukarıdaki aydınlatma metnini okudum, bir nüshasını teslim aldım.
"""

_KVKK_ACIK_RIZA = """\
{firma_unvan} tarafından tarafıma verilen Aydınlatma Metni'ni okudum ve anladım. Aşağıdaki hususlarda, ilgili işlemlerle sınırlı olmak üzere, özgür irademle açık rıza veriyorum:

• Fotoğrafımın personel kartında, işyeri yönetim sistemi içinde ve işyeri kimlik/yetki uygulamalarında kullanılması,
• Çalışma saatlerimin takibi amacıyla, telefon/işyeri uygulaması üzerinden mesai giriş-çıkış kayıtlarımın ve bu kayıtlarla ilişkili bağlantı (IP) bilgimin işlenmesi ve saklanması,
• Acil durumlarda yakınlarıma ulaşılabilmesi amacıyla bildirdiğim acil durum kişilerinin iletişim bilgilerinin işlenmesi,
• Sağlık raporu ve işe giriş muayenesi sonuçlarımın iş sağlığı ve güvenliği mevzuatı kapsamında iş sağlığı ve güvenliği uzmanı ve işyeri hekimi ile paylaşılması.

Bu rızanın iş sözleşmesinin kurulması için bir şart olmadığını, dilediğim zaman yazılı bildirimle geri alabileceğimi biliyorum.

Ad Soyad: {ad_soyad}   —   T.C. Kimlik No: {tc}   —   Tarih: {bugun}
"""

_FAZLA_CALISMA = """\
{firma_unvan} işyerinde {ise_giris} tarihinden itibaren "{gorev}" olarak çalışmaya başlayan ben {ad_soyad} (T.C. Kimlik No: {tc});

4857 sayılı İş Kanunu m. 41 ve Fazla Çalışma ve Fazla Sürelerle Çalışma Yönetmeliği uyarınca, işin gereklerinin ve işyerinin ihtiyaçlarının gerektirdiği hâllerde, haftalık yasal çalışma süresini aşan fazla çalışma yapmayı, yıllık yasal sınırı aşmamak kaydıyla, yazılı olarak kabul ediyorum.

Fazla çalışmanın karşılığının, her saat için normal çalışma saat ücretinin yüzde elli fazlasıyla ödeneceğini ya da talebim hâlinde her bir fazla çalışma saati için bir saat otuz dakika serbest zaman olarak kullandırılabileceğini biliyorum.

Bu muvafakatnamenin iş sözleşmesinin eki olduğunu, hafta tatili ve ulusal bayram/genel tatil günlerindeki çalışmaların ilgili mevzuata göre ayrıca değerlendirileceğini ve onayımı dilediğim zaman yazılı bildirimle geri alabileceğimi kabul ederim.

Tarih: {bugun}
"""

_I = BelgeSablonu.Imza
VARSAYILAN = {
    BelgeSablonu.Kod.SOZLESME: {"baslik": "BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ", "metin": _SOZLESME, "sira": 1, "tur": "SOZLESME", "imza": _I.IKI_TARAF,
                                "imza_notu": "Bu sözleşmeyi okudum, anladım, bir suretini teslim aldım."},
    BelgeSablonu.Kod.KVKK_AYDINLATMA: {"baslik": "KİŞİSEL VERİLERİN KORUNMASI KANUNU KAPSAMINDA ÇALIŞAN AYDINLATMA METNİ", "metin": _KVKK_AYDINLATMA, "sira": 2,
                                       "tur": "KVKK", "imza": _I.ISCI, "imza_notu": "Aydınlatma metnini okudum, bir nüshasını teslim aldım."},
    BelgeSablonu.Kod.KVKK_ACIK_RIZA: {"baslik": "KİŞİSEL VERİLERİN İŞLENMESİNE İLİŞKİN AÇIK RIZA METNİ", "metin": _KVKK_ACIK_RIZA, "sira": 3,
                                      "tur": "KVKK", "imza": _I.ISCI, "imza_notu": "Açık rızamı özgür irademle veriyorum."},
    BelgeSablonu.Kod.FAZLA_CALISMA: {"baslik": "FAZLA ÇALIŞMA MUVAFAKATNAMESİ", "metin": _FAZLA_CALISMA, "sira": 4, "tur": "FAZLA_MESAI_ONAYI",
                                     "imza": _I.IKI_TARAF, "imza_notu": "Fazla çalışmaya yazılı olarak muvafakat ediyorum."},
}


class SablonHatasi(ValueError):
    """Şablon kural ihlali (Türkçe mesaj)."""


def bilinmeyen_yer_tutucular(metin: str) -> list:
    """Metindeki tanımsız ``{yer_tutucu}`` adları (yazım hatası yakalamak için), ilk görülme sırasıyla."""
    gorulen = []
    for ad in _YER_TUTUCU_RE.findall(metin or ""):
        if ad not in YER_TUTUCULAR and ad not in gorulen:
            gorulen.append(ad)
    return gorulen


def doldur(metin: str, degerler: dict) -> str:
    """``{ad}`` yer tutucularını ``degerler`` ile değiştirir. Değer yoksa/boşsa noktalı çizgi (``BOS``) basılır; tanımsız yer tutucu
    olduğu gibi kalır (düzenleyen hatayı görsün). Yalnız düz yazı değiştirme: şablon dili/kod çalıştırılmaz."""
    def degistir(m):
        ad = m.group(1)
        if ad not in YER_TUTUCULAR:
            return m.group(0)
        v = degerler.get(ad)
        return str(v).strip() if v not in (None, "") and str(v).strip() else BOS
    return _YER_TUTUCU_RE.sub(degistir, metin or "")


def _yukselt(sablon: BelgeSablonu) -> bool:
    """Metni ESKİ bir varsayılanla birebir aynı (kullanıcı hiç düzenlememiş) şablonu güncel varsayılana yükseltir; düzenlenmişe dokunmaz.
    Başlık ancak eski varsayılan başlıktan biriyse güncellenir. Döner: değişti mi."""
    from core.services.belge_sablon_eski import ESKI_BASLIKLAR, ESKI_METINLER
    v = VARSAYILAN[sablon.kod]
    mevcut = sablon.metin.strip()
    if mevcut == v["metin"].strip() or mevcut not in {m.strip() for m in ESKI_METINLER.get(sablon.kod, [])}:
        return False
    sablon.metin = v["metin"].strip()
    if sablon.baslik in ESKI_BASLIKLAR.get(sablon.kod, []):
        sablon.baslik = v["baslik"]
    sablon.save(update_fields=["metin", "baslik", "updated_at"])
    return True


@transaction.atomic
def hazirla(kullanici=None) -> list:
    """Eksik varsayılan şablonları oluşturur; düzenlenmemiş eski varsayılanları güncele yükseltir (düzenlenmişe dokunmaz);
    sıralı aktif/pasif tüm şablonları döndürür."""
    var = {s.kod: s for s in BelgeSablonu.objects.filter(silindi=False)}
    for kod, v in VARSAYILAN.items():
        if kod not in var:
            BelgeSablonu.objects.create(kod=kod, baslik=v["baslik"], metin=v["metin"], sira=v["sira"], aktif=True, tur=v["tur"], imza=v["imza"],
                                        imza_notu=v["imza_notu"], created_by=kullanici, updated_by=kullanici)
        else:
            _yukselt(var[kod])
    return list(BelgeSablonu.objects.filter(silindi=False).order_by("sira", "id"))


def _sira_anahtari(kod, sira, ayirt):
    """Paket/liste sırası: SÖZLEŞME her zaman en başta (sıra ne girilmiş olursa olsun), sonra sıra no, sonra kayıt sırası."""
    return (kod != BelgeSablonu.Kod.SOZLESME, sira, ayirt)


def paket_sirala(sablonlar) -> list:
    """``BelgeSablonu`` nesnelerini paket sırasına dizer (sözleşme en başta)."""
    return sorted(sablonlar, key=lambda s: _sira_anahtari(s.kod, s.sira, s.pk))


def gorunum_listesi() -> list:
    """Kart ekranı için SALT-OKUNUR liste [{kod, baslik, aktif, sira, tur}]: kayıtlı TÜM şablonlar (sabit + eklenenler); henüz oluşmamış sabit olanlar
    varsayılanıyla (aktif) gösterilir. Kayıt oluşturmaz (GET yazmaz); basım sırasında ``hazirla()`` eksikleri oluşturur. Paket sırasıyla döner."""
    kayitlar = list(BelgeSablonu.objects.filter(silindi=False))
    var = {s.kod for s in kayitlar}
    tur_ad = dict(PersonelBelge.Tur.choices)
    out = [{"kod": s.kod, "baslik": s.baslik, "aktif": s.aktif, "sira": s.sira, "tur": s.tur, "tur_ad": tur_ad.get(s.tur, s.tur), "ayirt": s.pk} for s in kayitlar]
    for kod, v in VARSAYILAN.items():
        if kod not in var:
            out.append({"kod": kod, "baslik": v["baslik"], "aktif": True, "sira": v["sira"], "tur": v["tur"], "tur_ad": tur_ad.get(v["tur"], v["tur"]), "ayirt": 0})
    return sorted(out, key=lambda x: _sira_anahtari(x["kod"], x["sira"], x["ayirt"]))


def getir(kod: str) -> BelgeSablonu:
    hazirla()
    return BelgeSablonu.objects.get(kod=kod, silindi=False)


_ETIKET_RE = re.compile(r"^\s*([^/]{2,24}?)\s*/\s*([^/]{2,24}?)\s*$")


def imza_etiketleri(imza_notu: str):
    """İmza notu kısa bir "Sol / Sağ" çiftiyse (ör. "Teslim eden / Teslim alan") iki tarafın kutu başlıkları olur: (SOL, SAĞ) büyük harf. Değilse None
    (not, işçi kutusunda beyan metni olarak basılır). Yalnız "iki taraf" düzeninde kullanılır."""
    from core.metin import buyuk_harf_tr
    m = _ETIKET_RE.match(imza_notu or "")
    return (buyuk_harf_tr(m.group(1)), buyuk_harf_tr(m.group(2))) if m else None


def _temizle(baslik, metin, tur, imza, imza_notu) -> tuple:
    baslik = " ".join((baslik or "").split())
    metin = (metin or "").replace("\r\n", "\n").strip()
    imza_notu = " ".join((imza_notu or "").split())
    if not baslik:
        raise SablonHatasi("Başlık boş olamaz.")
    if not metin:
        raise SablonHatasi("Metin boş olamaz.")
    if tur not in PersonelBelge.Tur.values:
        raise SablonHatasi("Geçersiz belge türü.")
    if imza not in BelgeSablonu.Imza.values:
        raise SablonHatasi("Geçersiz imza düzeni.")
    if len(imza_notu) > 200:
        raise SablonHatasi("İmza notu en fazla 200 karakter olabilir.")
    yanlis = bilinmeyen_yer_tutucular(metin)
    if yanlis:
        raise SablonHatasi("Tanımsız yer tutucu: " + ", ".join("{" + y + "}" for y in yanlis)
                           + ". Kullanılabilir yer tutucular ekranın altındaki listededir.")
    return baslik, metin, imza_notu


def kaydet(sablon: BelgeSablonu, *, baslik, metin, aktif=True, sira=None, tur=None, imza=None, imza_notu=None, kullanici=None) -> BelgeSablonu:
    """Şablonu günceller. ``tur``/``imza``/``imza_notu`` verilmezse (None) mevcut değer korunur."""
    tur = sablon.tur if tur is None else tur
    imza = sablon.imza if imza is None else imza
    imza_notu = sablon.imza_notu if imza_notu is None else imza_notu
    baslik, metin, imza_notu = _temizle(baslik, metin, tur, imza, imza_notu)
    sablon.baslik, sablon.metin, sablon.aktif = baslik, metin, bool(aktif)
    sablon.tur, sablon.imza, sablon.imza_notu = tur, imza, imza_notu
    if sira is not None:
        sablon.sira = sira
    sablon.updated_by = kullanici
    sablon.save()
    return sablon


def _yeni_kod() -> str:
    """Eklenen şablonlar için ``OZEL_0001``… (silinmiş kayıtlar dahil sayılır: kod yeniden verilmez)."""
    sayilar = [int(k[5:]) for k in BelgeSablonu.objects.filter(kod__startswith="OZEL_").values_list("kod", flat=True) if k[5:].isdigit()]
    return f"OZEL_{max(sayilar, default=0) + 1:04d}"


@transaction.atomic
def olustur(*, baslik, metin, tur, sira=None, aktif=True, imza=BelgeSablonu.Imza.ISCI, imza_notu="", kullanici=None) -> BelgeSablonu:
    """Yeni (sabit olmayan) şablon. ``sira`` boşsa en sona eklenir. Silme yok; pasife alma var (``kaydet(aktif=False)``)."""
    baslik, metin, imza_notu = _temizle(baslik, metin, tur, imza, imza_notu)
    hazirla(kullanici)                                        # sabit 4 şablon önce yerinde olsun (sıra hesabı ve kod için)
    if sira is None:
        sira = (max(BelgeSablonu.objects.filter(silindi=False).values_list("sira", flat=True), default=0)) + 1
    return BelgeSablonu.objects.create(kod=_yeni_kod(), baslik=baslik, metin=metin, sira=sira, aktif=bool(aktif), tur=tur, imza=imza,
                                       imza_notu=imza_notu, created_by=kullanici, updated_by=kullanici)


def varsayilana_don(sablon: BelgeSablonu, kullanici=None) -> BelgeSablonu:
    if sablon.kod not in VARSAYILAN:
        raise SablonHatasi("Varsayılan metne yalnız sabit şablonlar döner.")
    v = VARSAYILAN[sablon.kod]
    return kaydet(sablon, baslik=v["baslik"], metin=v["metin"], aktif=sablon.aktif, sira=v["sira"], kullanici=kullanici)
