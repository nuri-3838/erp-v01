"""FASON > Fason Dönüşler (belge): fasoncudan gelen irsaliye = aynı belgeye bağlı fason operasyon kayıtları.

Her satır (operasyon + adet) için ``OperasyonKaydi`` açılır (``fason_cari`` + ``fason_donus`` dolu). Onayda kayıtlar normal üretim akışıyla işlenir:
girdiler carinin FASON DEPOSUNDAN düşer, ana + yan çıktılar belgenin deposuna (DEPO-ÜRETİM) girer, her çıktıya kendi ``FasonFiyat`` bedeli eklenir
(bkz. core.services.fason_maliyet). Belgenin kayıtları tek atomik işlemde onaylanır: biri hata verirse (fiyat yok, yetersiz stok…) hiçbiri onaylanmaz."""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Prefetch
from django.utils import timezone

from core.models import Cari, Depo, FasonDonus, Operasyon, OperasyonKaydi
from core.sayi import SayiHatasi, parse_tr
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
