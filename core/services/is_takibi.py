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

from core.models import EkranYetki, IsOturumu, IsOturumuUyesi, MesaiKaydi, OperasyonKaydi, OperasyonKaydiCikti, Personel
from core.services import is_oturumu
from core.services.varyant import varyant_kisa
from core.tarih import _TR, tr_bugun

SIFIR = Decimal("0")
KAYNAKLAR = (("oturum", "Atölye oturumu"), ("ofis", "Ofis kaydı (istasyon emirli, oturumsuz)"), ("emirsiz", "Emirsiz"))
URETIM_DEPARTMANI = "ÜRETİM"
ISTASYON_RENKLERI = ("#2563eb", "#0b6b3f", "#b35a00", "#7c3aed", "#0e7490", "#b3253f", "#4d7c0f", "#9d174d", "#1d4ed8", "#92400e", "#334155")
CIZELGE_AZAMI_GUN = 31
VARYANT_SR = ("markalama", "kilavuz", "afis", "yan_etiket")


def _sr(onek):
    return [f"{onek}__{a}" for a in VARYANT_SR]


def _etiket(sv):
    return varyant_kisa(sv) if sv is not None else ""


def _cikti_varyant(o):
    """Oturumun işi: (stok kartı, varyant etiketi) — emirli oturumda çıktı kartı, karma kesimde profil kartı (varyantsız)."""
    ie = o.istasyon_emri
    return (o.karma_profil, "") if ie is None else (ie.operasyon.cikti, _etiket(ie.cikti_varyant))


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
                 .select_related("istasyon_emri__operasyon__cikti", "istasyon_emri__cikti_varyant__stok", "uretim_emri", "istasyon", "baslatan", "sorumlu", "karma_profil",
                                 *_sr("istasyon_emri__cikti_varyant"))
                 .prefetch_related(is_oturumu._uyeler_prefetch()).order_by("baslangic_zamani", "pk"))
    satirlar, mesgul = [], set()
    for o in oturumlar:
        oz = is_oturumu.ozet(o)
        mesgul.update(u.personel_id for u in oz["uyeler"] if u.ayrilma_zamani is None)
        cikti, varyant = _cikti_varyant(o)
        satirlar.append({"oturum": o, "ozet": oz, "ie": o.istasyon_emri, "cikti": cikti, "varyant": varyant, "dunku": o.is_tarihi < bugun})
    adet = [{"oturum": o, "ozet": is_oturumu.ozet(o), "ie": o.istasyon_emri, "cikti": _cikti_varyant(o)[0], "varyant": _cikti_varyant(o)[1]}
            for o in (IsOturumu.objects.filter(silindi=False, durum=IsOturumu.Durum.ADET)
                      .select_related("istasyon_emri__operasyon__cikti", "istasyon_emri__cikti_varyant__stok", "uretim_emri", "istasyon", "baslatan", "sorumlu", "karma_profil",
                                      *_sr("istasyon_emri__cikti_varyant"))
                      .prefetch_related(is_oturumu._uyeler_prefetch()).order_by("bitis_zamani", "pk"))]
    mesailer = list(MesaiKaydi.objects.filter(silindi=False, cikis_zamani__isnull=True).select_related("personel").order_by("personel__ad", "personel__soyad"))
    bosta = [{"personel": m.personel, "mesai": m, "sure_saniye": _sure_sn(m.giris_zamani, None, simdi), "sure_metni": is_oturumu.sure_metni(_sure_sn(m.giris_zamani, None, simdi)),
              "dunku": m.is_tarihi < bugun} for m in mesailer if m.personel_id not in mesgul]
    return {"oturumlar": satirlar, "bosta": bosta, "mesaide": len(mesailer), "adet": adet}


# --- Geçmiş ----------------------------------------------------------------------------------------------------------------------------------

