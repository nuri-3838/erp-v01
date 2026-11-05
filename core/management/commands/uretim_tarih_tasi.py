"""Tek seferlik: ONAYLI üretim operasyon kayıtlarının tarihini (yanlış güne atılmış "Tümünü üret") düzeltir.

Mevcut kayıtlar YERİNDE düzeltilir — geri alıp yeniden üretme, manuel/mahsup fişi YOK; kayıt id'leri, kayıt no'ları, fiş no'ları aynen kalır:
  1. ``OperasyonKaydi.tarih``                                        -> yeni tarih
  2. kayıtların stok hareketleri (girdi çıkışı + ana/yan çıktı girişi) -> yeni tarih
  3. kayıtların maliyet aktarım fişleri (tarih + USD kuru)            -> yeni tarih / yeni tarihin USD kuru (fiş no ve yıl değişmez)
  4. etkilenen kartlar ``stok_ortalama.yeniden_hesapla`` ile baştan hesaplanır (hareketli ağırlıklı ortalama tarih sırasına bağlıdır);
     değeri değişen çıkışların fişleri/zincirleme etkileri mevcut motorun kendi yoluyla (stok_fis.*_senkronla) yenilenir.

Varsayılan DRY-RUN: işlemin TAMAMI bir transaction içinde gerçekten yapılır, önce/sonra raporu alınır ve sonunda GERİ ALINIR (hiçbir şey kalıcı
yazılmaz). ``--uygula`` aynı işlemi kalıcı yapar. Her iki modda da doğrulama tutmazsa (yeni negatif bakiye, ikinci hesapta değişiklik, kapsam dışı
kayıt) hata verir ve her şey geri alınır.

    python manage.py uretim_tarih_tasi --uretim-emri 51 --kayit-bas 1220 --kayit-son 1265 --eski-tarih 2026-11-04 --tarih 2026-11-02 [--irsaliye 116] [--uygula]
"""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import (Depo, IsOturumu, OperasyonKaydi, Stok, StokHareket, StokVaryant, TeklifSiparis, UretimEmri, YevmiyeFisi)
from core.services import hareket as hareket_servis
from core.services import stok_ortalama
from core.services.yevmiye import kur_usd_birebir

SIFIR = Decimal("0")


def _snapshot():
    """Önce/sonra karşılaştırması için tüm fişler, kartlar ve hareket tutarları."""
    fisler = {f.pk: (f.yil, f.fis_no, f.tarih, f.kur_usd, f.tb or SIFIR, f.kaynak, f.silindi)
              for f in YevmiyeFisi.objects.annotate(tb=Sum("satirlar__borc", filter=Q(satirlar__silindi=False)))}
    kartlar = {s.pk: (s.kod, s.ort_maliyet_try, s.maliyet_deger_try, s.maliyet_miktar)
               for s in Stok.objects.filter(silindi=False)}
    hareketler = {h[0]: h[1:] for h in StokHareket.objects.filter(silindi=False).values_list("pk", "tarih", "tutar_try", "giris_tutar_try", "maliyet_durumu")}
    return {"fisler": fisler, "kartlar": kartlar, "hareketler": hareketler}


def _bakiyeler(as_of):
    """({(stok, depo, varyant): as_of gün sonu bakiyesi}, {(stok, depo, varyant, tarih): gün sonu bakiyesi — as_of ve sonrası, <0 olanlar})."""
    satirlar = defaultdict(list)
    for stok_id, depo_id, varyant_id, tarih, tur, miktar in (StokHareket.objects.filter(silindi=False)
                                                           .values_list("stok_id", "depo_id", "varyant_id", "tarih", "tur", "miktar")):
        satirlar[(stok_id, depo_id, varyant_id)].append((tarih, miktar if tur == StokHareket.Tur.GIRIS else -miktar))
    as_of_bakiye, negatif_gunler = {}, {}
    for anahtar, liste in satirlar.items():
        gunluk = defaultdict(lambda: SIFIR)
        for tarih, m in liste:
            gunluk[tarih] += m
        kum, as_of_deger = SIFIR, SIFIR
        for tarih in sorted(gunluk):
            kum += gunluk[tarih]
            if tarih <= as_of:
                as_of_deger = kum
            elif kum < 0:
                negatif_gunler[anahtar + (tarih,)] = kum
            if tarih == as_of and kum < 0:
                negatif_gunler[anahtar + (tarih,)] = kum
        as_of_bakiye[anahtar] = as_of_deger
    return as_of_bakiye, negatif_gunler


