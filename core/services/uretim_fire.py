"""FİRE SONRASI YENİDEN PLANLAMA (2026-10-22).

Atölye "Bitir" ile fire girilince girdiler SAĞLAM + FİRE kadar tüketilir ama istasyon emrinin tamamlananı yalnız sağlam kadar artar; ÜS'nin girdi ayırması ise
tüketilen kadar düşer. Plan/ayırma yalnız ÜS açılış ve revizesinde hesaplandığından kalan üretim için ayrılmış girdi eksik kalıyordu (ör. büküm kalanı 4,
ayrılmış kesilmiş 3). Bu modül fire'lı ONAYLI kayıttan sonra EKLEMELİ bir ek plan yapar (global yeniden planlama değil — fire dışı sapmalara dokunmaz):

1) Ek ihtiyaç = fire × girdi / hedef (kaydın girdi satırlarından), ama YALNIZ ÜS'nin açık kalan kısmı kadar:
   ``açık = min(ek, max(0, tüketen emirlerin kalan ihtiyacı − ÜS ayırması − bekleyen üretim))`` (tamamlanmış emirde ek yoktur).
2) Önce aynı stok/varyantın SERBEST stoğundan (eldeki − tüm açık ayırmalar) ÜS'ye ayrılır (+ açık fason siparişinin boş kalanı: ÜS rezervi).
3) Yetmeyen kısım için girdiyi ÜRETEN istasyon emrinin planlananı (ve ihtiyacı) artırılır, bitmişse yeniden açılır; bu emrin girdileri için aynı kural planlayıcı
   (``uretim._plani_hesapla``, ``haric_emir=None``: serbest = tüm ayırmalar düşülmüş) ile ZİNCİRLEME uygulanır; emir yoksa açılır. Yaprak/stoklu ara mamul yalnız
   serbest stoktan ayrılır (eksik kalan mevcut "eksik malzeme" tablosunda görünür).
4) Fason istasyon emri artarsa ``IstasyonEmri.fire_ek`` işaretlenir → ÜS detayında "fason emri gerekli (fire kaynaklı)" rozeti (``fason_rozetleri``).
5) Yapılan her şey ``OperasyonKaydi.fire_ek``'e yazılır (geri alma için) + ÜS geçmişine FIRE_EK_PLAN revizyonu; kayıt geri alınırsa yalnız bu ek geri sarılır
   (``fire_ek_geri_al``; ek üretilmişse planlanan tamamlananın altına inmez). ÜS revizesi planı baştan kurduğu için izleri "yenilendi" işaretler.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from core.models import IstasyonEmri, Operasyon, OperasyonKaydi, Stok, UretimEmri, UretimEmriRevizyon
from core.services import fason_siparis as fs_servis, planlama_varyant, stok_ayirma, uretim as u
from core.services.stok_ayirma import AyirmaHatasi
from core.services.varyant import VaryantHatasi

log = logging.getLogger(__name__)
S = Decimal("0")
_ds = u._ds


def _d(x) -> Decimal:
    return Decimal(str(x)) if x not in (None, "") else S


def kayit_fire(kayit: OperasyonKaydi) -> Decimal:
    """Onaylı kaydın fire adedi: ana çıktının beklenen − gelen (sağlam) miktarı; yoksa 0."""
    ana = kayit.ciktilar.filter(ana_mi=True, silindi=False).first()
    if ana is None or ana.beklenen_miktar is None:
        return S
    return max(S, ana.beklenen_miktar - ana.miktar)


def uygulanabilir(kayit: OperasyonKaydi) -> bool:
    """ÜS + istasyon emrine bağlı, fasonsuz ÜRET kaydı (ÜS'ye bağlı olmayan fason dönüş fireleri kapsam dışı)."""
    return bool(kayit.istasyon_emri_id and kayit.uretim_emri_id and not kayit.fason_cari_id and kayit.operasyon.tur == Operasyon.Tur.URET)


def _girdi_listesi(ie: IstasyonEmri, cz, varyantli: bool) -> list:
    """Emrin bir çalıştırmada tükettiği girdiler: [(stok pk, varyant pk|None, çalıştırma başına miktar)] (varyantlı planda tanım kuralıyla çözülmüş düğümler)."""
    if varyantli:
        return [(g.girdi.stok.pk, g.girdi.varyant.pk if g.girdi.varyant is not None else None, g.miktar)
                for g in cz.girdi_dugumleri(ie.operasyon, ie.cikti_varyant)]
    return [(g.girdi_id, None, g.miktar) for g in u.operasyon_girdileri(ie.operasyon)]


def _cikti_miktari(op: Operasyon, stok_id) -> Decimal:
    for c in u.tanim_ciktilari(op):
        if c.stok_id == stok_id:
            return c.miktar
    return u._referans_miktar(op)


def _etiket(stok_id, vpk) -> str:
    return planlama_varyant.etiket(vpk) if vpk else ""


def _dugum_eslestir(tuketim: dict, stok_id, vpk):
    """Kaydın girdi satırı varyantı boşsa (varyantsız çıktılı emir) tüketen emirlerdeki tek varyantlı düğümle eşleştirir."""
    if vpk is not None or (stok_id, None) in tuketim:
        return vpk
    adaylar = [k[1] for k in tuketim if k[0] == stok_id and k[1] is not None]
    return adaylar[0] if len(adaylar) == 1 else vpk


def _fs_rezervleri_ekle(emir, ekler: dict, kullanici):
    """ÜS'nin FS rezervine ``ekler`` ({(stok pk, varyant pk|None): miktar}, negatif = düş) ekler; mevcut dağılım korunarak toplam düğüm hedefi eşitlenir."""
    toplam = {}
    for r in fs_servis.emir_rezervleri(emir):
        k = (r.satir.stok_id, r.satir.varyant_id)
        toplam[k] = toplam.get(k, S) + r.miktar
    hedef = dict(toplam)
    for (stok_pk, vpk), m in ekler.items():
        k = (stok_pk, vpk)
        if vpk is None:
            esl = [x for x in hedef if x[0] == stok_pk]
            if esl:
                k = esl[0]
        hedef[k] = max(S, hedef.get(k, S) + m)
    fs_servis.rezervleri_esitle(emir, hedef, kullanici)


def _hesapla_ve_uygula(kayit: OperasyonKaydi, kullanici, geriye_donuk: bool):
    emir = UretimEmri.objects.select_for_update(of=("self",)).get(pk=kayit.uretim_emri_id)
    if emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        return None
    fire = kayit_fire(kayit)
    if fire <= 0:
        return None
    u._planlama_kilidi()
    varyantli = emir.varyantli_plan
    cz = planlama_varyant.Cozucu(kullanici) if varyantli else None
    ies = list(IstasyonEmri.objects.select_for_update(of=("self",)).filter(uretim_emri=emir, silindi=False)
               .select_related("operasyon__cikti", "istasyon", "cikti_varyant").order_by("seviye", "pk"))
    aktif = [ie for ie in ies if ie.durum != IstasyonEmri.Durum.IPTAL]
    haric = {ie.operasyon.cikti_id for ie in aktif}

    tuketim = {}                                                # (stok, varyant) → tüketen emirlerin KALAN ihtiyacı
    for ie in aktif:
        for stok_id, vpk, miktar in _girdi_listesi(ie, cz, varyantli):
            tuketim[(stok_id, vpk)] = tuketim.get((stok_id, vpk), S) + ie.kalan * miktar

    ek_girdi = {}                                               # fire yüzünden fazla tüketilen (stok, varyant) → ek ihtiyaç
    for g in u.kaydi_girdi_satirlari(kayit):
        if g.gerceklesen_miktar <= 0:
            continue
        vpk = _dugum_eslestir(tuketim, g.girdi_id, g.varyant_id if varyantli else None)
        ek_girdi[(g.girdi_id, vpk)] = ek_girdi.get((g.girdi_id, vpk), S) + g.gerceklesen_miktar * fire / kayit.hedef_cikti_miktari

    acik_dugum = {}                                             # (stok, varyant) → karşılanması gereken açık miktar
    for (stok_id, vpk), e in ek_girdi.items():
        key = planlama_varyant.ihtiyac_anahtari(stok_id, vpk)
        uretici = next((ie for ie in aktif if key in (ie.ihtiyac or {})), None)
        bekleyen = fazla = S
        if uretici is not None:
            kalan_ihtiyac = max(S, _d(uretici.ihtiyac.get(key)) - u._onceki_ayrilan(uretici, stok_id, vpk))
            fiziksel = uretici.kalan * _cikti_miktari(uretici.operasyon, stok_id)          # emrin kalan koşumlarının üreteceği
            bekleyen = min(kalan_ihtiyac, fiziksel)
            fazla = max(S, fiziksel - kalan_ihtiyac)                                         # tam boy yuvarlamasından kalan plan fazlası: ihtiyaç artırılarak kullanılabilir
        ust = stok_ayirma.ayrilan_miktar(emir, stok_id, varyant=vpk)
        acik = min(e, max(S, tuketim.get((stok_id, vpk), S) - ust - bekleyen))
        if acik > 0:
            acik_dugum[(stok_id, vpk)] = (acik, uretici, fazla, key)
    if not acik_dugum:
        return None

    ayirma_log, fs_ekler, yaprak_eksik = {}, {}, []

    def ayirma_yaz(stok_pk, vpk, miktar):
        if miktar <= 0:
            return
        stok_ayirma.ayirma_ekle(emir, stok_pk, miktar, kullanici, varyant=vpk)
        ayirma_log[(stok_pk, vpk)] = ayirma_log.get((stok_pk, vpk), S) + miktar

    # yaprak / stoklu ara mamul: yalnız serbest stoktan (ve FS kalanından) ayrılır
    planlanacak = []
    fs_harita = None
    ihtiyac_fazla = {}                                          # IstasyonEmri → {ihtiyac anahtarı: mevcut plan fazlasından kullanılan}
    for (stok_id, vpk), (acik, uretici, fazla, key) in sorted(acik_dugum.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
        stok = Stok.objects.get(pk=stok_id)
        if varyantli:
            op_var = cz.op_bul(cz.kalem_dugumu(stok, vpk))[0] is not None
        else:
            op_var = u.ureten_operasyon(stok) is not None
        if op_var and not (stok.stoklu_uretim and stok.pk not in haric):
            serbest = max(S, stok_ayirma.kullanilabilir_varyant_haritasi([(stok_id, vpk)])[(stok_id, vpk)])
            a = min(acik, serbest)                               # 1) serbest stok
            ayirma_yaz(stok_id, vpk, a)
            acik -= a
            kullan = min(acik, fazla) if uretici is not None else S
            if kullan > 0:                                       # 2) üreten emrin plan fazlası: yeni koşum açmadan ihtiyaç artar
                ihtiyac_fazla.setdefault(uretici, {})[key] = ihtiyac_fazla.get(uretici, {}).get(key, S) + kullan
                acik -= kullan
            if acik > 0:                                         # 3) emir artışı (zincirleme)
                planlanacak.append((stok, acik, vpk))
            continue
        serbest = max(S, stok_ayirma.kullanilabilir_varyant_haritasi([(stok_id, vpk)])[(stok_id, vpk)])
        a = min(acik, serbest)
        ayirma_yaz(stok_id, vpk, a)
        kalan = acik - a
        if kalan > 0:
            if fs_harita is None:
                fs_harita = fs_servis.acik_kalan_haritasi()
            f = min(kalan, fs_servis.kalan_icin(fs_harita, stok_id, vpk))
            if f > 0:
                fs_ekler[(stok_id, vpk)] = fs_ekler.get((stok_id, vpk), S) + f
            if kalan - f > 0:
                yaprak_eksik.append({"stok": stok.kod, "varyant": _etiket(stok_id, vpk), "eksik": _ds(kalan - f)})

    ie_log = []
    for ie, anahtarlar in ihtiyac_fazla.items():
        yeni_ihtiyac = dict(ie.ihtiyac or {})
        for key, m in anahtarlar.items():
            yeni_ihtiyac[key] = _ds(_d(yeni_ihtiyac.get(key)) + m)
        ie.ihtiyac = yeni_ihtiyac
        ie.updated_by = kullanici
        ie.save(update_fields=["ihtiyac", "updated_by", "updated_at"])
        ie_log.append({"ie_id": ie.pk, "no": ie.no, "operasyon": ie.operasyon.cikti.kod, "varyant": _etiket(None, ie.cikti_varyant_id), "once": _ds(ie.planlanan),
                       "sonra": _ds(ie.planlanan), "ek": "0", "ihtiyac_ek": {k: _ds(v) for k, v in anahtarlar.items()}, "yeni": False, "durum_once": ie.durum,
                       "durum_sonra": ie.durum, "fason": bool(ie.istasyon.fason), "sadece_ihtiyac": True})
    if planlanacak:
        sonuc, _cz = u._plani_hesapla(planlanacak, varyantli, kullanici, haric_emir=None, stoklu_haric=haric, cozucu=cz)
        stok_pay, fason_pay = u._ayrilani_bol(sonuc, varyantli)
        for k, a in stok_pay.items():
            stok_pk, vpk = k if varyantli else (k, None)
            ayirma_yaz(stok_pk, vpk, a)
        for k, a in fason_pay.items():
            fs_ekler[k] = fs_ekler.get(k, S) + a
        mevcut = {(ie.operasyon_id, ie.cikti_varyant_id): ie for ie in ies}
        for p in sonuc["plan"]:
            calistirma = p["calistirma"]
            if calistirma <= 0:
                continue
            op, sv = planlama_varyant.kosum_coz(p["operasyon"])
            ihtiyac_ek = {}
            for c in p["ciktilar"]:
                if c["surucu"] and c["net"] > 0:
                    st, sdv = planlama_varyant.dugum_coz(c["stok"])
                    ihtiyac_ek[planlama_varyant.ihtiyac_anahtari(st.pk, sdv.pk if sdv is not None else None)] = c["net"]
            ie = mevcut.get((op.pk, sv.pk if sv is not None else None))
            yeni = ie is None
            if yeni:
                ie = u._istasyon_emri_ac(emir, p, kullanici)
                once, durum_once = S, None
                ie = IstasyonEmri.objects.select_related("operasyon__cikti", "istasyon", "cikti_varyant").get(pk=ie.pk)
            else:
                once, durum_once = ie.planlanan, ie.durum
                ie.planlanan = ie.planlanan + calistirma
                yeni_ihtiyac = dict(ie.ihtiyac or {})
                for key, m in ihtiyac_ek.items():
                    yeni_ihtiyac[key] = _ds(_d(yeni_ihtiyac.get(key)) + m)
                ie.ihtiyac = yeni_ihtiyac
                if ie.durum == IstasyonEmri.Durum.IPTAL:
                    ie.durum = IstasyonEmri.Durum.BEKLIYOR
            ie.fire_ek = {"ek": _ds(_d((ie.fire_ek or {}).get("ek")) + calistirma)}
            ie.updated_by = kullanici
            ie.save()
            u.istasyon_emri_durum_guncelle(ie)
            ie.refresh_from_db()
            ie_log.append({"ie_id": ie.pk, "no": ie.no, "operasyon": ie.operasyon.cikti.kod, "varyant": _etiket(None, ie.cikti_varyant_id), "once": _ds(once),
                           "sonra": _ds(ie.planlanan), "ek": _ds(calistirma), "ihtiyac_ek": {k: _ds(v) for k, v in ihtiyac_ek.items()}, "yeni": yeni,
                           "durum_once": durum_once, "durum_sonra": ie.durum, "fason": bool(ie.istasyon.fason)})

    fs_log = []
    if fs_ekler:
        _fs_rezervleri_ekle(emir, fs_ekler, kullanici)
        for (stok_pk, vpk), m in sorted(fs_ekler.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
            fs_log.append({"stok_id": stok_pk, "varyant_id": vpk, "kod": Stok.objects.get(pk=stok_pk).kod, "varyant": _etiket(stok_pk, vpk), "miktar": _ds(m)})
    ayirma_kayit = [{"stok_id": s, "varyant_id": v, "kod": Stok.objects.get(pk=s).kod, "varyant": _etiket(s, v), "miktar": _ds(m)}
                    for (s, v), m in sorted(ayirma_log.items(), key=lambda x: (x[0][0], x[0][1] or 0))]
    if not (ayirma_kayit or fs_log or ie_log):
        return {"kayit": kayit.no, "ie": kayit.istasyon_emri.no, "fire": _ds(fire), "ayirma": [], "fason_siparis": [], "istasyon_emirleri": [],
                "yaprak_eksik": yaprak_eksik, "geriye_donuk": geriye_donuk, "uygulanmadi": True}
    ek = {"kayit": kayit.no, "ie": kayit.istasyon_emri.no, "fire": _ds(fire), "ayirma": ayirma_kayit, "fason_siparis": fs_log, "istasyon_emirleri": ie_log,
          "yaprak_eksik": yaprak_eksik, "geriye_donuk": geriye_donuk}
    OperasyonKaydi.objects.filter(pk=kayit.pk).update(fire_ek=ek)
    kayit.fire_ek = ek
    ozet = ", ".join(
        [f"{a['kod']}{' [' + a['varyant'] + ']' if a['varyant'] else ''} +{a['miktar']} ayrıldı" for a in ayirma_kayit]
        + [f"{x['no']} ihtiyaç +{_ds(sum(_d(v) for v in x['ihtiyac_ek'].values()))} (plan fazlası)" if x.get("sadece_ihtiyac") else f"{x['no']} +{x['ek']} planlandı"
           for x in ie_log]) or "ek yok"
    u._revizyon_yaz(emir, UretimEmriRevizyon.Tur.FIRE_EK_PLAN, None,
                    f"{kayit.no} fire {_ds(fire)}{' (geriye dönük)' if geriye_donuk else ''}: {ozet}", ek, kullanici)
    return ek


def fire_ek_planla(kayit: OperasyonKaydi, kullanici=None, *, geriye_donuk=False, hata_yut=True):
    """Fire'lı ONAYLI kayıt için ek planı yapar (modül açıklaması). Fire/ek yoksa ya da ÜS açık değilse None; yapılanların özetini (kayıtta saklanan ``fire_ek``) döner.
    Zaten uygulanmışsa (``kayit.fire_ek`` dolu) tekrarlamaz. ``hata_yut=True`` (onay akışı): planlama hatası üretim kaydını ENGELLEMEZ — değişiklikler savepoint'te geri
    alınır, ÜS geçmişine hata satırı yazılır, ``{"hata": ...}`` döner; ``False`` (komut/test) hata fırlatır."""
    if not uygulanabilir(kayit) or kayit.fire_ek:
        return None
    try:
        with transaction.atomic():
            return _hesapla_ve_uygula(kayit, kullanici, geriye_donuk)
    except (u.UretimHatasi, AyirmaHatasi, VaryantHatasi) as e:
        if not hata_yut:
            raise
        log.warning("Fire ek planı yapılamadı (%s): %s", kayit.no, e)
        emir = kayit.uretim_emri
        u._revizyon_yaz(emir, UretimEmriRevizyon.Tur.FIRE_EK_PLAN, None, f"{kayit.no} fire için otomatik ek plan yapılamadı: {e}"[:300],
                        {"kayit": kayit.no, "hata": str(e)}, kullanici)
        return {"hata": str(e)}


@transaction.atomic
def fire_ek_geri_al(kayit: OperasyonKaydi, kullanici=None, *, yeniden=False):
    """Kayıt geri alınırken (``operasyon_kaydi_sil(onayli_geri_al=True)``) fire ek planının YALNIZ kendi eklediğini geri sarar: ayırma ve FS rezervi düşer, istasyon emri
    planlananı ek kadar iner (tamamlananın altına inmez: ek üretildiyse o kısım kalır; bu kayıtla açılmış ve hiç üretilmemiş emir İPTAL olur). ÜS açık değilse ya da ek
    revizeyle yenilenmişse dokunmaz. Yapılan işin özetini döner (yoksa None). ``yeniden=True`` (düzeltme komutu): kayıt 'geri alındı' işaretlenmez, ``fire_ek`` temizlenir
    ve ek yeniden hesaplanabilir."""
    kayit.refresh_from_db(fields=["fire_ek"])
    ek = kayit.fire_ek or {}
    if not ek or ek.get("geri_alindi") or ek.get("yenilendi") or "hata" in ek or ek.get("uygulanmadi"):
        return None
    emir = UretimEmri.objects.select_for_update(of=("self",)).get(pk=kayit.uretim_emri_id)
    if emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        return None
    u._planlama_kilidi()
    for a in ek.get("ayirma", []):
        stok_ayirma.ayirma_ekle(emir, a["stok_id"], -_d(a["miktar"]), kullanici, varyant=a["varyant_id"])
    if ek.get("fason_siparis"):
        _fs_rezervleri_ekle(emir, {(f["stok_id"], f["varyant_id"]): -_d(f["miktar"]) for f in ek["fason_siparis"]}, kullanici)
    geri = []
    for x in ek.get("istasyon_emirleri", []):
        ie = IstasyonEmri.objects.select_for_update(of=("self",)).filter(pk=x["ie_id"], silindi=False).first()
        if ie is None:
            continue
        ek_run = _d(x["ek"])
        yeni_plan = max(ie.tamamlanan, ie.planlanan - ek_run)
        ihtiyac = dict(ie.ihtiyac or {})
        for key, m in x.get("ihtiyac_ek", {}).items():
            stok_pk, vpk = planlama_varyant.anahtar_coz(key)
            taban = u._onceki_ayrilan(ie, stok_pk, vpk)
            ihtiyac[key] = _ds(max(taban, _d(ihtiyac.get(key)) - _d(m)))
        onceki_plan = ie.planlanan
        ie.planlanan, ie.ihtiyac = yeni_plan, ihtiyac
        kalan_ek = max(S, _d((ie.fire_ek or {}).get("ek")) - ek_run)
        ie.fire_ek = {"ek": _ds(kalan_ek)} if kalan_ek > 0 else {}
        if x.get("yeni") and ie.tamamlanan <= 0:
            ie.durum = IstasyonEmri.Durum.IPTAL
            ie.planlanan = S
        ie.updated_by = kullanici
        ie.save()
        u.istasyon_emri_durum_guncelle(ie)
        geri.append({"no": ie.no, "once": _ds(onceki_plan), "sonra": _ds(ie.planlanan), "uretilmis_ek_kaldi": ie.planlanan > _d(x["once"])})
    ozet = {"kayit": kayit.no, "ayirma": ek.get("ayirma", []), "fason_siparis": ek.get("fason_siparis", []), "istasyon_emirleri": geri}
    yeni_ek = {} if yeniden else dict(ek, geri_alindi=True)
    OperasyonKaydi.objects.filter(pk=kayit.pk).update(fire_ek=yeni_ek)
    kayit.fire_ek = yeni_ek
    u._revizyon_yaz(emir, UretimEmriRevizyon.Tur.FIRE_GERI_ALMA, None,
                    f"{kayit.no} fire ek planı geri sarıldı" + (" (yeniden hesaplanmak üzere)" if yeniden else ""), ozet, kullanici)
    return ozet


def revize_yenilendi(emir: UretimEmri) -> None:
    """ÜS revizesi planı baştan kurar: fire ek izleri 'yenilendi' işaretlenir (artık geri sarılmaz), istasyon emri fire işaretleri temizlenir."""
    for k in OperasyonKaydi.objects.filter(uretim_emri=emir, silindi=False).exclude(fire_ek={}):
        if not k.fire_ek.get("yenilendi"):
            OperasyonKaydi.objects.filter(pk=k.pk).update(fire_ek=dict(k.fire_ek, yenilendi=True))
    IstasyonEmri.objects.filter(uretim_emri=emir).exclude(fire_ek={}).update(fire_ek={})


def fire_notlari(emir: UretimEmri) -> list:
    """ÜS detayı 'Fire kaynaklı ek plan': geri alınmamış kayıtların fire ek izleri (en yeni üstte) — hangi kayıt, kaç adet fire, ne ayrıldı, hangi emir artırıldı."""
    sonuc = []
    for k in OperasyonKaydi.objects.filter(uretim_emri=emir, silindi=False).exclude(fire_ek={}).select_related("istasyon_emri").order_by("-yil", "-sira"):
        ek = k.fire_ek
        if ek.get("geri_alindi") or ek.get("uygulanmadi"):
            continue
        sonuc.append({"kayit": k, "ek": ek, "hata": ek.get("hata"), "yenilendi": bool(ek.get("yenilendi")), "geriye_donuk": bool(ek.get("geriye_donuk"))})
    return sonuc


def fason_rozetleri(ieler) -> list:
    """Fire yüzünden artmış FASON istasyon emirleri → ``uretim_emri_eksikleri`` satırları (tür FASON_EMRI_GEREKLI, ``fire``=True). Tutar = min(ek, emrin kalanı) × referans;
    fason dönüşle parça gelince (kalan düşer) ya da ÜS'ye fason rezervi yapılıp ÜS revize edilince (plan baştan kurulur, ek temizlenir) kalkar."""
    sonuc = []
    for ie in ieler:
        if not ie.fire_ek or ie.durum == IstasyonEmri.Durum.IPTAL:
            continue
        ek = _d(ie.fire_ek.get("ek"))
        if ek <= 0 or not ie.istasyon.fason:
            continue
        miktar = min(ek, ie.kalan) * u._referans_miktar(ie.operasyon)
        if miktar <= 0:
            continue
        sonuc.append({"stok": ie.operasyon.cikti, "varyant": ie.cikti_varyant, "varyant_etiketi": planlama_varyant.etiket(ie.cikti_varyant) if ie.cikti_varyant_id else "",
                      "gerekli": miktar, "kullanilabilir": S, "eksik": miktar, "stoklu": False, "tur": "FASON_EMRI_GEREKLI", "fire": True, "ie": ie})
    return sonuc
