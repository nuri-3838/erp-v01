"""Çekirdek modeller.

Burada yalnızca tüm tabloların paylaştığı invariant taban (audit + soft delete,
spec 0b-g) ile v0.1 veri modelinin ilk tablosu HESAP_PLANI (spec bölüm 2) var.
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models

from core.storage import (
    aday_ek_yolu, bordro_dosya_yolu, cari_ek_yolu, kdv_mahsup_dosya_yolu, cek_gorsel_yolu, fatura_ek_yolu, ik_ozel_depo, ozel_depo,
    personel_belge_yolu, personel_foto_yolu,
)


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

    _KUR_TIPI_SUFFIX = {"MB_ALIS": "alis", "MB_SATIS": "satis",
                        "EFEKTIF_ALIS": "efektif_alis", "EFEKTIF_SATIS": "efektif_satis"}

    def deger(self, pb, kur_tipi):
        """pb ('USD'/'EUR'/'GBP') + kur_tipi'ne (bkz. Cari.KurTipi) karşılık gelen saklı
        kur alanını döner (yoksa None) — carinin tercih ettiği kur tipini okumanın TEK
        doğruluk kaynağı; proje genelindeki `_kur_coz` kopyaları bunu kullanır."""
        suf = self._KUR_TIPI_SUFFIX.get(kur_tipi, "alis")
        return getattr(self, f"{pb.lower()}_{suf}", None)


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
        KREDI_KARTI = "KREDI_KARTI", "Kredi Kartı Hareketi (otomatik)"
        KREDI = "KREDI", "Kredi Hareketi (otomatik)"
        YATIRIM = "YATIRIM", "Yatırım Projesi Aktifleştirme (otomatik)"
        STOK_SARF = "STOK_SARF", "Stok Sarf Çıkışı (otomatik)"
        URETIM = "URETIM", "Üretim Maliyet Aktarımı (otomatik)"
        STOK_SATIS = "STOK_SATIS", "Satış Maliyeti (otomatik)"
        KUR_DEGERLEME = "KUR_DEGERLEME", "Dönem Sonu Kur Değerleme"
        CARI_KESINTI = "CARI_KESINTI", "Cari Kesinti / Masraf (otomatik)"
        DURAN_VARLIK = "DURAN_VARLIK", "Duran Varlık Kartı Açılışı (otomatik)"
        DONEMSEL = "DONEMSEL", "Dönemsel Dağıtım (otomatik)"
        CARI_VIRMAN = "CARI_VIRMAN", "Cari Virman (otomatik)"
        BORDRO = "BORDRO", "Personel Bordro Tahakkuku (otomatik)"
        KDV_MAHSUP = "KDV_MAHSUP", "KDV Dönem Mahsubu (otomatik)"
        FASON_TAHAKKUK = "FASON_TAHAKKUK", "Fason Tahakkuku (otomatik)"

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
    # Kaynak=KREDI_KARTI fişin kaynağı olan kredi kartı (hareket motoru); kasa ile aynı amaç.
    kredi_karti = models.ForeignKey(
        "KrediKarti", verbose_name="kaynak kredi kartı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
    )
    # Kaynak=CARI_KESINTI fişin kaynağı olan cari (kesinti/masraf hareketi); kasa ile aynı amaç.
    cari = models.ForeignKey(
        "Cari", verbose_name="kaynak cari", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kesinti_fisleri",
    )
    # Kaynak=CARI_VIRMAN: virmanın KARŞI tarafı olan cari (``cari`` = virmanın yapıldığı cari); iki carinin ekstresinde de Düzenle/Sil.
    karsi_cari = models.ForeignKey(
        "Cari", verbose_name="virman karşı cari", null=True, blank=True,
        on_delete=models.PROTECT, related_name="virman_karsi_fisleri",
    )
    # Kaynak=BORDRO fişin kaynağı olan aylık personel bordrosu (bordro başına TEK fiş); düzenle/sil bordro ekranından.
    personel_bordro = models.ForeignKey(
        "PersonelBordro", verbose_name="kaynak personel bordrosu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
    )
    # Kaynak=KDV_MAHSUP fişin kaynağı olan KDV dönem mahsubu (dönem başına TEK fiş); düzenle/sil KDV Dönem Mahsubu ekranından.
    kdv_mahsup = models.ForeignKey(
        "KdvMahsup", verbose_name="kaynak KDV dönem mahsubu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fisler",
    )
    # Kaynak=KREDI fişin kaynağı olan kredi (hareket motoru); kasa ile aynı amaç.
    kredi = models.ForeignKey(
        "Kredi", verbose_name="kaynak kredi", null=True, blank=True,
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
    # Yalnız hesap 258 (Yapılmakta Olan Yatırımlar) ailesindeyse anlamlı — manuel fişle
    # (fatura dışı, örn. gümrükçü dekontu) projeye eklenen tutarı izler; proje toplamına
    # dahil edilir (bkz. core.services.yatirim_projesi.proje_toplami). Fatura kaynaklı
    # 258 kalemleri bunu KULLANMAZ (onlar FaturaSatir.yatirim_projesi'nden gelir).
    yatirim_projesi = models.ForeignKey(
        "YatirimProjesi", verbose_name="yatırım projesi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="yevmiye_satirlari")
    # --- Ortalama kurla döviz maliyeti (bkz. core.services.kur_farki) -------------------------
    # Döviz hesabının ÇIKIŞ satırında TL, hesabın hareketli ağırlıklı ortalama kuruyla yazılır;
    # fark aynı fişe 646/656 satırı olarak eklenir. ham_tl = işlem kuruyla ilk yazılan TL (boşsa
    # satır değiştirilmemiştir), ort_kur = kullanılan ortalama kur, ana_satir = bu satır bir
    # kur farkı satırıysa ait olduğu ana satır (motor üretir/siler; elle düzenlenmez).
    ham_tl = models.DecimalField("ham TL (işlem kuruyla)", max_digits=18, decimal_places=2,
                                 null=True, blank=True)
    ort_kur = models.DecimalField("kullanılan ortalama kur", max_digits=18, decimal_places=6,
                                  null=True, blank=True)
    ana_satir = models.ForeignKey(
        "self", verbose_name="kur farkının ana satırı", null=True, blank=True,
        on_delete=models.CASCADE, related_name="kur_farki_satirlari")

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
                fields=["kullanici", "ekran_kod"], condition=models.Q(silindi=False),
                name="uq_ekran_yetki"
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
    # HİZMET kategorisi (stoksuz): bu kategorideki kartlar HİÇBİR koşulda stok hareketi yazmaz (fatura/irsaliye kalemi atlanır; elle hareket,
    # transfer, sarf, operasyon kaydı reddedilir), değerleme raporuna ve stok miktarına girmez. Fatura muhasebe fişi kategori hesap haritasına
    # göre normal oluşur (örn. fason hizmet faturası 151 alt hesabına borç). Yalnız ALT kategoride anlamlıdır.
    hizmet_kategorisi = models.BooleanField("hizmet kategorisi (stok hareketi yapmaz)", default=False)

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
    # Gider faturası: kalemler STOK yerine doğrudan bir GİDER HESABINA (yaprak 7xx/63x...)
    # yazılır; depo/stok hareketi yoktur, muhasebe haritası (KategoriHesap) kullanılmaz.
    # Yalnız ALIŞ yönünde anlamlıdır (servis fatura anında zorlar).
    gider = models.BooleanField("gider faturası", default=False)
    # Serbest meslek makbuzu gibi GV STOPAJI kesilen tip (yalnız gider+alış): faturada stopaj
    # oranı girilir, stopaj 360.xx'e alacak yazılıp cariden düşülür (bkz. core.services.fatura).
    stopajli = models.BooleanField("GV stopajlı (serbest meslek makbuzu)", default=False)
    # Yalnız KDV (yalnız ALIŞ): fatura kalemleri normal girilir (belge/KDV raporları matrah + KDV gösterir) ama yevmiye fişine YALNIZ KDV yazılır
    # (191 B / cari A; tevkifat varsa 360 A, cari net KDV). Matrah için satır, stok hareketi ve kategori-hesap eşlemesi yoktur — matrah başka yoldan
    # (ör. fason tahakkuk fişi) zaten yazılmıştır. Gider/stopajlı ile birlikte olmaz.
    yalniz_kdv = models.BooleanField("yalnız KDV (matrah fişe yazılmaz)", default=False)

    class MaliyetFisi(models.TextChoices):
        YOK = "", "Maliyet fişi yok"
        SATIS = "SATIS", "Satış (çıkış maliyeti: 620/621/623 borç, stok alacak)"
        SATIS_IADE = "SATIS_IADE", "Satış iadesi (giriş ortalamadan, ters maliyet fişi)"
        ALIS_IADE = "ALIS_IADE", "Alış iadesi (çıkış iade faturası tutarıyla, fişsiz)"

    # Stoklu faturada çıkış/giriş hareketinin ağırlıklı ortalama maliyeti için otomatik MALİYET
    # FİŞİ üretilsin mi (bkz. core.services.stok_fis). Boş = üretilmez (ör. alış faturası: stok
    # girişinin muhasebesini faturanın kendi fişi yapar).
    maliyet_fisi = models.CharField("stok maliyet fişi", max_length=10, blank=True, default="",
                                    choices=MaliyetFisi.choices)

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
    # Satış Teklifi PDF'i (?dil=en) için — bkz. ad_dil(). Boşsa TR ad kullanılır.
    ad_en = models.CharField("ad (İngilizce)", max_length=200, blank=True, default="")
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
    # Hareketli ağırlıklı ortalama maliyet — HESAP SONUCU ÖNBELLEĞİ (üretim biriminde, tüm
    # depolar için tek ortalama). Kaynak hareketlerdir; core.services.stok_ortalama
    # yeniden_hesapla her hareket değişiminde günceller, testle sıfırdan hesapla = saklanan
    # eşitliği denetlenir. Elle DÜZENLENMEZ.
    maliyet_miktar = models.DecimalField(
        "maliyet miktarı (önbellek)", max_digits=18, decimal_places=6, default=0)
    maliyet_deger_try = models.DecimalField(
        "stok değeri TL (önbellek)", max_digits=18, decimal_places=2, default=0)
    maliyet_deger_usd = models.DecimalField(
        "stok değeri USD (önbellek)", max_digits=18, decimal_places=2, default=0)
    ort_maliyet_try = models.DecimalField(
        "ortalama maliyet TL (önbellek)", max_digits=18, decimal_places=6, null=True, blank=True)
    ort_maliyet_usd = models.DecimalField(
        "ortalama maliyet USD (önbellek)", max_digits=18, decimal_places=6, null=True, blank=True)
    # KDV/tevkifat artık serbest sayı değil; AYARLAR tanım listelerine FK (otomatik
    # yevmiye buradan muhasebe hesabını/oranı okur). KDV formda/serviste ZORUNLU
    # (stok_olustur/stok_guncelle boşsa reddeder); model null=True kalır çünkü
    # KDV'siz kart senaryosu başka noktalarda (örn. teklif/sipariş KDV=0 hesap testi)
    # bilinçli olarak destekleniyor. Tevkifat tamamen opsiyonel.
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
    # Tedarikçinin bu ürüne verdiği isim ("tedarikçinin dilinde" satınalma için) — imalat/dahili
    # ad'dan (ad) BAĞIMSIZ; satınalma ekranlarında/PDF'lerinde ad yerine kullanılır. Boşsa
    # ad_satinalma() dahili ad'a düşer (ad_dil() ile aynı desen, satış yerine satınalma yönü).
    tedarikci_adi = models.CharField("tedarikçi ürün adı", max_length=200, blank=True, default="")
    # Alış fiyatı — bilgi amaçlı (muhasebe/fatura fiyatını ETKİLEMEZ, yalnız referans).
    # Opsiyonel. Para birimi: YevmiyeSatir.IslemPB.choices ile aynı kaynak (Cari.PARA_CHOICES
    # bunu aliaslar, ama Cari bu dosyada Stok'tan SONRA tanımlı — ileri referans olmasın diye
    # doğrudan IslemPB kullanılıyor).
    alis_fiyati = models.DecimalField(
        "alış fiyatı", max_digits=18, decimal_places=6, null=True, blank=True)
    alis_fiyati_pb = models.CharField(
        "alış fiyatı para birimi", max_length=3,
        choices=YevmiyeSatir.IslemPB.choices, default="TRY")

    # Ürün grubu (birden çok seçilebilir): kart hangi iş akışlarında kullanılıyor.
    # En az biri seçili olmalı (Meta.constraints + serviste zorlanır) — grup, formda
    # hangi alanların sorulacağını belirler (örn. yalnız Satınalma işaretliyse aşağıdaki
    # teklif/katalog alanları hiç sorulmaz, kayıtta hep boş kalır).
    satinalma_urunu = models.BooleanField("satınalma ürünü", default=False)
    # Varsayılan True: bu alan eklenmeden önce oluşturulmuş/oluşturulacak kartların
    # (fatura/teklif/stok testleri gibi grup umursamayan tüm çağıranlar dahil) en az
    # bir grubu her zaman olur — kısıt (ck_stok_en_az_bir_grup) sessizce ihlal edilmez.
    uretim_urunu = models.BooleanField("üretim ürünü", default=True)
    satis_urunu = models.BooleanField("satış ürünü", default=False)

    # Satış/teklif alanları — yalnız satis_urunu=True kartlarda anlamlı (serviste
    # satis_urunu=False ise bu alanlar temizlenir). SEMTA ürün kataloğu teknik ölçü
    # tablosuyla birebir; ileride teklif (quotation) PDF'i bunlardan beslenecek.
    # Üreticinin kendi model kodu (örn. "A21") — stok kartının otomatik ``kod``'undan
    # (ÜST-ALT-sıra) AYRI: ad içine gömülmez, Teklif/Teknik bölümünde ayrı gösterilir.
    model_kodu = models.CharField("model kodu", max_length=30, blank=True, default="")
    # H/S (Harmonize Sistem/GTİP) kodu — gümrük/ihracat evrakında kullanılır, salt kod.
    hs_kodu = models.CharField("H/S kodu", max_length=20, blank=True, default="")
    materyal = models.CharField("materyal", max_length=100, blank=True, default="")
    materyal_en = models.CharField(
        "materyal (İngilizce)", max_length=100, blank=True, default="")
    basamak_sayisi = models.PositiveIntegerField(
        "basamak sayısı", null=True, blank=True)
    yukseklik = models.DecimalField(
        "yükseklik (cm)", max_digits=8, decimal_places=1, null=True, blank=True)
    acik_derinlik = models.DecimalField(
        "açık derinlik (cm)", max_digits=8, decimal_places=1, null=True, blank=True)
    taban_genisligi = models.DecimalField(
        "taban genişliği (cm)", max_digits=8, decimal_places=1, null=True, blank=True)
    kapali_boy = models.DecimalField(
        "kapalı boy (cm)", max_digits=8, decimal_places=1, null=True, blank=True)
    agirlik = models.DecimalField(
        "ağırlık (kg)", max_digits=8, decimal_places=2, null=True, blank=True)
    azami_yuk = models.DecimalField(
        "azami yük (kg)", max_digits=8, decimal_places=2, null=True, blank=True)
    cbm = models.DecimalField(
        "CBM (m³/adet)", max_digits=8, decimal_places=3, null=True, blank=True)
    yukleme_20dc = models.PositiveIntegerField(
        "20' DC yükleme adedi", null=True, blank=True)
    yukleme_40hq = models.PositiveIntegerField(
        "40' HQ yükleme adedi", null=True, blank=True)
    yukleme_tir = models.PositiveIntegerField(
        "TIR yükleme adedi", null=True, blank=True)
    # Yalnız satis_urunu=True kartlarda anlamlı; kucult_webp ile küçültülüp WebP'ye
    # çevrilmiş halde saklanır (bkz. core/gorsel.py — banka logosuyla aynı desen,
    # burada max_kenar=1600 ile çağrılır).
    gorsel = models.ImageField(
        "ürün görseli", upload_to="stok_gorsel/", blank=True, null=True)

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
            models.CheckConstraint(
                condition=models.Q(alis_fiyati__isnull=True) | models.Q(alis_fiyati__gte=0),
                name="ck_stok_alis_fiyati_gte0"),
            models.CheckConstraint(
                condition=models.Q(yukseklik__isnull=True) | models.Q(yukseklik__gte=0),
                name="ck_stok_yukseklik_gte0"),
            models.CheckConstraint(
                condition=models.Q(acik_derinlik__isnull=True) | models.Q(acik_derinlik__gte=0),
                name="ck_stok_acik_derinlik_gte0"),
            models.CheckConstraint(
                condition=(models.Q(taban_genisligi__isnull=True)
                          | models.Q(taban_genisligi__gte=0)),
                name="ck_stok_taban_genisligi_gte0"),
            models.CheckConstraint(
                condition=models.Q(kapali_boy__isnull=True) | models.Q(kapali_boy__gte=0),
                name="ck_stok_kapali_boy_gte0"),
            models.CheckConstraint(
                condition=models.Q(agirlik__isnull=True) | models.Q(agirlik__gte=0),
                name="ck_stok_agirlik_gte0"),
            models.CheckConstraint(
                condition=models.Q(azami_yuk__isnull=True) | models.Q(azami_yuk__gte=0),
                name="ck_stok_azami_yuk_gte0"),
            models.CheckConstraint(
                condition=models.Q(cbm__isnull=True) | models.Q(cbm__gte=0),
                name="ck_stok_cbm_gte0"),
            models.CheckConstraint(
                condition=(models.Q(satinalma_urunu=True) | models.Q(uretim_urunu=True)
                          | models.Q(satis_urunu=True)),
                name="ck_stok_en_az_bir_grup"),
        ]

    def __str__(self):
        return f"{self.kod} {self.ad}"

    def ad_dil(self, dil):
        """Teklif PDF'i için dile göre ad ('en' -> ad_en, boşsa ad)."""
        return (self.ad_en or self.ad) if dil == "en" else self.ad

    def materyal_dil(self, dil):
        """Teklif PDF'i için dile göre materyal ('en' -> materyal_en, boşsa materyal)."""
        return (self.materyal_en or self.materyal) if dil == "en" else self.materyal

    def ad_satinalma(self):
        """Satınalma ekranı/PDF'i için ad — boşsa ad döner (bkz. ad_dil() ile aynı desen)."""
        return self.tedarikci_adi or self.ad


