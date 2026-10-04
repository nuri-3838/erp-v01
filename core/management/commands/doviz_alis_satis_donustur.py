"""Tek seferlik: "Hesaba Ödeme / Hesaptan Giriş" ile girilmiş döviz alış/satış fişlerini DÖVİZ ALIŞ/SATIŞ
yapısına dönüştürür (varsayılan DRY-RUN).

Eski kayıtta TL banka/kasa hesabı satırı da döviz (USD) işlem para birimiyle yazılmıştı; bu yüzden TL
hesabın ekstresinde döviz sütununda USD görünüyordu (ör. Vakıf TL "USD −3.380"). Dönüşüm fişi YERİNDE
düzeltir (fiş no / kaynak / banka hesabı bağı korunur): TL hesabı satırı TL (işlem PB = TRY, kur 1) olur;
döviz hesabı satırı olduğu gibi kalır. Adaylar: açıklamasında "DÖVİZ ALIŞ" / "DÖVİZ SATIŞ" geçen,
2 satırlı, bir tarafı TL banka/kasa, öbür tarafı aynı PB'li döviz banka/kasa olan BANKA/KASA fişleri.
Yapıya uymayanlar (ör. yalnız TL satırlı kambiyo vergisi fişi) dokunulmadan raporlanır.
Kur farkı (ortalama kur) hesabı bu komutun işi DEĞİLDİR — ayrı komut: kur_farki_geriye_donuk.

    python manage.py doviz_alis_satis_donustur [--uygula]
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.models import BankaHesap, Kasa, YevmiyeFisi
from core.sayi import format_tr


def _kume():
    tl, dvz = {}, {}
    for model, ad in ((BankaHesap, "banka"), (Kasa, "kasa")):
        for h in model.objects.filter(silindi=False):
            hedef = tl if h.para_birimi == "TRY" else dvz
            hedef[h.muhasebe_id] = (ad, h.pk, h.para_birimi, getattr(h, "ad", ""))
    return tl, dvz


class Command(BaseCommand):
    help = "Dovizli hesaba odeme/giris ile girilmis doviz alis/satis fislerini duzeltir (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def _tl_hesap_usd_bakiyeleri(self, tl_kodlari):
        """TL hesapların ekstresindeki döviz sütunu: {hesap: {pb: net döviz}}."""
        from core.models import YevmiyeSatir
        out = {}
        for s in (YevmiyeSatir.objects.filter(hesap_id__in=tl_kodlari, silindi=False, fis__silindi=False)
                  .exclude(islem_pb="TRY")):
            d = out.setdefault(s.hesap_id, {})
            d[s.islem_pb] = d.get(s.islem_pb, 0) + (s.islem_tutari if s.borc else -s.islem_tutari)
        return out

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        tl_k, dvz_k = _kume()
        once = self._tl_hesap_usd_bakiyeleri(tl_k)
        w("\nÖNCE TL hesapların döviz sütunu (ekstre): "
          + (", ".join(f"{k} {dict((p, str(v)) for p, v in d.items())}" for k, d in sorted(once.items())) or "yok"))
        adaylar = (YevmiyeFisi.objects.filter(silindi=False, kaynak__in=("BANKA", "KASA"))
                   .filter(Q(aciklama__contains="DÖVİZ ALIŞ") | Q(aciklama__contains="DÖVİZ SATIŞ"))
                   .order_by("tarih", "yil", "fis_no"))
        donusen, atlanan = [], []
        with transaction.atomic():
            w(f"\nAday fiş sayısı (açıklamada DÖVİZ ALIŞ/SATIŞ): {adaylar.count()}")
            for f in adaylar:
                sat = list(f.satirlar.filter(silindi=False).order_by("id"))
                no = f"{f.yil}/{f.fis_no}"
                tl_s = [s for s in sat if s.hesap_id in tl_k]
                dv_s = [s for s in sat if s.hesap_id in dvz_k]
                if len(sat) != 2 or len(tl_s) != 1 or len(dv_s) != 1:
                    nedeni = "yapı uymuyor (2 satır: 1 TL + 1 döviz hesap bekleniyor)"
                    w(f"  {no} | id {f.pk} | {f.tarih:%d.%m.%Y} | '{f.aciklama[:70]}'\n      → DOKUNULMAZ: {nedeni}")
                    atlanan.append((no, nedeni))
                    continue
                t, d = tl_s[0], dv_s[0]
                tl_tutar = t.borc or t.alacak
                if t.islem_pb == "TRY":
                    w(f"  {no} | id {f.pk} | {f.tarih:%d.%m.%Y} | zaten TL satırlı → dönüşüme gerek yok")
                    atlanan.append((no, "zaten dönüştürülmüş"))
                    continue
                if t.islem_pb != d.islem_pb or tl_tutar != (d.borc or d.alacak):
                    nedeni = "para birimi/tutar uyuşmuyor"
                    w(f"  {no} | id {f.pk} | → DOKUNULMAZ: {nedeni}")
                    atlanan.append((no, nedeni))
                    continue
                yon = "ALIŞ" if d.borc else "SATIŞ"
                w(f"  {no} | id {f.pk} | {f.tarih:%d.%m.%Y} | DÖVİZ {yon} {format_tr(d.islem_tutari)} {d.islem_pb} "
                  f"@ {d.islem_kuru:f} | TL {format_tr(tl_tutar)} | TL hesap {t.hesap_id} [{tl_k[t.hesap_id][3]}] "
                  f"↔ döviz hesap {d.hesap_id} [{dvz_k[d.hesap_id][3]}]")
                w(f"      TL hesap satırı: {t.islem_pb} {format_tr(t.islem_tutari)} @ {t.islem_kuru:f}  →  "
                  f"TRY {format_tr(tl_tutar)} @ 1")
                t.islem_pb, t.islem_tutari, t.islem_kuru = "TRY", tl_tutar, 1
                t.save(update_fields=["islem_pb", "islem_tutari", "islem_kuru", "updated_at"])
                donusen.append(no)
            sonra = self._tl_hesap_usd_bakiyeleri(tl_k)
            w(f"\nDönüşen fiş: {len(donusen)} {donusen}")
            w(f"Dokunulmayan/dönüşmeyen: {atlanan or 'yok'}")
            w("SONRA TL hesapların döviz sütunu (ekstre): "
              + (", ".join(f"{k} {dict((p, str(v)) for p, v in d.items())}" for k, d in sorted(sonra.items())) or
                 "yok (TL hesaplarda döviz kalıntısı kalmadı)"))
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
