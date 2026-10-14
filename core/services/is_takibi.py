"""ATÖLYE > Personel İş Takibi (2026-10-14): "kim ne iş yaptı". Yalnız yönetici (görünüm).

* Şu an: açık iş oturumları (ekip, başlangıç, geçen süre) + mesaide olup açık işi olmayanlar ("boşta").
* Geçmiş: tek listede (a) iş oturumları (BITTI / IPTAL / açık) ve (b) oturumsuz ONAYLI kayıtlar — ofis "Üretildi" / toplu üretim (istasyon emirli,
  personelsiz) ve EMİRSİZ kayıtlar; fason dönüşleri (dış işçilik) kapsam dışı. Oturumsuz satırda süre YOK ("Süresiz kayıt: N" sayacı).
* Ekip işi: sağlam/fire satırda EKİPÇE gösterilir; kişi özetinde "katıldığı" (tam) ve "pay" (adam-saniye oranı) ayrı. Fire % = fire ÷ (sağlam + fire).
* Verimlilik = kişinin oturum katılım süresi ÷ aralıktaki toplam mesai süresi ("oturum süresi ÷ mesai"); açık oturum/mesai şimdiye kadar sayılır,
  iptal oturum (iş yapılmadı) süreye girmez, kaydı geri alınan oturumun süresi sayılır adedi sayılmaz.
Sorgu sayısı sabittir (oturum + üye prefetch, kayıt + çıktı prefetch, mesai, istasyon/personel listeleri); hesaplar Python'da."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Prefetch, Q
from django.utils import timezone

from core.models import IsOturumu, IsOturumuUyesi, MesaiKaydi, OperasyonKaydi, OperasyonKaydiCikti
from core.services import is_oturumu
from core.services.varyant import varyant_kisa
from core.tarih import _TR, tr_bugun

SIFIR = Decimal("0")
KAYNAKLAR = (("oturum", "Atölye oturumu"), ("ofis", "Ofis kaydı (istasyon emirli, oturumsuz)"), ("emirsiz", "Emirsiz"))
VARYANT_SR = ("markalama", "kilavuz", "afis", "yan_etiket")


def _sr(onek):
    return [f"{onek}__{a}" for a in VARYANT_SR]


def _etiket(sv):
    return varyant_kisa(sv) if sv is not None else ""


def _yuzde(fire, saglam):
    toplam = (saglam or SIFIR) + (fire or SIFIR)
    return (Decimal(fire or 0) * 100 / toplam).quantize(Decimal("0.1")) if toplam else None


def _sure_sn(bas, bit, simdi):
    return max(int(((bit or simdi) - bas).total_seconds()), 0)


# --- Şu an ---------------------------------------------------------------------------------------------------------------------------------

def su_an(simdi=None) -> dict:
    """{"oturumlar": [{oturum, ozet, cikti, varyant, dunku}], "bosta": [{personel, mesai, sure_saniye, dunku}], "mesaide": n}."""
    simdi = simdi or timezone.now()
    bugun = tr_bugun()
    oturumlar = (IsOturumu.objects.filter(silindi=False, durum=IsOturumu.Durum.ACIK)
                 .select_related("istasyon_emri__operasyon__cikti", "istasyon_emri__cikti_varyant__stok", "uretim_emri", "istasyon", "baslatan",
                                 *_sr("istasyon_emri__cikti_varyant"))
                 .prefetch_related(is_oturumu._uyeler_prefetch()).order_by("baslangic_zamani", "pk"))
    satirlar, mesgul = [], set()
    for o in oturumlar:
        oz = is_oturumu.ozet(o)
        mesgul.update(u.personel_id for u in oz["uyeler"] if u.ayrilma_zamani is None)
        satirlar.append({"oturum": o, "ozet": oz, "ie": o.istasyon_emri, "cikti": o.istasyon_emri.operasyon.cikti,
                         "varyant": _etiket(o.istasyon_emri.cikti_varyant), "dunku": o.is_tarihi < bugun})
    mesailer = list(MesaiKaydi.objects.filter(silindi=False, cikis_zamani__isnull=True).select_related("personel").order_by("personel__ad", "personel__soyad"))
    bosta = [{"personel": m.personel, "mesai": m, "sure_saniye": _sure_sn(m.giris_zamani, None, simdi), "sure_metni": is_oturumu.sure_metni(_sure_sn(m.giris_zamani, None, simdi)),
              "dunku": m.is_tarihi < bugun} for m in mesailer if m.personel_id not in mesgul]
    return {"oturumlar": satirlar, "bosta": bosta, "mesaide": len(mesailer)}


# --- Geçmiş ----------------------------------------------------------------------------------------------------------------------------------

def _oturum_satiri(o, simdi):
    oz = is_oturumu.ozet(o)
    ie = o.istasyon_emri
    kayit = o.kayit if o.kayit_id else None
    geri_alindi = bool(kayit is not None and kayit.silindi)
    iptal = o.durum == IsOturumu.Durum.IPTAL
    saglam = SIFIR if (iptal or geri_alindi or o.miktar is None) else o.miktar
    fire = SIFIR if (iptal or geri_alindi) else (o.fire or SIFIR)
    uyeler = oz["uyeler"]
    toplam_sn = sum(u.katilim_saniye(o.bitis_zamani) for u in uyeler) or 0
    paylar = []
    for u in uyeler:
        sn = u.katilim_saniye(o.bitis_zamani)
        oran = (Decimal(sn) / Decimal(toplam_sn)) if toplam_sn else (Decimal(1) / Decimal(len(uyeler)))
        paylar.append({"personel": u.personel, "saniye": 0 if iptal else sn, "oran": oran})   # iptal (iş yapılmadı): süre sayılmaz
    return {"tur": "oturum", "tarih": o.is_tarihi, "zaman": o.baslangic_zamani, "oturum": o, "kayit": kayit, "ozet": oz, "istasyon": o.istasyon,
            "cikti": ie.operasyon.cikti, "varyant": _etiket(ie.cikti_varyant), "personeller": [u.personel for u in uyeler], "paylar": paylar,
            "personel_metni": oz["ekip_metni"] or "—", "saglam": saglam, "fire": fire, "fire_yuzde": _yuzde(fire, saglam),
            "sure_saniye": None if iptal else oz["sure_saniye"], "sure_metni": "—" if iptal else oz["sure_metni"], "adam_saniye": 0 if iptal else oz["adam_saniye"],
            "kaynak": "oturum", "kaynak_metni": f"{o.uretim_emri.no} / {ie.no}", "us_pk": o.uretim_emri_id, "ie_pk": ie.pk,
            "durum": o.get_durum_display(), "acik": o.durum == IsOturumu.Durum.ACIK, "iptal": iptal, "geri_alindi": geri_alindi,
            "href": f"/uretim/kayitlar/{kayit.pk}/" if kayit is not None else f"/uretim/is-oturumlari/?ara={ie.no}"}


def _kayit_satiri(k):
    ana = next((c for c in k.ciktilar.all() if c.ana_mi and not c.silindi), None)
    saglam = ana.miktar if ana is not None else k.hedef_cikti_miktari
    beklenen = (ana.beklenen_miktar if ana is not None and ana.beklenen_miktar is not None else k.hedef_cikti_miktari)
    fire = max(beklenen - saglam, SIFIR)
    emirsiz = k.istasyon_emri_id is None and k.uretim_emri_id is None
    return {"tur": "kayit", "tarih": k.tarih, "zaman": k.created_at, "oturum": None, "kayit": k, "ozet": None, "istasyon": k.operasyon.istasyon,
            "cikti": k.operasyon.cikti, "varyant": _etiket(k.cikti_varyant), "personeller": [k.personel] if k.personel_id else [],
            "paylar": [{"personel": k.personel, "saniye": 0, "oran": Decimal(1)}] if k.personel_id else [],
            "personel_metni": k.personel.ad_soyad if k.personel_id else "—", "saglam": saglam, "fire": fire, "fire_yuzde": _yuzde(fire, saglam),
            "sure_saniye": None, "sure_metni": "—", "adam_saniye": 0,
            "kaynak": "emirsiz" if emirsiz else "ofis",
            "kaynak_metni": "Emirsiz" if emirsiz else (f"{k.uretim_emri.no} / {k.istasyon_emri.no}" if k.istasyon_emri_id else k.uretim_emri.no),
            "us_pk": k.uretim_emri_id, "ie_pk": k.istasyon_emri_id, "durum": "Onaylı", "acik": False, "iptal": False, "geri_alindi": False,
            "href": f"/uretim/kayitlar/{k.pk}/"}


def gecmis(*, baslangic, bitis, personel_id=None, istasyon_id=None, kaynak="", simdi=None) -> list:
    """Tarih aralığındaki satırlar (en yeni üstte). Oturumsuz satır = oturumu olmayan onaylı, fasonsuz kayıt."""
    simdi = simdi or timezone.now()
    oq = (IsOturumu.objects.filter(silindi=False, is_tarihi__gte=baslangic, is_tarihi__lte=bitis)
          .select_related("istasyon_emri__operasyon__cikti", "istasyon_emri__cikti_varyant__stok", "uretim_emri", "istasyon", "baslatan", "bitiren", "kayit",
                          *_sr("istasyon_emri__cikti_varyant"))
          .prefetch_related(is_oturumu._uyeler_prefetch()))
    kq = (OperasyonKaydi.objects.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI, tarih__gte=baslangic, tarih__lte=bitis, is_oturumu__isnull=True,
                                        fason_cari__isnull=True, fason_donus__isnull=True)
          .select_related("operasyon__istasyon", "operasyon__cikti", "cikti_varyant__stok", "uretim_emri", "istasyon_emri", "personel", *_sr("cikti_varyant"))
          .prefetch_related(Prefetch("ciktilar", queryset=OperasyonKaydiCikti.objects.filter(silindi=False, ana_mi=True))))
    if personel_id:
        oq = oq.filter(uyeler__personel_id=personel_id, uyeler__silindi=False).distinct()
        kq = kq.filter(personel_id=personel_id)
    if istasyon_id:
        oq = oq.filter(istasyon_id=istasyon_id)
        kq = kq.filter(operasyon__istasyon_id=istasyon_id)
    satirlar = []
    if kaynak in ("", "oturum"):
        satirlar += [_oturum_satiri(o, simdi) for o in oq]
    if kaynak in ("", "ofis", "emirsiz"):
        for k in kq:
            s = _kayit_satiri(k)
            if kaynak == "" or s["kaynak"] == kaynak:
                satirlar.append(s)
    satirlar.sort(key=lambda s: (s["tarih"], s["zaman"]), reverse=True)
    return satirlar


# --- Özetler ---------------------------------------------------------------------------------------------------------------------------------

def _mesai_saniyeleri(baslangic, bitis, simdi) -> dict:
    """{personel pk: aralıktaki toplam mesai saniyesi} (açık mesai şimdiye kadar)."""
    sonuc = {}
    for m in MesaiKaydi.objects.filter(silindi=False, is_tarihi__gte=baslangic, is_tarihi__lte=bitis).select_related("personel"):
        sonuc.setdefault(m.personel, 0)
        sonuc[m.personel] += _sure_sn(m.giris_zamani, m.cikis_zamani, simdi)
    return sonuc


def kisi_ozeti(satirlar, *, baslangic, bitis, simdi=None) -> list:
    """Personel bazında: katıldığı sağlam/fire (tam), pay sağlam (adam-saniye oranı), fire %, iş süresi (oturum katılımı), mesai süresi, verimlilik,
    süresiz kayıt sayısı. Mesaisi olup işi olmayanlar da listelenir (verimlilik 0). Verimlilik = iş süresi ÷ mesai süresi."""
    simdi = simdi or timezone.now()
    mesai = _mesai_saniyeleri(baslangic, bitis, simdi)
    kisi = {}

    def kayit(p):
        return kisi.setdefault(p.pk, {"personel": p, "saglam": SIFIR, "fire": SIFIR, "pay": SIFIR, "is_saniye": 0, "mesai_saniye": 0, "oturum": 0, "suresiz": 0})
    for s in satirlar:
        for pay in s["paylar"]:
            k = kayit(pay["personel"])
            k["saglam"] += s["saglam"]
            k["fire"] += s["fire"]
            k["pay"] += (s["saglam"] * pay["oran"]).quantize(Decimal("0.001"))
            k["is_saniye"] += pay["saniye"]
            if s["tur"] == "oturum":
                k["oturum"] += 0 if s["iptal"] else 1
            else:
                k["suresiz"] += 1
    for p, sn in mesai.items():
        kayit(p)["mesai_saniye"] = sn
    sonuc = []
    for k in kisi.values():
        k["fire_yuzde"] = _yuzde(k["fire"], k["saglam"])
        k["is_metni"] = is_oturumu.sure_metni(k["is_saniye"]) if k["is_saniye"] else "—"
        k["mesai_metni"] = is_oturumu.sure_metni(k["mesai_saniye"]) if k["mesai_saniye"] else "—"
        k["verimlilik"] = (Decimal(k["is_saniye"]) * 100 / Decimal(k["mesai_saniye"])).quantize(Decimal("0.1")) if k["mesai_saniye"] else None
        sonuc.append(k)
    sonuc.sort(key=lambda k: (k["personel"].ad, k["personel"].soyad))
    return sonuc


def istasyon_ozeti(satirlar) -> list:
    """İstasyon bazında: sağlam, fire, fire %, oturum süresi, adam-saat, adet/saat (sağlam ÷ oturum saati), süresiz kayıt sayısı."""
    ist = {}
    for s in satirlar:
        i = s["istasyon"]
        k = ist.setdefault(i.pk, {"istasyon": i, "saglam": SIFIR, "fire": SIFIR, "sure_saniye": 0, "adam_saniye": 0, "oturum": 0, "suresiz": 0, "oturum_saglam": SIFIR})
        k["saglam"] += s["saglam"]
        k["fire"] += s["fire"]
        if s["tur"] == "oturum":
            k["oturum"] += 0 if s["iptal"] else 1
            k["sure_saniye"] += s["sure_saniye"] or 0
            k["adam_saniye"] += s["adam_saniye"]
            k["oturum_saglam"] += s["saglam"]
        else:
            k["suresiz"] += 1
    sonuc = []
    for k in ist.values():
        k["fire_yuzde"] = _yuzde(k["fire"], k["saglam"])
        k["sure_metni"] = is_oturumu.sure_metni(k["sure_saniye"]) if k["sure_saniye"] else "—"
        k["adam_saat"] = (Decimal(k["adam_saniye"]) / 3600).quantize(Decimal("0.01"))
        k["adet_saat"] = (k["oturum_saglam"] / Decimal(k["sure_saniye"]) * 3600).quantize(Decimal("0.1")) if k["sure_saniye"] else None
        sonuc.append(k)
    sonuc.sort(key=lambda k: (k["istasyon"].sira, k["istasyon"].kod))
    return sonuc


def toplamlar(satirlar) -> dict:
    return {"satir": len(satirlar), "saglam": sum((s["saglam"] for s in satirlar), SIFIR), "fire": sum((s["fire"] for s in satirlar), SIFIR),
            "suresiz": sum(1 for s in satirlar if s["sure_saniye"] is None and not s["iptal"]), "oturum": sum(1 for s in satirlar if s["tur"] == "oturum")}
