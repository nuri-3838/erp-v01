"""TCMB kurlarını otomatik çek (Aşama 2 — cron).

KUR tablosundaki son tarihin bir gün öncesinden BUGÜNE kadar günleri (yeniden) çeker, sonra YARIN (D+1) satırını şu kuralla yazar
(``tcmb.yarin_kuru_sec``): (1) bugün TCMB yayını alındıysa D+1 = bugünün yayını; (2) bugün yayın yok (hafta sonu/tatil) ve saat 16:00'yı
geçtiyse D+1 = son yayın; (3) iş günü olup yayın henüz gelmediyse D+1 YAZILMAZ (yer tutucu kur belgeye girmesin) — 18:00 koşusu tamamlar.
Bir tarihin kuru değişecekse ve o tarihli fiş/fatura varsa üzerine yazılmaz, ``KurUyarisi`` açılır (Kurlar ekranı + pano uyarısı).
Sunucu kapanır ya da bir gün kaçarsa görev ertesi çalıştığında aradaki günleri tamamlar (telafi).
Yoğun/kaydırmalı/tatil-atlamalı ``kurlari_guncelle`` motorunu kullanır. Çalışma
``logs/kur_cron.log`` dosyasına loglanır (ne zaman, hangi aralık, kaç gün, hata).

"Bugün" TR takvim gününe göre alınır (sunucu UTC olsa da muhasebe tarihi TR'dir).

Cron (sunucuda, HER GÜN 16:00 ve 18:00 TR = 13:00/15:00 UTC — hafta sonu/tatilde de koşar, önceki iş günü kuru yazılır; 18:00 koşusu
TCMB yayın gecikmesine karşı tekrar; eski sisteme dokunmaz)::

    0 13,15 * * * cd /home/nuri/erp_v01 && /home/nuri/erp_v01/.venv/bin/python \\
        manage.py kur_cek >> /home/nuri/erp_v01/logs/kur_cron.log 2>&1

Elle: ``python manage.py kur_cek``  (gerekirse ``--gun N`` ile boş tabloda geri sınır).
"""
import datetime
import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from core.models import Kur
from core.services.tcmb import AZAMI_GUN, TcmbHatasi, _yaz, kurlari_guncelle, yarin_kuru_sec

ILK_BACKFILL = 7              # tablo boşken kaç gün geriden başlanacağı (varsayılan)
_TR = ZoneInfo("Europe/Istanbul")


def tr_bugun() -> datetime.date:
    """TR takvim günü (sunucu UTC olsa da muhasebe tarihi TR'ye göre)."""
    return timezone.now().astimezone(_TR).date()


def tr_simdi() -> datetime.time:
    """TR saati (yarın kuralı: 16:00 eşiği)."""
    return timezone.now().astimezone(_TR).time()


def _logger():
    log = logging.getLogger("kur_cek")
    if not log.handlers:
        log.setLevel(logging.INFO)
        logs_dir = settings.BASE_DIR / "logs"
        logs_dir.mkdir(exist_ok=True)
        h = logging.FileHandler(logs_dir / "kur_cron.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.propagate = False
    return log


class Command(BaseCommand):
    help = "TCMB kurlarını çeker (son tarihin bir gün öncesinden bugüne; yarın satırı yayın/16:00 kuralıyla; belgeli tarih korunur)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--gun", type=int, default=ILK_BACKFILL,
            help=f"Tablo boşsa kaç gün geriden başlanacağı (varsayılan {ILK_BACKFILL}).",
        )

    def handle(self, *args, **opts):
        log = _logger()
        bugun = tr_bugun()
        bitis = bugun                                       # bugüne kadar; YARIN ayrı kuralla (aşağıda)
        yarin = bugun + datetime.timedelta(days=1)
        son = min(Kur.objects.aggregate(m=Max("tarih"))["m"] or bugun, bugun) if Kur.objects.exists() else None

        if son is None:
            baslangic = bugun - datetime.timedelta(days=opts["gun"])
        else:
            baslangic = son - datetime.timedelta(days=1)     # son iki günü yeniden çek (idempotent: gecikmiş yayın düzelir), ileriyi doldur
        if baslangic > bugun:
            baslangic = bugun

        # Çok büyük boşlukta üst sınır: son AZAMI_GUN günü çek (kalanı elle doldurulur).
        capli = (bitis - baslangic).days + 1 > AZAMI_GUN
        if capli:
            baslangic = bitis - datetime.timedelta(days=AZAMI_GUN - 1)

        log.info("başladı: aralık %s .. %s%s", baslangic, bitis,
                 " [CAP: eski boşluk elle doldurulmalı]" if capli else "")
        try:
            ozet = kurlari_guncelle(baslangic, bitis)
        except TcmbHatasi as e:
            log.error("HATA (TCMB): %s", e)
            self.stderr.write(f"kur_cek HATA: {e}")
            return
        except Exception as e:  # ağ/beklenmeyen — cron sessizce ölmesin
            log.exception("beklenmeyen HATA: %s", e)
            self.stderr.write(f"kur_cek beklenmeyen HATA: {e}")
            return

        kurlar, neden = yarin_kuru_sec(bugun, tr_simdi(), ozet.get("bitis_yayin"), ozet.get("son_yayin"))
        if kurlar:
            yarin_sonuc = _yaz(yarin, kurlar, None, kaynak="cron")
            log.info("yarın %s: %s (%s)", yarin, yarin_sonuc, neden)
        else:
            yarin_sonuc = "yazilmadi"
            log.info("yarın %s yazılmadı: %s", yarin, neden)
        if ozet.get("korunan"):
            log.warning("KORUNAN %s gün: kur değişecekti ama o tarihli belge var — Kurlar ekranındaki uyarıya bakın", ozet["korunan"])
        log.info("bitti: yayin=%s yazilan=%s atlanan=%s korunan=%s",
                 ozet["yayin"], ozet["yazilan"], ozet["atlanan"], ozet.get("korunan", 0))
        self.stdout.write(self.style.SUCCESS(
            f"kur_cek tamam: {baslangic}..{bitis} -> "
            f"yayin={ozet['yayin']} yazilan={ozet['yazilan']} atlanan={ozet['atlanan']} korunan={ozet.get('korunan', 0)} | "
            f"yarın {yarin}: {yarin_sonuc} ({neden})"
        ))
