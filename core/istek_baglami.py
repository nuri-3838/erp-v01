"""İstek bağlamı: o anki isteğin istemci IP'si (``contextvars``) — servis katmanı ``request`` görmeden silme denetim kaydına (``SilmeKaydi.ip_adresi``) yazabilsin diye.
``IstekBaglamiMiddleware`` her istekte IP'yi kurar, istek bitince geri alır; istek dışında (komut, test, shell) ``ip()`` None döner."""
import contextvars

from core.ip import istemci_ip

_ip = contextvars.ContextVar("istek_ip", default=None)


def ip():
    """Geçerli isteğin IP'si ya da None (istek dışı / geçersiz adres)."""
    return _ip.get() or None


class IstekBaglamiMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ham = (istemci_ip(request) or "").strip()
        token = _ip.set(ham[:45] or None)
        try:
            return self.get_response(request)
        finally:
            _ip.reset(token)
