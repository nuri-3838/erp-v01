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
    # Hiyerarşi TEK kaynaktan: hesap_kodu metni (320.10.0001 -> üst 320.10 -> 320).
    # Ayrı bir ust_hesap FK YOK (kaldırıldı); üst/alt her zaman koddan türetilir.
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
        FATURA = "FATURA", "Fatura (otomatik)"
        KASA = "KASA", "Kasa Hareketi (otomatik)"
        BANKA = "BANKA", "Banka Hareketi (otomatik)"
        CEK_SENET = "CEK_SENET", "Çek/Senet Bordrosu (otomatik)"

    yil = models.IntegerField("mali yıl")
    fis_no = models.PositiveIntegerField("fiş no")
    tarih = models.DateField("muhasebe tarihi")
    aciklama = models.CharField("açıklama", max_length=500, blank=True)
    kaynak = models.CharField(
        "kaynak", max_length=20, choices=Kaynak.choices, default=Kaynak.MANUEL
    )
    # Kaynak=KASA fişin kaynağı olan kasa (hareket motoru). Ham fiş ekranından
    # düzenleme/iptal kilidi + kasa detayından iptal için fiş→kasa bağı.
    kasa = models.ForeignKey(
        "Kasa", verbose_name="kaynak kasa", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
    )
    # Kaynak=BANKA fişin kaynağı olan banka hesabı (hareket motoru); kasa ile aynı amaç.
    banka_hesap = models.ForeignKey(
        "BankaHesap", verbose_name="kaynak banka hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
    )
    # Kaynak=CEK_SENET fişin kaynağı olan çek/senet bordrosu (bordro başına TEK fiş).
    cek_bordrosu = models.ForeignKey(
        "CekBordrosu", verbose_name="kaynak çek/senet bordrosu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
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
    # KDV/tevkifat artık serbest sayı değil; AYARLAR tanım listelerine FK (otomatik
    # yevmiye buradan muhasebe hesabını/oranı okur). Opsiyonel.
    kdv = models.ForeignKey(
        "KdvOrani", verbose_name="KDV oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stoklar")
    tevkifat = models.ForeignKey(
        "TevkifatOrani", verbose_name="tevkifat oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stoklar")
    # Kritik stok seviyesi: üretim biriminde miktar eşiği (Faz B'de uyarı için).
    kritik_stok = models.DecimalField("kritik stok seviyesi", max_digits=18,
                                      decimal_places=3, default=0)
    # Tedarikçi: Cari modülüne FK (opsiyonel). Eski serbest metin kaldırıldı.
    tedarikci = models.ForeignKey(
        "Cari", verbose_name="tedarikçi (cari)", null=True, blank=True,
        on_delete=models.PROTECT, related_name="tedarik_stoklari")

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
    # Borç = İndirilecek KDV (191, alış); Alacak = Hesaplanan KDV (391, satış).
    hesap_borc = models.ForeignKey(
        HesapPlani, verbose_name="borç hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kdv_borc_oranlari")
    hesap_alacak = models.ForeignKey(
        HesapPlani, verbose_name="alacak hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kdv_alacak_oranlari")

    class Meta:
        db_table = "kdv_orani"
        verbose_name = "KDV oranı"
        verbose_name_plural = "KDV oranları"
        ordering = ["sira", "oran"]
        constraints = [
            models.CheckConstraint(condition=models.Q(oran__gte=0),
                                   name="ck_kdv_orani_gte0"),
            # Aynı oran (ör. %20) iki kez tanımlanamaz; otomatik yevmiyede orana
            # göre eşleştirmede belirsizlik olmasın diye benzersiz.
            models.UniqueConstraint(fields=["oran"], condition=models.Q(silindi=False),
                                    name="uq_kdv_oran_aktif"),
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


# === FATURALAR — Alış/Satış faturası (otomatik yevmiye üretir) ===
class Fatura(TemelModel):
    """Alış/Satış faturası başlığı. Kaydında otomatik DENGELİ yevmiye fişi üretilir
    ve `fis`'e bağlanır (servis: fatura_olustur). Yön (alış/satış) `tip`ten gelir.

    Muhasebe haritası: mal/gelir hesabı = stok kategorisi × fatura tipi (KategoriHesap);
    KDV hesabı = stoğun KDV oranının borç (alış 191) / alacak (satış 391) hesabı;
    karşı taraf = carinin muhasebe hesabı. Tutarlar satırlardan hesaplanır (saklanmaz)."""

    tip = models.ForeignKey(
        FaturaTipi, verbose_name="fatura tipi", related_name="faturalar",
        on_delete=models.PROTECT)
    cari = models.ForeignKey(
        Cari, verbose_name="cari", related_name="faturalar", on_delete=models.PROTECT)
    tarih = models.DateField("fatura tarihi")
    fatura_no = models.CharField("fatura no", max_length=50, blank=True)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    kur = models.DecimalField("kur (TL)", max_digits=18, decimal_places=6, default=1)
    fis = models.ForeignKey(
        YevmiyeFisi, verbose_name="yevmiye fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="faturalar")
    # Stok hareketlerinin yazılacağı depo (alış→giriş, satış→çıkış). Boşsa hareket üretilmez.
    depo = models.ForeignKey(
        "Depo", verbose_name="depo", null=True, blank=True,
        on_delete=models.PROTECT, related_name="faturalar")

    class Meta:
        db_table = "fatura"
        verbose_name = "fatura"
        verbose_name_plural = "faturalar"
        ordering = ["-tarih", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(kur__gt=0), name="ck_fatura_kur_gt0"),
        ]

    def __str__(self):
        return f"{self.tip_id} {self.fatura_no} ({self.cari_id})"

    # --- Görüntüleme toplamları (fatura para biriminde; saklanmaz, satırdan) ---
    @property
    def ara_toplam(self):
        from decimal import Decimal
        return sum((s.tutar for s in self.satirlar.filter(silindi=False)), Decimal("0.00"))

    @property
    def kdv_toplam(self):
        from decimal import Decimal
        return sum((s.kdv_tutari for s in self.satirlar.filter(silindi=False)), Decimal("0.00"))

    @property
    def tevkifat_toplam(self):
        from decimal import Decimal
        return sum((s.tevkifat_tutari for s in self.satirlar.filter(silindi=False)), Decimal("0.00"))

    @property
    def genel_toplam(self):
        """KDV dahil brüt (mal + KDV)."""
        return self.ara_toplam + self.kdv_toplam

    @property
    def odenecek(self):
        """Carinin borç/alacağı = mal + KDV − tevkifat (tevkifat karşı tarafa ödenmez)."""
        return self.genel_toplam - self.tevkifat_toplam


class FaturaSatir(TemelModel):
    """Fatura kalemi: stok × miktar × birim fiyat (+ KDV oranı snapshot)."""

    fatura = models.ForeignKey(
        Fatura, verbose_name="fatura", related_name="satirlar", on_delete=models.CASCADE)
    stok = models.ForeignKey(
        Stok, verbose_name="stok", related_name="fatura_satirlari", on_delete=models.PROTECT)
    miktar = models.DecimalField("miktar", max_digits=18, decimal_places=3)
    birim_fiyat = models.DecimalField("birim fiyat", max_digits=18, decimal_places=6)
    # KDV oranı snapshot (fatura anındaki); stok sonradan değişse fatura korunur.
    kdv = models.ForeignKey(
        KdvOrani, verbose_name="KDV oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fatura_satirlari")
    # Tevkifat oranı snapshot (varsa). Alışta KDV'nin pay/payda kadarı 360'a alacak;
    # satışta Hesaplanan KDV o kadar azalır.
    tevkifat = models.ForeignKey(
        TevkifatOrani, verbose_name="tevkifat oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fatura_satirlari")

    class Meta:
        db_table = "fatura_satir"
        verbose_name = "fatura satırı"
        verbose_name_plural = "fatura satırları"
        ordering = ["fatura", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0), name="ck_fatura_satir_miktar_gt0"),
            models.CheckConstraint(condition=models.Q(birim_fiyat__gte=0), name="ck_fatura_satir_fiyat_gte0"),
        ]

    def __str__(self):
        return f"{self.stok_id} x {self.miktar}"

    @property
    def tutar(self):
        from core.sayi import yuvarla
        return yuvarla(self.miktar * self.birim_fiyat, 2)

    @property
    def kdv_tutari(self):
        from decimal import Decimal
        from core.sayi import yuvarla
        oran = self.kdv.oran if self.kdv_id else Decimal("0")
        return yuvarla(self.tutar * oran / Decimal("100"), 2)

    @property
    def tevkifat_tutari(self):
        """KDV'nin tevkifata düşen (alınan/ödenen) kısmı = KDV × pay/payda."""
        from decimal import Decimal
        from core.sayi import yuvarla
        if not self.tevkifat_id or not self.tevkifat.payda:
            return Decimal("0.00")
        return yuvarla(self.kdv_tutari * Decimal(self.tevkifat.pay)
                       / Decimal(self.tevkifat.payda), 2)


