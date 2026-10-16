"""Kalıcı silme DENETİM KAYDI (2026-10-16): kim, ne zaman, hangi IP'den, neyi sildi — silinen kaydın özeti + tam JSON dökümü ``SilmeKaydi`` tablosunda kalır.
Fiş türü silmeler (banka/kasa/kredi kartı/kredi/virman/kesinti/bordro/KDV mahsup/çek bordrosu/manuel fiş) ``fis_sil.fis_sil`` üzerinden, FATURA ve İRSALİYE silmeleri bu modülden yazılır.
Kayıt silmeyle AYNI transaction'dadır (silme geri alınırsa kayıt da geri alınır). Kayıt ekranlarda görünmez; yalnız yöneticiye Ayarlar › Silme Kayıtları'nda salt-okunur listelenir.
Veri ÖNCE (silmeden önce) toplanır, kayıt silme bittikten sonra yazılır."""
from __future__ import annotations

from decimal import Decimal

from core import istek_baglami
from core.models import SilmeKaydi, StokHareket


def yaz(tur, *, kayit_no, tarih, tutar, aciklama, kaynak="", kullanici=None, veri=None) -> SilmeKaydi:
    """Tek yazım noktası: IP + silen kullanıcı burada eklenir."""
    return SilmeKaydi.objects.create(
        tur=tur, kayit_no=(kayit_no or "")[:40], tarih=tarih, tutar=tutar, aciklama=(aciklama or "")[:500], kaynak=kaynak,
        silen=kullanici, created_by=kullanici, updated_by=kullanici, ip_adresi=istek_baglami.ip(), veri=veri or {})


def _s(x):
    return None if x is None else str(x)


def _hareketler(kalemler_filtre) -> list:
    return [{"id": h.pk, "tarih": str(h.tarih), "tur": h.tur, "miktar": str(h.miktar), "stok": f"{h.stok.kod} {h.stok.ad}", "aciklama": h.aciklama,
             "depo": h.depo.ad if getattr(h, "depo_id", None) else None, "silindi": h.silindi}
            for h in StokHareket.objects.filter(**kalemler_filtre).select_related("stok", "depo").order_by("id")]


def fatura_verisi(fatura) -> dict:
    """Faturayı SİLMEDEN ÖNCE: özet (no, cari, tarih, toplam) + fatura/satır/fiş/stok hareketi JSON dökümü."""
    from core.services import fis_sil as fis_sil_servis
    satirlar = [{"id": s.pk, "stok": (f"{s.stok.kod} {s.stok.ad}" if s.stok_id else None), "hesap": s.hesap_id, "varyant": s.varyant_id, "miktar": str(s.miktar),
                 "birim_fiyat": str(s.birim_fiyat), "kdv": (str(s.kdv.oran) if s.kdv_id else None), "tevkifat": (str(s.tevkifat) if s.tevkifat_id else None),
                 "donem": [_s(s.donem_baslangic), _s(s.donem_bitis)] if s.donem_baslangic else None, "silindi": s.silindi}
                for s in fatura.satirlar.select_related("stok", "kdv", "tevkifat").order_by("id")]
    fis_ozeti = fis_sil_servis.fis_ozeti(fatura.fis) if fatura.fis_id else None
    ozet = {
        "no": fatura.fatura_no or f"#{fatura.pk}", "tarih": fatura.tarih,
        "tutar": (fis_ozeti["tutar"] if fis_ozeti else (fatura.genel_toplam if fatura.para_birimi == "TRY" else None)),
        "aciklama": f"{fatura.cari.unvan} · {fatura.get_yon_display() if hasattr(fatura, 'get_yon_display') else fatura.yon}"
                    + (f" · {fatura.tip.ad}" if fatura.tip_id else "") + (f" · {fatura.aciklama}" if fatura.aciklama else ""),
    }
    veri = {
        "fatura": {"pk": fatura.pk, "fatura_no": fatura.fatura_no, "yon": fatura.yon, "durum": fatura.durum, "tip": fatura.tip.ad if fatura.tip_id else None,
                   "tarih": str(fatura.tarih), "vade_tarihi": _s(fatura.vade_tarihi), "para_birimi": fatura.para_birimi, "kur": _s(fatura.kur), "faturasiz": fatura.faturasiz,
                   "sahsi_alis": fatura.sahsi_alis, "aciklama": fatura.aciklama, "genel_toplam": str(fatura.genel_toplam), "ara_toplam": str(fatura.ara_toplam),
                   "kdv_toplam": str(fatura.kdv_toplam),
                   "cari": {"pk": fatura.cari_id, "kod": fatura.cari.kod, "unvan": fatura.cari.unvan, "muhasebe_kodu": fatura.cari.muhasebe_kodu},
                   "olusturan": fatura.created_by_id, "olusturulma": str(fatura.created_at)},
        "satirlar": satirlar,
        "fis": ({"fis_pk": fatura.fis_id, "no": fis_ozeti["no"], "tarih": str(fis_ozeti["tarih"]), "tutar": str(fis_ozeti["tutar"]), "kaynak": fis_ozeti["kaynak"],
                 "aciklama": fis_ozeti["aciklama"], "satirlar": fis_ozeti["satirlar"]} if fis_ozeti else None),
        "stok_hareketleri": _hareketler({"fatura_satir__fatura": fatura}),
        "kaynak_belgeler": list(fatura.kaynak_siparisler.values_list("belge_no", flat=True)),
    }
    return {"ozet": ozet, "veri": veri}


