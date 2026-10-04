"""Tek seferlik: hatalı/boş kayıtların FİZİKSEL silinmesi (varsayılan DRY-RUN).

A) İptal (silindi) durumdaki 3 banka hareketi fişi (2026/189, 2026/195, 2026/192) + satırları
B) Boş duran varlık kartları DV-0023, DV-0024 (pasif)
C) Boş cari 320-30-0025 (id 107) + muhasebe hesabı 320.30.0025

Her hedef için: bulundu mu, durum, bağlı kayıt sayısı (Django silme toplayıcısı: CASCADE satırları,
PROTECT/SET_NULL bağları). Beklenmeyen bağ varsa o kayıt SİLİNMEZ, raporlanır. Dry-run
transaction'ı geri alır; ``--uygula`` kalıcı siler. Başka hiçbir kayda dokunulmaz.

    python manage.py hatali_kayit_sil [--uygula]
"""
import datetime
from collections import Counter
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import router, transaction
from django.db.models import ProtectedError, RestrictedError
from django.db.models.deletion import Collector

from core.models import BankaHesap, Cari, DuranVarlik, HesapPlani, YevmiyeFisi, YevmiyeSatir
from core.services import raporlar

FISLER = [  # (pk, yil, fis_no, tutar, tarih, banka_hesap_id, aciklama parçası)
    (260, 2026, 189, "134757.98", datetime.date(2026, 1, 5), 4, "İCRA DAİRESİ GELEN BEDEL"),
    (266, 2026, 195, "6928.64", datetime.date(2026, 1, 19), 4, "İCRA DAİRESİ GELEN BEDEL"),
    (263, 2026, 192, "1849.35", datetime.date(2026, 1, 6), 13, "VADELİ HESAP FAİZ GELİRİ"),
]
DEMIRBASLAR = ["DV-0023", "DV-0024"]
CARI_PK, CARI_KOD, CARI_MUH = 107, "320-30-0025", "320.30.0025"
BANKA_HESAPLARI = (4, 13)


def _topla(obj, izinli):
    """(sayilar {model: n}, engel metni|None). izinli: obje dışında silinmesine izin verilen modeller."""
    c = Collector(using=router.db_for_write(type(obj)))
    try:
        c.collect([obj])
    except (ProtectedError, RestrictedError) as e:
        return {}, f"korumalı bağ: {e.args[0]}", c
    sayi = Counter()
    for model, objs in c.data.items():
        sayi[model.__name__] = len(objs)
    for qs in c.fast_deletes:
        sayi[qs.model.__name__] += qs.count()
    guncellenen = Counter()
    for (field, _v), liste in c.field_updates.items():
        guncellenen[f"{field.model.__name__}.{field.name}"] += sum(len(o) for o in liste)
    beklenmeyen = {m: n for m, n in sayi.items() if m != type(obj).__name__ and m not in izinli and n}
    if beklenmeyen:
        return dict(sayi), f"beklenmeyen bağlı kayıt: {beklenmeyen}", c
    if guncellenen:
        return dict(sayi), f"SET_NULL bağı var: {dict(guncellenen)}", c
    return dict(sayi), None, c


