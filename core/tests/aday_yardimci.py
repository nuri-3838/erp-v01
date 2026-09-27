"""Aday testleri için paylaşılan ufak yardımcı — ik_yardimci.py ile aynı desen.

Kaynak (AdayMusteriKategori) artık AdayMusteri'de ZORUNLU ve yalnız YAPRAK (alt kaynağı
olmayan) kayıtlar atanabilir (bkz. core.services.aday._kategori). Tip/Potansiyel/Aşama'nın
aksine kaynaklar migration'da tohumlanmaz (üretim listesi kullanıcı elle yönetir) — bu
yüzden testlerin kendi sabit, YAPRAK bir kaynağı olması gerekir.
"""
from core.models import AdayMusteriKategori


def varsayilan_kaynak_id():
    """Testler için tek, sabit kodlu (99) YAPRAK kaynak — get_or_create ile idempotent.
    Her TestCase kendi transaction'ında rollback olduğu için her çağrıda tazelenir; bu
    yüzden ucuz bir get_or_create (silme/ekleme yok) sorun çıkarmaz."""
    kaynak, _ = AdayMusteriKategori.objects.get_or_create(
        silindi=False, ust=None, kod="99", defaults={"ad": "TEST KAYNAK"})
    return kaynak.pk
