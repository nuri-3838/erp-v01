"""Eski sistemden cari kategorilerini yükler (idempotent, üst→alt sıralı).

Beklenen JSON:
  {"kategoriler":[{"id":13,"ust":null,"kod":"120","ad":"MÜŞTERİLER","aciklama":""},
                  {"id":14,"ust":13,"kod":"10","ad":"...","aciklama":""}, ...]}
Var olan (aynı üst+kod) atlanır; çok geçişli: üst eklenince altı eklenir.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core.metin import buyuk_harf_tr
from core.models import CariKategori
from core.services import cari_kategori as K


class Command(BaseCommand):
    help = "Cari kategorilerini JSON'dan yükler (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("json_yolu")

    def handle(self, *args, **opts):
        try:
            with open(opts["json_yolu"], encoding="utf-8") as f:
                kayitlar = json.load(f).get("kategoriler", [])
        except (OSError, ValueError) as e:
            raise CommandError(f"JSON okunamadı: {e}")

        harita = {}          # old_id -> CariKategori
        eklenen = 0
        kalan = list(kayitlar)
        ilerleme = True
        while kalan and ilerleme:
            ilerleme = False
            yeni_kalan = []
            for r in kalan:
                ust_old = r.get("ust")
                if ust_old is None:
                    ust = None
                elif ust_old in harita:
                    ust = harita[ust_old]
                else:
                    yeni_kalan.append(r)
                    continue
                ust_id = ust.pk if ust else None
                mevcut = CariKategori.objects.filter(
                    silindi=False, ust_id=ust_id,
                    kod=buyuk_harf_tr((r["kod"] or "").strip())).first()
                if mevcut is None:
                    mevcut = K.cari_kategori_olustur(
                        ad=r["ad"], kod=r["kod"], ust_id=ust_id)
                    eklenen += 1
                harita[r["id"]] = mevcut
                ilerleme = True
            kalan = yeni_kalan

        toplam = CariKategori.objects.filter(silindi=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Cari kategori taşıma tamam: {eklenen} eklendi, toplam {toplam}."
            + (f" {len(kalan)} kayıt üst bulunamadığı için atlandı." if kalan else "")))
