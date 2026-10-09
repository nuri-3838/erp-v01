"""FASON > Fason Dönüşler (belge): fasoncudan gelen irsaliye = aynı belgeye bağlı fason operasyon kayıtları.

Her satır (operasyon + adet) için ``OperasyonKaydi`` açılır (``fason_cari`` + ``fason_donus`` dolu). Onayda kayıtlar normal üretim akışıyla işlenir:
girdiler carinin FASON DEPOSUNDAN düşer, ana + yan çıktılar belgenin deposuna (DEPO-ÜRETİM) girer, her çıktıya kendi ``FasonFiyat`` bedeli eklenir
(bkz. core.services.fason_maliyet). Belgenin kayıtları tek atomik işlemde onaylanır: biri hata verirse (fiyat yok, yetersiz stok…) hiçbiri onaylanmaz."""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Prefetch
from django.utils import timezone

from core.models import Cari, Depo, Fatura, FasonDonus, Operasyon, OperasyonKaydi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import depo as depo_servis
from core.services import uretim as uretim_servis
from core.services.fason import FasonHatasi, gecerli_fiyat

SIFIR = Decimal("0")


def aktif_donusler():
    return (FasonDonus.objects.filter(silindi=False).select_related("cari", "depo")
            .prefetch_related(Prefetch("kayitlar", queryset=OperasyonKaydi.objects.filter(silindi=False)))
            .order_by("-yil", "-sira"))


def donus_kayitlari(donus):
    return (donus.kayitlar.filter(silindi=False)
            .select_related("operasyon__cikti", "operasyon__istasyon").prefetch_related("ciktilar__stok").order_by("sira", "pk"))


def donus_durumu(donus) -> str:
    """TASLAK (hiçbiri onaylı değil) / ONAYLI (hepsi onaylı). Onay tek atomik işlem olduğundan arada durum yoktur."""
    durumlar = {k.durum for k in donus.kayitlar.filter(silindi=False)}
    if durumlar == {OperasyonKaydi.Durum.ONAYLI}:
        return "ONAYLI"
    return "TASLAK"


