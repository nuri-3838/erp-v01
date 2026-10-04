"""Tek seferlik: 335 PERSONELE BORÇLAR hesabını personel carilerine böler (varsayılan DRY-RUN; ``--uygula`` kalıcı).

 1. 335.10 grubu (kategori 335-10 PERSONELE BORÇLAR) + personel cari kartları açılır; hesapları sıradaki 335.10.000N olur:
      335.10.0001 ERRAHMAN ALTITOK · 335.10.0002 FETTAH <SOYAD> · 335.10.0003 HAYDAR <SOYAD>   (335 üst hesap olur)
 2. 335'teki satırlar fiş/satır açıklamasına göre taşınır:  "ERRAHMAN" → 335.10.0001;  "FETTAH" → 335.10.0002.
    (Banka hareketlerinin karşı tarafı fişin satırıdır; aynı işlemle güncellenir.) Eşleşmeyen/belirsiz satıra DOKUNULMAZ — listelenir;
    böyle satır varsa ya da kontrol tutmazsa ``--uygula`` iptal edilir.
 Kontroller: 335 toplamı, cari başına bakiye, 335'in kendisinde satır, banka/kasa/kart/kredi bakiyeleri, mizan.

    python manage.py personel_335_detaylandir --fettah "FETTAH SOYAD" --haydar "HAYDAR SOYAD" [--uygula]
(--uygula için soyadlı tam unvan zorunlu; dry-run'da soyadsız verilirse uyarılır.)
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import (BankaHesap, Cari, CariKategori, HesapPlani, Kasa, Kredi, KrediKarti, YevmiyeFisi, YevmiyeSatir)
from core.sayi import format_tr
from core.services import cari as cari_servis
from core.services import hesap_plani as hp
from core.services import hesap_tasima as ht
from core.services import raporlar

KAYNAK = "335"
GRUP = "335.10"
BEKLENEN_TOPLAM = Decimal("182486.80")
BEKLENEN = {"335.10.0001": Decimal("175000.00"), "335.10.0002": Decimal("7486.80"), "335.10.0003": Decimal("0.00")}
KURALLAR = [("335.10.0001", ["ERRAHMAN"]), ("335.10.0002", ["FETTAH"])]


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


class Command(BaseCommand):
    help = "335 hesabini personel carilerine boler ve satirlari tasir (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--errahman", default="ERRAHMAN ALTITOK")
        parser.add_argument("--fettah", default="FETTAH")
        parser.add_argument("--haydar", default="HAYDAR")
        parser.add_argument("--uygula", action="store_true")

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def _nakit(self):
        out = {}
        for model, ad in ((BankaHesap, "BANKA"), (Kasa, "KASA"), (KrediKarti, "KART"), (Kredi, "KREDI")):
            for h in model.objects.filter(silindi=False).order_by("pk"):
                out[(ad, h.pk)] = (h.muhasebe_id, _bak(h.muhasebe_id))
        return out

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        kategori = CariKategori.objects.filter(kod="10", ad="PERSONELE BORÇLAR", ust__kod="335", silindi=False).first()
        if kategori is None:
            raise CommandError("Cari kategorisi 335-10 PERSONELE BORÇLAR bulunamadı.")
        isimler = [opts["errahman"], opts["fettah"], opts["haydar"]]
        eksik = [i for i in isimler if len(i.split()) < 2]
        for i in eksik:
            w(f"  ⚠ '{i}' soyadsız görünüyor (unvan = ad + soyad olmalı)")
        if eksik and uygula:
            raise CommandError("--uygula için soyadlı tam unvanlar gerekli (--fettah / --haydar).")
        once = {"mizan": self._mizan(), "nakit": self._nakit(), "335": _bak(KAYNAK), "n": YevmiyeSatir.objects.filter(hesap_id=KAYNAK).count()}
        w(f"\nÖNCE mizan: {once['mizan']}")
        w(f"335: {once['n']} satır, bakiye {format_tr(once['335'])}")
        with transaction.atomic():
            w("\n== 1) Hesap + cari kartları ==")
            if not HesapPlani.objects.filter(hesap_kodu=GRUP, silindi=False).exists():
                hp.hesap_olustur(kod=GRUP, ad=kategori.ad, ust_kodu=KAYNAK, hareketli_ust_izin=True)
                w(f"  {GRUP} {kategori.ad}: AÇILACAK (335 üst hesap olur)")
            beklenen_kodlar = ["335.10.0001", "335.10.0002", "335.10.0003"]
            for unvan, beklenen in zip(isimler, beklenen_kodlar):
                mevcut = Cari.objects.filter(silindi=False, muhasebe_kodu=beklenen).first()
                if mevcut is not None:
                    w(f"  {beklenen} zaten var: {mevcut.kod} {mevcut.unvan}")
                    continue
                try:
                    c = cari_servis.cari_olustur(unvan=unvan, kategori_id=kategori.pk, para_birimi="TRY")
                except cari_servis.CariHatasi as e:
                    raise CommandError(str(e))
                if c.muhasebe_kodu != beklenen:
                    raise CommandError(f"Beklenen hesap {beklenen}, oluşan {c.muhasebe_kodu}; işlem durduruldu.")
                w(f"  cari {c.kod} {c.unvan} → hesap {c.muhasebe_kodu}: AÇILACAK")
            w("\n== 2) Satır taşıma ==")
            p = ht.plan(KAYNAK, KURALLAR)
            for hedef, satirlar in p.eslesen.items():
                net = sum((s.borc - s.alacak for s in satirlar), Decimal("0"))
                w(f"  → {hedef}: {len(satirlar)} satır, net {format_tr(net)}")
                for s in satirlar:
                    w(f"      {s.fis.yil}/{s.fis.fis_no} | {s.fis.tarih:%d.%m.%Y} | {s.fis.kaynak} banka {s.fis.banka_hesap_id} | "
                      f"B {s.borc} A {s.alacak} | {(s.fis.aciklama or s.aciklama)[:70]}")
            w(f"\nEşleşmeyen/belirsiz satır: {len(p.eslesmeyen)}")
            for s, neden in p.eslesmeyen:
                w(f"  ✗ {s.fis.yil}/{s.fis.fis_no} | B {s.borc} A {s.alacak} | {neden} | '{(s.fis.aciklama or s.aciklama)[:70]}'")
            if p.eslesmeyen:
                w("\n→ Eşleşmeyen satır var: HİÇBİR ŞEY TAŞINMAYACAK.")
                if uygula:
                    raise CommandError("Eşleşmeyen satır var; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            else:
                w(f"\nTaşınan satır: {ht.uygula(p)}")
            w("\n== Kontroller ==")
            tamam = not p.eslesmeyen
            sonra = self._mizan()
            ok = sonra[2] == sonra[3] and sonra[:2] == once["mizan"][:2]
            tamam &= ok
            w(f"Mizan: {sonra} → {'DENGEDE' if ok else 'SORUNLU!'}")
            ok = _bak(KAYNAK) == once["335"] == BEKLENEN_TOPLAM
            tamam &= ok
            w(f"335 toplamı: {format_tr(once['335'])} → {format_tr(_bak(KAYNAK))} (beklenen {format_tr(BEKLENEN_TOPLAM)}) {'✓' if ok else 'FARKLI!'}")
            for kod, bek in BEKLENEN.items():
                b = _bak(kod)
                ok = b == bek
                tamam &= ok
                w(f"  {kod}: {format_tr(b)} B (beklenen {format_tr(bek)}) {'✓' if ok else 'FARKLI!'}")
            kalan = YevmiyeSatir.objects.filter(hesap_id=KAYNAK).count()
            tamam &= kalan == 0
            w(f"335'in kendisinde kalan satır: {kalan}")
            sn = self._nakit()
            degisen = [k for k in once["nakit"] if once["nakit"][k] != sn[k]]
            tamam &= not degisen
            w(f"Banka/kasa/kart/kredi bakiyeleri ({len(once['nakit'])} hesap): {'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            if uygula and not tamam:
                raise CommandError("Kontrol tutmadı; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
