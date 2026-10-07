"""Yatırım Projesi (DURAN VARLIK FAZ 1-3) servis katmanı.

258 (Yapılmakta Olan Yatırımlar) hesabında biriken alış gider faturası kalemlerinin
gruplandığı proje kartı — CRUD + toplam hesaplama + aktifleştirme (FAZ 3: tek fişle
253/254/255/260'a aktarım + DuranVarlik kartı üretimi) + geri alma.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import DuranVarlik, HesapPlani, YatirimProjesi, YevmiyeFisi
from core.services import hesap_plani as hp

SIFIR = Decimal("0.00")


class YatirimProjesiHatasi(ValueError):
    """Yatırım projesi kural ihlali (Türkçe mesaj)."""


def aktif_projeler():
    return YatirimProjesi.objects.filter(silindi=False).order_by("-created_at")


def sonraki_proje_kodu() -> str:
    n = 1
    while YatirimProjesi.objects.filter(kod=f"YP-{str(n).zfill(4)}", silindi=False).exists():
        n += 1
    return f"YP-{str(n).zfill(4)}"


def proje_olustur(*, ad, aciklama="", grup_kodu=None, kullanici=None) -> YatirimProjesi:
    """Projeyi açar. ``grup_kodu`` (258.01 … 258.04) verilirse sistem o grubun altında sıradaki 258.0X.000N hesabını (adı =
    proje adı) açıp projeye bağlar; projeye yazılan TÜM 258 satırları bu hesaba gider. (Ekran grubu zorunlu tutar; grup
    verilmeyen eski çağrılar hesapsız proje açar ve satırlar seçilen 258 hesabına yazılır.)"""
    from core.services import duran_hesap
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise YatirimProjesiHatasi("Proje adı boş olamaz.")
    with transaction.atomic():
        hesap = None
        if grup_kodu:
            try:
                hesap = duran_hesap.proje_hesabi_ac(grup_kodu, ad, kullanici=kullanici)
            except (duran_hesap.DuranHesapHatasi, hp.HesapHatasi) as e:
                raise YatirimProjesiHatasi(str(e))
        return YatirimProjesi.objects.create(
            kod=sonraki_proje_kodu(), ad=ad, aciklama=(aciklama or "").strip(), hesap=hesap,
            created_by=kullanici, updated_by=kullanici)


def proje_parcalari(proje: YatirimProjesi) -> dict:
    """Proje toplamının PARÇALARI + hareket istatistiği (liste ve detay ortak kaynağı; ``proje_toplami`` bunun toplamıdır):
    fatura (KDV hariç TL; SATIŞ yönlü 258 hesap satırı = alış iadesi EKSİ) + sarf (stok sarf FIFO maliyeti) + diger (projeye bağlı
    fiş satırları: kesinti/virman/banka/kur farkı… net borç). ``hareket`` = fatura kalemi + diğer satır + sarf sayısı;
    ``ilk``/``son`` = bu hareketlerin tarih aralığı (hiç hareket yoksa None)."""
    from core.models import YevmiyeSatir
    from core.services import stok_maliyet
    fatura, sarf, diger = SIFIR, SIFIR, SIFIR
    tarihler = []
    fatura_adet = diger_adet = sarf_adet = 0
    for s in (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False).select_related("fatura")):
        fatura += s.tutar_tl if s.fatura.yon != "SATIS" else -s.tutar_tl
        tarihler.append(s.fatura.tarih)
        fatura_adet += 1
    for h in proje_sarf_hareketleri(proje):
        sarf += stok_maliyet.hareket_maliyet_durumu(h)["tutar_try"] or SIFIR
        tarihler.append(h.tarih)
        sarf_adet += 1
    for s in proje_diger_hareketler(proje):
        diger += s.borc - s.alacak
        tarihler.append(s.fis.tarih)
        diger_adet += 1
    return {"fatura": fatura, "sarf": sarf, "diger": diger, "toplam": fatura + sarf + diger,
            "hareket": fatura_adet + diger_adet + sarf_adet, "fatura_adet": fatura_adet, "diger_adet": diger_adet,
            "sarf_adet": sarf_adet, "ilk": min(tarihler) if tarihler else None, "son": max(tarihler) if tarihler else None}


def proje_toplami(proje: YatirimProjesi) -> Decimal:
    """Projeye bağlı tüm (silinmemiş faturadaki silinmemiş) kalemlerin KDV HARİÇ TL
    karşılığı toplamı (``tutar_tl`` = tutar × fatura kuru; döviz faturada ``tutar`` TL
    DEĞİLDİR, fatura para biriminde kalır — bkz. FaturaSatir.tutar_tl)
    + projeye bağlı (silinmemiş) stok sarf çıkışlarının FIFO maliyet toplamı (zaten TL)
    + projeye bağlı fiş satırlarının (258, fatura dışı — kesinti/virman/banka/gümrükçü dekontu)
    net borç toplamı (zaten TL, bkz. core.models.YevmiyeSatir.yatirim_projesi). Parçalar: ``proje_parcalari``."""
    return proje_parcalari(proje)["toplam"]


def proje_diger_hareketler(proje: YatirimProjesi):
    """Projeye bağlı (``YevmiyeSatir.yatirim_projesi``) silinmemiş fiş satırları — fatura/sarf dışı hareketler (kesinti, virman, banka,
    kur farkı…). Tarih, fiş no, satır sırasıyla."""
    from core.models import YevmiyeSatir
    return (YevmiyeSatir.objects.filter(yatirim_projesi=proje, silindi=False, fis__silindi=False)
            .select_related("fis", "hesap").order_by("fis__tarih", "fis__fis_no", "id"))


def proje_yevmiye_kalemleri(proje: YatirimProjesi):
    """Projenin 258 hesabındaki TÜM fiş satırları (kaynağından bağımsız) yürüyen bakiyeli — ``raporlar.ekstre`` ile BİREBİR aynı motor
    (tüm tarihler). Hesapsız eski projede None."""
    import datetime

    from core.services import raporlar
    if not proje.hesap_id:
        return None
    return raporlar.ekstre(proje.hesap_id, datetime.date(1900, 1, 1), datetime.date(2999, 12, 31))


def proje_sarf_hareketleri(proje: YatirimProjesi):
    """Projeye (258 karşı hesabıyla) bağlı, silinmemiş stok sarf çıkışları."""
    from core.models import StokHareket
    return (StokHareket.objects.filter(yatirim_projesi=proje, silindi=False)
            .select_related("stok", "depo", "karsi_hesap").order_by("tarih", "id"))


def proje_fatura_sayisi(proje: YatirimProjesi) -> int:
    return (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
            .values("fatura_id").distinct().count())


def proje_aktiflestir(proje: YatirimProjesi, *, tarih, satirlar, kullanici=None) -> YatirimProjesi:
    """258 toplamını birden fazla duran varlık satırına böler; tek transaction'da:
    tek yevmiye fişi (her satır: hedef hesap borç / 258 alacak) + her satır için
    kaynak=PROJE bir DuranVarlik kartı (projenin tüm fatura kalemlerine bağlı) üretir,
    proje durumu AKTIFLESTI'ye geçer. ``satirlar``: [{"hesap_id", "varlik_adi", "tutar"}, ...]
    — toplamı proje toplamına kuruşuna eşit olmalı."""
    from core.services import duran_hesap
    from core.services.duran_varlik import sonraki_demirbas_kodu
    from core.services.hesap_plani import duran_varlik_karti_hesaplari
    from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

    if proje.silindi or proje.durum != YatirimProjesi.Durum.DEVAM:
        raise YatirimProjesiHatasi("Yalnız 'Devam Ediyor' durumundaki bir proje aktifleştirilebilir.")
    toplam = proje_toplami(proje)
    if toplam <= SIFIR:
        raise YatirimProjesiHatasi("Projede aktifleştirilecek tutar yok.")
    if not satirlar:
        raise YatirimProjesiHatasi("En az bir satır girilmelidir.")

    izinli_hesap_pk = set(duran_varlik_karti_hesaplari().values_list("pk", flat=True))
    hazir = []
    satir_toplam = SIFIR
    for i, s in enumerate(satirlar, start=1):
        hesap_id = s.get("hesap_id")
        grup_kodu = s.get("grup_kodu")
        varlik_adi = buyuk_harf_tr((s.get("varlik_adi") or "").strip())
        tutar = s.get("tutar")
        if grup_kodu:              # YENİ: grup seçilir, sistem sıradaki 000N hesabını açıp karta bağlar
            if not HesapPlani.objects.filter(hesap_kodu=grup_kodu, silindi=False, aktif=True).exists() \
                    or grup_kodu not in {k for gl in duran_hesap.GRUPLAR.values() for k, _ in gl
                                         if k.split(".")[0] in duran_hesap.KART_AILELERI}:
                raise YatirimProjesiHatasi(f"Satır {i}: geçerli bir varlık grubu seçin (253.01 … 260.02).")
            hesap_id = None
        elif hesap_id not in izinli_hesap_pk:
            raise YatirimProjesiHatasi(
                f"Satır {i}: geçerli bir duran varlık hesabı seçin (253/254/255/260).")
        if not varlik_adi:
            raise YatirimProjesiHatasi(f"Satır {i}: varlık adı boş olamaz.")
        if tutar is None or tutar <= SIFIR:
            raise YatirimProjesiHatasi(f"Satır {i}: tutar sıfırdan büyük olmalı.")
        hazir.append({"hesap_id": hesap_id, "grup_kodu": grup_kodu, "varlik_adi": varlik_adi, "tutar": tutar})
        satir_toplam += tutar

    if satir_toplam != toplam:
        raise YatirimProjesiHatasi(
            f"Satır toplamı ({satir_toplam}) proje toplamına ({toplam}) kuruşuna eşit olmalı.")

    # Karta yalnız ALIŞ yönlü satırlar bağlanır; satış faturasındaki 258 hesap satırı (alış iadesi) maliyeti düşürür
    # (proje_toplami'nda zaten eksi), karta maliyet kalemi olarak bağlanmaz.
    proje_satirlari = list(proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
                           .exclude(fatura__yon="SATIS"))
    if proje.hesap_id:                      # projenin kendi 258.0X.000N hesabı: satırlar zaten oraya yazılır
        kaynak_258_kodu = proje.hesap_id
    else:
        kaynak_kodlari = {s.hesap.hesap_kodu for s in proje_satirlari}
        if len(kaynak_kodlari) != 1:
            raise YatirimProjesiHatasi(
                "Proje kalemleri birden fazla 258 alt hesabına dağılmış; aktifleştirme tek hesap bekler.")
        kaynak_258_kodu = kaynak_kodlari.pop()

    with transaction.atomic():
        for row in hazir:
            if row["grup_kodu"]:
                try:
                    row["hesap_id"] = duran_hesap.varlik_hesabi_ac(
                        row["grup_kodu"], row["varlik_adi"], kullanici=kullanici).pk
                except (duran_hesap.DuranHesapHatasi, hp.HesapHatasi) as e:
                    raise YatirimProjesiHatasi(str(e))
        hesap_cache = {h.pk: h for h in HesapPlani.objects.filter(pk__in=[r["hesap_id"] for r in hazir])}
        fis_satirlari = []
        for row in hazir:
            hedef_hesap = hesap_cache[row["hesap_id"]]
            aciklama = f"{proje.kod} — {proje.ad} / {row['varlik_adi']}"
            fis_satirlari.append(SatirGirdi(hesap_kodu=hedef_hesap.hesap_kodu, taraf="B",
                                            islem_tutari=row["tutar"], aciklama=aciklama))
            fis_satirlari.append(SatirGirdi(hesap_kodu=kaynak_258_kodu, taraf="A",
                                            islem_tutari=row["tutar"], aciklama=aciklama))
        try:
            fis = fis_olustur(
                tarih=tarih, satirlar=fis_satirlari,
                aciklama=f"{proje.kod} — {proje.ad} aktifleştirme",
                kaynak=YevmiyeFisi.Kaynak.YATIRIM, kullanici=kullanici)
        except YevmiyeHatasi as e:
            raise YatirimProjesiHatasi(str(e))

        for row in hazir:
            varlik = DuranVarlik.objects.create(
                demirbas_kodu=sonraki_demirbas_kodu(), ad=row["varlik_adi"],
                hesap_id=row["hesap_id"], aktiflestirme_tarihi=tarih, maliyet=row["tutar"],
                durum=DuranVarlik.Durum.AKTIF, kaynak=DuranVarlik.Kaynak.PROJE,
                yatirim_projesi=proje, created_by=kullanici, updated_by=kullanici)
            if proje_satirlari:
                varlik.fatura_satirlari.set(proje_satirlari)

        proje.durum = YatirimProjesi.Durum.AKTIFLESTI
        proje.aktiflestirme_fisi = fis
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "aktiflestirme_fisi", "updated_by", "updated_at"])

    return proje


def proje_bakiye_258(proje: YatirimProjesi) -> Decimal:
    """Projenin 258 bakiyesi: projeye özel 258.0X.000N hesabının defter bakiyesi (borç − alacak); hesapsız eski projede proje toplamı."""
    import datetime

    from core.services import raporlar
    if proje.hesap_id:
        return raporlar._devir(proje.hesap_id, datetime.date(2100, 1, 1))[0]
    return proje_toplami(proje)


def proje_kapat(proje: YatirimProjesi, *, tarih, neden, aciklama="", kullanici=None) -> YatirimProjesi:
    """258 bakiyesi 0,00 olan (satılmış / başka hesaba aktarılmış) projeyi AKTİFLEŞTİRMEDEN kapatır. Fiş üretmez; yalnız durum 'Kapandı'
    olur (kapanış tarihi, nedeni, açıklaması saklanır). Bakiye 0 değilse kalan bakiye gösterilerek reddedilir. Kapanmış projeye yeni
    fatura/kesinti/virman/sarf satırı bağlanamaz."""
    import datetime
    if proje.silindi or proje.durum != YatirimProjesi.Durum.DEVAM:
        raise YatirimProjesiHatasi("Yalnız 'Devam Ediyor' durumundaki proje kapatılabilir.")
    if not isinstance(tarih, datetime.date):
        raise YatirimProjesiHatasi("Kapanış tarihi gerekli.")
    if neden not in YatirimProjesi.KapanisNedeni.values:
        raise YatirimProjesiHatasi("Kapanış nedeni seçin (Satıldı / Diğer).")
    aciklama = (aciklama or "").strip()
    if neden == YatirimProjesi.KapanisNedeni.DIGER and not aciklama:
        raise YatirimProjesiHatasi("Neden 'Diğer' ise açıklama yazın.")
    bakiye = proje_bakiye_258(proje)
    if bakiye != SIFIR:
        from core.sayi import format_tr
        raise YatirimProjesiHatasi(
            f"{proje.kod} kapatılamaz: 258 bakiyesi {format_tr(bakiye)} TL (0,00 olmalı). Önce kalan bakiyeyi satış/aktarım kaydıyla "
            f"kapatın ya da projeyi aktifleştirin.")
    with transaction.atomic():
        proje.durum = YatirimProjesi.Durum.KAPANDI
        proje.kapanis_tarihi, proje.kapanis_nedeni, proje.kapanis_aciklama = tarih, neden, aciklama
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "kapanis_tarihi", "kapanis_nedeni", "kapanis_aciklama", "updated_by", "updated_at"])
    return proje


def proje_yeniden_ac(proje: YatirimProjesi, *, kullanici=None) -> YatirimProjesi:
    """Kapanmış projeyi 'Devam Ediyor'a döndürür (yalnız süper kullanıcı). Kapanış bilgileri temizlenir; fiş etkilenmez."""
    if not getattr(kullanici, "is_superuser", False):
        raise YatirimProjesiHatasi("Projeyi yeniden açmak yalnız süper kullanıcıya açıktır.")
    if proje.silindi or proje.durum != YatirimProjesi.Durum.KAPANDI:
        raise YatirimProjesiHatasi("Yalnız 'Kapandı' durumundaki proje yeniden açılabilir.")
    with transaction.atomic():
        proje.durum = YatirimProjesi.Durum.DEVAM
        proje.kapanis_tarihi, proje.kapanis_nedeni, proje.kapanis_aciklama = None, "", ""
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "kapanis_tarihi", "kapanis_nedeni", "kapanis_aciklama", "updated_by", "updated_at"])
    return proje


def proje_geri_al(proje: YatirimProjesi, *, kullanici=None) -> YatirimProjesi:
    """Aktifleştirmeyi geri alır: fişi iptal eder, bu aktifleştirmeden üretilen
    DuranVarlik kartlarını siler, proje DEVAM'a döner. Yetki kontrolü (yalnız
    yönetici) ÇAĞIRAN katmanda (view) yapılır — bu fonksiyon yalnız iş kuralını
    zorlar."""
    from django.utils import timezone

    from core.services.yevmiye import fis_iptal

    if proje.durum != YatirimProjesi.Durum.AKTIFLESTI:
        raise YatirimProjesiHatasi("Yalnız 'Aktifleşti' durumundaki bir proje geri alınabilir.")
    if not proje.aktiflestirme_fisi_id:
        raise YatirimProjesiHatasi("Projeye bağlı bir aktifleştirme fişi bulunamadı.")

    with transaction.atomic():
        fis_iptal(proje.aktiflestirme_fisi, kullanici=kullanici)
        DuranVarlik.objects.filter(yatirim_projesi=proje, silindi=False).update(
            silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        proje.durum = YatirimProjesi.Durum.DEVAM
        proje.aktiflestirme_fisi = None
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "aktiflestirme_fisi", "updated_by", "updated_at"])

    return proje


# === Liste ekranı: filtre / sekme / özet / sıralama ===
DURUM_SEKMELERI = (("DEVAM", "Devam Ediyor"), ("AKTIFLESTI", "Aktifleşti"), ("KAPANDI", "Kapandı"), ("TUMU", "Tümü"))
SIRALAMA_ANAHTARLARI = ("kod", "ad", "hesap", "durum", "ilk", "son", "hareket", "toplam")


def _siralama_anahtari(kayit, anahtar):
    p = kayit["proje"]
    return {"kod": p.kod, "ad": p.ad, "hesap": p.hesap_id or "", "durum": p.get_durum_display(), "ilk": kayit["ilk"],
            "son": kayit["son"], "hareket": kayit["hareket"], "toplam": kayit["toplam"]}[anahtar]


def proje_listesi(*, arama="", grup="", baslangic=None, bitis=None, durum="DEVAM", sirala="son", yon="azalan", bugun=None) -> dict:
    """Yatırım projeleri listesi: arama (kod/ad/hesap kodu/açıklama) + hesap grubu (258.0X) + son hareket tarih aralığı TABAN kümeyi,
    durum sekmesi satırları belirler. Sekme sayıları ve özet kartları taban kümeden hesaplanır (durum seçimi onları değiştirmez).
    Döner: kayitlar (sıralı), sayilar{DEVAM,AKTIFLESTI,KAPANDI,TUMU}, devam_toplam, bu_yil_aktiflesen, mizan_258, devam_toplam_tum
    (filtresiz, mizan karşılaştırması için), uyari (devam toplamı ≠ 258 bakiyesi), toplam (listelenenlerin)."""
    import datetime

    from core.metin import buyuk_harf_tr
    from core.models import DuranVarlik
    from core.services import raporlar
    bugun = bugun or datetime.date.today()
    durum = durum if durum in {d for d, _ in DURUM_SEKMELERI} else "DEVAM"
    sirala = sirala if sirala in SIRALAMA_ANAHTARLARI else "son"
    azalan = yon != "artan"

    tum = list(YatirimProjesi.objects.filter(silindi=False).select_related("aktiflestirme_fisi", "hesap"))
    ozet = {p.pk: proje_parcalari(p) for p in tum}
    kartlar = {}
    for v in DuranVarlik.objects.filter(yatirim_projesi__isnull=False, silindi=False).order_by("demirbas_kodu"):
        kartlar.setdefault(v.yatirim_projesi_id, []).append(v)

    kayitlar = [{"proje": p, **ozet[p.pk], "kartlar": kartlar.get(p.pk, [])} for p in tum]
    devam_toplam_tum = sum((k["toplam"] for k in kayitlar if k["proje"].durum == YatirimProjesi.Durum.DEVAM), SIFIR)

    arama = (arama or "").strip()
    if arama:
        aranan = buyuk_harf_tr(arama)          # TR büyük harf (i→İ ı→I): ad/açıklama büyük harfle saklanır
        kayitlar = [k for k in kayitlar if aranan in buyuk_harf_tr(
            " ".join([k["proje"].kod, k["proje"].ad, k["proje"].hesap_id or "", k["proje"].aciklama or ""]))]
    if grup:
        kayitlar = [k for k in kayitlar if (k["proje"].hesap_id or "").startswith(grup + ".")]
    if baslangic or bitis:
        kayitlar = [k for k in kayitlar if k["son"] is not None and (not baslangic or k["son"] >= baslangic)
                    and (not bitis or k["son"] <= bitis)]

    sayilar = {"TUMU": len(kayitlar)}
    for d, _ in DURUM_SEKMELERI[:3]:
        sayilar[d] = sum(1 for k in kayitlar if k["proje"].durum == d)
    devam_toplam = sum((k["toplam"] for k in kayitlar if k["proje"].durum == YatirimProjesi.Durum.DEVAM), SIFIR)
    bu_yil = sum((k["toplam"] for k in kayitlar if k["proje"].durum == YatirimProjesi.Durum.AKTIFLESTI
                  and k["proje"].aktiflestirme_fisi_id and k["proje"].aktiflestirme_fisi.tarih.year == bugun.year), SIFIR)

    secili = kayitlar if durum == "TUMU" else [k for k in kayitlar if k["proje"].durum == durum]
    dolu = [k for k in secili if _siralama_anahtari(k, sirala) is not None]
    bos = [k for k in secili if _siralama_anahtari(k, sirala) is None]       # tarihi olmayanlar her yönde SONDA
    dolu.sort(key=lambda k: (_siralama_anahtari(k, sirala), k["proje"].kod), reverse=azalan)
    secili = dolu + bos

    mizan_258 = raporlar._devir("258", datetime.date(2100, 1, 1))[0]
    return {"kayitlar": secili, "sayilar": sayilar, "devam_toplam": devam_toplam, "bu_yil_aktiflesen": bu_yil,
            "mizan_258": mizan_258, "devam_toplam_tum": devam_toplam_tum, "uyari": devam_toplam_tum != mizan_258, "fark": devam_toplam_tum - mizan_258,
            "toplam": sum((k["toplam"] for k in secili), SIFIR), "durum": durum, "sirala": sirala, "yon": "azalan" if azalan else "artan"}
