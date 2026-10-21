"""Cron girişi (her 5 dk): kapanması gereken OPERATÖR mesailerini otomatik kapatır — fabrika dışı toleransı dolanlar (bitiş = son fabrika görülme) ve gece
kapanış saatini geçenler (bitiş = son fabrika görülme, yoksa o günün gece saati). ``--kuru``: yalnız listeler, hiçbir şey yazmaz.
Saat kararları Türkiye saatiyle verilir (sunucu UTC olsa da). Ayarlar: AYARLAR › Mesai Ayarları (bkz. core.services.mesai_otomatik)."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.services import mesai_otomatik
from core.tarih import _TR

ETIKET = {mesai_otomatik.SEBEP_DIS_IP: "fabrika dışı", mesai_otomatik.SEBEP_GECE: "gece"}


class Command(BaseCommand):
    help = "Fabrika dışı toleransı dolan ve gece kapanış saatini geçen operatör mesailerini otomatik kapatır (--kuru: yalnız listele)."

    def add_arguments(self, parser):
        parser.add_argument("--kuru", action="store_true", help="Hiçbir şey yazma; kapanacak mesaileri listele.")

    def handle(self, *args, **opts):
        kuru = opts["kuru"]
        simdi = timezone.now()
        etiket = f"[{simdi.astimezone(_TR):%d.%m.%Y %H:%M} TR] mesai_otomatik_kapat{' (KURU)' if kuru else ''}"
        devrede = mesai_otomatik.devrede_mi()
        sonuc = mesai_otomatik.toplu_kapat(simdi=simdi, kuru=kuru) if devrede else []
        from core.services import is_oturumu
        supurulen = is_oturumu.supurme(simdi=simdi, kuru=kuru)                  # mesaisi kapanmış ama açık kalan iş üyelikleri (ofis düzeltmesi vb.) — otomatik kapanış ayarından bağımsız
        for r in supurulen:
            self.stdout.write(f"{etiket}: süpürme — {r['uye'].personel.ad_soyad} üyeliği {r['uye'].oturum.istasyon_emri.no} oturumundan "
                              f"{'çıkarılacak' if kuru else 'çıkarıldı'} (bitiş {r['bitis'].astimezone(_TR):%d.%m %H:%M}).")
        if not devrede:
            if not supurulen:
                self.stdout.write(f"{etiket}: devre dışı (ayar kapalı ya da izinli ağ listesi boş).")
            return
        if not sonuc:
            if not supurulen:
                self.stdout.write(f"{etiket}: kapanacak mesai yok.")
            return
        self.stdout.write(f"{etiket}: {len(sonuc)} mesai {'kapanacak' if kuru else 'kapatıldı'}:")
        for r in sonuc:
            k = r["kayit"]
            son = k.son_fabrika_gorulme.astimezone(_TR).strftime("%d.%m %H:%M") if k.son_fabrika_gorulme else "—"
            self.stdout.write(f"  #{k.pk} {k.personel.ad_soyad} | iş tarihi {k.is_tarihi:%d.%m.%Y} | giriş {k.giris_zamani.astimezone(_TR):%d.%m %H:%M} | "
                              f"sebep {ETIKET[r['sebep']]} | son fabrika görülme {son} | bitiş {r['bitis'].astimezone(_TR):%d.%m %H:%M}")
