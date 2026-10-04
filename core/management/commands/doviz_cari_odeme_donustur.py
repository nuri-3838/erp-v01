"""Tek seferlik: döviz carilerin TL havuzundaki ÖDEME satırlarını ödeme günü TCMB döviz ALIŞ kuruyla dövize çevirir
(varsayılan DRY-RUN; ``--uygula`` kalıcı). Genel kural için bkz. core.services.doviz_cari.

 * FORMAL (320.10.0001): TL ödemeler (borç satırları) ödeme sırasıyla ÖNCE USD borcuna sayılır; USD kapasitesi (açık USD borcu)
   yetmezse EUR'a; ikisi de yetmezse satır listelenir ve DOKUNULMAZ. Satırın TL tutarı aynı kalır; döviz tutarı = TL / alış kuru.
   ``--aciklama-dovizine-uy``: açıklamada tek bir döviz (EUR/USD) açıkça geçen satır o dövize sayılır (varsayılan KAPALI; dry-run
   açıklamada döviz geçen satırları ayrıca uyarır).
 * ARGEMA (320.30.0001): carinin para birimi EUR yapılır; 01.06.2026 100.000 TL ödeme (fiş 2026/300) EUR'a çevrilir; 22.05.2026 çek
   bordrosu #9'un (2 × 790.000 TL) cari satırı ARA HESABA taşınır ve çekler 'ara hesapta' işaretlenir (çek ödendiği gün o günün
   alış kuruyla cari EUR borcundan düşülür). ``--ara-hesap`` zorunlu (yoksa dry-run/uygula içinde açılır; ``--ara-hesap-adi``).
 * Diğer döviz cariler: TL havuzlarındaki ödeme satırları yalnız BİLGİ olarak listelenir (dokunulmaz).
Kur farkı motoru etkilenen havuzları yeniden hesaplar (646/656; yatırım carisi kuralı geçerli).
Kontroller: mizan dengesi, banka/kasa/kart/kredi bakiyeleri, cari bazında önce/sonra USD/EUR/TL bakiyeleri, oluşan kur farkı.

    python manage.py doviz_cari_odeme_donustur --ara-hesap 159.20.0001 [--cari 320.30.0001] [--ara-hesap-adi ADI] [--aciklama-dovizine-uy] [--uygula]

 ``--cari``: yalnız o cariyi işler (320.10.0001 Formal ya da 320.30.0001 Argema); verilmezse ikisi de. Filtre varken diğer cariye
 DOKUNULMAZ ve bilgi listesi (bölüm 3) yazılmaz.
"""
import datetime
import re
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import (BankaHesap, Cari, CekHesapAyari, CekSenet, CekBordrosu, HesapPlani, Kasa, Kredi, KrediKarti,
                         YevmiyeFisi, YevmiyeSatir)
from core.sayi import format_tr, yuvarla
from core.services import doviz_cari
from core.services import hesap_plani as hp
from core.services import kur_farki as kf
from core.services import raporlar

SIFIR = Decimal("0.00")
FORMAL = "320.10.0001"
ARGEMA = "320.30.0001"
ARGEMA_100K_FIS = (2026, 300)
ARGEMA_CEK_BORDRO_FIS = (2026, 181)


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


def _havuzlar(kod):
    """{pb: (döviz net borç, TL net borç)} — aktif fişlerden (borç − alacak)."""
    h = defaultdict(lambda: [SIFIR, SIFIR])
    for s in YevmiyeSatir.objects.filter(hesap_id=kod, silindi=False, fis__silindi=False):
        h[s.islem_pb][0] += s.islem_tutari if s.borc else -s.islem_tutari
        h[s.islem_pb][1] += s.borc - s.alacak
    return {pb: tuple(v) for pb, v in h.items()}


def _fmt_havuz(h):
    out = []
    for pb in ("USD", "EUR", "GBP", "TRY"):
        if pb in h and (h[pb][0] != 0 or h[pb][1] != 0):
            d, t = h[pb]
            out.append(f"{pb} {format_tr(abs(d))}{'' if pb == 'TRY' else ' / ' + format_tr(abs(t)) + ' TL'} "
                       f"{'BORÇ' if d > 0 or (d == 0 and t > 0) else 'ALACAK'}")
    return " | ".join(out) or "—"


