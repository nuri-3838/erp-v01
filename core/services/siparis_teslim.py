"""SATINALMA SİPARİŞİ — KISMİ TESLİM (2026-10-20): bir alış siparişi birden çok irsaliyeye çevrilebilir; her ONAYLI irsaliye siparişin kalan miktarından düşer.

Hesap TÜRETİLMİŞTİR (saklanan sayaç YOK → irsaliye silinince / onayı geri alınınca kalan kendiliğinden geri artar):
* Teslim = siparişten doğan (``kaynak_siparis``) silinmemiş ve ONAYLI irsaliyelerin kalemleri. TASLAK irsaliye teslim sayılmaz (mal henüz girmedi; stok girişi onayda yazılır).
* Eşleşme anahtarı (stok, varyant): kalem varyantı boşsa varyantlı kartta kartın varsayılan varyantı (stok hareketi/ayırma ile aynı anlam). Aynı anahtarlı birden çok sipariş
  kalemi varsa teslim sırayla (kalem sırasına göre) dağıtılır. Siparişte olmayan kalem ("siparişsiz", ör. yan etiket) teslim hesabına girmez, ayrıca listelenir.
* Kalan = max(sipariş − teslim, 0); fazla teslim (sipariş üstü) kalemde "fazla" olarak gösterilir, kalan negatif olmaz.
* KALANI KAPAT (2026-10-20, ``SiparisKalanKapatma``): tedarikçinin göndermeyeceği miktar kalemde AYRICA düşülür — kalan = sipariş − teslim − kapatılan (aktif kayıtlar); kapatılan teslim
  sayılmaz (stok girişi yok). Kapatmayı geri alınca kayıt geri alınmış işaretlenir ve kalan geri açılır (türetilmiş hesap: sonradan teslim edilen miktar zaten düşülmüş olur).
* Durum: BEKLIYOR (ne teslim ne kapatma var) / KISMI (kalan var) / TAMAM (hiç kalan yok → "Tamamlandı").
Miktarlar FATURA birimindedir (kalem.miktar); gelecek stok tarafı üretim birimine ``core.services.satinalma`` içinde çevirir."""
from __future__ import annotations

from decimal import Decimal

import uuid

from django.db import transaction
from django.utils import timezone

from core.models import FaturaSatir, SiparisKalanKapatma, TeklifSiparis, TeklifSiparisKalem

SIFIR = Decimal("0")
BEKLIYOR, KISMI, TAMAM = "BEKLIYOR", "KISMI", "TAMAM"


class KalanKapatmaHatasi(ValueError):
    """Kalanı kapat / kapatmayı geri al kural ihlali (Türkçe mesaj)."""


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
    # SATIŞ siparişi: irsaliyesiz kesilen DEPOLU faturanın (siparis.fatura) irsaliyeye bağlı OLMAYAN satırları da sevk sayılır (stok çıkışını fatura yazmıştır)
    for sip in TeklifSiparis.objects.filter(pk__in=list(siparis_idler), yon=TeklifSiparis.Yon.SATIS, fatura__isnull=False, fatura__silindi=False, fatura__depo__isnull=False):
        for sat in FaturaSatir.objects.filter(fatura_id=sip.fatura_id, silindi=False, stok__isnull=False, irsaliye_kalem__isnull=True).select_related("stok"):
            a = (sat.stok_id, sat.varyant_id or (sat.stok.varsayilan_varyant_id if sat.stok.varyantli else None))
            h = havuz.setdefault(sip.pk, {})
            h[a] = h.get(a, SIFIR) + sat.miktar
    return havuz


def durumlar(siparis_idler, kalemler=None) -> dict:
    """{siparis_id: {"durum", "kalemler": [{"kalem", "siparis", "teslim", "kalan", "fazla"}], "kalan_by_kalem": {kalem_id: kalan}, "teslim_var", "kalan_var",
    "siparissiz": [{"stok", "varyant_id", "miktar"}], "kapatmalar": [SiparisKalanKapatma…] (hepsi, geri alınanlar dahil), "kapatma_var": aktif kayıt var mı}}
    — toplu (sayfa/liste başına sabit sorgu). Satırda ayrıca "kapatilan" (aktif kapatılan miktar, sipariş−teslim ile sınırlı). ``kalemler``: sipariş kalemleri verilirse (filtrelenmiş olabilir) onlar kullanılır."""
    idler = list(siparis_idler)
    if kalemler is None:
        kalemler = list(TeklifSiparisKalem.objects.filter(silindi=False, teklif_siparis_id__in=idler).select_related("stok").order_by("teklif_siparis_id", "id"))
    havuz = _teslim_havuzu(idler)
    kapatmalar = {}
    for kk in SiparisKalanKapatma.objects.filter(silindi=False, siparis_id__in=idler).select_related("kalem__stok", "created_by", "geri_alan").order_by("id"):
        kapatmalar.setdefault(kk.siparis_id, []).append(kk)
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
            kapatilan = min(sum((kk.miktar for kk in kapatmalar.get(sid, []) if kk.kalem_id == k.pk and kk.aktif), SIFIR), k.miktar - teslim)
            satir = {"kalem": k, "siparis": k.miktar, "teslim": teslim, "kapatilan": kapatilan, "kalan": k.miktar - teslim - kapatilan, "fazla": SIFIR}
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
        kapatma_var = any(kk.aktif for kk in kapatmalar.get(sid, []))
        kalan_var = any(s["kalan"] > 0 for s in satirlar)
        durum = BEKLIYOR if not (teslim_var or kapatma_var) else (KISMI if kalan_var else TAMAM)
        sonuc[sid] = {"durum": durum, "kalemler": satirlar, "kalan_by_kalem": {s["kalem"].pk: s["kalan"] for s in satirlar}, "teslim_var": teslim_var,
                      "kalan_var": kalan_var, "siparissiz": siparissiz, "kapatmalar": kapatmalar.get(sid, []), "kapatma_var": kapatma_var}
    return sonuc


