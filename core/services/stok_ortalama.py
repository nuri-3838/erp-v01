"""Stok hareketli AĞIRLIKLI ORTALAMA maliyet motoru (kart bazında, TÜM depolar için tek ortalama).

Kurallar (kullanıcı kararı):
- Maliyeti FATURA belirler: giriş tutarı fatura satır tutarıdır (KDV/tevkifat hariç, TL) —
  ``StokHareket.giris_tutar_try`` (+ USD karşılığı ``giris_tutar_usd``). Fatura gelmemiş
  (irsaliye) giriş FİYATSIZdır: miktar eklenir ama ortalamayı DEĞİŞTİRMEZ; mevcut ortalamayla
  geçici değerlenir ve GEÇİCİ işaretlenir. Fatura gelince fiyatlanır, kart baştan hesaplanır.
- Her girişte yeni ortalama = (miktar × ortalama + giriş tutarı) / toplam miktar.
- Çıkışlar o anki ortalamayla değerlenir (``tutar_try``); son parça (çıkış = kalan miktar)
  kalan değerin TAMAMINI alır — yuvarlama kalıntısı kalmaz.
- Geç gelen fatura / geriye tarihli kayıt: kartın TÜM hareketleri (tarih, giriş önce, id)
  sırasıyla baştan hesaplanır (``yeniden_hesapla``); değeri değişen sarf çıkışlarının muhasebe
  fişi güncellenir.
- Hareket üzerindeki ``tutar_*``/``birim_maliyet_*``/``sonrasi_*`` ve ``Stok.maliyet_*`` /
  ``ort_maliyet_*`` alanları HESAP SONUCU ÖNBELLEĞİDİR (elle düzenlenmez); her an
  ``hesapla`` ile sıfırdan üretilebilir (testle eşitliği denetlenir).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum

from core.models import Stok, StokHareket
from core.sayi import yuvarla

SIFIR = Decimal("0")
GIRIS = StokHareket.Tur.GIRIS
CIKIS = StokHareket.Tur.CIKIS
KESIN = StokHareket.MaliyetDurumu.KESIN
GECICI = StokHareket.MaliyetDurumu.GECICI
YOK = StokHareket.MaliyetDurumu.YOK


class MaliyetHatasi(ValueError):
    """Maliyet yeniden hesabı kural ihlali (Türkçe mesaj)."""


@dataclass
class Sonuc:
    """Tek hareketin hesap sonucu (hareket sırasıyla)."""
    durum: str
    tutar_try: Decimal
    tutar_usd: Decimal
    birim_try: Decimal | None
    birim_usd: Decimal | None
    sonrasi_miktar: Decimal
    sonrasi_deger_try: Decimal
    sonrasi_deger_usd: Decimal
    sonrasi_ort_try: Decimal | None
    sonrasi_ort_usd: Decimal | None


def _ort(deger, miktar):
    return yuvarla(deger / miktar, 6) if miktar > 0 else None


def hesapla(kalemler) -> list[Sonuc]:
    """Saf hesap. ``kalemler`` HAREKET SIRASIYLA (tarih, giriş önce, id) bir dizi nesne:
    ``tur``, ``miktar``, ``giris_tutar_try``, ``giris_tutar_usd`` (+ opsiyonel ``giris_tahmini``)
    alanları. DB'ye dokunmaz."""
    q = v = vu = SIFIR
    bekleyen = 0                 # fiyatsız (fatura gelmemiş) ya da tahmini giriş görüldü mü
    cikti = []
    for k in kalemler:
        m = k.miktar
        if getattr(k, "kaynak", None) == StokHareket.Kaynak.TRANSFER:
            # Depo transferi: MALİYETİ DEĞİŞTİRMEZ (tek ortalama, tüm depolar) — bilgi amaçlı
            # tutar o anki ortalamayla; miktar/değer durumu aynen kalır (çıkış+giriş net sıfır).
            t = yuvarla(m * v / q, 2) if q > 0 and v > 0 else SIFIR
            tu = yuvarla(m * vu / q, 2) if q > 0 and v > 0 else SIFIR
            durum = (KESIN if bekleyen == 0 else GECICI) if t > 0 else YOK
        elif k.tur == GIRIS and getattr(k, "giris_ortalama", False):
            # Satış iadesi girişi: o anki ağırlıklı ortalamayla (ortalamayı değiştirmez).
            if q > 0 and v > 0:
                t, tu = yuvarla(m * v / q, 2), yuvarla(m * vu / q, 2)
                durum = KESIN if bekleyen == 0 else GECICI
            else:
                t, tu, durum = SIFIR, SIFIR, YOK
            q, v, vu = q + m, v + t, vu + tu
        elif k.tur == GIRIS:
            if k.giris_tutar_try is not None:
                t = k.giris_tutar_try
                tu = k.giris_tutar_usd if k.giris_tutar_usd is not None else SIFIR
                if getattr(k, "giris_tahmini", False):
                    durum = GECICI
                    bekleyen += 1
                else:
                    durum = KESIN
            else:
                bekleyen += 1
                if q > 0:
                    t, tu, durum = yuvarla(m * v / q, 2), yuvarla(m * vu / q, 2), GECICI
                else:
                    t, tu, durum = SIFIR, SIFIR, YOK
            q, v, vu = q + m, v + t, vu + tu
        elif k.giris_tutar_try is not None:
            # Alış iadesi çıkışı (tedarikçiye iade): iade faturasının tutarıyla değerlenir; stok
            # değeri o tutar kadar düşer, ortalama kalan miktar/değerden yeniden hesaplanır.
            t = k.giris_tutar_try
            tu = k.giris_tutar_usd if k.giris_tutar_usd is not None else SIFIR
            durum = KESIN
            q, v, vu = q - m, v - t, vu - tu
        else:
            if q <= 0 or v <= 0:             # eldeki yok / hiç fiyatlı değer yok: maliyet bilinmiyor
                t, tu, durum = SIFIR, SIFIR, YOK
            else:
                if m >= q:                       # son parça: kalan değerin tamamı
                    t, tu = v, vu
                else:
                    t, tu = yuvarla(m * v / q, 2), yuvarla(m * vu / q, 2)
                durum = KESIN if bekleyen == 0 else GECICI
            q, v, vu = q - m, v - t, vu - tu
        cikti.append(Sonuc(
            durum=durum, tutar_try=t, tutar_usd=tu,
            birim_try=yuvarla(t / m, 6) if m > 0 else None,
            birim_usd=yuvarla(tu / m, 6) if m > 0 else None,
            sonrasi_miktar=q, sonrasi_deger_try=v, sonrasi_deger_usd=vu,
            sonrasi_ort_try=_ort(v, q), sonrasi_ort_usd=_ort(vu, q)))
    return cikti


