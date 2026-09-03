"""Eski sistemden cari kartlarını yükler (idempotent). Eski KOD korunur.

Beklenen JSON kaydı (her cari):
  {"kod","unvan","kisa_ad","kategori"(kod_yolu|null),"vergi_dairesi","vkn_tckn","tax_id",
   "telefon","telefon_2","eposta","web","kep_adresi","ulke"(kod|null),"sehir"(ad|null),
   "adres","posta_kodu","sevk_farkli","sevk_ulke"(kod|null),"sevk_sehir"(ad|null),
   "sevk_adres","sevk_posta_kodu","para_birimi","kredi_limiti","iskonto_yuzdesi","notlar"}
Kategori kod yoluna, ülke koduna, şehir (ülke+ad) eşleştirilir. Var olan kod atlanır.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core.metin import buyuk_harf_tr
from core.models import Cari, CariKategori, Sehir, Ulke
from core.services import cari as C


class Command(BaseCommand):
    help = "Cari kartlarını JSON'dan yükler (idempotent, eski kodu korur)."

    def add_arguments(self, parser):
        parser.add_argument("json_yolu")

    def handle(self, *args, **opts):
        try:
            with open(opts["json_yolu"], encoding="utf-8") as f:
                kayitlar = json.load(f).get("cariler", [])
        except (OSError, ValueError) as e:
            raise CommandError(f"JSON okunamadı: {e}")

        kat_map = {k.kod_yolu: k for k in
                   CariKategori.objects.filter(silindi=False).select_related("ust")}
        ulke_map = {u.kod: u for u in Ulke.objects.filter(silindi=False)}
        sehir_map = {(s.ulke.kod, s.ad): s for s in
                     Sehir.objects.filter(silindi=False).select_related("ulke")}

        def ulke_id(kod):
            u = ulke_map.get(buyuk_harf_tr((kod or "").strip())) if kod else None
            return u.pk if u else None

        def sehir_id(ulke_kod, ad):
            if not ad:
                return None
            s = sehir_map.get((buyuk_harf_tr((ulke_kod or "").strip()),
                               buyuk_harf_tr((ad or "").strip())))
            return s.pk if s else None

        eklenen = atlanan = 0
        for r in kayitlar:
            kod = (r.get("kod") or "").strip()
            if kod and Cari.objects.filter(silindi=False, kod=kod).exists():
                atlanan += 1
                continue
            kat = kat_map.get((r.get("kategori") or "").strip()) if r.get("kategori") else None
            cari = C.cari_olustur(
                kod=kod, unvan=r["unvan"], kategori_id=(kat.pk if kat else None),
                kisa_ad=r.get("kisa_ad", ""), vergi_dairesi=r.get("vergi_dairesi", ""),
                vkn_tckn=r.get("vkn_tckn", ""), tax_id=r.get("tax_id", ""),
                telefon=r.get("telefon", ""), telefon_2=r.get("telefon_2", ""),
                eposta=r.get("eposta", ""), web=r.get("web", ""),
                kep_adresi=r.get("kep_adresi", ""),
                ulke_id=ulke_id(r.get("ulke")), sehir_id=sehir_id(r.get("ulke"), r.get("sehir")),
                adres=r.get("adres", ""), posta_kodu=r.get("posta_kodu", ""),
                para_birimi=r.get("para_birimi", "TRY"),
                kredi_limiti=r.get("kredi_limiti", 0),
                iskonto_yuzdesi=r.get("iskonto_yuzdesi", 0), notlar=r.get("notlar", ""))
            if r.get("sevk_farkli"):
                C.sevk_adresi_ekle(
                    cari, ad="Sevk Adresi",
                    ulke_id=ulke_id(r.get("sevk_ulke")),
                    sehir_id=sehir_id(r.get("sevk_ulke"), r.get("sevk_sehir")),
                    adres=r.get("sevk_adres", ""), posta_kodu=r.get("sevk_posta_kodu", ""),
                    varsayilan=True)
            eklenen += 1

        toplam = Cari.objects.filter(silindi=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Cari taşıma tamam: {eklenen} eklendi, {atlanan} atlandı, toplam {toplam}."))
