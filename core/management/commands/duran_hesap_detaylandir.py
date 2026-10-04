"""Tek seferlik: duran varlık (253/254/255/260) ve yatırım (258) hesaplarını VARLIK/PROJE bazında detaylandırır
(varsayılan DRY-RUN; ``--uygula`` kalıcı).

 1. Grup hesapları açılır (253.01 … 260.02); 2. her duran varlık kartı / yatırım projesi için 000N hesabı açılır (ad = kart/proje
 adı) ve karta/projeye bağlanır (``DuranVarlik.hesap`` / ``YatirimProjesi.hesap``); 3. 253/255/260 satırları bağlı oldukları kartın,
 258 satırları projenin hesabına taşınır (fatura/banka/kart/kesinti/stok sarf/kur farkı kaynaklı satırlar dahil; kaynak kayıtlar —
 fatura kalemi, stok sarf hareketi — yeni hesabı gösterir). Bağlantısız/eşleşmeyen satıra DOKUNULMAZ, listelenir; böyle satır varsa
 ya da bir kontrol tutmazsa ``--uygula`` iptal edilir (hiçbir şey yazılmaz).

    python manage.py duran_hesap_detaylandir [--uygula]
"""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from core.models import (BankaHesap, DuranVarlik, Fatura, FaturaSatir, HesapPlani, Kasa, Kredi, KrediKarti,
                         StokHareket, YatirimProjesi, YevmiyeFisi, YevmiyeSatir)
from core.sayi import format_tr
from core.services import duran_hesap as dh
from core.services import hesap_plani as hp
from core.services import raporlar
from core.services.yatirim_projesi import proje_toplami

SIFIR = Decimal("0.00")
ESKI = ("253", "254", "255", "258", "260")

# Kart (DV-xxxx) → varlık hesabı; proje (YP-xxxx) → proje hesabı.  Hesap adı = kart/proje adı.
KART_ESLEME = {
    "253.01.0001": "DV-0013", "253.01.0002": "DV-0011", "253.01.0003": "DV-0003",
    "253.02.0001": "DV-0016", "253.02.0002": "DV-0022",
    "253.03.0001": "DV-0005", "253.03.0002": "DV-0004", "253.03.0003": "DV-0006", "253.03.0004": "DV-0018",
    "253.03.0005": "DV-0019", "253.03.0006": "DV-0014",
    "253.04.0001": "DV-0012", "253.04.0002": "DV-0010", "253.04.0003": "DV-0008", "253.04.0004": "DV-0009",
    "255.01.0001": "DV-0007", "255.01.0002": "DV-0015", "255.01.0003": "DV-0025",
    "255.02.0001": "DV-0020", "255.02.0002": "DV-0021", "255.02.0003": "DV-0002",
    "255.03.0001": "DV-0017",
    "260.01.0001": "DV-0001",
}
PROJE_ESLEME = {
    "258.01.0001": "YP-0005", "258.01.0002": "YP-0011", "258.01.0003": "YP-0007", "258.01.0004": "YP-0009",
    "258.01.0005": "YP-0001", "258.01.0006": "YP-0003", "258.01.0007": "YP-0006",
    "258.02.0001": "YP-0008",
    "258.03.0001": "YP-0002", "258.03.0002": "YP-0004",
    "258.04.0001": "YP-0010",
}
BEKLENEN = {"253": Decimal("1179384.31"), "255": Decimal("155644.50"), "258": Decimal("9368817.97"),
            "260": Decimal("16470.87")}


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


def _aile(prefix):
    """Ana hesap + tüm alt hesaplar (_devir zaten alt hesapları toplar)."""
    return _bak(prefix)