def _etiket(anahtar, kodlar, depolar, varyantlar):
    stok_id, depo_id, varyant_id = anahtar[:3]
    v = f" [{varyantlar[varyant_id]}]" if varyant_id else ""
    return f"{kodlar.get(stok_id, stok_id)}{v} (depo {depolar.get(depo_id, depo_id)})"


def _irsaliye_kontrol(ts):
    """Satış irsaliyesinin her kalemi için onaydaki tarih bazlı kontrol (hareket.cikis_kontrol, irsaliye deposu/tarihi/kalem varyantı).
    [(kod, gerekli, eldeki, tamam_mi, mesaj)] — HİÇBİR hareket yazmaz."""
    sonuc = []
    for k in ts.kalemler.filter(silindi=False).select_related("stok__kategori").order_by("pk"):
        if k.stok.kategori.hizmet_kategorisi:
            continue
        gerekli = (k.miktar / (k.stok.cevirici or Decimal("1"))).quantize(Decimal("0.001"))
        varyant = hareket_servis._varyant_coz(k.stok, k.varyant_id)
        eldeki = hareket_servis.eldeki_miktar(k.stok, ts.depo, ts.tarih, varyant=varyant)
        try:
            hareket_servis.cikis_kontrol(k.stok, ts.depo, ts.tarih, gerekli, varyant=varyant)
            sonuc.append((k.stok.kod, gerekli, eldeki, True, ""))
        except hareket_servis.HareketHatasi as e:
            sonuc.append((k.stok.kod, gerekli, eldeki, False, str(e)))
    return sonuc


