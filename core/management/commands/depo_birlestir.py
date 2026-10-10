"""151 ve 152 depolarını TAMAMEN kaldırır (hepsi 150 SEMTA DEPO'da birleşir). TEK ATOMİK işlem:

  1. 151/152'ye dokunan depo transferi çiftleri (``depo_sadelestir``'in 151→150 çiftleri) KALICI silinir (hard delete; her iki bacak).
  2. 151/152'ye bağlı KALAN her kayıt (stok hareketleri — silinmişler dahil —, operasyon kayıtları, fason dönüşler, belgeler…) 150'ye taşınır.
  3. Etkilenen stok kartları yeniden hesaplanır (hiçbir çıkış maliyeti değişmemeli).
  4. Kontroller (önce/sonra BİREBİR): her kartın eldeki miktarı, ortalama maliyet/değer alanları, hareket tutarları, değerleme 150–153 ve mizan, fiş sayısı ve
     borç/alacak toplamları; 150'de (ve genel olarak) yeni negatif eldeki oluşmaması; 151/152'ye bağlı hiçbir kayıt kalmaması.
  5. 151/152 depo kartları KALICI silinir.
Herhangi bir kontrol tutmazsa HEPSİ geri alınır.

  manage.py depo_birlestir --dry-run   # tüm adımlar + kontroller gerçekten koşar, sonunda geri alınır (hiçbir şey yazılmaz)
  manage.py depo_birlestir             # gerçek koşu"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import Depo, Stok, StokHareket, StokMaliyetKatmani, StokMaliyetTuketimi, YevmiyeFisi, YevmiyeSatir
from core.services import stok_ortalama
from core.services.hareket import toplu_eldeki

HEDEF_KOD = "150"
KAYNAK_KODLARI = ("151", "152")
STOK_ALANLARI = ("maliyet_miktar", "maliyet_deger_try", "maliyet_deger_usd", "ort_maliyet_try", "ort_maliyet_usd")


def _anlik() -> dict:
    """Karşılaştırılacak her şey (önce ve sonra aynı fonksiyon)."""
    kartlar = {r["pk"]: tuple(r[a] for a in STOK_ALANLARI) for r in Stok.objects.values("pk", *STOK_ALANLARI)}
    eldeki = {k: v for k, v in toplu_eldeki(Stok.objects.values_list("pk", flat=True)).items() if v != 0}
    # transfer bacakları hariç hareket maliyetleri (transfer çiftleri silineceği için); id değişmez
    hareket = {r["pk"]: (r["stok_id"], r["tur"], r["miktar"], r["tutar_try"], r["maliyet_durumu"]) for r in
               StokHareket.objects.filter(silindi=False).exclude(kaynak=StokHareket.Kaynak.TRANSFER)
               .values("pk", "stok_id", "tur", "miktar", "tutar_try", "maliyet_durumu")}
    rapor = stok_ortalama.degerleme_raporu()
    deger = ([(k["kod"], k["stok_degeri"], k["mizan"], k["fark"], k["toplam_deger"]) for k in rapor["karsilastirma"]]
             + [("TOPLAM", rapor["toplam_deger"], rapor["toplam_usd"])])
    satir = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(b=Sum("borc"), a=Sum("alacak"), n=Count("id"))
    fis = (YevmiyeFisi.objects.filter(silindi=False).count(), satir["n"], satir["b"], satir["a"])
    return {"kartlar": kartlar, "eldeki": eldeki, "hareket": hareket, "deger": deger, "fis": fis}


def _negatifler() -> set:
    """(stok, depo) çiftleri: silinmemiş hareketlerde, hesap sırasıyla (tarih, giriş önce, id) koşan bakiye herhangi bir anda < 0 olanlar."""
    gruplar = defaultdict(list)
    for h in StokHareket.objects.filter(silindi=False).values("stok_id", "depo_id", "tarih", "tur", "miktar", "pk"):
        gruplar[(h["stok_id"], h["depo_id"])].append(h)
    sonuc = set()
    for anahtar, liste in gruplar.items():
        bakiye = 0
        for h in sorted(liste, key=lambda x: (x["tarih"], 0 if x["tur"] == StokHareket.Tur.GIRIS else 1, x["pk"])):
            bakiye += h["miktar"] if h["tur"] == StokHareket.Tur.GIRIS else -h["miktar"]
            if bakiye < 0:
                sonuc.add(anahtar)
                break
    return sonuc


def _fark(ad, once, sonra, cikti):
    if once != sonra:
        if isinstance(once, dict):
            anahtarlar = sorted(k for k in set(once) | set(sonra) if once.get(k) != sonra.get(k))[:5]
            cikti.append(f"{ad}: {len(anahtarlar)}+ fark, örn. " + "; ".join(f"{k}: {once.get(k)} → {sonra.get(k)}" for k in anahtarlar))
        else:
            cikti.append(f"{ad}: {once} → {sonra}")


class Command(BaseCommand):
    help = "151/152 depolarını 150 SEMTA DEPO'da birleştirir ve kalıcı siler (tek atomik işlem, --dry-run destekli)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Her şeyi koştur, kontrolleri yap, sonunda GERİ AL.")

    def handle(self, *args, **opts):
        kuru = opts["dry_run"]
        hedef = Depo.objects.filter(silindi=False, kod=HEDEF_KOD).first()
        kaynaklar = list(Depo.objects.filter(silindi=False, kod__in=KAYNAK_KODLARI).order_by("kod"))
        if hedef is None:
            raise CommandError(f"Hedef depo (kod {HEDEF_KOD}) bulunamadı.")
        if hedef.fason_cari_id or any(k.fason_cari_id for k in kaynaklar):
            raise CommandError("Fason deposu birleştirilemez.")
        if not kaynaklar:
            self.stdout.write("Zaten tamam: 151/152 depoları yok.")
            return
        with transaction.atomic():
            self._calistir(hedef, kaynaklar)
            if kuru:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY-RUN: tüm adımlar ve kontroller koştu; işlem GERİ ALINDI, hiçbir şey yazılmadı."))
            else:
                self.stdout.write(self.style.SUCCESS("TAMAM: işlem kalıcı yazıldı."))

    def _calistir(self, hedef, kaynaklar):
        yaz = self.stdout.write
        kaynak_idler = [k.pk for k in kaynaklar]
        once = _anlik()
        negatif_once = _negatifler()
        yaz(f"Hedef: {hedef.kod} '{hedef.ad}' | birleşecek: " + ", ".join(f"{k.kod} '{k.ad}'" for k in kaynaklar))

        # 1) transfer çiftleri (151/152'ye dokunan gruplar; silinmiş bacaklar dahil) — KALICI sil
        gruplar = set(StokHareket.objects.filter(depo_id__in=kaynak_idler, kaynak=StokHareket.Kaynak.TRANSFER, transfer_grubu__isnull=False)
                      .values_list("transfer_grubu", flat=True))
        bacaklar = StokHareket.objects.filter(transfer_grubu__in=gruplar)
        yanlis = [g for g in gruplar if bacaklar.filter(transfer_grubu=g, silindi=False).count() != 2]
        if yanlis:
            raise CommandError(f"{len(yanlis)} transfer grubu tam çift değil (2 aktif bacak beklenir); işlem iptal.")
        if StokMaliyetKatmani.objects.filter(stok_hareket__in=bacaklar).exists() or StokMaliyetTuketimi.objects.filter(tuketen_hareket__in=bacaklar).exists():
            raise CommandError("Transfer bacaklarına bağlı maliyet katmanı/tüketimi var; işlem iptal.")
        sil_sayisi, etkilenen = bacaklar.count(), set(bacaklar.values_list("stok_id", flat=True))
        bacaklar.delete()
        yaz(f"1) {len(gruplar)} transfer çifti ({sil_sayisi} bacak) KALICI silindi.")

        # 2) kalan bağlı kayıtlar → hedef depo (silinmişler dahil)
        etkilenen |= set(StokHareket.objects.filter(depo_id__in=kaynak_idler).values_list("stok_id", flat=True))
        yaz("2) Taşınan kayıtlar:")
        for rel in Depo._meta.related_objects:
            model, alan = rel.related_model, rel.field.name
            n = model.objects.filter(**{f"{alan}__in": kaynak_idler}).update(**{alan: hedef})
            if n:
                yaz(f"     {model.__name__}.{alan}: {n}")

        # 3) etkilenen kartları yeniden hesapla — hiçbir çıkış maliyeti değişmemeli
        for s in Stok.objects.filter(pk__in=etkilenen):
            degisen = stok_ortalama.yeniden_hesapla(s, fis_guncelle=False)
            if degisen:
                raise CommandError(f"{s.kod}: yeniden hesapta {len(degisen)} çıkışın maliyeti değişti; işlem GERİ ALINDI.")
        yaz(f"3) {len(etkilenen)} stok kartı yeniden hesaplandı (hiçbir çıkış maliyeti değişmedi).")

        # 4) kontroller
        sonra = _anlik()
        farklar = []
        for ad in ("kartlar", "eldeki", "hareket", "deger", "fis"):
            _fark(ad, once[ad], sonra[ad], farklar)
        yeni_negatif = _negatifler() - negatif_once
        if yeni_negatif:
            farklar.append(f"yeni negatif eldeki (stok, depo): {sorted(yeni_negatif)[:5]}")
        kalan = {f"{rel.related_model.__name__}.{rel.field.name}": rel.related_model.objects.filter(**{f"{rel.field.name}__in": kaynak_idler}).count()
                 for rel in Depo._meta.related_objects}
        kalan = {k: v for k, v in kalan.items() if v}
        if kalan:
            farklar.append(f"151/152'ye hâlâ bağlı kayıt: {kalan}")
        if farklar:
            raise CommandError("KONTROL BAŞARISIZ — işlem GERİ ALINDI:\n  " + "\n  ".join(farklar))
        yaz("4) Kontroller (önce/sonra BİREBİR aynı):")
        yaz(f"     stok kartı: {len(once['kartlar'])} (ortalama maliyet/değer alanları) | eldeki miktar: {len(once['eldeki'])} kart | aktif hareket maliyetleri: {len(once['hareket'])}")
        for satir in sonra["deger"]:
            yaz(f"     değerleme/mizan: {satir}")
        yaz(f"     fiş (adet, satır, borç, alacak): {sonra['fis']}")
        yaz(f"     yeni negatif eldeki: yok | 151/152'ye bağlı kayıt: yok")

        # 5) depo kartlarını KALICI sil
        for k in kaynaklar:
            k.delete()
        yaz("5) 151 ve 152 depo kartları KALICI silindi.")
