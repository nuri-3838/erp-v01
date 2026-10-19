"""Tek boyutlu varyantlı kartta bir varyantı (ör. yan etiket SEMTA) başka bir varyantla (YOK/markasız) BİRLEŞTİRİR — tek seferlik düzeltme komutu (2026-10-20, 150-50-0002 YAN ETİKET).

Ne yapar (hepsi tek transaction; ÖNİZLEME de aynı işlemi uygulayıp GERİ ALIR ve önce/sonra karşılaştırmasını yazar):
* ``StokVaryant``'a bağlı TÜM satırlar (stok hareketi, ayırma, belge kalemleri… — silinmişler dahil; alanlar modellerden bulunur) kaynak varyanttan hedefe çevrilir;
  miktar / maliyet / fiş tutarları değişmez. Kartın varsayılan varyantı hedef olur.
* Kartı girdi olarak kullanan AKTİF operasyon girdi tanımları (``OperasyonGirdi``) "SABİT <hedef>" kuralına alınır (çıktı varyantından taşıma kalkar — markasız malzeme).
* Kaynak varyant satırı (hiçbir yerde kullanılmıyorsa) kapatılır (soft-delete); kartta tek varyant kalır.
* Hareketli ağırlıklı ortalama maliyet birleşen zincirde baştan hesaplanır (``stok_ortalama.yeniden_hesapla``, fiş güncellemesi KAPALI); herhangi bir ÇIKIŞ/fiş tutarı değişecekse
  işlem DURDURULUR (muhasebe değişmemeli). Ayrıca mizan (hesap bazında borç/alacak), kartın toplam miktar/değeri, tüm kartların stok değeri toplamı önce/sonra aynı olmalıdır.

    python manage.py varyant_birlestir --stok 150-50-0002 --kaynak SEMTA --hedef YOK            # önizleme (hiçbir şey yazmaz)
    python manage.py varyant_birlestir --stok 150-50-0002 --kaynak SEMTA --hedef YOK --uygula   # uygular; denetim dökümü logs/varyant_birlestir_<stok>_<tarih>.json
"""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Operasyon, OperasyonGirdi, Stok, StokHareket, StokVaryant, Varyant, YevmiyeSatir
from core.services import stok_ortalama
from core.services import varyant as varyant_servis

SIFIR = Decimal("0")


def _metin(x):
    return None if x is None else (format(x, "f") if isinstance(x, Decimal) else str(x))


def anlik(stok) -> dict:
    """Doğrulama için önce/sonra görüntüsü."""
    stok = Stok.objects.get(pk=stok.pk)
    sv = {v.pk: {"anahtar": v.anahtar, "silindi": v.silindi, "miktar": _metin(v.maliyet_miktar), "deger": _metin(v.maliyet_deger_try), "ort": _metin(v.ort_maliyet_try)}
          for v in StokVaryant.objects.filter(stok=stok)}
    hareket = StokHareket.objects.filter(stok=stok, silindi=False)
    mizan = defaultdict(lambda: [SIFIR, SIFIR])
    for hk, b, a in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).values_list("hesap__hesap_kodu").annotate(b=Sum("borc"), a=Sum("alacak"))):
        mizan[hk] = [b or SIFIR, a or SIFIR]
    kart_degerleri = Stok.objects.aggregate(d=Sum("maliyet_deger_try"))["d"] or SIFIR
    tutarlar = {h.pk: (h.tutar_try, h.tur, h.miktar, h.tarih) for h in hareket}
    return {"stok": {"miktar": stok.maliyet_miktar, "deger": stok.maliyet_deger_try, "ort": stok.ort_maliyet_try, "varsayilan_varyant": stok.varsayilan_varyant_id},
            "varyantlar": sv, "hareket_sayisi": len(tutarlar), "hareket_tutarlari": tutarlar, "mizan": {k: tuple(v) for k, v in mizan.items()},
            "tum_kartlar_deger": kart_degerleri,
            "hareket_miktar_tur": {t: sum((m for _, tt, m, _ in tutarlar.values() if tt == t), SIFIR) for t in ("GIRIS", "CIKIS")}}


def varyant_fk_alanlari():
    """[(model, alan adı)] — StokVaryant'a FK veren tüm alanlar (kartın varsayılan varyantı hariç; o ayrıca yönetilir)."""
    sonuc = []
    for m in apps.get_app_config("core").get_models():
        for f in m._meta.get_fields():
            if f.is_relation and f.many_to_one and f.related_model is StokVaryant and not (m is Stok and f.name == "varsayilan_varyant"):
                sonuc.append((m, f.name))
    return sonuc


