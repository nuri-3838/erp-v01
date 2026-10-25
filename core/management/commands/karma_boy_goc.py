"""Karma kesim veri göçü: basamak kesim operasyonlarının parça boyu (``boy_mm``) stok ADINDAN okunur, profil kartına boy uzunluğu yazılır.

    python manage.py karma_boy_goc                       # KURU: yalnız listeler (stok kodu, ad, okunan mm, mevcut boy_mm); hiçbir şey yazmaz
    python manage.py karma_boy_goc --uygula              # listelenenleri yazar (onaydan sonra)
    python manage.py karma_boy_goc --istasyon 40 --profil 150-10-0011 --profil-boy 6000

Aday operasyon: verilen istasyonda, ÜRET, tek girdisi ``--profil`` olan, çıktı adında "(349,73 MM)" biçiminde boy geçen. Adı okunamayan operasyon YAZILMAZ (listede
"OKUNAMADI" görünür). Boy zaten dolu ve farklıysa üzerine yazılmaz (listede "ÇAKIŞMA"); yazılan: ``Operasyon.boy_mm`` + referans ``OperasyonCikti.boy_mm``.
"""
import re
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Operasyon, OperasyonCikti, Stok
from core.sayi import SayiHatasi, parse_tr
from core.services import uretim

_BOY_RE = re.compile(r"\(\s*([\d.,]+)\s*MM\s*\)", re.IGNORECASE)


class Command(BaseCommand):
    help = "Basamak operasyonlarının parça boyunu stok adından okur; profil kartına boy uzunluğu yazar (varsayılan KURU)."

    def add_arguments(self, parser):
        parser.add_argument("--istasyon", default="40", help="İş istasyonu kodu (varsayılan 40).")
        parser.add_argument("--profil", default="150-10-0011", help="Profil stok kodu (varsayılan 150-10-0011).")
        parser.add_argument("--profil-boy", default="6000", help="Profil boyu mm (varsayılan 6000).")
        parser.add_argument("--uygula", action="store_true", help="Yaz (verilmezse KURU: yalnız listeler).")

    def handle(self, *args, **o):
        profil = Stok.objects.filter(kod=o["profil"], silindi=False).first()
        if profil is None:
            raise CommandError(f"Profil bulunamadı: {o['profil']}")
        try:
            boy = parse_tr(o["profil_boy"])
        except SayiHatasi:
            raise CommandError("--profil-boy geçerli bir sayı olmalı.")
        satirlar, yazilacak = [], []
        qs = (Operasyon.objects.filter(silindi=False, istasyon__kod=o["istasyon"], tur=Operasyon.Tur.URET).select_related("cikti").order_by("cikti__kod"))
        for op in qs:
            g = uretim.operasyon_girdileri(op)
            if len(g) != 1 or g[0].girdi_id != profil.pk:
                continue
            m = _BOY_RE.search(op.cikti.ad)
            okunan, durum = None, "OKUNAMADI"
            if m:
                try:
                    okunan = parse_tr(m.group(1))
                    durum = "YAZILACAK"
                except SayiHatasi:
                    okunan = None
            if okunan is not None and op.boy_mm not in (None, okunan):
                durum = "ÇAKIŞMA"
            elif okunan is not None and op.boy_mm == okunan:
                durum = "ZATEN DOĞRU"
            satirlar.append((op, okunan, durum))
            if durum == "YAZILACAK":
                yazilacak.append((op, okunan))
        self.stdout.write(f"Profil {profil.kod} {profil.ad}: boy_uzunluk_mm {profil.boy_uzunluk_mm} → {boy}")
        self.stdout.write(f"{'stok kodu':14} {'okunan mm':>10} {'mevcut':>9}  durum        ad")
        for op, okunan, durum in satirlar:
            self.stdout.write(f"{op.cikti.kod:14} {('' if okunan is None else okunan):>10} {('' if op.boy_mm is None else op.boy_mm):>9}  {durum:12} {op.cikti.ad}")
        self.stdout.write(f"Toplam {len(satirlar)} operasyon; yazılacak {len(yazilacak)}; "
                          f"okunamayan {sum(d == 'OKUNAMADI' for _o, _m, d in satirlar)}; çakışma {sum(d == 'ÇAKIŞMA' for _o, _m, d in satirlar)}.")
        if not o["uygula"]:
            self.stdout.write("KURU çalıştırma: hiçbir şey yazılmadı (yazmak için --uygula).")
            return
        with transaction.atomic():
            if profil.boy_uzunluk_mm != boy:
                profil.boy_uzunluk_mm = boy
                profil.save(update_fields=["boy_uzunluk_mm", "updated_at"])
            for op, okunan in yazilacak:
                op.boy_mm = okunan
                op.save(update_fields=["boy_mm", "updated_at"])
                OperasyonCikti.objects.filter(operasyon=op, silindi=False, sira=0).update(boy_mm=okunan)
        self.stdout.write(f"UYGULANDI: profil boyu {boy}; {len(yazilacak)} operasyonun boy_mm değeri yazıldı.")
