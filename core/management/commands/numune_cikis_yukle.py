"""Tek seferlik veri girişi: 10.10.2026 tarihli müşteri numune çıkışları (14 belge, her müşteri ayrı) — SEMTA DEPO (150), serbest stoktan.
Varsayılan KURU ÇALIŞTIRMA (hiçbir şey yazmaz): stok kodları, serbest stok yeterliliği, tahmini maliyet raporlanır. ``--uygula`` her belgeyi oluşturup ONAYLAR
(tek transaction: biri hatalıysa hiçbiri kaydedilmez). Aynı işaretli belge varsa atlanır (tekrar koşulabilir). Üretim siparişi ayırmalarına dokunmaz."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Cari, Depo, NumuneCikis, Stok, Ulke
from core.services import numune as ns
from core.services import stok_ayirma

TARIH = date(2026, 10, 10)
ISARET = "TOPLU NUMUNE GİRİŞİ 10.10.2026"
KOD = {"2+1": "152-10-0001", "3+1": "152-10-0002", "4+1": "152-10-0003", "5+1": "152-10-0004",
       "3+3": "152-22-0002", "4+4": "152-22-0003", "5+5": "152-22-0004"}

# (alıcı adı, cari kodu, ülke, [modeller])
BELGELER = [
    ("GÖKRAY", None, None, ["3+1", "3+3"]),
    ("BURSA DORUK", None, None, ["3+1", "3+3"]),
    ("AKDEMİR-RODEX", None, None, ["2+1", "3+3"]),
    ("SGS", None, None, ["2+1", "3+3"]),
    ("DOĞRULAR-KONYA", None, None, ["2+1", "3+3"]),
    ("DOĞRULAR-İSTANBUL", None, None, ["3+1", "4+1", "5+1", "3+3", "4+4", "5+5"]),
    ("İSTANBUL - ZUCHEX", None, None, ["3+1", "4+4"]),
    ("GÜRCİSTAN - PROLİNE", None, "Gürcistan", ["2+1", "3+3"]),
    ("ERTEKPA", "120-10-0001", None, ["3+1", "3+3"]),
    ("MAYSA", None, None, ["2+1", "3+1", "3+3"]),
    ("HAYDAR UÇAN", None, None, ["4+1", "4+4"]),
    ("BULGARİSTAN KREZ", None, "Bulgaristan", ["3+1", "5+5"]),
    ("KAYGUN", None, None, ["3+3"]),
    ("YUNUS", None, None, ["2+1", "3+3"]),
]
BEKLENEN = {"2+1": 6, "3+1": 7, "4+1": 2, "5+1": 1, "3+3": 11, "4+4": 3, "5+5": 2}


class Command(BaseCommand):
    help = "10.10.2026 numune çıkışlarını (14 belge) oluşturup onaylar (varsayılan kuru çalıştırma)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        w = self.stdout.write
        depo = Depo.objects.filter(kod="150", silindi=False).first()
        if depo is None:
            raise CommandError("Depo 150 bulunamadı.")
        stoklar = {}
        for model, kod in KOD.items():
            st = Stok.objects.filter(kod=kod, silindi=False).first()
            if st is None:
                raise CommandError(f"Stok kartı yok: {model} = {kod}")
            stoklar[model] = st
        toplam = defaultdict(int)
        for _, _, _, modeller in BELGELER:
            for m in modeller:
                toplam[m] += 1
        if dict(toplam) != BEKLENEN or sum(toplam.values()) != 32:
            raise CommandError(f"Belge verisi beklenen toplamlarla uyuşmuyor: {dict(toplam)}")
        w(f"Depo: {depo.kod} {depo.ad} | tarih {TARIH:%d.%m.%Y} | {len(BELGELER)} belge, {sum(toplam.values())} adet")
        w("\nSTOK YETERLİLİĞİ (serbest = eldeki − ayrılan; depo eldeki)")
        yeterli = True
        from core.services.hareket import eldeki_miktar
        for m, st in stoklar.items():
            serbest, depoda = stok_ayirma.kullanilabilir(st), eldeki_miktar(st, depo)
            ok = toplam[m] <= serbest and toplam[m] <= depoda
            yeterli &= ok
            w(f"  {m:<4} {st.kod}  gerekli {toplam[m]:>2} | serbest {serbest} | depoda {depoda} | ort.maliyet {st.ort_maliyet_try} {'OK' if ok else 'YETERSİZ!'}")
        tahmini = sum((stoklar[m].ort_maliyet_try or Decimal("0")) * n for m, n in toplam.items())
        w(f"  Tahmini toplam maliyet (güncel ortalama): {tahmini:.2f} TL")
        if not yeterli:
            raise CommandError("Stok yetersiz: hiçbir şey yazılmadı.")
        mevcut = {(nc.alici_adi, nc.cari_id) for nc in NumuneCikis.objects.filter(silindi=False, aciklama=ISARET)}
        w("\nBELGELER")
        olusan = []
        try:
            with transaction.atomic():
                for alici, cari_kod, ulke_ad, modeller in BELGELER:
                    cari = None
                    if cari_kod:
                        cari = (Cari.objects.filter(kod=cari_kod, silindi=False).first()
                                or Cari.objects.filter(muhasebe_kodu=cari_kod.replace("-", "."), silindi=False).first())
                        if cari is None:
                            raise CommandError(f"Cari bulunamadı: {cari_kod}")
                    ulke = None
                    if ulke_ad:
                        ulke = Ulke.objects.filter(ad__iexact=ulke_ad).first() or Ulke.objects.filter(ad__icontains=ulke_ad[:5]).first()
                        if ulke is None:
                            raise CommandError(f"Ülke bulunamadı: {ulke_ad}")
                    satirlar = ", ".join(modeller)
                    if (alici if not cari else "", cari.pk if cari else None) in mevcut:
                        w(f"  {alici:<22} ZATEN VAR (atlandı)")
                        continue
                    if not opts["uygula"]:
                        w(f"  {alici:<22} {('cari ' + cari.kod) if cari else 'serbest metin':<18} {(ulke.ad if ulke else '-'):<11} {satirlar}")
                        continue
                    nc = ns.numune_olustur(
                        tarih=TARIH, depo_id=depo.pk, cari_id=cari.pk if cari else None, alici_adi="" if cari else alici,
                        ulke_id=ulke.pk if ulke else None, aciklama=ISARET,
                        satirlar=[{"stok_id": stoklar[m].pk, "miktar": 1} for m in modeller])
                    nc = ns.numune_onayla(nc)
                    maliyet = sum((s.maliyet_try for s in nc.satirlar.filter(silindi=False)), Decimal("0"))
                    olusan.append((nc, maliyet))
                    w(f"  {nc.no}  {alici:<22} {satirlar:<28} maliyet {maliyet:>9.2f} TL  fiş {nc.fis.yil}/{nc.fis.fis_no}")
        except ns.NumuneHatasi as e:
            raise CommandError(f"Hata — hiçbir şey kaydedilmedi: {e}")
        if opts["uygula"]:
            w(f"\nUYGULANDI: {len(olusan)} belge, toplam maliyet {sum((m for _, m in olusan), Decimal('0')):.2f} TL")
        else:
            w(self.style.WARNING("\nKURU ÇALIŞTIRMA: uygulamak için --uygula."))
