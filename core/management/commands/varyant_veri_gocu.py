"""VARYANT VERİ GÖÇÜ (E-2, docs/varyant_kararlari.md karar 3/5): 53 kart varyantlı yapılır, mevcut TÜM hareketleri ve ayırmaları tek
varyanta aktarılır — kesilmiş A tipi ön ayak (151-10-0001…0005) ve çift çıkış SOL ayak (151-10-0014/16/18/20/22/24) → YOK (markasız),
diğerleri → SEMTA (standart). Kartın ``varsayilan_varyant``ı da o varyant olur (varyant verilmeden yazılan hareket oraya gider).

Varsayılan KURU çalıştırma: her şey bir transaction içinde yapılır, ALTIN KONTROL raporu alınır ve GERİ ALINIR. ``--uygula`` yalnız altın
kontrol birebir eşitse kalıcı yazar; tek kuruş/adet fark varsa geri alır ve raporlar. ``--kod`` ile plan alt kümesi (test/parçalı).

ALTIN KONTROL (önce/sonra): her kart için eldeki, maliyet miktarı, stok değeri TL/USD, ortalama TL/USD + her hareketin tutar/durum/ortalaması;
stok değerleme 150/151/152 (stok değeri, mizan, fark); mizan 150/151/152/620; açık ÜS'lerin (ör. ÜS-2026-0002) stok bazında ayırmaları.

    python manage.py varyant_veri_gocu                 # kuru
    python manage.py varyant_veri_gocu --uygula        # kalıcı (önce DB yedeği!)
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Stok, StokAyirma, StokHareket, UretimEmri, Varyant
from core.services import stok_ayirma
from core.services import stok_ortalama as so
from core.services import varyant as varyant_servis
from core.services.hareket import toplu_eldeki

SIFIR = Decimal("0")
AYAK_YOK = [f"151-10-{i:04d}" for i in range(1, 6)] + [f"151-10-{i:04d}" for i in (14, 16, 18, 20, 22, 24)]
MARKALAMA_SEMTA = ([f"151-20-{i:04d}" for i in range(1, 6)] + [f"151-80-{i:04d}" for i in range(1, 6)]
                   + [f"151-80-{i:04d}" for i in range(11, 17)] + [f"151-99-{i:04d}" for i in range(1, 12)])
AMBALAJ = {"150-50-0001": ["AFIS"], "150-50-0003": ["AFIS"], "150-50-0002": ["YAN_ETIKET"], "150-50-0004": ["KILAVUZ"]}
MAMUL = [f"152-10-{i:04d}" for i in range(1, 6)] + [f"152-22-{i:04d}" for i in range(1, 7)]
DORT_BOYUT = ["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"]
MIZAN_HESAPLARI = ("150", "151", "152", "620")


def varsayilan_plan() -> list:
    """[(kod, boyutlar, hedef varyant kodu)] — 53 kart."""
    plan = [(k, ["MARKALAMA"], "YOK") for k in AYAK_YOK]
    plan += [(k, ["MARKALAMA"], "SEMTA") for k in MARKALAMA_SEMTA]
    plan += [(k, b, "SEMTA") for k, b in AMBALAJ.items()]
    plan += [(k, DORT_BOYUT, "SEMTA") for k in MAMUL]
    return plan


def altin_kontrol(stok_pkler) -> dict:
    """Önce/sonra karşılaştırılacak her şey (Decimal'ler aynen; sözlük eşitliği birebir)."""
    kartlar = {}
    eldeki = toplu_eldeki(stok_pkler)
    for s in Stok.objects.filter(pk__in=stok_pkler).order_by("kod"):
        kartlar[s.kod] = (eldeki.get(s.pk, SIFIR), s.maliyet_miktar, s.maliyet_deger_try, s.maliyet_deger_usd, s.ort_maliyet_try, s.ort_maliyet_usd)
    hareketler = {h.pk: (h.tutar_try, h.tutar_usd, h.maliyet_durumu, h.sonrasi_miktar, h.sonrasi_deger_try, h.sonrasi_ort_try)
                  for h in StokHareket.objects.filter(stok_id__in=stok_pkler, silindi=False)}
    d = so.degerleme_raporu()
    degerleme = {k["kod"]: (k["stok_degeri"], k["mizan"], k["fark"], k["toplam_deger"]) for k in d["karsilastirma"] if k["kod"] in ("150", "151", "152")}
    mizan = {k: so._mizan_bakiyesi(k) for k in MIZAN_HESAPLARI}
    ayirmalar = {}
    for a in stok_ayirma.acik_ayirmalar().select_related("uretim_emri", "stok"):
        e = ayirmalar.setdefault(a.uretim_emri.no, {})
        e[a.stok.kod] = e.get(a.stok.kod, SIFIR) + a.miktar
    return {"kartlar": kartlar, "hareketler": hareketler, "degerleme": degerleme, "mizan": mizan, "ayirmalar": ayirmalar}


def farklar(once, sonra) -> list:
    out = []
    for bolum in ("kartlar", "hareketler", "degerleme", "mizan", "ayirmalar"):
        o, s = once[bolum], sonra[bolum]
        for k in sorted(set(o) | set(s), key=str):
            if o.get(k) != s.get(k):
                out.append(f"{bolum} {k}: {o.get(k)} -> {s.get(k)}")
    return out


def goc(plan, *, uygula=False) -> dict:
    """Planı uygular (transaction); kuru ya da altın kontrol farklıysa geri alır. Rapor sözlüğü döner."""
    semta = Varyant.objects.filter(silindi=False, varsayilan=True).first()
    if semta is None or semta.kod != "SEMTA":
        raise CommandError("Varsayılan varyant SEMTA değil/tanımsız (AYARLAR › Varyantlar).")
    yok = Varyant.objects.filter(silindi=False, kod="YOK").first()
    if yok is None:
        raise CommandError("YOK varyantı tanımsız (AYARLAR › Varyantlar).")
    stoklar = {s.kod: s for s in Stok.objects.filter(kod__in=[k for k, _b, _h in plan], silindi=False)}
    eksik = [k for k, _b, _h in plan if k not in stoklar]
    if eksik:
        raise CommandError("Planda olup kartı bulunamayan kodlar: " + ", ".join(eksik))
    satirlar, atlanan = [], []
    for kod, boyutlar, hedef in plan:
        s = stoklar[kod]
        if s.varyantli:
            atlanan.append(kod)
            continue
        satirlar.append({"stok": s, "boyutlar": boyutlar, "hedef": hedef,
                         "hareket": StokHareket.objects.filter(stok=s, silindi=False).count(),
                         "hareket_tum": StokHareket.objects.filter(stok=s).count(),
                         "ayirma": stok_ayirma.acik_ayirmalar().filter(stok=s).count()})
    pkler = [x["stok"].pk for x in satirlar]
    once = altin_kontrol(pkler)
    uygulandi, fark = False, []
    with transaction.atomic():
        for x in satirlar:
            s = x["stok"]
            Stok.objects.filter(pk=s.pk).update(varyantli=True, varyant_boyutlari=x["boyutlar"])
            s = Stok.objects.get(pk=s.pk)
            standart = varyant_servis.standart_varyant(s)
            if x["hedef"] == "YOK":
                sv = varyant_servis.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": yok})
            else:
                sv = standart
            Stok.objects.filter(pk=s.pk).update(varsayilan_varyant=sv)
            StokHareket.objects.filter(stok=s).update(varyant=sv)            # silinmişler dahil: kartın tüm geçmişi tek varyant
            StokAyirma.objects.filter(stok=s).update(varyant=sv)
            x["varyant"] = sv
        for x in satirlar:
            so.yeniden_hesapla(Stok.objects.get(pk=x["stok"].pk), fis_guncelle=False)
        sonra = altin_kontrol(pkler)
        fark = farklar(once, sonra)
        if fark or not uygula:
            transaction.set_rollback(True)
        else:
            uygulandi = True
    return {"satirlar": satirlar, "atlanan": atlanan, "once": once, "sonra": sonra, "fark": fark, "uygulandi": uygulandi,
            "acik_us": sorted(once["ayirmalar"])}


class Command(BaseCommand):
    help = "Varyant veri gocu (E-2): 53 karti varyantli yapar, hareket/ayirmalari SEMTA/YOK varyantina tasir; varsayilan KURU + altin kontrol."

    def add_arguments(self, parser):
        parser.add_argument("--kod", action="append", default=[], help="yalnız bu kodlar (plan alt kümesi); tekrarlanabilir")
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        plan = varsayilan_plan()
        if opts["kod"]:
            secili = set(opts["kod"])
            plan = [p for p in plan if p[0] in secili]
            bilinmeyen = secili - {p[0] for p in plan}
            if bilinmeyen:
                raise CommandError("Planda olmayan kod: " + ", ".join(sorted(bilinmeyen)))
        r = goc(plan, uygula=opts["uygula"])
        w = self.stdout.write
        w("UYGULANDI" if r["uygulandi"] else ("GERI ALINDI — ALTIN KONTROL FARKI" if r["fark"] else "KURU (geri alindi, kalici degisiklik YOK)"))
        w(f"\nVARYANTLI YAPILACAK KARTLAR ({len(r['satirlar'])}; planda {len(plan)}, zaten varyantli {len(r['atlanan'])})")
        w(f"  {'kod':<13} {'ad':<40} {'boyutlar':<36} {'hedef':<6} {'hareket':>8} {'(tum)':>6} {'acik ayirma':>12}")
        for x in r["satirlar"]:
            w(f"  {x['stok'].kod:<13} {x['stok'].ad[:40]:<40} {'+'.join(x['boyutlar']):<36} {x['hedef']:<6} {x['hareket']:>8} {x['hareket_tum']:>6} {x['ayirma']:>12}")
        if r["atlanan"]:
            w("  zaten varyantli (atlandi): " + ", ".join(r["atlanan"]))
        w(f"\nTOPLAM: hareket {sum(x['hareket'] for x in r['satirlar'])} (tum {sum(x['hareket_tum'] for x in r['satirlar'])}), "
          f"acik ayirma {sum(x['ayirma'] for x in r['satirlar'])}; acik US: {', '.join(r['acik_us']) or '-'}")
        w("\nALTIN KONTROL — kartlar (eldeki / maliyet miktari / deger TL / deger USD / ort TL / ort USD) ONCE -> SONRA")
        for kod, o in r["once"]["kartlar"].items():
            s = r["sonra"]["kartlar"].get(kod)
            isaret = "=" if o == s else "FARK"
            w(f"  {kod:<13} {isaret:<4} {o[0]:>12} {o[1]:>12} {o[2]:>14,.2f} {o[3]:>12,.2f} {o[4] if o[4] is not None else '-':>14} {o[5] if o[5] is not None else '-':>12}")
        w("ALTIN KONTROL — degerleme (stok degeri / mizan / fark) ve mizan")
        for k, o in r["once"]["degerleme"].items():
            s = r["sonra"]["degerleme"][k]
            w(f"  {k}: {o[0]:>16,.2f} / {o[1]:>16,.2f} / {o[2]:>12,.2f}  {'=' if o == s else 'FARK'}")
        for k, o in r["once"]["mizan"].items():
            w(f"  mizan {k}: {o:>16,.2f}  {'=' if o == r['sonra']['mizan'][k] else 'FARK'}")
        w("ALTIN KONTROL — acik US ayirmalari")
        for no, stoklar in r["once"]["ayirmalar"].items():
            esit = stoklar == r["sonra"]["ayirmalar"].get(no)
            w(f"  {no}: {len(stoklar)} stok, toplam {sum(stoklar.values(), SIFIR):f}  {'=' if esit else 'FARK'}")
        w(f"hareket satirlari karsilastirildi: {len(r['once']['hareketler'])}")
        if r["fark"]:
            w(self.style.ERROR(f"\nALTIN KONTROL: {len(r['fark'])} FARK — UYGULANMADI"))
            for f in r["fark"][:50]:
                w("  " + f)
        else:
            w(self.style.SUCCESS("\nALTIN KONTROL: ESIT (once = sonra, birebir)"))
        if not r["uygulandi"] and not r["fark"]:
            w(self.style.WARNING("KURU: kalici uygulamak icin once DB yedegi, sonra --uygula."))
