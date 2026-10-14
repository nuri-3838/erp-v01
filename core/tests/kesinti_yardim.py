"""Kesinti / Masraf ekranı testleri için: ekran artık iki adımlı (Önizle → Onayla ve Kaydet)."""


def onayli_post(client, url, veri):
    """1) Önizleme POST'u (kayıt yazmaz) → 2) önizlemenin imzasıyla ``onayla``. Önizleme/doğrulama hatasında ilk yanıt döner."""
    r = client.post(url, veri)
    onizleme = r.context.get("onizleme") if getattr(r, "context", None) else None
    if r.status_code != 200 or not onizleme:
        return r
    return client.post(url, {**veri, "onayla": "1", "imza": onizleme["imza"]})
