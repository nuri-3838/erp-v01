"""Duran Varlık kartı (DURAN VARLIK FAZ 2) servis katmanı.

253/254/255/260 hesaplarından birine bağlı somut bir sabit kıymetin basit kart kaydı —
ekle/listele/detay + 253/254/255/260 için kart-toplamı vs mizan-bakiyesi kontrol raporu.
Aktifleştirme (FAZ 3: kaynak=PROJE, M2M fatura kalemi ataması) burada henüz YOK.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from core.metin import buyuk_harf_tr
from core.models import DuranVarlik, FaturaSatir, HesapPlani, YevmiyeSatir
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
    olmamalı (bkz. core.services.duran_varlik._dogrula_satirlar). Satır(lar) verilirse
    ``maliyet`` parametresi YOK SAYILIR — kart maliyeti bağlı kalemlerin (KDV/tevkifat
    hariç) matrah toplamı olur (bkz. baglanti_toplami); satır verilmezse (ACILIS) elle
    girilen ``maliyet`` kullanılır."""
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise DuranVarlikHatasi("Ad boş olamaz.")
    if not duran_varlik_karti_hesaplari().filter(pk=hesap_id).exists():
        raise DuranVarlikHatasi(
            "Geçerli bir duran varlık hesabı seçin (253/254/255/260, yaprak hesap olmalı).")

    satirlar = _dogrula_satirlar(fatura_satirlari, hesap_id)
    if satirlar:
        maliyet = sum((s.tutar_tl for s in satirlar), SIFIR)
    elif maliyet is None or maliyet < 0:
        raise DuranVarlikHatasi("Maliyet negatif olamaz.")

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
        pk__in=ids, silindi=False, fatura__silindi=False).select_related("fatura"))
    if len(satirlar) != len(set(ids)):
        raise DuranVarlikHatasi("Belirtilen fatura kalemlerinden biri bulunamadı.")
    for s in satirlar:
        if s.fatura.yon == "SATIS" or s.demirbas_id:
            raise DuranVarlikHatasi(f"Satır {s.pk}: satış faturası satırı karta maliyet olarak bağlanamaz.")
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
                                       fatura__silindi=False, demirbas__isnull=True)
            .exclude(fatura__yon="SATIS")          # satış faturasındaki hesap/demirbaş satırı maliyet değildir
            .exclude(duran_varliklar__silindi=False)
            .select_related("fatura", "fatura__cari").order_by("fatura__tarih", "id"))


def satir_bagla(varlik: DuranVarlik, satir_id, *, kullanici=None) -> DuranVarlik:
    """Kalemi karta bağlar VE kart maliyetini bağlı (silinmemiş) kalemlerin matrah
    toplamına göre otomatik günceller (bkz. baglanti_toplami) — elle girilmiş maliyet
    bundan sonra korunmaz."""
    satirlar = _dogrula_satirlar([satir_id], varlik.hesap_id, haric_varlik_pk=varlik.pk)
    varlik.fatura_satirlari.add(*satirlar)
    varlik.maliyet = baglanti_toplami(varlik)
    varlik.updated_by = kullanici
    varlik.save(update_fields=["maliyet", "updated_by", "updated_at"])
    return varlik


def satir_cikar(varlik: DuranVarlik, satir_id, *, kullanici=None) -> DuranVarlik:
    """Kalemi karttan çıkarır VE kart maliyetini kalan bağlı kalemlerin matrah toplamına
    göre otomatik günceller (hiç kalem kalmazsa 0,00 olur)."""
    satir = varlik.fatura_satirlari.filter(pk=satir_id).first()
    if not satir:
        raise DuranVarlikHatasi("Bu kalem karta bağlı değil.")
    varlik.fatura_satirlari.remove(satir)
    varlik.maliyet = baglanti_toplami(varlik)
    varlik.updated_by = kullanici
    varlik.save(update_fields=["maliyet", "updated_by", "updated_at"])
    return varlik


def baglanti_toplami(varlik: DuranVarlik) -> Decimal:
    toplam = SIFIR
    for s in (varlik.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
             .select_related("fatura")):
        toplam += s.tutar_tl
    return toplam


def durum_degistir(varlik: DuranVarlik, *, durum, kullanici=None) -> DuranVarlik:
    if durum not in DuranVarlik.Durum.values:
        raise DuranVarlikHatasi("Geçersiz durum.")
    if durum == DuranVarlik.Durum.SATILDI or varlik.durum == DuranVarlik.Durum.SATILDI:
        raise DuranVarlikHatasi(
            "Satıldı durumu yalnız satış faturasıyla değişir (faturayı silince kart Aktif'e döner).")
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


