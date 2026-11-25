"""TEK SEFERLİK: 646/656 karşı hesaplı bir CARİ VİRMAN fişinin cari satırını bir DÖVİZ havuzuna bağlar (döviz tutarı 0, yalnız TL).

Vaka (2026-11-25): 2 BAREN PROJE (TRY carisi, 136.10.0001). USD 6.500 alındı (2026/351) ve iade edildi (2026/414) → USD havuzu döviz 0, TL 4.433,00
kaldı; fark 2026/2463 virmanıyla (656 B / 136.10.0001 A 4.433,00) TRY satırı olarak yazıldı → ekstrede TRY havuzu 4.433 düştü. Bu komut FİŞİ
DEĞİŞTİRMEZ (hesaplar, borç/alacak TL tutarları aynı kalır); yalnız cari satırının işlem para birimini TRY 4.433 → USD 0 (yalnız TL) yapar.
Böylece TRY havuzu eski haline döner, USD havuzunun TL karşılığı kapanır; mizan değişmez.

Kullanım:
    python manage.py virman_kur_farki_havuz_ata --fis 2026/2463            # DRY-RUN (varsayılan): yalnız gösterir, yazmaz
    python manage.py virman_kur_farki_havuz_ata --fis 2026/2463 --pb USD   # havuz açıkça (boşsa: döviz 0 / TL farkı kalan tek havuz)
    python manage.py virman_kur_farki_havuz_ata --fis 2026/2463 --uygula   # yazar (tek transaction)
İdempotent: satır zaten o havuza bağlıysa hiçbir şey yapmaz.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Cari, YevmiyeFisi, YevmiyeSatir
from core.sayi import format_tr
from core.services import cari_virman as cv
from core.services import kur_farki as kf


def _havuzlar(hesap):
    h = {}
    for s in YevmiyeSatir.objects.filter(hesap_id=hesap, silindi=False, fis__silindi=False):
        t = h.setdefault(s.islem_pb, [Decimal("0"), Decimal("0")])
        t[0] += (1 if s.borc > 0 else -1) * s.islem_tutari
        t[1] += s.borc - s.alacak
    return h


def _yaz(out, baslik, h):
    out.write(f"  {baslik}:")
    for pb, (q, v) in sorted(h.items()):
        out.write(f"    {pb:<4} döviz {format_tr(q, 2):>16}   TL {format_tr(v, 2):>16}")


class Command(BaseCommand):
    help = "646/656 karşı hesaplı cari virman fişinin cari satırını döviz havuzuna (döviz 0, yalnız TL) bağlar. Varsayılan DRY-RUN."

    def add_arguments(self, parser):
        parser.add_argument("--fis", required=True, help="Fiş: YIL/NO (ör. 2026/2463)")
        parser.add_argument("--pb", default="", help="Döviz havuzu (USD/EUR/GBP); boş = döviz 0 / TL farkı kalan tek havuz")
        parser.add_argument("--uygula", action="store_true", help="Değişikliği YAZ (yoksa yalnız dry-run)")

    def handle(self, *args, fis, pb, uygula, **kw):
        try:
            yil, no = (int(x) for x in fis.split("/"))
        except ValueError:
            raise CommandError("--fis YIL/NO biçiminde olmalı (ör. 2026/2463).")
        f = YevmiyeFisi.objects.filter(yil=yil, fis_no=no, silindi=False).first()
        if f is None:
            raise CommandError(f"Fiş {fis} bulunamadı (ya da silinmiş).")
        if f.kaynak != YevmiyeFisi.Kaynak.CARI_VIRMAN:
            raise CommandError(f"Fiş {fis} bir cari virman fişi değil (kaynak {f.kaynak}).")
        cari = Cari.objects.filter(pk=f.cari_id).first()
        if cari is None or not cari.muhasebe_kodu:
            raise CommandError("Fişin carisi / cari muhasebe hesabı yok.")
        ana = list(f.satirlar.filter(silindi=False, ana_satir__isnull=True).order_by("id"))
        cari_s = [s for s in ana if s.hesap_id == cari.muhasebe_kodu]
        karsi = [s for s in ana if s.hesap_id != cari.muhasebe_kodu]
        if len(cari_s) != 1 or len(karsi) != 1 or f.karsi_cari_id:
            raise CommandError("Fiş yapısı beklenen gibi değil (tek cari satırı + tek karşı HESAP satırı olmalı).")
        s, k = cari_s[0], karsi[0]
        if not cv.kur_farki_hesabi_mi(k.hesap_id):
            raise CommandError(f"Karşı hesap {k.hesap_id} 646/656 değil; bu komut yalnız kur farkı virmanı içindir.")
        pb = (pb or "").strip().upper()
        if s.islem_pb != "TRY" and s.islem_tutari == 0 and pb in ("", s.islem_pb):     # idempotent: zaten bir döviz havuzuna (döviz 0) bağlı
            self.stdout.write(self.style.SUCCESS(f"Zaten {s.islem_pb} havuzuna (döviz 0, yalnız TL) bağlı (fiş {fis}, satır {s.pk}) — yapılacak bir şey yok."))
            return
        pb = pb or cv.varsayilan_kur_farki_pb(cari)
        if not pb:
            raise CommandError("Döviz havuzu belirlenemedi (döviz 0 / TL farkı kalan tek havuz yok); --pb ile verin.")
        havuz_once = _havuzlar(cari.muhasebe_kodu)
        if pb not in havuz_once or pb == "TRY":
            raise CommandError(f"{cari.unvan} hesabında {pb} döviz havuzu yok.")

        out = self.stdout
        out.write(f"FİŞ {f.yil}/{f.fis_no} (pk {f.pk}) {f.tarih:%d.%m.%Y} — {f.aciklama}")
        out.write(f"CARİ {cari.kod} {cari.unvan} · hesap {cari.muhasebe_kodu} · cari PB {cari.para_birimi}")
        for x in (s, k):
            out.write(f"  satır {x.pk}: {x.hesap_id:<14} B {format_tr(x.borc, 2):>12}  A {format_tr(x.alacak, 2):>12}  "
                      f"{x.islem_pb} {format_tr(x.islem_tutari, 2)} @ {x.islem_kuru}")
        if s.islem_pb == pb and s.islem_tutari == 0:
            out.write(self.style.SUCCESS(f"Zaten {pb} havuzuna (döviz 0, yalnız TL) bağlı — yapılacak bir şey yok."))
            return
        kur = cv._kur_farki_kuru(cari, pb, f.tarih)
        tl = s.borc - s.alacak
        havuz_sonra = {p: list(t) for p, t in havuz_once.items()}
        yon = 1 if s.borc > 0 else -1
        havuz_sonra[s.islem_pb][0] -= yon * s.islem_tutari
        havuz_sonra[s.islem_pb][1] -= tl
        havuz_sonra[pb][1] += tl                                  # döviz (q) değişmez, yalnız TL (v)
        out.write(f"DEĞİŞİKLİK (yalnız satır {s.pk}): işlem PB {s.islem_pb} {format_tr(s.islem_tutari, 2)} @ {s.islem_kuru}  →  "
                  f"{pb} 0,00 @ {kur} (yalnız TL; borç/alacak TL AYNI: B {format_tr(s.borc, 2)} / A {format_tr(s.alacak, 2)})")
        out.write("Fiş hesapları ve TL tutarları DEĞİŞMEZ → mizan aynı.")
        _yaz(out, "Havuzlar ÖNCE", havuz_once)
        _yaz(out, "Havuzlar SONRA", {p: tuple(t) for p, t in havuz_sonra.items()})
        if not uygula:
            out.write(self.style.WARNING("DRY-RUN: hiçbir şey yazılmadı. Uygulamak için --uygula ekleyin."))
            return
        with transaction.atomic():
            s = YevmiyeSatir.objects.select_for_update().get(pk=s.pk)
            fis_tl_once = sorted(f.satirlar.filter(silindi=False).values_list("hesap_id", "borc", "alacak"))
            s.islem_pb, s.islem_tutari, s.islem_kuru, s.ham_tl, s.ort_kur = pb, Decimal("0.00"), kur, None, None
            s.save(update_fields=["islem_pb", "islem_tutari", "islem_kuru", "ham_tl", "ort_kur", "updated_at"])
            uygun = kf.uygun_kume()
            ciftler = {c for c in {(cari.muhasebe_kodu, pb)} if uygun.uygun_mu(*c)}
            if ciftler:                                           # 120/320 gibi motor havuzuysa yeniden hesapla (136'da yok)
                kf.havuzlari_yeniden_hesapla(ciftler)
            fis_tl_sonra = sorted(f.satirlar.filter(silindi=False).values_list("hesap_id", "borc", "alacak"))
            if fis_tl_once != fis_tl_sonra:
                raise CommandError("Fişin TL satırları değişti; işlem geri alındı.")
        _yaz(out, "Havuzlar (yazıldıktan sonra)", _havuzlar(cari.muhasebe_kodu))
        out.write(self.style.SUCCESS(f"UYGULANDI: satır {s.pk} → {pb} havuzu (döviz 0, yalnız TL)."))
