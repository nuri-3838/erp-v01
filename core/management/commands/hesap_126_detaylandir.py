"""Tek seferlik: 126 VERİLEN DEPOZİTO VE TEMİNATLAR hesabını detaylandırır (varsayılan DRY-RUN; ``--uygula`` kalıcı).

 1. Alt hesaplar açılır: 126.01 UYAP İHALE TEMİNATLARI, 126.02 MİMARSİNAN OSB ABONELİK TEMİNATI (126 üst hesap olur).
 2. 126'daki mevcut yevmiye satırlarının hesabı fiş/satır açıklamasına göre taşınır:
      "UYAP" geçen            → 126.01
      "MİMARSİNAN OSB" geçen  → 126.02
    (Banka hareketlerinin karşı hesabı fişin satırıdır; aynı işlemle güncellenir.) Eşleşmeyen ya da birden çok kurala uyan
    satıra DOKUNULMAZ — listelenir; bu durumda hiçbir şey uygulanmaz.
 Kontroller (öncesi/sonrası): 126 ailesi toplamı, 126.01/126.02 bakiyeleri, 126'nın kendisinde satır, banka/kasa/kart/kredi
 bakiyeleri, mizan dengesi.

    python manage.py hesap_126_detaylandir [--uygula]
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import BankaHesap, HesapPlani, Kasa, Kredi, KrediKarti, YevmiyeFisi, YevmiyeSatir
from core.sayi import format_tr
from core.services import hesap_plani as hp
from core.services import hesap_tasima as ht
from core.services import raporlar

KAYNAK = "126"
ALT_HESAPLAR = [("126.01", "UYAP İHALE TEMİNATLARI"), ("126.02", "MİMARSİNAN OSB ABONELİK TEMİNATI")]
KURALLAR = [("126.01", ["UYAP"]), ("126.02", ["MİMARSİNAN OSB"])]


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


class Command(BaseCommand):
    help = "126 hesabini 126.01/126.02 alt hesaplarina boler (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(
            n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def _nakit(self):
        out = {}
        for model, ad in ((BankaHesap, "BANKA"), (Kasa, "KASA"), (KrediKarti, "KART"), (Kredi, "KREDI")):
            for h in model.objects.filter(silindi=False).order_by("pk"):
                out[(ad, h.pk)] = (getattr(h, "ad", ""), h.muhasebe_id, _bak(h.muhasebe_id))
        return out

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        once = {"mizan": self._mizan(), "126": _bak(KAYNAK), "nakit": self._nakit()}
        w(f"\nÖNCE mizan: {once['mizan']} | 126 toplamı (borç−alacak): {format_tr(once['126'])}")
        with transaction.atomic():
            w("\n== 1) Alt hesaplar ==")
            for kod, ad in ALT_HESAPLAR:
                mevcut = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
                if mevcut:
                    w(f"  {kod} zaten var: {mevcut.hesap_adi}")
                    continue
                h = hp.hesap_olustur(kod=kod, ad=ad, ust_kodu=KAYNAK, hareketli_ust_izin=True)
                w(f"  {h.hesap_kodu} {h.hesap_adi}: AÇILACAK (rapor grubu {h.rapor_grubu}, kalem {h.rapor_kalemi}, parasal {h.parasal})")

            w("\n== 2) Satır taşıma ==")
            p = ht.plan(KAYNAK, KURALLAR)
            for hedef, satirlar in p.eslesen.items():
                net = sum((s.borc - s.alacak for s in satirlar), 0)
                w(f"  → {hedef}: {len(satirlar)} satır, net {format_tr(net)}")
                for s in satirlar:
                    w(f"      {s.fis.yil}/{s.fis.fis_no} | {s.fis.tarih:%d.%m.%Y} | {s.fis.kaynak} banka {s.fis.banka_hesap_id} | "
                      f"B {s.borc} A {s.alacak} | {(s.fis.aciklama or s.aciklama)[:70]}")
            w(f"\nEşleşmeyen/belirsiz satır: {len(p.eslesmeyen)}")
            for s, neden in p.eslesmeyen:
                w(f"  ✗ {s.fis.yil}/{s.fis.fis_no} | B {s.borc} A {s.alacak} | {neden} | '{(s.fis.aciklama or s.aciklama)[:70]}'")
            if p.eslesmeyen:
                w("\n→ Eşleşmeyen satır var: HİÇBİR ŞEY TAŞINMAYACAK (önce kuralları/verileri netleştirin).")
                if uygula:
                    raise CommandError("Eşleşmeyen satır var; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            else:
                n = ht.uygula(p)
                w(f"\nTaşınan satır: {n}")

            w("\n== Kontroller ==")
            sonra_mizan = self._mizan()
            w(f"Mizan: {sonra_mizan} → {'DENGEDE' if sonra_mizan[2] == sonra_mizan[3] else 'DENGESİZ!'}")
            w(f"126 toplamı: {format_tr(once['126'])} → {format_tr(_bak(KAYNAK))}  "
              f"({'değişmedi' if _bak(KAYNAK) == once['126'] else 'DEĞİŞTİ!'})")
            for kod, _ad in ALT_HESAPLAR:
                w(f"{kod} bakiyesi: {format_tr(_bak(kod))}")
            kalan = YevmiyeSatir.objects.filter(hesap_id=KAYNAK).count()
            w(f"126'nın kendisinde kalan satır: {kalan}")
            sonra_nakit = self._nakit()
            degisen = [k for k in once["nakit"] if once["nakit"][k] != sonra_nakit[k]]
            w(f"Banka/kasa/kart/kredi bakiyeleri ({len(once['nakit'])} hesap): "
              f"{'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