def _siralama(h):
    return (h.tarih, 0 if h.tur == GIRIS else 1, h.pk or 0)


def kart_hareketleri(stok):
    """Kartın silinmemiş hareketleri, hesap sırasıyla."""
    return sorted(StokHareket.objects.filter(stok=stok, silindi=False), key=_siralama)


_HAREKET_ALANLARI = ["maliyet_durumu", "tutar_try", "tutar_usd", "birim_maliyet_try",
                     "birim_maliyet_usd", "sonrasi_miktar", "sonrasi_deger_try",
                     "sonrasi_deger_usd", "sonrasi_ort_try", "sonrasi_ort_usd"]


def _usd_doldur(hareketler):
    """TL girdisi var, USD girdisi yok ise (eski kayıt) o tarihin USD kurundan türet."""
    from core.services.yevmiye import kur_usd_bul
    for h in hareketler:
        if h.tur == GIRIS and h.giris_tutar_try is not None and h.giris_tutar_usd is None:
            kur = kur_usd_bul(h.tarih)
            h.giris_tutar_usd = yuvarla(h.giris_tutar_try / kur, 2) if kur else SIFIR


@transaction.atomic
def yeniden_hesapla(stok, *, fis_guncelle=True, _derinlik=0) -> list:
    """Kartın TÜM hareketlerini baştan hesaplar, sonuçları hareketlere ve ``Stok`` önbelleğine
    yazar. Değeri değişen sarf çıkışlarının fişini günceller (``fis_guncelle=False`` ise
    güncellemez, yalnız değişenleri döner). Dönüş: değeri değişen çıkış hareketleri."""
    hareketler = kart_hareketleri(stok)
    _usd_doldur(hareketler)
    sonuclar = hesapla(hareketler)
    degisen_cikislar, guncellenecek = [], []
    for h, s in zip(hareketler, sonuclar):
        eski_tutar = h.tutar_try
        yeni = {"maliyet_durumu": s.durum, "tutar_try": s.tutar_try, "tutar_usd": s.tutar_usd,
                "birim_maliyet_try": s.birim_try, "birim_maliyet_usd": s.birim_usd,
                "sonrasi_miktar": s.sonrasi_miktar, "sonrasi_deger_try": s.sonrasi_deger_try,
                "sonrasi_deger_usd": s.sonrasi_deger_usd, "sonrasi_ort_try": s.sonrasi_ort_try,
                "sonrasi_ort_usd": s.sonrasi_ort_usd}
        if any(getattr(h, a) != y for a, y in yeni.items()):
            for a, y in yeni.items():
                setattr(h, a, y)
            guncellenecek.append(h)
        if (eski_tutar is not None and eski_tutar != s.tutar_try
                and (h.tur == CIKIS or h.giris_ortalama)
                and h.kaynak != StokHareket.Kaynak.TRANSFER):
            degisen_cikislar.append(h)
    if guncellenecek:
        StokHareket.objects.bulk_update(guncellenecek, _HAREKET_ALANLARI + ["giris_tutar_usd"])
    son = sonuclar[-1] if sonuclar else None
    stok.maliyet_miktar = son.sonrasi_miktar if son else SIFIR
    stok.maliyet_deger_try = son.sonrasi_deger_try if son else SIFIR
    stok.maliyet_deger_usd = son.sonrasi_deger_usd if son else SIFIR
    stok.ort_maliyet_try = son.sonrasi_ort_try if son else None
    stok.ort_maliyet_usd = son.sonrasi_ort_usd if son else None
    Stok.objects.filter(pk=stok.pk).update(
        maliyet_miktar=stok.maliyet_miktar, maliyet_deger_try=stok.maliyet_deger_try,
        maliyet_deger_usd=stok.maliyet_deger_usd, ort_maliyet_try=stok.ort_maliyet_try,
        ort_maliyet_usd=stok.ort_maliyet_usd)
    if fis_guncelle:
        _degisenleri_isle(degisen_cikislar, _derinlik)
    return degisen_cikislar


