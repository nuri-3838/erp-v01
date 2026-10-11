"""FİYATSIZ GİRİŞ DÜZELTME: fatura tutarı yazılmamış (giris_tutar_try boş) GİRİŞ hareketlerine elle verilen birim fiyatı yazar ve maliyet zincirini
yeniden hesaplar (kart ortalaması → bu girdiyi tüketen üretim kayıtlarının çıktı maliyeti + 15x→15x aktarım fişi → onları kullanan sonraki üretim →
satış maliyet fişi [620/621/623] / sarf / numune [760.06] fişleri — ``stok_ortalama.yeniden_hesapla`` zinciri).

İsteğe bağlı ``--fis KOD=KARŞI_HESAP[:AÇIKLAMA]``: o stoğun fiyatlanan MANUEL/açılış girişleri için tek fiş keser — stok hesabı (kategori haritası)
BORÇ / karşı hesap (ör. 770.08 ofis gideri) ALACAK, tarih = giriş tarihi, kaynak STOK_DUZELTME (elle silinemez/düzenlenemez; giriş silinirse iptal
olur). İRSALİYE girişine fiş KESİLMEZ: faturası gelince ``irsaliye_girislerini_fiyatla`` fiyatı yerinde fatura tutarıyla ezer ve fatura fişi oluşur.

Varsayılan DRY-RUN: her şey bir transaction içinde gerçekten yapılır, fark raporu alınır ve GERİ ALINIR (kalıcı hiçbir şey yazılmaz). ``--uygula`` kalıcı
uygular (kullanıcı onayıyla). Rapor: fiyatlanan girişler, yeni fişler, tutarı değişen çıkış/giriş hareketleri, ortalama maliyeti değişen kartlar,
150/151/152/620/621/623/760/770 mizan farkları, 150-153 stok değeri farkları, değişen fiş sayısı ve uygulama sonrası stok değeri = mizan kontrolü
(fişsiz/fatura bekleyen girişlerin açıklamasıyla).

    python manage.py stok_maliyet_duzelt            # varsayılan: 150-50-0004=1,00 (+ 770.08 fişi) ve 151-40-0007=1,00 (fişsiz) — kuru çalıştırma
    python manage.py stok_maliyet_duzelt --fiyat KOD=1,20 [--fis KOD=770.08:Açıklama] [--uygula]
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from core.models import HesapPlani, Stok, StokHareket, YevmiyeFisi, YevmiyeSatir
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import stok_ortalama as so
from core.services.hesap_plani import yaprak_mi
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur, kur_usd_bul

SIFIR = Decimal("0.00")
VARSAYILAN_FIYATLAR = (("150-50-0004", "1,00"), ("151-40-0007", "1,00"))
VARSAYILAN_FISLER = (("150-50-0004", "770.08", "Kullanma kılavuzu iç baskı maliyeti – giderden stoğa aktarım"),)
MIZAN_HESAPLARI = ("150", "151", "152", "620", "621", "623", "760", "770")
FIS_KAYNAKLARI = (YevmiyeFisi.Kaynak.URETIM, YevmiyeFisi.Kaynak.STOK_SATIS, YevmiyeFisi.Kaynak.STOK_SARF, YevmiyeFisi.Kaynak.NUMUNE,
                  YevmiyeFisi.Kaynak.STOK_DUZELTME)


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


def _fis_coz(deger):
    """'KOD=770.08:Açıklama' → (kod, hesap kodu, açıklama)."""
    if "=" not in deger:
        raise CommandError(f"--fis biçimi KOD=HESAP[:AÇIKLAMA] olmalı: {deger}")
    kod, kalan = deger.split("=", 1)
    hesap, _, aciklama = kalan.partition(":")
    kod, hesap, aciklama = kod.strip(), hesap.strip(), aciklama.strip()
    if not hesap:
        raise CommandError(f"{kod}: --fis için karşı hesap kodu gerekli.")
    return kod, hesap, aciklama or f"{kod} maliyet düzeltme – giderden stoğa aktarım"


def _fis_toplamlari():
    """{fiş pk: Σ borç} — zincirin yazdığı/güncellediği fişler (üretim aktarımı, satış maliyeti, sarf, numune, düzeltme); silinmemiş."""
    return {r["fis_id"]: r["t"] for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, fis__kaynak__in=FIS_KAYNAKLARI)
            .values("fis_id").annotate(t=Sum("borc"))}


def _stok_degerleri():
    d = so.degerleme_raporu()
    return {k["kod"]: k["toplam_deger"] for k in d["karsilastirma"]}


def duzelt(ciftler, *, fisler=None, uygula=False) -> dict:
    """``ciftler``: [(stok kodu, birim fiyat TL)]; ``fisler``: {stok kodu: (karşı hesap kodu, açıklama)}.
    Transaction içinde uygular, raporu döner; ``uygula=False`` ise geri alır."""
    with transaction.atomic():
        rapor = _isle(ciftler, fisler or {})
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    return rapor


def _karsi_hesap(kod):
    hesap = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).first()
    if hesap is None or not yaprak_mi(hesap):
        raise CommandError(f"Karşı hesap bulunamadı ya da yaprak değil: {kod}")
    return hesap


def _giris_fisi_kes(stok, h, karsi, aciklama):
    """Fiyatlanan MANUEL girişe tek fiş: stok hesabı B / karşı hesap A, tarih = giriş tarihi; hareket.fis'e bağlanır."""
    from core.services import stok_fis
    try:
        stok_hesabi = stok_fis.stok_hesabi_kodu(stok)
        fis = fis_olustur(tarih=h.tarih, satirlar=[SatirGirdi(stok_hesabi, "B", h.giris_tutar_try), SatirGirdi(karsi.hesap_kodu, "A", h.giris_tutar_try)],
                          aciklama=aciklama, kaynak=YevmiyeFisi.Kaynak.STOK_DUZELTME)
    except (so.MaliyetHatasi, YevmiyeHatasi) as e:
        raise CommandError(f"{stok.kod} hareket {h.pk}: fiş kesilemedi — {e}")
    h.fis = fis
    h.save(update_fields=["fis", "updated_at"])
    return {"fis": fis, "hareket": h, "stok": stok, "stok_hesabi": stok_hesabi, "karsi": karsi.hesap_kodu, "tutar": h.giris_tutar_try}


