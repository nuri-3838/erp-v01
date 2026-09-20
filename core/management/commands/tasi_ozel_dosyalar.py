"""Gizli yüklemeleri (cari/aday ekleri, çek/senet görselleri) MEDIA_ROOT'tan özel depoya
(IK_OZEL_DIR) taşır: kopyala → SHA-256 doğrula → (--sil ile) kaynağı sil. DB'ye dokunmaz.

Önerilen sıra: (1) --kuru, (2) bayraksız (yalnız kopyalar+doğrular), kod yayına alınıp çalıştığı
görüldükten sonra (3) --sil. Kod geri alınırsa --geri dosyaları MEDIA_ROOT'a döndürür."""
from django.core.management.base import BaseCommand, CommandError

from core.services import ozel_tasima


class Command(BaseCommand):
    help = "Cari/aday ekleri ve çek/senet görsellerini MEDIA_ROOT'tan özel depoya taşır."

    def add_arguments(self, parser):
        parser.add_argument("--kuru", action="store_true",
                            help="Hiçbir şey yazmadan/silmeden ne yapılacağını raporla.")
        parser.add_argument("--sil", action="store_true",
                            help="Kopya SHA-256 ile doğrulandıktan sonra kaynağı sil "
                                 "(aksi halde yalnız kopyalanır).")
        parser.add_argument("--geri", action="store_true",
                            help="Yönü ters çevir: özel depo → MEDIA_ROOT.")

    def handle(self, *args, **o):
        try:
            s = ozel_tasima.tasi(kuru=o["kuru"], sil=o["sil"], geri=o["geri"])
            eksik = ozel_tasima.eksik_kayitlar(geri=o["geri"])
        except ozel_tasima.OzelTasimaHatasi as e:
            raise CommandError(str(e))
        yon = "özel depo → MEDIA_ROOT" if o["geri"] else "MEDIA_ROOT → özel depo"
        ilk = "kopyalanacak" if s.kuru else "kopyalandı"
        self.stdout.write(
            f"[{yon}{' — KURU ÇALIŞMA' if s.kuru else ''}] {s.kopyalanan} {ilk} "
            f"({s.bayt} bayt), {s.zaten_var} zaten hedefte (doğrulandı), {s.silinen} kaynak silindi.")
        for etiket, liste in (("atlandı (symlink/dosya değil)", s.atlanan),
                              ("ÇAKIŞMA (hedefte farklı içerik; dokunulmadı)", s.catisma),
                              ("HATA", s.hata)):
            for ad in liste:
                self.stdout.write(self.style.WARNING(f"  {etiket}: {ad}"))
        if eksik:
            self.stdout.write(self.style.WARNING(
                f"  UYARI: {len(eksik)} DB kaydının dosyası hedefte yok (bilgi; taşımayı etkilemez):"))
            for etiket, pk, yol in eksik[:20]:
                self.stdout.write(f"    {etiket} pk={pk} {yol}")
        if not s.temiz:
            raise CommandError(
                f"tasi_ozel_dosyalar: {len(s.catisma)} çakışma, {len(s.hata)} hata — "
                "çakışan/hatalı dosyalara dokunulmadı; incelemeden yayına almayın.")
        self.stdout.write(self.style.SUCCESS("tasi_ozel_dosyalar tamam."))