class Command(BaseCommand):
    help = "Duran varlik/yatirim hesaplarini varlik/proje bazinda detaylandirir (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    # ------------------------------------------------------------------ yardımcılar
    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def _nakit(self):
        out = {}
        for model, ad in ((BankaHesap, "BANKA"), (Kasa, "KASA"), (KrediKarti, "KART"), (Kredi, "KREDI")):
            for h in model.objects.filter(silindi=False).order_by("pk"):
                out[(ad, h.pk)] = (h.muhasebe_id, _bak(h.muhasebe_id))
        return out

    def _esleme_coz(self):
        kartlar, projeler, hatalar = {}, {}, []
        for hesap, kod in KART_ESLEME.items():
            dv = DuranVarlik.objects.filter(demirbas_kodu=kod, silindi=False).first()
            if dv is None:
                hatalar.append(f"{kod} kartı bulunamadı")
            elif dv.hesap_id != hesap.split(".")[0] and dv.hesap_id != hesap:
                hatalar.append(f"{kod} kartının hesabı {dv.hesap_id}; eşlemedeki {hesap} ailesiyle uyuşmuyor")
            else:
                kartlar[dv.pk] = (dv, hesap)
        for hesap, kod in PROJE_ESLEME.items():
            p = YatirimProjesi.objects.filter(kod=kod, silindi=False).first()
            if p is None:
                hatalar.append(f"{kod} projesi bulunamadı")
            else:
                projeler[p.pk] = (p, hesap)
        for dv in DuranVarlik.objects.filter(silindi=False):
            if dv.pk not in kartlar:
                hatalar.append(f"{dv.demirbas_kodu} kartı eşlemede yok")
        for p in YatirimProjesi.objects.filter(silindi=False):
            if p.pk not in projeler:
                hatalar.append(f"{p.kod} projesi eşlemede yok")
        return kartlar, projeler, hatalar

    def _plan(self, kartlar, projeler):
        """Her ESKİ hesap satırı için hedef hesap: {satir.pk: (hedef, neden)}; eşleşmeyenler ayrı. DB'ye yazmaz."""
        hedef, eslesmeyen = {}, []
        kart_hesap = {pk: h for pk, (dv, h) in kartlar.items()}
        proje_hesap = {pk: h for pk, (p, h) in projeler.items()}
        kod_kart = {dv.demirbas_kodu: h for dv, h in kartlar.values()}
        fatura_fis = {f.fis_id: f for f in Fatura.objects.filter(fis__isnull=False, silindi=False)}
        satirlar = (YevmiyeSatir.objects.filter(hesap_id__in=ESKI).select_related("fis", "hesap")
                    .order_by("fis_id", "id"))
        per_fis = defaultdict(list)
        for s in satirlar:
            per_fis[s.fis_id].append(s)
        for fis_id, lines in per_fis.items():
            kalan = sorted(lines, key=lambda x: (x.silindi or x.fis.silindi, x.id))      # canlı satırlar önce eşleşir

            def ata(s, h, neden):
                hedef[s.pk] = (h, neden)
                kalan.remove(s)

            # 1) satırın kendi projesi (manuel/kart/kesinti/banka/kur farkı)
            for s in list(kalan):
                if s.hesap_id == "258" and s.yatirim_projesi_id in proje_hesap:
                    ata(s, proje_hesap[s.yatirim_projesi_id], "satır projesi")
            # 2) demirbaş satışı maliyet satırı: açıklama "DV-xxxx MALİYET"
            for s in list(kalan):
                ack = (s.aciklama or "")
                if s.hesap_id != "258" and ack.endswith(" MALİYET") and ack.split(" ")[0] in kod_kart:
                    ata(s, kod_kart[ack.split(" ")[0]], "demirbaş satışı")
            # 3) fatura kalemi (tutar eşleşmesi)
            f = fatura_fis.get(fis_id)
            if f is not None and kalan:
                for fs in (f.satirlar.filter(silindi=False, hesap_id__in=ESKI, demirbas__isnull=True)
                           .select_related("fatura").order_by("id")):
                    if fs.hesap_id == "258":
                        h = proje_hesap.get(fs.yatirim_projesi_id)
                    else:
                        dv = fs.duran_varliklar.filter(silindi=False).first()
                        h = kart_hesap.get(dv.pk) if dv else None
                    if h is None:
                        continue
                    net = fs.tutar_tl if f.yon == "ALIS" else -fs.tutar_tl
                    for s in kalan:
                        if s.hesap_id == fs.hesap_id and (s.borc - s.alacak) == net:
                            ata(s, h, f"fatura {f.fatura_no or f.pk} kalemi")
                            break
            # 4) stok sarf hareketi (258 + proje)
            if kalan:
                for hr in StokHareket.objects.filter(fis_id=fis_id, silindi=False, yatirim_projesi__isnull=False):
                    h = proje_hesap.get(hr.yatirim_projesi_id)
                    for s in list(kalan):
                        if h and s.hesap_id == "258":
                            ata(s, h, "stok sarf")
                            break
            # 5) düzenleme geçmişi (silinmiş) satırlar: aynı fişte aynı tutarlı eşleşmiş satırın, yoksa aynı eski hesaptaki
            #    eşleşmiş satırların (tek hedefse) hedefine gider (bakiyeyi etkilemez; üst hesapta satır kalmasın diye)
            for s in list(kalan):
                if not (s.silindi or s.fis.silindi):
                    continue
                esler = [(x, hedef[x.pk][0]) for x in lines if x.pk in hedef]
                ayni_tutar = {h for x, h in esler if (x.borc - x.alacak) == (s.borc - s.alacak)}
                ayni_hesap = {h for x, h in esler if x.hesap_id == s.hesap_id}
                tek = ayni_tutar if len(ayni_tutar) == 1 else (ayni_hesap if len(ayni_hesap) == 1 else set())
                if len(tek) == 1:
                    ata(s, next(iter(tek)), "düzenleme geçmişi")
            for s in kalan:
                eslesmeyen.append(s)
        return hedef, eslesmeyen

    # ------------------------------------------------------------------ ana akış
    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        once = {"mizan": self._mizan(), "nakit": self._nakit(), "aile": {k: _aile(k) for k in BEKLENEN}}
        w(f"\nÖNCE mizan: {once['mizan']}")
        for k, v in once["aile"].items():
            w(f"  {k} ailesi: {format_tr(v)} (beklenen {format_tr(BEKLENEN[k])})")
        kartlar, projeler, hatalar = self._esleme_coz()
        for e in hatalar:
            w(f"  ✗ {e}")
        if hatalar and uygula:
            raise CommandError("Eşleme hatası; --uygula iptal edildi.")
        with transaction.atomic():
            w("\n== 1) Grup hesapları ==")
            for aile, gruplar in dh.GRUPLAR.items():
                if not HesapPlani.objects.filter(hesap_kodu=aile, silindi=False).exists():
                    w(f"  {aile} ana hesabı yok; grupları atlandı")
                    continue
                for kod, ad in gruplar:
                    if HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).exists():
                        w(f"  {kod} zaten var")
                        continue
                    hp.hesap_olustur(kod=kod, ad=ad, ust_kodu=aile, hareketli_ust_izin=True)
                    w(f"  {kod} {ad}: AÇILDI")
            w("\n== 2) Varlık / proje hesapları ==")
            plan_hedef, eslesmeyen = self._plan(kartlar, projeler)       # taşıma planı ESKİ bağlara göre (hesap açmadan önce)
            for pk, (dv, hesap) in sorted(kartlar.items(), key=lambda x: x[1][1]):
                if HesapPlani.objects.filter(hesap_kodu=hesap, silindi=False).exists():
                    w(f"  {hesap} zaten var")
                else:
                    hp.hesap_olustur(kod=hesap, ad=dv.ad[:200], ust_kodu=hesap.rsplit(".", 1)[0], hareketli_ust_izin=True)
                    w(f"  {hesap} {dv.ad[:60]} ← {dv.demirbas_kodu}")
                dv.hesap_id = hesap
                dv.save(update_fields=["hesap", "updated_at"])
            for pk, (p, hesap) in sorted(projeler.items(), key=lambda x: x[1][1]):
                if HesapPlani.objects.filter(hesap_kodu=hesap, silindi=False).exists():
                    w(f"  {hesap} zaten var")
                else:
                    hp.hesap_olustur(kod=hesap, ad=p.ad[:200], ust_kodu=hesap.rsplit(".", 1)[0], hareketli_ust_izin=True)
                    w(f"  {hesap} {p.ad[:60]} ← {p.kod}")
                p.hesap_id = hesap
                p.save(update_fields=["hesap", "updated_at"])

            w("\n== 3) Satır taşıma ==")
            toplam_hedef = defaultdict(lambda: [0, SIFIR])
            nedenler = defaultdict(int)
            for s in YevmiyeSatir.objects.filter(pk__in=list(plan_hedef)).order_by("id"):
                h, neden = plan_hedef[s.pk]
                toplam_hedef[h][0] += 1
                toplam_hedef[h][1] += s.borc - s.alacak
                nedenler[neden.split(" ")[0] + " " + (neden.split(" ")[1] if " " in neden else "")] += 1
            for h in sorted(toplam_hedef):
                n, net = toplam_hedef[h]
                w(f"  → {h}: {n} satır, net {format_tr(net)}")
            w(f"\nEşleşmeyen/bağlantısız satır: {len(eslesmeyen)}")
            for s in eslesmeyen:
                w(f"  ✗ {s.fis.yil}/{s.fis.fis_no} | {s.hesap_id} | B {s.borc} A {s.alacak} | silindi={s.silindi}/{s.fis.silindi} | "
                  f"{s.fis.kaynak} | '{(s.aciklama or s.fis.aciklama or '')[:60]}'")
            if eslesmeyen and uygula:
                raise CommandError("Eşleşmeyen satır var; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            tasinan = 0
            for s in YevmiyeSatir.objects.filter(pk__in=list(plan_hedef)):
                s.hesap_id = plan_hedef[s.pk][0]
                s.save(update_fields=["hesap", "updated_at"])
                tasinan += 1
            w(f"\nTaşınan yevmiye satırı: {tasinan}")
            # kaynak kayıtlar
            fk = 0
            for fs in FaturaSatir.objects.filter(silindi=False, hesap_id__in=ESKI).select_related("demirbas"):
                if fs.demirbas_id:
                    yeni = fs.demirbas.hesap_id
                elif fs.hesap_id == "258":
                    p = projeler.get(fs.yatirim_projesi_id)
                    yeni = p[1] if p else None
                else:
                    dv = fs.duran_varliklar.filter(silindi=False).first()
                    yeni = kartlar[dv.pk][1] if dv and dv.pk in kartlar else None
                if yeni:
                    fs.hesap_id = yeni
                    fs.save(update_fields=["hesap", "updated_at"])
                    fk += 1
            sh = 0
            for hr in StokHareket.objects.filter(silindi=False, karsi_hesap_id="258", yatirim_projesi__isnull=False):
                p = projeler.get(hr.yatirim_projesi_id)
                if p:
                    hr.karsi_hesap_id = p[1]
                    hr.save(update_fields=["karsi_hesap", "updated_at"])
                    sh += 1
            w(f"Kaynak kayıtlar: {fk} fatura kalemi, {sh} stok sarf hareketi yeni hesabı gösteriyor")
            # kalan eski hesap bağları (bilgi)
            kalan_fs = FaturaSatir.objects.filter(silindi=False, hesap_id__in=ESKI).count()
            kalan_dv = DuranVarlik.objects.filter(silindi=False, hesap_id__in=ESKI).count()
            w(f"Eski (üst) hesaba bağlı kalan: fatura kalemi {kalan_fs}, kart {kalan_dv}")

            w("\n== Kontroller ==")
            sonra_mizan = self._mizan()
            tamam = True
            w(f"Mizan: {sonra_mizan} → {'DENGEDE' if sonra_mizan[2] == sonra_mizan[3] else 'DENGESİZ!'}")
            tamam &= sonra_mizan[2] == sonra_mizan[3] and sonra_mizan[:2] == once["mizan"][:2]
            for k, beklenen in BEKLENEN.items():
                v = _aile(k)
                ok = v == beklenen and v == once["aile"][k]
                tamam &= ok
                w(f"{k} ailesi: {format_tr(once['aile'][k])} → {format_tr(v)}  (beklenen {format_tr(beklenen)}) {'✓' if ok else 'FARKLI!'}")
            for eski in ESKI:
                n = YevmiyeSatir.objects.filter(hesap_id=eski).count()
                tamam &= n == 0
                w(f"{eski} (üst hesap) üzerinde kalan satır: {n}")
            for pk, (dv, hesap) in sorted(kartlar.items(), key=lambda x: x[1][1]):
                bek = SIFIR if dv.durum == DuranVarlik.Durum.SATILDI else dv.maliyet
                b = _bak(hesap)
                ok = b == bek
                tamam &= ok
                w(f"  {hesap} {dv.demirbas_kodu} bakiye {format_tr(b)} | kart {format_tr(bek)}"
                  f"{' (satıldı)' if dv.durum == DuranVarlik.Durum.SATILDI else ''} {'✓' if ok else 'FARKLI!'}")
            for pk, (p, hesap) in sorted(projeler.items(), key=lambda x: x[1][1]):
                b, bek = _bak(hesap), proje_toplami(p)
                ok = b == bek
                tamam &= ok
                w(f"  {hesap} {p.kod} bakiye {format_tr(b)} | proje {format_tr(bek)} {'✓' if ok else 'FARKLI!'}")
            sonra_nakit = self._nakit()
            degisen = [k for k in once["nakit"] if once["nakit"][k] != sonra_nakit[k]]
            tamam &= not degisen
            w(f"Banka/kasa/kart/kredi bakiyeleri ({len(once['nakit'])} hesap): "
              f"{'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            if uygula and not tamam:
                raise CommandError("Kontrol tutmadı; --uygula iptal edildi (hiçbir değişiklik yazılmadı).")
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
