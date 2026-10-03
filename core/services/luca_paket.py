"""Luca zip paketlerini okuma (gelen e-fatura / e-irsaliye): dosya adı ``No_SaticiVKN_Unvan.(xml|pdf)``."""
from __future__ import annotations

import re
import zipfile
from collections import defaultdict

from django.core.management.base import CommandError

from core.services.fatura import _fatura_no_anahtar

AD_DESENI = re.compile(r"^(?P<no>[^_]+)_(?P<vkn>\d{10,11})_(?P<unvan>.*)\.(?P<uz>xml|pdf)$",
                       re.IGNORECASE)


def zip_adi(bilgi):
    """UTF-8 bayrağı yoksa zipfile adı cp437 sanır; Luca/Windows zip'lerinde cp1254 olabilir."""
    ad = bilgi.filename
    if not (bilgi.flag_bits & 0x800):
        try:
            ad = ad.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                ad = ad.encode("cp437").decode("cp1254")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    return ad.replace("\\", "/").rsplit("/", 1)[-1]


def paketleri_oku(yollar):
    """-> (gruplar{(no_anahtar, vkn): {xml/pdf: (ad, bayt)}}, tanimayan[], tekrar[])"""
    gruplar, tanimayan, tekrar = defaultdict(dict), [], []
    for yol in yollar:
        try:
            z = zipfile.ZipFile(yol)
        except (OSError, zipfile.BadZipFile) as e:
            raise CommandError(f"Zip açılamadı: {yol} ({e})")
        with z:
            for b in z.infolist():
                if b.is_dir():
                    continue
                ad = zip_adi(b)
                m = AD_DESENI.match(ad)
                if not m:
                    tanimayan.append(ad)
                    continue
                anahtar = (_fatura_no_anahtar(m["no"]), m["vkn"])
                uz = m["uz"].lower()
                if uz in gruplar[anahtar]:
                    tekrar.append(ad)
                    continue
                gruplar[anahtar][uz] = (ad, z.read(b))
    return gruplar, tanimayan, tekrar