def _degisenleri_isle(degisen, derinlik):
    """Değeri değişen çıkış/iade hareketlerinin muhasebe fişlerini ve zincirleme etkilerini işler:
    sarf fişi güncellenir; üretim girdisi ise çıktı maliyeti + aktarım fişi yenilenir ve çıktı kartı
    (onu kullanan sonraki üretimler dahil) baştan hesaplanır; satış/iade ise maliyet fişi yenilenir."""
    from core.services import stok_fis
    if derinlik > 10:
        raise MaliyetHatasi("Maliyet zinciri çok derin (döngü olabilir); işlem durduruldu.")
    kayitlar, faturalar = {}, {}
    for h in degisen:
        if h.kaynak == StokHareket.Kaynak.SARF and h.fis_id:
            _cikis_fisini_guncelle(h)
        elif h.operasyon_kaydi_id:
            kayitlar[h.operasyon_kaydi_id] = h.operasyon_kaydi
        elif h.fatura_satir_id:
            faturalar[h.fatura_satir.fatura_id] = h.fatura_satir.fatura
    for kayit in kayitlar.values():
        for cikti_stok in stok_fis.uretim_senkronla(kayit):          # ana + yan çıktılar
            yeniden_hesapla(cikti_stok, _derinlik=derinlik + 1)
    for fatura in faturalar.values():
        stok_fis.satis_senkronla(fatura)


