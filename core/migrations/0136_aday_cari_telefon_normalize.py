"""CRM telefon alanlarını uluslararası (phonenumbers) biçimine normalize eder:
AdayMusteri.telefon/telefon_2, Cari.telefon/telefon_2, AdayYetkili.telefon,
CariYetkili.telefon (bkz. core.dogrulama.telefon_normalize).

SAF DML (RunPython) — hiçbir şema değişikliği içermez: Ulke.kod zaten ISO-2 kodu tutuyor
(TR, AE, IQ, RS…), yeni alan gerekmedi. DDL hiç olmadığı için bu migration'ı bölmeye gerek
yok (bkz. [[postgres-migration-dml-ddl-ayni-transaction]] dersi — o sorun yalnız DML+DDL
AYNI migration'da olunca çıkıyor).

Geri alınabilirlik: değişen her (tablo, id, alan) için ESKİ değer JSON dosyasına yazılır
(BACKUP_DIR altına — .sql.gz/.tar.gz günlük yedekleriyle AYNI dizin, git'e girmez, günlük
yedek retention'ı bu dosyaya dokunmaz çünkü adı erp_v01_*.sql.gz/.tar.gz desenine uymuyor).
Reverse bu dosyayı okuyup eski değerleri geri yazar; dosya yoksa (ör. forward hiç
çalışmadıysa) uyarı verip sessizce çıkar."""
import json
from pathlib import Path

from django.conf import settings
from django.db import migrations

from core.dogrulama import telefon_normalize

_YEDEK_YOLU = Path(settings.BASE_DIR) / "backups" / "core_0136_telefon_yedek.json"

_ALANLAR = {
    "AdayMusteri": ("telefon", "telefon_2"),
    "Cari": ("telefon", "telefon_2"),
    "AdayYetkili": ("telefon",),
    "CariYetkili": ("telefon",),
}


def _iso2(ulke):
    return (ulke.kod if ulke else "") or "TR"


def _isle(model_adi, Model, qs, ulke_coz, yedek):
    degisen = zaten_dogru = dogrulanamayan = 0
    liste = []
    for kayit in qs.iterator():
        iso2 = ulke_coz(kayit)
        degisiklik = {}
        for alan in _ALANLAR[model_adi]:
            eski = getattr(kayit, alan)
            if not eski:
                continue
            yeni, basarili = telefon_normalize(eski, iso2)
            if not basarili:
                dogrulanamayan += 1
                ad = getattr(kayit, "unvan", None) or getattr(kayit, "ad_soyad", "")
                liste.append({"id": kayit.pk, "unvan": ad, "alan": alan, "deger": eski})
                continue
            if yeni != eski:
                yedek.setdefault(f"{model_adi}.{alan}", {})[str(kayit.pk)] = eski
                degisiklik[alan] = yeni
                degisen += 1
            else:
                zaten_dogru += 1
        if degisiklik:
            Model.objects.filter(pk=kayit.pk).update(**degisiklik)
    return {"degisen": degisen, "zaten_dogru": zaten_dogru,
            "dogrulanamayan": dogrulanamayan, "liste": liste}


def _tasi(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    Cari = apps.get_model("core", "Cari")
    AdayYetkili = apps.get_model("core", "AdayYetkili")
    CariYetkili = apps.get_model("core", "CariYetkili")

    yedek = {}
    rapor = {}
    rapor["AdayMusteri"] = _isle(
        "AdayMusteri", AdayMusteri, AdayMusteri.objects.select_related("ulke").all(),
        lambda k: _iso2(k.ulke), yedek)
    rapor["Cari"] = _isle(
        "Cari", Cari, Cari.objects.select_related("ulke").all(),
        lambda k: _iso2(k.ulke), yedek)
    rapor["AdayYetkili"] = _isle(
        "AdayYetkili", AdayYetkili, AdayYetkili.objects.select_related("aday__ulke").all(),
        lambda k: _iso2(k.aday.ulke), yedek)
    rapor["CariYetkili"] = _isle(
        "CariYetkili", CariYetkili, CariYetkili.objects.select_related("cari__ulke").all(),
        lambda k: _iso2(k.cari.ulke), yedek)

    _YEDEK_YOLU.parent.mkdir(parents=True, exist_ok=True)
    with open(_YEDEK_YOLU, "w", encoding="utf-8") as f:
        json.dump(yedek, f, ensure_ascii=False, indent=2)

    print(f"BİLGİ (0136): telefon normalizasyonu tamam — yedek: {_YEDEK_YOLU}")
    for model_adi, r in rapor.items():
        print(f"  {model_adi}: değişen={r['degisen']} zaten_doğru={r['zaten_dogru']} "
              f"doğrulanamayan={r['dogrulanamayan']}")
        for d in r["liste"]:
            print(f"    doğrulanamadı: {model_adi}#{d['id']} {d['alan']} "
                  f"{d['unvan']!r} değer={d['deger']!r}")


def _geri_al(apps, schema_editor):
    if not _YEDEK_YOLU.exists():
        print(f"UYARI (0136 geri alma): yedek dosyası bulunamadı ({_YEDEK_YOLU}); atlandı.")
        return
    with open(_YEDEK_YOLU, encoding="utf-8") as f:
        yedek = json.load(f)
    modeller = {
        "AdayMusteri": apps.get_model("core", "AdayMusteri"),
        "Cari": apps.get_model("core", "Cari"),
        "AdayYetkili": apps.get_model("core", "AdayYetkili"),
        "CariYetkili": apps.get_model("core", "CariYetkili"),
    }
    toplam = 0
    for anahtar, degerler in yedek.items():
        model_adi, alan = anahtar.split(".")
        Model = modeller[model_adi]
        for pk, eski_deger in degerler.items():
            Model.objects.filter(pk=int(pk)).update(**{alan: eski_deger})
            toplam += 1
    print(f"BİLGİ (0136 geri alma): {toplam} alan eski değerine döndürüldü.")


class Migration(migrations.Migration):
    """Saf DML — şema değişikliği yok (bkz. dosya başı notu)."""

    dependencies = [
        ('core', '0135_aday_kaynak_64_239_duzeltme'),
    ]

    operations = [
        migrations.RunPython(_tasi, _geri_al),
    ]
