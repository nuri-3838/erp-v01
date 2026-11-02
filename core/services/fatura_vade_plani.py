"""FATURA VADE PLANI (çoklu vade) — isteğe bağlı; YALNIZ vade bilgisi: fiş/muhasebe/stok HİÇ değişmez.

``Fatura.vade_plani`` = [{"vade": "YYYY-MM-DD", "tutar": "50000.00"}, ...] (JSON; vade sırasına göre). Kurallar:
- Satır tutarlarının toplamı faturanın ÖDENECEK tutarına (``Fatura.odenecek``: cariye yazılan tutar; tevkifat/stopaj sonrası) EŞİT olmalıdır; değilse kaydedilmez.
- Plan yoksa (None/boş) bugünkü tek ``vade_tarihi`` aynen geçerlidir. Plan varsa ``vade_tarihi`` = ilk plan vadesi (eski ekranlar için), plan geçerli kaynaktır.
- DAĞITIM (``dagit``): faturanın açık kalanı plan satırlarına FIFO dağıtılır — ödemeler EN ERKEN vadeden düşer; her satır kendi vadesinde görünür
  (ödeme takvimi, yaşlandırma, vadesi geçenler). Hesap tek yerde (``fatura_odeme.fifo_kalemler`` bu modülü çağırır).
- Plan toplamı sonradan faturayla uyuşmaz hâle gelirse (fatura tutarı başka yoldan değişti) plan YOK SAYILIR (tek vade_tarihi'ne dönülür) ve faturada uyarı gösterilir."""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla

SIFIR = Decimal("0.00")
ESIK = Decimal("0.005")
EN_FAZLA_SATIR = 36


class VadePlaniHatasi(ValueError):
    """Vade planı kural ihlali (Türkçe mesaj)."""


def plan_oku(fatura_veya_json) -> list:
    """[(vade: date, tutar: Decimal)] vadeye göre sıralı (aynı tarihte giriş sırası korunur). Plan yok ya da kayıt bozuksa []."""
    ham = fatura_veya_json if isinstance(fatura_veya_json, (list, tuple)) else getattr(fatura_veya_json, "vade_plani", None)      # Fatura ya da ham JSON listesi
    cikti = []
    for r in ham or []:
        try:
            cikti.append((datetime.date.fromisoformat(r["vade"]), Decimal(str(r["tutar"]))))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return []
    return sorted(cikti, key=lambda x: x[0])


def dogrula(girdi, odenecek) -> list:
    """Form girdisini ([{"vade": date|"YYYY-MM-DD", "tutar": Decimal|"1.234,56"}, ...]; tamamen boş satırlar atlanır) doğrular ve kayıt biçimine çevirir.
    Boş liste = plan yok. Toplam ``odenecek`` ile (kuruşa kadar) eşit olmalı. Hata: VadePlaniHatasi."""
    satirlar = []
    for i, g in enumerate(girdi or [], 1):
        vade, tutar = g.get("vade"), g.get("tutar")
        if vade in (None, "") and tutar in (None, ""):
            continue
        if vade in (None, "") or tutar in (None, ""):
            raise VadePlaniHatasi(f"Vade planı satır {i}: vade tarihi ve tutar birlikte girilmeli.")
        if not isinstance(vade, datetime.date):
            try:
                vade = datetime.date.fromisoformat(str(vade).strip())
            except ValueError:
                raise VadePlaniHatasi(f"Vade planı satır {i}: vade tarihi geçersiz.")
        if not 2000 <= vade.year <= 2100:
            raise VadePlaniHatasi(f"Vade planı satır {i}: vade tarihi geçersiz.")
        try:
            t = tutar if isinstance(tutar, Decimal) else parse_tr(tutar)
        except (SayiHatasi, InvalidOperation, ValueError):
            raise VadePlaniHatasi(f"Vade planı satır {i}: tutar geçersiz.")
        t = yuvarla(t, 2)
        if t <= 0:
            raise VadePlaniHatasi(f"Vade planı satır {i}: tutar sıfırdan büyük olmalı.")
        satirlar.append((vade, t))
    if not satirlar:
        return []
    if len(satirlar) > EN_FAZLA_SATIR:
        raise VadePlaniHatasi(f"Vade planı en fazla {EN_FAZLA_SATIR} satır olabilir.")
    toplam = sum((t for _v, t in satirlar), SIFIR)
    odenecek = yuvarla(odenecek, 2)
    if abs(toplam - odenecek) > ESIK:
        fark = odenecek - toplam
        raise VadePlaniHatasi(f"Vade planı toplamı ({format_tr(toplam)}) faturanın ödenecek tutarına ({format_tr(odenecek)}) eşit olmalı; "
                              f"fark {'eksik' if fark > 0 else 'fazla'}: {format_tr(abs(fark))}.")
    satirlar.sort(key=lambda x: x[0])
    return [{"vade": v.isoformat(), "tutar": format(t, "f")} for v, t in satirlar]


def ayarla(fatura, girdi, kullanici=None):
    """Faturanın vade planını yazar (``girdi`` None DEĞİL; boş liste = planı kaldır). Satırlar zaten kayıtlıysa çağrılmalıdır (ödenecek tutar satırlardan).
    Plan varsa ``vade_tarihi`` ilk plan vadesine çekilir. Doğrulama hatası VadePlaniHatasi (çağıran FaturaHatasi'na çevirir; atomik blok geri alınır)."""
    plan = dogrula(girdi, fatura.odenecek)
    fatura.vade_plani = plan or None
    alanlar = ["vade_plani", "updated_by", "updated_at"]
    if plan:
        fatura.vade_tarihi = datetime.date.fromisoformat(plan[0]["vade"])
        alanlar.append("vade_tarihi")
    fatura.updated_by = kullanici
    fatura.save(update_fields=alanlar)
    return fatura


def dagit(plan, tutar, kalan):
    """Açık kalanı plan satırlarına FIFO dağıtır: ödenen (tutar − kalan) en erken vadeden düşer. [{"sira", "vade", "tutar", "kalan"}] döner;
    plan yoksa ya da toplamı ``tutar`` ile uyuşmuyorsa None."""
    if not plan or abs(sum((t for _v, t in plan), SIFIR) - tutar) > ESIK:
        return None
    odenen = max(tutar - kalan, SIFIR)
    cikti = []
    for sira, (vade, t) in enumerate(plan, 1):
        dus = min(t, odenen)
        odenen -= dus
        k = t - dus
        cikti.append({"sira": sira, "vade": vade, "tutar": t, "kalan": k if k >= ESIK else SIFIR})
    return cikti
