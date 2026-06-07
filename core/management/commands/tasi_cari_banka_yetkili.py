"""Eski sistemden cari banka hesapları + yetkili kişileri yükler (idempotent).

JSON:
  {"bankalar":[{"cari":"320-10-0001","banka_adi":"HALKBANK","hesap_sahibi":"",
                "iban":"TR..","swift":"","para_birimi":"TRY","aciklama":"","varsayilan":true}],
   "yetkililer":[{"cari":"320-10-0002","ad_soyad":"...","unvan":"","telefon":"","eposta":"","notlar":""}]}
``cari`` = cari kodu. Var olan (cari+banka_adi+iban / cari+ad_soyad) atlanır.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core.metin import buyuk_harf_tr
from core.models import Cari, CariBanka, CariYetkili
from core.services import cari as C


class Command(BaseCommand):
    help = "Cari banka hesabı + yetkili kişileri JSON'dan yükler (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("json_yolu")

    def handle(self, *args, **opts):
        try:
            with open(opts["json_yolu"], encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise CommandError(f"JSON okunamadı: {e}")

        cari_map = {c.kod: c for c in Cari.objects.filter(silindi=False)}
        b_ekli = y_ekli = 0

        for r in data.get("bankalar", []):
            cari = cari_map.get((r.get("cari") or "").strip())
            if cari is None:
                continue
            ad = buyuk_harf_tr((r.get("banka_adi") or "").strip())
            iban = buyuk_harf_tr((r.get("iban") or "").strip())
            if CariBanka.objects.filter(cari=cari, silindi=False, banka_adi=ad,
                                        iban=iban).exists():
                continue
            C.banka_ekle(cari, banka_adi=r.get("banka_adi", ""),
                         hesap_sahibi=r.get("hesap_sahibi", ""), iban=r.get("iban", ""),
                         swift=r.get("swift", ""), para_birimi=r.get("para_birimi", "TRY"),
                         aciklama=r.get("aciklama", ""), varsayilan=bool(r.get("varsayilan")))
            b_ekli += 1

        for r in data.get("yetkililer", []):
            cari = cari_map.get((r.get("cari") or "").strip())
            if cari is None:
                continue
            ad = buyuk_harf_tr((r.get("ad_soyad") or "").strip())
            if CariYetkili.objects.filter(cari=cari, silindi=False, ad_soyad=ad).exists():
                continue
            C.yetkili_ekle(cari, ad_soyad=r.get("ad_soyad", ""), unvan=r.get("unvan", ""),
                           telefon=r.get("telefon", ""), eposta=r.get("eposta", ""),
                           notlar=r.get("notlar", ""))
            y_ekli += 1

        self.stdout.write(self.style.SUCCESS(
            f"Banka/Yetkili taşıma tamam: {b_ekli} banka, {y_ekli} yetkili eklendi."))