# === STOKLAR Faz B — Depo (çok depo) ===
class Depo(TemelModel):
    """Stok deposu. Çok depo destekli; eldeki miktar depo bazında hareketlerden
    HESAPLANIR (saklanmaz). Kod elle, ad+kod silinmemişler arasında benzersiz."""

    kod = models.CharField("kod", max_length=20)
    ad = models.CharField("ad", max_length=100)

    class Meta:
        db_table = "depo"
        verbose_name = "depo"
        verbose_name_plural = "depolar"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_depo_kod_aktif"),
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_depo_ad_aktif"),
        ]

    def __str__(self):
        return f"{self.kod} {self.ad}"


class StokHareket(TemelModel):
    """Stok miktar hareketi (giriş/çıkış). Eldeki miktar = Σgiriş − Σçıkış (saklanmaz).
    Miktar üretim biriminde, daima pozitif; yön ``tur`` ile. Muhasebeden BAĞIMSIZ
    (TL tarafını fatura işler) — bu defter yalnız MİKTAR izler."""

    class Tur(models.TextChoices):
        GIRIS = "GIRIS", "Giriş"
        CIKIS = "CIKIS", "Çıkış"

    class Kaynak(models.TextChoices):
        MANUEL = "MANUEL", "Manuel"
        FATURA = "FATURA", "Fatura"

    stok = models.ForeignKey(
        Stok, verbose_name="stok", related_name="hareketler", on_delete=models.PROTECT)
    depo = models.ForeignKey(
        Depo, verbose_name="depo", related_name="hareketler", on_delete=models.PROTECT)
    tarih = models.DateField("tarih")
    tur = models.CharField("tür", max_length=5, choices=Tur.choices)
    miktar = models.DecimalField("miktar", max_digits=18, decimal_places=3)
    aciklama = models.CharField("açıklama", max_length=300, blank=True)
    # Faturadan otomatik üretilen hareketler bu kaleme bağlanır (iptal/güncellemede izlenir).
    fatura_satir = models.ForeignKey(
        "FaturaSatir", verbose_name="kaynak fatura satırı", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stok_hareketleri")
    kaynak = models.CharField("kaynak", max_length=20, choices=Kaynak.choices,
                              default=Kaynak.MANUEL)

    class Meta:
        db_table = "stok_hareket"
        verbose_name = "stok hareketi"
        verbose_name_plural = "stok hareketleri"
        ordering = ["-tarih", "-id"]
        indexes = [models.Index(fields=["stok", "depo"], name="idx_stokhareket_stok_depo")]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0),
                                   name="ck_stok_hareket_miktar_gt0"),
        ]

    def __str__(self):
        return f"{self.stok_id} {self.tur} {self.miktar}"


