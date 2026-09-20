"""730 GENEL ÜRETİM GİDERLERİ ve 770 GENEL YÖNETİM GİDERLERİ altına gider alt hesapları.

Alış-Gider faturasında seçilebilen yaprak gider hesaplarının ilk kümesi. Veri migration'ıdır:
- İdempotent (aynı adlı alt hesap zaten varsa atlanır; kod çakışırsa sıradaki boş kod alınır).
- Üst hesap (730/770) yoksa o üst için hiçbir şey yapmaz (taze test/kurulum veritabanı).
- Rapor grubu / kalem / parasal ÜST hesaptan miras alınır (servis kuralıyla aynı).
Seed CSV'sine BİLEREK eklenmedi: seed'e eklense 730/770 üst hesap olur ve doğrudan onlara
fiş kesen testler/akışlar kırılırdı.
"""
from django.db import migrations

ALT_HESAPLAR = {
    "730": [
        "ELEKTRİK GİDERİ", "SU GİDERİ", "KİRA GİDERİ", "MUTFAK GİDERİ", "TEMİZLİK GİDERİ",
        "PERSONEL MAAŞ GİDERİ", "ARAÇ GİDERLERİ", "PERSONEL GİDERLERİ",
    ],
    "770": [
        "AĞIRLAMA GİDERİ", "ALINAN HİZMET GİDERLERİ", "BANKA MASRAF GİDERLERİ",
        "DEMİRBAŞ SATIŞ ZARARI", "FİNANSMAN GİDERİ", "KARGO GİDERİ", "NUMUNE GİDERLERİ",
        "OFİS MALZEMELERİ GİDERİ", "RESMİ EVRAK GİDERLERİ", "SEYAHAT GİDERİ",
        "VERGİ ÖDEMELERİ", "YAZILIM GİDERLERİ",
    ],
}


def alt_hesaplari_ac(apps, schema_editor):
    HesapPlani = apps.get_model("core", "HesapPlani")
    for ust_kodu, adlar in ALT_HESAPLAR.items():
        ust = HesapPlani.objects.filter(hesap_kodu=ust_kodu, silindi=False).first()
        if ust is None:
            continue
        onek = ust_kodu + "."
        # doğrudan çocuk sıra numaraları (silinmişler dahil: kod yeniden kullanılmaz)
        kullanilan = set()
        for kod in HesapPlani.objects.filter(hesap_kodu__startswith=onek).values_list(
                "hesap_kodu", flat=True):
            son = kod[len(onek):]
            if "." not in son and son.isdigit():
                kullanilan.add(int(son))
        for ad in adlar:
            if HesapPlani.objects.filter(
                    hesap_kodu__startswith=onek, hesap_adi=ad, silindi=False).exists():
                continue                                   # aynı adlı alt hesap zaten var
            n = 1
            while n in kullanilan:
                n += 1
            kullanilan.add(n)
            HesapPlani.objects.create(
                hesap_kodu=f"{onek}{n:02d}", hesap_adi=ad,
                rapor_grubu=ust.rapor_grubu, rapor_kalemi=ust.rapor_kalemi,
                parasal=ust.parasal, aktif=True)


def alt_hesaplari_kaldir(apps, schema_editor):
    """Geri alma: yalnız HİÇ yevmiye satırı olmayan bu adlı alt hesapları siler."""
    HesapPlani = apps.get_model("core", "HesapPlani")
    YevmiyeSatir = apps.get_model("core", "YevmiyeSatir")
    for ust_kodu, adlar in ALT_HESAPLAR.items():
        for h in HesapPlani.objects.filter(
                hesap_kodu__startswith=ust_kodu + ".", hesap_adi__in=adlar):
            if not YevmiyeSatir.objects.filter(hesap=h).exists():
                h.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0123_fatura_gider_kalemi"),
    ]

    operations = [
        migrations.RunPython(alt_hesaplari_ac, alt_hesaplari_kaldir),
    ]
