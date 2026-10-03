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
                         seri_no="", notlar="", fatura_satirlari=None, kullanici=None) -> DuranVarlik:
    """``fatura_satirlari``: FaturaSatir pk veya instance listesi (opsiyonel, birden fazla
    olabilir) — hepsi ``hesap_id`` ile aynı hesaba işlenmiş ve henüz başka bir karta bağlı
    olmamalı (bkz. core.services.duran_varlik._dogrula_satirlar)."""
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise DuranVarlikHatasi("Ad boş olamaz.")
    if not duran_varlik_karti_hesaplari().filter(pk=hesap_id).exists():
        raise DuranVarlikHatasi(
            "Geçerli bir duran varlık hesabı seçin (253/254/255/260, yaprak hesap olmalı).")
    if maliyet is None or maliyet < 0:
        raise DuranVarlikHatasi("Maliyet negatif olamaz.")

    satirlar = _dogrula_satirlar(fatura_satirlari, hesap_id)

    dv = DuranVarlik.objects.create(
        demirbas_kodu=sonraki_demirbas_kodu(), ad=ad, hesap_id=hesap_id,
        aktiflestirme_tarihi=aktiflestirme_tarihi, maliyet=maliyet,
        marka_model=(marka_model or "").strip(), seri_no=(seri_no or "").strip(),
        notlar=(notlar or "").strip(),
        kaynak=DuranVarlik.Kaynak.FATURA if satirlar else DuranVarlik.Kaynak.ACILIS,
        created_by=kullanici, updated_by=kullanici)
    if satirlar:
        dv.fatura_satirlari.set(satirlar)
    return dv


def _dogrula_satirlar(fatura_satirlari, hesap_id, *, haric_varlik_pk=None):
    """pk/instance listesini FaturaSatir'lere çözer; hepsinin var olduğunu, ``hesap_id``
    ile aynı hesaba işlendiğini ve (``haric_varlik_pk`` dışında) başka bir karta bağlı
    olmadığını doğrular."""
    if not fatura_satirlari:
        return []
    ids = [s.pk if hasattr(s, "pk") else s for s in fatura_satirlari]
    satirlar = list(FaturaSatir.objects.filter(
        pk__in=ids, silindi=False, fatura__silindi=False))
    if len(satirlar) != len(set(ids)):
        raise DuranVarlikHatasi("Belirtilen fatura kalemlerinden biri bulunamadı.")
    for s in satirlar:
        if s.hesap_id != hesap_id:
            raise DuranVarlikHatasi(
                f"Satır {s.pk}: kartın hesabıyla aynı hesaba işlenmiş olmalı.")
        bagli = s.duran_varliklar.filter(silindi=False)
        if haric_varlik_pk:
            bagli = bagli.exclude(pk=haric_varlik_pk)
        if bagli.exists():
            raise DuranVarlikHatasi(f"Satır {s.pk}: zaten başka bir duran varlık kartına bağlı.")
    return satirlar


def baglanabilir_satirlar(varlik: DuranVarlik):
    """Kartın hesabıyla aynı hesaba işlenmiş ve henüz HİÇBİR karta bağlı olmayan fatura
    kalemleri — "Fatura kalemi bağla" seçiminde listelenir."""
    return (FaturaSatir.objects.filter(hesap_id=varlik.hesap_id, silindi=False,
                                       fatura__silindi=False)
            .exclude(duran_varliklar__silindi=False)
            .select_related("fatura", "fatura__cari").order_by("fatura__tarih", "id"))


def satir_bagla(varlik: DuranVarlik, satir_id, *, kullanici=None) -> DuranVarlik:
    satirlar = _dogrula_satirlar([satir_id], varlik.hesap_id, haric_varlik_pk=varlik.pk)
    varlik.fatura_satirlari.add(*satirlar)
    varlik.updated_by = kullanici
    varlik.save(update_fields=["updated_by", "updated_at"])
    return varlik