class Command(BaseCommand):
    help = "Tek boyutlu varyantlı kartta bir varyantı başka bir varyantla birleştirir (önizleme: varsayılan; --uygula: yazar)."

    def add_arguments(self, parser):
        parser.add_argument("--stok", required=True, help="Stok kodu (ör. 150-50-0002)")
        parser.add_argument("--kaynak", required=True, help="Birleştirilecek varyant kodu (ör. SEMTA)")
        parser.add_argument("--hedef", required=True, help="Kalacak varyant kodu (ör. YOK)")
        parser.add_argument("--uygula", action="store_true", help="Değişiklikleri yaz (yoksa önizleme: işlem yapılır, doğrulanır ve GERİ ALINIR).")
        parser.add_argument("--kaynak-satirini-kapatma", action="store_true", help="Kaynak varyant satırını (StokVaryant) kapatma; boş satır olarak kalsın.")

    def handle(self, *args, **o):
        uygula = o["uygula"]
        stok = Stok.objects.filter(kod=o["stok"], silindi=False).first()
        if stok is None or not stok.varyantli:
            raise CommandError(f"{o['stok']}: varyantlı stok kartı bulunamadı.")
        boyutlar = varyant_servis.stok_boyutlari(stok)
        if len(boyutlar) != 1:
            raise CommandError(f"{stok.kod}: komut yalnız TEK boyutlu kartlar içindir (boyutlar: {boyutlar}).")
        alan = varyant_servis._BOYUT_ALANI[boyutlar[0]]
        kv = Varyant.objects.filter(kod=o["kaynak"], silindi=False).first()
        hv = Varyant.objects.filter(kod=o["hedef"], silindi=False).first()
        if kv is None or hv is None or kv.pk == hv.pk:
            raise CommandError("Kaynak/hedef varyant bulunamadı ya da aynı.")
        sv_k = StokVaryant.objects.filter(stok=stok, silindi=False, **{alan: kv}).first()
        sv_h = StokVaryant.objects.filter(stok=stok, silindi=False, **{alan: hv}).first()
        if sv_k is None and sv_h is not None and StokVaryant.objects.filter(stok=stok, silindi=True, **{alan: kv}).exists():
            self.stdout.write(f"{stok.kod}: {o["kaynak"]} varyant satırı zaten kapalı (birleştirme daha önce uygulanmış); yapılacak iş yok.")
            return
        if sv_k is None or sv_h is None:
            raise CommandError(f"{stok.kod}: kaynak ({o['kaynak']}) ve hedef ({o['hedef']}) için aktif StokVaryant satırı olmalı (bulunan: kaynak={sv_k}, hedef={sv_h}).")
        self.stdout.write(f"{'UYGULA' if uygula else 'ÖNİZLEME'}: {stok.kod} {stok.ad} | boyut {boyutlar[0]} | {o['kaynak']} (sv#{sv_k.pk}) → {o['hedef']} (sv#{sv_h.pk}) | kartın varsayılan varyantı sv#{stok.varsayilan_varyant_id}")
        degisim = {}
        with transaction.atomic():
            once = anlik(stok)
            degisim = self._uygula(stok, sv_k, sv_h, kv, hv, kapat=not o["kaynak_satirini_kapatma"])
            sonra = anlik(stok)
            self._dogrula(once, sonra)
            self._yaz(once, sonra, degisim)
            if not uygula:
                transaction.set_rollback(True)
        if uygula:
            yol = self._log(stok, o, sv_k, sv_h, once, sonra, degisim)
            self.stdout.write(self.style.SUCCESS(f"UYGULANDI. Denetim dökümü: {yol}"))
        else:
            self.stdout.write(self.style.WARNING("ÖNİZLEME — hiçbir şey kaydedilmedi. Uygulamak için --uygula ekleyin."))

    # ---------------------------------------------------------------- değişiklik
    def _uygula(self, stok, sv_k, sv_h, kv, hv, *, kapat) -> dict:
        degisim = {"satirlar": {}, "operasyon_girdileri": [], "varsayilan_varyant": None, "kaynak_satiri_kapatildi": False}
        for model, alan in varyant_fk_alanlari():
            ids = list(model._base_manager.filter(**{alan: sv_k}).values_list("pk", flat=True))
            if not ids:
                continue
            try:
                with transaction.atomic():
                    model._base_manager.filter(pk__in=ids).update(**{alan: sv_h})
            except IntegrityError as e:
                raise CommandError(f"{model.__name__}.{alan}: hedef varyantla çakışma (benzersizlik) — {e}")
            degisim["satirlar"][f"{model.__name__}.{alan}"] = ids
        if stok.varsayilan_varyant_id != sv_h.pk:
            degisim["varsayilan_varyant"] = [stok.varsayilan_varyant_id, sv_h.pk]
            Stok.objects.filter(pk=stok.pk).update(varsayilan_varyant=sv_h)
            stok.varsayilan_varyant = sv_h
        TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT
        for g in OperasyonGirdi.objects.filter(girdi=stok, silindi=False).select_related("operasyon__cikti", "operasyon__istasyon").order_by("pk"):
            if g.varyant_kurali == SABIT and g.sabit_varyant_id == hv.pk:
                continue
            if g.varyant_kurali == SABIT and g.sabit_varyant_id not in (kv.pk, hv.pk):
                continue                                                          # başka bir sabit değer: dokunma
            cakisan = OperasyonGirdi.objects.filter(operasyon=g.operasyon, girdi=stok, silindi=False, varyant_kurali=SABIT, sabit_varyant=hv).exclude(pk=g.pk).exists()
            if cakisan:
                raise CommandError(f"Operasyon #{g.operasyon_id}: aynı girdi için zaten SABİT {hv.kod} satırı var; elle birleştirin.")
            degisim["operasyon_girdileri"].append({"girdi_id": g.pk, "operasyon_id": g.operasyon_id, "istasyon": f"{g.operasyon.istasyon.kod} {g.operasyon.istasyon.ad}", "cikti": g.operasyon.cikti.kod,
                                                   "operasyon_silindi": g.operasyon.silindi, "eski_kural": g.varyant_kurali, "eski_sabit_varyant": g.sabit_varyant_id})
            OperasyonGirdi.objects.filter(pk=g.pk).update(varyant_kurali=SABIT, sabit_varyant=hv)
        if kapat:
            kalan = {f"{m.__name__}.{a}": m._base_manager.filter(**{a: sv_k}).count() for m, a in varyant_fk_alanlari() if m._base_manager.filter(**{a: sv_k}).exists()}
            if kalan or Stok.objects.filter(varsayilan_varyant=sv_k).exists():
                raise CommandError(f"Kaynak varyant satırı hâlâ kullanımda, kapatılamaz: {kalan}")
            StokVaryant.objects.filter(pk=sv_k.pk).update(silindi=True, silindi_at=timezone.now(), standart=False)
            degisim["kaynak_satiri_kapatildi"] = True
        degisen = stok_ortalama.yeniden_hesapla(Stok.objects.get(pk=stok.pk), fis_guncelle=False)
        if degisen:
            raise CommandError(f"DURDURULDU: {len(degisen)} çıkış hareketinin maliyeti değişecek (muhasebe etkilenir): {[h.pk for h in degisen][:20]}")
        if degisim["kaynak_satiri_kapatildi"]:                                  # kapalı satırın maliyet önbelleği sıfırlanır (yeniden_hesapla yalnız aktif satırları yazar)
            StokVaryant.objects.filter(pk=sv_k.pk).update(maliyet_miktar=SIFIR, maliyet_deger_try=SIFIR, maliyet_deger_usd=SIFIR, ort_maliyet_try=None, ort_maliyet_usd=None)
        return degisim

    # ---------------------------------------------------------------- doğrulama
    def _dogrula(self, once, sonra):
        hata = []
        if once["hareket_sayisi"] != sonra["hareket_sayisi"]:
            hata.append("hareket sayısı değişti")
        if once["hareket_tutarlari"] != sonra["hareket_tutarlari"]:
            hata.append("hareket tutarları/miktarları/tarihleri değişti")
        if once["mizan"] != sonra["mizan"]:
            hata.append("MİZAN değişti (hesap bazında borç/alacak)")
        if once["stok"]["miktar"] != sonra["stok"]["miktar"] or once["stok"]["deger"] != sonra["stok"]["deger"]:
            hata.append(f"kart miktar/değer değişti: {once['stok']['miktar']}/{once['stok']['deger']} → {sonra['stok']['miktar']}/{sonra['stok']['deger']}")
        if once["tum_kartlar_deger"] != sonra["tum_kartlar_deger"]:
            hata.append("tüm kartların stok değeri toplamı değişti")
        if hata:
            raise CommandError("DOĞRULAMA BAŞARISIZ (hiçbir şey kaydedilmedi): " + "; ".join(hata))

    # ---------------------------------------------------------------- çıktı
    def _yaz(self, once, sonra, degisim):
        w = self.stdout.write
        w("\n-- Değişen satırlar (tablo.alan: adet)")
        for ad, ids in degisim["satirlar"].items():
            w(f"   {ad}: {len(ids)}")
        if degisim["varsayilan_varyant"]:
            w(f"   Stok.varsayilan_varyant: sv#{degisim['varsayilan_varyant'][0]} → sv#{degisim['varsayilan_varyant'][1]}")
        w(f"-- Operasyon girdi tanımları SABİT'e alınan: {len(degisim['operasyon_girdileri'])}")
        for g in degisim["operasyon_girdileri"]:
            w(f"   girdi#{g['girdi_id']} operasyon#{g['operasyon_id']} {g['istasyon']} → {g['cikti']}{' (silinmiş operasyon)' if g['operasyon_silindi'] else ''}: {g['eski_kural']} → SABIT")
        w(f"-- Kaynak varyant satırı kapatıldı: {degisim['kaynak_satiri_kapatildi']}")
        w("\n-- ÖNCE / SONRA")
        w(f"   kart miktar:  {once['stok']['miktar']} → {sonra['stok']['miktar']}")
        w(f"   kart değer TL: {once['stok']['deger']} → {sonra['stok']['deger']}")
        w(f"   kart ort. maliyet TL: {once['stok']['ort']} → {sonra['stok']['ort']}")
        w(f"   hareket sayısı: {once['hareket_sayisi']} → {sonra['hareket_sayisi']} | giriş {once['hareket_miktar_tur']['GIRIS']} → {sonra['hareket_miktar_tur']['GIRIS']} | çıkış {once['hareket_miktar_tur']['CIKIS']} → {sonra['hareket_miktar_tur']['CIKIS']}")
        w(f"   tüm kartların stok değeri toplamı TL: {once['tum_kartlar_deger']} → {sonra['tum_kartlar_deger']}")
        w(f"   mizan (tüm hesaplar, borç/alacak): {'AYNI' if once['mizan'] == sonra['mizan'] else 'DEĞİŞTİ'} ({len(sonra['mizan'])} hesap)")
        for kod in ("150", "150.50"):
            b = lambda d: (sum((v[0] for k, v in d['mizan'].items() if k.startswith(kod)), SIFIR), sum((v[1] for k, v in d['mizan'].items() if k.startswith(kod)), SIFIR))
            w(f"   {kod}* hesap bakiyesi (borç, alacak): {b(once)} → {b(sonra)}")
        w("   varyant satırları:")
        for pk in sorted(set(once["varyantlar"]) | set(sonra["varyantlar"])):
            o, s = once["varyantlar"].get(pk), sonra["varyantlar"].get(pk)
            w(f"      sv#{pk}: önce {o and (o['anahtar'], 'silindi' if o['silindi'] else 'aktif', o['miktar'], o['deger'], o['ort'])} → sonra {s and (s['anahtar'], 'silindi' if s['silindi'] else 'aktif', s['miktar'], s['deger'], s['ort'])}")

    def _log(self, stok, o, sv_k, sv_h, once, sonra, degisim) -> str:
        klasor = Path(getattr(settings, "BASE_DIR", ".")) / "logs"
        klasor.mkdir(exist_ok=True)
        yol = klasor / f"varyant_birlestir_{stok.kod}_{timezone.now():%Y%m%d_%H%M%S}.json"

        def sade(x):
            if isinstance(x, dict):
                return {str(k): sade(v) for k, v in x.items() if k not in ("hareket_tutarlari", "mizan")}
            if isinstance(x, (list, tuple)):
                return [sade(v) for v in x]
            return _metin(x) if isinstance(x, Decimal) else x
        veri = {"zaman": timezone.now().isoformat(), "stok": stok.kod, "kaynak": o["kaynak"], "hedef": o["hedef"], "kaynak_sv": sv_k.pk, "hedef_sv": sv_h.pk,
                "degisen_satirlar": degisim["satirlar"], "varsayilan_varyant": degisim["varsayilan_varyant"], "operasyon_girdileri": degisim["operasyon_girdileri"],
                "kaynak_satiri_kapatildi": degisim["kaynak_satiri_kapatildi"], "once": sade(once), "sonra": sade(sonra),
                "mizan_ayni": once["mizan"] == sonra["mizan"], "geri_alma": "degisen_satirlar içindeki id'ler hedef varyanttan kaynak varyanta çevrilerek (ve operasyon_girdileri eski kurallarına döndürülerek) geri alınabilir"}
        yol.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(yol)
