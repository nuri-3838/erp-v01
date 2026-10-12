"""Şifre disiplini: "ilk girişte şifre değiştirilmeli" işaretli personel hesabı (``Personel.sifre_degistirilmeli``) GİRİŞ ANINDA tespit edilir
(``user_logged_in`` sinyali → session anahtarı ``SESSION_ANAHTARI``; her istekte ek sorgu YOK) ve hangi sayfayı isterse istesin 🔑 Şifre ekranına
yönlendirilir; yalnız şifre ekranı, çıkış ve statik/medya dosyaları serbesttir. Personel şifresini değiştirince görünüm anahtarı temizler
(bkz. core.services.mesai_hesap, views.sifre_degistir). Yönetici şifreyi sıfırlayınca Django oturum hash'i değiştiği için açık oturum zaten
düşer; yeniden girişte zorunluluk devreye girer."""
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.shortcuts import redirect
from django.urls import reverse

SESSION_ANAHTARI = "sifre_degistirilmeli"


@receiver(user_logged_in)
def _giriste_isaretle(sender, request, user, **kwargs):
    from core.services.mesai_hesap import sifre_degistirilmeli_mi
    if request is not None and hasattr(request, "session"):
        request.session[SESSION_ANAHTARI] = sifre_degistirilmeli_mi(user)


class SifreDegistirmeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and getattr(request, "session", None) is not None
                and request.session.get(SESSION_ANAHTARI) and not self._muaf(request.path)):
            return redirect("core:sifre_degistir")
        return self.get_response(request)

    @staticmethod
    def _muaf(path: str) -> bool:
        if path == reverse("core:sifre_degistir") or path == reverse("logout"):
            return True
        return any(onek and path.startswith(onek) for onek in (settings.STATIC_URL, settings.MEDIA_URL))
