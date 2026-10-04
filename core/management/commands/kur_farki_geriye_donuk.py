"""Tek seferlik: MEVCUT döviz hareketleri için ortalama kur + kur farkı satırlarını yeniden hesaplar ve
elle girilmiş kur farkı fişlerini kalıcı siler (yerlerini motorun ürettiği 646/656 satırları alır).
Varsayılan DRY-RUN (transaction geri alınır); ``--uygula`` kalıcı yazar.

Sıra (tek transaction):
 1. Elle girilmiş 4 kur farkı fişi (2026/416, 2026/531, 2026/539, 2026/624) kalıcı silinir
    (denetim kaydıyla; fiş no/açıklama/kaynak doğrulanır, uymayan DOKUNULMAZ).
 2. Tüm döviz havuzları (banka/kasa/cari/çek-senet) tarih sırasıyla baştan hesaplanır: çıkış
    satırları ortalama kurla yazılır, fark aynı fişe 646/656 satırı olur.
Rapor: havuz bazında üretilecek kur farkı (kâr/zarar), önce/sonra TL bakiyeleri, kontroller
(döviz bakiyesi 0 olan havuzda TL bakiye 0; TL banka bakiyeleri değişmedi; mizan dengede).

    python manage.py kur_farki_geriye_donuk [--uygula]
"""
import argparse
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Sum

from core.models import (BankaHesap, Cari, Kasa, SilmeKaydi, YevmiyeFisi, YevmiyeSatir)
from core.services import fis_sil as fs
from core.services import kur_farki as kf
from core.services import raporlar
from core.sayi import format_tr

# (fiş id, yıl, fiş no, açıklamada geçmesi gereken parça)
MANUEL_KUR_FARKLARI = [
    (487, 2026, 416, "VAKIF USD HESABI KUR FARKI"),
    (602, 2026, 531, "HALK USD HESABI KUR FARKI"),
    (610, 2026, 539, "NATUREL DOOR"),
    (695, 2026, 624, "GARANTİ USD HESABI KUR FARKI"),
]
NATUREL_DOOR_CARI_HESABI = "320.30.0039"


def _bakiye(hesap_kodu):
    """Muhasebe hesabının (ve alt hesapların) TL bakiyesi (borç−alacak) — aktif fişlerden."""
    net, _dvz = raporlar._devir(hesap_kodu, datetime.date(2100, 1, 1))
    return net