def siparis_durumu(siparis) -> dict:
    return durumlar([siparis.pk])[siparis.pk]


def taslak_irsaliye(siparis):
    """Siparişten doğan ve henüz ONAYLANMAMIŞ (silinmemiş) irsaliye — varsa yeni irsaliye açılamaz (aynı kalan iki kez rezerve edilmesin)."""
    return siparis.donusen_irsaliyeler.filter(silindi=False, durum=TeklifSiparis.Durum.TASLAK).order_by("pk").first()


# --- KALANI KAPAT (gelmeyecek) ---

def _aciklama_dogrula(aciklama) -> str:
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise KalanKapatmaHatasi("Açıklama zorunlu (ör. \"tedarikçi göndermeyecek\").")
    if len(aciklama) > 300:
        raise KalanKapatmaHatasi("Açıklama en fazla 300 karakter olabilir.")
    return aciklama


@transaction.atomic
def kalani_kapat(siparis, *, aciklama, kullanici=None, kalem_miktarlari=None):
    """Kısmen teslim alınmış alış siparişinin kalanını KAPATIR (tedarikçi göndermeyecek). ``kalem_miktarlari`` None → tüm kalan; {kalem_id: miktar} → yalnız o kalemler (miktar ≤ kalem kalanı).
    Kapatılan miktar teslim sayılmaz; kalan o kadar düşer; sipariş kalan kalmayınca "Tamamlandı" olur. Onaylanmamış (taslak) irsaliyesi olan siparişte yapılamaz. [SiparisKalanKapatma] döner."""
    s = TeklifSiparis.objects.select_for_update().get(pk=siparis.pk)
    if s.silindi or s.belge_tur != TeklifSiparis.BelgeTur.SIPARIS or s.yon != TeklifSiparis.Yon.ALIS or s.durum != TeklifSiparis.Durum.ONAYLI:
        raise KalanKapatmaHatasi("Yalnız onaylı satınalma siparişinin kalanı kapatılabilir.")
    aciklama = _aciklama_dogrula(aciklama)
    taslak = taslak_irsaliye(s)
    if taslak is not None:
        raise KalanKapatmaHatasi(f"Siparişin onaylanmamış bir irsaliye taslağı var ({taslak.belge_no}); önce onaylayın ya da silin.")
    d = durumlar([s.pk])[s.pk]
    if not d["teslim_var"]:
        raise KalanKapatmaHatasi("Yalnız kısmen teslim alınmış siparişin kalanı kapatılabilir (henüz hiçbir irsaliye onaylanmamış).")
    if not d["kalan_var"]:
        raise KalanKapatmaHatasi("Kapatılacak kalan yok.")
    satirlar = {x["kalem"].pk: x for x in d["kalemler"]}
    secim = {}
    if kalem_miktarlari is None:
        secim = {kid: x["kalan"] for kid, x in satirlar.items() if x["kalan"] > 0}
    else:
        for kid, ham in kalem_miktarlari.items():
            kid = int(kid)
            if kid not in satirlar:
                raise KalanKapatmaHatasi("Seçilen kalem bu siparişe ait değil.")
            try:
                from core.sayi import parse_tr
                m = parse_tr(ham)
            except Exception:
                raise KalanKapatmaHatasi("Kapatılacak miktar geçerli bir sayı olmalı.")
            if m == 0:
                continue
            if m < 0:
                raise KalanKapatmaHatasi("Kapatılacak miktar sıfırdan büyük olmalı.")
            kalan = satirlar[kid]["kalan"]
            if m > kalan:
                raise KalanKapatmaHatasi(f"{satirlar[kid]['kalem'].stok.kod}: kapatılacak miktar ({m.normalize():f}) kalanı ({kalan.normalize():f}) aşamaz.")
            secim[kid] = m
    if not secim:
        raise KalanKapatmaHatasi("Kapatılacak kalem/miktar seçilmedi.")
    grup = uuid.uuid4().hex[:12]
    return [SiparisKalanKapatma.objects.create(siparis=s, kalem=satirlar[kid]["kalem"], miktar=m, aciklama=aciklama, grup=grup, created_by=kullanici, updated_by=kullanici)
            for kid, m in secim.items()]


@transaction.atomic
def kapatmayi_geri_al(kapatma, *, kullanici=None):
    """Kapatmayı geri alır: kayıt "geri alındı" işaretlenir (geçmiş kalır), kapatılan miktar kalana geri eklenir. Hesap türetilmiş olduğundan kapatmadan sonra teslim edilen
    miktar (varsa) zaten düşülür — kalan hiçbir zaman sipariş − teslim'i aşmaz. Geri alma engellenmez."""
    kk = SiparisKalanKapatma.objects.select_for_update().select_related("siparis").get(pk=kapatma.pk)
    if kk.geri_alindi_at is not None:
        raise KalanKapatmaHatasi("Bu kapatma zaten geri alınmış.")
    s = kk.siparis
    if s.silindi or s.durum != TeklifSiparis.Durum.ONAYLI:
        raise KalanKapatmaHatasi("Sipariş onaylı değil; kapatma geri alınamaz.")
    kk.geri_alindi_at, kk.geri_alan, kk.updated_by = timezone.now(), kullanici, kullanici
    kk.save(update_fields=["geri_alindi_at", "geri_alan", "updated_by", "updated_at"])
    return kk
