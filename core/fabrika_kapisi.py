"""Fabrika kapısı: operatör hesaplar (``core.yetki.fabrika_kisitli_mi``) fabrika ağı dışından ``/atolye/*`` ve ``/mesai/*`` sayfalarına giremez.
Fabrika IP'sinden gelen her istek açık mesainin ``son_fabrika_gorulme``'sini günceller (heartbeat dahil); fabrika DIŞINDAN gelen istek, açık mesaide
tolerans sayacını başlatır ve tolerans dolmuşsa mesaiyi otomatik kapatır (bkz. core.services.mesai_otomatik). Yönetici/ofis kullanıcıları ve ağ listesi
boşken ya da ayar kapalıyken herkes muaftır. Çıkış (/logout/) ve statik/medya yolları kapı dışındadır."""
from django.http import JsonResponse
from django.shortcuts import render

from core.ip import istemci_ip
from core.services import mesai_ag, mesai_otomatik

KAPILI_YOLLAR = ("/atolye/", "/mesai/")
PING_YOLU = "/atolye/ping/"


class FabrikaKapisiMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(KAPILI_YOLLAR):
            yanit = self._kontrol(request)
            if yanit is not None:
                return yanit
        return self.get_response(request)

    @staticmethod
    def _kontrol(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        from core.yetki import fabrika_kisitli_mi
        if not fabrika_kisitli_mi(user):
            return None
        if not mesai_otomatik.devrede_mi():
            return None
        from core.services.atolye import personel_coz
        personel = personel_coz(user)
        if mesai_ag.ip_izinli_mi(istemci_ip(request)):
            if personel is not None:
                mesai_otomatik.fabrika_gorulde(personel)
            return None
        kapandi = mesai_otomatik.dis_ip_gorulde(personel, ip=istemci_ip(request)) if personel is not None else False
        if request.path == PING_YOLU:
            yanit = JsonResponse({"fabrika_disi": True, "mesai_kapandi": kapandi}, status=403)
        else:
            yanit = render(request, "core/fabrika_disi.html", {
                "personel": personel, "acik_kayit": None, "kapandi": kapandi,
                "tolerans_dk": mesai_otomatik.ayar().dis_ip_tolerans_dk}, status=403)
        yanit["Cache-Control"] = "no-store"
        return yanit
