"""Fatura eklerindeki İRSALİYE PDF'lerini ait oldukları irsaliyelere taşır (2026-10-15).

Eşleşme (otomatik taşıma yalnız bu kuralla): fatura ekinin dosya adı e-belge arşiv kalıbındadır ``<BELGE_NO>_<VKN>_<UNVAN>.pdf``; BELGE_NO (boşluk/büyük-küçük harf farkı
yok sayılarak) ERP'deki AKTİF bir irsaliyenin ``irsaliye_no``'suna (tedarikçinin e-irsaliye numarası: IRS…, FRI…, BIG…, ELT…, VTD… vb.) eşit, dosya adındaki VKN o irsaliyenin
carisinin VKN'sine eşit ve irsaliye ya bu faturaya bağlı ya da hiç faturaya bağlanmamıştır. Faturanın KENDİ PDF'i (BELGE_NO = fatura no) hiç dokunulmaz.
Şüpheli olanlar (birden çok irsaliyeye uyan, VKN uyuşmayan, irsaliyesi başka faturaya bağlı, dosyası diskte olmayan, aynı irsaliyede tekrar eden, e-belge no'su ERP irsaliyelerinde
olmayan) TAŞINMAZ, ayrı listelenir. İçerik (PDF metni) taraması yapılmaz: bir fatura PDF'i anıtladığı irsaliye no'yu içerebilir; içerikle otomatik taşıma yanlış taşıma riski taşır.

Taşıma = AYNI dosya (yeniden yükleme yok; depolama yolu aynen kalır): irsaliyeye ``IrsaliyeEk`` kaydı açılır, faturadan ``FaturaEk`` kaydı fiziksel kaldırılır (dosya diskte durur).
Varsayılan ÖNİZLEME; ``--uygula`` tek transaction. İdempotent: ikinci çalıştırmada 0 iş. Taşınan her ek için denetim dökümü ``logs/irsaliye_ek_tasima_<tarih>.json``
(yalnız iş varsa yazılır). Ayrıca PDF'i bulunmayan irsaliyeler raporlanır.

    python manage.py irsaliye_ek_tasi
    python manage.py irsaliye_ek_tasi --uygula
"""
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Fatura, FaturaEk, IrsaliyeEk, TeklifSiparis
from core.services.fatura import _fatura_no_anahtar

_AD_KALIBI = re.compile(r"^([^_]+)_(\d{10,11})_")                   # BELGE_NO_VKN_UNVAN.pdf
_E_BELGE_NO = re.compile(r"^[A-Z0-9]{3}\d{13}$")                    # 3 karakter önek + 13 hane (e-fatura/e-irsaliye numarası)