class Command(BaseCommand):
    help = "Doviz carilerin TL odeme satirlarini odeme gunu TCMB alis kuruyla dovize cevirir (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")
        parser.add_argument("--ara-hesap", required=True, help="Döviz carilere verilen çekler ara hesabı (yaprak; yoksa açılır)")
        parser.add_argument("--ara-hesap-adi", default="DÖVİZ CARİLERE VERİLEN ÇEKLER (ÖDEME BEKLEYEN)")
        parser.add_argument("--aciklama-dovizine-uy", action="store_true")
        parser.add_argument("--cari", choices=(FORMAL, ARGEMA), default=None,
                            help="Yalnız bu carinin hesabını işle (verilmezse Formal + Argema)")

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def _nakit(self):
        out = {}
        for model, ad in ((BankaHesap, "BANKA"), (Kasa, "KASA"), (KrediKarti, "KART"), (Kredi, "KREDI")):
            for h in model.objects.filter(silindi=False).order_by("pk"):
                out[(ad, h.pk)] = (h.muhasebe_id, _bak(h.muhasebe_id))
        return out

    def _ara_hesap_ac(self, kod, ad, w):
        h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
        if h is not None:
            if hp._alt_hesaplar_qs(kod).exists():
                raise CommandError(f"{kod} yaprak hesap değil.")
            w(f"  ara hesap {kod} zaten var: {h.hesap_adi}")
            return h
        parcalar = kod.split(".")
        for i in range(2, len(parcalar) + 1):
            ust = ".".join(parcalar[:i - 1])
            kk = ".".join(parcalar[:i])
            if HesapPlani.objects.filter(hesap_kodu=kk, silindi=False).exists():
                continue
            son = i == len(parcalar)
            hp.hesap_olustur(kod=kk, ad=(ad if son else "DÖVİZ CARİLERE VERİLEN ÇEKLER"), ust_kodu=ust, hareketli_ust_izin=True)
            w(f"  ara hesap {kk} AÇILACAK")
        return HesapPlani.objects.get(hesap_kodu=kod)

    def _donustur(self, s, pb, w=None):
        kur = doviz_cari.alis_kuru(pb, s.fis.tarih)
        tl = s.borc or s.alacak
        dvz = yuvarla(tl / kur, 2)
        s.islem_pb, s.islem_tutari, s.islem_kuru = pb, dvz, kur
        s.save(update_fields=["islem_pb", "islem_tutari", "islem_kuru", "updated_at"])
        return dvz, kur

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        formal = Cari.objects.filter(muhasebe_kodu=FORMAL, silindi=False).first()
        argema = Cari.objects.filter(muhasebe_kodu=ARGEMA, silindi=False).first()
        if formal is None or argema is None:
            raise CommandError("Formal ya da Argema carisi bulunamadı.")
        kapsam = (opts["cari"],) if opts["cari"] else (FORMAL, ARGEMA)
        w(f"Kapsam: {', '.join(kapsam)}")
        once = {"mizan": self._mizan(), "nakit": self._nakit(),
                "havuz": {k: _havuzlar(k) for k in kapsam},
                "kf": {k: _bak(k) for k in ("646", "656", "258")}}
        w(f"\nÖNCE mizan: {once['mizan']}")
        if FORMAL in kapsam:
            w(f"Formal ({FORMAL}, {formal.para_birimi}): {_fmt_havuz(once['havuz'][FORMAL])}")
        if ARGEMA in kapsam:
            w(f"Argema ({ARGEMA}, {argema.para_birimi}): {_fmt_havuz(once['havuz'][ARGEMA])}")
        sorunlar = []
        with transaction.atomic():
            w("\n== 0) Ara hesap ==")
            ara = self._ara_hesap_ac(opts["ara_hesap"], opts["ara_hesap_adi"], w)
            ayar = CekHesapAyari.get()
            ayar.doviz_cari_ara = ara
            ayar.save(update_fields=["doviz_cari_ara", "updated_at"])
            w(f"  Çek/Senet ayarı: döviz cari ara hesabı = {ara.hesap_kodu} {ara.hesap_adi}")

            havuzlar = set()
            # ------------------------------------------------------------ FORMAL
            w(f"\n== 1) FORMAL — TL ödemeler döviz borcuna ==")
            if FORMAL not in kapsam:
                w("  (kapsam dışı — DOKUNULMADI)")
            kap = {pb: -once["havuz"].get(FORMAL, {}).get(pb, (SIFIR, SIFIR))[0] for pb in ("USD", "EUR")}   # açık döviz borcu (alacak bakiye)
            if FORMAL in kapsam:
                w(f"  kapasite (açık döviz borcu): USD {format_tr(kap['USD'])} | EUR {format_tr(kap['EUR'])}")
            lines = (YevmiyeSatir.objects.filter(hesap_id=FORMAL, islem_pb="TRY", silindi=False, fis__silindi=False, borc__gt=0)
                     .select_related("fis").order_by("fis__tarih", "id")) if FORMAL in kapsam else []
            toplam_tl = SIFIR
            sayac = defaultdict(lambda: [0, SIFIR, SIFIR])
            for s in lines:
                f = s.fis
                tl = s.borc
                ack = (f.aciklama or s.aciklama or "")
                bulunan = sorted(set(re.findall(r"\b(USD|EUR)\b", ack.upper())))
                tercih = ["USD", "EUR"]
                if opts["aciklama_dovizine_uy"] and len(bulunan) == 1:
                    tercih = [bulunan[0]]
                secilen = None
                for pb in tercih:
                    try:
                        kur = doviz_cari.alis_kuru(pb, f.tarih)
                    except doviz_cari.DovizCariHatasi:
                        continue
                    dvz = yuvarla(tl / kur, 2)
                    if dvz <= kap[pb]:
                        secilen = (pb, kur, dvz)
                        break
                uyari = f"  ⚠ açıklamada {'/'.join(bulunan)} geçiyor" if bulunan and not (opts["aciklama_dovizine_uy"] and len(bulunan) == 1) else ""
                if secilen is None:
                    sorunlar.append(s)
                    w(f"  ✗ {f.yil}/{f.fis_no} {f.tarih:%d.%m.%Y} {f.kaynak} {format_tr(tl)} TL → kapasite/kur yok; DOKUNULMADI{uyari}")
                    continue
                pb, kur, dvz = secilen
                kap[pb] -= dvz
                self._donustur(s, pb)
                havuzlar.add((FORMAL, pb))
                sayac[pb][0] += 1
                sayac[pb][1] += tl
                sayac[pb][2] += dvz
                toplam_tl += tl
                w(f"  {f.yil}/{f.fis_no} {f.tarih:%d.%m.%Y} {f.kaynak:11} {format_tr(tl):>14} TL → {format_tr(dvz):>12} {pb} @ {kur}{uyari} | {(ack)[:42]}")
            for pb, (n, tl, dv) in sayac.items():
                w(f"  → {pb}: {n} satır, {format_tr(tl)} TL = {format_tr(dv)} {pb}")

            # ------------------------------------------------------------ ARGEMA
            w(f"\n== 2) ARGEMA — para birimi EUR; 100.000 TL → EUR; çek bordrosu #9 → ara hesap ==")
            if ARGEMA not in kapsam:
                w("  (kapsam dışı — DOKUNULMADI)")
            elif argema.para_birimi != "EUR":
                argema.para_birimi = "EUR"
                argema.save(update_fields=["para_birimi", "updated_at"])
                w("  Argema para birimi: TRY → EUR")
            if ARGEMA in kapsam:
                s100 = YevmiyeSatir.objects.filter(hesap_id=ARGEMA, fis__yil=ARGEMA_100K_FIS[0], fis__fis_no=ARGEMA_100K_FIS[1],
                                                   silindi=False, fis__silindi=False, borc__gt=0).select_related("fis").first()
                if s100 is None or s100.borc != Decimal("100000.00") or s100.fis.tarih != datetime.date(2026, 6, 1):
                    sorunlar.append(s100)
                    w("  ✗ 2026/300 (01.06.2026, 100.000 TL) satırı bulunamadı/uymuyor; DOKUNULMADI")
                elif s100.islem_pb == "TRY":
                    dvz, kur = self._donustur(s100, "EUR")
                    havuzlar.add((ARGEMA, "EUR"))
                    w(f"  2026/300 01.06.2026: 100.000,00 TL → {format_tr(dvz)} EUR @ {kur}")
                bordro = CekBordrosu.objects.filter(fisler__yil=ARGEMA_CEK_BORDRO_FIS[0], fisler__fis_no=ARGEMA_CEK_BORDRO_FIS[1],
                                                    fisler__silindi=False).first()
                cekler = list(bordro.cek_senetler.all()) if bordro else []
                sc = (YevmiyeSatir.objects.filter(hesap_id=ARGEMA, fis__cek_bordrosu=bordro, silindi=False, borc__gt=0).first()
                      if bordro else None)
                if (bordro is None or bordro.tur != CekBordrosu.Tur.FIRMA_CIKIS or bordro.tarih != datetime.date(2026, 5, 22)
                        or len(cekler) != 2 or sum(c.tutar for c in cekler) != Decimal("1580000.00")
                        or sc is None or sc.borc != Decimal("1580000.00")):
                    sorunlar.append(None)
                    w("  ✗ çek bordrosu #9 (22.05.2026, 2 × 790.000 TL) beklenen yapıda değil; DOKUNULMADI")
                else:
                    sc.hesap_id = ara.hesap_kodu
                    sc.save(update_fields=["hesap", "updated_at"])
                    for c in cekler:
                        c.ara_hesapta = True
                        c.save(update_fields=["ara_hesapta", "updated_at"])
                    w(f"  çek bordrosu #{bordro.pk} ({bordro.tarih:%d.%m.%Y}): {format_tr(sc.borc)} TL cari satırı {FORMAL.replace(FORMAL, ARGEMA)} → "
                      f"{ara.hesap_kodu}; {len(cekler)} çek 'ara hesapta' (vade {', '.join(f'{c.vade:%d.%m.%Y}' for c in cekler)})")

            # ------------------------------------------------------------ bilgi: diğer döviz cariler
            w("\n== 3) Diğer döviz cariler (BİLGİ — dokunulmaz) ==")
            for c in (Cari.objects.none() if opts["cari"] else Cari.objects.filter(silindi=False)).exclude(para_birimi="TRY").exclude(muhasebe_kodu__in=(FORMAL, ARGEMA)).order_by("muhasebe_kodu"):
                q = YevmiyeSatir.objects.filter(hesap_id=c.muhasebe_kodu, islem_pb="TRY", silindi=False, fis__silindi=False)
                n, b, a = q.count(), sum((x.borc for x in q), SIFIR), sum((x.alacak for x in q), SIFIR)
                if n:
                    w(f"  {c.muhasebe_kodu} {c.unvan[:34]} ({c.para_birimi}): TL havuzunda {n} satır, borç {format_tr(b)} / alacak {format_tr(a)} TL")

            # ------------------------------------------------------------ kur farkı
            w("\n== 4) Kur farkı motoru ==")
            kf.havuzlari_yeniden_hesapla(havuzlar)
            w(f"  yeniden hesaplanan havuzlar: {sorted(havuzlar)}")

            w("\n== Kontroller ==")
            tamam = not sorunlar
            sonra_mizan = self._mizan()
            ok = sonra_mizan[2] == sonra_mizan[3]
            tamam &= ok
            w(f"Mizan: {sonra_mizan} → {'DENGEDE' if ok else 'DENGESİZ!'}")
            for kod, ad in ((FORMAL, "Formal"), (ARGEMA, "Argema")):
                if kod not in kapsam:
                    continue
                w(f"{ad} ÖNCE : {_fmt_havuz(once['havuz'][kod])}")
                w(f"{ad} SONRA: {_fmt_havuz(_havuzlar(kod))}")
            for k in ("646", "656", "258"):
                w(f"Kur farkı etkisi — {k}: {format_tr(once['kf'][k])} → {format_tr(_bak(k))} (fark {format_tr(_bak(k) - once['kf'][k])})")
            w(f"Ara hesap {ara.hesap_kodu} bakiyesi: {format_tr(_bak(ara.hesap_kodu))} (Argema çekleri 1.580.000,00 BORÇ beklenir)")
            ok = _bak(ara.hesap_kodu) == (Decimal("1580000.00") if ARGEMA in kapsam else Decimal("0.00"))
            tamam &= ok
            sn = self._nakit()
            degisen = [k for k in once["nakit"] if once["nakit"][k] != sn[k]]
            tamam &= not degisen
            w(f"Banka/kasa/kart/kredi bakiyeleri ({len(once['nakit'])} hesap): "
              f"{'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            if uygula and not tamam:
                raise CommandError("Kontrol tutmadı / eşleşmeyen satır var; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