# === FİNANS modülü — tanımlar (Kasa/Banka/Kredi/Kredi Kartı) ===
# Her finans hesabı bir YAPRAK muhasebe hesabına bağlanır; bakiye SAKLANMAZ,
# o hesabın yevmiyesinden hesaplanır (cari/ekstre mantığı). İşlem motoru yok.
class Kasa(TemelModel):
    """Kasa tanımı. Bakiye bağlı muhasebe hesabının yevmiyesinden gelir (saklanmaz)."""

    ad = models.CharField("kasa adı", max_length=100)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    muhasebe = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", on_delete=models.PROTECT,
        related_name="kasalar")

    class Meta:
        db_table = "kasa"
        verbose_name = "kasa"
        verbose_name_plural = "kasalar"
        ordering = ["ad"]
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_kasa_ad_aktif"),
        ]

    def __str__(self):
        return self.ad


class Banka(TemelModel):
    """Banka (kurum). Altındaki hesaplar BankaHesap'ta; muhasebe hesabı HESAP düzeyinde."""

    ad = models.CharField("banka adı", max_length=150)
    kisa_ad = models.CharField("kısa ad", max_length=50, blank=True, default="")
    sube = models.CharField("şube", max_length=100, blank=True, default="")
    swift_kod = models.CharField("SWIFT/BIC", max_length=11, blank=True, default="")
    musteri_no = models.CharField("müşteri no", max_length=50, blank=True, default="")
    adres = models.CharField("adres", max_length=255, blank=True, default="")
    logo = models.ImageField("logo", upload_to="banka_logo/", blank=True, null=True)

    class Meta:
        db_table = "finans_banka"
        verbose_name = "banka"
        verbose_name_plural = "bankalar"
        ordering = ["ad"]
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_banka_ad_aktif"),
        ]

    def __str__(self):
        return self.ad


