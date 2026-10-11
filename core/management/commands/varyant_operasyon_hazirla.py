"""E-5 OPERASYON VERİ HAZIRLIĞI (docs/varyant_kararlari.md karar 6/10): varsayılan KURU, ``--uygula`` ile kalıcı.

1. LAZER MARKALAMA istasyonu (kod 11, sıra 15: kesim 10'dan sonra, manuel testere 12'den önce) — yoksa açılır.
2. Kesim tanımlarının (kesilmiş A tipi ön ayak + çift çıkış SOL ayak çıktısı) çıktı kuralı SABİT YOK (fason kesim markasız çıkar).
3. Her ayak için DÖNÜŞTÜR tanımı (istasyon 11): girdi aynı kart ×1 SABİT YOK → çıktı aynı kart ×1 TAŞI. E-6'ya kadar PASİF (sürücü
   değil; ihtiyaç hesabı/ÜS açılışı görmez).
4. Çift çıkış MONTAJ (151-99-0002, 0007…0011): birleştirilmiş ön ayak ×2 tek satır → ×1 TAŞI (sol ayağı markalı) + ×1 SABİT YOK.
5. Yan etiket (150-50-0002): 11 MONTAJ tanımından çıkar, 11 PAKETLEME tanımına (aynı miktarla) girer — paketsiz 151-99 nötr kalır.
   Birleştirme SOL girdisi, A tipi zinciri, afiş/kılavuz/paketsiz zaten TAŞI (varsayılan): değişiklik yok.

ALTIN KONTROL: 10 adet 152-10-0003 + 10 adet 152-22-0003 standart ihtiyaç hesabı (``ihtiyac_hesapla``) önce/sonra — yalnız yan etiketin
istasyonu (70 → 80) değişmeli; başka fark varsa (ya da ÜS'lere/geçmiş kayıtlara dokunulsaydı) uygulanmaz. Açık/kapalı ÜS'ler ve
operasyon kayıtları DEĞİŞMEZ (yalnız tanımlar).

    python manage.py varyant_operasyon_hazirla            # kuru
    python manage.py varyant_operasyon_hazirla --uygula
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.management.commands.varyant_veri_gocu import AYAK_YOK
from core.models import IsIstasyonu, Operasyon, OperasyonCikti, OperasyonGirdi, Stok, Varyant
from core.services import uretim

LAZER_KOD, LAZER_AD, LAZER_SIRA = "11", "LAZER MARKALAMA", 15
YAN_ETIKET = "150-50-0002"
CC_MONTAJ_CIKTILARI = ["151-99-0002"] + [f"151-99-{i:04d}" for i in range(7, 12)]
GOLDEN_KALEMLER = (("152-10-0003", 10), ("152-22-0003", 10))
TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT


def _ureten(kod):
    s = Stok.objects.filter(kod=kod, silindi=False).first()
    return s, (uretim.ureten_operasyon(s) if s else None)


def golden(kalemler) -> dict:
    """{stok kodu: (istasyon kodu, ihtiyaç, çalıştırma, üretilecek)} — standart plan özeti (brüt)."""
    coz = []
    for kod, miktar in kalemler:
        s = Stok.objects.filter(kod=kod, silindi=False).first()
        if s is None:
            return {}
        coz.append((s, Decimal(miktar)))
    sonuc = uretim.ihtiyac_hesapla(coz)
    out = {}
    for o in sonuc["ozet"]:
        out[o["stok"].kod] = (o["istasyon"].kod if o["istasyon"] else None, o["ihtiyac"], o["calistirma_sayisi"], o["uretilecek_miktar"])
    for p in sonuc["plan"]:
        out["PLAN " + p["stok"].kod] = (p["operasyon"].istasyon.kod, p["calistirma"], p["uretilecek"])
    kullanim = {}                                                      # girdi stok kodu → hangi istasyonlar tüketiyor (yaprakta üreten yok)

    def gez(d):
        for c in d.get("cocuklar", []):
            kullanim.setdefault(c["stok"].kod, set()).add(d["istasyon"].kod if d.get("istasyon") else None)
            gez(c)
    for kok in sonuc["agac"]:
        gez(kok)
    for kod, istler in kullanim.items():
        out["KULLANIM " + kod] = tuple(sorted(x or "" for x in istler))
    return out


def hazirla(*, uygula=False) -> dict:
    yok = Varyant.objects.filter(kod="YOK", silindi=False).first()
    if yok is None:
        raise CommandError("YOK varyantı tanımsız.")
    r = {"istasyon": None, "kesim_kural": [], "donustur": [], "montaj_bol": [], "yan_etiket_cikar": [], "yan_etiket_ekle": [],
         "atlanan": [], "eksik": []}
    once = golden(GOLDEN_KALEMLER)
    with transaction.atomic():
        ist = IsIstasyonu.objects.filter(kod=LAZER_KOD, silindi=False).first()
        if ist is None:
            ist = uretim.istasyon_olustur(kod=LAZER_KOD, ad=LAZER_AD, sira=LAZER_SIRA)
            r["istasyon"] = f"YENİ {ist.kod} {ist.ad} (sıra {ist.sira})"
        else:
            r["istasyon"] = f"var: {ist.kod} {ist.ad} (sıra {ist.sira})"
        for kod in AYAK_YOK:
            s, op = _ureten(kod)
            if s is None or op is None:
                r["eksik"].append(f"{kod}: kart/üreten tanım yok")
                continue
            if not s.varyantli:
                r["eksik"].append(f"{kod}: varyantlı değil (önce varyant_veri_gocu)")
                continue
            if (op.cikti_varyant_kurali, op.cikti_sabit_varyant_id) != (SABIT, yok.pk):
                Operasyon.objects.filter(pk=op.pk).update(cikti_varyant_kurali=SABIT, cikti_sabit_varyant=yok)
                r["kesim_kural"].append(f"{op.istasyon.kod} → {kod}: çıktı kuralı SABİT YOK")
            if Operasyon.objects.filter(cikti=s, tur=Operasyon.Tur.DONUSTUR, silindi=False).exists():
                r["atlanan"].append(f"{kod}: dönüştür tanımı zaten var")
                continue
            d = uretim.operasyon_olustur(istasyon_id=ist.pk, cikti_id=s.pk, cikti_miktar=Decimal("1"), tur=Operasyon.Tur.DONUSTUR,
                                         satirlar=[(s, Decimal("1"), SABIT, yok)], tam_boy=False)
            r["donustur"].append(f"{ist.kod} DÖNÜŞTÜR {kod}: girdi {kod} ×1 [SABİT YOK] → çıktı {kod} ×1 [TAŞI] (op {d.pk})")
        for kod in CC_MONTAJ_CIKTILARI:
            s, op = _ureten(kod)
            if op is None:
                r["eksik"].append(f"{kod}: montaj tanımı yok")
                continue
            for g in list(uretim.operasyon_girdileri(op)):
                if g.girdi.kod.startswith("151-80-") and g.miktar == Decimal("2") and g.varyant_kurali == TASI:
                    g.silindi, g.silindi_at = True, timezone.now()
                    g.save(update_fields=["silindi", "silindi_at", "updated_at"])
                    OperasyonGirdi.objects.create(operasyon=op, girdi=g.girdi, miktar=Decimal("1"), sira=g.sira, varyant_kurali=TASI)
                    OperasyonGirdi.objects.create(operasyon=op, girdi=g.girdi, miktar=Decimal("1"), sira=g.sira + 1, varyant_kurali=SABIT,
                                                  sabit_varyant=yok)
                    r["montaj_bol"].append(f"{op.istasyon.kod} → {kod}: {g.girdi.kod} ×2 → ×1 [TAŞI] + ×1 [SABİT YOK]")
        ye = Stok.objects.filter(kod=YAN_ETIKET, silindi=False).first()
        if ye is None:
            r["eksik"].append(f"{YAN_ETIKET}: kart yok")
        else:
            montaj = list(OperasyonGirdi.objects.filter(girdi=ye, silindi=False, operasyon__silindi=False, operasyon__istasyon__kod="70")
                          .select_related("operasyon__cikti", "operasyon__istasyon"))
            for g in montaj:
                paketleme = uretim.ureten_operasyon(g.operasyon.cikti)                      # 151-99-x'i kullanan paketleme tanımı
                kullanan = [k.operasyon for k in OperasyonGirdi.objects.filter(girdi=g.operasyon.cikti, silindi=False, operasyon__silindi=False)
                            .select_related("operasyon__istasyon", "operasyon__cikti") if k.operasyon.istasyon.kod == "80"]
                if len(kullanan) != 1:
                    r["eksik"].append(f"{g.operasyon.cikti.kod}: paketleme tanımı bulunamadı/tekil değil ({len(kullanan)})")
                    continue
                pk_op = kullanan[0]
                g.silindi, g.silindi_at = True, timezone.now()
                g.save(update_fields=["silindi", "silindi_at", "updated_at"])
                r["yan_etiket_cikar"].append(f"70 MONTAJ → {g.operasyon.cikti.kod}: {YAN_ETIKET} ×{g.miktar.normalize():f} çıkarıldı (girdi {g.pk})")
                if not OperasyonGirdi.objects.filter(operasyon=pk_op, girdi=ye, silindi=False).exists():
                    yeni = OperasyonGirdi.objects.create(operasyon=pk_op, girdi=ye, miktar=g.miktar,
                                                         sira=(max([x.sira for x in uretim.operasyon_girdileri(pk_op)] or [0]) + 10))
                    r["yan_etiket_ekle"].append(f"80 PAKETLEME → {pk_op.cikti.kod}: {YAN_ETIKET} ×{g.miktar.normalize():f} eklendi (girdi {yeni.pk})")
        sonra = golden(GOLDEN_KALEMLER)
        farklar = []
        for k in sorted(set(once) | set(sonra)):
            if once.get(k) != sonra.get(k):
                farklar.append((k, once.get(k), sonra.get(k)))
        beklenen = {YAN_ETIKET, "KULLANIM " + YAN_ETIKET}                  # yalnız yan etiketin tüketildiği istasyon değişir (70 → 80)
        beklenmeyen = [f for f in farklar if f[0] not in beklenen or (f[0] == YAN_ETIKET and f[1] and f[2] and f[1][1:] != f[2][1:])]
        r["golden_fark"], r["golden_beklenmeyen"], r["golden_satir"] = farklar, beklenmeyen, len(once)
        if r["eksik"] or beklenmeyen or not uygula:
            transaction.set_rollback(True)
            r["uygulandi"] = False
        else:
            r["uygulandi"] = True
    return r


class Command(BaseCommand):
    help = "E-5: LAZER MARKALAMA istasyonu, kesim/donustur/montaj varyant kurallari, yan etiketin paketlemeye tasinmasi (varsayilan KURU)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        r = hazirla(uygula=opts["uygula"])
        w = self.stdout.write
        w("UYGULANDI" if r["uygulandi"] else ("GERI ALINDI — EKSIK/FARK" if (r["eksik"] or r["golden_beklenmeyen"]) else "KURU (geri alindi, kalici degisiklik YOK)"))
        w(f"\nISTASYON: {r['istasyon']}")
        for baslik, anahtar in (("KESIM CIKTI KURALI (SABIT YOK)", "kesim_kural"), ("DONUSTUR TANIMLARI (lazer markalama)", "donustur"),
                                ("CIFT CIKIS MONTAJ AYAK BOLME", "montaj_bol"), ("YAN ETIKET — MONTAJDAN CIKAR", "yan_etiket_cikar"),
                                ("YAN ETIKET — PAKETLEMEYE EKLE", "yan_etiket_ekle"), ("ATLANAN", "atlanan"), ("EKSIK/HATA", "eksik")):
            w(f"\n{baslik} ({len(r[anahtar])})")
            for s in r[anahtar]:
                w("  " + s)
        w(f"\nYAN ETIKET DEGISEN TANIM SATIRI: {len(r['yan_etiket_cikar']) + len(r['yan_etiket_ekle'])} (cikar {len(r['yan_etiket_cikar'])} + ekle {len(r['yan_etiket_ekle'])})")
        w(f"\nALTIN KONTROL — standart plan 10×152-10-0003 + 10×152-22-0003 ({r['golden_satir']} satir): fark {len(r['golden_fark'])}")
        for k, o, s in r["golden_fark"]:
            w(f"  {k}: {o} -> {s}")
        if r["golden_beklenmeyen"]:
            w(self.style.ERROR(f"BEKLENMEYEN FARK ({len(r['golden_beklenmeyen'])}) — UYGULANMADI"))
        else:
            w(self.style.SUCCESS("ALTIN KONTROL: yalniz yan etiketin istasyonu degisti (70 -> 80); miktar/calistirma birebir"))
        if r["eksik"]:
            w(self.style.ERROR("EKSIK var — UYGULANMADI"))
        elif not r["uygulandi"]:
            w(self.style.WARNING("KURU: kalici uygulamak icin --uygula."))