def _oturum_satiri(o, simdi):
    oz = is_oturumu.ozet(o)
    ie = o.istasyon_emri
    cikti, varyant = _cikti_varyant(o)
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
        paylar.append({"personel": u.personel, "saniye": 0 if iptal else sn, "oran": oran,             # iptal (iş yapılmadı): süre sayılmaz
                       "bas": u.katilim_zamani, "bit": u.ayrilma_zamani or o.bitis_zamani})
    return {"tur": "oturum", "tarih": o.is_tarihi, "zaman": o.baslangic_zamani, "oturum": o, "kayit": kayit, "ozet": oz, "istasyon": o.istasyon,
            "cikti": cikti, "varyant": varyant, "personeller": [u.personel for u in uyeler], "paylar": paylar,
            "personel_metni": oz["ekip_metni"] or "—", "saglam": saglam, "fire": fire, "fire_yuzde": _yuzde(fire, saglam),
            "sure_saniye": None if iptal else oz["sure_saniye"], "sure_metni": "—" if iptal else oz["sure_metni"], "adam_saniye": 0 if iptal else oz["adam_saniye"],
            "kaynak": "oturum", "kaynak_metni": f"{o.uretim_emri.no} / {ie.no}" if ie is not None else (f"Karma kesim {o.karma_kesim.no}" if o.karma_kesim_id else "Karma kesim"),
            "us_pk": o.uretim_emri_id, "ie_pk": ie.pk if ie is not None else None,
            "durum": o.get_durum_display(), "acik": o.durum == IsOturumu.Durum.ACIK, "iptal": iptal, "geri_alindi": geri_alindi,
            "href": f"/uretim/kayitlar/{kayit.pk}/" if kayit is not None else f"/uretim/is-oturumlari/?ara={ie.no if ie is not None else (o.karma_kesim.no if o.karma_kesim_id else o.karma_profil.kod)}"}


def _kayit_satiri(k):
    ana = next((c for c in k.ciktilar.all() if c.ana_mi and not c.silindi), None)
    saglam = ana.miktar if ana is not None else k.hedef_cikti_miktari
    beklenen = (ana.beklenen_miktar if ana is not None and ana.beklenen_miktar is not None else k.hedef_cikti_miktari)
    fire = max(beklenen - saglam, SIFIR)
    emirsiz = k.istasyon_emri_id is None and k.uretim_emri_id is None
    return {"tur": "kayit", "tarih": k.tarih, "zaman": k.created_at, "oturum": None, "kayit": k, "ozet": None, "istasyon": k.operasyon.istasyon,
            "cikti": k.operasyon.cikti, "varyant": _etiket(k.cikti_varyant), "personeller": [k.personel] if k.personel_id else [],
            "paylar": [{"personel": k.personel, "saniye": 0, "oran": Decimal(1), "bas": None, "bit": None}] if k.personel_id else [],
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
          .select_related("istasyon_emri__operasyon__cikti", "istasyon_emri__cikti_varyant__stok", "uretim_emri", "istasyon", "baslatan", "sorumlu", "bitiren", "kayit",
                          "karma_profil", "karma_kesim", *_sr("istasyon_emri__cikti_varyant"))
          .prefetch_related(is_oturumu._uyeler_prefetch()))
    kq = (OperasyonKaydi.objects.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI, tarih__gte=baslangic, tarih__lte=bitis, is_oturumu__isnull=True,
                                        fason_cari__isnull=True, fason_donus__isnull=True)
          .exclude(karma__is_oturumu__isnull=False)                                    # atölye karma kesim oturumunun kayıtları oturum satırıyla birlikte gösterilir
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

def mesai_araliklari(baslangic, bitis, simdi=None) -> dict:
    """{Personel: [(giriş, çıkış|şimdi, iş tarihi, açık mı)]} — tek sorgu; kişi özeti ve zaman çizelgesi aynı veriyi kullanır."""
    simdi = simdi or timezone.now()
    sonuc = {}
    for m in MesaiKaydi.objects.filter(silindi=False, is_tarihi__gte=baslangic, is_tarihi__lte=bitis).select_related("personel").order_by("giris_zamani"):
        sonuc.setdefault(m.personel, []).append((m.giris_zamani, m.cikis_zamani or simdi, m.is_tarihi, m.cikis_zamani is None))
    return sonuc


def _mesai_saniyeleri(baslangic, bitis, simdi, mesai=None) -> dict:
    """{personel: aralıktaki toplam mesai saniyesi} (açık mesai şimdiye kadar)."""
    mesai = mesai if mesai is not None else mesai_araliklari(baslangic, bitis, simdi)
    return {p: sum(_sure_sn(g, c, simdi) for g, c, _t, _a in ar) for p, ar in mesai.items()}


def kisi_ozeti(satirlar, *, baslangic, bitis, simdi=None, mesai=None) -> list:
    """Personel bazında: katıldığı sağlam/fire (tam), pay sağlam (adam-saniye oranı), fire %, iş süresi (oturum katılımı), mesai süresi, verimlilik,
    süresiz kayıt sayısı. Mesaisi olup işi olmayanlar da listelenir (verimlilik 0). Verimlilik = iş süresi ÷ mesai süresi."""
    simdi = simdi or timezone.now()
    mesai = _mesai_saniyeleri(baslangic, bitis, simdi, mesai)
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