def _isle(ciftler, fisler):
    once_h = {h.pk: (h.tur, h.tutar_try, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False)}
    once_ort = {s.pk: s.ort_maliyet_try for s in Stok.objects.filter(silindi=False)}
    once_mizan = {k: so._mizan_bakiyesi(k) for k in MIZAN_HESAPLARI}
    once_deger = _stok_degerleri()
    once_fis = _fis_toplamlari()
    karsi_hesaplar = {kod: (_karsi_hesap(hesap), aciklama) for kod, (hesap, aciklama) in fisler.items()}

    fiyatlanan, yeni_fisler, fissiz, hata = [], [], [], None
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
            if h.kaynak == StokHareket.Kaynak.IRSALIYE or h.fis_id:
                fissiz.append({"stok": stok, "hareket": h, "tutar": tutar,
                               "neden": "irsaliye girişi: fatura gelince fiyat ve fiş faturadan" if h.kaynak == StokHareket.Kaynak.IRSALIYE else "fişi zaten var"})
            elif kod in karsi_hesaplar:
                karsi, aciklama = karsi_hesaplar[kod]
                yeni_fisler.append(_giris_fisi_kes(stok, h, karsi, aciklama))
            else:
                fissiz.append({"stok": stok, "hareket": h, "tutar": tutar, "neden": "--fis verilmedi"})
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
    # Uygulama sonrası kontrol: stok değeri (kesin) − mizan; fişsiz fiyatlanan girişler bu farkı açıklar (eldeki + tüketilen değeri).
    kontrol = [(k["kod"], k["stok_degeri"], k["mizan"], k["fark"]) for k in so.degerleme_raporu()["karsilastirma"] if k["kod"] in ("150", "151", "152")]
    aciklama_fissiz = []
    for stok in {x["stok"].pk: x["stok"] for x in fissiz}.values():
        stok.refresh_from_db()
        toplam = sum((x["tutar"] for x in fissiz if x["stok"].pk == stok.pk), SIFIR)
        eldeki = stok.maliyet_deger_try or SIFIR
        aciklama_fissiz.append({"stok": stok, "toplam": toplam, "eldeki_miktar": stok.maliyet_miktar, "eldeki": eldeki, "tuketilen": toplam - eldeki})
    return {"fiyatlanan": fiyatlanan, "yeni_fisler": yeni_fisler, "fissiz": fissiz, "degisen": degisen, "ort": ort, "hata": hata,
            "mizan": [(k, once_mizan[k], sonra_mizan[k], sonra_mizan[k] - once_mizan[k]) for k in MIZAN_HESAPLARI],
            "deger": [(k, once_deger.get(k, SIFIR), sonra_deger.get(k, SIFIR), sonra_deger.get(k, SIFIR) - once_deger.get(k, SIFIR))
                      for k in ("150", "151", "152", "153")],
            "fis_degisen": fis_degisen, "fisler": {pk: (once_fis.get(pk), sonra_fis.get(pk)) for pk in fis_degisen},
            "kontrol": kontrol, "aciklama_fissiz": aciklama_fissiz}


