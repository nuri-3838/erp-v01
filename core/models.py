"""Çekirdek modeller.

Burada yalnızca tüm tabloların paylaştığı invariant taban (audit + soft delete,
spec 0b-g) ile v0.1 veri modelinin ilk tablosu HESAP_PLANI (spec bölüm 2) var.
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class TemelModel(models.Model):
    """Tüm tablolar için ortak invariant'lar (spec 0b-g).

    - Audit: created/updated by + at (çok kullanıcı v0.1'de yok ama alanlar
      baştan tutulur; sonradan eklemek acılıdır).
    - Soft delete: kayıt fiziksel silinmez; ``silindi`` ile pasifleştirilir.

    ``created_by`` / ``updated_by`` v0.1'de doldurulmaz (kullanıcı arayüzü yok),
    bu yüzden ``null=True``; fiş giriş ekranı gelince servis katmanında atanır.
    """

    created_at = models.DateTimeField("oluşturulma", auto_now_add=True)
    updated_at = models.DateTimeField("güncellenme", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="oluşturan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="güncelleyen",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    silindi = models.BooleanField("silindi (soft delete)", default=False)
    silindi_at = models.DateTimeField("silinme zamanı", null=True, blank=True)

    class Meta:
        abstract = True


class HesapPlani(TemelModel):
    """TDHP / 7-A hesap planı (spec bölüm 2 — Tablo: HESAP_PLANI).

    Bakiye saklanmaz; mizan/bilanço/gelir tablosu yevmiye satırlarından hesaplanır.
    """

    class RaporGrubu(models.TextChoices):
        BILANCO = "BILANCO", "Bilanço"
        GELIR_TABLOSU = "GELIR_TABLOSU", "Gelir Tablosu"
        MALIYET = "MALIYET", "Maliyet (7xx)"

    hesap_kodu = models.CharField("hesap kodu", max_length=20, primary_key=True)
    hesap_adi = models.CharField("hesap adı", max_length=200)
    rapor_grubu = models.CharField(
        "rapor grubu", max_length=20, choices=RaporGrubu.choices
    )
    # Gelir tablosu/bilanço satır kodu (CSV'de "-" => boş string saklanır).
    rapor_kalemi = models.CharField("rapor kalemi", max_length=20, blank=True)
    # Parasal mı? (USD bilanço için: parasal => kapanış kuru, değil => tarihi kur)
    # Yalnızca bilanço hesaplarında dolu; gelir/maliyet hesaplarında boş (CSV'de "-").
    parasal = models.BooleanField("parasal", null=True, blank=True)
    ust_hesap = models.ForeignKey(
        "self", verbose_name="üst hesap", null=True, blank=True,
        on_delete=models.PROTECT, related_name="alt_hesaplar",
    )
    aktif = models.BooleanField("aktif", default=True)

    class Meta:
        db_table = "hesap_plani"
        verbose_name = "hesap"
        verbose_name_plural = "hesap planı"
        ordering = ["hesap_kodu"]
        indexes = [
            # Fiş listesi aramasında hesap kodu/adı "%...%" (içeren) sorgusu için trigram.
            GinIndex(fields=["hesap_adi"], name="gin_hesap_adi",
                     opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["hesap_kodu"], name="gin_hesap_kodu",
                     opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return f"{self.hesap_kodu} {self.hesap_adi}"


class Kur(TemelModel):
    """Günlük TCMB alış kurları (spec bölüm 2 — Tablo: KUR).

    v0.1'de elle girilir. islem_kuru ve fişin kur_usd alanı buradan beslenir.
    Hafta sonu/tatilde kur yoktur → tüketici tarafta son yayımlanan kur kullanılır.
    """

    tarih = models.DateField("tarih", primary_key=True)
    usd_alis = models.DecimalField("USD alış", max_digits=18, decimal_places=6)
    eur_alis = models.DecimalField("EUR alış", max_digits=18, decimal_places=6, null=True, blank=True)
    gbp_alis = models.DecimalField("GBP alış", max_digits=18, decimal_places=6, null=True, blank=True)
    usd_satis = models.DecimalField("USD MB satış", max_digits=18, decimal_places=6, null=True, blank=True)
    usd_efektif_alis = models.DecimalField("USD MB efektif alış", max_digits=18, decimal_places=6, null=True, blank=True)
    usd_efektif_satis = models.DecimalField("USD MB efektif satış", max_digits=18, decimal_places=6, null=True, blank=True)
    eur_satis = models.DecimalField("EUR MB satış", max_digits=18, decimal_places=6, null=True, blank=True)
    eur_efektif_alis = models.DecimalField("EUR MB efektif alış", max_digits=18, decimal_places=6, null=True, blank=True)
    eur_efektif_satis = models.DecimalField("EUR MB efektif satış", max_digits=18, decimal_places=6, null=True, blank=True)
    gbp_satis = models.DecimalField("GBP MB satış", max_digits=18, decimal_places=6, null=True, blank=True)
    gbp_efektif_alis = models.DecimalField("GBP MB efektif alış", max_digits=18, decimal_places=6, null=True, blank=True)
    gbp_efektif_satis = models.DecimalField("GBP MB efektif satış", max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = "kur"
        verbose_name = "kur"
        verbose_name_plural = "kurlar"
        ordering = ["-tarih"]
        constraints = [
            models.CheckConstraint(condition=models.Q(usd_alis__gt=0),
                                   name="ck_kur_usd_alis_gt0"),
        ]

    def __str__(self):
        return f"{self.tarih} USD={self.usd_alis}"


class YevmiyeFisi(TemelModel):
    """Yevmiye fişi başlığı (spec bölüm 2 — Tablo: YEVMIYE_FISI).

    İç PK ``id`` teknik; insana görünen ``fis_no`` mali yıl içinde müteselsil ve
    boşluksuzdur. İptal edilen (soft-delete) fişin numarası korunur, yeniden
    kullanılmaz. Dengeli fiş kuralı servis katmanında zorlanır.
    """

    class Kaynak(models.TextChoices):
        MANUEL = "MANUEL", "Manuel"

    yil = models.IntegerField("mali yıl")
    fis_no = models.PositiveIntegerField("fiş no")
    tarih = models.DateField("muhasebe tarihi")
    aciklama = models.CharField("açıklama", max_length=500, blank=True)
    kaynak = models.CharField(
        "kaynak", max_length=20, choices=Kaynak.choices, default=Kaynak.MANUEL
    )
    # USD raporlama için fiş tarihindeki TCMB USD alış kuru (snapshot).
    # Kur yoksa boş kalabilir; USD sonra tamamlanır.
    kur_usd = models.DecimalField(
        "USD kuru", max_digits=18, decimal_places=6, null=True, blank=True
    )

    class Meta:
        db_table = "yevmiye_fisi"
        verbose_name = "yevmiye fişi"
        verbose_name_plural = "yevmiye fişleri"
        ordering = ["yil", "fis_no"]
        indexes = [
            models.Index(fields=["tarih"], name="idx_yevmiye_tarih"),
            # Fiş listesi aramasında açıklama "%...%" (içeren) sorgusu için trigram.
            GinIndex(fields=["aciklama"], name="gin_fis_aciklama",
                     opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["yil", "fis_no"], name="uq_yevmiye_yil_fisno"
            ),
            models.CheckConstraint(
                condition=models.Q(kur_usd__isnull=True) | models.Q(kur_usd__gt=0),
                name="ck_fis_kur_usd_gt0",
            ),
        ]

    def __str__(self):
        return f"{self.yil}/{self.fis_no}"


class YevmiyeSatir(TemelModel):
    """Yevmiye satırı (spec bölüm 2 — Tablo: YEVMIYE_SATIR).

    ``borc``/``alacak`` her zaman TL (fonksiyonel). Yabancı işlemde TL,
    ``islem_tutari × islem_kuru``'dan türetilir; TRY'de islem_kuru=1.
    """

    class IslemPB(models.TextChoices):
        TRY = "TRY", "TRY"
        USD = "USD", "USD"
        EUR = "EUR", "EUR"
        GBP = "GBP", "GBP"

    fis = models.ForeignKey(
        YevmiyeFisi, verbose_name="fiş", related_name="satirlar",
        on_delete=models.CASCADE,
    )
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="hesap", related_name="satirlar",
        on_delete=models.PROTECT,
    )
    borc = models.DecimalField("borç (TL)", max_digits=18, decimal_places=2, default=0)
    alacak = models.DecimalField("alacak (TL)", max_digits=18, decimal_places=2, default=0)
    islem_pb = models.CharField(
        "işlem PB", max_length=3, choices=IslemPB.choices, default=IslemPB.TRY
    )
    islem_tutari = models.DecimalField(
        "işlem tutarı", max_digits=18, decimal_places=2
    )
    islem_kuru = models.DecimalField(
        "işlem kuru", max_digits=18, decimal_places=6
    )
    aciklama = models.CharField("açıklama", max_length=500, blank=True)

    class Meta:
        db_table = "yevmiye_satir"
        verbose_name = "yevmiye satırı"
        verbose_name_plural = "yevmiye satırları"
        ordering = ["fis", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(borc__gte=0),
                                   name="ck_satir_borc_gte0"),
            models.CheckConstraint(condition=models.Q(alacak__gte=0),
                                   name="ck_satir_alacak_gte0"),
            # Bir satır ya borç ya alacak olur; ikisi birden pozitif olamaz.
            models.CheckConstraint(condition=models.Q(borc=0) | models.Q(alacak=0),
                                   name="ck_satir_tek_taraf"),
            models.CheckConstraint(condition=models.Q(islem_tutari__gte=0),
                                   name="ck_satir_islem_tutari_gte0"),
            models.CheckConstraint(condition=models.Q(islem_kuru__gt=0),
                                   name="ck_satir_islem_kuru_gt0"),
        ]

    def __str__(self):
        return f"{self.fis} {self.hesap_id} B={self.borc} A={self.alacak}"


class Profil(TemelModel):
    """Kullanıcıya ek alanlar (Django User'da olmayan): telefon + yönetici işareti.

    TC (kullanıcı adı), isim/soyisim (first/last name), e-posta Django User'dadır;
    burada yalnızca ek alanlar tutulur. Aktif/pasif = User.is_active.
    """

    kullanici = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="profil", verbose_name="kullanıcı",
    )
    telefon = models.CharField("telefon", max_length=20, blank=True)
    yonetici = models.BooleanField("yönetici", default=False)

    class Meta:
        db_table = "profil"
        verbose_name = "profil"
        verbose_name_plural = "profiller"

    def __str__(self):
        return f"{self.kullanici_id} profil"




class EkranYetki(TemelModel):
    """Kullanıcının görebileceği bir EKRAN (moduller.py'deki Ekran.kod).

    Güvenli varsayılan: satır VARSA kullanıcı o ekranı görür; YOKSA göremez.
    Yeni kullanıcıda hiç satır olmadığından tüm ekranlar kapalıdır.
    Yönetici (superuser/profil.yonetici) bu tablodan bağımsız her şeyi görür.
    """

    kullanici = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="ekran_yetkileri", verbose_name="kullanıcı",
    )
    ekran_kod = models.CharField("ekran kodu", max_length=50)

    class Meta:
        db_table = "ekran_yetki"
        verbose_name = "ekran yetkisi"
        verbose_name_plural = "ekran yetkileri"
        constraints = [
            models.UniqueConstraint(
                fields=["kullanici", "ekran_kod"], name="uq_ekran_yetki"
            )
        ]

    def __str__(self):
        return f"{self.kullanici_id}:{self.ekran_kod}"


class Birim(TemelModel):
    """Stok birimi (STOKLAR modülü). Ondalık hane: KG=3 (1,250 kg), ADET=0 (tam).

    İleride stok kartı bu birime bağlanacak; o yüzden audit + soft-delete baştan tutulur.
    """

    ad = models.CharField("ad", max_length=50)
    kisa_ad = models.CharField("kısa ad", max_length=10)
    ondalik = models.PositiveSmallIntegerField("ondalık hane", default=0)

    class Meta:
        db_table = "birim"
        verbose_name = "birim"
        verbose_name_plural = "birimler"
        ordering = ["ad"]
        constraints = [
            models.CheckConstraint(condition=models.Q(ondalik__lte=6),
                                   name="ck_birim_ondalik_0_6"),
            # Ad ve kısa ad silinmemişler arasında benzersiz (kısmi unique).
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_birim_ad_aktif"),
            models.UniqueConstraint(fields=["kisa_ad"], condition=models.Q(silindi=False),
                                    name="uq_birim_kisa_ad_aktif"),
        ]

    def __str__(self):
        return f"{self.ad} ({self.kisa_ad})"


class Kategori(TemelModel):
    """Stok kategorisi (STOKLAR modülü). İki seviye: ÜST kategori (ust=None) →
    ALT kategori (ust=bir ÜST kategori).

    Stok kartı sonraki aşamada ALT kategoriye açılacak. ``kod`` elle girilir ve
    silinmemişler arasında benzersizdir. Muhasebe hesabı bağı artık tekil değil:
    ALT kategori × fatura tipi → yaprak hesap haritası ``KategoriHesap``'ta tutulur.
    İki seviye sınırı, kod benzersizliği ve yaprak kuralı servis katmanında zorlanır.
    """

    ad = models.CharField("ad", max_length=100)
    # default="" yalnız migration kolaylığı için; servis boş/benzersiz olmayan kodu reddeder.
    kod = models.CharField("kod", max_length=30, default="")
    ust = models.ForeignKey(
        "self", verbose_name="üst kategori", null=True, blank=True,
        on_delete=models.PROTECT, related_name="alt_kategoriler",
    )

    class Meta:
        db_table = "kategori"
        verbose_name = "kategori"
        verbose_name_plural = "kategoriler"
        ordering = ["ad"]
        constraints = [
            # Kod, bağlı olduğu ÜST grubun içinde benzersiz (kardeşler arası); kök
            # kategoriler kendi arasında. ust=NULL'lar da çakışsın diye nulls_distinct=False
            # (PG15+). Böylece farklı üstler altında aynı kod (örn. her grupta 10) kullanılabilir.
            models.UniqueConstraint(
                fields=["ust", "kod"], condition=models.Q(silindi=False),
                nulls_distinct=False, name="uq_kategori_ust_kod_aktif"),
        ]

    def __str__(self):
        return self.ad


class FaturaTipi(TemelModel):
    """Fatura tipi (STOKLAR) — yönetilebilir liste. Satış/alış faturalarının türleri;
    Faz 2'de ALT kategori × fatura tipi → muhasebe hesabı haritası bunlara bağlanacak.

    Ad silinmemişler arasında benzersiz (kısmi unique). Sıra menü/listede gösterim
    düzenini verir (satış 10'lar, alış 50'ler gibi).
    """

    class Yon(models.TextChoices):
        SATIS = "SATIS", "Satış"
        ALIS = "ALIS", "Alış"

    ad = models.CharField("ad", max_length=100)
    yon = models.CharField("yön", max_length=5, choices=Yon.choices)
    sira = models.PositiveSmallIntegerField("sıra", default=0)

    class Meta:
        db_table = "fatura_tipi"
        verbose_name = "fatura tipi"
        verbose_name_plural = "fatura tipleri"
        ordering = ["sira", "ad"]
        constraints = [
            models.UniqueConstraint(
                fields=["ad"], condition=models.Q(silindi=False),
                name="uq_fatura_tipi_ad_aktif"),
        ]

    def __str__(self):
        return self.ad


class KategoriHesap(TemelModel):
    """ALT kategori × fatura tipi → muhasebe (yaprak) hesabı haritası.

    Her ALT kategori, her fatura tipi için (Satış Faturası, Alış Faturası-Gider, …)
    farklı bir muhasebe hesabına bağlanabilir. Bir (kategori, fatura_tipi) çifti için
    en fazla bir kayıt (unique). "Bağ kaldır" = soft-delete; yeniden bağlanınca aynı
    satır canlanır (servis update_or_create mantığı). Yaprak hesap kuralı serviste.
    """

    kategori = models.ForeignKey(
        Kategori, verbose_name="kategori", related_name="hesap_baglari",
        on_delete=models.CASCADE,
    )
    fatura_tipi = models.ForeignKey(
        FaturaTipi, verbose_name="fatura tipi", related_name="kategori_baglari",
        on_delete=models.PROTECT,
    )
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", related_name="kategori_baglari",
        on_delete=models.PROTECT,
    )

    class Meta:
        db_table = "kategori_hesap"
        verbose_name = "kategori hesap bağı"
        verbose_name_plural = "kategori hesap bağları"
        constraints = [
            models.UniqueConstraint(
                fields=["kategori", "fatura_tipi"], name="uq_kategori_fatura_tipi"),
        ]

    def __str__(self):
        return f"{self.kategori_id}:{self.fatura_tipi_id} -> {self.hesap_id}"


class Stok(TemelModel):
    """Stok/ürün kartı (STOKLAR — master). Miktar BURADA tutulmaz (bakiye saklanmaz
    ilkesi); eldeki miktar ileride stok hareketlerinden hesaplanacak.

    ``kod`` otomatik üretilir: ``ÜST.kod-ALT.kod-NNNN`` (örn. 150-10-0001); sıra her ALT
    kategori içinde ayrı ilerler (servis). Kart bir ALT kategoriye bağlıdır; muhasebe
    hesapları kartta DEĞİL, o kategorinin fatura-tipi haritasından gelir. Üretim ve fatura
    birimi farklı olabilir: ``cevirici`` = 1 üretim birimi kaç fatura birimi eder.
    Kod ve kategori oluşturmadan sonra DEĞİŞMEZ (servis zorlar).
    """

    kod = models.CharField("kod", max_length=40)
    ad = models.CharField("ad", max_length=200)
    kategori = models.ForeignKey(
        Kategori, verbose_name="alt kategori", related_name="stoklar",
        on_delete=models.PROTECT,
    )
    uretim_birimi = models.ForeignKey(
        Birim, verbose_name="üretim birimi", related_name="uretim_stoklari",
        on_delete=models.PROTECT,
    )
    fatura_birimi = models.ForeignKey(
        Birim, verbose_name="fatura birimi", related_name="fatura_stoklari",
        on_delete=models.PROTECT,
    )
    # 1 üretim birimi = cevirici × fatura birimi.
    cevirici = models.DecimalField("çevirici", max_digits=18, decimal_places=6, default=1)
    kdv_orani = models.DecimalField("KDV oranı (%)", max_digits=5, decimal_places=2, default=0)
    tevkifat_orani = models.DecimalField("tevkifat oranı (%)", max_digits=5,
                                         decimal_places=2, default=0)
    # Kritik stok seviyesi: üretim biriminde miktar eşiği (Faz B'de uyarı için).
    kritik_stok = models.DecimalField("kritik stok seviyesi", max_digits=18,
                                      decimal_places=3, default=0)
    # Tedarikçi: Cari modülü henüz yok -> şimdilik serbest metin (ileride FK olacak).
    tedarikci = models.CharField("tedarikçi (cari)", max_length=200, blank=True)

    class Meta:
        db_table = "stok"
        verbose_name = "stok"
        verbose_name_plural = "stoklar"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_stok_kod_aktif"),
            models.CheckConstraint(condition=models.Q(cevirici__gt=0),
                                   name="ck_stok_cevirici_gt0"),
            models.CheckConstraint(condition=models.Q(kdv_orani__gte=0),
                                   name="ck_stok_kdv_gte0"),
            models.CheckConstraint(condition=models.Q(tevkifat_orani__gte=0),
                                   name="ck_stok_tevkifat_gte0"),
            models.CheckConstraint(condition=models.Q(kritik_stok__gte=0),
                                   name="ck_stok_kritik_gte0"),
        ]

    def __str__(self):
        return f"{self.kod} {self.ad}"


# === CARİLER modülü — Ülke / Şehir (lokasyon master data) ===
class Ulke(TemelModel):
    """ISO 3166-1 ülke. ``kod`` 2 harf (TR, DE…), silinmemişler arası benzersiz."""

    kod = models.CharField("ISO kod", max_length=2)
    ad = models.CharField("ad", max_length=80)
    ad_en = models.CharField("İngilizce ad", max_length=80, blank=True)

    class Meta:
        db_table = "ulke"
        verbose_name = "ülke"
        verbose_name_plural = "ülkeler"
        ordering = ["ad"]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_ulke_kod_aktif"),
        ]

    def __str__(self):
        return self.ad


class Sehir(TemelModel):
    """Şehir — ülkeye bağlı. ``kod`` plaka/kod (opsiyonel). Ad, ülke içinde benzersiz."""

    ulke = models.ForeignKey(
        Ulke, verbose_name="ülke", related_name="sehirler", on_delete=models.PROTECT)
    kod = models.CharField("plaka/kod", max_length=10, blank=True)
    ad = models.CharField("ad", max_length=80)
    ad_en = models.CharField("İngilizce ad", max_length=80, blank=True)

    class Meta:
        db_table = "sehir"
        verbose_name = "şehir"
        verbose_name_plural = "şehirler"
        ordering = ["ulke", "ad"]
        constraints = [
            models.UniqueConstraint(fields=["ulke", "ad"], condition=models.Q(silindi=False),
                                    name="uq_sehir_ulke_ad_aktif"),
        ]

    def __str__(self):
        return f"{self.ad} ({self.ulke.kod})"


class CariKategori(TemelModel):
    """Cari kategorisi (CARİLER) — 2 seviye: ÜST (ust=None) → ALT. Kod ve ad, bağlı olduğu
    üst grup içinde benzersiz. ``kod_yolu`` üstten alta kodları '-' ile birleştirir
    (örn. 320-10) — cari kodu Faz 3'te bundan türetilecek.
    """

    ad = models.CharField("ad", max_length=100)
    kod = models.CharField("kod", max_length=10)
    ust = models.ForeignKey(
        "self", verbose_name="üst kategori", null=True, blank=True,
        on_delete=models.PROTECT, related_name="alt_kategoriler")

    class Meta:
        db_table = "cari_kategori"
        verbose_name = "cari kategori"
        verbose_name_plural = "cari kategorileri"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(
                fields=["ust", "kod"], condition=models.Q(silindi=False),
                nulls_distinct=False, name="uq_carikat_ust_kod_aktif"),
            models.UniqueConstraint(
                fields=["ust", "ad"], condition=models.Q(silindi=False),
                nulls_distinct=False, name="uq_carikat_ust_ad_aktif"),
        ]

    def __str__(self):
        return self.ad

    @property
    def kod_yolu(self):
        parcalar, k = [], self
        while k is not None:
            if k.kod:
                parcalar.insert(0, k.kod)
            k = k.ust
        return "-".join(parcalar)


class Cari(TemelModel):
    """Cari kartı (CARİLER) — müşteri/tedarikçi. ``kod`` kategori kod yolundan otomatik
    (örn. 320-10-0001), kategorisizse CAR-NNNN. Ödeme şekli/vade tipi v0.1'de YOK.
    Cari hesap hareketi/ekstre Faz 4 (finans gerektirir).
    """

    PARA_CHOICES = YevmiyeSatir.IslemPB.choices   # TRY/USD/EUR/GBP

    # Kimlik
    kod = models.CharField("cari kodu", max_length=30)
    # Hesap planındaki muhasebe hesap kodu (kod'un noktalı hâli, örn. 320.10.0003).
    # Servis cari kodundan türetir ve hesap planında otomatik açar.
    muhasebe_kodu = models.CharField("muhasebe kodu", max_length=40, blank=True, default="")
    unvan = models.CharField("unvan / ad soyad", max_length=200)
    kisa_ad = models.CharField("kısa ad", max_length=80, blank=True)
    kategori = models.ForeignKey(
        CariKategori, verbose_name="kategori", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cariler")
    # Vergi
    vergi_dairesi = models.CharField("vergi dairesi", max_length=100, blank=True)
    vkn_tckn = models.CharField("VKN / TCKN", max_length=15, blank=True, db_index=True)
    tax_id = models.CharField("Tax ID (yurtdışı)", max_length=30, blank=True, db_index=True)
    # İletişim
    telefon = models.CharField("telefon", max_length=20, blank=True)
    telefon_2 = models.CharField("telefon 2", max_length=20, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    web = models.URLField("web", blank=True)
    kep_adresi = models.CharField("KEP", max_length=100, blank=True)
    # Ana adres
    ulke = models.ForeignKey(
        Ulke, verbose_name="ülke", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cariler")
    sehir = models.ForeignKey(
        Sehir, verbose_name="şehir", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cariler")
    adres = models.TextField("adres", blank=True)
    posta_kodu = models.CharField("posta kodu", max_length=15, blank=True)
    # Sevk adresi (farklıysa)
    sevk_farkli = models.BooleanField("sevk adresi farklı", default=False)
    sevk_ulke = models.ForeignKey(
        Ulke, verbose_name="sevk ülke", null=True, blank=True,
        on_delete=models.PROTECT, related_name="sevk_cariler")
    sevk_sehir = models.ForeignKey(
        Sehir, verbose_name="sevk şehir", null=True, blank=True,
        on_delete=models.PROTECT, related_name="sevk_cariler")
    sevk_adres = models.TextField("sevk adres", blank=True)
    sevk_posta_kodu = models.CharField("sevk posta kodu", max_length=15, blank=True)
    # Ticari
    para_birimi = models.CharField("para birimi", max_length=3, choices=PARA_CHOICES, default="TRY")
    kredi_limiti = models.DecimalField("kredi/risk limiti", max_digits=14, decimal_places=2, default=0)
    iskonto_yuzdesi = models.DecimalField("varsayılan iskonto %", max_digits=5, decimal_places=2, default=0)
    notlar = models.TextField("notlar", blank=True)

    class Meta:
        db_table = "cari"
        verbose_name = "cari"
        verbose_name_plural = "cariler"
        ordering = ["unvan"]
        indexes = [models.Index(fields=["unvan"])]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_cari_kod_aktif"),
            models.UniqueConstraint(
                fields=["vkn_tckn"],
                condition=models.Q(silindi=False) & ~models.Q(vkn_tckn=""),
                name="uq_cari_vkn_dolu"),
            models.UniqueConstraint(
                fields=["tax_id"],
                condition=models.Q(silindi=False) & ~models.Q(tax_id=""),
                name="uq_cari_taxid_dolu"),
        ]

    def __str__(self):
        return f"{self.kod} — {self.unvan}" if self.kod else self.unvan


class CariBanka(TemelModel):
    """Cariye ait banka hesabı (çoklu)."""

    cari = models.ForeignKey(Cari, verbose_name="cari", related_name="banka_hesaplari",
                             on_delete=models.CASCADE)
    banka_adi = models.CharField("banka", max_length=100)
    hesap_sahibi = models.CharField("hesap sahibi", max_length=200, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    swift = models.CharField("SWIFT/BIC", max_length=15, blank=True)
    para_birimi = models.CharField("para birimi", max_length=3,
                                   choices=YevmiyeSatir.IslemPB.choices, default="TRY")
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    varsayilan = models.BooleanField("varsayılan", default=False)

    class Meta:
        db_table = "cari_banka"
        verbose_name = "banka hesabı"
        verbose_name_plural = "banka hesapları"
        ordering = ["-varsayilan", "banka_adi"]

    def __str__(self):
        return f"{self.banka_adi} — {self.iban}"


class CariYetkili(TemelModel):
    """Cariye ait yetkili kişi (çoklu)."""

    cari = models.ForeignKey(Cari, verbose_name="cari", related_name="yetkililer",
                             on_delete=models.CASCADE)
    ad_soyad = models.CharField("ad soyad", max_length=120)
    unvan = models.CharField("görev/unvan", max_length=80, blank=True)
    telefon = models.CharField("telefon", max_length=20, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    notlar = models.CharField("notlar", max_length=200, blank=True)

    class Meta:
        db_table = "cari_yetkili"
        verbose_name = "yetkili kişi"
        verbose_name_plural = "yetkili kişiler"
        ordering = ["ad_soyad"]

    def __str__(self):
        return self.ad_soyad


# === AYARLAR > Tanım Listeleri (KDV / Tevkifat oranları) ===
class KdvOrani(TemelModel):
    """KDV oranı tanımı — otomatik yevmiyede indirilecek/hesaplanan KDV hesabını besler."""

    sira = models.PositiveSmallIntegerField("sıra", default=0)
    aciklama = models.CharField("açıklama", max_length=100)
    oran = models.DecimalField("KDV oranı (%)", max_digits=5, decimal_places=2)
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kdv_oranlari")

    class Meta:
        db_table = "kdv_orani"
        verbose_name = "KDV oranı"
        verbose_name_plural = "KDV oranları"
        ordering = ["sira", "oran"]
        constraints = [
            models.CheckConstraint(condition=models.Q(oran__gte=0),
                                   name="ck_kdv_orani_gte0"),
        ]

    def __str__(self):
        return f"%{self.oran} {self.aciklama}"


class TevkifatOrani(TemelModel):
    """Tevkifat oranı tanımı (pay/payda, örn. 5/10) — otomatik yevmiyede tevkifat hesabını besler."""

    kod = models.CharField("kod", max_length=20)
    pay = models.PositiveSmallIntegerField("pay")
    payda = models.PositiveSmallIntegerField("payda")
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="tevkifat_oranlari")

    class Meta:
        db_table = "tevkifat_orani"
        verbose_name = "tevkifat oranı"
        verbose_name_plural = "tevkifat oranları"
        ordering = ["kod"]
        constraints = [
            models.CheckConstraint(condition=models.Q(pay__gte=0),
                                   name="ck_tevkifat_pay_gte0"),
            models.CheckConstraint(condition=models.Q(payda__gt=0),
                                   name="ck_tevkifat_payda_gt0"),
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_tevkifat_kod_aktif"),
        ]

    def __str__(self):
        return f"{self.kod} ({self.pay}/{self.payda})"
