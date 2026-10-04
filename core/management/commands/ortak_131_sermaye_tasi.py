"""Tek seferlik: ortak hareketlerini 131.01 NURİ ÖZER hesabından Nuri Özer sermaye carisine (500-10-0001, hesap 500.10.0001) taşır
(varsayılan DRY-RUN; ``--uygula`` kalıcı).

 * 131.01'deki TÜM yevmiye satırları (banka/kasa/kart hareketleri, faturalar, manuel fişler; tüm tarihler, düzenleme geçmişi dahil)
   500.10.0001'e taşınır: yön, tutar, döviz bilgisi, tarih, fiş no AYNI; karşı hesaplar (banka, 258 vb.) değişmez.
 * Kaynak kayıtlar yeni hesabı gösterir: şahsi alış faturalarının ortak hesabı (``Fatura.sahsi_ortak``) 500.10.0001 olur
   (banka/kasa/kart hareketinin karşı tarafı fişin satırının kendisidir → aynı işlemle güncellenir).
 * Cari bağlantısı: 500-10-0001 NURİ ÖZER carisi (muhasebe_kodu = 500.10.0001) hesaba bağlıdır; cari ekstresi hesaptan okunur.
 * 131.01 hesabı hareketsiz KALIR (silinmez) ve pasifleştirilir → yeni kayıtlarda önerilmez.
 Kontroller (öncesi/sonrası): 131.01 bakiyesi 0 ve satır 0; 500.10.0001 = 16.588.966,79 ALACAK; cari ekstresi bu tutarı gösterir;
 banka/kasa/kart/kredi bakiyeleri; mizan dengesi. Tutmayan kontrolde ``--uygula`` iptal edilir (hiçbir şey yazılmaz).

    python manage.py ortak_131_sermaye_tasi [--uygula]
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import (BankaHesap, Cari, Fatura, HesapPlani, Kasa, Kredi, KrediKarti, YevmiyeFisi, YevmiyeSatir)
from core.sayi import format_tr
from core.services import raporlar

KAYNAK = "131.01"
HEDEF = "500.10.0001"
BEKLENEN_SATIR = 85
BEKLENEN_NET = Decimal("-16588966.79")          # borç − alacak (negatif = ALACAK bakiye)


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


class Command(BaseCommand):
    help = "131.01 ortak hareketlerini 500.10.0001 (Nuri Ozer sermaye carisi) hesabina tasir (varsayilan dry-run)."

    def add_arguments(self, parser):
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
        kaynak = HesapPlani.objects.filter(hesap_kodu=KAYNAK, silindi=False).first()
        hedef = HesapPlani.objects.filter(hesap_kodu=HEDEF, silindi=False, aktif=True).first()
        cari = Cari.objects.filter(muhasebe_kodu=HEDEF, silindi=False).first()
        for ad, nesne in (("131.01 hesabı", kaynak), ("500.10.0001 hesabı", hedef), ("500-10-0001 carisi", cari)):
            if nesne is None:
                raise CommandError(f"{ad} bulunamadı.")
        w(f"Cari: {cari.kod} {cari.unvan} → hesap {cari.muhasebe_kodu}")
        satirlar = YevmiyeSatir.objects.filter(hesap_id=KAYNAK)
        once = {"mizan": self._mizan(), "nakit": self._nakit(), "131": _bak("131"), "131.01": _bak(KAYNAK),
                "hedef": _bak(HEDEF), "n": satirlar.count(), "n_hedef": YevmiyeSatir.objects.filter(hesap_id=HEDEF).count()}
        w(f"\nÖNCE mizan: {once['mizan']}")
        w(f"131.01: {once['n']} satır, bakiye {format_tr(once['131.01'])} | 500.10.0001: {once['n_hedef']} satır, bakiye {format_tr(once['hedef'])}")
        from collections import Counter
        w("Kaynak dağılımı: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(satirlar.values_list("fis__kaynak", flat=True)).items())))
        w("Para birimi: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(satirlar.values_list("islem_pb", flat=True)).items())))
        w(f"Şahsi alış faturası (ortak hesabı 131.01): {Fatura.objects.filter(sahsi_ortak_id=KAYNAK).count()}")
        with transaction.atomic():
            tasinan = 0
            for s in satirlar:
                s.hesap_id = HEDEF
                s.save(update_fields=["hesap", "updated_at"])
                tasinan += 1
            w(f"\nTaşınan yevmiye satırı: {tasinan}")
            fat = Fatura.objects.filter(sahsi_ortak_id=KAYNAK).update(sahsi_ortak_id=HEDEF)
            w(f"Kaynak kayıt: {fat} şahsi alış faturasının ortak hesabı {HEDEF} oldu")
            kaynak.aktif = False                          # hareketsiz kalır, yeni kayıtlarda önerilmez (silinmez)
            kaynak.save(update_fields=["aktif", "updated_at"])
            w("131.01 pasifleştirildi (silinmedi).")
            tamam = True
            w("\n== Kontroller ==")
            sonra = self._mizan()
            ok = sonra[2] == sonra[3] and sonra[:2] == once["mizan"][:2]
            tamam &= ok
            w(f"Mizan: {sonra} → {'DENGEDE' if ok else 'SORUNLU!'}")
            n131 = YevmiyeSatir.objects.filter(hesap_id=KAYNAK).count()
            ok = _bak(KAYNAK) == 0 and n131 == 0
            tamam &= ok
            w(f"131.01: bakiye {format_tr(_bak(KAYNAK))}, kalan satır {n131} {'✓' if ok else 'FARKLI!'}")
            ok = _bak("131") == 0
            tamam &= ok
            w(f"131 (aile) bakiyesi: {format_tr(_bak('131'))} {'✓' if ok else 'FARKLI!'}")
            hb = _bak(HEDEF)
            ok = hb == BEKLENEN_NET and YevmiyeSatir.objects.filter(hesap_id=HEDEF).count() == BEKLENEN_SATIR
            tamam &= ok
            w(f"500.10.0001: {format_tr(abs(hb))} {'ALACAK' if hb < 0 else 'BORÇ'}, {YevmiyeSatir.objects.filter(hesap_id=HEDEF).count()} satır "
              f"(beklenen {format_tr(abs(BEKLENEN_NET))} ALACAK, {BEKLENEN_SATIR} satır) {'✓' if ok else 'FARKLI!'}")
            eks = raporlar.ekstre_devirli(HEDEF)
            ok = (eks.acilis + eks.bakiye) == BEKLENEN_NET
            tamam &= ok
            w(f"Cari ekstresi ({cari.unvan}): kapanış {format_tr(abs(eks.acilis + eks.bakiye))} "
              f"{'ALACAK' if eks.acilis + eks.bakiye < 0 else 'BORÇ'} {'✓' if ok else 'FARKLI!'}")
            sn = self._nakit()
            degisen = [k for k in once["nakit"] if once["nakit"][k] != sn[k]]
            tamam &= not degisen
            w(f"Banka/kasa/kart/kredi bakiyeleri ({len(once['nakit'])} hesap): "
              f"{'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            if uygula and not tamam:
                raise CommandError("Kontrol tutmadı; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