def satir_cikar(varlik: DuranVarlik, satir_id, *, kullanici=None) -> DuranVarlik:
    satir = varlik.fatura_satirlari.filter(pk=satir_id).first()
    if not satir:
        raise DuranVarlikHatasi("Bu kalem karta bağlı değil.")
    varlik.fatura_satirlari.remove(satir)
    varlik.updated_by = kullanici
    varlik.save(update_fields=["updated_by", "updated_at"])
    return varlik


def baglanti_toplami(varlik: DuranVarlik) -> Decimal:
    toplam = SIFIR
    for s in varlik.fatura_satirlari.filter(silindi=False, fatura__silindi=False):
        toplam += s.tutar
    return toplam


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
    """253/254/255/260 yaprak hesaplarının her biri için kart-toplamı (yalnız AKTİF —
    PASİF kartlar artık defter değerini temsil etmediği varsayılır, hariç tutulur —
    silinmemiş DuranVarlik.maliyet toplamı) vs mizan-bakiyesi (yevmiyeden hesaplanan
    net borç) — salt okunur karşılaştırma; fark != 0 ise muhasebe ile kart kayıtları
    arasında tutarsızlık var demektir (örn. kart girilmeden fatura kesilmiş)."""
    satirlar = []
    for h in duran_varlik_karti_hesaplari():
        kart_toplami = (DuranVarlik.objects.filter(
                hesap_id=h.pk, silindi=False, durum=DuranVarlik.Durum.AKTIF)
                        .aggregate(t=Sum("maliyet"))["t"] or SIFIR)
        mizan_bakiye = _hesap_bakiyesi(h.hesap_kodu)
        satirlar.append({
            "hesap": h, "kart_toplami": kart_toplami, "mizan_bakiye": mizan_bakiye,
            "fark": kart_toplami - mizan_bakiye,
        })
    return satirlar


def silinebilir_mi(varlik: DuranVarlik) -> bool:
    """Kart "Kartı sil" ile silinebilir mi? Yalnız: hiç fatura kalemi bağlı DEĞİL VE
    kaynağı PROJE DEĞİL (FAZ 3 aktifleştirmesinden üretilen kartlar yalnız projenin
    "Aktifleştirmeyi Geri Al" akışıyla kaldırılır, bkz. core.services.yatirim_projesi
    .proje_geri_al)."""
    return (varlik.kaynak != DuranVarlik.Kaynak.PROJE
            and not varlik.fatura_satirlari.exists())


def varlik_sil(varlik: DuranVarlik, *, kullanici=None) -> DuranVarlik:
    if varlik.kaynak == DuranVarlik.Kaynak.PROJE:
        raise DuranVarlikHatasi("Kaynağı Yatırım Projesi olan kart silinemez.")
    if varlik.fatura_satirlari.exists():
        raise DuranVarlikHatasi("Bağlı fatura kalemi olan kart silinemez.")
    from django.utils import timezone
    varlik.silindi = True
    varlik.silindi_at = timezone.now()
    varlik.updated_by = kullanici
    varlik.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return varlik


def duran_varlik_guncelle(varlik: DuranVarlik, *, ad, maliyet, marka_model="", seri_no="",
                          notlar="", kullanici=None) -> DuranVarlik:
    """Yalnız ad/marka-model/seri no/notlar/maliyet düzenlenebilir — hesap ve kaynak
    SABİTTİR (hesap kartın temsil ettiği muhasebe hesabını, kaynak kartın nasıl
    üretildiğini belirler; ikisi de düzenleme ekranından değiştirilemez)."""
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise DuranVarlikHatasi("Ad boş olamaz.")
    if maliyet is None or maliyet < 0:
        raise DuranVarlikHatasi("Maliyet negatif olamaz.")
    varlik.ad = ad
    varlik.maliyet = maliyet
    varlik.marka_model = (marka_model or "").strip()
    varlik.seri_no = (seri_no or "").strip()
    varlik.notlar = (notlar or "").strip()
    varlik.updated_by = kullanici
    varlik.save(update_fields=["ad", "maliyet", "marka_model", "seri_no", "notlar",
                               "updated_by", "updated_at"])
    return varlik