def _cikis_fisini_guncelle(hareket):
    """Sarf çıkışının fişini (2 satır: karşı hesap B, stok hesabı A) yeni tutara çeker."""
    from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle
    fis = hareket.fis
    if fis is None or fis.silindi:
        return
    tutar = hareket.tutar_try
    if tutar is None or tutar <= 0:
        raise MaliyetHatasi(
            f"{hareket.stok.kod} sarf çıkışının maliyeti sıfıra düşüyor (stok/maliyet "
            "kaydı değişmiş olabilir); önce çıkışı düzeltin.")
    satirlar = [SatirGirdi(s.hesap_id, "B" if s.borc > 0 else "A", tutar,
                           yatirim_projesi_id=s.yatirim_projesi_id)
                for s in fis.satirlar.filter(silindi=False).order_by("id")]
    try:
        fis_guncelle(fis, tarih=fis.tarih, satirlar=satirlar, aciklama=fis.aciklama,
                     kur_usd=fis.kur_usd)
    except YevmiyeHatasi as e:
        raise MaliyetHatasi(str(e))


def tum_kartlari_yeniden_hesapla(stok_idler=None, *, fis_guncelle=True) -> dict:
    """Hareketi olan (veya verilen) tüm kartları hesaplar. {stok_id: değişen çıkış sayısı}."""
    qs = Stok.objects.filter(silindi=False)
    if stok_idler is not None:
        qs = qs.filter(pk__in=list(stok_idler))
    else:
        qs = qs.filter(Q(hareketler__silindi=False) | ~Q(maliyet_miktar=0)).distinct()
    return {s.pk: len(yeniden_hesapla(s, fis_guncelle=fis_guncelle)) for s in qs}


# === Fatura → giriş tutarı atama ======================================================

def _usd(tutar_try, kur_usd):
    return yuvarla(tutar_try / kur_usd, 2) if kur_usd else SIFIR


def _fatura_usd_kuru(fatura):
    return fatura.fis.kur_usd if fatura.fis_id and fatura.fis is not None else None


def irsaliye_girisleri(fatura):
    """İrsaliyeli (stok girişini İRSALİYE yazmış) faturanın irsaliye GİRİŞ hareketleri."""
    return list(StokHareket.objects.filter(
        teklif_siparis_kalem__teklif_siparis__fatura=fatura,
        teklif_siparis_kalem__teklif_siparis__silindi=False,
        tur=GIRIS, silindi=False).order_by("id"))


def irsaliye_girislerini_sifirla(fatura) -> set:
    """Faturanın irsaliye girişlerini FİYATSIZ'a döndürür (fatura silinirken/yeniden
    fiyatlanırken). Etkilenen stok id'lerini döner; hesabı ÇAĞIRAN yapar."""
    stoklar = set()
    for h in irsaliye_girisleri(fatura):
        if h.giris_tutar_try is not None or h.maliyet_fatura_satir_id:
            h.giris_tutar_try = h.giris_tutar_usd = h.maliyet_fatura_satir = None
            h.save(update_fields=["giris_tutar_try", "giris_tutar_usd",
                                  "maliyet_fatura_satir", "updated_at"])
        stoklar.add(h.stok_id)
    return stoklar