def _sonraki_sira(yil):
    return (FasonDonus.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"] or 0) + 1


@transaction.atomic
def donus_olustur(*, cari_id, depo_id, tarih, satirlar, irsaliye_no="", aciklama="", onayla=False, kullanici=None) -> FasonDonus:
    """``satirlar``: [(operasyon_id, adet), ...] — adet = fasoncudan dönen ANA çıktı adedi (tam boy kuralı: tam boya yükseltilir). Her satır için
    fason operasyon kaydı (TASLAK) açılır; ``onayla=True`` ise belge hemen onaylanır."""
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FasonHatasi("Fasoncu (cari) bulunamadı.")
    if depo_servis.fason_deposu(cari) is None:
        raise FasonHatasi(f"{cari.unvan} için bağlı bir fason deposu yok (Depolar ekranında fasoncuyu bir depoya bağlayın).")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise FasonHatasi("Çıktıların gireceği depo bulunamadı.")
    if depo.fason_cari_id:
        raise FasonHatasi("Çıktılar fason deposuna değil, üretim/ana depoya girmelidir.")
    satirlar = [(oid, a) for oid, a in satirlar if oid]
    if not satirlar:
        raise FasonHatasi("En az bir satır (operasyon + adet) girin.")
    yil = tarih.year
    donus = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(yil)
                donus = FasonDonus.objects.create(
                    yil=yil, sira=sira, no=f"FD-{yil}-{sira:04d}", cari=cari, depo=depo, tarih=tarih,
                    irsaliye_no=(irsaliye_no or "").strip(), aciklama=(aciklama or "").strip(),
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if donus is None:
        raise FasonHatasi("Belge numarası üretilemedi; tekrar deneyin.")
    for i, (operasyon_id, adet) in enumerate(satirlar, start=1):
        try:
            adet = adet if hasattr(adet, "as_tuple") else parse_tr(adet)
        except SayiHatasi:
            raise FasonHatasi(f"{i}. satır: adet geçerli bir sayı olmalı.")
        if adet <= 0:
            raise FasonHatasi(f"{i}. satır: adet sıfırdan büyük olmalı.")
        try:
            uretim_servis.operasyon_kaydi_olustur(
                operasyon_id=operasyon_id, depo_id=depo.pk, tarih=tarih, hedef_cikti_miktari=adet,
                aciklama=f"Fason dönüş {donus.no}" + (f" · irsaliye {donus.irsaliye_no}" if donus.irsaliye_no else ""),
                kullanici=kullanici, fason_cari=cari, fason_donus=donus)
        except uretim_servis.UretimHatasi as e:
            raise FasonHatasi(f"{i}. satır: {e}")
    if onayla:
        donus_onayla(donus, kullanici=kullanici)
    return donus


@transaction.atomic
def donus_onayla(donus: FasonDonus, kullanici=None) -> FasonDonus:
    """Belgenin tüm taslak kayıtlarını onaylar (tek atomik işlem; biri hata verirse hiçbiri onaylanmaz)."""
    if donus.silindi:
        raise FasonHatasi("Silinmiş belge onaylanamaz.")
    kayitlar = list(donus_kayitlari(donus))
    if not kayitlar:
        raise FasonHatasi("Belgede kayıt yok.")
    for kayit in kayitlar:
        if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
            continue
        try:
            uretim_servis.operasyon_kaydi_onayla(kayit, kullanici=kullanici)
        except uretim_servis.UretimHatasi as e:
            raise FasonHatasi(f"{kayit.no} ({kayit.operasyon.cikti.kod}): {e}")
    return donus


@transaction.atomic
def donus_sil(donus: FasonDonus, kullanici=None) -> FasonDonus:
    """Yalnız TASLAK belge silinir (kayıtlarıyla birlikte). Onaylı belge silinemez."""
    if donus.silindi:
        return donus
    if donus_durumu(donus) == "ONAYLI":
        raise FasonHatasi("Onaylı fason dönüş silinemez.")
    for kayit in donus_kayitlari(donus):
        uretim_servis.operasyon_kaydi_sil(kayit, kullanici=kullanici)
    donus.silindi = True
    donus.silindi_at = timezone.now()
    donus.updated_by = kullanici
    donus.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return donus


def donus_bilgisi(donus: FasonDonus) -> dict:
    """Belge detayı için: kayıtlar (çıktı satırlarıyla), durum ve fason bedeli.
    ONAYLI → snapshot toplamı (TL/USD); TASLAK → bugünkü geçerli fiyatla TAHMİN (fiyatı olmayan çıktılar ``eksikler``de)."""
    kayitlar = list(donus_kayitlari(donus))
    durum = donus_durumu(donus)
    satirlar, eksikler = [], []
    toplam_try = toplam_usd = SIFIR
    for k in kayitlar:
        ciktilar = []
        if k.durum == OperasyonKaydi.Durum.ONAYLI:
            for c in sorted(k.ciktilar.all(), key=lambda x: (not x.ana_mi, x.pk)):
                ciktilar.append({"stok": c.stok, "miktar": c.miktar, "birim_fiyat": c.fason_birim_fiyat, "pb": c.fason_para_birimi,
                                 "tutar_try": c.fason_tutar, "tutar_usd": c.fason_tutar_usd})
                toplam_try += c.fason_tutar or SIFIR
                toplam_usd += c.fason_tutar_usd or SIFIR
        else:
            op = k.operasyon
            calistirma = k.hedef_cikti_miktari / op.cikti_miktar
            adaylar = [(op.cikti, k.hedef_cikti_miktari)] + [
                (y.stok, y.miktar * calistirma) for y in uretim_servis.operasyon_yan_ciktilari(op)]
            for stok, miktar in adaylar:
                f = gecerli_fiyat(donus.cari_id, stok, k.tarih)
                if f is None:
                    eksikler.append(stok)
                ciktilar.append({"stok": stok, "miktar": miktar, "birim_fiyat": f.birim_fiyat if f else None,
                                 "pb": f.para_birimi if f else "", "tutar_try": None, "tutar_usd": None})
        satirlar.append({"kayit": k, "ciktilar": ciktilar})
    return {"satirlar": satirlar, "durum": durum, "toplam_try": toplam_try, "toplam_usd": toplam_usd, "eksikler": eksikler}


# === Fasoncunun faturası ====================================================================================
# Fason hizmet faturası normal bir ALIŞ faturasıdır (kalemi, kategorisi 151 yarı mamul alt hesabına eşli bir hizmet kartı; depo YOK → stok
# hareketi yok): fatura 151'e borç yazar, fason bedeli de stok değerinde olduğundan fatura gelince mizan = stok değeri olur. Fatura ONAYLI olana
# kadar bedel 'tahmini'dir. Beklenen tutar (Σ fason_tutar) ile fatura tutarı (ara toplam × kur, TL) farklıysa uyarı verilir; maliyet ancak
# AÇIK işlemle ("faturaya göre güncelle") fatura tutarına çekilir — sessiz düzeltme yoktur.

FARK_ESIGI = Decimal("0.01")


def yeniden_senkronla(donuslar):
    """Dönüşlerin ONAYLI kayıtlarının çıktı maliyetini (fason bedeli + tahmini durumu) yeniden yazar ve değişen çıktı kartlarının ortalama
    maliyetini baştan hesaplar (``stok_ortalama.yeniden_hesapla``). Fatura bağlanınca/onaylanınca/silinince/güncellenince çağrılır."""
    from core.services import stok_fis, stok_ortalama
    for donus in donuslar:
        for kayit in donus_kayitlari(donus):
            if kayit.durum != OperasyonKaydi.Durum.ONAYLI:
                continue
            for stok in stok_fis.uretim_senkronla(kayit):
                stok_ortalama.yeniden_hesapla(stok)


def fatura_degisti(fatura):
    """Fatura ONAYLANDI / GÜNCELLENDİ: ona bağlı dönüşlerin maliyeti yeniden senkronlanır."""
    yeniden_senkronla(list(FasonDonus.objects.filter(fatura=fatura, silindi=False)))


def faturaya_bagla(donus: FasonDonus, fatura: Fatura, kullanici=None) -> FasonDonus:
    if donus.silindi or fatura.silindi:
        raise FasonHatasi("Silinmiş belge/fatura bağlanamaz.")
    if donus_durumu(donus) != "ONAYLI":
        raise FasonHatasi("Yalnız onaylı fason dönüş faturaya bağlanabilir.")
    if fatura.yon != "ALIS":
        raise FasonHatasi("Fason faturası bir ALIŞ faturası olmalı.")
    if fatura.cari_id != donus.cari_id:
        raise FasonHatasi("Fatura, dönüşün fasoncusuna (cari) ait olmalı.")
    with transaction.atomic():
        donus.fatura = fatura
        donus.updated_by = kullanici
        donus.save(update_fields=["fatura", "updated_by", "updated_at"])
        yeniden_senkronla([donus])
    return donus


def faturadan_kopar(donus: FasonDonus, kullanici=None) -> FasonDonus:
    if not donus.fatura_id:
        return donus
    with transaction.atomic():
        donus.fatura = None
        donus.updated_by = kullanici
        donus.save(update_fields=["fatura", "updated_by", "updated_at"])
        yeniden_senkronla([donus])
    return donus


def fatura_tutari_try(fatura: Fatura) -> Decimal:
    """Faturanın KDV hariç ara toplamı, TL (fatura para birimi × fatura kuru)."""
    return yuvarla(fatura.ara_toplam * (fatura.kur or Decimal("1")), 2)


def fatura_karsilastirma(fatura: Fatura) -> dict:
    """Faturaya bağlı dönüşlerin beklenen fason bedeli (Σ onaylı çıktı fason_tutar) ile fatura tutarı. ``fark`` = fatura − beklenen."""
    donusler = list(FasonDonus.objects.filter(fatura=fatura, silindi=False).select_related("cari").order_by("yil", "sira"))
    beklenen_try = beklenen_usd = SIFIR
    for d in donusler:
        for k in donus_kayitlari(d):
            if k.durum != OperasyonKaydi.Durum.ONAYLI:
                continue
            for c in k.ciktilar.all():
                beklenen_try += c.fason_tutar or SIFIR
                beklenen_usd += c.fason_tutar_usd or SIFIR
    ft = fatura_tutari_try(fatura)
    fark = ft - beklenen_try
    return {"donusler": donusler, "beklenen_try": beklenen_try, "beklenen_usd": beklenen_usd, "fatura_try": ft, "fark": fark,
            "fark_var": bool(donusler) and abs(fark) >= FARK_ESIGI, "onayli": fatura.durum == Fatura.Durum.ONAYLI}


@transaction.atomic
def faturaya_gore_guncelle(fatura: Fatura, kullanici=None) -> dict:
    """AÇIK işlem: bağlı dönüşlerin fason bedelleri fatura tutarına ORANTILI çekilir (her çıktı: eski × fatura/beklenen, yuvarlama farkı en büyük
    satıra; USD aynı oranla), ardından çıktı maliyetleri yeniden hesaplanır (ortalama dahil). Yalnız ONAYLI faturada."""
    if fatura.durum != Fatura.Durum.ONAYLI or fatura.silindi:
        raise FasonHatasi("Maliyet yalnız ONAYLI faturanın tutarına göre güncellenebilir.")
    kar = fatura_karsilastirma(fatura)
    if not kar["donusler"]:
        raise FasonHatasi("Bu faturaya bağlı fason dönüş yok.")
    if kar["beklenen_try"] <= 0:
        raise FasonHatasi("Beklenen fason bedeli sıfır; oranlanamaz.")
    ft = kar["fatura_try"]
    if ft <= 0:
        raise FasonHatasi("Fatura tutarı sıfır; maliyet güncellenemez.")
    if not kar["fark_var"]:
        return {**kar, "guncellendi": False}
    oran = ft / kar["beklenen_try"]
    satirlar = []
    for d in kar["donusler"]:
        for k in donus_kayitlari(d):
            if k.durum == OperasyonKaydi.Durum.ONAYLI:
                satirlar.extend(c for c in k.ciktilar.all() if (c.fason_tutar or SIFIR) > 0)
    yeni = {c.pk: yuvarla(c.fason_tutar * oran, 2) for c in satirlar}
    fark_try = ft - sum(yeni.values(), SIFIR)
    en_buyuk = max(satirlar, key=lambda c: c.fason_tutar)
    yeni[en_buyuk.pk] += fark_try                              # toplam fatura tutarına BİREBİR eşit
    hedef_usd = yuvarla(kar["beklenen_usd"] * oran, 2)
    yeni_usd = {c.pk: yuvarla((c.fason_tutar_usd or SIFIR) * oran, 2) for c in satirlar}
    yeni_usd[en_buyuk.pk] += hedef_usd - sum(yeni_usd.values(), SIFIR)
    for c in satirlar:
        c.fason_tutar, c.fason_tutar_usd, c.updated_by = yeni[c.pk], yeni_usd[c.pk], kullanici
        c.save(update_fields=["fason_tutar", "fason_tutar_usd", "updated_by", "updated_at"])
    yeniden_senkronla(kar["donusler"])
    return {**fatura_karsilastirma(fatura), "guncellendi": True}