class BankaHesap(TemelModel):
    """Bir bankaya bağlı hesap. Bakiye bağlı yaprak muhasebe hesabından (saklanmaz)."""

    banka = models.ForeignKey(Banka, verbose_name="banka", on_delete=models.CASCADE,
                              related_name="hesaplar")
    ad = models.CharField("hesap adı", max_length=100)
    hesap_no = models.CharField("hesap no", max_length=40, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    muhasebe = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", on_delete=models.PROTECT,
        related_name="banka_hesaplari")

    class Meta:
        db_table = "finans_banka_hesap"
        verbose_name = "banka hesabı"
        verbose_name_plural = "banka hesapları"
        ordering = ["banka", "ad"]
        constraints = [
            models.UniqueConstraint(fields=["banka", "ad"], condition=models.Q(silindi=False),
                                    name="uq_banka_hesap_ad_aktif"),
        ]

    def __str__(self):
        return f"{self.banka_id} {self.ad}"


class KrediKarti(TemelModel):
    """Kredi kartı tanımı. Bakiye muhasebe hesabından (saklanmaz). Kart no yalnız son 4."""

    ad = models.CharField("kart adı", max_length=100)
    banka_adi = models.CharField("banka adı", max_length=150, blank=True)
    kart_son4 = models.CharField("kart no (son 4)", max_length=4, blank=True)
    limit = models.DecimalField("kart limiti", max_digits=14, decimal_places=2, default=0)
    kesim_gunu = models.PositiveSmallIntegerField("hesap kesim günü", null=True, blank=True)
    son_odeme_gunu = models.PositiveSmallIntegerField("son ödeme günü", null=True, blank=True)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    muhasebe = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", on_delete=models.PROTECT,
        related_name="kredi_kartlari")

    class Meta:
        db_table = "finans_kredi_karti"
        verbose_name = "kredi kartı"
        verbose_name_plural = "kredi kartları"
        ordering = ["ad"]
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_kredi_karti_ad_aktif"),
            models.CheckConstraint(condition=models.Q(limit__gte=0),
                                   name="ck_kredi_karti_limit_gte0"),
        ]

    def __str__(self):
        return self.ad


class Kredi(TemelModel):
    """Kredi tanımı. Bakiye (kalan borç) muhasebe hesabından (saklanmaz)."""

    ad = models.CharField("kredi adı", max_length=100)
    banka_adi = models.CharField("banka adı", max_length=150, blank=True)
    anapara = models.DecimalField("anapara", max_digits=14, decimal_places=2, default=0)
    faiz_orani = models.DecimalField("aylık faiz oranı (%)", max_digits=6, decimal_places=4,
                                     default=0)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    muhasebe = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", on_delete=models.PROTECT,
        related_name="krediler")

    class Meta:
        db_table = "finans_kredi"
        verbose_name = "kredi"
        verbose_name_plural = "krediler"
        ordering = ["ad"]
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_kredi_ad_aktif"),
            models.CheckConstraint(condition=models.Q(anapara__gte=0),
                                   name="ck_kredi_anapara_gte0"),
            models.CheckConstraint(condition=models.Q(faiz_orani__gte=0),
                                   name="ck_kredi_faiz_gte0"),
        ]

    def __str__(self):
        return self.ad


