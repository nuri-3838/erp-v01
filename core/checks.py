"""Django system check'leri — `manage.py check` (her deploy/reload öncesi) çalışır.

NOT: Eski `hesap_hiyerarsi_tutarli` (core.E001) check'i KALDIRILDI. O check, hesap
planındaki iki hiyerarşinin (kod metni vs ayrı `ust_hesap` FK) ayrışmasını yakalardı.
Artık `ust_hesap` FK yok; hiyerarşi TEK kaynaktan — `hesap_kodu` metninden — türetiliyor,
dolayısıyla ayrışma imkânsız ve check gereksiz. Yeni check gerekirse buraya eklenir.
"""
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register


@register()
def ik_ozel_dizin_medya_disinda(app_configs, **kwargs):
    """core.E002 — İK özel dosya dizini (KVKK hassas evrak/fotoğraf) MEDIA_ROOT ya da
    STATIC_ROOT ile aynı ya da altında olamaz: prod nginx bu iki dizini KİMLİK DOĞRULAMASIZ
    sunar; ayar yanlışlıkla oraya çekilirse evraklar herkese açılır."""
    hatalar = []
    ozel = getattr(settings, "IK_OZEL_DIR", None)
    if not ozel:
        return [Error("IK_OZEL_DIR ayarı tanımlı değil.", id="core.E002")]
    ozel = Path(ozel).resolve()
    for ad in ("MEDIA_ROOT", "STATIC_ROOT"):
        kok = getattr(settings, ad, None)
        if not kok:
            continue
        kok = Path(kok).resolve()
        if ozel == kok or kok in ozel.parents:
            hatalar.append(Error(
                f"IK_OZEL_DIR ({ozel}), {ad} ({kok}) ile aynı ya da altında olamaz.",
                hint="Özlük evrakları nginx'in kimlik doğrulamasız sunduğu bir dizine "
                     "konamaz; IK_OZEL_DIR'i MEDIA_ROOT/STATIC_ROOT dışında bir yola ayarlayın.",
                id="core.E002"))
    return hatalar