# --- Şu an: personel kartları ---------------------------------------------------------------------------------------------------------------

def atolye_personeli():
    """Gri ("mesaide değil") kartlarda gösterilecek aktif personel: atölye hesabı olan ya da ÜRETİM departmanındaki çalışanlar (tek sorgu).
    Ofis personeli yalnız mesaideyse (sarı/yeşil) görünür."""
    from core.services import personel as personel_servis
    atolye = EkranYetki.objects.filter(ekran_kod="atolye", silindi=False).values("kullanici_id")
    return list(personel_servis.calisan_personeller().filter(Q(departman=URETIM_DEPARTMANI) | Q(kullanici_id__in=atolye)).order_by("ad", "soyad"))


def su_an_kartlar(simdi=None) -> dict:
    """Kişi başına kart: {"durum": calisiyor|bosta|yok, "personel", "oturum", "ie", "cikti", "varyant", "ekip" [diğer üyeler], "baslangic_ms", "dunku"}.
    Sıra: çalışanlar → boşta → mesaide değil; grup içinde ada göre. Sayaçlar ayrı. Toplam 4 sorgu (su_an 3 + personel listesi)."""
    simdi = simdi or timezone.now()
    d = su_an(simdi)
    kartlar, gorulen = [], set()
    for s in d["oturumlar"]:
        o = s["oturum"]
        uyeler = [u for u in s["ozet"]["uyeler"] if u.ayrilma_zamani is None]
        for u in uyeler:
            gorulen.add(u.personel_id)
            sorumlu = s["ozet"]["sorumlu"]
            kartlar.append({"durum": "calisiyor", "personel": u.personel, "oturum": o, "ie": s["ie"], "cikti": s["cikti"], "varyant": s["varyant"], "istasyon": o.istasyon,
                            "sorumlu": sorumlu, "sorumlu_mu": u.personel_id == sorumlu.pk, "devir_adaylari": [x.personel for x in uyeler if x.personel_id != sorumlu.pk],
                            "ekip": [x.personel for x in uyeler if x.personel_id != u.personel_id], "baslangic_ms": int(u.katilim_zamani.timestamp() * 1000),
                            "sure_metni": is_oturumu.sure_metni(u.katilim_saniye()), "dunku": s["dunku"]})
    for b in d["bosta"]:
        gorulen.add(b["personel"].pk)
        kartlar.append({"durum": "bosta", "personel": b["personel"], "oturum": None, "ie": None, "cikti": None, "varyant": "", "istasyon": None, "ekip": [],
                        "baslangic_ms": int(b["mesai"].giris_zamani.timestamp() * 1000), "sure_metni": b["sure_metni"], "dunku": b["dunku"]})
    for p in atolye_personeli():
        if p.pk not in gorulen:
            kartlar.append({"durum": "yok", "personel": p, "oturum": None, "ie": None, "cikti": None, "varyant": "", "istasyon": None, "ekip": [],
                            "baslangic_ms": None, "sure_metni": "", "dunku": False})
    sira = {"calisiyor": 0, "bosta": 1, "yok": 2}
    kartlar.sort(key=lambda k: (sira[k["durum"]], k["personel"].ad, k["personel"].soyad))
    sayac = {d_: sum(1 for k in kartlar if k["durum"] == d_) for d_ in sira}
    adet = []
    for a in d["adet"]:                                                 # "Adet bekliyor" işler: yöneticinin adet girmesi / sorumlu değiştirmesi için
        gecmis_uyeler = {x.personel_id: x.personel for x in a["ozet"]["uyeler"]}
        adet.append({**a, "sorumlu": a["ozet"]["sorumlu"], "uye_adaylari": [p for pk, p in gecmis_uyeler.items() if pk != a["ozet"]["sorumlu"].pk]})
    return {"kartlar": kartlar, "sayac": sayac, "mesaide": d["mesaide"], "adet": adet}


# --- Geçmiş: zaman çizelgesi (sorgu yok; gecmis + mesai_araliklari verisinden) ---------------------------------------------------------------

def istasyon_renkleri(satirlar) -> dict:
    """{istasyon pk: renk} — satırlardaki istasyonlar sıra/koda göre sabit paletten."""
    ist = {s["istasyon"].pk: s["istasyon"] for s in satirlar}
    sirali = sorted(ist.values(), key=lambda i: (i.sira, i.kod))
    return {i.pk: ISTASYON_RENKLERI[n % len(ISTASYON_RENKLERI)] for n, i in enumerate(sirali)}


