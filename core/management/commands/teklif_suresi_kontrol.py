"""Satış Teklifi'nde geçerlilik tarihi geçmiş "Gönderildi" belgeleri "Süresi Doldu"ya
çeker (cron).

GONDERILDI + gecerlilik_teslim_tarihi < bugün olan Satış Tekliflerini SURESI_DOLDU'ya
çeker (bkz. core.services.teklif_siparis.teklif_suresi_dolanlari_isaretle). "Yumuşak" bir
durak: SURESI_DOLDU'dan da Kabul/Red edilebilir (geç gelen müşteri cevabı) — bu komut
yalnız liste/filtre rozetini günceller, hiçbir işlemi engellemez. Çalışma
``logs/teklif_suresi_cron.log`` dosyasına loglanır.

"Bugün" TR takvim gününe göre alınır (sunucu UTC olsa da muhasebe tarihi TR'dir).

Cron (sunucuda, her gün gece 01:00 TR = 22:00 UTC)::

    0 22 * * * cd /home/nuri/erp_v01 && /home/nuri/erp_v01/.venv/bin/python \\
        manage.py teklif_suresi_kontrol >> /home/nuri/erp_v01/logs/teklif_suresi_cron.log 2>&1

Elle: ``python manage.py teklif_suresi_kontrol``
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.teklif_siparis import teklif_suresi_dolanlari_isaretle
from core.tarih import tr_bugun


def _logger():
    log = logging.getLogger("teklif_suresi_kontrol")
    if not log.handlers:
        log.setLevel(logging.INFO)
        logs_dir = settings.BASE_DIR / "logs"
        logs_dir.mkdir(exist_ok=True)
        h = logging.FileHandler(logs_dir / "teklif_suresi_cron.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.propagate = False
    return log


class Command(BaseCommand):
    help = "Geçerlilik tarihi geçmiş 'Gönderildi' Satış Tekliflerini 'Süresi Doldu'ya çeker."

    def handle(self, *args, **opts):
        log = _logger()
        bugun = tr_bugun()
        try:
            sayisi = teklif_suresi_dolanlari_isaretle(bugun=bugun)
        except Exception as e:  # cron sessizce ölmesin
            log.exception("beklenmeyen HATA: %s", e)
            self.stderr.write(f"teklif_suresi_kontrol beklenmeyen HATA: {e}")
            return
        log.info("bitti: bugun=%s işaretlenen=%s", bugun, sayisi)
        self.stdout.write(self.style.SUCCESS(
            f"teklif_suresi_kontrol tamam: {bugun} -> {sayisi} teklif süresi doldu."
        ))