class Command(BaseCommand):
    help = "Fiyatsiz giris hareketlerine elle birim fiyat (+ istege bagli giderden stoga aktarim fisi) verip maliyet zincirini yeniden hesaplar (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--fiyat", action="append", default=[], help="KOD=TUTAR (TL, TR biçimi); tekrarlanabilir")
        parser.add_argument("--fis", action="append", default=[], help="KOD=KARSI_HESAP[:ACIKLAMA] — o stoğun fiyatlanan manuel girişlerine fiş; tekrarlanabilir")
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        if opts["fiyat"]:
            ciftler = [_fiyat_coz(x) for x in opts["fiyat"]]
            fisler = dict((k, (h, a)) for k, h, a in (_fis_coz(x) for x in opts["fis"]))
        else:
            ciftler = [_fiyat_coz(f"{k}={f}") for k, f in VARSAYILAN_FIYATLAR]
            fisler = {k: (h, a) for k, h, a in VARSAYILAN_FISLER} if not opts["fis"] else dict((k, (h, a)) for k, h, a in (_fis_coz(x) for x in opts["fis"]))
        bilinmeyen = set(fisler) - {k for k, _ in ciftler}
        if bilinmeyen:
            raise CommandError(f"--fis verilen stok --fiyat listesinde yok: {', '.join(sorted(bilinmeyen))}")
        rapor = duzelt(ciftler, fisler=fisler, uygula=opts["uygula"])
        self._yaz(rapor, ciftler, fisler)

    def _yaz(self, r, ciftler, fisler):
        w = self.stdout.write
        w(f"{'UYGULANDI' if r['uygulandi'] else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'} — "
          + ", ".join(f"{k}={f.normalize():f} TL" for k, f in ciftler)
          + (" | fis: " + ", ".join(f"{k} -> {h}" for k, (h, _a) in fisler.items()) if fisler else ""))
        if r["hata"]:
            w(self.style.ERROR(f"HATA (zincir durdu): {r['hata']}"))
        w(f"\nFIYATLANAN GIRISLER ({len(r['fiyatlanan'])})")
        for x in r["fiyatlanan"]:
            h = x["hareket"]
            w(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {x['stok'].kod} {h.kaynak} {h.miktar.normalize():f} x {x['fiyat'].normalize():f} = {x['tutar']:,.2f} TL")
        w(f"\nYENI FISLER ({len(r['yeni_fisler'])}; giderden stoga aktarim, kaynak STOK_DUZELTME)")
        for x in r["yeni_fisler"]:
            f = x["fis"]
            w(f"  fis {f.yil}/{f.fis_no} {f.tarih:%d.%m.%Y}  {x['stok_hesabi']} B {x['tutar']:,.2f} / {x['karsi']} A {x['tutar']:,.2f}  — {f.aciklama}"
              f"  [hareket {x['hareket'].pk} {x['stok'].kod}]")
        w(f"\nFISSIZ FIYATLANAN GIRISLER ({len(r['fissiz'])})")
        for x in r["fissiz"]:
            w(f"  hareket {x['hareket'].pk} {x['stok'].kod} {x['tutar']:,.2f} TL — {x['neden']}")
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
        w("\nMIZAN KONTROLU — uygulama sonrasi (grup: stok degeri / mizan / fark)")
        for k, sd, m, f in r["kontrol"]:
            w(f"  {k}: {sd:>16,.2f} / {m:>16,.2f} / {f:>14,.2f}{'  <-- fark' if f else '  = esit'}")
        if r["aciklama_fissiz"]:
            w("  Fissiz (fatura bekleyen) fiyatlanan girisler — farki aciklar: toplam = eldeki degeri + tuketilen degeri")
            for x in r["aciklama_fissiz"]:
                w(f"    {x['stok'].kod} {x['stok'].ad[:40]}: {x['toplam']:,.2f} = eldeki {x['eldeki']:,.2f} ({x['eldeki_miktar'].normalize():f} adet)"
                  f" + tuketilen {x['tuketilen']:,.2f}")
        if not r["uygulandi"]:
            w(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))