def _cek_hesap_fk(adi):
    """CekHesapAyari için yaprak muhasebe hesabına opsiyonel bağ (tekil ayar alanı)."""
    return models.ForeignKey(
        HesapPlani, verbose_name=adi, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")


class CekHesapAyari(TemelModel):
    """Çek/Senet modülü muhasebe hesap eşlemesi — TEKİL ayar kaydı (pk=1).

    Her DURUM için çek ve senet AYRI hesaba bağlanır (alınan çek 101 / senet 121;
    verilen çek 103 / senet 321 gibi). Bordro işlemleri yevmiye fişini bu eşlemeden,
    evrak tipine (çek/senet) bakarak üretir. Boş alan = o durum henüz tanımlanmadı.
    """

    portfoy_cek = _cek_hesap_fk("portföydeki çek hesabı")
    portfoy_senet = _cek_hesap_fk("portföydeki senet hesabı")
    tahsilde_cek = _cek_hesap_fk("bankada tahsildeki çek hesabı")
    tahsilde_senet = _cek_hesap_fk("bankada tahsildeki senet hesabı")
    teminatta_cek = _cek_hesap_fk("bankada teminattaki çek hesabı")
    teminatta_senet = _cek_hesap_fk("bankada teminattaki senet hesabı")
    verilen_cek = _cek_hesap_fk("verilen çek hesabı")
    verilen_senet = _cek_hesap_fk("verilen senet hesabı")

    class Meta:
        db_table = "finans_cek_hesap_ayari"
        verbose_name = "çek/senet hesap ayarı"
        verbose_name_plural = "çek/senet hesap ayarı"

    def __str__(self):
        return "Çek/Senet Hesap Ayarı"

    @classmethod
    def get(cls):
        """Tekil ayar kaydı (yoksa oluşturur)."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CekBordrosu(TemelModel):
    """Çek/Senet bordrosu — toplu işlem belgesi. Bordro başına TEK birleşik yevmiye fişi
    (kaynak=CEK_SENET, fiş→bordro bağı). Giriş bordrosu N adet CekSenet oluşturur."""

    class Tur(models.TextChoices):
        CARI_GIRIS = "CARI_GIRIS", "Cari Giriş"
        FIRMA_CIKIS = "FIRMA_CIKIS", "Firma Çek-Senet"
        CARI_CIRO = "CARI_CIRO", "Cari Ciro"
        BANKA_TAHSIL = "BANKA_TAHSIL", "Banka Tahsil"
        BANKA_TEMINAT = "BANKA_TEMINAT", "Banka Teminat"
        CARI_IADE = "CARI_IADE", "Cari İade"
        BANKA_TAHSIL_IADE = "BANKA_TAHSIL_IADE", "Banka Tahsil İade"
        BANKA_TEMINAT_IADE = "BANKA_TEMINAT_IADE", "Banka Teminat İade"
        TAHSIL = "TAHSIL", "Tahsil Gerçekleşme"
        ODEME = "ODEME", "Firma Çek Ödeme"

    tur = models.CharField("tür", max_length=20, choices=Tur.choices)
    tarih = models.DateField("işlem tarihi")
    aciklama = models.CharField("açıklama", max_length=300, blank=True)
    cari = models.ForeignKey(
        "Cari", verbose_name="cari", null=True, blank=True, on_delete=models.PROTECT,
        related_name="cek_bordrolari")
    banka_hesap = models.ForeignKey(
        "BankaHesap", verbose_name="banka hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cek_bordrolari")
    # Tahsil gerçekleşmede nakit Kasa'ya girdiyse hedef (Banka veya Kasa; ikisinden biri).
    kasa = models.ForeignKey(
        "Kasa", verbose_name="kasa", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cek_bordrolari")

    # Evrak OLUŞTURAN bordro türleri (giriş/çıkış). Diğerleri mevcut evrakı SEÇER (işlem bordrosu).
    GIRIS_TURLERI = (Tur.CARI_GIRIS, Tur.FIRMA_CIKIS)

    class Meta:
        db_table = "finans_cek_bordrosu"
        verbose_name = "çek/senet bordrosu"
        verbose_name_plural = "çek/senet bordroları"
        ordering = ["-tarih", "-id"]

    def __str__(self):
        return f"{self.get_tur_display()} #{self.pk}"

    def evrak_qs(self):
        """Bu bordronun çek/senetleri: giriş/çıkış → oluşturduğu (giris_bordrosu);
        işlem bordrosu → seçtiği (CekBordroSatir)."""
        if self.tur in self.GIRIS_TURLERI:
            return self.cek_senetler.filter(silindi=False)
        return CekSenet.objects.filter(bordro_satirlari__bordro=self,
                                       bordro_satirlari__silindi=False, silindi=False)


class CekSenet(TemelModel):
    """Tek bir çek/senet (kıymetli evrak). Bir GİRİŞ bordrosuyla portföye girer; sonraki
    bordro işlemleriyle durumu değişir. Bakiye saklanmaz; muhasebe hesabı ayar matrisinden."""

    class Tip(models.TextChoices):
        CEK = "CEK", "Çek"
        SENET = "SENET", "Senet"

    class Yon(models.TextChoices):
        ALINAN = "ALINAN", "Alınan"
        VERILEN = "VERILEN", "Verilen"

    class Durum(models.TextChoices):
        PORTFOYDE = "PORTFOYDE", "Portföyde"
        TAHSILDE = "TAHSILDE", "Bankada Tahsilde"
        TEMINATTA = "TEMINATTA", "Bankada Teminatta"
        CIRO = "CIRO", "Ciro Edildi"
        IADE = "IADE", "İade Edildi"
        TAHSIL = "TAHSIL", "Tahsil Edildi"
        VERILDI = "VERILDI", "Verildi"
        ODENDI = "ODENDI", "Ödendi"
        KARSILIKSIZ = "KARSILIKSIZ", "Karşılıksız"

    tip = models.CharField("tip", max_length=5, choices=Tip.choices)
    yon = models.CharField("yön", max_length=7, choices=Yon.choices)
    tutar = models.DecimalField("tutar", max_digits=14, decimal_places=2)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    vade = models.DateField("vade tarihi")
    kesideci = models.CharField("keşideci", max_length=200, blank=True)
    belge_no = models.CharField("belge no", max_length=50, blank=True)
    durum = models.CharField("durum", max_length=12, choices=Durum.choices,
                             default=Durum.PORTFOYDE)
    cari = models.ForeignKey(
        "Cari", verbose_name="cari", null=True, blank=True, on_delete=models.PROTECT,
        related_name="cek_senetler")
    giris_bordrosu = models.ForeignKey(
        CekBordrosu, verbose_name="giriş bordrosu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cek_senetler")
    on_yuz = models.ImageField("ön yüz görseli", upload_to="cek_senet/", null=True, blank=True)
    arka_yuz = models.ImageField("arka yüz görseli", upload_to="cek_senet/", null=True, blank=True)

    class Meta:
        db_table = "finans_cek_senet"
        verbose_name = "çek/senet"
        verbose_name_plural = "çek/senetler"
        ordering = ["vade", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(tutar__gt=0),
                                   name="ck_cek_senet_tutar_gt0"),
        ]

    def __str__(self):
        return f"{self.get_tip_display()} {self.belge_no} {self.tutar}"


class CekBordroSatir(TemelModel):
    """İşlem bordrosu (ciro/tahsil/teminat…) ile işlenen çek/senet bağı + işlem ÖNCESİ durum
    (geri-al için). Giriş/çıkış bordroları evrakı CekSenet.giris_bordrosu ile bağlar; bu model
    yalnız MEVCUT evrakı SEÇEN işlem bordroları içindir."""

    bordro = models.ForeignKey(CekBordrosu, verbose_name="bordro",
                               on_delete=models.PROTECT, related_name="satirlar")
    cek_senet = models.ForeignKey(CekSenet, verbose_name="çek/senet",
                                  on_delete=models.PROTECT, related_name="bordro_satirlari")
    onceki_durum = models.CharField("önceki durum", max_length=12, choices=CekSenet.Durum.choices)

    class Meta:
        db_table = "finans_cek_bordro_satir"
        verbose_name = "çek/senet bordro satırı"
        verbose_name_plural = "çek/senet bordro satırları"
        ordering = ["id"]

    def __str__(self):
        return f"Bordro #{self.bordro_id} · {self.cek_senet_id}"
