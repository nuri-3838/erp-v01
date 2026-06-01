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
    aktif = models.BooleanField("aktif", default=True)

    class Meta:
        db_table = "birim"
        verbose_name = "birim"
        verbose_name_plural = "birimler"
        ordering = ["ad"]
        constraints = [
            models.CheckConstraint(condition=models.Q(ondalik__lte=6),
                                   name="ck_birim_ondalik_0_6"),
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
    aktif = models.BooleanField("aktif", default=True)

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
