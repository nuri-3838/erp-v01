"""Django system check'leri — `manage.py check` (her deploy/reload öncesi) çalışır.

Hesap planında İKİ hiyerarşi var: (a) kod metni (320.10.0001 -> 320), (b) ``ust_hesap``
FK. Roll-up (rapor) kod metnine, yaprak kuralı FK'ya güvenir. Bu ikisi ayrışırsa raporlar
ile yaprak kuralı çelişir. Oluşturma anında zaten tutarlı kurulur; bu check, seed/elle/
toplu yükleme ile oluşabilecek AYRIŞMAYI deploy kapısında yakalar (E001).
"""
from django.core.checks import Error, register


@register()
def hesap_hiyerarsi_tutarli(app_configs, **kwargs):
    from core.models import HesapPlani
    try:
        hesaplar = list(
            HesapPlani.objects.values("hesap_kodu", "ust_hesap_id")
        )
    except Exception:
        # DB henüz hazır değil (ilk migrate öncesi) — check'i atla.
        return []

    hatalar = []
    for h in hesaplar:
        kod = h["hesap_kodu"]
        ust = h["ust_hesap_id"]
        beklenen = kod.rsplit(".", 1)[0] if "." in kod else None
        if ust != beklenen:
            hatalar.append(Error(
                f"Hesap {kod}: ust_hesap={ust!r}, ama koduna göre üst hesap "
                f"{beklenen!r} olmalı.",
                hint="Kod metni ile ust_hesap FK ayrışmış; roll-up (rapor) ile yaprak "
                     "kuralı çelişir. Hesabı düzeltin ya da yeniden oluşturun.",
                id="core.E001",
            ))
    return hatalar