class Command(BaseCommand):
    help = "Doviz havuzlari icin ortalama kur/kur farki yeniden hesaplar; elle kur farki fislerini siler (dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")
        parser.add_argument(
            "--naturel-door-usd", action=argparse.BooleanOptionalAction, default=True,
            help="(varsayılan AÇIK; kapatmak için --no-naturel-door-usd) Fiş 2026/535'teki Naturel Door TL "
                 "emanet satırını 10.000 USD @45,7175 olarak yeniden yazar (TL aynı) — böylece 3.316 kur "
                 "farkını motor üretir ve cari 0 olur.")

    def _adlar(self):
        ad = {}
        for h in BankaHesap.objects.filter(silindi=False).select_related("banka"):
            ad[h.muhasebe_id] = f"Banka · {h.banka.ad} - {h.ad}"
        for k in Kasa.objects.filter(silindi=False):
            ad[k.muhasebe_id] = f"Kasa · {k.ad}"
        for c in Cari.objects.filter(silindi=False).exclude(muhasebe_kodu=""):
            ad[c.muhasebe_kodu] = f"Cari · {c.unvan[:40]}"
        return ad

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(
            n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def _banka_tl(self):
        """Tüm banka/kasa hesaplarının kapanış TL bakiyesi (kontrol)."""
        out = {}
        for h in BankaHesap.objects.filter(silindi=False).select_related("banka").order_by("pk"):
            out[("BANKA", h.pk)] = (f"{h.banka.ad} - {h.ad} ({h.para_birimi})", h.muhasebe_id, _bakiye(h.muhasebe_id))
        for k in Kasa.objects.filter(silindi=False).order_by("pk"):
            out[("KASA", k.pk)] = (f"{k.ad} ({k.para_birimi})", k.muhasebe_id, _bakiye(k.muhasebe_id))
        return out

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        adlar = self._adlar()
        once_mizan, once_tl = self._mizan(), self._banka_tl()
        nd_once = _bakiye(NATUREL_DOOR_CARI_HESABI)
        w(f"\nÖNCE mizan (aktif fiş, satır, borç, alacak): {once_mizan}")
        w(f"ÖNCE Naturel Door cari ({NATUREL_DOOR_CARI_HESABI}) TL bakiyesi: {nd_once}")
        havuzlar = kf.tum_havuzlar()
        once_havuz = {c: kf.havuz_bakiyesi(*c) for c in havuzlar}
        silinen, atlanan = [], []
        with transaction.atomic():
            w("\n== 1) Elle girilmiş kur farkı fişlerinin kalıcı silinmesi ==")
            for pk, yil, no, parca in MANUEL_KUR_FARKLARI:
                f = YevmiyeFisi.objects.filter(pk=pk).first()
                if f is None:
                    w(f"  2026/{no} (id {pk}): BULUNAMADI")
                    atlanan.append(f"{yil}/{no} bulunamadı")
                    continue
                ozet = fs.fis_ozeti(f)
                sorun = []
                if f.silindi:
                    sorun.append("iptal durumunda")
                if (f.yil, f.fis_no) != (yil, no):
                    sorun.append("fiş no uyuşmuyor")
                if f.kaynak != "MANUEL":
                    sorun.append(f"kaynak MANUEL değil ({f.kaynak})")
                if parca not in f.aciklama:
                    sorun.append("açıklama beklenenle uyuşmuyor")
                w(f"  {ozet['no']} | id {pk} | {f.tarih:%d.%m.%Y} | {format_tr(ozet['tutar'])} TL | '{f.aciklama[:80]}'")
                for s in ozet["satirlar"]:
                    w(f"      {s['hesap']} B {s['borc']} A {s['alacak']}")
                if sorun:
                    w(f"      → DOKUNULMAZ: {'; '.join(sorun)}")
                    atlanan.append(f"{ozet['no']}: {'; '.join(sorun)}")
                    continue
                try:
                    o, veri = fs.fis_kalici_sil(f)
                    veri["temizlik"] = "kur_farki_geriye_donuk"
                    fs._denetim(SilmeKaydi.Tur.FIS, o, veri, None)
                except fs.SilmeHatasi as e:
                    w(f"      → DOKUNULMAZ: {e}")
                    atlanan.append(f"{ozet['no']}: {e}")
                    continue
                silinen.append(ozet["no"])
                w("      → silinecek")

            if opts["naturel_door_usd"]:
                w("\n== 1b) Naturel Door emanet satırı USD'ye çevrilir (fiş 2026/535) ==")
                f535 = YevmiyeFisi.objects.filter(yil=2026, fis_no=535, silindi=False).first()
                sat = (f535.satirlar.filter(silindi=False, hesap_id=NATUREL_DOOR_CARI_HESABI, islem_pb="TRY").first()
                       if f535 else None)
                if sat is None or sat.borc != 457175 or sat.islem_tutari != 457175:
                    w("  → DOKUNULMAZ: 2026/535 bulunamadı ya da cari satırı beklenenle (TRY borç 457.175,00) uyuşmuyor")
                    atlanan.append("2026/535 Naturel Door USD dönüşümü")
                else:
                    w(f"  2026/535: {sat.hesap_id} B {sat.borc} TRY → 10.000,00 USD @ 45,717500 (TL aynı)")
                    sat.islem_pb, sat.islem_tutari, sat.islem_kuru = "USD", Decimal("10000.00"), Decimal("45.7175")
                    sat.save(update_fields=["islem_pb", "islem_tutari", "islem_kuru", "updated_at"])

            w("\n== 2) Havuz bazında yeniden hesap (ortalama kur + 646/656 kur farkı) ==")
            toplam_kar = toplam_zarar = 0
            yatirim_toplam = {}
            for hesap, pb in kf.tum_havuzlar():
                tl_once = _bakiye(hesap)
                r = kf.havuz_yeniden_hesapla(hesap, pb)
                toplam_kar += r["kar"]
                toplam_zarar += r["zarar"]
                for kod, tut in r["yatirim"].items():
                    yatirim_toplam[kod] = yatirim_toplam.get(kod, 0) + tut
                q, v = kf.havuz_bakiyesi(hesap, pb)
                hedef = f" → 258 / {r['proje']}" if r["proje"] else " → 646/656"
                w(f"  {hesap} {pb} [{adlar.get(hesap, '')}]: değişen satır {r['degisen']} | üretilen kur farkı "
                  f"kâr {format_tr(r['kar'])} / zarar {format_tr(r['zarar'])}{hedef} | döviz bakiye {format_tr(q)} → "
                  f"TL havuz bakiyesi {format_tr(v)}" + (f" (ort. kur {format_tr(v / q, 4)})" if q else ""))
                w(f"      hesap TL bakiyesi (tüm satırlar): önce {format_tr(tl_once)} → sonra {format_tr(_bakiye(hesap))}")
            w(f"\nToplam üretilen kur farkı: kâr {format_tr(toplam_kar)} / zarar {format_tr(toplam_zarar)} "
              f"(yatırım carilerinin kur farkı 258'e, kalanı 646/656'ya yazılır)")
            w("Yatırım maliyetine (258) yazılan kur farkı — proje bazında (+ maliyet artışı / − azalış):")
            for kod, tut in sorted(yatirim_toplam.items()):
                w(f"  {kod}: {format_tr(tut)} TL")
            if not yatirim_toplam:
                w("  yok")
            w(f"Silinen elle kur farkı fişleri: {silinen or '-'} | dokunulmayan: {atlanan or 'yok'}")

            w("\n== Kontroller ==")
            sonra_mizan, sonra_tl = self._mizan(), self._banka_tl()
            dengeli = sonra_mizan[2] == sonra_mizan[3]
            w(f"Mizan: {sonra_mizan} → {'DENGEDE' if dengeli else 'DENGESİZ!'}")
            bozuk = []
            for c in kf.tum_havuzlar():
                q, v = kf.havuz_bakiyesi(*c)
                if q == 0 and v != 0:
                    bozuk.append((c, str(v)))
            w(f"Döviz bakiyesi 0 olup TL bakiyesi ≠ 0 kalan havuz: {bozuk or 'YOK (hepsi 0)'}")
            w(f"Naturel Door cari ({NATUREL_DOOR_CARI_HESABI}) TL bakiyesi: önce {nd_once} → sonra {_bakiye(NATUREL_DOOR_CARI_HESABI)}")
            w("Banka/kasa TL bakiyeleri (önce → sonra):")
            for key, (ad, kod, v1) in sonra_tl.items():
                v0 = once_tl[key][2]
                isaret = "değişmedi" if v0 == v1 else "DEĞİŞTİ"
                w(f"  {key[0]} {key[1]} {ad} [{kod}]: {format_tr(v0)} → {format_tr(v1)}  ({isaret})")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
