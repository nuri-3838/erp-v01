"""İstemci IP adresi — GÜVENLİ okuma (bkz. core.services.mesai_ag, "fabrika Wi-Fi'ı" kontrolü).

Sunucudaki nginx (deploy config) `X-Real-IP` başlığını HER ZAMAN kendi gördüğü
``$remote_addr`` değeriyle DEĞİŞTİRİR: ``proxy_set_header X-Real-IP $remote_addr;`` —
istemcinin göndermiş olabileceği sahte değeri EZER (eklemez), bu yüzden bu başlık
güvenilirdir. ``X-Forwarded-For`` ise GÜVENİLMEZ: nginx ``$proxy_add_x_forwarded_for``
kullanır ve bu, istemcinin göndermiş olabileceği sahte değerin SONUNA gerçek IP'yi
EKLER ("sahte_ip, gerçek_ip") — ilk değeri okumak sahte IP'yi kabul etmek olurdu.

gunicorn yalnız 127.0.0.1:8001'i dinler (dışarıdan erişilemez), dolayısıyla Django'nun
kendi ``REMOTE_ADDR``'ı prod'da her zaman nginx'in kendisidir (127.0.0.1) — gerçek istemci
DEĞİL; bu yüzden ``X-Real-IP`` başlığı okunmalıdır. Yerelde (nginx yokken, ör. runserver/
test) bu başlık hiç gelmez; bu durumda ``REMOTE_ADDR``'a düşülür.
"""


def istemci_ip(request) -> str:
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or ""