def irsaliye_verisi(irsaliye) -> dict:
    """İrsaliyeyi SİLMEDEN ÖNCE: özet + belge/kalem/stok hareketi JSON dökümü."""
    kalemler = [{"id": k.pk, "stok": f"{k.stok.kod} {k.stok.ad}", "varyant": k.varyant_id, "miktar": str(k.miktar), "birim_fiyat": str(k.birim_fiyat),
                 "iskonto_yuzdesi": str(k.iskonto_yuzdesi), "kdv": (str(k.kdv.oran) if k.kdv_id else None)}
                for k in irsaliye.kalemler.select_related("stok", "kdv").order_by("id")]
    toplam = irsaliye.genel_toplam if irsaliye.para_birimi == "TRY" else None
    ozet = {"no": irsaliye.belge_no or f"#{irsaliye.pk}", "tarih": irsaliye.tarih, "tutar": toplam,
            "aciklama": f"{irsaliye.cari.unvan if irsaliye.cari_id else '—'} · {irsaliye.yon}" + (f" · {irsaliye.aciklama}" if irsaliye.aciklama else "")}
    veri = {
        "irsaliye": {"pk": irsaliye.pk, "belge_no": irsaliye.belge_no, "yon": irsaliye.yon, "durum": irsaliye.durum, "tarih": str(irsaliye.tarih), "para_birimi": irsaliye.para_birimi,
                     "kur": _s(irsaliye.kur), "aciklama": irsaliye.aciklama, "genel_toplam": str(irsaliye.genel_toplam),
                     "cari": ({"pk": irsaliye.cari_id, "kod": irsaliye.cari.kod, "unvan": irsaliye.cari.unvan} if irsaliye.cari_id else None),
                     "fatura_id": irsaliye.fatura_id, "olusturan": irsaliye.created_by_id, "olusturulma": str(irsaliye.created_at)},
        "kalemler": kalemler,
        "stok_hareketleri": _hareketler({"teklif_siparis_kalem__teklif_siparis": irsaliye}),
    }
    return {"ozet": ozet, "veri": veri}


def fatura_yaz(toplanan, kullanici) -> SilmeKaydi:
    o = toplanan["ozet"]
    return yaz(SilmeKaydi.Tur.FATURA, kayit_no=o["no"], tarih=o["tarih"], tutar=o["tutar"], aciklama=o["aciklama"], kaynak="FATURA", kullanici=kullanici, veri=toplanan["veri"])


def irsaliye_yaz(toplanan, kullanici) -> SilmeKaydi:
    o = toplanan["ozet"]
    return yaz(SilmeKaydi.Tur.IRSALIYE, kayit_no=o["no"], tarih=o["tarih"], tutar=o["tutar"], aciklama=o["aciklama"], kaynak="IRSALIYE", kullanici=kullanici, veri=toplanan["veri"])
