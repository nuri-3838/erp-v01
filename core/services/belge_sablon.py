"""AYARLAR > Belge Şablonları servis katmanı — işe giriş evraklarının DÜZENLENEBİLİR metinleri (bkz. core.models.BelgeSablonu).

Metin düz yazıdır (boş satır = paragraf); ``{ad_soyad}`` gibi yer tutucular basımda ``doldur`` ile değiştirilir. Django şablonu
ÇALIŞTIRILMAZ (yalnız ``str`` değiştirme; enjeksiyon yok). Varsayılan metinler genel TASLAKTIR — kullanmadan önce hukuk
danışmanı / mali müşavir onayı gerekir; bu yüzden ekranda uyarı gösterilir. Kayıtlar ilk kullanımda ``hazirla()`` ile kendiliğinden
oluşur (veri migration'ı gerekmez; test/boş veritabanında da çalışır).
"""
from __future__ import annotations

import re

from django.db import transaction

from core.models import BelgeSablonu

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
}

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

VARSAYILAN = {
    BelgeSablonu.Kod.SOZLESME: {"baslik": "BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ", "metin": _SOZLESME, "sira": 1},
    BelgeSablonu.Kod.KVKK_AYDINLATMA: {"baslik": "KİŞİSEL VERİLERİN KORUNMASI KANUNU KAPSAMINDA ÇALIŞAN AYDINLATMA METNİ", "metin": _KVKK_AYDINLATMA, "sira": 2},
    BelgeSablonu.Kod.KVKK_ACIK_RIZA: {"baslik": "KİŞİSEL VERİLERİN İŞLENMESİNE İLİŞKİN AÇIK RIZA METNİ", "metin": _KVKK_ACIK_RIZA, "sira": 3},
    BelgeSablonu.Kod.FAZLA_CALISMA: {"baslik": "FAZLA ÇALIŞMA MUVAFAKATNAMESİ", "metin": _FAZLA_CALISMA, "sira": 4},
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
            BelgeSablonu.objects.create(kod=kod, baslik=v["baslik"], metin=v["metin"], sira=v["sira"], aktif=True,
                                        created_by=kullanici, updated_by=kullanici)
        else:
            _yukselt(var[kod])
    return list(BelgeSablonu.objects.filter(silindi=False).order_by("sira", "id"))


def gorunum_listesi() -> list:
    """Kart ekranı için SALT-OKUNUR liste [{kod, baslik, aktif}]: kayıtlı şablonlar; henüz oluşmamış olanlar varsayılanıyla (aktif) gösterilir.
    Kayıt oluşturmaz (GET yazmaz); basım sırasında ``hazirla()`` eksikleri oluşturur."""
    var = {s.kod: s for s in BelgeSablonu.objects.filter(silindi=False)}
    out = []
    for kod, v in sorted(VARSAYILAN.items(), key=lambda kv: kv[1]["sira"]):
        s = var.get(kod)
        out.append({"kod": kod, "baslik": s.baslik if s else v["baslik"], "aktif": s.aktif if s else True, "sira": s.sira if s else v["sira"]})
    return sorted(out, key=lambda x: x["sira"])


def getir(kod: str) -> BelgeSablonu:
    hazirla()
    return BelgeSablonu.objects.get(kod=kod, silindi=False)


def kaydet(sablon: BelgeSablonu, *, baslik, metin, aktif=True, sira=None, kullanici=None) -> BelgeSablonu:
    baslik = " ".join((baslik or "").split())
    metin = (metin or "").replace("\r\n", "\n").strip()
    if not baslik:
        raise SablonHatasi("Başlık boş olamaz.")
    if not metin:
        raise SablonHatasi("Metin boş olamaz.")
    yanlis = bilinmeyen_yer_tutucular(metin)
    if yanlis:
        raise SablonHatasi("Tanımsız yer tutucu: " + ", ".join("{" + y + "}" for y in yanlis)
                           + ". Kullanılabilir yer tutucular ekranın altındaki listededir.")
    sablon.baslik, sablon.metin, sablon.aktif = baslik, metin, bool(aktif)
    if sira is not None:
        sablon.sira = sira
    sablon.updated_by = kullanici
    sablon.save()
    return sablon


def varsayilana_don(sablon: BelgeSablonu, kullanici=None) -> BelgeSablonu:
    v = VARSAYILAN[sablon.kod]
    return kaydet(sablon, baslik=v["baslik"], metin=v["metin"], aktif=sablon.aktif, sira=v["sira"], kullanici=kullanici)
