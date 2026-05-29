"""Çekirdek modeller.

Burada yalnızca tüm tabloların paylaştığı invariant taban (audit + soft delete,
spec 0b-g) ile v0.1 veri modelinin ilk tablosu HESAP_PLANI (spec bölüm 2) var.
"""
from django.conf import settings
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
    aktif = models.BooleanField("aktif", default=True)

    class Meta:
        db_table = "hesap_plani"
        verbose_name = "hesap"
        verbose_name_plural = "hesap planı"
        ordering = ["hesap_kodu"]

    def __str__(self):
        return f"{self.hesap_kodu} {self.hesap_adi}"
