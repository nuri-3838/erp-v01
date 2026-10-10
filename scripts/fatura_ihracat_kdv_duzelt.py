"""Tek seferlik: İHRACAT/İHRAÇ KAYITLI faturanın stok satırı KDV'sini %0 yapar ve MEVCUT fişi aynı fiş üzerinde yeniden üretir.
Stok hareketleri/maliyet kayıtlarına DOKUNMAZ (öncesi/sonrası özeti basılır). Kullanım: FATURA_ID=217 [DRY=1] manage.py shell < scripts/fatura_ihracat_kdv_duzelt.py"""
import os

from django.db import transaction

from core.models import Fatura, KdvOrani, StokHareket
from core.services import fatura as fs
from core.services.yevmiye import fis_guncelle

fid = int(os.environ["FATURA_ID"])
dry = os.environ.get("DRY") == "1"


class Geri(Exception):
    pass


def fis_yaz(f):
    print("  FİŞ:", f.fis, "| pk", f.fis.pk)
    for s in f.fis.satirlar.select_related("hesap").order_by("id"):
        print("   ", s.hesap.hesap_kodu.ljust(8), "B", str(s.borc).rjust(12), "A", str(s.alacak).rjust(12), s.islem_pb, s.islem_tutari)


def hareket_ozeti(f):
    q = StokHareket.objects.filter(fatura=f) if hasattr(StokHareket, "fatura") else StokHareket.objects.none()
    return sorted(q.values_list("pk", "stok_id", "miktar", "tur", "tarih"))


try:
    with transaction.atomic():
        f = Fatura.objects.select_related("tip", "cari", "fis").get(pk=fid)
        assert f.durum == Fatura.Durum.ONAYLI and fs.ihracat_tipi_mi(f.tip), "ihracat tipi onaylı fatura değil"
        once = hareket_ozeti(f)
        print("ÖNCE (", f.fatura_no, f.tip.ad, f.para_birimi, f.kur, ")")
        fis_yaz(f)
        sifir = KdvOrani.objects.filter(oran=0, silindi=False).order_by("pk").first()
        n = 0
        for s in f.satirlar.filter(silindi=False, stok__isnull=False):
            if s.kdv_id != sifir.pk:
                s.kdv = sifir
                s.save(update_fields=["kdv"])
                n += 1
        print("KDV %0 yapılan stok satırı:", n)
        satirlar = fs._muhasebe_satirlari(f, f.tip, f.cari, f.para_birimi, f.kur)
        fis_guncelle(f.fis, tarih=f.tarih, satirlar=satirlar, aciklama=f.fis.aciklama, kullanici=None)
        f.refresh_from_db()
        print("SONRA")
        fis_yaz(f)
        print("Stok hareketleri değişmedi:", once == hareket_ozeti(f), "(", len(once), "kayıt )")
        if dry:
            raise Geri()
except Geri:
    print("DRY: geri alındı")
