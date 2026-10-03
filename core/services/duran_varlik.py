"""Duran Varlık kartı (DURAN VARLIK FAZ 2) servis katmanı.

253/254/255/260 hesaplarından birine bağlı somut bir sabit kıymetin basit kart kaydı —
ekle/listele/detay + 253/254/255/260 için kart-toplamı vs mizan-bakiyesi kontrol raporu.
Aktifleştirme (FAZ 3: kaynak=PROJE, M2M fatura kalemi ataması) burada henüz YOK.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from core.metin import buyuk_harf_tr
from core.models import DuranVarlik, FaturaSatir, YevmiyeSatir
from core.services.hesap_plani import duran_varlik_karti_hesaplari

SIFIR = Decimal("0.00")


class DuranVarlikHatasi(ValueError):
    """Duran varlık kart kuralı ihlali (Türkçe mesaj)."""


def sonraki_demirbas_kodu() -> str:
    n = 1
    while DuranVarlik.objects.filter(demirbas_kodu=f"DV-{str(n).zfill(4)}", silindi=False).exists():
        n += 1
    return f"DV-{str(n).zfill(4)}"


def varliklar(*, hesap_id=None, durum=None):
    qs = DuranVarlik.objects.filter(silindi=False).select_related("hesap").order_by("-created_at")
    if hesap_id:
        qs = qs.filter(hesap_id=hesap_id)
    if durum:
        qs = qs.filter(durum=durum)
    return qs


def duran_varlik_olustur(*, ad, hesap_id, aktiflestirme_tarihi, maliyet, marka_model="",
                         seri_no="", notlar="", fatura_satir_id=None, kullanici=None) -> DuranVarlik:
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise DuranVarlikHatasi("Ad boş olamaz.")
    if not duran_varlik_karti_hesaplari().filter(pk=hesap_id).exists():
        raise DuranVarlikHatasi(
            "Geçerli bir duran varlık hesabı seçin (253/254/255/260, yaprak hesap olmalı).")
    if maliyet is None or maliyet < 0:
        raise DuranVarlikHatasi("Maliyet negatif olamaz.")

    satir = None
    if fatura_satir_id:
        satir = FaturaSatir.objects.filter(
            pk=fatura_satir_id, silindi=False, fatura__silindi=False).first()
        if not satir:
            raise DuranVarlikHatasi("Belirtilen fatura kalemi bulunamadı.")

    dv = DuranVarlik.objects.create(
        demirbas_kodu=sonraki_demirbas_kodu(), ad=ad, hesap_id=hesap_id,
        aktiflestirme_tarihi=aktiflestirme_tarihi, maliyet=maliyet,
        marka_model=(marka_model or "").strip(), seri_no=(seri_no or "").strip(),
        notlar=(notlar or "").strip(),
        kaynak=DuranVarlik.Kaynak.FATURA if satir else DuranVarlik.Kaynak.ACILIS,
        created_by=kullanici, updated_by=kullanici)
    if satir:
        dv.fatura_satirlari.add(satir)
    return dv


def durum_degistir(varlik: DuranVarlik, *, durum, kullanici=None) -> DuranVarlik:
    if durum not in DuranVarlik.Durum.values:
        raise DuranVarlikHatasi("Geçersiz durum.")
    varlik.durum = durum
    varlik.updated_by = kullanici
    varlik.save(update_fields=["durum", "updated_by", "updated_at"])
    return varlik


def hesap_bazli_toplam(qs) -> list:
    """Verilen (filtrelenmiş) kayıt kümesinin hesap başına maliyet toplamı — liste
    ekranındaki özet için."""
    gruplar = {}
    for dv in qs:
        key = dv.hesap_id
        if key not in gruplar:
            gruplar[key] = {"hesap": dv.hesap, "toplam": SIFIR, "adet": 0}
        gruplar[key]["toplam"] += dv.maliyet
        gruplar[key]["adet"] += 1
    return sorted(gruplar.values(), key=lambda g: g["hesap"].hesap_kodu)


def _hesap_bakiyesi(hesap_kodu: str) -> Decimal:
    """Hesabın TÜM ZAMANLARDAKİ net borç bakiyesi (borç−alacak); mizanla birebir aynı
    mantık (bkz. core.services.raporlar.ekstre) ama tarih aralığı yok — kart-toplamı bir
    dönem değil, varlığın BUGÜNE kadarki durumuyla karşılaştırılır."""
    agg = YevmiyeSatir.objects.filter(
        hesap_id=hesap_kodu, silindi=False, fis__silindi=False
    ).aggregate(b=Sum("borc"), a=Sum("alacak"))
    return (agg["b"] or SIFIR) - (agg["a"] or SIFIR)


def kontrol_raporu() -> list:
    """253/254/255/260 yaprak hesaplarının her biri için kart-toplamı (silinmemiş
    DuranVarlik.maliyet toplamı) vs mizan-bakiyesi (yevmiyeden hesaplanan net borç) —
    salt okunur karşılaştırma; fark != 0 ise muhasebe ile kart kayıtları arasında
    tutarsızlık var demektir (örn. kart girilmeden fatura kesilmiş)."""
    satirlar = []
    for h in duran_varlik_karti_hesaplari():
        kart_toplami = (DuranVarlik.objects.filter(hesap_id=h.pk, silindi=False)
                        .aggregate(t=Sum("maliyet"))["t"] or SIFIR)
        mizan_bakiye = _hesap_bakiyesi(h.hesap_kodu)
        satirlar.append({
            "hesap": h, "kart_toplami": kart_toplami, "mizan_bakiye": mizan_bakiye,
            "fark": kart_toplami - mizan_bakiye,
        })
    return satirlar