def _yatirim_projeleri_kontrol_satiri() -> dict:
    """"Yatırım projeleri toplamı = mizan 258 bakiyesi" satırı: DEVAM eden projelerin
    proje_toplami() toplamı (TL), 258 ailesinin (258 + varsa alt hesapları) net mizan
    bakiyesiyle karşılaştırılır. AKTİFLEŞTİ projeler 258'den çıktığı (fişle 253/255/...'e
    aktarıldığı) için hesaba dahil edilmez."""
    from django.db.models import Q

    from core.models import YatirimProjesi
    from core.services.yatirim_projesi import proje_toplami

    proje_toplam = sum(
        (proje_toplami(p) for p in
         YatirimProjesi.objects.filter(silindi=False, durum=YatirimProjesi.Durum.DEVAM)),
        SIFIR)
    mizan_258 = SIFIR
    for kod in HesapPlani.objects.filter(
            Q(hesap_kodu="258") | Q(hesap_kodu__startswith="258."),
            silindi=False).values_list("hesap_kodu", flat=True):
        mizan_258 += _hesap_bakiyesi(kod)
    hesap_258 = HesapPlani.objects.filter(hesap_kodu="258", silindi=False).first()
    if hesap_258 is None:
        return None
    return {
        "hesap": hesap_258, "kart_toplami": proje_toplam, "mizan_bakiye": mizan_258,
        "fark": proje_toplam - mizan_258,
    }


def kontrol_raporu() -> list:
    """253/254/255/260 yaprak hesaplarının her biri için kart-toplamı (yalnız AKTİF —
    PASİF kartlar artık defter değerini temsil etmediği varsayılır, hariç tutulur —
    silinmemiş DuranVarlik.maliyet toplamı) vs mizan-bakiyesi (yevmiyeden hesaplanan
    net borç) — salt okunur karşılaştırma; fark != 0 ise muhasebe ile kart kayıtları
    arasında tutarsızlık var demektir (örn. kart girilmeden fatura kesilmiş). Son satır
    258 (Yapılmakta Olan Yatırımlar) için DEVAM eden proje toplamı vs mizan bakiyesi."""
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
    yatirim_satiri = _yatirim_projeleri_kontrol_satiri()
    if yatirim_satiri is not None:
        satirlar.append(yatirim_satiri)
    return satirlar


def silinebilir_mi(varlik: DuranVarlik) -> bool:
    """Kart "Kartı sil" ile silinebilir mi? Yalnız: hiç fatura kalemi bağlı DEĞİL VE
    kaynağı PROJE DEĞİL (FAZ 3 aktifleştirmesinden üretilen kartlar yalnız projenin
    "Aktifleştirmeyi Geri Al" akışıyla kaldırılır, bkz. core.services.yatirim_projesi
    .proje_geri_al)."""
    return (varlik.kaynak != DuranVarlik.Kaynak.PROJE
            and varlik.durum != DuranVarlik.Durum.SATILDI
            and not varlik.fatura_satirlari.exists())


def varlik_sil(varlik: DuranVarlik, *, kullanici=None) -> DuranVarlik:
    if varlik.durum == DuranVarlik.Durum.SATILDI:
        raise DuranVarlikHatasi("Satılmış kart silinemez (önce satış faturasını silin).")
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


def duran_varlik_guncelle(varlik: DuranVarlik, *, ad, maliyet=None, marka_model="", seri_no="",
                          notlar="", birikmis_amortisman=None, kullanici=None) -> DuranVarlik:
    """Yalnız ad/marka-model/seri no/notlar/maliyet düzenlenebilir — hesap ve kaynak
    SABİTTİR (hesap kartın temsil ettiği muhasebe hesabını, kaynak kartın nasıl
    üretildiğini belirler; ikisi de düzenleme ekranından değiştirilemez). Kartın bağlı
    fatura kalemi VARSA maliyet artık otomatik (bkz. satir_bagla/satir_cikar) — bu
    fonksiyona verilen ``maliyet`` o durumda YOK SAYILIR; yalnız hiç bağlı kalemi
    olmayan (ACILIS) kartlarda elle değiştirilebilir."""
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise DuranVarlikHatasi("Ad boş olamaz.")
    alanlar = ["ad", "marka_model", "seri_no", "notlar", "updated_by", "updated_at"]
    varlik.ad = ad
    varlik.marka_model = (marka_model or "").strip()
    varlik.seri_no = (seri_no or "").strip()
    varlik.notlar = (notlar or "").strip()
    if not varlik.fatura_satirlari.exists():
        if maliyet is None or maliyet < 0:
            raise DuranVarlikHatasi("Maliyet negatif olamaz.")
        varlik.maliyet = maliyet
        alanlar.append("maliyet")
    if birikmis_amortisman is not None:
        if birikmis_amortisman < 0 or birikmis_amortisman > varlik.maliyet:
            raise DuranVarlikHatasi("Birikmiş amortisman 0 ile maliyet arasında olmalı.")
        if varlik.durum == DuranVarlik.Durum.SATILDI:
            raise DuranVarlikHatasi("Satılmış kartın birikmiş amortismanı değiştirilemez.")
        varlik.birikmis_amortisman = birikmis_amortisman
        alanlar.append("birikmis_amortisman")
    varlik.updated_by = kullanici
    varlik.save(update_fields=alanlar)
    return varlik