class Command(BaseCommand):
    help = "Onayli uretim operasyon kayitlarinin tarihini yerinde duzeltir (varsayilan dry-run; --uygula)."

    def add_arguments(self, parser):
        parser.add_argument("--uretim-emri", type=int, default=None, help="Verilirse kayitlarin bu uretim emrine ait oldugu dogrulanir (emirsiz kayitlarda bos birak).")
        parser.add_argument("--kayit-bas", type=int, required=True)
        parser.add_argument("--kayit-son", type=int, required=True)
        parser.add_argument("--eski-tarih", required=True, help="Kayitlarin su anki (yanlis) tarihi, YYYY-AA-GG.")
        parser.add_argument("--tarih", required=True, help="Yeni tarih, YYYY-AA-GG.")
        parser.add_argument("--haric", default="", help="Aralıktan çıkarılacak kayıt id'leri (virgüllü) — senaryo analizi.")
        parser.add_argument("--ek", default="", help="Aralık dışından eklenecek kayıt id'leri (virgüllü; başka ÜS olabilir) — senaryo analizi.")
        parser.add_argument("--irsaliye", type=int, default=None, help="Kontrol edilecek satis irsaliyesi (teklif_siparis id); onaylanmaz.")
        parser.add_argument("--uygula", action="store_true", help="Kalici uygula.")

    def handle(self, *args, **o):
        try:
            eski, yeni = datetime.date.fromisoformat(o["eski_tarih"]), datetime.date.fromisoformat(o["tarih"])
        except ValueError as e:
            raise CommandError(str(e))
        haric = {int(x) for x in o["haric"].split(",") if x.strip()}
        ek = {int(x) for x in o["ek"].split(",") if x.strip()}
        kayitlar = list(OperasyonKaydi.objects.filter(Q(pk__range=(o["kayit_bas"], o["kayit_son"])) | Q(pk__in=ek), silindi=False)
                        .exclude(pk__in=haric).select_related("fis", "operasyon").order_by("pk"))
        ids = [k.pk for k in kayitlar]
        sorunlar = []
        if not kayitlar:
            raise CommandError("Kapsamda kayit yok.")
        emir = UretimEmri.objects.get(pk=o["uretim_emri"]) if o["uretim_emri"] else None
        for k in kayitlar:
            if emir is not None and k.uretim_emri_id != emir.pk and k.pk not in ek:
                sorunlar.append(f"{k.no}: uretim emri {emir.no} degil")
            if k.durum != OperasyonKaydi.Durum.ONAYLI:
                sorunlar.append(f"{k.no}: onayli degil")
            if k.tarih != eski:
                sorunlar.append(f"{k.no}: tarihi {k.tarih:%d.%m.%Y} (beklenen {eski:%d.%m.%Y})")
            if k.fason_cari_id or k.fason_donus_id or k.karma_id:
                sorunlar.append(f"{k.no}: fason/karma kayit (kapsam disi)")
            if k.fis_id and (k.fis.silindi or k.fis.kaynak != YevmiyeFisi.Kaynak.URETIM or k.fis.tarih != eski or k.fis.yil != yeni.year):
                sorunlar.append(f"{k.no}: fis beklenen durumda degil (kaynak/tarih/yil)")
        fis_idler = [k.fis_id for k in kayitlar if k.fis_id]
        if len(fis_idler) != len(set(fis_idler)):
            sorunlar.append("birden fazla kayit ayni fisi paylasiyor")
        for f in YevmiyeFisi.objects.filter(pk__in=fis_idler):
            if f.operasyon_kayitlari.exclude(pk__in=ids).exists():
                sorunlar.append(f"fis {f.yil}/{f.fis_no}: kapsam disi kayit da bagli")
        hareketler = StokHareket.objects.filter(operasyon_kaydi_id__in=ids, silindi=False)
        if hareketler.filter(kaynak=StokHareket.Kaynak.DUZELTME).exists():
            sorunlar.append("kayitlarda recete duzeltme hareketi var (kapsam disi)")
        if hareketler.exclude(tarih=eski).exists():
            sorunlar.append("bazi hareketlerin tarihi kayit tarihinden farkli")
        if StokHareket.objects.filter(operasyon_kaydi_girdi__kayit_id__in=ids, silindi=False, operasyon_kaydi__isnull=True).exists():
            sorunlar.append("operasyon_kaydi bagi olmayan girdi hareketi var")
        if kur_usd_birebir(yeni) is None:
            sorunlar.append(f"{yeni:%d.%m.%Y} icin USD kuru yok")
        if sorunlar:
            raise CommandError("Uygulanamaz:\n  " + "\n  ".join(sorunlar))
        ts = TeklifSiparis.objects.select_related("depo").get(pk=o["irsaliye"]) if o["irsaliye"] else None

        kodlar = dict(Stok.objects.values_list("pk", "kod"))
        depolar = dict(Depo.objects.values_list("pk", "kod"))
        varyantlar = {v.pk: v.ad if hasattr(v, "ad") else str(v) for v in StokVaryant.objects.all()}
        try:
            from core.services.varyant import varyant_kisa
            varyantlar = {v.pk: varyant_kisa(v) for v in StokVaryant.objects.all()}
        except Exception:                                                     # etiket yalnız raporlama içindir
            pass
        etkilenen = sorted({h.stok_id for h in hareketler})
        once = _snapshot()
        once_as_of, once_neg = _bakiyeler(yeni)
        once_irs = _irsaliye_kontrol(ts) if ts else []
        say = {"kayit": len(kayitlar), "hareket": hareketler.count(), "fis": len(fis_idler), "kart": len(etkilenen),
               "fissiz_kayit": sum(1 for k in kayitlar if not k.fis_id),
               "is_oturumu": IsOturumu.objects.filter(kayit_id__in=ids).count()}

        sp = transaction.atomic()
        sp.__enter__()
        try:
            simdi = timezone.now()
            kur_yeni = kur_usd_birebir(yeni)
            OperasyonKaydi.objects.filter(pk__in=ids).update(tarih=yeni, updated_at=simdi)
            StokHareket.objects.filter(operasyon_kaydi_id__in=ids, silindi=False).update(tarih=yeni, updated_at=simdi)
            YevmiyeFisi.objects.filter(pk__in=fis_idler).update(tarih=yeni, kur_usd=kur_yeni, updated_at=simdi)
            # Kartları baştan hesapla; değeri değişen çıkışlar fişleri/zincirleme etkileri kendi motoruyla yeniler. Sabit noktaya kadar tekrar.
            degisen_toplam = []
            for tur in range(6):
                sonuc = stok_ortalama.tum_kartlari_yeniden_hesapla(etkilenen)
                degisen_toplam.append(sum(sonuc.values()))
                if tur > 0 and sum(sonuc.values()) == 0:
                    break
            else:
                raise CommandError(f"Maliyet hesabi sabit noktaya ulasmadi: {degisen_toplam}")
            sonra = _snapshot()
            sonra_as_of, sonra_neg = _bakiyeler(yeni)
            sonra_irs = _irsaliye_kontrol(ts) if ts else []
            yeni_negatifler = {a: b for a, b in sonra_neg.items() if a not in once_neg}
            negatif_asof = {a: b for a, b in sonra_as_of.items() if b < 0}
            yeni_negatif_asof = {a: b for a, b in negatif_asof.items() if once_as_of.get(a, SIFIR) >= 0}
            self._rapor(say, o, eski, yeni, once, sonra, once_as_of, sonra_as_of, once_neg, sonra_neg, yeni_negatifler, negatif_asof, yeni_negatif_asof,
                        once_irs, sonra_irs, kodlar, depolar, varyantlar, fis_idler, ids, degisen_toplam, kayitlar)
            if yeni_negatifler or yeni_negatif_asof:
                raise CommandError("Yeni negatif bakiye olusuyor; geri alindi (rapor yukarida).")
        except BaseException:
            sp.__exit__(*__import__("sys").exc_info())
            raise
        if o["uygula"]:
            sp.__exit__(None, None, None)
            self.stdout.write(self.style.SUCCESS("UYGULANDI (kalici)."))
        else:
            transaction.set_rollback(True)
            sp.__exit__(None, None, None)
            self.stdout.write(self.style.WARNING("DRY-RUN: hicbir sey yazilmadi (geri alindi)."))

    # --- rapor ---
    def _rapor(self, say, o, eski, yeni, once, sonra, once_as_of, sonra_as_of, once_neg, sonra_neg, yeni_negatifler, negatif_asof, yeni_negatif_asof,
               once_irs, sonra_irs, kodlar, depolar, varyantlar, fis_idler, ids, degisen_toplam, kayitlar):
        w = self.stdout.write
        w(f"=== KAPSAM === ÜS id {o['uretim_emri']} | kayıt {len(kayitlar)} ({kayitlar[0].no} … {kayitlar[-1].no}) | {eski:%d.%m.%Y} -> {yeni:%d.%m.%Y}")
        w(f"değişen: kayıt {say['kayit']}, stok hareketi {say['hareket']}, maliyet fişi {say['fis']} (fişsiz kayıt {say['fissiz_kayit']}), etkilenen kart {say['kart']}, iş oturumu bağlı {say['is_oturumu']}")
        w(f"maliyet yeniden hesap geçişleri (değişen çıkış sayısı/geçiş): {degisen_toplam}")
        # fiş farkları
        bizim = set(fis_idler)
        tarih_degisen = [f for f in bizim if sonra["fisler"][f][2] != once["fisler"][f][2]]
        w(f"\n=== FİŞLER === bizim fişlerde tarih değişen: {len(tarih_degisen)}/{len(bizim)} | fiş no/yıl değişen: "
          f"{sum(1 for f in bizim if sonra['fisler'][f][:2] != once['fisler'][f][:2])} | USD kuru {next(iter(once['fisler'][f][3] for f in bizim)) if bizim else '-'} -> {sonra['fisler'][next(iter(bizim))][3] if bizim else '-'}")
        farklar = []
        for pk, (yil, no, tarih, kur, tb, kaynak, silindi) in sonra["fisler"].items():
            e = once["fisler"].get(pk)
            if e is None:
                farklar.append((f"{yil}/{no}", kaynak, "YENİ FİŞ", SIFIR, tb, tarih))
            elif e[4] != tb or e[6] != silindi:
                farklar.append((f"{yil}/{no}", kaynak, "bizim" if pk in bizim else "DİĞER", e[4], tb, tarih))
        yeni_fis_sayisi = len(sonra["fisler"]) - len(once["fisler"])
        w(f"tutarı değişen/yeni fiş: {len(farklar)} | toplam fiş sayısı {len(once['fisler'])} -> {len(sonra['fisler'])} ({yeni_fis_sayisi:+d})")
        toplam_once = sum((e[4] for pk, e in once["fisler"].items() if pk in bizim), SIFIR)
        toplam_sonra = sum((sonra["fisler"][pk][4] for pk in bizim), SIFIR)
        w(f"bizim {len(bizim)} fişin toplam borcu: {toplam_once} -> {toplam_sonra} (fark {toplam_sonra - toplam_once})")
        for no, kaynak, grup, e, s, tarih in sorted(farklar)[:80]:
            w(f"  fiş {no} [{kaynak}] {grup}: {e} -> {s} (fark {s - e}) tarih {tarih:%d.%m.%Y}")
        if not farklar:
            w("  (tutarı değişen fiş YOK)")
        # kart / hareket maliyet farkları
        kart_farklari = [(k, once["kartlar"][pk], sonra["kartlar"][pk]) for pk, k in ((pk, v[0]) for pk, v in sonra["kartlar"].items())
                         if pk in once["kartlar"] and (once["kartlar"][pk][1:] != sonra["kartlar"][pk][1:])]
        w(f"\n=== KART MALİYETİ === ort. maliyet/değer/miktar değişen kart: {len(kart_farklari)}")
        for kod, e, s in sorted(kart_farklari)[:60]:
            w(f"  {kod}: ort {e[1]} -> {s[1]} | değer {e[2]} -> {s[2]} | miktar {e[3]} -> {s[3]}")
        hareket_tutar = sum(1 for pk, s in sonra["hareketler"].items() if pk in once["hareketler"] and once["hareketler"][pk][1] != s[1])
        w(f"tutar_try değişen stok hareketi: {hareket_tutar} (hareket tarihi değişen: {sum(1 for pk, s in sonra['hareketler'].items() if pk in once['hareketler'] and once['hareketler'][pk][0] != s[0])})")
        # negatif bakiye
        w(f"\n=== NEGATİF BAKİYE === {yeni:%d.%m.%Y} gün sonu itibarıyla önce negatif: {sum(1 for b in once_as_of.values() if b < 0)}, sonra negatif: {len(negatif_asof)}, YENİ: {len(yeni_negatif_asof)}")
        for a, b in sorted(negatif_asof.items(), key=lambda x: str(x[0]))[:40]:
            w(f"  {_etiket(a, kodlar, depolar, varyantlar)}: {b}  ({'YENİ' if a in yeni_negatif_asof else 'önceden de vardı: ' + str(once_as_of.get(a))})")
        w(f"{yeni:%d.%m.%Y} SONRASI günlerde negatife düşen (önce yok, sonra var): {len(yeni_negatifler)}")
        for a, b in sorted(yeni_negatifler.items(), key=lambda x: str(x[0]))[:40]:
            w(f"  {_etiket(a, kodlar, depolar, varyantlar)} {a[3]:%d.%m.%Y}: {b}")
        depo_negatif = {d: sum(1 for a, b in sonra_as_of.items() if b < 0 and depolar.get(a[1]) == d) for d in ("150", "999")}
        w(f"depo bazında {yeni:%d.%m.%Y} negatif kart sayısı (sonra): {depo_negatif}")
        # irsaliye
        if sonra_irs:
            w(f"\n=== İRSALİYE KONTROLÜ ({yeni:%d.%m.%Y}, onay kontrolü simülasyonu — onaylanmadı) ===")
            for (kod, g, e1, ok1, m1), (_k, _g, e2, ok2, m2) in zip(once_irs, sonra_irs):
                w(f"  {kod}: gerekli {g} | eldeki {e1} -> {e2} | {'TAMAM' if ok2 else 'YETERSİZ'} (önce: {'tamam' if ok1 else 'yetersiz'}){' | ' + m2 if m2 else ''}")
            w(f"  SONUÇ: onaylanabilir = {all(r[3] for r in sonra_irs)} (önce: {all(r[3] for r in once_irs)}); toplam miktar {sum(r[1] for r in sonra_irs)}")
