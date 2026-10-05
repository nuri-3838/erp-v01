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


def proje_toplami(proje: YatirimProjesi) -> Decimal:
    """Projeye bağlı tüm (silinmemiş faturadaki silinmemiş) kalemlerin KDV HARİÇ TL
    karşılığı toplamı (``tutar_tl`` = tutar × fatura kuru; döviz faturada ``tutar`` TL
    DEĞİLDİR, fatura para biriminde kalır — bkz. FaturaSatir.tutar_tl)
    + projeye bağlı (silinmemiş) stok sarf çıkışlarının FIFO maliyet toplamı (zaten TL)
    + projeye bağlı manuel fiş satırlarının (258, fatura dışı — örn. gümrükçü dekontu)
    net borç toplamı (zaten TL, bkz. core.models.YevmiyeSatir.yatirim_projesi)."""
    from core.models import YevmiyeSatir
    from core.services import stok_maliyet
    toplam = SIFIR
    for s in (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
             .select_related("fatura")):
        # SATIŞ yönlü faturadaki 258 hesap satırı (alış iadesi) proje maliyetini AZALTIR.
        toplam += s.tutar_tl if s.fatura.yon != "SATIS" else -s.tutar_tl
    for h in proje_sarf_hareketleri(proje):
        durum = stok_maliyet.hareket_maliyet_durumu(h)
        toplam += durum["tutar_try"] or SIFIR
    for s in YevmiyeSatir.objects.filter(
            yatirim_projesi=proje, silindi=False, fis__silindi=False):
        toplam += s.borc - s.alacak
    return toplam


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
