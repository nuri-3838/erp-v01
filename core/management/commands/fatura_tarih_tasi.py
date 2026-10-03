"""Tek seferlik: seçili ONAYLI alış-gider faturalarını başka bir tarihe (varsayılan
2026-01-01) taşır. Fatura + vade tarihi yeni tarih olur, açıklamanın başına eski tarih
"GERÇEK TARİH: gg.aa.yyyy. " olarak eklenir (300 karakteri geçmez), bağlı yevmiye fişi
YENİ mali yılda (yeni numarayla) yeniden oluşturulur ve ESKİ FİŞ KALICI SİLİNİR
(fatura_sil ile aynı bilinçli istisna). Kalem hesabı/proje/KDV/şahsi/stopaj ayarları ve
fatura kuru (TL tutarlar) AYNEN korunur; fişin USD kuru yeni tarihin Kur kaydından gelir.

Varsayılan DRY-RUN: işlemin TAMAMI bir transaction içinde gerçekten yapılır, doğrulama
çıktısı alınır ve sonunda GERİ ALINIR (hiçbir şey kalıcı yazılmaz) — böylece yeni fiş
numaraları ve işlem sonrası değerler gerçek hesaptır. ``--uygula`` aynı işlemi kalıcı yapar.
Doğrulama tutmazsa (eski yılda fiş kaldı, proje toplamı değişti) her iki modda da hata verir.

    python manage.py fatura_tarih_tasi --idler 145,147,... [--tarih 2026-01-01] [--uygula]
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Sum

from core.models import (Fatura, Kur, StokHareket, YatirimProjesi, YevmiyeFisi, YevmiyeSatir)
from core.services.fatura import _muhasebe_satirlari
from core.services.yatirim_projesi import proje_toplami
from core.services.yevmiye import fis_olustur

SIFIR = Decimal("0.00")
ONEK = "GERÇEK TARİH: {:%d.%m.%Y}. "


def _tam_aciklama(f):
    return ONEK.format(f.tarih) + (f.aciklama or "")


def _yeni_aciklama(f):
    return _tam_aciklama(f)[:300]


def _diger_referanslar(fis, haric_fatura_id):
    """Eski fişe Fatura.fis ve YevmiyeSatir dışında işaret eden kayıtlar (silmeyi engeller)."""
    sayim = []
    for rel in YevmiyeFisi._meta.related_objects:
        if rel.related_model is YevmiyeSatir:
            continue
        qs = rel.related_model._default_manager.filter(**{rel.field.name: fis})
        if rel.related_model is Fatura:
            qs = qs.exclude(pk=haric_fatura_id)
        n = qs.count()
        if n:
            sayim.append(f"{rel.related_model.__name__}={n}")
    return sayim


def _bakiye(yil, *kodlar):
    """Verilen hesap kodları (ve alt hesapları) için o mali yılın net borç bakiyesi."""
    q = Q()
    for k in kodlar:
        q |= Q(hesap__hesap_kodu=k) | Q(hesap__hesap_kodu__startswith=k + ".")
    agg = YevmiyeSatir.objects.filter(
        q, silindi=False, fis__silindi=False, fis__yil=yil).aggregate(b=Sum("borc"), a=Sum("alacak"))
    return (agg["b"] or SIFIR) - (agg["a"] or SIFIR)


def _proje_toplamlari():
    return {p.kod: proje_toplami(p) for p in
            YatirimProjesi.objects.filter(silindi=False).order_by("kod")}


def _durum_ozeti(yeni_yil, eski_yillar):
    return {
        "projeler": _proje_toplamlari(),
        "258": {y: _bakiye(y, "258") for y in sorted({yeni_yil, *eski_yillar})},
        "360.20": {y: _bakiye(y, "360.20") for y in sorted({yeni_yil, *eski_yillar})},
        "fis_sayisi": {y: YevmiyeFisi.objects.filter(yil=y).count()
                       for y in sorted({yeni_yil, *eski_yillar})},
    }


class Command(BaseCommand):
    help = "Secili alis-gider faturalarini baska tarihe tasir (varsayilan dry-run; --uygula)."

    def add_arguments(self, parser):
        parser.add_argument("--idler", required=True, help="Virgullu fatura id listesi.")
        parser.add_argument("--tarih", default="2026-01-01", help="Yeni tarih (YYYY-AA-GG).")
        parser.add_argument("--uygula", action="store_true", help="Kalici uygula.")

    def handle(self, *args, **opts):
        try:
            yeni = datetime.date.fromisoformat(opts["tarih"])
            idler = [int(x) for x in opts["idler"].split(",") if x.strip()]
        except ValueError as e:
            raise CommandError(str(e))
        faturalar = list(Fatura.objects.filter(pk__in=idler, silindi=False)
                         .select_related("tip", "cari", "fis").order_by("fis__yil", "fis__fis_no"))
        if len(faturalar) != len(set(idler)):
            raise CommandError(
                f"Bulunamayan/silinmis fatura: {sorted(set(idler) - {f.pk for f in faturalar})}")
        kur = Kur.objects.filter(tarih=yeni, silindi=False, usd_alis__isnull=False).first()
        if kur is None:
            raise CommandError(f"{yeni:%d.%m.%Y} icin USD kuru yok.")

        sorunlar = []
        for f in faturalar:
            if f.durum != Fatura.Durum.ONAYLI or f.fis_id is None or f.fis.silindi:
                sorunlar.append(f"fatura {f.pk}: onayli/fisli degil")
                continue
            if f.fis.kaynak != YevmiyeFisi.Kaynak.FATURA:
                sorunlar.append(f"fatura {f.pk}: fis kaynagi fatura degil")
            if f.fis.yil == yeni.year:
                sorunlar.append(f"fatura {f.pk}: fis zaten {yeni.year} yilinda")
            if StokHareket.objects.filter(fatura_satir__fatura=f, silindi=False).exists():
                sorunlar.append(f"fatura {f.pk}: stok hareketi var")
            diger = _diger_referanslar(f.fis, f.pk)
            if diger:
                sorunlar.append(f"fatura {f.pk}: fise baska kayit bagli ({', '.join(diger)})")
        if sorunlar:
            raise CommandError("Uygulanamaz:\n  " + "\n  ".join(sorunlar))

        eski_yillar = sorted({f.fis.yil for f in faturalar})
        onceki = _durum_ozeti(yeni.year, eski_yillar)
        # Eski bilgileri işlemden ÖNCE al (işlem sırasında nesneler güncellenecek)
        eski = {f.pk: (f.tarih, f.fis.yil, f.fis.fis_no, len(_tam_aciklama(f)) > 300)
                for f in faturalar}
        uygula = opts["uygula"]
        satirlar_rapor = []
        sp = transaction.atomic()
        sp.__enter__()
        try:
            for f in faturalar:
                eski_fis = f.fis
                satirlar = _muhasebe_satirlari(f, f.tip, f.cari, f.para_birimi, f.kur)
                yeni_fis = fis_olustur(
                    tarih=yeni, satirlar=satirlar, aciklama=eski_fis.aciklama,
                    kaynak=YevmiyeFisi.Kaynak.FATURA, kullanici=None)
                f.aciklama = _yeni_aciklama(f)
                f.tarih = f.vade_tarihi = yeni
                f.fis = yeni_fis
                f.save(update_fields=["aciklama", "tarih", "vade_tarihi", "fis", "updated_at"])
                eski_fis.delete()                    # satirlari CASCADE; fatura artik yenisine bagli
                satirlar_rapor.append((f, yeni_fis))
            sonraki = _durum_ozeti(yeni.year, eski_yillar)
            kalan = {y: sonraki["fis_sayisi"][y] for y in eski_yillar}
            if any(kalan.values()):
                raise CommandError(f"Eski yilda fis kaldi: {kalan}; geri alindi.")
            if sonraki["projeler"] != onceki["projeler"]:
                raise CommandError("Proje toplamlari degisti; geri alindi.")
            devam_toplam = sum((v for p, v in zip(
                YatirimProjesi.objects.filter(silindi=False).order_by("kod"),
                sonraki["projeler"].values()) if p.durum == YatirimProjesi.Durum.DEVAM), SIFIR)
        except BaseException as e:
            sp.__exit__(type(e), e, e.__traceback__)
            raise
        # dry-run: tüm işlemi geri al; uygula: kalıcı
        if uygula:
            sp.__exit__(None, None, None)
        else:
            transaction.set_rollback(True)
            sp.__exit__(None, None, None)

        self.stdout.write(
            f"{'UYGULANDI' if uygula else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'} — "
            f"yeni tarih {yeni:%d.%m.%Y}, fis USD kuru {kur.usd_alis}\n")
        self.stdout.write(f"{'Fatura':<7} {'Eski tarih':<11} {'Eski fis':<9} {'Yeni fis':<10} "
                          f"{'Kisaltildi':<11} Yeni aciklama (ilk 60)")
        for f, yf in satirlar_rapor:
            et, ey, en, kisa = eski[f.pk]
            self.stdout.write(f"{f.pk:<7} {et:%d.%m.%Y}  {ey}/{en:<5} {yf.yil}/{yf.fis_no:<6} "
                              f"{'EVET' if kisa else 'hayir':<11} {f.aciklama[:60]}")
        self.stdout.write("\nPROJE TOPLAMLARI (once -> sonra)")
        for k, v in onceki["projeler"].items():
            self.stdout.write(f"  {k}: {v} -> {sonraki['projeler'][k]}"
                              f"{'' if v == sonraki['projeler'][k] else '  DEGISTI!'}")
        self.stdout.write("\nMIZAN BAKIYELERI (net borc; once -> sonra)")
        for hesap in ("258", "360.20"):
            for y in sorted(onceki[hesap]):
                self.stdout.write(f"  {y} mizan {hesap}: {onceki[hesap][y]} -> {sonraki[hesap][y]}")
        self.stdout.write("\nFIS SAYISI (once -> sonra)")
        for y in sorted(onceki["fis_sayisi"]):
            self.stdout.write(f"  {y}: {onceki['fis_sayisi'][y]} -> {sonraki['fis_sayisi'][y]}")
        b258 = sonraki["258"][yeni.year]
        self.stdout.write(f"\n{yeni.year} mizan 258 = {b258}; DEVAM proje toplamlari = "
                          f"{devam_toplam}; fark = {b258 - devam_toplam}")
        if not uygula:
            self.stdout.write(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))