@transaction.atomic
def irsaliye_girislerini_fiyatla(fatura) -> int:
    """İRSALİYELİ faturada maliyeti FATURA belirler: önce irsaliye girişleri fiyatsıza
    döndürülür, sonra her stok için fatura satır tutarları (TL, KDV/tevkifat hariç) o stoğun
    irsaliye girişlerine MİKTAR ORANINDA dağıtılır (son giriş kalanı alır → toplam fatura
    tutarına birebir eşit). Fatura satırında karşılığı olmayan giriş fiyatsız kalır. Etkilenen
    kartlar yeniden hesaplanır. Dönüş: fiyatlanan hareket sayısı."""
    etkilenen = irsaliye_girislerini_sifirla(fatura)
    kur_usd = _fatura_usd_kuru(fatura)
    satirlar = {}
    for s in fatura.satirlar.filter(silindi=False, stok__isnull=False).order_by("id"):
        satirlar.setdefault(s.stok_id, []).append(s)
    girisler = {}
    for h in irsaliye_girisleri(fatura):
        girisler.setdefault(h.stok_id, []).append(h)
    fiyatlanan = 0
    for stok_id, hs in girisler.items():
        ss = satirlar.get(stok_id)
        if not ss:
            continue
        toplam = sum((s.tutar_tl for s in ss), SIFIR)
        toplam_usd = _usd(toplam, kur_usd)
        miktar = sum((h.miktar for h in hs), SIFIR)
        kalan, kalan_usd = toplam, toplam_usd
        for i, h in enumerate(hs):
            if i == len(hs) - 1:
                t, tu = kalan, kalan_usd
            else:
                t, tu = yuvarla(toplam * h.miktar / miktar, 2), yuvarla(toplam_usd * h.miktar / miktar, 2)
            kalan, kalan_usd = kalan - t, kalan_usd - tu
            h.giris_tutar_try, h.giris_tutar_usd, h.maliyet_fatura_satir = t, tu, ss[0]
            h.save(update_fields=["giris_tutar_try", "giris_tutar_usd",
                                  "maliyet_fatura_satir", "updated_at"])
            fiyatlanan += 1
    for stok in Stok.objects.filter(pk__in=etkilenen):
        yeniden_hesapla(stok)
    return fiyatlanan


# === Stok değerleme raporu ===========================================================

HESAP_GRUPLARI = ("150", "151", "152", "153")


def _mizan_bakiyesi(kod):
    """Hesap ailesinin (kod + alt hesaplar) NET BORÇ bakiyesi — tüm mali yıllar (varlık hesabı
    yıldan yıla devreder; duran varlık kontrol raporuyla aynı mantık)."""
    from core.models import YevmiyeSatir
    agg = YevmiyeSatir.objects.filter(
        Q(hesap__hesap_kodu=kod) | Q(hesap__hesap_kodu__startswith=kod + "."),
        silindi=False, fis__silindi=False).aggregate(b=Sum("borc"), a=Sum("alacak"))
    return (agg["b"] or SIFIR) - (agg["a"] or SIFIR)


