# El yazımı veri taşıma — mevcut Aday Müşteri kayıtlarında unvana köşeli parantezle
# yazılmış etiketi ("[MÜŞTERİ]"/"[ADAY]"/"[ARACI]"/"[PAZAR BİLGİSİ]") okuyup tip alanına
# aktarır, sonra etiketi unvandan siler. Geri alınabilir: reverse tip'e göre etiketi
# unvanın SONUNA ekler (orijinal konumu değil — orijinal metindeki tam yer bilgisi
# saklanmıyor, bkz. plan). Eşleme büyük/küçük harf ve Türkçe İ duyarlı (fold yok).
import re

from django.db import migrations

_ETIKET_TIP = [
    ("[MÜŞTERİ]", "ESKI_MUSTERI"),
    ("[ARACI]", "ARACI"),          # GÜMRÜK geçenler asagida GUMRUK'a çevrilir
    ("[PAZAR BİLGİSİ]", "PAZAR_BILGISI"),
    ("[ADAY]", "ADAY"),
]
_TERS_ETIKET = {
    "ESKI_MUSTERI": "[MÜŞTERİ]",
    "ADAY": "[ADAY]",
    "ARACI": "[ARACI]",
    "GUMRUK": "[ARACI]",
    "PAZAR_BILGISI": "[PAZAR BİLGİSİ]",
}
_TEHNOALAT_ISTISNA = "TEHNOALAT"


def _tip_belirle(unvan):
    for etiket, tip in _ETIKET_TIP:
        if etiket in unvan:
            if etiket == "[ARACI]" and "GÜMRÜK" in unvan:
                return "GUMRUK"
            return tip
    if _TEHNOALAT_ISTISNA in unvan:
        return "ESKI_MUSTERI"
    return "ADAY"


def _etiket_temizle(unvan):
    for etiket, _ in _ETIKET_TIP:
        unvan = unvan.replace(etiket, " ")
    return re.sub(r"\s+", " ", unvan).strip()


def tipleri_ata(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    for a in AdayMusteri.objects.all():
        a.tip = _tip_belirle(a.unvan)
        a.unvan = _etiket_temizle(a.unvan)
        a.asama = "YENI"
        a.potansiyel = ""
        a.kapanis_nedeni = ""
        a.save(update_fields=["tip", "unvan", "asama", "potansiyel", "kapanis_nedeni"])


def tipleri_geri_al(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    for a in AdayMusteri.objects.all():
        etiket = _TERS_ETIKET.get(a.tip)
        if etiket and etiket not in a.unvan:
            a.unvan = f"{a.unvan} {etiket}".strip()
            a.save(update_fields=["unvan"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0125_aday_musteri_tip_potansiyel_asama"),
    ]

    operations = [
        migrations.RunPython(tipleri_ata, tipleri_geri_al),
    ]