class Command(BaseCommand):
    help = "Hatali/bos kayitlari fiziksel siler (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def _bakiyeler(self):
        out = {}
        for pk in BANKA_HESAPLARI:
            h = BankaHesap.objects.select_related("muhasebe").filter(pk=pk).first()
            if h is None:
                continue
            net, dvz = raporlar._devir(h.muhasebe_id, datetime.date(2100, 1, 1))
            out[pk] = (h.muhasebe_id, net, {k: v for k, v in dvz.items() if v})
        return out

    def _mizan(self):
        from django.db.models import Count, Sum
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(
            n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        once_mizan, once_bak = self._mizan(), self._bakiyeler()
        w(f"\nÖNCE  mizan (aktif fiş, aktif satır, borç, alacak): {once_mizan}")
        for pk, (kod, net, dvz) in once_bak.items():
            w(f"ÖNCE  banka hesap {pk} ({kod}) kapanış bakiyesi: {net} TL, döviz {dvz}")
        silinen, atlanan = Counter(), []

        with transaction.atomic():
            w("\n== A) İptal fişler ==")
            for pk, yil, no, tutar, tarih, bh, ack in FISLER:
                f = YevmiyeFisi.objects.filter(pk=pk).first()
                if f is None:
                    w(f"  fis {pk} ({yil}/{no}): BULUNAMADI")
                    atlanan.append(f"fis {pk} bulunamadı")
                    continue
                satirlar = list(f.satirlar.all())
                borc = sum(s.borc for s in satirlar)
                alacak = sum(s.alacak for s in satirlar)
                durum = "iptal (silindi)" if f.silindi else "AKTİF"
                w(f"  fis {pk} {f.yil}/{f.fis_no}: bulundu, durum {durum}, {f.tarih}, "
                  f"banka_hesap={f.banka_hesap_id}, kaynak={f.kaynak}, '{f.aciklama}', "
                  f"satır {len(satirlar)} (borç {borc} / alacak {alacak})")
                sorun = []
                if not f.silindi:
                    sorun.append("fiş iptal durumunda değil")
                if (f.yil, f.fis_no, f.tarih, f.banka_hesap_id) != (yil, no, tarih, bh) or ack not in f.aciklama:
                    sorun.append("fiş bilgileri beklenenle uyuşmuyor")
                if f.kaynak != "BANKA":
                    sorun.append("kaynak BANKA değil")
                if borc != Decimal(tutar):
                    sorun.append(f"tutar {tutar} değil")
                sayi, engel, c = _topla(f, {"YevmiyeSatir"})
                w(f"    silinecek (toplayıcı): {sayi or '-'}")
                if engel:
                    sorun.append(engel)
                if sorun:
                    w(f"    → SİLİNMEZ: {'; '.join(sorun)}")
                    atlanan.append(f"fis {pk}: {'; '.join(sorun)}")
                    continue
                c.delete()
                silinen.update(sayi)
                w("    → silinecek")

            w("\n== B) Duran varlık kartları ==")
            for kod in DEMIRBASLAR:
                d = DuranVarlik.objects.filter(demirbas_kodu=kod).first()
                if d is None:
                    w(f"  {kod}: BULUNAMADI")
                    atlanan.append(f"{kod} bulunamadı")
                    continue
                fs = d.fatura_satirlari.count()
                w(f"  {kod} (id {d.pk}) '{d.ad}': bulundu, durum {d.durum}, silindi={d.silindi}, "
                  f"hesap {d.hesap_id}, maliyet {d.maliyet}, kaynak {d.kaynak}, proje {d.yatirim_projesi_id}, "
                  f"bağlı fatura satırı {fs}")
                sorun = []
                if d.durum != DuranVarlik.Durum.PASIF:
                    sorun.append("pasif değil")
                if fs:
                    sorun.append("bağlı fatura satırı var")
                if d.yatirim_projesi_id:
                    sorun.append("yatırım projesine bağlı")
                izinli = set()
                sayi, engel, c = _topla(d, izinli)
                w(f"    silinecek (toplayıcı): {sayi or '-'}")
                if engel:
                    sorun.append(engel)
                if sorun:
                    w(f"    → SİLİNMEZ: {'; '.join(sorun)}")
                    atlanan.append(f"{kod}: {'; '.join(sorun)}")
                    continue
                c.delete()
                silinen.update(sayi)
                w("    → silinecek")

            w("\n== C) Boş cari + muhasebe hesabı ==")
            cari = Cari.objects.filter(pk=CARI_PK).first()
            if cari is None:
                w(f"  cari {CARI_PK}: BULUNAMADI")
                atlanan.append("cari bulunamadı")
            else:
                w(f"  cari {cari.pk} {cari.kod} '{cari.unvan}': bulundu, silindi={cari.silindi}, "
                  f"muhasebe_kodu={cari.muhasebe_kodu}")
                hs = HesapPlani.objects.filter(hesap_kodu=CARI_MUH).first()
                hsatir = YevmiyeSatir.objects.filter(hesap_id=CARI_MUH).count() if hs else 0
                w(f"  hesap {CARI_MUH}: {'bulundu: ' + hs.hesap_adi + f', silindi={hs.silindi}' if hs else 'BULUNAMADI'}; "
                  f"yevmiye satırı (iptaller dahil) {hsatir}")
                sorun = []
                if cari.kod != CARI_KOD or cari.muhasebe_kodu != CARI_MUH:
                    sorun.append("cari kodu/muhasebe kodu beklenenle uyuşmuyor")
                if hs is None:
                    sorun.append("muhasebe hesabı yok")
                izinli = {"CariSevkAdresi", "CariYetkili"}
                sayi, engel, c = _topla(cari, izinli)
                w(f"    cari silinecek (toplayıcı): {sayi or '-'}")
                if engel:
                    sorun.append(engel)
                sayi_h, engel_h, ch = ({}, None, None)
                if hs is not None:
                    sayi_h, engel_h, ch = _topla(hs, set())
                    w(f"    hesap silinecek (toplayıcı): {sayi_h or '-'}")
                    if engel_h:
                        sorun.append("hesap: " + engel_h)
                if sorun:
                    w(f"    → SİLİNMEZ: {'; '.join(sorun)}")
                    atlanan.append(f"cari {CARI_PK}: {'; '.join(sorun)}")
                else:
                    c.delete()
                    ch.delete()
                    silinen.update(sayi)
                    silinen.update(sayi_h)
                    w("    → silinecek (önce cari, sonra hesap)")

            w(f"\nToplam silinecek satır sayıları: {dict(silinen) or '-'}")
            w(f"Atlanan/engellenen: {atlanan or 'yok'}")
            sonra_mizan, sonra_bak = self._mizan(), self._bakiyeler()
            w(f"\nSONRA mizan: {sonra_mizan}  → {'DEĞİŞMEDİ' if sonra_mizan == once_mizan else 'DEĞİŞTİ!'}")
            for pk in sonra_bak:
                w(f"SONRA banka hesap {pk} kapanış: {sonra_bak[pk][1]} TL, döviz {sonra_bak[pk][2]}  → "
                  f"{'DEĞİŞMEDİ' if sonra_bak[pk] == once_bak[pk] else 'DEĞİŞTİ!'}")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı silmek için --uygula."))