def degerleme_raporu() -> dict:
    """Kart bazında miktar/ortalama/değer + 150-153 mizan karşılaştırması. Geçici (fiyatsız)
    girişlerin değeri mizan karşılaştırmasından DÜŞÜLÜR (henüz faturası/fişi yok). Muhasebe
    hesabı kategori eşlemesinden bulunamayan kartlar 'TANIMSIZ' grubunda ayrı gösterilir."""
    from core.services import kategori as kategori_servis
    kartlar = (Stok.objects.filter(silindi=False, kategori__hizmet_kategorisi=False)         # hizmet kartları stok değerine girmez
               .filter(Q(hareketler__silindi=False) | ~Q(maliyet_miktar=0)).distinct()
               .select_related("uretim_birimi", "kategori").order_by("kod"))
    gecici = {r["stok_id"]: r["t"] for r in StokHareket.objects.filter(
        silindi=False, tur=GIRIS, maliyet_durumu=GECICI).values("stok_id").annotate(t=Sum("tutar_try"))}
    # FASON: faturası gelmemiş fason bedeli 'tahmini'dir; mizanda YOKTUR (fason faturası 151 alt hesabına borç yazar). Çıktı girişi tahmini
    # sayıldığından tutarının TAMAMI 'geçici' düşer; oysa malzeme payı fişle mizana girmiştir → geçiciden yalnız fason payı düşülür (malzeme
    # de tahminiyse eskisi gibi tamamı). Böylece stok değeri − mizan farkı (``ham_fark``) tam olarak bekleyen fason bedeline eşit olur.
    from core.services.fason_maliyet import bekleyen_ciktilar
    fason_bekleyen = {}                                   # stok_id -> bekleyen fason bedeli (TL)
    for b in bekleyen_ciktilar():
        c, h = b["cikti"], b["hareket"]
        fason_bekleyen[c.stok_id] = fason_bekleyen.get(c.stok_id, SIFIR) + c.fason_tutar
        if h is not None and h.maliyet_durumu == GECICI and not b["malzeme_tahmini"]:
            gecici[c.stok_id] = gecici.get(c.stok_id, SIFIR) - (h.tutar_try or SIFIR) + min(h.tutar_try or SIFIR, c.fason_tutar)
    yok = {r["stok_id"]: r["n"] for r in StokHareket.objects.filter(
        silindi=False, maliyet_durumu=YOK).values("stok_id").annotate(n=Count("id"))}
    hesap_onbellek = {}
    satirlar, grup_toplam = [], {}
    for s in kartlar:
        if s.kategori_id not in hesap_onbellek:
            try:
                hesap_onbellek[s.kategori_id] = kategori_servis.stok_muhasebe_hesabi(s).hesap_kodu
            except Exception:                        # kategori hesabı tanımsız/çoklu
                hesap_onbellek[s.kategori_id] = None
        kod = hesap_onbellek[s.kategori_id]
        grup = kod[:3] if kod and kod[:3] in HESAP_GRUPLARI else "TANIMSIZ"
        g = gecici.get(s.pk) or SIFIR
        satirlar.append({"stok": s, "hesap": kod, "grup": grup, "miktar": s.maliyet_miktar,
                         "ort_try": s.ort_maliyet_try, "ort_usd": s.ort_maliyet_usd,
                         "deger_try": s.maliyet_deger_try, "deger_usd": s.maliyet_deger_usd,
                         "gecici_try": g, "yok_adet": yok.get(s.pk, 0)})
        agg = grup_toplam.setdefault(grup, {"deger": SIFIR, "gecici": SIFIR, "usd": SIFIR, "fason": SIFIR})
        agg["deger"] += s.maliyet_deger_try
        agg["usd"] += s.maliyet_deger_usd
        agg["gecici"] += g
        agg["fason"] += fason_bekleyen.get(s.pk, SIFIR)
    karsilastirma = []
    for kod in HESAP_GRUPLARI:
        agg = grup_toplam.get(kod, {"deger": SIFIR, "gecici": SIFIR, "usd": SIFIR, "fason": SIFIR})
        kesin = agg["deger"] - agg["gecici"]
        mizan = _mizan_bakiyesi(kod)
        karsilastirma.append({"kod": kod, "stok_degeri": kesin, "gecici": agg["gecici"],
                              "mizan": mizan, "fark": kesin - mizan,
                              "toplam_deger": agg["deger"], "ham_fark": agg["deger"] - mizan, "fason_bekleyen": agg["fason"]})
    tanimsiz = grup_toplam.get("TANIMSIZ")
    return {"satirlar": satirlar, "karsilastirma": karsilastirma, "tanimsiz": tanimsiz,
            "toplam_deger": sum((r["deger_try"] for r in satirlar), SIFIR),
            "toplam_usd": sum((r["deger_usd"] for r in satirlar), SIFIR)}