def _sha(ek):
    try:
        with ek.dosya.open("rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _boyut(ek):
    try:
        return ek.dosya.size
    except OSError:
        return None


class Command(BaseCommand):
    help = "Fatura eklerindeki irsaliye PDF'lerini irsaliyelere taşır (varsayılan önizleme; --uygula tek transaction; idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true", help="Taşı (yoksa yalnız döküm).")
        parser.add_argument("--log-dizini", default=None, help="Denetim dökümü klasörü (varsayılan: <proje>/logs).")

    def handle(self, *args, **o):
        w = self.stdout.write
        uygula = o["uygula"]
        irsaliyeler = list(TeklifSiparis.objects.filter(belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, silindi=False)
                           .select_related("cari", "fatura").order_by("tarih", "id"))
        indeks = defaultdict(list)                                                    # normalize irsaliye_no -> [irsaliye]
        for t in irsaliyeler:
            if t.irsaliye_no.strip():
                indeks[_fatura_no_anahtar(t.irsaliye_no)].append(t)

        fatura_no_ler = {_fatura_no_anahtar(x) for x in Fatura.objects.filter(silindi=False).exclude(fatura_no="").values_list("fatura_no", flat=True)}
        tasinacak, atlanan = [], []
        kendi_pdf = kalip_disi = 0
        gorulen = set()
        for ek in (FaturaEk.objects.filter(silindi=False, fatura__silindi=False).select_related("fatura", "fatura__cari").order_by("fatura_id", "id")):
            ad = ek.orijinal_ad or os.path.basename(ek.dosya.name)
            if not ek.dosya.name.lower().endswith(".pdf"):
                continue                                                              # yalnız PDF
            m = _AD_KALIBI.match(ad)
            if not m:
                kalip_disi += 1
                continue
            belge, vkn = _fatura_no_anahtar(m.group(1)), m.group(2)
            if belge == _fatura_no_anahtar(ek.fatura.fatura_no):
                kendi_pdf += 1                                                        # faturanın kendi PDF'i — dokunulmaz
                continue
            satir = {"ek": ek, "ad": ad, "fatura_no": ek.fatura.fatura_no, "belge": belge, "vkn": vkn}
            adaylar = indeks.get(belge, [])
            if not adaylar:
                if belge in fatura_no_ler:
                    atlanan.append({**satir, "neden": "başka bir ERP faturasının PDF'i (irsaliye değil)"})
                elif _E_BELGE_NO.match(belge):
                    atlanan.append({**satir, "neden": "e-belge no ERP irsaliyelerinde yok (irsaliye ERP'de kayıtlı değil ya da başka bir fatura PDF'i)"})
                else:
                    kalip_disi += 1
                continue
            vkn_uyan = [t for t in adaylar if t.cari_id and t.cari.vkn_tckn == vkn]
            if not vkn_uyan:
                atlanan.append({**satir, "neden": f"irsaliye no eşleşti ({', '.join(t.belge_no for t in adaylar)}) ama VKN uyuşmuyor"})
                continue
            if len(vkn_uyan) > 1:
                atlanan.append({**satir, "neden": "birden çok irsaliyeye uyuyor: " + ", ".join(t.belge_no for t in vkn_uyan)})
                continue
            t = vkn_uyan[0]
            if t.fatura_id and t.fatura_id != ek.fatura_id:
                atlanan.append({**satir, "neden": f"irsaliye {t.belge_no} başka bir faturaya bağlı ({t.fatura.fatura_no})"})
                continue
            if not ek.dosya.storage.exists(ek.dosya.name):
                atlanan.append({**satir, "neden": "dosya diskte yok"})
                continue
            if (t.pk, ad) in gorulen:
                atlanan.append({**satir, "neden": f"aynı dosya adı {t.belge_no} için zaten taşınacak (tekrar)"})
                continue
            gorulen.add((t.pk, ad))
            yontem = ("dosya adı: e-irsaliye no + VKN eşleşti; irsaliye bu faturaya bağlı" if t.fatura_id
                      else "dosya adı: e-irsaliye no + VKN eşleşti; irsaliye henüz faturaya bağlı değil")
            tasinacak.append({**satir, "irsaliye": t, "yontem": yontem})

        w(self.style.WARNING("UYGULAMA MODU" if uygula else "ÖNİZLEME / DÖKÜM (hiçbir şey değişmez; --uygula ile taşınır)"))
        w(f"\nTAŞINACAK : {len(tasinacak)}   ATLANAN (şüpheli, dokunulmaz): {len(atlanan)}   "
          f"(faturanın kendi PDF'i: {kendi_pdf}, e-belge kalıbı dışı: {kalip_disi})")
        if tasinacak:
            w("\n== TAŞINACAK: fatura no | irsaliye (SAI / e-irsaliye no) | dosya | yöntem")
            for x in tasinacak:
                t = x["irsaliye"]
                w(f"  {x['fatura_no']:24} | {t.belge_no} / {t.irsaliye_no} | {x['ad']} | {x['yontem']}")
        if atlanan:
            w("\n== ATLANAN: fatura no | dosya | neden")
            for x in atlanan:
                w(f"  {x['fatura_no']:24} | {x['ad']} | {x['neden']}")

        hedef = {x["irsaliye"].pk for x in tasinacak}
        pdf_var = set(IrsaliyeEk.objects.filter(silindi=False, irsaliye__in=irsaliyeler, dosya__iendswith=".pdf").values_list("irsaliye_id", flat=True)) | hedef
        eksik = [t for t in irsaliyeler if t.pk not in pdf_var]
        w(f"\n== PDF'İ OLMAYAN İRSALİYELER ({len(eksik)} / {len(irsaliyeler)}) — taşıma sonrası da PDF'i bulunmayanlar")
        for t in eksik:
            f = t.fatura
            w(f"  {t.belge_no} | e-irsaliye no={t.irsaliye_no or '—'} | {t.tarih:%d.%m.%Y} | {t.cari.unvan if t.cari_id else '—'} | fatura={f.fatura_no if f else '—'}")

        if not uygula:
            w("\nÖnizleme bitti — hiçbir kayıt değişmedi.")
            return
        if not tasinacak:
            w("\nTaşınacak ek yok (0 iş).")
            return
        denetim = []
        with transaction.atomic():
            for x in tasinacak:
                ek, t = x["ek"], x["irsaliye"]
                denetim.append({"fatura_ek_id": ek.pk, "fatura_id": ek.fatura_id, "fatura_no": ek.fatura.fatura_no, "irsaliye_id": t.pk, "irsaliye_belge_no": t.belge_no,
                                "irsaliye_no": t.irsaliye_no, "orijinal_ad": ek.orijinal_ad, "dosya": ek.dosya.name, "boyut": _boyut(ek), "sha256": _sha(ek), "yontem": x["yontem"]})
                yeni = IrsaliyeEk.objects.create(irsaliye=t, dosya=ek.dosya.name, orijinal_ad=ek.orijinal_ad, created_by=ek.created_by, updated_by=ek.created_by)
                if not yeni.dosya.storage.exists(yeni.dosya.name):
                    raise RuntimeError(f"Dosya diskte bulunamadı, işlem geri alındı: {yeni.dosya.name}")
                ek.delete()                                                           # yalnız kayıt (dosya diskte kalır — artık irsaliye ekinin dosyası)
        log_dir = Path(o["log_dizini"]) if o["log_dizini"] else Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"irsaliye_ek_tasima_{timezone.now():%Y%m%d_%H%M%S}.json"
        log.write_text(json.dumps(denetim, ensure_ascii=False, indent=1), encoding="utf-8")
        w(self.style.SUCCESS(f"\nTAŞINDI: {len(denetim)} ek (aynı dosya, yeniden yükleme yok). Denetim dökümü: {log}"))
