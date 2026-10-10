"""Depo sadeleştirme (tek fabrika deposu): 150 DEPO - HAMMADDE kartı "SEMTA DEPO" olarak yeniden adlandırılır; 151 ve 152 depolarındaki TÜM eldeki stok
mevcut depo transferi servisiyle (maliyet DEĞİŞMEZ, muhasebe fişi yok) SEMTA DEPO'ya taşınır (tarih = bugün); sonra 151/152 PASİF yapılır (silinmez, tarihçe
korunur). Fason depoları (ör. 999) dokunulmaz.

  manage.py depo_sadelestir --dry-run   # yalnız plan: hangi stok kaç adet taşınacak, hiçbir şey yazılmaz
  manage.py depo_sadelestir             # gerçek koşu (tek işlem; değerleme/mizan veya stok toplamı değişirse HEPSİ geri alınır)

Tekrar çalıştırılabilir: iş zaten yapılmışsa "zaten tamam" der."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Depo, Stok, StokHareket
from core.services import depo as depo_servis
from core.services.depo_transfer import depo_transferi_yap
from core.services.stok_ortalama import degerleme_raporu

HEDEF_KOD = "150"
KAYNAK_KODLARI = ("151", "152")


def _stok_toplamlari() -> dict:
    """{stok id: tüm depolardaki eldeki toplam} — transferin toplamı değiştirmediğini doğrulamak için."""
    sonuc = {}
    for r in StokHareket.objects.filter(silindi=False).values("stok_id", "tur").annotate(s=Sum("miktar")):
        sonuc[r["stok_id"]] = sonuc.get(r["stok_id"], 0) + (r["s"] if r["tur"] == StokHareket.Tur.GIRIS else -r["s"])
    return {k: v for k, v in sonuc.items() if v != 0}


def _degerleme_ozeti() -> list:
    rapor = degerleme_raporu()
    return [(k["kod"], k["stok_degeri"], k["mizan"], k["fark"], k["toplam_deger"]) for k in rapor["karsilastirma"]] + [("TOPLAM", rapor["toplam_deger"], rapor["toplam_usd"])]


class Command(BaseCommand):
    help = "151/152 depolarındaki stoğu SEMTA DEPO'ya (150) taşır, 151/152'yi pasif yapar (--dry-run destekli)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Yalnız planı göster; hiçbir şey yazma.")

    def handle(self, *args, **opts):
        kuru = opts["dry_run"]
        hedef = Depo.objects.filter(silindi=False, kod=HEDEF_KOD).first()
        if hedef is None:
            raise CommandError(f"Hedef depo (kod {HEDEF_KOD}) bulunamadı.")
        if hedef.fason_cari_id:
            raise CommandError("Hedef depo fason deposu olamaz.")
        kaynaklar = list(Depo.objects.filter(silindi=False, kod__in=KAYNAK_KODLARI).order_by("kod"))
        yeniden_adlandir = hedef.ad != depo_servis.VARSAYILAN_DEPO_ADI
        self.stdout.write(f"Hedef: {hedef.kod} '{hedef.ad}'" + (f" → '{depo_servis.VARSAYILAN_DEPO_ADI}' olarak yeniden adlandırılacak" if yeniden_adlandir else " (ad zaten doğru)"))
        plan, toplam_kalem = [], 0
        for k in kaynaklar:
            eldeki = depo_servis.depo_eldeki_stoklar(k)
            self.stdout.write(f"Kaynak: {k.kod} '{k.ad}' aktif={k.aktif} — eldeki stoklu kart: {len(eldeki)}")
            stoklar = {s.pk: s for s in Stok.objects.filter(pk__in=eldeki.keys())}
            for stok_id, miktar in sorted(eldeki.items(), key=lambda kv: stoklar[kv[0]].kod):
                self.stdout.write(f"    {stoklar[stok_id].kod:<14} {stoklar[stok_id].ad[:40]:<40} {miktar.normalize():f}")
                plan.append((k, stok_id, miktar))
            toplam_kalem += len(eldeki)
        if not plan and not yeniden_adlandir and all(not k.aktif for k in kaynaklar):
            self.stdout.write("Zaten tamam: yapılacak bir şey yok.")
            return
        self.stdout.write(f"Plan: {toplam_kalem} stok kalemi taşınacak; {sum(1 for k in kaynaklar if k.aktif)} depo pasif yapılacak.")
        if kuru:
            self.stdout.write("DRY-RUN: hiçbir şey yazılmadı.")
            return
        onceki_deger, onceki_toplam = _degerleme_ozeti(), _stok_toplamlari()
        bugun = timezone.localdate()
        with transaction.atomic():
            if yeniden_adlandir:
                depo_servis.depo_guncelle(hedef, kod=hedef.kod, ad=depo_servis.VARSAYILAN_DEPO_ADI, fason_cari=None)
            for k, stok_id, miktar in plan:
                depo_transferi_yap(stok_id=stok_id, kaynak_depo_id=k.pk, hedef_depo_id=hedef.pk, tarih=bugun, miktar=miktar,
                                   aciklama="Depo sadeleştirme (tek fabrika deposu)")
            for k in kaynaklar:
                if k.aktif:
                    depo_servis.depo_guncelle(k, kod=k.kod, ad=k.ad, fason_cari=k.fason_cari_id, aktif=False)
            sonraki_deger, sonraki_toplam = _degerleme_ozeti(), _stok_toplamlari()
            if onceki_deger != sonraki_deger:
                raise CommandError(f"Değerleme/mizan değişti; işlem GERİ ALINDI.\nÖnce:  {onceki_deger}\nSonra: {sonraki_deger}")
            if onceki_toplam != sonraki_toplam:
                raise CommandError("Stok toplamları değişti; işlem GERİ ALINDI.")
        self.stdout.write(self.style.SUCCESS(f"Tamam: {toplam_kalem} kalem taşındı; değerleme/mizan ve stok toplamları aynı."))
        for satir in sonraki_deger:
            self.stdout.write(f"    {satir}")