def _yuzde_konum(bas, bit, eksen_bas, eksen_bit):
    toplam = (eksen_bit - eksen_bas).total_seconds() or 1
    sol = max((bas - eksen_bas).total_seconds(), 0) / toplam * 100
    sag = min((bit - eksen_bas).total_seconds(), toplam) / toplam * 100
    return f"{sol:.2f}", f"{max(sag - sol, 0.3):.2f}"          # CSS için NOKTALI metin (şablon yerelleştirmesi sayıyı virgülle basardı)


def zaman_cizelgesi(satirlar, mesai, *, baslangic, bitis, simdi=None) -> dict:
    """{personel pk: {"personel", "gunler": [{"tarih", "saat_etiketleri", "mesai": [{sol, en, acik}], "bloklar": [{sol, en, renk, ipucu, href, iptal}]}],
    "suresiz": [kayıt satırları], "cok_uzun": bool}}. Gün ekseni: o günkü mesai ve oturumların en erken/en geç saati (tam saate yuvarlı). Aralık
    CIZELGE_AZAMI_GUN'den uzunsa günler boş, ``cok_uzun`` True."""
    simdi = simdi or timezone.now()
    renk = istasyon_renkleri(satirlar)
    kisiler = {}

    def kisi(p):
        return kisiler.setdefault(p.pk, {"personel": p, "gunler": {}, "suresiz": [], "cok_uzun": (bitis - baslangic).days + 1 > CIZELGE_AZAMI_GUN})
    for p, aralik in mesai.items():
        for g, c, t, acik in aralik:
            kisi(p)["gunler"].setdefault(t, {"mesai": [], "oturum": []})["mesai"].append((g, c, acik))
    for s in satirlar:
        if s["tur"] != "oturum":
            for pay in s["paylar"]:
                kisi(pay["personel"])["suresiz"].append(s)
            continue
        for pay in s["paylar"]:
            if pay["bas"] is None:
                continue
            kisi(pay["personel"])["gunler"].setdefault(s["tarih"], {"mesai": [], "oturum": []})["oturum"].append((pay["bas"], pay["bit"] or simdi, s))
    for k in kisiler.values():
        gunler = []
        if not k["cok_uzun"]:
            for t in sorted(k["gunler"], reverse=True):
                g = k["gunler"][t]
                zamanlar = [z for m in g["mesai"] for z in m[:2]] + [z for o in g["oturum"] for z in o[:2]]
                if not zamanlar:
                    continue
                en_erken = min(zamanlar).astimezone(_TR).replace(minute=0, second=0, microsecond=0)
                en_gec = max(zamanlar).astimezone(_TR)
                en_gec = (en_gec.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)) if en_gec.minute or en_gec.second else en_gec
                saat = int((en_gec - en_erken).total_seconds() // 3600) or 1
                adim = 1 if saat <= 8 else (2 if saat <= 16 else 4)
                etiketler = [{"sol": f"{i / saat * 100:.2f}", "metin": f"{(en_erken + datetime.timedelta(hours=i)).hour:02d}"} for i in range(0, saat + 1, adim)]
                mesai_b = [dict(zip(("sol", "en"), _yuzde_konum(m[0], m[1], en_erken, en_gec)), acik=m[2]) for m in g["mesai"]]
                bloklar = []
                for b, c, s in sorted(g["oturum"], key=lambda x: x[0]):
                    sol, en = _yuzde_konum(b, c, en_erken, en_gec)
                    ipucu = (f"{s['istasyon'].kod} {s['istasyon'].ad} · {s['cikti'].kod}" + (f" [{s['varyant']}]" if s["varyant"] else "")
                             + f" · {b.astimezone(_TR):%H:%M}–{c.astimezone(_TR):%H:%M}"
                             + (" · iptal (iş yapılmadı)" if s["iptal"] else f" · sağlam {s['saglam'].normalize():f}" + (f" / fire {s['fire'].normalize():f}" if s["fire"] else ""))
                             + (" · kayıt geri alındı" if s["geri_alindi"] else ""))
                    bloklar.append({"sol": sol, "en": en, "renk": renk.get(s["istasyon"].pk, ISTASYON_RENKLERI[-1]), "ipucu": ipucu, "href": s["href"], "iptal": s["iptal"],
                                    "acik": s["acik"], "kod": s["istasyon"].kod})
                gunler.append({"tarih": t, "saat_etiketleri": etiketler, "mesai": mesai_b, "bloklar": bloklar})
        k["gunler"] = gunler
    return kisiler
