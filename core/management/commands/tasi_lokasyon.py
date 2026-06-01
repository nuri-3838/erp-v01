"""Eski sistemden çıkarılan ülke/şehir JSON'unu yeni sisteme yükler (idempotent).

Beklenen JSON:
  {"ulkeler":[{"kod":"TR","ad":"TÜRKİYE","ad_en":"TURKEY"}, ...],
   "sehirler":[{"ulke":"TR","kod":"38","ad":"KAYSERİ","ad_en":""}, ...]}
(``sehirler[].ulke`` = ülke ISO kodu.) Tekrar çalıştırılabilir: var olanı atlar.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core.metin import buyuk_harf_tr
from core.models import Ulke
from core.services import lokasyon as L


class Command(BaseCommand):
    help = "Ülke/Şehir verisini JSON'dan yükler (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("json_yolu")

    def handle(self, *args, **opts):
        try:
            with open(opts["json_yolu"], encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise CommandError(f"JSON okunamadı: {e}")

        u_ekli = s_ekli = 0
        for r in data.get("ulkeler", []):
            try:
                L.ulke_olustur(kod=r["kod"], ad=r["ad"], ad_en=r.get("ad_en", ""))
                u_ekli += 1
            except L.LokasyonHatasi:
                pass  # zaten var
        ulke_map = {u.kod: u for u in Ulke.objects.filter(silindi=False)}
        for r in data.get("sehirler", []):
            ulke = ulke_map.get(buyuk_harf_tr((r.get("ulke") or "").strip()))
            if ulke is None:
                continue
            try:
                L.sehir_olustur(ulke_id=ulke.pk, ad=r["ad"],
                                kod=r.get("kod", ""), ad_en=r.get("ad_en", ""))
                s_ekli += 1
            except L.LokasyonHatasi:
                pass  # zaten var
        self.stdout.write(self.style.SUCCESS(
            f"Lokasyon taşıma tamam: {u_ekli} ülke, {s_ekli} şehir eklendi."))