class StokFiyat(TemelModel):
    """Stok satış fiyat listesi — bir stok için para birimi başına EN FAZLA bir aktif
    satır (TRY/USD/EUR/GBP). Yalnız satis_urunu=True kartlarda anlamlı; satis_urunu
    kapatılınca serviste tüm satırlar soft-delete edilir (diğer satış/teklif alanlarıyla
    aynı invariant, bkz. core/services/stok.py). UI'da satır ekle/sil YOK — Stok formunda
    4 sabit para birimi input'u, servis katmanı upsert/soft-delete eder.
    """

    stok = models.ForeignKey(Stok, verbose_name="stok", related_name="fiyatlar",
                             on_delete=models.CASCADE)
    para_birimi = models.CharField("para birimi", max_length=3,
                                   choices=YevmiyeSatir.IslemPB.choices)
    fiyat = models.DecimalField("satış fiyatı", max_digits=18, decimal_places=6)

    class Meta:
        db_table = "stok_fiyat"
        verbose_name = "stok fiyatı"
        verbose_name_plural = "stok fiyatları"
        ordering = ["stok", "para_birimi"]
        constraints = [
            models.UniqueConstraint(
                fields=["stok", "para_birimi"], condition=models.Q(silindi=False),
                name="uq_stok_fiyat_stok_pb_aktif"),
            models.CheckConstraint(condition=models.Q(fiyat__gte=0),
                                   name="ck_stok_fiyat_gte0"),
        ]

    def __str__(self):
        return f"{self.stok_id}:{self.para_birimi}={self.fiyat}"


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

    def ad_dil(self, dil):
        """Teklif PDF'i için dile göre ad ('en' -> ad_en, boşsa ad)."""
        return (self.ad_en or self.ad) if dil == "en" else self.ad


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

    def ad_dil(self, dil):
        """Teklif PDF'i için dile göre ad ('en' -> ad_en, boşsa ad)."""
        return (self.ad_en or self.ad) if dil == "en" else self.ad


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
    (örn. 320-10-0001), kategorisizse CAR-NNNN.
    Cari hesap hareketi/ekstre Faz 4 (finans gerektirir).
    """

    PARA_CHOICES = YevmiyeSatir.IslemPB.choices   # TRY/USD/EUR/GBP

    # FASON: fasoncu fatura kesmiyorsa dönüş onayında fason bedeli ayrı bir tahakkuk fişiyle (151 alt hesabı borç / bu cari alacak) cariye yazılır.
    fason_faturasiz = models.BooleanField("faturasız fason (dönüş onayında cariye yaz)", default=False)

    class KurTipi(models.TextChoices):
        MB_ALIS = "MB_ALIS", "MB Alış"
        MB_SATIS = "MB_SATIS", "MB Satış"
        EFEKTIF_ALIS = "EFEKTIF_ALIS", "Efektif Alış"
        EFEKTIF_SATIS = "EFEKTIF_SATIS", "Efektif Satış"

    class DegerlemeKurali(models.TextChoices):
        OTOMATIK = "OTOMATIK", "Otomatik (avans değerlenmez)"
        HER_ZAMAN = "HER_ZAMAN", "Her zaman değerle"
        HIC = "HIC", "Hiç değerleme"

    class KurFarkiHedefi(models.TextChoices):
        OTOMATIK = "OTOMATIK", "Otomatik (yatırım projesi varsa 258)"
        HESAP_646_656 = "HESAP_646_656", "Her zaman 646-656"

    class OdemeKosulu(models.TextChoices):
        PESIN = "PESIN", "Peşin (fatura tarihi)"
        GUN_SONRA = "GUN_SONRA", "Fatura tarihinden X gün sonra"
        SONRAKI_AY_GUNU = "SONRAKI_AY_GUNU", "Sonraki ayın N. günü"

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
    telefon_whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)
    telefon_2 = models.CharField("telefon 2", max_length=20, blank=True)
    telefon_2_whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)
    eposta = models.EmailField("e-posta", blank=True)
    web = models.URLField("web", blank=True)
    ilgili_kisi = models.CharField("adı soyadı", max_length=120, blank=True, default="")
    kep_adresi = models.CharField("KEP", max_length=100, blank=True)
    # Ana adres
    ulke = models.ForeignKey(
        Ulke, verbose_name="ülke", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cariler")
    sehir = models.ForeignKey(
        Sehir, verbose_name="şehir", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cariler")
    adres = models.TextField("adres", blank=True)
    # Ticari
    para_birimi = models.CharField("para birimi", max_length=3, choices=PARA_CHOICES, default="TRY")
    # Yalnız para_birimi != TRY iken anlamlı — döviz belgelerinde (İrsaliye/Fatura/Kasa/
    # Banka/Kredi Kartı/Çek) kur önizlemesi/hesabı hangi TCMB kur tipini (bkz. Kur.deger)
    # kullanacağını belirler. Varsayılan MB_ALIS -> mevcut carilerin davranışı DEĞİŞMEZ.
    kur_tipi = models.CharField("kur tipi", max_length=15, choices=KurTipi.choices,
                                default=KurTipi.MB_ALIS)
    # Dönem sonu kur değerlemesi (bkz. core.services.kur_degerleme): OTOMATIK → 320/321 havuzunda döviz
    # bakiyesi BORÇ (verilen avans) ya da 120/121'de ALACAK (alınan avans) ise değerlenmez; HER_ZAMAN →
    # avans olsa da değerlenir (ör. parasal alacak sayılan depozito); HIC → hiç değerlenmez.
    kur_degerleme = models.CharField("kur değerlemesi", max_length=10, choices=DegerlemeKurali.choices,
                                     default=DegerlemeKurali.OTOMATIK)
    # Kur farkının (motor + dönem sonu değerleme) yazılacağı hesap: OTOMATIK → carinin en son projeli onaylı
    # faturasındaki yatırım projesi "Devam Ediyor" ise 258 + o proje, değilse 646/656; HESAP_646_656 → hep 646/656.
    kur_farki_hedefi = models.CharField("kur farkı hedefi", max_length=14, choices=KurFarkiHedefi.choices,
                                        default=KurFarkiHedefi.OTOMATIK)
    kredi_limiti = models.DecimalField("kredi/risk limiti", max_digits=14, decimal_places=2, default=0)
    iskonto_yuzdesi = models.DecimalField("varsayılan iskonto %", max_digits=5, decimal_places=2, default=0)
    # Boş (null): koşul yok, fatura vade tarihi elle girilir — mevcut carilerin hepsi
    # böyle kalır (migration veri göçü yok). Dolu olduğunda fatura ekranı vadeyi otomatik
    # önerir (bkz. core.services.cari.vade_hesapla).
    odeme_kosulu = models.CharField(
        "ödeme koşulu", max_length=20, choices=OdemeKosulu.choices, null=True, blank=True)
    odeme_gunu = models.PositiveSmallIntegerField("ödeme günü", null=True, blank=True)
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

    def clean(self):
        super().clean()
        if self.odeme_kosulu in (self.OdemeKosulu.GUN_SONRA, self.OdemeKosulu.SONRAKI_AY_GUNU):
            if self.odeme_gunu is None:
                raise ValidationError(
                    {"odeme_gunu": "Bu ödeme koşulu için gün sayısı zorunludur."})
            if (self.odeme_kosulu == self.OdemeKosulu.SONRAKI_AY_GUNU
                    and not (1 <= self.odeme_gunu <= 31)):
                raise ValidationError(
                    {"odeme_gunu": "Sonraki ayın günü 1-31 arasında olmalı."})
            if (self.odeme_kosulu == self.OdemeKosulu.GUN_SONRA
                    and not (0 <= self.odeme_gunu <= 365)):
                raise ValidationError({"odeme_gunu": "Gün sayısı 0-365 arasında olmalı."})

    @property
    def odeme_kosulu_metni(self):
        """Cari detayında okunur metin (örn. 'Sonraki ayın 25. günü')."""
        if not self.odeme_kosulu:
            return ""
        if self.odeme_kosulu == self.OdemeKosulu.PESIN:
            return "Peşin (fatura tarihi)"
        if self.odeme_kosulu == self.OdemeKosulu.GUN_SONRA:
            return f"Fatura tarihinden {self.odeme_gunu} gün sonra"
        if self.odeme_kosulu == self.OdemeKosulu.SONRAKI_AY_GUNU:
            return f"Sonraki ayın {self.odeme_gunu}. günü"
        return self.get_odeme_kosulu_display()


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


class CariSevkAdresi(TemelModel):
    """Cariye ait sevk (teslimat) adresi (çoklu) — bir carinin birden fazla teslimat
    noktası olabilir (depo, şube, fabrika vb.)."""

    cari = models.ForeignKey(Cari, verbose_name="cari", related_name="sevk_adresleri",
                             on_delete=models.CASCADE)
    ad = models.CharField("adres adı", max_length=100)
    ulke = models.ForeignKey(
        Ulke, verbose_name="ülke", null=True, blank=True,
        on_delete=models.PROTECT, related_name="sevk_adresleri")
    sehir = models.ForeignKey(
        Sehir, verbose_name="şehir", null=True, blank=True,
        on_delete=models.PROTECT, related_name="sevk_adresleri")
    adres = models.TextField("adres", blank=True)
    varsayilan = models.BooleanField("varsayılan", default=False)

    class Meta:
        db_table = "cari_sevk_adresi"
        verbose_name = "sevk adresi"
        verbose_name_plural = "sevk adresleri"
        ordering = ["-varsayilan", "ad"]

    def __str__(self):
        return self.ad


class CariYetkili(TemelModel):
    """Cariye ait yetkili kişi (çoklu)."""

    cari = models.ForeignKey(Cari, verbose_name="cari", related_name="yetkililer",
                             on_delete=models.CASCADE)
    ad_soyad = models.CharField("ad soyad", max_length=120)
    unvan = models.CharField("görev/unvan", max_length=80, blank=True)
    telefon = models.CharField("telefon", max_length=20, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    notlar = models.CharField("notlar", max_length=200, blank=True)
    whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)

    class Meta:
        db_table = "cari_yetkili"
        verbose_name = "yetkili kişi"
        verbose_name_plural = "yetkili kişiler"
        ordering = ["ad_soyad"]

    def __str__(self):
        return self.ad_soyad


class CariAktivite(TemelModel):
    """Cariyle yapılan görüşme/temas kaydı (çoklu) — cari detay sayfasında zaman çizelgesi."""

    class Tur(models.TextChoices):
        GORUSME = "GORUSME", "Görüşme"
        TELEFON = "TELEFON", "Telefon"
        TOPLANTI = "TOPLANTI", "Toplantı"
        EPOSTA = "EPOSTA", "E-posta"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        NOT = "NOT", "Not"

    cari = models.ForeignKey(Cari, verbose_name="cari", related_name="aktiviteler",
                             on_delete=models.CASCADE)
    tarih = models.DateField("tarih")
    tur = models.CharField("tür", max_length=10, choices=Tur.choices, default=Tur.NOT)
    aciklama = models.TextField("açıklama")

    class Meta:
        db_table = "cari_aktivite"
        verbose_name = "cari aktivite"
        verbose_name_plural = "cari aktiviteler"
        ordering = ["-tarih", "-id"]

    def __str__(self):
        return f"{self.cari.unvan} — {self.get_tur_display()} ({self.tarih})"


class CariAktiviteEk(TemelModel):
    """Aktiviteye eklenen dosya (çoklu) — resim yüklemede WebP'ye küçültülür (spec invariant'ı),
    PDF olduğu gibi saklanır."""

    aktivite = models.ForeignKey(CariAktivite, verbose_name="aktivite", related_name="ekler",
                                 on_delete=models.CASCADE)
    # Gizli: özel depoda (MEDIA_ROOT dışı), yalnız yetkili görünümle sunulur (bkz. core.storage).
    dosya = models.FileField("dosya", storage=ozel_depo, upload_to=cari_ek_yolu)
    orijinal_ad = models.CharField("orijinal dosya adı", max_length=255, blank=True)

    class Meta:
        db_table = "cari_aktivite_ek"
        verbose_name = "aktivite eki"
        verbose_name_plural = "aktivite ekleri"
        ordering = ["id"]

    def __str__(self):
        return self.orijinal_ad or self.dosya.name

    @property
    def resim_mi(self):
        return self.dosya.name.lower().endswith(".webp")


class AdayTip(models.TextChoices):
    ESKI_MUSTERI = "ESKI_MUSTERI", "Eski müşteri (Çakmak)"
    ADAY = "ADAY", "Aday"
    ARACI = "ARACI", "Aracı / Komisyoncu"
    LOJISTIK = "LOJISTIK", "Lojistik / Nakliye"
    GUMRUK = "GUMRUK", "Gümrük müşaviri"
    TEDARIKCI = "TEDARIKCI", "Tedarikçi"
    RAKIP = "RAKIP", "Rakip"
    PAZAR_BILGISI = "PAZAR_BILGISI", "Pazar bilgisi (firma değil)"


class AdayPotansiyel(models.TextChoices):
    DUSUK = "DUSUK", "Düşük"
    ORTA = "ORTA", "Orta"
    YUKSEK = "YUKSEK", "Yüksek"


class AdayAsama(models.TextChoices):
    YENI = "YENI", "Yeni"
    TEMAS = "TEMAS", "Temas kuruldu"
    ILGILI = "ILGILI", "İlgileniyor"
    TEKLIF = "TEKLIF", "Teklif verildi"
    NUMUNE = "NUMUNE", "Numune"
    SIPARIS = "SIPARIS", "Sipariş"
    KAPALI = "KAPALI", "Kapalı"


class KapanisNedeni(models.TextChoices):
    ULASILAMIYOR = "ULASILAMIYOR", "Ulaşılamıyor"
    ILGISIZ = "ILGISIZ", "İlgilenmiyor"
    RAKIP = "RAKIP", "Rakiple çalışıyor"
    KAPANMIS = "KAPANMIS", "Firma kapanmış"
    DIGER = "DIGER", "Diğer"


class TanimRenk(models.TextChoices):
    """Aday Tip/Potansiyel/Aşama tanım rozetlerinin rengi — birkaç hazır seçenek."""
    GRI = "GRI", "Gri"
    MAVI = "MAVI", "Mavi"
    YESIL = "YESIL", "Yeşil"
    TURUNCU = "TURUNCU", "Turuncu"
    KIRMIZI = "KIRMIZI", "Kırmızı"
    MOR = "MOR", "Mor"


class _AdayTanimTaban(TemelModel):
    """Aday Tip/Potansiyel/Aşama tanım tablolarının ortak taban modeli (soyut) — kullanıcı
    tarafından düzenlenebilir tanım listeleri (bkz. core.services.aday_tanim). ``sistem_kodu``
    dolu kayıtlar tohum veridir: silinemez ve kodu değişmez (serviste zorlanır), ama adı/
    sırası/rengi/aktifliği değişebilir. ``aktif=False`` kayıt formlarda seçilemez ama mevcut
    adaylarda görünmeye devam eder (soft-delete ``silindi`` alanından AYRI bir kavram)."""

    ad = models.CharField("ad", max_length=100)
    sira = models.PositiveSmallIntegerField("sıra", default=0)
    aktif = models.BooleanField("aktif", default=True)
    renk = models.CharField("renk", max_length=10, choices=TanimRenk.choices,
                            default=TanimRenk.GRI)
    # Tohum veri (migration 0132) işareti — null: kullanıcının sonradan eklediği serbest kayıt.
    sistem_kodu = models.CharField("sistem kodu", max_length=20, null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["sira", "ad"]

    def __str__(self):
        return self.ad


class AdayTipTanim(_AdayTanimTaban):
    """CRM > Tipler — Cariye Dönüştür'ün kategori önerisi/engeli artık BURADAN okunur
    (bkz. core.services.aday_donustur.kategori_onerisi/donusturme_engeli_var_mi)."""

    cari_kategori_yurtici = models.ForeignKey(
        "CariKategori", verbose_name="yurtiçi cari kategorisi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    cari_kategori_yurtdisi = models.ForeignKey(
        "CariKategori", verbose_name="yurtdışı cari kategorisi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    cariye_donusturulebilir = models.BooleanField("cariye dönüştürülebilir", default=True)

    class Meta(_AdayTanimTaban.Meta):
        db_table = "aday_tip_tanim"
        verbose_name = "aday tipi"
        verbose_name_plural = "aday tipleri"
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_adaytiptanim_ad_aktif"),
            models.UniqueConstraint(fields=["sistem_kodu"], condition=models.Q(silindi=False),
                                    name="uq_adaytiptanim_kod_aktif"),
        ]


class AdayPotansiyelTanim(_AdayTanimTaban):
    sicak = models.BooleanField("Sıcak sekmesine girer", default=False)

    class Meta(_AdayTanimTaban.Meta):
        db_table = "aday_potansiyel_tanim"
        verbose_name = "aday potansiyeli"
        verbose_name_plural = "aday potansiyelleri"
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_adaypottanim_ad_aktif"),
            models.UniqueConstraint(fields=["sistem_kodu"], condition=models.Q(silindi=False),
                                    name="uq_adaypottanim_kod_aktif"),
        ]


class AdayAsamaTanim(_AdayTanimTaban):
    class Rol(models.TextChoices):
        BASLANGIC = "BASLANGIC", "Başlangıç"
        ARA = "ARA", "Ara"
        KAPALI = "KAPALI", "Kapalı"
        CARI = "CARI", "Cari olunca"
        TEKLIF = "TEKLIF", "Teklif verilince"
        SIPARIS = "SIPARIS", "Sipariş olunca"

    rol = models.CharField("rol", max_length=10, choices=Rol.choices, default=Rol.ARA)

    class Meta(_AdayTanimTaban.Meta):
        db_table = "aday_asama_tanim"
        verbose_name = "aday aşaması"
        verbose_name_plural = "aday aşamaları"
        constraints = [
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_adayasamatanim_ad_aktif"),
            models.UniqueConstraint(fields=["sistem_kodu"], condition=models.Q(silindi=False),
                                    name="uq_adayasamatanim_kod_aktif"),
        ]


class AdayMusteri(TemelModel):
    """CRM: henüz Cari olmamış potansiyel müşteri. Kasıtlı olarak Cari'den ayrı ve HAFİF —
    Cari.kaydı açılınca otomatik muhasebe hesabı açılır (bkz. cari_servis.muhasebe_hesabi_ac),
    bu adaylar için yanlış olur. Yapısı bilinçli olarak Cari'ye çok yakın (kimlik/iletişim +
    kategori + para birimi + iskonto) — "Cariye Dönüştür" servisi (bkz.
    core.services.aday_donustur) gerçek bir Cari açar/bağlar ve ``cari`` + ``cariye_donusum_
    tarihi``'ni set eder; kayıt silinmez, iz kalır (TeklifSiparis'in kaynak_teklif/
    kaynak_siparis self-FK desenindeki gibi)."""

    PARA_CHOICES = YevmiyeSatir.IslemPB.choices

    unvan = models.CharField("unvan / ad soyad", max_length=200)
    ilgili_kisi = models.CharField("ilgili kişi", max_length=120, blank=True, default="")
    telefon = models.CharField("telefon", max_length=20, blank=True, default="")
    telefon_whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)
    telefon_2 = models.CharField("telefon 2", max_length=20, blank=True, default="")
    telefon_2_whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)
    eposta = models.EmailField("e-posta", blank=True, default="")
    eposta_gecersiz = models.BooleanField("e-posta geçersiz (bounce)", default=False)
    eposta_2 = models.EmailField("e-posta 2", blank=True, default="")
    eposta_2_gecersiz = models.BooleanField("e-posta 2 geçersiz (bounce)", default=False)
    web = models.URLField("web", blank=True, default="")
    ulke = models.ForeignKey(
        "Ulke", verbose_name="ülke", null=True, blank=True,
        on_delete=models.PROTECT, related_name="aday_musteriler")
    sehir = models.ForeignKey(
        "Sehir", verbose_name="şehir", null=True, blank=True,
        on_delete=models.PROTECT, related_name="aday_musteriler")
    adres = models.TextField("adres", blank=True, default="")
    kategori = models.ForeignKey(
        "AdayMusteriKategori", verbose_name="kaynak", null=True, blank=True,
        on_delete=models.PROTECT, related_name="aday_musteriler")
    tip = models.ForeignKey(
        AdayTipTanim, verbose_name="tip", on_delete=models.PROTECT,
        related_name="aday_musteriler")
    potansiyel = models.ForeignKey(
        AdayPotansiyelTanim, verbose_name="potansiyel", null=True, blank=True,
        on_delete=models.PROTECT, related_name="aday_musteriler")
    asama = models.ForeignKey(
        AdayAsamaTanim, verbose_name="aşama", on_delete=models.PROTECT,
        related_name="aday_musteriler")
    kapanis_nedeni = models.CharField("kapanış nedeni", max_length=20,
                                      choices=KapanisNedeni.choices, blank=True, default="")
    # Sonraki adım tarihi doluyken metin boş olamaz (form/servis kuralı) — tersi serbest.
    sonraki_adim = models.CharField("sonraki adım", max_length=200, blank=True, default="")
    sonraki_adim_tarihi = models.DateField("sonraki adım tarihi", null=True, blank=True,
                                           db_index=True)
    para_birimi = models.CharField("para birimi", max_length=3, choices=PARA_CHOICES,
                                   default="TRY")
    iskonto_yuzdesi = models.DecimalField(
        "varsayılan iskonto %", max_digits=5, decimal_places=2, default=0)
    # Cariye dönüştürülünce açılan/bağlanan gerçek Cari — dönüşüm tek seferlik, servis
    # tekrar dönüştürmeyi engeller (TeklifSiparis.kaynak_teklif ile aynı invariant).
    cari = models.ForeignKey(
        Cari, verbose_name="cari", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kaynak_adaylar")
    cariye_donusum_tarihi = models.DateTimeField("cariye dönüşüm tarihi", null=True, blank=True)

    class Meta:
        db_table = "aday_musteri"
        verbose_name = "aday müşteri"
        verbose_name_plural = "aday müşteriler"
        ordering = ["-created_at"]

    def __str__(self):
        return self.unvan

    @property
    def takip_durum(self):
        """'gecmis' / 'bugun' / 'ileri' / "" — sonraki_adim_tarihi'nin TR bugününe göre
        konumu (liste/detay renk kuralı + '⏰ Takip' rozeti TEK kaynak)."""
        if not self.sonraki_adim_tarihi:
            return ""
        from core.tarih import tr_bugun
        bugun = tr_bugun()
        if self.sonraki_adim_tarihi < bugun:
            return "gecmis"
        if self.sonraki_adim_tarihi == bugun:
            return "bugun"
        return "ileri"

    @property
    def son_aktivite_gun_once(self):
        """Liste ekranının ``son_aktivite_tarihi`` annotate'ine kaç gün önce olduğu (int)
        veya hiç aktivite yoksa None — 90+ gün gri gösterim kuralı TEK kaynak (bkz.
        aday_musteriler view'ı, yalnız annotate edilmiş sorgularda dolu)."""
        tarih = getattr(self, "son_aktivite_tarihi", None)
        if not tarih:
            return None
        from core.tarih import tr_bugun
        return (tr_bugun() - tarih).days

    @property
    def son_aktivite_turu_etiket(self):
        """``son_aktivite_turu`` annotate'i (ham AdayAktivite.Tur değeri) -> görünen etiket.
        Annotate edilmiş bir alan gerçek model alanı olmadığından Django'nun otomatik
        ``get_FOO_display()``'i burada YOK — TEK kaynak bu property."""
        tur = getattr(self, "son_aktivite_turu", None)
        return AdayAktivite.Tur(tur).label if tur else ""

    @property
    def eposta_kanal_durum(self):
        """'gecerli' / 'gecersiz' / "" — liste ekranının ✉ kanal simgesi için TEK kaynak."""
        if (self.eposta and not self.eposta_gecersiz) or (self.eposta_2 and not self.eposta_2_gecersiz):
            return "gecerli"
        if self.eposta or self.eposta_2:
            return "gecersiz"
        return ""


class AdayMusteriKategori(TemelModel):
    """Aday KAYNAĞI (CRM) — "adayın nereden geldiği" (eski firma verisi, fuar, tavsiye,
    ziyaret…). Model/tablo/alan adı tarihsel nedenlerle "Kategori" kalır (migration riski),
    yalnız KULLANICIYA GÖRÜNEN metin "Kaynak"tır (bkz. verbose_name, core/urls.py
    crm/kaynaklar/, core/moduller.py). CariKategori ile aynı desen (2 seviye: ÜST → ALT),
    ama Cari'nin muhasebe/kod-numaralama ihtiyacından bağımsız, CRM'e özel ayrı bir ağaç."""

    ad = models.CharField("ad", max_length=100)
    kod = models.CharField("kod", max_length=10)
    ust = models.ForeignKey(
        "self", verbose_name="üst kaynak", null=True, blank=True,
        on_delete=models.PROTECT, related_name="alt_kategoriler")

    class Meta:
        db_table = "aday_musteri_kategori"
        verbose_name = "aday kaynağı"
        verbose_name_plural = "aday kaynakları"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(
                fields=["ust", "kod"], condition=models.Q(silindi=False),
                nulls_distinct=False, name="uq_adaykat_ust_kod_aktif"),
            models.UniqueConstraint(
                fields=["ust", "ad"], condition=models.Q(silindi=False),
                nulls_distinct=False, name="uq_adaykat_ust_ad_aktif"),
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


class AdayYetkili(TemelModel):
    """Adaya ait yetkili kişi (çoklu) — CariYetkili ile BİREBİR aynı alan şekli (bkz.
    dosya başı ilke: Cariye Dönüştür'ün ileride bu alanları birebir aktarabilmesi için)."""

    aday = models.ForeignKey(AdayMusteri, verbose_name="aday", related_name="yetkililer",
                             on_delete=models.CASCADE)
    ad_soyad = models.CharField("ad soyad", max_length=120)
    unvan = models.CharField("görev/unvan", max_length=80, blank=True)
    telefon = models.CharField("telefon", max_length=20, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    notlar = models.CharField("notlar", max_length=200, blank=True)
    whatsapp = models.BooleanField("bu numara WhatsApp kullanıyor", default=False)

    class Meta:
        db_table = "aday_yetkili"
        verbose_name = "yetkili kişi"
        verbose_name_plural = "yetkili kişiler"
        ordering = ["ad_soyad"]

    def __str__(self):
        return self.ad_soyad


class AdayAktivite(TemelModel):
    """Adayla yapılan görüşme/temas kaydı (çoklu) — CariAktivite ile aynı desen."""

    class Tur(models.TextChoices):
        GORUSME = "GORUSME", "Görüşme"
        TELEFON = "TELEFON", "Telefon"
        TOPLANTI = "TOPLANTI", "Toplantı"
        EPOSTA = "EPOSTA", "E-posta"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        NOT = "NOT", "Not"

    aday = models.ForeignKey(AdayMusteri, verbose_name="aday", related_name="aktiviteler",
                             on_delete=models.CASCADE)
    tarih = models.DateField("tarih")
    tur = models.CharField("tür", max_length=10, choices=Tur.choices, default=Tur.NOT)
    aciklama = models.TextField("açıklama")

    class Meta:
        db_table = "aday_aktivite"
        verbose_name = "aday aktivite"
        verbose_name_plural = "aday aktiviteler"
        ordering = ["-tarih", "-id"]
        indexes = [
            models.Index(fields=["aday", "-tarih"], name="ix_aday_aktivite_aday_tarih"),
            # Liste ekranının arama kutusu aciklama'yı icontains ile tarar (bkz.
            # YevmiyeFisi.aciklama'daki aynı desen, migration 0010) — trigram GIN olmadan
            # her arama tüm tabloyu satır satır tarar.
            GinIndex(fields=["aciklama"], name="gin_aday_akt_aciklama", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return f"{self.aday.unvan} — {self.get_tur_display()} ({self.tarih})"


class AdayAktiviteEk(TemelModel):
    """Aday aktivitesine eklenen dosya (çoklu) — CariAktiviteEk ile aynı desen: resim
    yüklemede WebP'ye küçültülür (spec invariant'ı), PDF olduğu gibi saklanır."""

    aktivite = models.ForeignKey(AdayAktivite, verbose_name="aktivite", related_name="ekler",
                                 on_delete=models.CASCADE)
    # Gizli: özel depoda (MEDIA_ROOT dışı), yalnız yetkili görünümle sunulur (bkz. core.storage).
    dosya = models.FileField("dosya", storage=ozel_depo, upload_to=aday_ek_yolu)
    orijinal_ad = models.CharField("orijinal dosya adı", max_length=255, blank=True)

    class Meta:
        db_table = "aday_aktivite_ek"
        verbose_name = "aktivite eki"
        verbose_name_plural = "aktivite ekleri"
        ordering = ["id"]

    def __str__(self):
        return self.orijinal_ad or self.dosya.name

    @property
    def resim_mi(self):
        return self.dosya.name.lower().endswith(".webp")


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


class TanimSecenegi(TemelModel):
    """AYARLAR > Tanım Listeleri'ndeki basit seçenek listeleri (kod + ad + sıra) — tek
    modelde, ``kategori`` ile ayrılır: Yükleme Şekli, Ödeme Koşulu, Yükleme Tipi. Satış
    Teklifi bu listelerden seçer. Yükleme Tipi'nde ``kod`` zorunlu ve Stok'un konteyner/TIR
    adet alanına eşlenir (bkz. YUKLEME_ALANI) — navlun dağıtımı bu eşlemeyi kullanır."""

    class Kategori(models.TextChoices):
        YUKLEME_SEKLI = "YUKLEME_SEKLI", "Yükleme Şekli"
        ODEME_KOSULU = "ODEME_KOSULU", "Ödeme Koşulu"
        YUKLEME_TIPI = "YUKLEME_TIPI", "Yükleme Tipi"
        TESLIM_SURESI = "TESLIM_SURESI", "Teslim Süresi"

    # Yükleme Tipi kodu -> Stok'taki "bu tipe kaç adet sığar" alanı.
    YUKLEME_ALANI = {"20DC": "yukleme_20dc", "40HQ": "yukleme_40hq", "TIR": "yukleme_tir"}

    kategori = models.CharField("kategori", max_length=15, choices=Kategori.choices)
    kod = models.CharField("kod", max_length=30, blank=True, default="")
    ad = models.CharField("ad", max_length=200)
    # İngilizce teklif PDF'inde gösterilen karşılık; boşsa ``ad`` kullanılır.
    ad_en = models.CharField("ad (İngilizce)", max_length=200, blank=True, default="")
    sira = models.PositiveSmallIntegerField("sıra", default=0)
    # Kategori başına EN FAZLA bir aktif satır True olabilir (bkz. constraint) — yeni Satış
    # Teklifi formu açılırken ilgili alan bu satırla önceden seçili gelir.
    varsayilan = models.BooleanField("varsayılan", default=False)

    class Meta:
        db_table = "tanim_secenegi"
        verbose_name = "tanım seçeneği"
        verbose_name_plural = "tanım seçenekleri"
        ordering = ["kategori", "sira", "ad"]
        constraints = [
            models.UniqueConstraint(
                fields=["kategori", "ad"], condition=models.Q(silindi=False),
                name="uq_tanim_secenegi_kategori_ad_aktif"),
            models.UniqueConstraint(
                fields=["kategori", "kod"],
                condition=models.Q(silindi=False) & ~models.Q(kod=""),
                name="uq_tanim_secenegi_kategori_kod_aktif"),
            models.UniqueConstraint(
                fields=["kategori"], condition=models.Q(varsayilan=True, silindi=False),
                name="uq_tanim_secenegi_varsayilan_kategori"),
        ]

    def __str__(self):
        return f"{self.kod} {self.ad}".strip() if self.kod else self.ad

    def ad_dil(self, dil):
        """Teklif PDF'i için dile göre ad ('en' -> ad_en, boşsa ad)."""
        return (self.ad_en or self.ad) if dil == "en" else self.ad


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

    class Durum(models.TextChoices):
        TASLAK = "TASLAK", "Taslak"
        ONAYLI = "ONAYLI", "Onaylı"

    # tip boş olabilir: İrsaliye'den otomatik açılan taslak faturanın tipi henüz bilinmez
    # (kullanıcı onaylamadan önce seçer — fatura_onayla tip olmadan onaylamayı reddeder).
    tip = models.ForeignKey(
        FaturaTipi, verbose_name="fatura tipi", related_name="faturalar", null=True, blank=True,
        on_delete=models.PROTECT)
    # tip boşken bile yön bilinsin diye ayrıca saklanır (normalde tip.yon'dan türetilebilirdi,
    # ama tip=None durumunda listelerde görünmesi ve view'ların çökmemesi için gerekli).
    yon = models.CharField(
        "yön", max_length=5, choices=FaturaTipi.Yon.choices, null=True, blank=True)
    durum = models.CharField("durum", max_length=6, choices=Durum.choices,
                             default=Durum.TASLAK)
    cari = models.ForeignKey(
        Cari, verbose_name="cari", related_name="faturalar", on_delete=models.PROTECT)
    tarih = models.DateField("fatura tarihi")
    # Yalnız ALIŞ ekleme/düzenleme ekranında gösterilir (bkz. fatura_ekle.html); hiçbir
    # otomasyonu (hatırlatma/rapor) yok, salt bilgi amaçlı serbest tarih alanı.
    vade_tarihi = models.DateField("vade tarihi", null=True, blank=True)
    fatura_no = models.CharField("fatura no", max_length=50, blank=True)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    kur = models.DecimalField("kur (TL)", max_digits=18, decimal_places=6, default=1)
    # TASLAK faturada kullanıcının ELLE girdiği kur: onayda aynen kullanılır (boşsa sistem, carinin
    # kur tipine göre atar). ``kur`` TASLAK'ta yer tutucu (1) olduğundan ayrı alan gerekir.
    taslak_kur = models.DecimalField("taslakta girilen kur", max_digits=18, decimal_places=6,
                                     null=True, blank=True)
    fis = models.ForeignKey(
        YevmiyeFisi, verbose_name="yevmiye fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="faturalar")
    # Stok hareketlerinin yazılacağı depo (alış→giriş, satış→çıkış). Boşsa hareket üretilmez.
    depo = models.ForeignKey(
        "Depo", verbose_name="depo", null=True, blank=True,
        on_delete=models.PROTECT, related_name="faturalar")
    aciklama = models.CharField("açıklama", max_length=300, blank=True, default="")
    # Ortak adına (şahsi) alış: şirkete kesilmiş ama ortağın şahsi harcaması olan GİDER
    # faturası. İşaretliyse kalem(ler)in KDV DAHİL tutarı sahsi_ortak'a (131 ailesi) borçlanır,
    # KDV normal 191'e borçlanır AMA aynı tutar "FAZLA KDV" (602.01) hesabına alacak yazılır —
    # bkz. core.services.fatura (_satir_coz/_hazirla/_muhasebe_satirlari). Yalnız ALIŞ+GİDER
    # faturasında anlamlıdır; servis zorlar.
    sahsi_alis = models.BooleanField("ortak adına (şahsi) alış", default=False)
    # Yalnız tip.stopajli faturada dolu: brüt ücret (ara toplam) × oran/100 = GV stopajı.
    gv_stopaj_orani = models.DecimalField(
        "GV stopaj oranı (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    sahsi_ortak = models.ForeignKey(
        HesapPlani, verbose_name="ortak hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="sahsi_alis_faturalari")

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
    def gv_stopaj_tutari(self):
        """GV stopajı = brüt ücret (ara toplam) × oran/100 (yalnız stopajlı faturada)."""
        from decimal import Decimal
        from core.sayi import yuvarla
        if not self.gv_stopaj_orani:
            return Decimal("0.00")
        return yuvarla(self.ara_toplam * self.gv_stopaj_orani / Decimal("100"), 2)

    @property
    def satir_tipleri(self):
        """Faturadaki satır türleri ("Stok · Hesap · Demirbaş") — liste/detayda görünür."""
        adlar = []
        for x in self.satirlar.all():
            if x.silindi:
                continue
            if x.satir_tipi_ad not in adlar:
                adlar.append(x.satir_tipi_ad)
        return " · ".join(adlar)

    @property
    def odenecek(self):
        """Carinin borç/alacağı = mal + KDV − tevkifat − GV stopajı (ikisi de karşı tarafa
        ödenmez, vergi dairesine yatar)."""
        if self.tip_id and self.tip.yalniz_kdv:            # matrah başka yoldan yazılmış: cariye yalnız net KDV
            return self.kdv_toplam - self.tevkifat_toplam
        return self.genel_toplam - self.tevkifat_toplam - self.gv_stopaj_tutari


class YatirimProjesi(TemelModel):
    """Duran Varlık modülü FAZ 1 — 258 (Yapılmakta Olan Yatırımlar) hesabında biriken alış
    gider faturası kalemlerinin gruplandığı proje kartı. FAZ 3'te "Aktifleştir" ile 258
    bakiyesi tek fişle ilgili duran varlık hesabına (253/254/255/260) aktarılır ve durum
    AKTIFLESTI'ye geçer; o andan sonra projeye yeni kalem eklenemez (bkz. core.services.
    yatirim_projesi). Amortisman/257/268/264 bu modülün HİÇBİR fazında YOK (CLAUDE.md
    görev kapsamı — bilinçli olarak dışarıda)."""

    class Durum(models.TextChoices):
        DEVAM = "DEVAM", "Devam Ediyor"
        AKTIFLESTI = "AKTIFLESTI", "Aktifleşti"
        KAPANDI = "KAPANDI", "Kapandı"      # 258 bakiyesi 0 (satıldı / aktarıldı): fiş üretmeden kapatılır; "yeniden aç" ile geri alınır

    class KapanisNedeni(models.TextChoices):
        SATILDI = "SATILDI", "Satıldı"
        DIGER = "DIGER", "Diğer"

    kod = models.CharField("proje kodu", max_length=20)
    ad = models.CharField("ad", max_length=200)
    aciklama = models.TextField("açıklama", blank=True)
    durum = models.CharField("durum", max_length=12, choices=Durum.choices, default=Durum.DEVAM)
    aktiflestirme_fisi = models.ForeignKey(
        YevmiyeFisi, verbose_name="aktifleştirme fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="yatirim_projesi_aktiflestirmeleri")
    kapanis_tarihi = models.DateField("kapanış tarihi", null=True, blank=True)
    kapanis_nedeni = models.CharField("kapanış nedeni", max_length=10, choices=KapanisNedeni.choices, blank=True)
    kapanis_aciklama = models.TextField("kapanış açıklaması", blank=True)
    # Projeye özel 258.0X.000N muhasebe hesabı (grup: 258.01 makine-teçhizat ... 258.04 arsa); projeye yazılan TÜM 258
    # satırları bu hesaba gider (bkz. core.services.duran_hesap). Proje silinse/aktifleşse de hesap geçmiş için kalır.
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="yatirim_projeleri")

    class Meta:
        db_table = "yatirim_projesi"
        verbose_name = "yatırım projesi"
        verbose_name_plural = "yatırım projeleri"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_yatirim_projesi_kod_aktif"),
        ]

    def __str__(self):
        return f"{self.kod} — {self.ad}"


class FaturaEk(TemelModel):
    """Faturaya eklenen belge (çoklu): asıl fatura PDF'i/görseli (ör. Luca'dan indirilen).
    Gizli: özel depoda (MEDIA_ROOT dışı), yalnız yetkili görünümle sunulur (bkz. core.storage).
    Resim WebP'ye küçültülür, PDF olduğu gibi saklanır; muhasebeyi etkilemez."""

    fatura = models.ForeignKey(Fatura, verbose_name="fatura", related_name="ekler",
                               on_delete=models.CASCADE)
    dosya = models.FileField("dosya", storage=ozel_depo, upload_to=fatura_ek_yolu)
    orijinal_ad = models.CharField("orijinal dosya adı", max_length=255, blank=True)

    class Meta:
        db_table = "fatura_ek"
        verbose_name = "fatura eki"
        verbose_name_plural = "fatura ekleri"
        ordering = ["id"]

    def __str__(self):
        return self.orijinal_ad or self.dosya.name

    @property
    def resim_mi(self):
        return self.dosya.name.lower().endswith(".webp")


class FaturaSatir(TemelModel):
    """Fatura kalemi: stok × miktar × birim fiyat (+ KDV oranı snapshot).

    GİDER faturasında (FaturaTipi.gider) kalem STOK değil doğrudan bir GİDER HESABIDIR
    (`hesap`, yaprak — duran varlık hesapları 253/254/255/258/260 DAHİL, bkz. core.services.
    hesap_plani.duran_varlik_hesaplari) ve KDV oranı satırda seçilir; her satırda stok VE
    hesaptan TAM biri dolu olur (DB kısıtı). ``yatirim_projesi`` yalnız duran varlık
    hesaplarında anlamlıdır (258'de ZORUNLU, 253/254/255/260'ta opsiyonel — serviste
    zorlanır, bkz. core.services.fatura._satir_coz)."""

    fatura = models.ForeignKey(
        Fatura, verbose_name="fatura", related_name="satirlar", on_delete=models.CASCADE)
    stok = models.ForeignKey(
        Stok, verbose_name="stok", related_name="fatura_satirlari", on_delete=models.PROTECT,
        null=True, blank=True)
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="gider hesabı", related_name="fatura_satirlari",
        on_delete=models.PROTECT, null=True, blank=True)
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
    yatirim_projesi = models.ForeignKey(
        YatirimProjesi, verbose_name="yatırım projesi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="fatura_satirlari")
    # SATIŞ faturasında DEMİRBAŞ (duran varlık) SATIŞ satırı: satılan kart. Bu satırda ``hesap`` = kartın 25x
    # hesabı (stok-veya-hesap kısıtı için), miktar 1, birim fiyat = satış bedeli (bkz. core.services.fatura).
    demirbas = models.ForeignKey(
        "DuranVarlik", verbose_name="satılan demirbaş", null=True, blank=True,
        on_delete=models.PROTECT, related_name="satis_satirlari")
    # DÖNEMSEL GİDER (alış gider faturası): satır ``hesap`` = faturaya özel 180.xx.000N hesabı; gider hesabı + dönem burada tutulur,
    # aylık dağıtım fişleri bu bilgiyle üretilir (bkz. core.services.donemsel_gider).
    donem_baslangic = models.DateField("dönem başlangıcı", null=True, blank=True)
    donem_bitis = models.DateField("dönem bitişi", null=True, blank=True)
    donem_gider = models.ForeignKey(
        HesapPlani, verbose_name="dönemsel gider hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="donemsel_satirlar")
    donem_aciklama = models.CharField("dönemsel gider açıklaması", max_length=200, blank=True)

    class Meta:
        db_table = "fatura_satir"
        verbose_name = "fatura satırı"
        verbose_name_plural = "fatura satırları"
        ordering = ["fatura", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0), name="ck_fatura_satir_miktar_gt0"),
            models.CheckConstraint(condition=models.Q(birim_fiyat__gte=0), name="ck_fatura_satir_fiyat_gte0"),
            # Bir kalem ya stok ya gider hesabıdır (ikisi birden / hiçbiri olamaz).
            models.CheckConstraint(
                condition=(models.Q(stok__isnull=False, hesap__isnull=True)
                           | models.Q(stok__isnull=True, hesap__isnull=False)),
                name="ck_fatura_satir_stok_veya_hesap"),
        ]

    def __str__(self):
        return f"{self.stok_id or self.hesap_id} x {self.miktar}"

    @property
    def satir_tipi(self):
        """STOK / HESAP (gider·258 hesap satırı) / DEMIRBAS (demirbaş satışı)."""
        if self.demirbas_id:
            return "DEMIRBAS"
        return "HESAP" if self.hesap_id else "STOK"

    @property
    def satir_tipi_ad(self):
        return {"STOK": "Stok", "HESAP": "Hesap", "DEMIRBAS": "Demirbaş"}[self.satir_tipi]

    @property
    def tutar(self):
        from core.sayi import yuvarla
        return yuvarla(self.miktar * self.birim_fiyat, 2)

    @property
    def tutar_tl(self):
        """TL karşılığı = tutar × fatura kuru (TRY'de fatura.kur=1, değişmez). Proje
        toplamı/duran varlık kart maliyeti BUNU kullanmalı — ``tutar`` döviz faturada
        TL değil fatura para birimindedir (bkz. core.services.fatura._muhasebe_satirlari,
        aynı dönüşümü fiş tarafında zaten doğru yapan referans kod)."""
        from core.sayi import yuvarla
        return yuvarla(self.tutar * self.fatura.kur, 2)

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


class DuranVarlik(TemelModel):
    """Duran varlık kartı (DURAN VARLIK FAZ 2) — 253/254/255/260 hesaplarından birine
    bağlı somut bir sabit kıymetin basit kaydı (bkz. core.services.duran_varlik).
    Amortisman/257/268/264 bu modülün HİÇBİR fazında YOK (CLAUDE.md görev kapsamı —
    bilinçli olarak dışarıda). ``kaynak=PROJE`` yalnız FAZ 3'teki yatırım projesi
    aktifleştirme akışıyla üretilecek — bu fazda UI'dan seçilemez."""

    class Durum(models.TextChoices):
        AKTIF = "AKTIF", "Aktif"
        PASIF = "PASIF", "Pasif"
        SATILDI = "SATILDI", "Satıldı"
        BOLUNDU = "BOLUNDU", "Bölündü"      # birden çok adet tek kartta tutuluyordu → yeni kartlara bölündü (kısmi satış için); satışta seçilemez

    class Kaynak(models.TextChoices):
        FATURA = "FATURA", "Fatura"
        PROJE = "PROJE", "Yatırım Projesi"
        ACILIS = "ACILIS", "Açılış"

    demirbas_kodu = models.CharField("demirbaş kodu", max_length=20)
    ad = models.CharField("ad", max_length=200)
    hesap = models.ForeignKey(
        HesapPlani, verbose_name="muhasebe hesabı", on_delete=models.PROTECT,
        related_name="duran_varliklar")
    aktiflestirme_tarihi = models.DateField("aktifleştirme tarihi")
    maliyet = models.DecimalField("maliyet (KDV hariç, TRY)", max_digits=18, decimal_places=2)
    marka_model = models.CharField("marka / model", max_length=200, blank=True)
    seri_no = models.CharField("seri no", max_length=100, blank=True)
    durum = models.CharField("durum", max_length=10, choices=Durum.choices, default=Durum.AKTIF)
    notlar = models.TextField("notlar", blank=True)
    # Birikmiş amortisman (257) — karttan elle girilir (amortisman hesaplaması bu modülde yok); demirbaş satışında
    # 257'ye BORÇ yazılır, defter değeri = maliyet − birikmiş amortisman.
    birikmis_amortisman = models.DecimalField("birikmiş amortisman (TRY)", max_digits=18, decimal_places=2,
                                              default=0)
    # Satış faturasındaki "Demirbaş satırı" ile satıldığında doldurulur; fatura silinirse kart eski durumuna döner.
    satis_tarihi = models.DateField("satış tarihi", null=True, blank=True)
    satis_faturasi = models.ForeignKey(
        "Fatura", verbose_name="satış faturası", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="satilan_demirbaslar")
    kaynak = models.CharField("kaynak", max_length=10, choices=Kaynak.choices)
    yatirim_projesi = models.ForeignKey(
        YatirimProjesi, verbose_name="yatırım projesi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="duran_varliklar")
    # Elle (ACILIS) açılan kartta opsiyonel karşı hesap (500.10.xxxx / 100 / 102): doluysa kart kaydedilince otomatik fiş
    # (kart hesabı BORÇ / karşı hesap ALACAK, tarih = aktifleştirme tarihi); kart düzenlenince fiş güncellenir, silinince silinir.
    karsi_hesap = models.ForeignKey(
        HesapPlani, verbose_name="karşı hesap", null=True, blank=True,
        on_delete=models.PROTECT, related_name="duran_varlik_karsi_kartlar")
    fis = models.ForeignKey(
        YevmiyeFisi, verbose_name="açılış fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="duran_varlik_acilislari")
    fatura_satirlari = models.ManyToManyField(
        FaturaSatir, verbose_name="fatura kalemleri", blank=True,
        related_name="duran_varliklar")
    # Bölme (kısmi satış): bu kart hangi karttan bölündü (aynı muhasebe hesabında kalır; bölme fiş yazmaz). Orijinal kart durum=BOLUNDU olur.
    bolunen_kart = models.ForeignKey(
        "self", verbose_name="bölündüğü kart", null=True, blank=True, on_delete=models.PROTECT, related_name="bolunmus_kartlar")

    class Meta:
        db_table = "duran_varlik"
        verbose_name = "duran varlık"
        verbose_name_plural = "duran varlıklar"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["demirbas_kodu"], condition=models.Q(silindi=False),
                                    name="uq_duran_varlik_kod_aktif"),
            models.CheckConstraint(condition=models.Q(maliyet__gte=0),
                                   name="ck_duran_varlik_maliyet_gte0"),
        ]

    def __str__(self):
        return f"{self.demirbas_kodu} — {self.ad}"


class TeklifSiparis(TemelModel):
    """Satınalma/Satış Teklifi veya Siparişi — TİCARİ belge, yevmiye ÜRETMEZ ve stok
    hareketi YARATMAZ (muhasebe ve stok her zaman faturayla girer). belge_tur × yon
    ekranları tek modelden besler. Tutarlar kalemlerden (saklanmaz).

    PROFORMA yalnız SATIŞ yönünde var: zincir Teklif → Proforma → Sipariş (→ Fatura) —
    Teklif katalog fiyat listesi (miktar anlamsız), Proforma müşterinin istediği GERÇEK
    miktarlarla hazırlanır. Proforma→Sipariş dönüşümü aday müşteride ENGELLENİR (bkz.
    proformayi_siparise_cevir) — sipariş gerçek muhasebe/stok zincirine girdiği için önce
    aday Cariye dönüştürülmüş olmalı."""

    class BelgeTur(models.TextChoices):
        TEKLIF = "TEKLIF", "Teklif"
        PROFORMA = "PROFORMA", "Proforma"
        SIPARIS = "SIPARIS", "Sipariş"
        IRSALIYE = "IRSALIYE", "İrsaliye"

    class Yon(models.TextChoices):
        ALIS = "ALIS", "Alış"
        SATIS = "SATIS", "Satış"

    class Durum(models.TextChoices):
        TASLAK = "TASLAK", "Taslak"
        ONAYLI = "ONAYLI", "Onaylı"
        # Aşağıdaki 5 durum YALNIZ belge_tur=TEKLIF, yon=SATIS için kullanılır (bkz.
        # core.services.teklif_siparis.teklif_gonder/kabul_et/reddet/iptal_et) — diğer 6
        # belge_tur/yon kombinasyonu (Satınalma Teklif/Sipariş/İrsaliye, Satış Proforma/
        # Sipariş) hep TASLAK/ONAYLI kullanmaya devam eder, bu değerleri hiç görmez.
        GONDERILDI = "GONDERILDI", "Gönderildi"
        KABUL = "KABUL", "Kabul Edildi"
        RED = "RED", "Reddedildi"
        SURESI_DOLDU = "SURESI_DOLDU", "Süresi Doldu"
        IPTAL = "IPTAL", "İptal Edildi"

    belge_tur = models.CharField("belge türü", max_length=8, choices=BelgeTur.choices)
    yon = models.CharField("yön", max_length=5, choices=Yon.choices)
    durum = models.CharField("durum", max_length=12, choices=Durum.choices,
                             default=Durum.TASLAK)
    # Yalnız durum=RED iken anlamlı (Satış Teklifi) — isteğe bağlı kısa açıklama.
    red_nedeni = models.CharField("red nedeni", max_length=300, blank=True, default="")
    # cari / aday_musteri karşılıklı dışlayıcı (bkz. ck_teklif_siparis_cari_xor_aday_musteri) —
    # yalnız SATIŞ+TEKLİF/PROFORMA'da aday müşteriye (CRM lead, henüz Cari değil) belge
    # açılabilir; SATIŞ+SIPARIS dahil diğer tüm belge türlerinde her zaman cari doludur.
    cari = models.ForeignKey(
        Cari, verbose_name="cari", related_name="teklif_siparisler", null=True, blank=True,
        on_delete=models.PROTECT)
    aday_musteri = models.ForeignKey(
        "AdayMusteri", verbose_name="aday müşteri", related_name="teklif_siparisler",
        null=True, blank=True, on_delete=models.PROTECT)
    tarih = models.DateField("belge tarihi")
    # Teklifte geçerlilik, siparişte teslim tarihi — tek alan, etiket ekranda değişir.
    gecerlilik_teslim_tarihi = models.DateField(
        "geçerlilik / teslim tarihi", null=True, blank=True)
    # belge_no OTOMATİK üretilir (yil+sira'dan, kullanıcı girmez/değiştirmez) — insana görünen
    # numara müteselsil/boşluksuz, iç PK'dan ayrı (fiş no ile aynı invariant/desen).
    belge_no = models.CharField("belge no", max_length=50, blank=True, editable=False)
    yil = models.PositiveSmallIntegerField("yıl", null=True, blank=True, editable=False)
    sira = models.PositiveIntegerField("sıra", null=True, blank=True, editable=False)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")
    # Fatura.kur ile aynı şekil, ama NULL olabilir (Fatura'nın aksine TASLAK'ta bir "1"
    # yer tutucusu yok — kur hiç kaydedilmemiş bile olabilir). Doluysa (kullanıcı elle
    # girdi/değiştirdi ya da JS otomatik doldurdu) İrsaliye onayında bunun YERİNE
    # kullanılır — bkz. core.services.teklif_siparis._irsaliye_stok_hareketi_yaz. Yalnız
    # İRSALİYE'de gerçek bir etkisi var; Teklif/Proforma/Sipariş'te salt önizleme/
    # dönüşüm-zincirinde taşınan bir değer olarak saklanır.
    kur = models.DecimalField("kur (TL)", max_digits=18, decimal_places=6,
                              null=True, blank=True)
    aciklama = models.CharField("açıklama", max_length=500, blank=True)
    # Yalnız SATIŞ+TEKLİF/PROFORMA ekranlarında doldurulur — AYARLAR > Tanım Listeleri'nden
    # seçilir (TanimSecenegi, kategoriye göre). Diğer belge türlerinde hep boş.
    yukleme_sekli = models.ForeignKey(
        TanimSecenegi, verbose_name="yükleme şekli", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    odeme_kosulu = models.ForeignKey(
        TanimSecenegi, verbose_name="ödeme koşulu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    yukleme_tipi = models.ForeignKey(
        TanimSecenegi, verbose_name="yükleme tipi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    # Yalnız Satış Teklifi/Proforması'nda kullanılır (SatisBelgeBaslikForm) — üretim/
    # hazırlık süresi.
    teslim_suresi = models.ForeignKey(
        TanimSecenegi, verbose_name="teslim süresi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    # Belge düzeyinde tek navlun; kalem başına dağıtımı hesaplanır, saklanmaz
    # (TeklifSiparisKalem.navlun_payi — seçilen yükleme tipine sığan adede bölünür).
    navlun_tutari = models.DecimalField(
        "navlun tutarı", max_digits=18, decimal_places=2, null=True, blank=True)
    # Hangi TEKLIF'ten dönüştürüldüğü (self-FK). ALIŞ'ta hedef SIPARIS (otomatik), SATIŞ'ta
    # hedef PROFORMA (elle) — related_name bu yüzden yöne göre Sipariş VEYA Proforma
    # taşıyabilen genel bir isim ("donusen_belgeler"). Tek seferlik dönüşüm — servis
    # katmanı zaten dönüştürülmüş teklifi tekrar çevirmeyi engeller.
    kaynak_teklif = models.ForeignKey(
        "self", verbose_name="kaynak teklif", null=True, blank=True,
        on_delete=models.PROTECT, related_name="donusen_belgeler")
    # SATIŞ+SIPARIS ise: hangi PROFORMA'dan dönüştürüldüğü (self-FK). related_name
    # "donusen_siparisler" — PROFORMA'nın kendisi için burası her zaman gerçek bir Sipariş'i
    # taşır (aday müşteride bu dönüşüm zaten engellenir, bkz. proformayi_siparise_cevir).
    kaynak_proforma = models.ForeignKey(
        "self", verbose_name="kaynak proforma", null=True, blank=True,
        on_delete=models.PROTECT, related_name="donusen_siparisler")
    # IRSALIYE ise: hangi SIPARIS'ten dönüştürüldüğü (self-FK, bir kademe aşağısı — yalnız
    # ALIŞ zincirinde kullanılır).
    kaynak_siparis = models.ForeignKey(
        "self", verbose_name="kaynak sipariş", null=True, blank=True,
        on_delete=models.PROTECT, related_name="donusen_irsaliyeler")
    # Yalnız SATIŞ+PROFORMA'da kullanılır — proformanın para birimine uygun FİNANS >
    # Banka hesabı (PDF + detay sayfasında gösterilir). Boşsa banka bilgisi hiç gösterilmez.
    banka_hesabi = models.ForeignKey(
        "BankaHesap", verbose_name="banka hesabı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="teklif_siparisler")
    # IRSALIYE ise: malın girdiği depo (gerçek stok hareketi için gerekli — serviste zorunlu).
    depo = models.ForeignKey(
        "Depo", verbose_name="depo", null=True, blank=True,
        on_delete=models.PROTECT, related_name="teklif_siparisler")
    # IRSALIYE ise: tedarikçinin KENDİ (kağıt) irsaliye numarası — bizim otomatik belge_no'muzdan
    # ayrı, serbest metin, opsiyonel (Fatura.fatura_no ile aynı desen).
    irsaliye_no = models.CharField("irsaliye no", max_length=50, blank=True)
    # SIPARIS/IRSALIYE ise: hangi Fatura'ya dönüştürüldüğü. Fatura modülü TeklifSiparis'i BİLMEZ —
    # bağlantı burada, sipariş/irsaliye tarafında kurulur (Fatura oluştuktan SONRA set edilir).
    fatura = models.ForeignKey(
        "Fatura", verbose_name="fatura", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kaynak_siparisler")

    class Meta:
        db_table = "teklif_siparis"
        verbose_name = "teklif / sipariş"
        verbose_name_plural = "teklif / siparişler"
        ordering = ["-tarih", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["belge_tur", "yon", "yil", "sira"],
                condition=models.Q(sira__isnull=False),
                name="uq_teklif_siparis_tur_yon_yil_sira"),
            models.CheckConstraint(
                condition=models.Q(navlun_tutari__isnull=True) | models.Q(navlun_tutari__gte=0),
                name="ck_teklif_siparis_navlun_gte0"),
            models.CheckConstraint(
                condition=(models.Q(cari__isnull=False, aday_musteri__isnull=True)
                          | models.Q(cari__isnull=True, aday_musteri__isnull=False)),
                name="ck_teklif_siparis_cari_xor_aday_musteri"),
            models.CheckConstraint(
                condition=models.Q(kur__isnull=True) | models.Q(kur__gt=0),
                name="ck_teklif_siparis_kur_gt0"),
        ]

    def __str__(self):
        return f"{self.get_belge_tur_display()} {self.belge_no} ({self.cari_id or self.aday_musteri_id})"

    @property
    def taraf(self):
        """Belgenin karşı tarafı: SATIŞ TEKLİFİ'nde bir Cari veya bir AdayMusteri (CRM lead)
        olabilir (cari/aday_musteri karşılıklı dışlayıcı); diğer tüm belge türlerinde her
        zaman bir Cari'dir. Cari ile AdayMusteri ortak alan adları (unvan/ilgili_kisi/telefon/
        eposta/ulke/sehir) paylaştığı için şablonlar/PDF context'i tek bu alandan okuyabilir."""
        return self.cari or self.aday_musteri

    # --- Görüntüleme toplamları (belge para biriminde; saklanmaz, kalemden) ---
    @property
    def ara_toplam(self):
        from decimal import Decimal
        return sum((k.tutar for k in self.kalemler.filter(silindi=False)), Decimal("0.00"))

    @property
    def kdv_toplam(self):
        from decimal import Decimal
        return sum((k.kdv_tutari for k in self.kalemler.filter(silindi=False)), Decimal("0.00"))

    @property
    def tevkifat_toplam(self):
        from decimal import Decimal
        return sum((k.tevkifat_tutari for k in self.kalemler.filter(silindi=False)), Decimal("0.00"))

    @property
    def genel_toplam(self):
        """KDV dahil brüt (mal + KDV)."""
        return self.ara_toplam + self.kdv_toplam

    @property
    def odenecek(self):
        """Carinin borç/alacağı = mal + KDV − tevkifat (tevkifat karşı tarafa ödenmez)."""
        return self.genel_toplam - self.tevkifat_toplam

    @property
    def miktar_toplam(self):
        """Fatura birimi bazında miktar toplamları (örn. {"KG": Decimal("9.015")})."""
        from decimal import Decimal
        toplam = {}
        for k in self.kalemler.filter(silindi=False).select_related("stok__fatura_birimi"):
            birim = k.stok.fatura_birimi.kisa_ad
            toplam[birim] = toplam.get(birim, Decimal("0")) + k.miktar
        return toplam


class TeklifSiparisKalem(TemelModel):
    """Teklif/Sipariş kalemi: stok × miktar × birim fiyat (+ KDV oranı snapshot)."""

    teklif_siparis = models.ForeignKey(
        TeklifSiparis, verbose_name="teklif / sipariş", related_name="kalemler",
        on_delete=models.CASCADE)
    stok = models.ForeignKey(
        Stok, verbose_name="stok", related_name="teklif_siparis_kalemleri",
        on_delete=models.PROTECT)
    miktar = models.DecimalField("miktar", max_digits=18, decimal_places=3)
    birim_fiyat = models.DecimalField("birim fiyat", max_digits=18, decimal_places=6)
    # Fatura biriminden (miktar / stok.cevirici) hesaplanan üretim miktarı yalnız TEORİK
    # bir yaklaşıklık — gerçek dünyada (örn. profil ağırlığı) tolerans farkı olabilir.
    # Kullanıcı gerçek üretim miktarını (örn. fiilen sayılan BOY adedi) burada elle
    # onaylar/düzeltirse İrsaliye stok girişi (miktar/cevirici YERİNE) bunu kullanır —
    # bkz. core.services.teklif_siparis._irsaliye_stok_hareketi_yaz. Boşsa eski davranış
    # (miktar/cevirici) aynen çalışır; sadece Satınalma (ALIŞ) ekranlarında girilir.
    uretim_miktar = models.DecimalField(
        "üretim miktarı", max_digits=18, decimal_places=3, null=True, blank=True)
    # Yalnız SATIŞ+TEKLİF/PROFORMA ekranlarında kullanılır (cariden/aday müşteriden otomatik
    # gelir, satır bazlı elle değiştirilebilir). Default 0 -> diğer belge türlerinde tutar
    # hesabı DEĞİŞMEZ.
    iskonto_yuzdesi = models.DecimalField("iskonto %", max_digits=5, decimal_places=2, default=0)
    kdv = models.ForeignKey(
        KdvOrani, verbose_name="KDV oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="teklif_siparis_kalemleri")
    # Tevkifat oranı snapshot (varsa, stoktan otomatik gelir — kullanıcı seçmez).
    tevkifat = models.ForeignKey(
        TevkifatOrani, verbose_name="tevkifat oranı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="teklif_siparis_kalemleri")

    class Meta:
        db_table = "teklif_siparis_kalem"
        verbose_name = "teklif / sipariş kalemi"
        verbose_name_plural = "teklif / sipariş kalemleri"
        ordering = ["teklif_siparis", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0),
                                   name="ck_ts_kalem_miktar_gt0"),
            models.CheckConstraint(condition=models.Q(birim_fiyat__gte=0),
                                   name="ck_ts_kalem_fiyat_gte0"),
            models.CheckConstraint(
                condition=models.Q(iskonto_yuzdesi__gte=0) & models.Q(iskonto_yuzdesi__lte=100),
                name="ck_ts_kalem_iskonto_0_100"),
            models.CheckConstraint(
                condition=models.Q(uretim_miktar__isnull=True) | models.Q(uretim_miktar__gt=0),
                name="ck_ts_kalem_uretim_miktar_gt0"),
        ]

    def __str__(self):
        return f"{self.stok_id} x {self.miktar}"

    @property
    def net_birim_fiyat_tam(self):
        """İskonto uygulanmış birim fiyat, alanın TAM hassasiyetinde (6 hane) — irsaliye/sipariş →
        fatura aktarımında kullanılır (4 haneye yuvarlama toplamı kaydırır)."""
        from decimal import Decimal
        from core.sayi import yuvarla
        carpan = (Decimal("100") - self.iskonto_yuzdesi) / Decimal("100")
        return yuvarla(self.birim_fiyat * carpan, 6)

    @property
    def net_birim_fiyat(self):
        """İskonto uygulanmış birim fiyat (görüntüleme için — birim_fiyat her zaman liste
        fiyatı olarak kalır)."""
        from decimal import Decimal
        from core.sayi import yuvarla
        carpan = (Decimal("100") - self.iskonto_yuzdesi) / Decimal("100")
        return yuvarla(self.birim_fiyat * carpan, 4)

    @property
    def tutar(self):
        from decimal import Decimal
        from core.sayi import yuvarla
        carpan = (Decimal("100") - self.iskonto_yuzdesi) / Decimal("100")
        return yuvarla(self.miktar * self.birim_fiyat * carpan, 2)

    @property
    def navlun_payi(self):
        """Birim başına navlun payı = belge navlunu / stoğun seçilen yükleme tipine sığan
        adedi ("bu ürünle dolu bir konteyner/TIR" varsayımı). Navlun ya da yükleme tipi yoksa
        veya stokta o tip için adet tanımsızsa None (UI'da uyarı)."""
        from core.sayi import yuvarla
        ts = self.teklif_siparis
        if ts.navlun_tutari is None or not ts.yukleme_tipi_id:
            return None
        alan = TanimSecenegi.YUKLEME_ALANI.get(ts.yukleme_tipi.kod)
        adet = getattr(self.stok, alan, None) if alan else None
        if not adet:
            return None
        return yuvarla(ts.navlun_tutari / adet, 4)

    @property
    def nakliye_dahil_fiyat(self):
        from core.sayi import yuvarla
        payi = self.navlun_payi
        if payi is None:
            return None
        return yuvarla(self.net_birim_fiyat + payi, 4)

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

    @property
    def genel_toplam(self):
        return self.tutar + self.kdv_tutari


# === STOKLAR Faz B — Depo (çok depo) ===
class Depo(TemelModel):
    """Stok deposu. Çok depo destekli; eldeki miktar depo bazında hareketlerden
    HESAPLANIR (saklanmaz). Kod elle, ad+kod silinmemişler arasında benzersiz."""

    kod = models.CharField("kod", max_length=20)
    ad = models.CharField("ad", max_length=100)
    # FASON deposu: bu depo bir fasoncunun (cari) elindeki stoğu gösterir. Fason dönüşte (OperasyonKaydi.fason_cari) girdiler bu depodan
    # düşer; ham profil sevki bu depoya depo transferiyle yapılır. Bir cariye en çok bir aktif fason deposu bağlanır.
    fason_cari = models.ForeignKey(
        "Cari", verbose_name="fasoncu (cari)", null=True, blank=True, on_delete=models.PROTECT, related_name="fason_depolari")

    class Meta:
        db_table = "depo"
        verbose_name = "depo"
        verbose_name_plural = "depolar"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(fields=["fason_cari"], condition=models.Q(silindi=False, fason_cari__isnull=False),
                                    name="uq_depo_fason_cari_aktif"),
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
        IRSALIYE = "IRSALIYE", "İrsaliye"
        URETIM = "URETIM", "Üretim"
        SARF = "SARF", "Sarf (hesaba çıkış)"
        TRANSFER = "TRANSFER", "Depo transferi"

    stok = models.ForeignKey(
        Stok, verbose_name="stok", related_name="hareketler", on_delete=models.PROTECT)
    depo = models.ForeignKey(
        Depo, verbose_name="depo", related_name="hareketler", on_delete=models.PROTECT)
    tarih = models.DateField("tarih")
    tur = models.CharField("tür", max_length=5, choices=Tur.choices)
    miktar = models.DecimalField("miktar", max_digits=18, decimal_places=6)
    aciklama = models.CharField("açıklama", max_length=300, blank=True)
    # Faturadan otomatik üretilen hareketler bu kaleme bağlanır (iptal/güncellemede izlenir).
    fatura_satir = models.ForeignKey(
        "FaturaSatir", verbose_name="kaynak fatura satırı", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stok_hareketleri")
    # İrsaliyeden otomatik üretilen hareketler bu kaleme bağlanır (fatura_satir'in İrsaliye karşılığı).
    teklif_siparis_kalem = models.ForeignKey(
        "TeklifSiparisKalem", verbose_name="kaynak irsaliye kalemi", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stok_hareketleri")
    # Operasyon Kaydı onayından otomatik üretilen GİRDİ ÇIKIŞ hareketleri bu kaleme bağlanır
    # (çıktı GİRİŞ hareketinin böyle bir satır karşılığı yok — kaydın kendisi aciklama'da anılır).
    operasyon_kaydi_girdi = models.ForeignKey(
        "OperasyonKaydiGirdi", verbose_name="kaynak operasyon kaydı girdisi", null=True,
        blank=True, on_delete=models.SET_NULL, related_name="stok_hareketleri")
    kaynak = models.CharField("kaynak", max_length=20, choices=Kaynak.choices,
                              default=Kaynak.MANUEL)
    # Yalnız kaynak=SARF (stoktan hesaba/yatırım projesine çıkış) için: karşı hesap (borç),
    # varsa yatırım projesi (258 karşı hesabında zorunlu) ve üretilen muhasebe fişi.
    karsi_hesap = models.ForeignKey(
        HesapPlani, verbose_name="karşı hesap", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stok_sarf_hareketleri")
    yatirim_projesi = models.ForeignKey(
        "YatirimProjesi", verbose_name="yatırım projesi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stok_sarf_hareketleri")
    fis = models.ForeignKey(
        "YevmiyeFisi", verbose_name="muhasebe fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stok_sarf_hareketleri")

    # --- Hareketli ağırlıklı ortalama maliyet (bkz. core.services.stok_ortalama) ---------
    # GİRDİ (yalnız GİRİŞ): giriş tutarı FATURA'dan gelir (KDV/tevkifat hariç, TL). Boşsa giriş
    # "fiyatsız/GEÇİCİ" sayılır (ör. fatura gelmemiş irsaliye girişi): miktar eklenir ama
    # ortalamayı DEĞİŞTİRMEZ.
    # Çıkışta yalnız ALIŞ İADESİ (tedarikçiye iade) faturasında dolu olur: çıkış ortalamayla değil
    # iade faturasının tutarıyla değerlenir (stok değeri o fatura fişiyle birlikte düşer).
    giris_tutar_try = models.DecimalField(
        "belirlenen tutar TL (fatura)", max_digits=18, decimal_places=2, null=True, blank=True)
    giris_tutar_usd = models.DecimalField(
        "belirlenen tutar USD (fatura)", max_digits=18, decimal_places=2, null=True, blank=True)
    # Giriş tutarını belirleyen fatura satırı (yalnız bilgi — fatura_satir'dan FARKLIDIR:
    # o alan "bu hareketi fatura yazdı" demektir ve fatura iptalinde hareketi siler).
    # Giriş tutarı kısmi/eksik veriden türetildiyse (ör. üretim çıktısı) True: durum GEÇİCİ olur ve
    # bu bayrak o girişten beslenen sonraki çıkış/üretimlere miras kalır.
    giris_tahmini = models.BooleanField("giriş tutarı tahmini", default=False)
    # Yalnız satış iadesi girişi: tutar fatura değil, o anki ağırlıklı ORTALAMA (çıkışın maliyeti).
    giris_ortalama = models.BooleanField("giriş ortalamadan değerlenir", default=False)
    # Üretim kaydının girdi ÇIKIŞ ve çıktı GİRİŞ hareketleri (maliyet aktarımı için).
    operasyon_kaydi = models.ForeignKey(
        "OperasyonKaydi", verbose_name="operasyon kaydı", null=True, blank=True,
        on_delete=models.PROTECT, related_name="stok_hareketleri_maliyet")
    # Depo transferinin iki bacağı (çıkış + giriş) aynı kimliği taşır; maliyeti DEĞİŞTİRMEZ.
    transfer_grubu = models.UUIDField("transfer grubu", null=True, blank=True, db_index=True)
    maliyet_fatura_satir = models.ForeignKey(
        "FaturaSatir", verbose_name="maliyeti belirleyen fatura satırı", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="maliyet_hareketleri")
    # SONUÇ (motor yazar, elle düzenlenmez): bu hareketin değeri ve hareket sonrası durum.
    class MaliyetDurumu(models.TextChoices):
        KESIN = "KESIN", "Kesin"
        GECICI = "GECICI", "Geçici"
        YOK = "YOK", "Maliyetsiz"

    maliyet_durumu = models.CharField(
        "maliyet durumu", max_length=6, choices=MaliyetDurumu.choices,
        default=MaliyetDurumu.YOK)
    tutar_try = models.DecimalField(
        "değer TL", max_digits=18, decimal_places=2, null=True, blank=True)
    tutar_usd = models.DecimalField(
        "değer USD", max_digits=18, decimal_places=2, null=True, blank=True)
    birim_maliyet_try = models.DecimalField(
        "birim maliyet TL", max_digits=18, decimal_places=6, null=True, blank=True)
    birim_maliyet_usd = models.DecimalField(
        "birim maliyet USD", max_digits=18, decimal_places=6, null=True, blank=True)
    sonrasi_miktar = models.DecimalField(
        "hareket sonrası miktar", max_digits=18, decimal_places=6, null=True, blank=True)
    sonrasi_deger_try = models.DecimalField(
        "hareket sonrası değer TL", max_digits=18, decimal_places=2, null=True, blank=True)
    sonrasi_deger_usd = models.DecimalField(
        "hareket sonrası değer USD", max_digits=18, decimal_places=2, null=True, blank=True)
    sonrasi_ort_try = models.DecimalField(
        "hareket sonrası ortalama TL", max_digits=18, decimal_places=6, null=True, blank=True)
    sonrasi_ort_usd = models.DecimalField(
        "hareket sonrası ortalama USD", max_digits=18, decimal_places=6, null=True, blank=True)

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


class StokMaliyetKatmani(TemelModel):
    """FIFO maliyet katmanı — bir GİRİŞ StokHareket'inin taşıdığı, parça parça tüketilen
    birim maliyet kaydı. Miktar defterinden (StokHareket) AYRI, yalnız TL tarafını izler.
    stok/depo/tarih, stok_hareket'ten TÜRETİLİR (servis katmanında tek noktada atanır,
    asla ayrıca parametre olarak verilmez) — iki kopyanın birbirinden sapması engellenir."""

    stok_hareket = models.OneToOneField(
        StokHareket, verbose_name="kaynak stok hareketi", on_delete=models.PROTECT,
        related_name="maliyet_katmani")
    stok = models.ForeignKey(Stok, verbose_name="stok", on_delete=models.PROTECT,
                             related_name="maliyet_katmanlari")
    depo = models.ForeignKey(Depo, verbose_name="depo", on_delete=models.PROTECT,
                             related_name="maliyet_katmanlari")
    tarih = models.DateField("tarih")
    giris_miktar = models.DecimalField("giriş miktarı", max_digits=18, decimal_places=6)
    kalan_miktar = models.DecimalField("kalan miktar", max_digits=18, decimal_places=6)
    birim_maliyet_try = models.DecimalField("birim maliyet (TL)", max_digits=18, decimal_places=6)
    kaynak_pb = models.CharField("kaynak para birimi", max_length=3, blank=True, default="")
    kaynak_birim_fiyat = models.DecimalField(
        "kaynak birim fiyat", max_digits=18, decimal_places=6, null=True, blank=True)
    kaynak_kur = models.DecimalField("kaynak kur", max_digits=18, decimal_places=6,
                                     null=True, blank=True)
    # Bu katmanın maliyeti eksik/kısmi veriden (örn. üretim girdilerinden biri tam
    # karşılanamadı) türediyse True — ekranda "tahmini" uyarısıyla gösterilir.
    tahmini = models.BooleanField("tahmini/eksik veri", default=False)

    class Meta:
        db_table = "stok_maliyet_katmani"
        verbose_name = "stok maliyet katmanı"
        verbose_name_plural = "stok maliyet katmanları"
        constraints = [
            models.CheckConstraint(condition=models.Q(giris_miktar__gt=0),
                                   name="ck_katman_giris_gt0"),
            models.CheckConstraint(condition=models.Q(kalan_miktar__gte=0),
                                   name="ck_katman_kalan_gte0"),
            models.CheckConstraint(condition=models.Q(birim_maliyet_try__gte=0),
                                   name="ck_katman_maliyet_gte0"),
        ]
        indexes = [
            models.Index(fields=["stok", "depo", "tarih", "id"],
                        condition=models.Q(kalan_miktar__gt=0, silindi=False),
                        name="idx_maliyet_katman_fifo"),
        ]

    def __str__(self):
        return f"{self.stok_id} {self.tarih} kalan={self.kalan_miktar}"


class StokMaliyetTuketimi(TemelModel):
    """Bir ÇIKIŞ StokHareket'inin hangi maliyet katman(lar)ından ne kadar düştüğü —
    FIFO tüketiminin denetim izi. tutar_try = miktar × birim_maliyet_try (sorgu kolaylığı
    için saklanır; kaynağı miktar/birim_maliyet_try olduğundan "hesaplanır, saklanmaz"
    ilkesini ihlal etmez — ikisi de bu satırda zaten var, sadece çarpımı önden alınıyor)."""

    katman = models.ForeignKey(StokMaliyetKatmani, verbose_name="katman",
                               on_delete=models.PROTECT, related_name="tuketimler")
    tuketen_hareket = models.ForeignKey(
        StokHareket, verbose_name="tüketen hareket", on_delete=models.PROTECT,
        related_name="maliyet_tuketimleri")
    miktar = models.DecimalField("miktar", max_digits=18, decimal_places=6)
    birim_maliyet_try = models.DecimalField("birim maliyet (TL, snapshot)",
                                            max_digits=18, decimal_places=6)
    tutar_try = models.DecimalField("tutar (TL)", max_digits=18, decimal_places=2)

    class Meta:
        db_table = "stok_maliyet_tuketimi"
        verbose_name = "stok maliyet tüketimi"
        verbose_name_plural = "stok maliyet tüketimleri"
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0),
                                   name="ck_tuketim_miktar_gt0"),
        ]

    def __str__(self):
        return f"{self.tuketen_hareket_id} <- {self.katman_id} ({self.miktar})"


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
    banka = models.ForeignKey(
        Banka, verbose_name="banka", on_delete=models.PROTECT, null=True, blank=True,
        related_name="kredi_kartlari")
    kart_son4 = models.CharField("kart no (son 4)", max_length=4, blank=True)
    limit = models.DecimalField("kart limiti", max_digits=14, decimal_places=2, default=0)
    kesim_gunu = models.PositiveSmallIntegerField("hesap kesim günü", null=True, blank=True)
    son_odeme_gunu = models.PositiveSmallIntegerField("son ödeme günü", null=True, blank=True)
    # Taksit bölümünde bölünemeyen kuruş farkı hangi taksite eklenir (bankaya göre değişir); bkz. core.services.kk_donem.bolme.
    kurus_farki = models.CharField("kuruş farkı", max_length=3, default="SON",
                                   choices=(("ILK", "İlk taksite"), ("SON", "Son taksite")))
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


class KrediKartiTaksit(TemelModel):
    """Taksitli harcama planı (yalnız BİLGİ/TAKİP). Harcama fişi TAM tutardadır (borç anında
    gerçek); bu kayıt taksit takvimini saklar. Eşit taksit; son taksit yuvarlamayı üstlenir."""

    kart = models.ForeignKey(
        KrediKarti, verbose_name="kredi kartı", on_delete=models.PROTECT,
        related_name="taksit_planlari")
    fis = models.ForeignKey(
        "YevmiyeFisi", verbose_name="harcama fişi", on_delete=models.PROTECT,
        related_name="taksit_planlari")
    taksit_adedi = models.PositiveSmallIntegerField("taksit adedi")
    ilk_vade = models.DateField("ilk taksit tarihi")
    toplam_tutar = models.DecimalField("toplam tutar", max_digits=18, decimal_places=2)
    para_birimi = models.CharField(
        "para birimi", max_length=3, choices=Cari.PARA_CHOICES, default="TRY")

    class Meta:
        db_table = "finans_kredi_karti_taksit"
        verbose_name = "kredi kartı taksit planı"
        verbose_name_plural = "kredi kartı taksit planları"
        ordering = ["ilk_vade", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(taksit_adedi__gte=2),
                                   name="ck_kk_taksit_adedi_gte2"),
            models.CheckConstraint(condition=models.Q(toplam_tutar__gt=0),
                                   name="ck_kk_taksit_toplam_gt0"),
        ]

    def __str__(self):
        return f"{self.kart.ad} {self.taksit_adedi} taksit"


class Kredi(TemelModel):
    """Kredi tanımı. Bakiye (kalan borç) muhasebe hesabından (saklanmaz)."""

    ad = models.CharField("kredi adı", max_length=100)
    banka = models.ForeignKey(
        Banka, verbose_name="banka", on_delete=models.PROTECT, null=True, blank=True,
        related_name="krediler")
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


class KrediTaksit(TemelModel):
    """Kredi geri ödeme planı satırı — ELLE girilir (vade + anapara + faiz). Ödeme, plandaki
    BEKLİYOR taksitler seçilip ödenerek yapılır (tek geri ödeme fişi); ödenen taksitler ODENDI +
    ödeme fişine bağlanır. Muhasebe fişte tutulur; bu satır plan/takip amaçlıdır."""

    class Durum(models.TextChoices):
        BEKLIYOR = "BEKLIYOR", "Bekliyor"
        ODENDI = "ODENDI", "Ödendi"

    kredi = models.ForeignKey(
        Kredi, verbose_name="kredi", on_delete=models.PROTECT, related_name="taksitler")
    sira = models.PositiveSmallIntegerField("sıra")
    vade = models.DateField("vade")
    anapara = models.DecimalField("anapara", max_digits=18, decimal_places=2)
    faiz = models.DecimalField("faiz", max_digits=18, decimal_places=2, default=0)
    durum = models.CharField("durum", max_length=10, choices=Durum.choices,
                             default=Durum.BEKLIYOR)
    odeme_fisi = models.ForeignKey(
        "YevmiyeFisi", verbose_name="ödeme fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="odenen_taksitler")

    class Meta:
        db_table = "finans_kredi_taksit"
        verbose_name = "kredi taksiti"
        verbose_name_plural = "kredi taksitleri"
        ordering = ["kredi_id", "sira", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(anapara__gt=0),
                                   name="ck_kredi_taksit_anapara_gt0"),
            models.CheckConstraint(condition=models.Q(faiz__gte=0),
                                   name="ck_kredi_taksit_faiz_gte0"),
        ]

    @property
    def toplam(self):
        return self.anapara + self.faiz

    def __str__(self):
        return f"{self.kredi.ad} taksit {self.sira}"


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
    # Döviz carilere verilen TL çekler: çıkışta cari borcu DÜŞMEZ (çek tutarı bu ara hesapta bekler); çek ödendiği gün o günün TCMB
    # alış kuruyla cari döviz borcundan düşülür (bkz. core.services.doviz_cari, core.services.cek).
    doviz_cari_ara = _cek_hesap_fk("döviz carilere verilen çekler ara hesabı")

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
        KARSILIKSIZ = "KARSILIKSIZ", "Karşılıksız"
        FIRMA_KARSILIKSIZ = "FIRMA_KARSILIKSIZ", "Firma Çek Karşılıksız"

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
    # Döviz carisine verilen TL çek: tutar ara hesapta bekliyor; ödeme gününde cari döviz borcundan düşülür.
    ara_hesapta = models.BooleanField("ara hesapta bekliyor", default=False)
    cari = models.ForeignKey(
        "Cari", verbose_name="cari", null=True, blank=True, on_delete=models.PROTECT,
        related_name="cek_senetler")
    giris_bordrosu = models.ForeignKey(
        CekBordrosu, verbose_name="giriş bordrosu", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cek_senetler")
    # Gizli: özel depoda (MEDIA_ROOT dışı), yalnız yetkili görünümle sunulur (bkz. core.storage).
    on_yuz = models.ImageField("ön yüz görseli", storage=ozel_depo, upload_to=cek_gorsel_yolu,
                               null=True, blank=True)
    arka_yuz = models.ImageField("arka yüz görseli", storage=ozel_depo, upload_to=cek_gorsel_yolu,
                                 null=True, blank=True)

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


class YemekSayimi(TemelModel):
    """İNSAN KAYNAKLARI > Yemek Takibi — günlük personel yemek/kişi sayımı (yemek firmasına bildirilen
    sayı). Ay sonunda toplanıp firmanın kestiği faturayla karşılaştırmak içindir; fatura
    üretmez, yevmiyeye girmez — yalnız kontrol amaçlı kayıt. Aynı cari+tarih için tek kayıt
    (silinmemişler arası benzersiz)."""

    cari = models.ForeignKey(
        Cari, verbose_name="cari (yemek firması)", on_delete=models.PROTECT,
        related_name="yemek_sayimlari")
    tarih = models.DateField("tarih")
    kisi_sayisi = models.PositiveIntegerField("kişi sayısı")
    birim_fiyat = models.DecimalField("kişi başı ücret", max_digits=14, decimal_places=2,
                                      default=0)
    notlar = models.CharField("notlar", max_length=200, blank=True)

    class Meta:
        db_table = "yemek_sayimi"
        verbose_name = "yemek sayımı"
        verbose_name_plural = "yemek sayımları"
        ordering = ["-tarih"]
        constraints = [
            models.UniqueConstraint(
                fields=["cari", "tarih"], condition=models.Q(silindi=False),
                name="uq_yemek_sayimi_cari_tarih_aktif"),
            models.CheckConstraint(condition=models.Q(birim_fiyat__gte=0),
                                   name="ck_yemek_sayimi_birim_fiyat_gte0"),
        ]

    def __str__(self):
        return f"{self.cari.unvan} — {self.tarih} ({self.kisi_sayisi} kişi)"

    @property
    def tutar(self):
        return self.kisi_sayisi * self.birim_fiyat


class FirmaBilgisi(TemelModel):
    """AYARLAR > Firma Bilgileri — TEKİL kayıt (pk=1, bkz. CekHesapAyari ile aynı desen).
    Teklif/fatura gibi çıktı belgelerinde kullanılacak marka/iletişim/vergi bilgileri + logo."""

    unvan = models.CharField("unvan", max_length=200, blank=True)
    vergi_dairesi = models.CharField("vergi dairesi", max_length=100, blank=True)
    vergi_no = models.CharField("vergi no", max_length=20, blank=True)
    adres = models.TextField("adres", blank=True)
    telefon = models.CharField("telefon", max_length=30, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    web = models.CharField("web sitesi", max_length=200, blank=True)
    logo = models.ImageField("logo", upload_to="firma_logo/", blank=True, null=True)

    class Meta:
        db_table = "core_firma_bilgisi"
        verbose_name = "firma bilgisi"
        verbose_name_plural = "firma bilgisi"

    def __str__(self):
        return self.unvan or "Firma Bilgisi"

    @classmethod
    def get(cls):
        """Tekil ayar kaydı (yoksa oluşturur)."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class FirmaBanka(TemelModel):
    """Firmaya ait banka hesabı (çoklu) — Firma Bilgileri ekranında serbestçe eklenip
    çıkarılabilir, CariBanka ile aynı alan şekli (+ şube)."""

    firma = models.ForeignKey(FirmaBilgisi, verbose_name="firma", related_name="bankalar",
                              on_delete=models.CASCADE)
    banka_adi = models.CharField("banka", max_length=100)
    sube = models.CharField("şube", max_length=100, blank=True)
    hesap_sahibi = models.CharField("hesap sahibi", max_length=200, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    para_birimi = models.CharField("para birimi", max_length=3,
                                   choices=YevmiyeSatir.IslemPB.choices, default="TRY")
    sira = models.PositiveSmallIntegerField("sıra", default=0)

    class Meta:
        db_table = "core_firma_banka"
        verbose_name = "firma banka hesabı"
        verbose_name_plural = "firma banka hesapları"
        ordering = ["sira", "pk"]

    def __str__(self):
        return f"{self.banka_adi} ({self.para_birimi})"


class FasonFiyat(TemelModel):
    """FASON > Fason Fiyatları — fasoncunun (cari) bir KESİLMİŞ PARÇA için PARÇA ADEDİ başına faturaladığı sabit fiyat. Fasoncunun kendi parça
    kodu (``fasoncu_kodu``, örn. GZ-P-00041) kesim listesi PDF'inde kullanılır. Aynı cari + stok için birden çok satır olabilir (farklı
    ``gecerlilik_baslangic``); verilen tarihte geçerli fiyat = başlangıcı o tarihi geçmeyen EN SON aktif satır (bkz. core.services.fason.gecerli_fiyat).
    Fason dönüşte (OperasyonKaydi.fason_cari) her çıktının maliyetine bu fiyat × adet eklenir."""

    cari = models.ForeignKey("Cari", verbose_name="fasoncu (cari)", on_delete=models.PROTECT, related_name="fason_fiyatlari")
    stok = models.ForeignKey(Stok, verbose_name="kesilmiş parça", on_delete=models.PROTECT, related_name="fason_fiyatlari")
    fasoncu_kodu = models.CharField("fasoncunun parça kodu", max_length=40, blank=True, default="")
    birim_fiyat = models.DecimalField("birim fiyat (parça adedi başına)", max_digits=18, decimal_places=6)
    para_birimi = models.CharField("para birimi", max_length=3, choices=YevmiyeSatir.IslemPB.choices, default="TRY")
    gecerlilik_baslangic = models.DateField("geçerlilik başlangıcı")
    aktif = models.BooleanField("aktif", default=True)

    class Meta:
        db_table = "core_fason_fiyat"
        verbose_name = "fason fiyatı"
        verbose_name_plural = "fason fiyatları"
        ordering = ["cari__unvan", "stok__kod", "-gecerlilik_baslangic"]
        constraints = [
            models.CheckConstraint(condition=models.Q(birim_fiyat__gte=0), name="ck_fason_fiyat_gte0"),
            models.UniqueConstraint(fields=["cari", "stok", "gecerlilik_baslangic"], condition=models.Q(silindi=False),
                                    name="uq_fason_fiyat_cari_stok_tarih_aktif"),
        ]

    def __str__(self):
        return f"{self.cari_id} · {self.stok_id} · {self.birim_fiyat} {self.para_birimi}"


class FasonDonus(TemelModel):
    """FASON > Fason Dönüşler — fasoncudan gelen bir İRSALİYE: aynı belgeye bağlı bir ya da birden çok fason operasyon kaydı
    (``OperasyonKaydi.fason_donus``). Her satır bir operasyonun çalıştırılmasıdır: girdiler (ham profil) carinin fason deposundan düşer,
    ana + yan çıktılar ``depo``ya (DEPO-ÜRETİM) girer, çıktı başına fasoncunun ``FasonFiyat`` bedeli maliyete eklenir. Fasoncunun
    alış faturası bu belgeye bağlanınca (``fatura``) fason bedeli 'tahmini'likten çıkar."""

    yil = models.PositiveSmallIntegerField("yıl", editable=False)
    sira = models.PositiveIntegerField("sıra", editable=False)
    no = models.CharField("belge no", max_length=20, editable=False)
    cari = models.ForeignKey("Cari", verbose_name="fasoncu (cari)", on_delete=models.PROTECT, related_name="fason_donusleri")
    depo = models.ForeignKey("Depo", verbose_name="çıktıların gireceği depo", on_delete=models.PROTECT, related_name="fason_donusleri")
    irsaliye_no = models.CharField("fasoncunun irsaliye no", max_length=50, blank=True, default="")
    tarih = models.DateField("tarih")
    aciklama = models.CharField("açıklama", max_length=300, blank=True, default="")
    # Fasoncunun ALIŞ faturası (fason hizmet kalemi; kategorisi 151 yarı mamul hesabına eşli): bir fatura birden çok dönüş belgesine bağlanabilir.
    # Fatura ONAYLI olunca fason bedeli 'tahmini'likten çıkar; fatura silinirse bağ kopar (SET_NULL) ve bedel yeniden tahmini olur.
    fatura = models.ForeignKey("Fatura", verbose_name="fason faturası", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="fason_donusleri")
    # Faturasız fasoncuda (Cari.fason_faturasiz) onayda yazılan tahakkuk fişi: 151 alt hesap(lar)ı BORÇ / fasoncu cari ALACAK (KDV yok).
    tahakkuk_fis = models.ForeignKey("YevmiyeFisi", verbose_name="fason tahakkuk fişi", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="fason_tahakkuk_donusleri", editable=False)

    class Meta:
        db_table = "core_fason_donus"
        verbose_name = "fason dönüş"
        verbose_name_plural = "fason dönüşler"
        ordering = ["-yil", "-sira"]
        constraints = [models.UniqueConstraint(fields=["yil", "sira"], name="uq_fason_donus_yil_sira")]

    def __str__(self):
        return self.no

    @property
    def onayli(self):
        """Belgenin (silinmemiş) tüm kayıtları onaylıysa True; onay tek atomik işlem olduğundan arası yoktur."""
        kayitlar = [k for k in self.kayitlar.all() if not k.silindi]
        return bool(kayitlar) and all(k.durum == "ONAYLI" for k in kayitlar)


# === ÜRETİM modülü — İş İstasyonu + Operasyon (rota) + Üretim Emri + Operasyon Kaydı ===
# Bağımsız, sıfırdan kurulan bir Stok↔Stok rota modeli. Bitmiş bir ürün, farklı İŞ
# İSTASYONLARINDA (Lazer Kesim, Büküm, ...) art arda yapılan OPERASYONLARLA adım adım
# ortaya çıkar: her Operasyon, bir istasyonda, bir/daha fazla GİRDİ stoktan TEK bir ÇIKTI
# stok üretir (oranlı dönüşüm — örn. 1 boy profil → 2 adet kesilmiş parça). Bir Üretim
# Emri (\"1 adet merdiven istiyorum\") bu zinciri özyinelemeli olarak hesaplayıp zincirdeki
# HER operasyon için ayrı bir TASLAK Operasyon Kaydı açar; her istasyon KENDİ kaydını kendi
# zamanında onaylar — onay anında stok ÇIKIŞ/GİRİŞ hareketleri (Kaynak=URETIM) yazılır.
# Hiçbir adım YevmiyeFisi/YevmiyeSatir'e dokunmaz — maliyetin muhasebeye yansıtılması ay
# sonu mali müşavirin elle yapacağı ayrı bir iştir (bkz. docs/ERP_v0.1_kapsam.md v0.4).
class IsIstasyonu(TemelModel):
    """ÜRETİM > İş İstasyonları — fiziksel/mantıksal üretim istasyonu (örn. Lazer Kesim,
    Büküm, Kaynak, Montaj). Depo modeliyle birebir aynı desen."""

    kod = models.CharField("kod", max_length=20)
    ad = models.CharField("ad", max_length=100)

    class Meta:
        db_table = "core_is_istasyonu"
        verbose_name = "iş istasyonu"
        verbose_name_plural = "iş istasyonları"
        ordering = ["kod"]
        constraints = [
            models.UniqueConstraint(fields=["kod"], condition=models.Q(silindi=False),
                                    name="uq_is_istasyonu_kod_aktif"),
            models.UniqueConstraint(fields=["ad"], condition=models.Q(silindi=False),
                                    name="uq_is_istasyonu_ad_aktif"),
        ]

    def __str__(self):
        return f"{self.kod} {self.ad}"


class Operasyon(TemelModel):
    """ÜRETİM > Operasyon Tanımları — bir iş istasyonunda, bir/daha fazla GİRDİ stoktan TEK
    bir ÇIKTI stok üretilme tanımı (rota adımı). cikti_miktar: 1 ÇALIŞTIRMADA üretilen çıktı
    adedi (kesimde 2 olabilir — 1 boydan 2 parça). Çıktı başına en fazla 1 aktif operasyon
    olur; çıktı, oluşturulduktan sonra değişmez. Girdi satırları OperasyonGirdi'de."""

    class Tur(models.TextChoices):
        URET = "URET", "Üret (N girdi → 1 çıktı)"
        PARCALA = "PARCALA", "Parçala (1 girdi → N çıktı)"

    class PayAnahtari(models.TextChoices):
        BOY = "BOY", "Boy oranı (miktar × boy)"
        ESIT = "ESIT", "Eşit (adet başı)"
        YUZDE = "YUZDE", "Yüzde (elle)"

    istasyon = models.ForeignKey(
        IsIstasyonu, verbose_name="iş istasyonu", on_delete=models.PROTECT,
        related_name="operasyonlar")
    # ÜRET: tek (ana) çıktı + isteğe bağlı yan çıktılar. PARÇALA: tek girdi, N eşit düzey çıktı (ana/yan ayrımı yok) — çıktıların TAMAMI
    # ``ciktilar`` (OperasyonCikti) tablosundadır; ``cikti`` / ``cikti_miktar`` / ``boy_mm`` o tablonun sıra 0 satırının (referans çıktı) kopyasıdır.
    tur = models.CharField("tür", max_length=7, choices=Tur.choices, default=Tur.URET)
    # Girdi maliyetinin çıktılara paylaştırma anahtarı. ÜRET her zaman BOY (ana + yan çıktılar, miktar × boy_mm); PARÇALA: BOY / ESIT / YUZDE.
    pay_anahtari = models.CharField("maliyet pay anahtarı", max_length=5, choices=PayAnahtari.choices, default=PayAnahtari.BOY)
    cikti = models.ForeignKey(
        Stok, verbose_name="çıktı", on_delete=models.PROTECT, related_name="operasyonlar")
    cikti_miktar = models.DecimalField(
        "çıktı miktarı (1 çalıştırma için)", max_digits=18, decimal_places=3, default=1)
    # Kesimde TAM BOY: çalıştırma sayısı kesirli olamaz (yukarı yuvarlanır); bir boydan çıkan fazla parça stoğa girer.
    # Yeni tanımda varsayılan: girdilerden en az biri BOY birimliyse işaretli; formdan kullanıcı kaldırabilir/işaretleyebilir.
    tam_calistirma = models.BooleanField("tam çalıştırma zorunlu (tam boy)", default=False)
    # Ana çıktının parça boyu (mm): yan çıktısı olan operasyonda girdi maliyeti BOY ORANINA göre paylaştırılır (miktar × boy_mm).
    boy_mm = models.DecimalField("ana çıktı boyu (mm)", max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "core_operasyon"
        verbose_name = "operasyon"
        verbose_name_plural = "operasyonlar"
        ordering = ["istasyon__kod", "cikti__kod"]
        constraints = [
            models.UniqueConstraint(fields=["cikti"], condition=models.Q(silindi=False),
                                    name="uq_operasyon_cikti_aktif"),
            models.CheckConstraint(condition=models.Q(cikti_miktar__gt=0),
                                   name="ck_operasyon_cikti_miktar_gt0"),
            models.CheckConstraint(condition=models.Q(boy_mm__isnull=True) | models.Q(boy_mm__gt=0),
                                   name="ck_operasyon_boy_mm_gt0"),
        ]

    def __str__(self):
        return f"{self.istasyon.kod} {self.cikti.kod}"


class OperasyonKaydiCikti(TemelModel):
    """Onaylı operasyon kaydının ÇIKTI satırı (ana + yan çıktılar): ONAY ANINDAKİ miktar, boy (mm) ve maliyet pay oranı SAKLANIR.
    Sonradan maliyet yeniden paylaştırması (geç gelen fatura vb.) operasyon tanımına DEĞİL bu satırlara göre yapılır; tanımdaki boy/yan
    çıktı değişiklikleri geçmiş onaylı kayıtları etkilemez. Satırı olmayan (eski) onaylı kayıt = tek çıktı, ana çıktı %100."""

    kayit = models.ForeignKey("OperasyonKaydi", on_delete=models.CASCADE, related_name="ciktilar")
    stok = models.ForeignKey(Stok, verbose_name="çıktı", on_delete=models.PROTECT, related_name="kayit_cikti_satirlari")
    miktar = models.DecimalField("giriş miktarı", max_digits=18, decimal_places=6)
    # Beklenen (çalıştırmadan çıkması gereken) adet: fason dönüşte ``miktar`` GELEN adettir, ``beklenen_miktar − miktar`` = FİRE (eksik teslim).
    beklenen_miktar = models.DecimalField("beklenen miktar", max_digits=18, decimal_places=6, null=True, blank=True)
    boy_mm = models.DecimalField("boy (mm)", max_digits=12, decimal_places=2, null=True, blank=True)
    pay_orani = models.DecimalField("maliyet pay oranı", max_digits=12, decimal_places=10, default=1)
    # Onay anındaki paylaştırma AĞIRLIĞI (BOY: gelen miktar × boy_mm, ESIT: gelen miktar, YUZDE: tanımdaki yüzde): sonradan yeniden paylaştırma
    # tanıma değil bu değere bakar. Boşsa (eski kayıt) miktar × boy_mm kullanılır.
    agirlik = models.DecimalField("paylaştırma ağırlığı", max_digits=30, decimal_places=8, null=True, blank=True)
    # Üretim siparişi akışı: bu çıktıdan onay anında ÜS'ye AYRILAN miktar (stok ayırma izi; onaylı kaydın geri alınmasında tam geri sarılır).
    ayrilan = models.DecimalField("üretim siparişine ayrılan", max_digits=18, decimal_places=6, default=0)
    ana_mi = models.BooleanField("ana çıktı", default=True)
    # FASON dönüş kaydında ONAY ANI SNAPSHOT'I: çıktının kendi fason birim fiyatı (fiyatın para biriminde), kur ve TL/USD tutarı. Bölüşüm YOK:
    # her çıktı kendi fiyatı × adedi kadar bedel alır; malzeme maliyeti ise boy oranıyla paylaşılır (bkz. stok_fis.uretim_senkronla).
    fason_birim_fiyat = models.DecimalField("fason birim fiyat", max_digits=18, decimal_places=6, null=True, blank=True)
    fason_para_birimi = models.CharField("fason fiyat para birimi", max_length=3, blank=True, default="")
    fason_kur = models.DecimalField("fason kur (TL)", max_digits=18, decimal_places=6, null=True, blank=True)
    fason_tutar = models.DecimalField("fason tutarı (TL)", max_digits=18, decimal_places=2, null=True, blank=True)
    fason_tutar_usd = models.DecimalField("fason tutarı (USD)", max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "core_operasyon_kaydi_cikti"
        verbose_name = "operasyon kaydı çıktısı"
        verbose_name_plural = "operasyon kaydı çıktıları"
        ordering = ["-ana_mi", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["kayit", "stok"], condition=models.Q(silindi=False),
                                    name="uq_operasyon_kaydi_cikti_aktif"),
            models.CheckConstraint(condition=models.Q(pay_orani__gte=0) & models.Q(pay_orani__lte=1),
                                   name="ck_operasyon_kaydi_cikti_pay_0_1"),
        ]

    def __str__(self):
        return f"{self.kayit} → {self.stok.kod} × {self.miktar} (%{self.pay_orani * 100:.1f})"


class OperasyonCikti(TemelModel):
    """Operasyon TANIMININ çıktıları (ÜRET: ana + yan çıktılar; PARÇALA: N çıktı). Sıra 0 = REFERANS çıktı (``Operasyon.cikti`` ile aynı stok).
    ``surucu``: bu çıktı bu tanımdan "üretilir" sayılır (planlama/ürün ağacı onu bu tanıma bağlar) — ÜRET'te yalnız referans çıktı, PARÇALA'da
    HER çıktı; stok başına tek aktif sürücü satır olur. ÜRET'in yan çıktıları (surucu=False) yalnız ek üründür: talebi etkilemez."""

    operasyon = models.ForeignKey(Operasyon, on_delete=models.CASCADE, related_name="ciktilar")
    stok = models.ForeignKey(Stok, verbose_name="çıktı", on_delete=models.PROTECT, related_name="operasyon_cikti_satirlari")
    miktar = models.DecimalField("miktar (1 çalıştırma için)", max_digits=18, decimal_places=6)
    boy_mm = models.DecimalField("boy (mm)", max_digits=12, decimal_places=2, null=True, blank=True)
    yuzde = models.DecimalField("maliyet payı (%)", max_digits=7, decimal_places=4, null=True, blank=True)
    sira = models.PositiveSmallIntegerField("sıra", default=0)
    surucu = models.BooleanField("bu tanımdan üretilir", default=True)

    class Meta:
        db_table = "core_operasyon_cikti"
        verbose_name = "operasyon çıktısı"
        verbose_name_plural = "operasyon çıktıları"
        ordering = ["sira", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0), name="ck_op_cikti_satir_miktar_gt0"),
            models.CheckConstraint(condition=models.Q(boy_mm__isnull=True) | models.Q(boy_mm__gt=0), name="ck_op_cikti_satir_boy_gt0"),
            models.CheckConstraint(condition=models.Q(yuzde__isnull=True) | (models.Q(yuzde__gt=0) & models.Q(yuzde__lte=100)),
                                   name="ck_op_cikti_satir_yuzde_0_100"),
            models.UniqueConstraint(fields=["operasyon", "stok"], condition=models.Q(silindi=False), name="uq_operasyon_cikti_aktif_op_stok"),
            models.UniqueConstraint(fields=["stok"], condition=models.Q(silindi=False, surucu=True), name="uq_operasyon_cikti_surucu_aktif"),
        ]

    def __str__(self):
        return f"{self.operasyon} → {self.stok.kod} × {self.miktar}"


class OperasyonGirdi(TemelModel):
    operasyon = models.ForeignKey(
        Operasyon, on_delete=models.CASCADE, related_name="girdiler")
    girdi = models.ForeignKey(
        Stok, verbose_name="girdi", on_delete=models.PROTECT,
        related_name="operasyon_girdi_kullanimlari")
    miktar = models.DecimalField("miktar (1 çalıştırma için)", max_digits=18, decimal_places=6)
    sira = models.PositiveSmallIntegerField("sıra", default=0)

    class Meta:
        db_table = "core_operasyon_girdi"
        verbose_name = "operasyon girdisi"
        verbose_name_plural = "operasyon girdileri"
        ordering = ["sira", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gt=0),
                                   name="ck_operasyon_girdi_miktar_gt0"),
            models.UniqueConstraint(fields=["operasyon", "girdi"],
                                    condition=models.Q(silindi=False),
                                    name="uq_operasyon_girdi_aktif"),
        ]

    def __str__(self):
        return f"{self.operasyon} ← {self.girdi.kod} × {self.miktar}"


class UretimEmri(TemelModel):
    """ÜRETİM > Üretim Emirleri — üst-düzey tetikleyici (\"N adet [hedef ürün] istiyorum\").
    Kendi başına stok hareketi ÜRETMEZ: yalnızca zincirdeki her operasyon için ayrı bir
    TASLAK OperasyonKaydi açar (tek atomik işlemde, hepsi aynı depoyu kullanır). Her
    OperasyonKaydi'nın onayı bu emirden BAĞIMSIZ, ilgili istasyon kendi zamanında yapar —
    emir açılışı hiçbir durumu etkilemez, hepsi TASLAK açılır.

    Bir emir BİRDEN ÇOK kalem taşıyabilir ("tek emir, çoklu kalem" — bkz. UretimEmriKalemi);
    hedef ürün/miktar burada değil, ayrı kalem satırlarındadır. kaynak_siparis doluysa bu
    emir bir SATIŞ Siparişi onaylandıktan sonra 'Üretim Emri Aç' ile açılmıştır
    (izlenebilirlik için); manuel (Üretim Emirleri > + Yeni) emirlerde boştur.

    ÜRETİM SİPARİŞİ (2026-10, bkz. docs/uretim-siparisi-plan.md): bu model "Üretim Siparişi"dir (ekran adı); yeni kayıtlar ``ÜS-yyyy-nnnn``
    numarası alır (eski ``UE-`` kayıtlar aynen). ``durum`` ACIK → KAPALI (sipariş sevk edilince) / IPTAL (başlamış sipariş silinmez, iptal
    edilir); ``revizyon_no`` her revizede artar (geçmiş: UretimEmriRevizyon). Açılışta stok AYRILIR (StokAyirma) ve her operasyon için
    İSTASYON EMRİ (IstasyonEmri) açılır; operasyon kayıtları emirden açılır (adım 4+)."""

    class Durum(models.TextChoices):
        ACIK = "ACIK", "Açık"
        KAPALI = "KAPALI", "Kapalı"
        IPTAL = "IPTAL", "İptal"

    yil = models.PositiveSmallIntegerField("yıl", editable=False)
    sira = models.PositiveIntegerField("sıra", editable=False)
    no = models.CharField("emir no", max_length=20, editable=False)
    depo = models.ForeignKey(
        Depo, verbose_name="depo", on_delete=models.PROTECT, related_name="uretim_emirleri")
    tarih = models.DateField("tarih")
    aciklama = models.CharField("açıklama", max_length=300, blank=True, default="")
    kaynak_siparis = models.ForeignKey(
        "TeklifSiparis", verbose_name="kaynak sipariş", null=True, blank=True,
        on_delete=models.PROTECT, related_name="uretim_emirleri")
    durum = models.CharField("durum", max_length=6, choices=Durum.choices, default=Durum.ACIK)
    revizyon_no = models.PositiveSmallIntegerField("revizyon no", default=0)
    kapanis_tarihi = models.DateField("kapanış tarihi", null=True, blank=True)

    class Meta:
        db_table = "core_uretim_emri"
        verbose_name = "üretim siparişi"
        verbose_name_plural = "üretim siparişleri"
        ordering = ["-yil", "-sira"]
        constraints = [
            models.UniqueConstraint(fields=["yil", "sira"], name="uq_uretim_emri_yil_sira"),
        ]

    def __str__(self):
        return self.no


class UretimEmriKalemi(TemelModel):
    """Üretim Emri kalemi: emrin ürettiği HER hedef ürün + miktar satırı ("tek emir, çoklu
    kalem"). Kendi başına hiçbir kayıt/hareket üretmez — yalnızca UretimEmri.olustur()
    servisi tarafından, o emrin zincirinde kök olarak işlenen ürünleri kaydeder
    (izlenebilirlik + detay ekranı için); OperasyonKaydi zincirini AÇAN hep servis
    katmanıdır, bu model değil."""

    uretim_emri = models.ForeignKey(
        UretimEmri, verbose_name="üretim emri", related_name="kalemler",
        on_delete=models.CASCADE)
    hedef_urun = models.ForeignKey(
        Stok, verbose_name="hedef ürün", on_delete=models.PROTECT,
        related_name="uretim_emri_kalemleri")
    hedef_miktar = models.DecimalField("hedef miktar", max_digits=18, decimal_places=3)
    sira = models.PositiveSmallIntegerField("sıra", default=0)
    # Siparişten açılan üretim siparişinde kalemin SATIŞ sipariş kalemi (karar 6/10: hedef miktar = sipariş kalem miktarı); manuelde boş.
    siparis_kalem = models.ForeignKey(
        "TeklifSiparisKalem", verbose_name="sipariş kalemi", null=True, blank=True, on_delete=models.PROTECT,
        related_name="uretim_emri_kalemleri")
    # Açılış/revize anında eldeki kullanılabilir mamulden AYRILAN miktar (snapshot; detay ekranı "ayrılan / üretilecek" için). Güncel ayırma
    # StokAyirma'dadır.
    eldeki_ayrilan = models.DecimalField("eldekinden ayrılan", max_digits=18, decimal_places=6, default=0)

    class Meta:
        db_table = "core_uretim_emri_kalemi"
        verbose_name = "üretim emri kalemi"
        verbose_name_plural = "üretim emri kalemleri"
        ordering = ["sira", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(hedef_miktar__gt=0),
                                   name="ck_uretim_emri_kalemi_hedef_miktar_gt0"),
        ]

    def __str__(self):
        return f"{self.uretim_emri.no} — {self.hedef_urun.kod} × {self.hedef_miktar}"


class StokAyirma(TemelModel):
    """ÜRETİM > Üretim Siparişi STOK AYIRMASI (rezervasyon): bir üretim siparişine (``UretimEmri``) bir stoktan AYRILAN güncel miktar —
    (sipariş, stok) başına TEK aktif satır; olaylarla (açılış, revize, kayıt onayı, sevk) güncellenir, geçmişi revizyon kaydında tutulur.
    Stok hareketi DEĞİLDİR: eldekiyi değiştirmez; yalnız "kullanılabilir = eldeki − açık siparişlerin ayrılan toplamı" hesabında düşülür
    (core.services.stok_ayirma). Kural YUMUŞAKTIR: başka çıkışlar ayrılmış stoğu fiilen tüketebilir, hiçbir hareket engellenmez; kullanılabilir
    eksiye düşünce ekranlar uyarır. Depo bazında değildir (tüm depolar). Bkz. docs/uretim-siparisi-plan.md."""

    uretim_emri = models.ForeignKey(
        UretimEmri, verbose_name="üretim siparişi", on_delete=models.CASCADE, related_name="ayirmalar")
    stok = models.ForeignKey(Stok, verbose_name="stok", on_delete=models.PROTECT, related_name="ayirmalar")
    miktar = models.DecimalField("ayrılan miktar", max_digits=18, decimal_places=6)

    class Meta:
        db_table = "core_stok_ayirma"
        verbose_name = "stok ayırması"
        verbose_name_plural = "stok ayırmaları"
        ordering = ["uretim_emri", "stok__kod"]
        constraints = [
            models.CheckConstraint(condition=models.Q(miktar__gte=0), name="ck_stok_ayirma_miktar_gte0"),
            models.UniqueConstraint(fields=["uretim_emri", "stok"], condition=models.Q(silindi=False), name="uq_stok_ayirma_aktif"),
        ]

    def __str__(self):
        return f"{self.uretim_emri_id} ← {self.stok_id} × {self.miktar}"


class IstasyonEmri(TemelModel):
    """ÜRETİM > İstasyon Emirleri: bir üretim siparişinin (``UretimEmri``) net planındaki HER operasyon için bir emir — "bu istasyon bu
    operasyonu şu kadar çalıştıracak". ``planlanan``/``tamamlanan`` ÇALIŞTIRMA birimindedir (ekranda referans çıktı adedi × gösterilir);
    kalan = planlanan − tamamlanan. Operasyon kayıtları (``OperasyonKaydi.istasyon_emri``) emirden açılır; kayıt onaylanınca tamamlanan
    artar ve üretilen parça siparişe ayrılır. ``durum`` servis tarafından her olayda yeniden hesaplanır (BEKLIYOR: hiç kayıt yok; BASLADI:
    açık taslak var ya da kısmen tamamlandı; BITTI: tamamlanan ≥ planlanan; IPTAL: revizede gereksiz kaldı). Numara ``IE-yyyy-nnnn``.
    Bkz. docs/uretim-siparisi-plan.md."""

    class Durum(models.TextChoices):
        BEKLIYOR = "BEKLIYOR", "Bekliyor"
        BASLADI = "BASLADI", "Başladı"
        BITTI = "BITTI", "Bitti"
        IPTAL = "IPTAL", "İptal"

    uretim_emri = models.ForeignKey(UretimEmri, verbose_name="üretim siparişi", on_delete=models.CASCADE, related_name="istasyon_emirleri")
    operasyon = models.ForeignKey(Operasyon, verbose_name="operasyon", on_delete=models.PROTECT, related_name="istasyon_emirleri")
    istasyon = models.ForeignKey(IsIstasyonu, verbose_name="iş istasyonu", on_delete=models.PROTECT, related_name="emirler")   # açılış anı snapshot'ı
    yil = models.PositiveSmallIntegerField("yıl", editable=False)
    sira = models.PositiveIntegerField("sıra", editable=False)
    no = models.CharField("emir no", max_length=20, editable=False)
    seviye = models.PositiveSmallIntegerField("seviye (zincir sırası)", default=0)
    planlanan = models.DecimalField("planlanan çalıştırma", max_digits=24, decimal_places=10)
    tamamlanan = models.DecimalField("tamamlanan çalıştırma", max_digits=24, decimal_places=10, default=0)
    durum = models.CharField("durum", max_length=8, choices=Durum.choices, default=Durum.BEKLIYOR)
    # Açılış/revize anı snapshot'ı: bu emrin sürücü çıktıları için ÜS'nin ÜRETİMLE karşılaması gereken NET miktar {stok pk: miktar}. Kayıt onayında
    # üretilen parça bu miktara kadar ÜS'ye ayrılır (fazlası serbest stok — karar 8).
    ihtiyac = models.JSONField("net ihtiyaç ({stok pk: miktar})", default=dict, blank=True)

    class Meta:
        db_table = "core_istasyon_emri"
        verbose_name = "istasyon emri"
        verbose_name_plural = "istasyon emirleri"
        ordering = ["uretim_emri", "seviye", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["yil", "sira"], name="uq_istasyon_emri_yil_sira"),
            models.UniqueConstraint(fields=["uretim_emri", "operasyon"], condition=models.Q(silindi=False), name="uq_istasyon_emri_aktif_op"),
            models.CheckConstraint(condition=models.Q(planlanan__gte=0), name="ck_istasyon_emri_planlanan_gte0"),
            models.CheckConstraint(condition=models.Q(tamamlanan__gte=0), name="ck_istasyon_emri_tamamlanan_gte0"),
        ]

    def __str__(self):
        return self.no

    @property
    def kalan(self):
        from decimal import Decimal
        return max(Decimal("0"), self.planlanan - self.tamamlanan)


class UretimEmriRevizyon(TemelModel):
    """Üretim siparişi OLAY/REVİZE GEÇMİŞİ: açılış, revize (kalem ±, ekle/çıkar), kapanış, yeniden açılış, iptal — her olayda bir satır;
    ``detay`` JSON önce/sonra farklarını (kalemler, ayırmalar, istasyon emirleri) taşır. ``no`` sipariş içinde sıralı (revizyon_no ile aynı
    sayaç). Yalnız kayıttır; hiçbir hesabı etkilemez."""

    class Tur(models.TextChoices):
        ACILIS = "ACILIS", "Açılış"
        REVIZE = "REVIZE", "Revize"
        KAPANIS = "KAPANIS", "Kapanış"
        YENIDEN_ACILIS = "YENIDEN_ACILIS", "Yeniden açılış"
        IPTAL = "IPTAL", "İptal"

    uretim_emri = models.ForeignKey(UretimEmri, verbose_name="üretim siparişi", on_delete=models.CASCADE, related_name="revizyonlar")
    no = models.PositiveSmallIntegerField("sıra no")
    tur = models.CharField("tür", max_length=14, choices=Tur.choices)
    tarih = models.DateField("tarih")
    aciklama = models.CharField("açıklama", max_length=300, blank=True, default="")
    detay = models.JSONField("detay (önce/sonra)", default=dict, blank=True)

    class Meta:
        db_table = "core_uretim_emri_revizyon"
        verbose_name = "üretim siparişi revizyonu"
        verbose_name_plural = "üretim siparişi revizyonları"
        ordering = ["uretim_emri", "no"]
        constraints = [
            models.UniqueConstraint(fields=["uretim_emri", "no"], name="uq_uretim_emri_revizyon_no"),
        ]

    def __str__(self):
        return f"{self.uretim_emri_id} #{self.no} {self.tur}"


class OperasyonKaydi(TemelModel):
    """ÜRETİM > Operasyon Kayıtları — bir Operasyon'un fiilen çalıştırılma kaydı, bir
    istasyonda, bir tarihte. TASLAK'ta serbestçe düzenlenir/silinir; Onayla'da tek atomik
    işlemde girdiler için stok ÇIKIŞ + çıktı için stok GİRİŞ hareketleri (Kaynak=URETIM)
    yazılır, kayıt kilitlenir. uretim_emri doluysa bir üst Üretim Emri'nden otomatik
    açılmıştır; boşsa istasyonun kendi inisiyatifiyle açtığı bağımsız/serbest kayıttır."""

    class Durum(models.TextChoices):
        TASLAK = "TASLAK", "Taslak"
        ONAYLI = "ONAYLI", "Onaylı"

    yil = models.PositiveSmallIntegerField("yıl", editable=False)
    sira = models.PositiveIntegerField("sıra", editable=False)
    no = models.CharField("kayıt no", max_length=20, editable=False)
    operasyon = models.ForeignKey(
        Operasyon, verbose_name="operasyon", on_delete=models.PROTECT, related_name="kayitlar")
    uretim_emri = models.ForeignKey(
        UretimEmri, verbose_name="üretim emri", null=True, blank=True,
        on_delete=models.PROTECT, related_name="operasyon_kayitlari")
    # Üretim siparişi akışında kaydın açıldığı İSTASYON EMRİ (uretim_emri de dolu olur); eski/bağımsız kayıtlarda boş.
    istasyon_emri = models.ForeignKey(
        "IstasyonEmri", verbose_name="istasyon emri", null=True, blank=True, on_delete=models.PROTECT, related_name="kayitlar")
    depo = models.ForeignKey(
        Depo, verbose_name="depo", on_delete=models.PROTECT, related_name="operasyon_kayitlari")
    tarih = models.DateField("tarih")
    hedef_cikti_miktari = models.DecimalField(
        "hedef çıktı miktarı", max_digits=18, decimal_places=6)
    durum = models.CharField("durum", max_length=6, choices=Durum.choices, default=Durum.TASLAK)
    aciklama = models.CharField("açıklama", max_length=300, blank=True, default="")
    # Onayda girdi maliyetinin çıktıya aktarımı için 15x→15x fişi (hesaplar aynıysa fiş yok).
    fis = models.ForeignKey(
        "YevmiyeFisi", verbose_name="maliyet aktarım fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="operasyon_kayitlari")
    # FASON: doluysa kayıt fasoncuda (dışarıda) yapılan işin dönüşüdür — girdiler carinin fason deposundan düşer (``depo`` yalnız ÇIKTILARIN
    # gireceği depodur), çıktılara FasonFiyat bedeli eklenir; ``fason_donus`` aynı irsaliyeye bağlı kayıtları toplar.
    fason_cari = models.ForeignKey(
        "Cari", verbose_name="fasoncu (cari)", null=True, blank=True, on_delete=models.PROTECT, related_name="fason_operasyon_kayitlari")
    fason_donus = models.ForeignKey(
        "FasonDonus", verbose_name="fason dönüş belgesi", null=True, blank=True, on_delete=models.PROTECT, related_name="kayitlar")
    # FASON GELEN ADET (taslakta girilir): fasoncudan fiilen gelen adetler, TÜM çıktılar için {stok pk: adet} (referans dahil). Boşsa ÜRET'te BEKLENEN adet
    # gelmiş sayılır, PARÇALA'da 0. Girdi tüketimi beklenen çalıştırmadan, stoğa giren adet ve fason bedeli GELEN adetten; fark FİRE (OperasyonKaydiCikti).
    gelen = models.JSONField("gelen adetler ({stok pk: adet})", default=dict, blank=True)

    class Meta:
        db_table = "core_operasyon_kaydi"
        verbose_name = "operasyon kaydı"
        verbose_name_plural = "operasyon kayıtları"
        ordering = ["-yil", "-sira"]
        constraints = [
            models.UniqueConstraint(fields=["yil", "sira"], name="uq_operasyon_kaydi_yil_sira"),
            models.CheckConstraint(condition=models.Q(hedef_cikti_miktari__gt=0),
                                   name="ck_operasyon_kaydi_hedef_gt0"),
        ]

    def __str__(self):
        return self.no


class OperasyonKaydiGirdi(TemelModel):
    kayit = models.ForeignKey(
        OperasyonKaydi, on_delete=models.CASCADE, related_name="girdi_satirlari")
    girdi = models.ForeignKey(
        Stok, verbose_name="girdi", on_delete=models.PROTECT,
        related_name="operasyon_kaydi_kullanimlari")
    # OperasyonGirdi'den SNAPSHOT (kayıt açıldığı andaki tanıma göre) — tanım sonradan
    # değişse bu kayıt etkilenmez. gerceklesen_miktar TASLAK'ta elle düzeltilebilir (gerçek
    # sarfiyat/fire planlanandan sapabilir); Onayla'da STOK ÇIKIŞI bu değerle yazılır —
    # gerceklesen_miktar == 0 ise o satır için hiç hareket yazılmaz (bu girdiye bu seferlik
    # gerek kalmadı anlamına gelir, hata sayılmaz).
    planlanan_miktar = models.DecimalField("planlanan miktar", max_digits=18, decimal_places=6)
    gerceklesen_miktar = models.DecimalField(
        "gerçekleşen miktar", max_digits=18, decimal_places=6)
    sira = models.PositiveSmallIntegerField("sıra", default=0)
    # Üretim siparişi akışı: onayda bu girdi için ÜS'nin ayırmasından DÜŞEN miktar (ayrılmış malzeme tüketildi; geri alınınca aynen geri eklenir).
    ayrilan_dusen = models.DecimalField("ayırmadan düşen", max_digits=18, decimal_places=6, default=0)

    class Meta:
        db_table = "core_operasyon_kaydi_girdi"
        verbose_name = "operasyon kaydı girdisi"
        verbose_name_plural = "operasyon kaydı girdileri"
        ordering = ["sira", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(planlanan_miktar__gt=0),
                                   name="ck_operasyon_kaydi_girdi_planlanan_gt0"),
            models.CheckConstraint(condition=models.Q(gerceklesen_miktar__gte=0),
                                   name="ck_operasyon_kaydi_girdi_gerceklesen_gte0"),
        ]

    def __str__(self):
        return f"{self.kayit.no} — {self.girdi.kod} × {self.gerceklesen_miktar}"


class Personel(TemelModel):
    """İNSAN KAYNAKLARI > Personel Kartları — çalışan kaydı (yalnız KAYIT: bordro, maaş,
    SGK/vergi/prim ve muhasebe fişi bu modülde YOKTUR; Yemek Takibi gibi kontrol amaçlı).
    'Aktif/ayrıldı' saklanmaz, isten_cikis_tarihi'nden türetilir (çıkış tarihi boş ya da
    bugün/gelecekteyse aktif). TC kimlik no doluysa silinmemişler arası benzersiz."""

    KAN_GRUPLARI = [(k, k) for k in ("0+", "0-", "A+", "A-", "B+", "B-", "AB+", "AB-")]

    ad = models.CharField("ad", max_length=100)
    soyad = models.CharField("soyad", max_length=100)
    tc_kimlik_no = models.CharField("TC kimlik no", max_length=11, blank=True)
    dogum_tarihi = models.DateField("doğum tarihi", null=True, blank=True)
    kan_grubu = models.CharField("kan grubu", max_length=3, blank=True, choices=KAN_GRUPLARI)
    telefon = models.CharField("telefon", max_length=20, blank=True)
    eposta = models.EmailField("e-posta", blank=True)
    adres = models.TextField("adres", blank=True)
    acil_durum_kisi = models.CharField("acil durumda aranacak kişi", max_length=120, blank=True)
    acil_durum_telefon = models.CharField("acil durum telefonu", max_length=20, blank=True)
    departman = models.CharField("departman", max_length=80, blank=True)
    gorev = models.CharField("görev", max_length=80, blank=True)
    ise_giris_tarihi = models.DateField("işe giriş tarihi")
    isten_cikis_tarihi = models.DateField("işten çıkış tarihi", null=True, blank=True)
    cikis_nedeni = models.CharField("çıkış nedeni", max_length=200, blank=True)
    notlar = models.TextField("notlar", blank=True)
    # Sisteme geçmeden ÖNCE kullanılmış yıllık izin (gün) — mevcut personelin bakiyesi doğru
    # başlasın diye; sistemde ayrıca girilen izinler buraya EKLENMEZ (çifte sayım olur).
    izin_onceki_kullanilan = models.DecimalField(
        "sisteme geçmeden önce kullanılan yıllık izin (gün)", max_digits=5, decimal_places=1,
        default=0)
    # Fotoğraf da ÖZEL depoda (MEDIA_ROOT dışı) — yalnız yetkili görünümle sunulur (bkz. storage).
    foto = models.FileField("fotoğraf", storage=ik_ozel_depo, upload_to=personel_foto_yolu,
                            blank=True, max_length=100)
    # Personelin KENDİ mesai (giriş/çıkış) hesabı — bkz. core.services.mesai_hesap. Bu hesaba
    # HİÇBİR EkranYetki verilmez (yalnız /mesai/'ye erişir); yönetici oluşturur/kapatır.
    kullanici = models.OneToOneField(
        settings.AUTH_USER_MODEL, verbose_name="mesai hesabı", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="personel_karti")

    class Meta:
        db_table = "core_personel"
        verbose_name = "personel"
        verbose_name_plural = "personel"
        ordering = ["ad", "soyad"]
        constraints = [
            models.UniqueConstraint(
                fields=["tc_kimlik_no"],
                condition=models.Q(silindi=False) & ~models.Q(tc_kimlik_no=""),
                name="uq_personel_tc_dolu"),
            models.CheckConstraint(
                condition=(models.Q(isten_cikis_tarihi__isnull=True)
                           | models.Q(isten_cikis_tarihi__gte=models.F("ise_giris_tarihi"))),
                name="ck_personel_cikis_gte_giris"),
            models.CheckConstraint(
                condition=models.Q(izin_onceki_kullanilan__gte=0),
                name="ck_personel_izin_onceki_gte0"),
        ]

    def __str__(self):
        return self.ad_soyad

    @property
    def ad_soyad(self):
        return f"{self.ad} {self.soyad}".strip()


class PersonelIzin(TemelModel):
    """İNSAN KAYNAKLARI > İzinler — personelin izin kaydı (onay akışı YOK, direkt kayıt).
    Yalnız tur=YILLIK yıllık izin bakiyesini düşürür; rapor/mazeret/ücretsiz/diğer yalnız
    kayıttır. `gun` Pazar günleri VE resmî tatiller (bkz. ResmiTatil) hariç takvim günü
    olarak ÖNERİLİR, kullanıcı yarım gün adımlarıyla elle düzeltebilir."""

    class Tur(models.TextChoices):
        YILLIK = "YILLIK", "Yıllık İzin"
        RAPOR = "RAPOR", "Rapor"
        MAZERET = "MAZERET", "Mazeret İzni"
        UCRETSIZ = "UCRETSIZ", "Ücretsiz İzin"
        DIGER = "DIGER", "Diğer"

    personel = models.ForeignKey(
        Personel, verbose_name="personel", on_delete=models.PROTECT, related_name="izinler")
    tur = models.CharField("tür", max_length=10, choices=Tur.choices, default=Tur.YILLIK)
    baslangic = models.DateField("başlangıç")
    bitis = models.DateField("bitiş")
    gun = models.DecimalField("gün", max_digits=5, decimal_places=1)
    aciklama = models.CharField("açıklama", max_length=300, blank=True)

    class Meta:
        db_table = "core_personel_izin"
        verbose_name = "personel izni"
        verbose_name_plural = "personel izinleri"
        ordering = ["-baslangic", "-id"]
        indexes = [models.Index(fields=["personel", "baslangic"], name="ix_personel_izin_kisi_bas")]
        constraints = [
            models.CheckConstraint(condition=models.Q(bitis__gte=models.F("baslangic")),
                                   name="ck_personel_izin_bitis_gte_baslangic"),
            models.CheckConstraint(condition=models.Q(gun__gt=0),
                                   name="ck_personel_izin_gun_gt0"),
        ]

    def __str__(self):
        return f"{self.personel.ad_soyad} — {self.get_tur_display()} {self.baslangic}–{self.bitis}"


class PersonelBelge(TemelModel):
    """İNSAN KAYNAKLARI > Özlük Belgeleri — personele bağlı evrak (kimlik, sağlık raporu,
    sözleşme, İSG eğitimi, operatör belgesi...). Bir kayıt = bir dosya; yenileme, aynı türden
    YENİ kayıt açmaktır (eskisi geçmiş olarak kalır; "süresi dolacaklar" hesabı yalnız her
    (personel, tür) grubunun en geç bitişli kaydına bakar). Dosya ÖZEL depoda (MEDIA_ROOT dışı,
    UUID adlı) tutulur, özgün ad yalnız `orijinal_ad`'da — bkz. core.storage."""

    class Tur(models.TextChoices):
        KIMLIK = "KIMLIK", "Kimlik"
        SAGLIK_RAPORU = "SAGLIK_RAPORU", "Sağlık Raporu"
        SOZLESME = "SOZLESME", "İş Sözleşmesi"
        SGK_GIRIS = "SGK_GIRIS", "SGK İşe Giriş Belgesi"
        ISG_EGITIMI = "ISG_EGITIMI", "İSG Eğitim Belgesi"
        OPERATOR_BELGESI = "OPERATOR_BELGESI", "Operatör / Yeterlilik Belgesi"
        EHLIYET = "EHLIYET", "Ehliyet"
        DIPLOMA = "DIPLOMA", "Diploma / Sertifika"
        DIGER = "DIGER", "Diğer"

    personel = models.ForeignKey(
        Personel, verbose_name="personel", on_delete=models.PROTECT, related_name="belgeler")
    tur = models.CharField("tür", max_length=20, choices=Tur.choices)
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    dosya = models.FileField("dosya", storage=ik_ozel_depo, upload_to=personel_belge_yolu,
                             max_length=100)
    orijinal_ad = models.CharField("özgün dosya adı", max_length=255)
    bitis_tarihi = models.DateField("geçerlilik bitiş tarihi", null=True, blank=True)

    class Meta:
        db_table = "core_personel_belge"
        verbose_name = "personel belgesi"
        verbose_name_plural = "personel belgeleri"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["personel", "tur"], name="ix_personel_belge_kisi_tur")]

    def __str__(self):
        return f"{self.personel.ad_soyad} — {self.get_tur_display()}"


class PersonelDevam(TemelModel):
    """İNSAN KAYNAKLARI > Devam / Yoklama — personelin o günkü durumu (yalnız KAYIT: saat, mesai,
    ücret alanı YOK). İzinli/Raporlu SAKLANMAZ: gün, PersonelIzin kaydıyla örtüşüyorsa durum izinden
    TÜRETİLİR (bkz. core.services.personel_devam.turet_durum); yalnız Geldi / Gelmedi / Yarım Gün
    girilir. Girilmemiş gün = satır yok. Bir kişi için bir günde tek aktif kayıt."""

    class Durum(models.TextChoices):
        GELDI = "GELDI", "Geldi"
        GELMEDI = "GELMEDI", "Gelmedi"
        YARIM_GUN = "YARIM_GUN", "Yarım Gün"

    personel = models.ForeignKey(
        Personel, verbose_name="personel", on_delete=models.PROTECT, related_name="devam_kayitlari")
    tarih = models.DateField("tarih")
    durum = models.CharField("durum", max_length=10, choices=Durum.choices)
    notlar = models.CharField("not", max_length=200, blank=True)

    class Meta:
        db_table = "core_personel_devam"
        verbose_name = "personel devam kaydı"
        verbose_name_plural = "personel devam kayıtları"
        ordering = ["-tarih", "-id"]
        indexes = [models.Index(fields=["tarih"], name="ix_personel_devam_tarih")]
        constraints = [
            models.UniqueConstraint(
                fields=["personel", "tarih"], condition=models.Q(silindi=False),
                name="uq_personel_devam_aktif"),
        ]

    def __str__(self):
        return f"{self.personel.ad_soyad} — {self.tarih} {self.get_durum_display()}"


class PersonelUcret(TemelModel):
    """İNSAN KAYNAKLARI > Personel Ücretleri — YALNIZ KAYIT (bordro/brüt/SGK/vergi hesabı YOK).
    Bir kayıt, gecerlilik_baslangic tarihinden İTİBAREN (bir sonraki kayda kadar) geçerli
    ücreti temsil eder; zam/değişiklik yeni bir kayıt eklemekle yapılır, eski kayıt SİLİNMEZ
    (geçmiş olarak kalır) — bkz. core.services.personel_ucret.gecerli_ucret. Ücret bilgisi
    yalnız ayrı 'personel_ucret' ekran yetkisi (veya yönetici) olan kullanıcıya görünür;
    'personel' yetkisi tek başına GÖSTERMEZ (server tarafında core.yetki ile zorlanır)."""

    class Tip(models.TextChoices):
        ASGARI = "ASGARI", "Asgari Ücret"
        NET = "NET", "Net Ücret"

    personel = models.ForeignKey(
        Personel, verbose_name="personel", on_delete=models.PROTECT, related_name="ucretler")
    gecerlilik_baslangic = models.DateField("geçerlilik başlangıcı")
    tip = models.CharField("tip", max_length=10, choices=Tip.choices)
    net_tutar = models.DecimalField(
        "net tutar (TL)", max_digits=12, decimal_places=2, null=True, blank=True)
    aciklama = models.CharField("açıklama", max_length=200, blank=True)

    class Meta:
        db_table = "core_personel_ucret"
        verbose_name = "personel ücreti"
        verbose_name_plural = "personel ücretleri"
        ordering = ["-gecerlilik_baslangic", "-id"]
        indexes = [
            models.Index(fields=["personel", "gecerlilik_baslangic"],
                        name="ix_personel_ucret_kisi_bas"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["personel", "gecerlilik_baslangic"], condition=models.Q(silindi=False),
                name="uq_personel_ucret_kisi_bas_aktif"),
            # tip=ASGARI ise tutar NULL; tip=NET ise tutar dolu ve > 0 olmalı.
            models.CheckConstraint(
                condition=(models.Q(tip="ASGARI", net_tutar__isnull=True)
                          | models.Q(tip="NET", net_tutar__isnull=False, net_tutar__gt=0)),
                name="ck_personel_ucret_tip_tutar"),
        ]

    def __str__(self):
        tutar = "Asgari Ücret" if self.tip == self.Tip.ASGARI else f"{self.net_tutar} TL net"
        return f"{self.personel.ad_soyad} — {tutar} ({self.gecerlilik_baslangic:%d.%m.%Y})"


class ResmiTatil(TemelModel):
    """İNSAN KAYNAKLARI > Resmî Tatiller — tam gün resmî/dinî tatil takvimi. Yoklama
    (girilmemiş sayılmaz + 'tatil çalışması' sayacı), izin gün önerisi (bkz.
    core.services.personel_izin.pazarsiz_gun) ve aylık puantaj dökümünde kullanılır. Arife
    TANIMLANMAZ (normal iş günü sayılır); yarım gün/ücret etkisi bu modülün kapsamı
    DIŞINDADIR — bkz. core.services.resmi_tatil."""

    tarih = models.DateField("tarih")
    ad = models.CharField("ad", max_length=100)

    class Meta:
        db_table = "core_resmi_tatil"
        verbose_name = "resmî tatil"
        verbose_name_plural = "resmî tatiller"
        ordering = ["tarih"]
        constraints = [
            models.UniqueConstraint(
                fields=["tarih"], condition=models.Q(silindi=False),
                name="uq_resmi_tatil_tarih_aktif"),
        ]

    def __str__(self):
        return f"{self.tarih:%d.%m.%Y} — {self.ad}"


class MesaiIzinliAg(TemelModel):
    """İNSAN KAYNAKLARI > Mesai Ayarları — personelin kendi telefonundan mesai başlatıp
    bitirebileceği IP/CIDR aralıkları (fabrika Wi-Fi'ı). Yalnız yönetici düzenler (bkz.
    core.services.mesai_ag). Liste BOŞSA özellik KAPALI sayılır — herkes reddedilir."""

    cidr = models.CharField("IP / CIDR", max_length=43)   # ör. "5.6.7.8" veya "10.0.0.0/24"
    aciklama = models.CharField("açıklama", max_length=100, blank=True)

    class Meta:
        db_table = "core_mesai_izinli_ag"
        verbose_name = "mesai izinli ağı"
        verbose_name_plural = "mesai izinli ağları"
        ordering = ["cidr"]
        constraints = [
            models.UniqueConstraint(
                fields=["cidr"], condition=models.Q(silindi=False),
                name="uq_mesai_izinli_ag_aktif"),
        ]

    def __str__(self):
        return f"{self.cidr}" + (f" — {self.aciklama}" if self.aciklama else "")


class MesaiKaydi(TemelModel):
    """İNSAN KAYNAKLARI > Mesai Kayıtları — personelin kendi telefonundan (fabrika ağındayken)
    başlattığı/bitirdiği mesai. YALNIZ KAYIT: saatten ücret/fazla mesai HESABI YOK. Zaman HER
    ZAMAN sunucu saatidir (istemciden asla alınmaz) — bkz. core.services.mesai. Başlangıçta
    girilmemiş günlük yoklamayı otomatik "Geldi" yapar (bkz. core.services.personel_devam)."""

    class Kaynak(models.TextChoices):
        PERSONEL = "PERSONEL", "Personel"
        YONETICI = "YONETICI", "Yönetici"

    personel = models.ForeignKey(
        Personel, verbose_name="personel", on_delete=models.PROTECT,
        related_name="mesai_kayitlari")
    is_tarihi = models.DateField("iş tarihi")           # girişin TR tarihi
    giris_zamani = models.DateTimeField("giriş zamanı")
    cikis_zamani = models.DateTimeField("çıkış zamanı", null=True, blank=True)
    giris_ip = models.GenericIPAddressField("giriş IP", null=True, blank=True)
    cikis_ip = models.GenericIPAddressField("çıkış IP", null=True, blank=True)
    kaynak = models.CharField("kaynak", max_length=10, choices=Kaynak.choices,
                              default=Kaynak.PERSONEL)
    duzeltme_notu = models.CharField("düzeltme notu", max_length=300, blank=True)

    class Meta:
        db_table = "core_mesai_kaydi"
        verbose_name = "mesai kaydı"
        verbose_name_plural = "mesai kayıtları"
        ordering = ["-giris_zamani", "-id"]
        indexes = [
            models.Index(fields=["personel", "is_tarihi"], name="ix_mesai_kaydi_kisi_gun"),
        ]
        constraints = [
            # Aynı (personel, iş tarihi) için silinmemiş TEK açık (çıkışsız) kayıt olabilir —
            # birden çok KAPALI (tamamlanmış) kayıt aynı güne serbesttir (öğle molası vb.).
            models.UniqueConstraint(
                fields=["personel", "is_tarihi"],
                condition=models.Q(silindi=False, cikis_zamani__isnull=True),
                name="uq_mesai_acik_kayit"),
            models.CheckConstraint(
                condition=(models.Q(cikis_zamani__isnull=True)
                           | models.Q(cikis_zamani__gte=models.F("giris_zamani"))),
                name="ck_mesai_cikis_gte_giris"),
        ]

    def __str__(self):
        return f"{self.personel.ad_soyad} — {self.is_tarihi:%d.%m.%Y}"

    @property
    def sure_dakika(self):
        """Tamamlanmış kayıtta geçen süre (dakika); açık kayıtta None."""
        if self.cikis_zamani is None:
            return None
        return int((self.cikis_zamani - self.giris_zamani).total_seconds() // 60)


class SilmeKaydi(TemelModel):
    """Kalıcı (fiziksel) silme denetim kaydı — her fiş/bordro silmesi için BİR kayıt; silinen
    kaydın özeti (no, tarih, tutar, açıklama) + satırları JSON olarak burada kalır. Bu kaydın
    kendisi silinmez/güncellenmez (bkz. core.services.fis_sil)."""

    class Tur(models.TextChoices):
        FIS = "FIS", "Yevmiye fişi"
        CEK_BORDRO = "CEK_BORDRO", "Çek/senet bordrosu"

    tur = models.CharField("tür", max_length=12, choices=Tur.choices)
    kayit_no = models.CharField("kayıt no", max_length=40)   # fiş: "2026/189"; bordro: "#12"
    tarih = models.DateField("kayıt tarihi", null=True, blank=True)
    tutar = models.DecimalField("tutar (TL)", max_digits=18, decimal_places=2, null=True, blank=True)
    aciklama = models.CharField("açıklama", max_length=500, blank=True)
    kaynak = models.CharField("kaynak", max_length=20, blank=True)
    silen = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="silen", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+")
    veri = models.JSONField("silinen veri", default=dict)

    class Meta:
        db_table = "silme_kaydi"
        verbose_name = "silme kaydı"
        verbose_name_plural = "silme kayıtları"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_tur_display()} {self.kayit_no} silindi"


class FisNoSayaci(models.Model):
    """Mali yıl başına fiş numarası sayacı — numara GERİ DÖNMEZ: fiş silinse bile (en son fiş dahil)
    sıradaki yeni fiş silinen numarayı almaz. Değer her zaman ≥ o yıldaki en yüksek fiş no'dur
    (bkz. core.services.yevmiye._sonraki_fis_no / fis_no_sayacini_koru)."""

    yil = models.IntegerField("mali yıl", unique=True)
    son_no = models.PositiveIntegerField("son verilen fiş no", default=0)

    class Meta:
        db_table = "yevmiye_fis_no_sayaci"
        verbose_name = "fiş no sayacı"
        verbose_name_plural = "fiş no sayaçları"

    def __str__(self):
        return f"{self.yil}: {self.son_no}"


class KurDegerleme(TemelModel):
    """Dönem sonu kur değerleme kaydı — seçilen tarihte açık döviz bakiyelerinin TCMB döviz alış
    kuruyla değerlenmesi (fark fişi) ve isteğe bağlı ters kaydı (bkz. core.services.kur_degerleme).
    Fişler ``YevmiyeFisi.Kaynak.KUR_DEGERLEME``; bu kayıt ikisini birbirine bağlar."""

    tarih = models.DateField("değerleme tarihi")
    fis = models.OneToOneField(
        YevmiyeFisi, verbose_name="değerleme fişi", on_delete=models.PROTECT,
        related_name="kur_degerleme")
    ters_fis = models.OneToOneField(
        YevmiyeFisi, verbose_name="ters kayıt fişi", null=True, blank=True,
        on_delete=models.PROTECT, related_name="kur_degerleme_ters")
    toplam_kar = models.DecimalField("kambiyo kârı (TL)", max_digits=18, decimal_places=2, default=0)
    toplam_zarar = models.DecimalField("kambiyo zararı (TL)", max_digits=18, decimal_places=2, default=0)
    kurlar = models.JSONField("kullanılan kurlar", default=dict)

    class Meta:
        db_table = "kur_degerleme"
        verbose_name = "kur değerleme"
        verbose_name_plural = "kur değerlemeleri"
        ordering = ["-tarih", "-id"]

    def __str__(self):
        return f"Kur değerleme {self.tarih:%d.%m.%Y}"


class DonemselDagitim(TemelModel):
    """Dönemsel gider (180) aylık dağıtım planı satırı: her ay sonu için döviz/TL tutarı; ``fis`` üretilmişse aylık fiş
    (180.xx ALACAK / gider hesabı BORÇ, kaynak=DONEMSEL). Fatura düzenlenince/silinince plan + fişler yeniden hesaplanır/silinir
    (bkz. core.services.donemsel_gider)."""

    fatura = models.ForeignKey(Fatura, verbose_name="fatura", on_delete=models.CASCADE, related_name="donemsel_dagitimlar")
    hesap = models.ForeignKey(HesapPlani, verbose_name="180 hesabı", on_delete=models.PROTECT, related_name="donemsel_dagitimlar")
    gider_hesap = models.ForeignKey(HesapPlani, verbose_name="gider hesabı", on_delete=models.PROTECT, related_name="+")
    sira = models.PositiveIntegerField("sıra")
    ay_sonu = models.DateField("ay sonu")
    doviz = models.DecimalField("tutar (fatura para birimi)", max_digits=18, decimal_places=2)
    para_birimi = models.CharField("para birimi", max_length=3)
    kur = models.DecimalField("kur", max_digits=18, decimal_places=6)
    tl = models.DecimalField("TL karşılığı", max_digits=18, decimal_places=2)
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    fis = models.ForeignKey(YevmiyeFisi, verbose_name="dağıtım fişi", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="donemsel_dagitimlari")

    class Meta:
        db_table = "donemsel_dagitim"
        verbose_name = "dönemsel dağıtım"
        verbose_name_plural = "dönemsel dağıtımlar"
        ordering = ["fatura", "hesap", "sira"]
        constraints = [models.UniqueConstraint(fields=["fatura", "hesap", "sira"], name="uq_donemsel_dagitim_sira")]

    def __str__(self):
        return f"{self.hesap_id} {self.ay_sonu:%m.%Y}"


class PersonelBordro(TemelModel):
    """Aylık personel bordrosu tahakkuku (mali müşavirin PDF bordrosu): dönem + tahakkuk tarihi + (ops.) PDF eki (özel depo) + personel
    satırları; bordro başına TEK yevmiye fişi (kaynak=BORDRO, fiş→bordro bağı). Bkz. core.services.bordro."""

    yil = models.PositiveSmallIntegerField("dönem yılı")
    ay = models.PositiveSmallIntegerField("dönem ayı")
    tahakkuk_tarihi = models.DateField("tahakkuk tarihi")
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    dosya = models.FileField("bordro PDF", storage=ozel_depo, upload_to=bordro_dosya_yolu, blank=True)
    orijinal_ad = models.CharField("özgün dosya adı", max_length=255, blank=True)

    class Meta:
        db_table = "personel_bordro"
        verbose_name = "personel bordrosu"
        verbose_name_plural = "personel bordroları"
        ordering = ["-yil", "-ay", "-id"]

    def __str__(self):
        return f"{self.ay:02d}.{self.yil} bordro"


class PersonelBordroSatir(TemelModel):
    """Bordro satırı: bir personel carisinin aylık kalemleri (TL). Kontrol: brüt − SGK işçi − işsizlik işçi − GV − DV = net."""

    bordro = models.ForeignKey(PersonelBordro, verbose_name="bordro", on_delete=models.CASCADE, related_name="satirlar")
    # Net ödenen > 0 satırda personel carisi ZORUNLU; net = 0 satırda cari olmayabilir (yalnız ``ad_soyad`` — personel carisine ALACAK yazılmaz).
    cari = models.ForeignKey("Cari", verbose_name="personel carisi", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="bordro_satirlari")
    ad_soyad = models.CharField("ad soyad (cari yok)", max_length=150, blank=True)
    gider_hesap = models.ForeignKey(HesapPlani, verbose_name="gider hesabı", on_delete=models.PROTECT, related_name="+")
    brut = models.DecimalField("brüt kazanç", max_digits=18, decimal_places=2)
    sgk_isci = models.DecimalField("SGK işçi payı", max_digits=18, decimal_places=2, default=0)
    issizlik_isci = models.DecimalField("işsizlik işçi payı", max_digits=18, decimal_places=2, default=0)
    gelir_vergisi = models.DecimalField("gelir vergisi", max_digits=18, decimal_places=2, default=0)
    damga_vergisi = models.DecimalField("damga vergisi", max_digits=18, decimal_places=2, default=0)
    net = models.DecimalField("net ödenen", max_digits=18, decimal_places=2)
    sgk_isveren = models.DecimalField("SGK işveren (teşvik sonrası net)", max_digits=18, decimal_places=2, default=0)
    issizlik_isveren = models.DecimalField("işsizlik işveren", max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "personel_bordro_satir"
        verbose_name = "bordro satırı"
        verbose_name_plural = "bordro satırları"
        ordering = ["bordro", "id"]

    @property
    def personel_adi(self):
        return self.cari.unvan if self.cari_id else self.ad_soyad


class KdvMahsup(TemelModel):
    """KDV dönem mahsubu (191/391/190/360.30): beyanname verilince AY SONU tarihli TEK otomatik fiş (kaynak=KDV_MAHSUP, fiş→mahsup bağı). Alanlardaki ERP
    değerleri kayıt anındaki hesaplamadır (liste + "mahsup sonrası değişiklik" tespiti); bakiyeler yine defterden hesaplanır. Bkz. core.services.kdv_mahsup."""

    yil = models.PositiveSmallIntegerField("dönem yılı")
    ay = models.PositiveSmallIntegerField("dönem ayı")
    donem_sonu = models.DateField("dönem sonu (fiş tarihi)")
    beyan_devreden = models.DecimalField("beyan: sonraki döneme devreden KDV", max_digits=18, decimal_places=2, default=0)
    beyan_odenecek = models.DecimalField("beyan: ödenecek KDV", max_digits=18, decimal_places=2, default=0)
    fark_hesap = models.ForeignKey(HesapPlani, verbose_name="fark hesabı", on_delete=models.PROTECT, related_name="+")
    aciklama = models.CharField("açıklama", max_length=200, blank=True)
    dosya = models.FileField("beyanname PDF", storage=ozel_depo, upload_to=kdv_mahsup_dosya_yolu, blank=True)
    orijinal_ad = models.CharField("özgün dosya adı", max_length=255, blank=True)
    erp_191 = models.DecimalField("ERP 191 bakiyesi", max_digits=18, decimal_places=2, default=0)
    erp_391 = models.DecimalField("ERP 391 bakiyesi", max_digits=18, decimal_places=2, default=0)
    erp_190 = models.DecimalField("önceki 190 bakiyesi", max_digits=18, decimal_places=2, default=0)
    erp_devreden = models.DecimalField("ERP devreden (190+191−391)", max_digits=18, decimal_places=2, default=0)
    fark = models.DecimalField("fark (ERP − beyan)", max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "kdv_mahsup"
        verbose_name = "KDV dönem mahsubu"
        verbose_name_plural = "KDV dönem mahsupları"
        ordering = ["-yil", "-ay", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["yil", "ay"], condition=models.Q(silindi=False), name="uq_kdv_mahsup_donem_aktif"),
        ]

    def __str__(self):
        return f"{self.ay:02d}/{self.yil} KDV mahsubu"
