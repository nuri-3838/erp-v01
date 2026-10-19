"""SATINALMA SİPARİŞİ — KISMİ TESLİM (2026-10-20): bir alış siparişi birden çok irsaliyeye çevrilebilir; her ONAYLI irsaliye siparişin kalan miktarından düşer.

Hesap TÜRETİLMİŞTİR (saklanan sayaç YOK → irsaliye silinince / onayı geri alınınca kalan kendiliğinden geri artar):
* Teslim = siparişten doğan (``kaynak_siparis``) silinmemiş ve ONAYLI irsaliyelerin kalemleri. TASLAK irsaliye teslim sayılmaz (mal henüz girmedi; stok girişi onayda yazılır).
* Eşleşme anahtarı (stok, varyant): kalem varyantı boşsa varyantlı kartta kartın varsayılan varyantı (stok hareketi/ayırma ile aynı anlam). Aynı anahtarlı birden çok sipariş
  kalemi varsa teslim sırayla (kalem sırasına göre) dağıtılır. Siparişte olmayan kalem ("siparişsiz", ör. yan etiket) teslim hesabına girmez, ayrıca listelenir.
* Kalan = max(sipariş − teslim, 0); fazla teslim (sipariş üstü) kalemde "fazla" olarak gösterilir, kalan negatif olmaz.
* Durum: BEKLIYOR (onaylı irsaliye yok) / KISMI (teslim var, kalan var) / TAMAM (hiç kalan yok → "Tamamlandı").
Miktarlar FATURA birimindedir (kalem.miktar); gelecek stok tarafı üretim birimine ``core.services.satinalma`` içinde çevirir."""
from __future__ import annotations

from decimal import Decimal

from core.models import TeklifSiparis, TeklifSiparisKalem

SIFIR = Decimal("0")
BEKLIYOR, KISMI, TAMAM = "BEKLIYOR", "KISMI", "TAMAM"


def anahtar(k) -> tuple:
    """(stok pk, varyant pk|None): varyantlı kartta boş varyant = kartın varsayılan varyantı."""
    return (k.stok_id, k.varyant_id or (k.stok.varsayilan_varyant_id if k.stok.varyantli else None))


def _teslim_havuzu(siparis_idler) -> dict:
    """{siparis_id: {anahtar: toplam teslim miktar}} — ONAYLI, silinmemiş irsaliyelerin kalemleri."""
    havuz = {}
    qs = (TeklifSiparisKalem.objects.filter(
        silindi=False, teklif_siparis__silindi=False, teklif_siparis__belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, teklif_siparis__durum=TeklifSiparis.Durum.ONAYLI,
        teklif_siparis__kaynak_siparis_id__in=list(siparis_idler)).select_related("stok", "teklif_siparis").order_by("id"))
    for k in qs:
        h = havuz.setdefault(k.teklif_siparis.kaynak_siparis_id, {})
        h[anahtar(k)] = h.get(anahtar(k), SIFIR) + k.miktar
    return havuz


def durumlar(siparis_idler, kalemler=None) -> dict:
    """{siparis_id: {"durum", "kalemler": [{"kalem", "siparis", "teslim", "kalan", "fazla"}], "kalan_by_kalem": {kalem_id: kalan}, "teslim_var", "kalan_var",
    "siparissiz": [{"stok", "varyant_id", "miktar"}]}} — toplu (sayfa/liste başına sabit sorgu). ``kalemler``: sipariş kalemleri verilirse (filtrelenmiş olabilir) onlar kullanılır."""
    idler = list(siparis_idler)
    if kalemler is None:
        kalemler = list(TeklifSiparisKalem.objects.filter(silindi=False, teklif_siparis_id__in=idler).select_related("stok").order_by("teklif_siparis_id", "id"))
    havuz = _teslim_havuzu(idler)
    gruplar = {i: [] for i in idler}
    for k in kalemler:
        gruplar.setdefault(k.teklif_siparis_id, []).append(k)
    sonuc = {}
    for sid, ks in gruplar.items():
        kalan_havuz = dict(havuz.get(sid, {}))
        satirlar, son_kalem = [], {}
        for k in sorted(ks, key=lambda x: x.pk):
            a = anahtar(k)
            teslim = min(k.miktar, kalan_havuz.get(a, SIFIR))
            kalan_havuz[a] = kalan_havuz.get(a, SIFIR) - teslim
            satir = {"kalem": k, "siparis": k.miktar, "teslim": teslim, "kalan": k.miktar - teslim, "fazla": SIFIR}
            satirlar.append(satir)
            son_kalem[a] = satir
        siparissiz = []
        for a, fazla in kalan_havuz.items():
            if fazla <= 0:
                continue
            if a in son_kalem:
                son_kalem[a]["fazla"] = fazla                           # sipariş üstü teslim: aynı anahtarın son kaleminde
            else:
                siparissiz.append({"stok_id": a[0], "varyant_id": a[1], "miktar": fazla})
        teslim_var = bool(havuz.get(sid))
        kalan_var = any(s["kalan"] > 0 for s in satirlar)
        durum = BEKLIYOR if not teslim_var else (KISMI if kalan_var else TAMAM)
        sonuc[sid] = {"durum": durum, "kalemler": satirlar, "kalan_by_kalem": {s["kalem"].pk: s["kalan"] for s in satirlar}, "teslim_var": teslim_var,
                      "kalan_var": kalan_var, "siparissiz": siparissiz}
    return sonuc


def siparis_durumu(siparis) -> dict:
    return durumlar([siparis.pk])[siparis.pk]


def taslak_irsaliye(siparis):
    """Siparişten doğan ve henüz ONAYLANMAMIŞ (silinmemiş) irsaliye — varsa yeni irsaliye açılamaz (aynı kalan iki kez rezerve edilmesin)."""
    return siparis.donusen_irsaliyeler.filter(silindi=False, durum=TeklifSiparis.Durum.TASLAK).order_by("pk").first()
