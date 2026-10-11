"""FİYATSIZ GİRİŞ DÜZELTME: fatura tutarı yazılmamış (giris_tutar_try boş) GİRİŞ hareketlerine elle verilen birim fiyatı yazar ve maliyet zincirini
yeniden hesaplar (kart ortalaması → bu girdiyi tüketen üretim kayıtlarının çıktı maliyeti + 15x→15x aktarım fişi → onları kullanan sonraki üretim →
satış maliyet fişi [620/621/623] / sarf / numune fişleri — ``stok_ortalama.yeniden_hesapla`` zinciri).

Varsayılan DRY-RUN: her şey bir transaction içinde gerçekten yapılır, fark raporu alınır ve GERİ ALINIR (kalıcı hiçbir şey yazılmaz). ``--uygula`` kalıcı
uygular (kullanıcı onayıyla). Rapor: fiyatlanan girişler, tutarı değişen çıkış/giriş hareketleri, ortalama maliyeti değişen kartlar (mamul/yarı mamul ayrı),
150/151/152/620/621/623 mizan farkları ve 150-153 stok değeri farkları, değişen fiş sayısı.

    python manage.py stok_maliyet_duzelt                       # varsayılan: 150-50-0004=1,20 ve 151-40-0007=0,50 TL (kuru çalıştırma)
    python manage.py stok_maliyet_duzelt --fiyat KOD=1,20 --fiyat KOD2=0,50 [--uygula]
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from core.models import Stok, StokHareket, YevmiyeFisi, YevmiyeSatir
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import stok_ortalama as so
from core.services.yevmiye import kur_usd_bul

SIFIR = Decimal("0.00")
VARSAYILAN_FIYATLAR = (("150-50-0004", "1,20"), ("151-40-0007", "0,50"))
MIZAN_HESAPLARI = ("150", "151", "152", "620", "621", "623")
FIS_KAYNAKLARI = (YevmiyeFisi.Kaynak.URETIM, YevmiyeFisi.Kaynak.STOK_SATIS, YevmiyeFisi.Kaynak.STOK_SARF, YevmiyeFisi.Kaynak.NUMUNE)


def _fiyat_coz(deger):
    """'KOD=1,20' → (kod, Decimal)."""
    if "=" not in deger:
        raise CommandError(f"--fiyat biçimi KOD=TUTAR olmalı: {deger}")
    kod, ham = deger.split("=", 1)
    try:
        fiyat = parse_tr(ham.strip())
    except SayiHatasi:
        raise CommandError(f"{kod}: fiyat geçerli bir sayı değil ({ham}).")
    if fiyat <= 0:
        raise CommandError(f"{kod}: fiyat sıfırdan büyük olmalı.")
    return kod.strip(), fiyat


def _fis_toplamlari():
    """{fiş pk: Σ borç} — zincirin yazdığı/güncellediği fişler (üretim aktarımı, satış maliyeti, sarf, numune); silinmemiş."""
    return {r["fis_id"]: r["t"] for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, fis__kaynak__in=FIS_KAYNAKLARI)
            .values("fis_id").annotate(t=Sum("borc"))}


def _stok_degerleri():
    d = so.degerleme_raporu()
    return {k["kod"]: k["toplam_deger"] for k in d["karsilastirma"]}


def duzelt(ciftler, *, uygula=False) -> dict:
    """``ciftler``: [(stok kodu, birim fiyat TL)]. Transaction içinde uygular, raporu döner; ``uygula=False`` ise geri alır."""
    with transaction.atomic():
        rapor = _isle(ciftler)
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    return rapor


def _isle(ciftler):
    once_h = {h.pk: (h.tur, h.tutar_try, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False)}
    once_ort = {s.pk: s.ort_maliyet_try for s in Stok.objects.filter(silindi=False)}
    once_mizan = {k: so._mizan_bakiyesi(k) for k in MIZAN_HESAPLARI}
    once_deger = _stok_degerleri()
    once_fis = _fis_toplamlari()

    fiyatlanan, hata = [], None
    for kod, fiyat in ciftler:
        stok = Stok.objects.filter(kod=kod, silindi=False).first()
        if stok is None:
            raise CommandError(f"Stok bulunamadı: {kod}")
        girisler = list(StokHareket.objects.filter(stok=stok, silindi=False, tur=StokHareket.Tur.GIRIS, giris_tutar_try__isnull=True)
                        .exclude(kaynak=StokHareket.Kaynak.TRANSFER).order_by("tarih", "pk"))
        for h in girisler:
            tutar = yuvarla(h.miktar * fiyat, 2)
            kur = kur_usd_bul(h.tarih)
            h.giris_tutar_try = tutar
            h.giris_tutar_usd = yuvarla(tutar / kur, 2) if kur else None
            h.save(update_fields=["giris_tutar_try", "giris_tutar_usd", "updated_at"])
            fiyatlanan.append({"stok": stok, "hareket": h, "fiyat": fiyat, "tutar": tutar})
        try:
            so.yeniden_hesapla(stok)                               # zincir: üretim çıktıları → sonraki üretim → satış/sarf/numune fişleri
        except so.MaliyetHatasi as e:
            hata = f"{kod}: {e}"
            break

    sonra_h = {h.pk: (h.tur, h.tutar_try, h.maliyet_durumu, h) for h in
               StokHareket.objects.filter(silindi=False).select_related("stok", "depo")}
    degisen = []
    for pk, (tur, tutar, durum, h) in sonra_h.items():
        o = once_h.get(pk)
        if o is None or (o[1], o[2]) == (tutar, durum):
            continue
        degisen.append({"hareket": h, "eski": o[1], "yeni": tutar, "eski_durum": o[2], "yeni_durum": durum})
    degisen.sort(key=lambda x: (x["hareket"].stok.kod, x["hareket"].tarih, x["hareket"].pk))
    ort = []
    for s in Stok.objects.filter(silindi=False).order_by("kod"):
        if once_ort.get(s.pk) != s.ort_maliyet_try:
            ort.append({"stok": s, "eski": once_ort.get(s.pk), "yeni": s.ort_maliyet_try, "miktar": s.maliyet_miktar})
    sonra_mizan = {k: so._mizan_bakiyesi(k) for k in MIZAN_HESAPLARI}
    sonra_deger = _stok_degerleri()
    sonra_fis = _fis_toplamlari()
    fis_degisen = sorted(pk for pk in set(once_fis) | set(sonra_fis) if once_fis.get(pk) != sonra_fis.get(pk))
    return {"fiyatlanan": fiyatlanan, "degisen": degisen, "ort": ort, "hata": hata,
            "mizan": [(k, once_mizan[k], sonra_mizan[k], sonra_mizan[k] - once_mizan[k]) for k in MIZAN_HESAPLARI],
            "deger": [(k, once_deger.get(k, SIFIR), sonra_deger.get(k, SIFIR), sonra_deger.get(k, SIFIR) - once_deger.get(k, SIFIR))
                      for k in ("150", "151", "152", "153")],
            "fis_degisen": fis_degisen, "fisler": {pk: (once_fis.get(pk), sonra_fis.get(pk)) for pk in fis_degisen}}


class Command(BaseCommand):
    help = "Fiyatsiz giris hareketlerine elle birim fiyat verip maliyet zincirini yeniden hesaplar (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--fiyat", action="append", default=[], help="KOD=TUTAR (TL, TR biçimi); tekrarlanabilir")
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        ciftler = [_fiyat_coz(x) for x in opts["fiyat"]] or [_fiyat_coz(f"{k}={f}") for k, f in VARSAYILAN_FIYATLAR]
        rapor = duzelt(ciftler, uygula=opts["uygula"])
        self._yaz(rapor, ciftler)

    def _yaz(self, r, ciftler):
        w = self.stdout.write
        w(f"{'UYGULANDI' if r['uygulandi'] else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'} — "
          + ", ".join(f"{k}={f.normalize():f} TL" for k, f in ciftler))
        if r["hata"]:
            w(self.style.ERROR(f"HATA (zincir durdu): {r['hata']}"))
        w(f"\nFIYATLANAN GIRISLER ({len(r['fiyatlanan'])})")
        for x in r["fiyatlanan"]:
            h = x["hareket"]
            w(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {x['stok'].kod} {h.kaynak} {h.miktar.normalize():f} x {x['fiyat'].normalize():f} = {x['tutar']:,.2f} TL")
        w(f"\nTUTARI/DURUMU DEGISEN HAREKETLER ({len(r['degisen'])}; cikis = tuketim, giris = uretim ciktisi)")
        for x in r["degisen"]:
            h = x["hareket"]
            eski = f"{x['eski']:,.2f}" if x["eski"] is not None else "-"
            yeni = f"{x['yeni']:,.2f}" if x["yeni"] is not None else "-"
            w(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {h.stok.kod:<13} {h.tur:<5} {h.kaynak:<8} {h.miktar.normalize():>10f}  {eski:>14} -> {yeni:>14} TL"
              f"  [{x['eski_durum']}->{x['yeni_durum']}]")
        mamul = [x for x in r["ort"] if x["stok"].kod.startswith("152-")]
        yari = [x for x in r["ort"] if x["stok"].kod.startswith("151-")]
        diger = [x for x in r["ort"] if x not in mamul and x not in yari]
        for baslik, liste in (("MAMUL (152) BIRIM MALIYETI DEGISENLER", mamul), ("YARI MAMUL (151) BIRIM MALIYETI DEGISENLER", yari),
                              ("DIGER KARTLAR", diger)):
            w(f"\n{baslik} ({len(liste)})")
            for x in liste:
                eski = f"{x['eski']:,.6f}" if x["eski"] is not None else "-"
                yeni = f"{x['yeni']:,.6f}" if x["yeni"] is not None else "-"
                w(f"  {x['stok'].kod:<13} {x['stok'].ad[:40]:<40} eldeki {x['miktar'].normalize():>10f}  ort {eski:>16} -> {yeni:>16} TL")
        w("\nMIZAN FARKLARI (hesap: once / sonra / fark)")
        for k, o, s, f in r["mizan"]:
            w(f"  {k}: {o:>16,.2f} / {s:>16,.2f} / {f:>14,.2f}{'  <--' if f else ''}")
        w("\nSTOK DEGERI FARKLARI (grup: once / sonra / fark)")
        for k, o, s, f in r["deger"]:
            w(f"  {k}: {o:>16,.2f} / {s:>16,.2f} / {f:>14,.2f}{'  <--' if f else ''}")
        w(f"\nDEGISEN/YENI FIS SAYISI: {len(r['fis_degisen'])}")
        for pk in r["fis_degisen"]:
            o, s = r["fisler"][pk]
            w(f"  fis {pk}: {(f'{o:,.2f}' if o is not None else '-')} -> {(f'{s:,.2f}' if s is not None else '-')}")
        if not r["uygulandi"]:
            w(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))
