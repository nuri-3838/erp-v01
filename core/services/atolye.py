"""ATÖLYE operatör ekranı (/atolye/) — işçinin kendi telefonundan: mesai başlat → istasyon seç → emirde "Başla" (ekip seç) → "Bitir" (adet) /
"İptal" → mesai bitir. Üretim kaydı yalnız Bitir'de açılır (süresiz "Üretildi" atölyeden KALDIRILDI, 2026-10-12; ofis ÜS detayında durur).

Kurallar (kullanıcı kararları, 2026-10-11/12):
* Aynı personel hesabı (``Personel.kullanici``) ve aynı fabrika ağı kısıtı (``mesai_ag.ip_izinli_mi``) — mesai başlatmada da üretim girişinde de.
* Mesai açık değilse iş başlatılamaz/bitirilemez; miktar yalnız tam sayı; kayıt başlatan personele yazılır (``OperasyonKaydi.personel`` +
  ``mesai_kaydi``), ekip/süre ``IsOturumu``'nda — işçilik/GÜG dağıtımının kaynağı (bkz. core.services.is_oturumu).
* İstasyon seçimi serbest (fason istasyonu listede yok); kalanı aşan miktar ofisteki kuralla: LAZER MARKALAMA (dönüştür) ve PAKETLEME'de
  yasak (``uretim.fazla_uretim_yasak``), diğerlerinde uyarıyla serbest.
* Geri alma: yalnız bugünkü, KENDİ, o emrin SON onaylı kaydı (süre sınırı yok) — ``uretim.operasyon_kaydi_sil(onayli_geri_al=True)``; oturum kalır.
* Yetki: ekran kodu ``atolye`` (AYARLAR › Kullanıcı Yetkileri ya da personel kartındaki "Atölye operatörü" düğmesi).
* Malzeme durumu (2026-10-12): her emir kartında girdilerin BUGÜNKÜ kullanılabilir stoğuna (eldeki − başka ÜS'lerin ayırmaları; varyantlı emirde
  girdi varyantı bazında) göre üç durum: HAZIR (kalanın tamamı yapılabilir, yeşil) / KISMI ("Kısmen hazır: X / kalan", turuncu) / YOK ("Malzeme
  yok", gri, kart soluk, Başla uyarı sorar — engel yok). Eksik girdi "<varyant> markalı <ad> — önce <kaynak istasyon> (<IE no>)" (ara mamul: onu
  üreten tanımın istasyonu + bu ÜS'deki emri) ya da "satınalma bekleniyor" (hammadde). Bu ÜS'ye rezerve açık fason siparişi satırı girdiyse kartta
  "X adet fason siparişinden bekleniyor (FS-…, termin)". Sıralama: HAZIR, KISMI, YOK; sonra termin (kaynak siparişin termini, yoksa ÜS tarihi).
  Bitir: SAĞLAM + FİRE adet (boş sağlam → "Adet girin"; fire girdisi tüketilir, stoğa girmez — ``uretim.istasyon_emri_uret``)."""
from __future__ import annotations

import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from core.models import EkranYetki, IsIstasyonu, IsOturumu, IstasyonEmri, KarmaKesim, OperasyonKaydi, Personel, Stok, UretimEmri
from core.services import fason_siparis as fs_servis
from core.services import is_oturumu, karma_kesim, planlama_varyant, stok_ayirma
from core.services import mesai as mesai_servis
from core.services import mesai_ag, uretim
from core.services import varyant as varyant_servis
from core.tarih import _TR, tr_bugun

EKRAN_KODU = "atolye"


class AtolyeHatasi(ValueError):
    """Atölye kural ihlali (büyük, anlaşılır Türkçe mesaj)."""


def personel_coz(user) -> Personel | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return Personel.objects.filter(kullanici=user, silindi=False).first()


def _personel(user) -> Personel:
    p = personel_coz(user)
    if p is None:
        raise AtolyeHatasi("Bu hesaba bağlı bir personel kartı yok.")
    return p


def istasyonlar():
    """Seçilebilir istasyonlar: fason olmayanlar, akış sırasıyla (11 LAZER MARKALAMA dahil)."""
    return list(IsIstasyonu.objects.filter(silindi=False, fason=False).order_by("sira", "kod"))


def acik_emir_sayilari() -> dict:
    """{istasyon pk: açık emir sayısı} — açık ÜS'lerin BEKLİYOR/BAŞLADI istasyon emirleri (seçim kartlarındaki "N açık iş")."""
    qs = (IstasyonEmri.objects.filter(silindi=False, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
          .values("istasyon_id").annotate(n=Count("id")))
    return {r["istasyon_id"]: r["n"] for r in qs}


def istasyon_kartlari() -> list:
    """İstasyon seçim kartları: [{istasyon, acik}] — açık işi olmayan istasyon soluk gösterilir."""
    sayilar = acik_emir_sayilari()
    return [{"istasyon": i, "acik": sayilar.get(i.pk, 0)} for i in istasyonlar()]


def durum(user) -> dict:
    """{personel, acik_kayit, calisiyor, izleme} — ekranın üst durumu. Personel kartı olmayan YÖNETİCİ ekranı yalnız izler (istasyonlar ve
    emirler görünür; mesai/üretim/geri alma personel hesabı ister); personel kartı olmayan diğer kullanıcı için hata."""
    from core.services.personel import aktif_mi
    from core.yetki import yonetici_mi
    p = personel_coz(user)
    if p is None:
        if yonetici_mi(user):
            return {"personel": None, "acik_kayit": None, "calisiyor": False, "izleme": True}
        raise AtolyeHatasi("Bu hesaba bağlı bir personel kartı yok.")
    return {"personel": p, "acik_kayit": mesai_servis.acik_kayit(p), "calisiyor": aktif_mi(p), "izleme": False}


def mesai_baslat(user, ip) -> None:
    p = _personel(user)
    try:
        mesai_servis.baslat(p, ip=ip, kullanici=user)
    except mesai_servis.MesaiHatasi as e:
        raise AtolyeHatasi(str(e))


def mesai_bitir(user, ip, adet_ham=None, fire_ham=None, bekle=False) -> None:
    """Mesai bitir. Tek başına SORUMLU olduğu açık işi varsa: adet (+fire) girerse iş normal Bitir ile kapanır, girmezse ``bekle`` onayıyla "Adet bekliyor" olur
    (ikisi de yoksa mesai bitmez). Ekipte başkası varsa sorumluluk en uzun süredir ekipte olana geçer; üyeyse ayrılır."""
    p = _personel(user)
    adet = _miktar_coz(adet_ham, ad="Sağlam adet") if (adet_ham or "").strip() else None
    fire = _miktar_coz(fire_ham, ad="Fire", zorunlu=False) if adet is not None else None
    try:
        mesai_servis.bitir(p, ip=ip, kullanici=user, adet=adet, fire=fire, bekle_onay=bool(bekle))
    except mesai_servis.MesaiHatasi as e:
        raise AtolyeHatasi(str(e))


def mesai_bitir_bilgisi(user) -> dict:
    """Mesai bitir ekranı: kişinin tek başına SORUMLU olduğu açık işi (adet/fire kutusu gösterilir) ve diğer açık işi (üye / ekipli sorumlu: bilgi)."""
    p = personel_coz(user)
    sonuc = {"tek_sorumlu": None, "diger": None}
    if p is None:
        return sonuc
    u = is_oturumu.aktif_uyelik(p)
    if u is None:
        return sonuc
    satir = oturum_satiri(u.oturum, p)
    if satir["sorumlu_benim"] and len(satir["acik_uyeler"]) == 1:
        sonuc["tek_sorumlu"] = satir
    else:
        sonuc["diger"] = satir
    return sonuc


def _acik_mesai(user):
    p = _personel(user)
    m = mesai_servis.acik_kayit(p)
    if m is None:
        raise AtolyeHatasi("Önce mesainizi başlatın; mesai açık değilken üretim girilemez.")
    return p, m


def son_kaydim(user, ie: IstasyonEmri):
    """Emrin SON onaylı kaydı bu kullanıcıya ait ve bugün tarihliyse o kayıt (geri alınabilir), değilse None."""
    son = ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI).order_by("-pk").first()
    if son is not None and son.created_by_id == getattr(user, "pk", None) and son.tarih == tr_bugun():
        return son
    return None


def acik_emirler(istasyon, user=None) -> list:
    """İstasyonun açık emirleri (açık ÜS'ler, BEKLİYOR/BAŞLADI): ``uretim.istasyon_emri_satiri`` satırı + müşteri ("" = stok için üretim),
    varyant metni (standart dışı), tam sayı kalan, bugünkü kendi son kaydı, emrin açık iş oturumları (``oturumlar``: özet + ``benim``),
    ``malzeme`` (``malzeme_durumu``) ve ``termin``. SIRALAMA: ACİL ÜS önce; sonra malzemesi hazır olanlar, sonra termin yakın olan (boş termin sonda), ÜS tarihi/sırası, derin seviye önce
    (Acil → termin → tarih, İstasyon Emirleri ile ortak ``uretim.siralama``; "malzemesi hazır önce" atölyeye özgü ikinci anahtardır)."""
    qs = (IstasyonEmri.objects.filter(silindi=False, istasyon=istasyon, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
          .select_related("uretim_emri__kaynak_siparis__cari", "operasyon__cikti", "istasyon", "cikti_varyant")
          .order_by(*uretim.siralama("uretim_emri__"), "-seviye", "pk"))          # Acil → termin → tarih (İstasyon Emirleri ile ortak)
    p = personel_coz(user) if user is not None else None
    acik, bekleyen = {}, {}
    for o in is_oturumu.acik_oturumlar():
        acik.setdefault(o.istasyon_emri_id, []).append(o)
    for o in is_oturumu.adet_bekleyenler():
        bekleyen.setdefault(o.istasyon_emri_id, []).append(o)
    emirler = list(qs)
    girdiler = _girdi_satirlari(emirler, user)
    kullanilabilir = _kullanilabilir_haritalari(emirler, girdiler)
    kaynaklar = _girdi_kaynaklari(emirler, girdiler)
    fason = _fason_bekleyenler(emirler)
    sonuc = []
    for ie in emirler:
        s = uretim.istasyon_emri_satiri(ie, ie.uretim_emri)
        sip = ie.uretim_emri.kaynak_siparis
        s["musteri"] = (sip.cari.kisa_ad or sip.cari.unvan) if (sip is not None and sip.cari_id) else ""
        s["termin_etkin"] = uretim.etkin_termin(ie.uretim_emri)                                  # boşsa None (sırada sonda)
        s["acil"] = ie.uretim_emri.oncelik == UretimEmri.Oncelik.ACIL
        s["termin"] = s["termin_etkin"] or ie.uretim_emri.tarih
        s["varyant_metni"] = varyant_servis.standart_disi_metin(ie.cikti_varyant_id, "tr") if ie.cikti_varyant_id else ""
        s["kalan_tam"] = _tam_metin(s["acik"])
        s["son_kaydim"] = son_kaydim(user, ie) if user is not None else None
        s["oturumlar"] = [oturum_satiri(o, p) for o in acik.get(ie.pk, [])]                 # tek ekip kuralı: en çok BİR açık oturum
        s["adet_bekleyenler"] = [oturum_satiri(o, p) for o in bekleyen.get(ie.pk, [])]
        satirlar = girdiler[(ie.operasyon_id, ie.cikti_varyant_id)]
        s["malzeme"] = malzeme_durumu(ie, s["acik"], satirlar, kullanilabilir[ie.uretim_emri_id], kaynaklar)
        s["fason_bekleyen"] = [b["metin"] for b in fason.get(ie.uretim_emri_id, [])
                               if any(g.girdi_id == b["stok_id"] and (vpk is None or vpk == b["varyant_id"]) for g, vpk in satirlar)]
        sonuc.append(s)
    sira = {"HAZIR": 0, "KISMI": 1, "YOK": 2}
    sonuc.sort(key=lambda s: (not s["acil"], sira[s["malzeme"]["durum"]], s["termin_etkin"] or datetime.date.max, s["ie"].uretim_emri.tarih,
                              s["ie"].uretim_emri.yil, s["ie"].uretim_emri.sira, -s["ie"].seviye, s["ie"].pk))
    return sonuc


def _girdi_satirlari(emirler, user=None) -> dict:
    """{(operasyon pk, çıktı varyant pk|None): [(OperasyonGirdi, girdi varyant pk|None)]} — varyantlı emirde girdi varyantı tanım kuralından
    (``planlama_varyant.girdi_varyanti``); atlanan (tüm boyutları YOK, satın alınan) girdi listede yok; varyantsız emirde kart toplamı (None)."""
    ob = planlama_varyant.Onbellek()
    sonuc = {}
    for ie in emirler:
        anahtar = (ie.operasyon_id, ie.cikti_varyant_id)
        if anahtar in sonuc:
            continue
        satirlar = []
        for g in uretim.operasyon_girdileri(ie.operasyon):
            if ie.cikti_varyant_id:
                sv, atla = planlama_varyant.girdi_varyanti(g, ie.cikti_varyant, user, ob)
                if atla:
                    continue
                satirlar.append((g, sv.pk if sv is not None else None))
            else:
                satirlar.append((g, None))
        sonuc[anahtar] = satirlar
    return sonuc


def _kullanilabilir_haritalari(emirler, girdiler) -> dict:
    """{ÜS pk: {(stok pk, varyant pk|None): kullanılabilir}} — eldeki − BAŞKA ÜS'lerin ayırmaları (ÜS'nin kendi ayırması kendine kullanılabilir)."""
    dugumler = {}
    for ie in emirler:
        dugumler.setdefault(ie.uretim_emri_id, set()).update((g.girdi_id, vpk) for g, vpk in girdiler[(ie.operasyon_id, ie.cikti_varyant_id)])
    return {us: stok_ayirma.kullanilabilir_varyant_haritasi(d, haric_emir=us) for us, d in dugumler.items()}


def _girdi_kaynaklari(emirler, girdiler) -> dict:
    """Eksik girdi için kaynak: ``{"ureten": {girdi stok pk: Operasyon|None}, "ie": {(ÜS pk, operasyon pk, çıktı varyant pk|None): IstasyonEmri}}``
    — ara mamulü üreten tanımın istasyonu + bu ÜS'deki (iptal olmayan) emri; tanım yoksa hammadde/satınalma."""
    ureten = {}
    for satirlar in girdiler.values():
        for g, _v in satirlar:
            if g.girdi_id not in ureten:
                ureten[g.girdi_id] = uretim.ureten_operasyon(g.girdi)
    us_idler = {ie.uretim_emri_id for ie in emirler}
    ie_harita = {}
    for x in (IstasyonEmri.objects.filter(uretim_emri_id__in=us_idler, silindi=False).exclude(durum=IstasyonEmri.Durum.IPTAL)
              .select_related("istasyon").order_by("pk")):
        ie_harita.setdefault((x.uretim_emri_id, x.operasyon_id, x.cikti_varyant_id), x)
        ie_harita.setdefault((x.uretim_emri_id, x.operasyon_id, None), x)
    return {"ureten": ureten, "ie": ie_harita}


def _fason_bekleyenler(emirler) -> dict:
    """{ÜS pk: [{"stok_id", "varyant_id", "metin"}]} — ÜS'ye rezerve açık fason siparişi satırları (kartta girdiyle eşleşenler gösterilir)."""
    sonuc = {}
    for us in {ie.uretim_emri_id for ie in emirler}:
        for r in fs_servis.emir_rezervleri(us):
            fs = r.satir.siparis
            termin = f"termin {fs.termin:%d.%m.%Y}" if fs.termin else "termin yok"
            sonuc.setdefault(us, []).append({"stok_id": r.satir.stok_id, "varyant_id": r.satir.varyant_id,
                                             "metin": f"{_tam_metin(r.miktar)} adet fason siparişinden bekleniyor ({fs.no}, {termin})"})
    return sonuc


def _varyant_on_eki(vpk) -> str:
    """Girdi adının önüne varyant (standart dışı boyutlar): tek boyut markalamaysa 'ELMOP markalı ' / 'markasız ' (YOK), başka boyut da varsa
    '[Afiş: yok · Markalama: ELMOP] '; standart ya da varyantsız → ''."""
    return _on_ek_metni(varyant_servis.standart_disi_metin(vpk, "tr")) if vpk else ""


def _on_ek_metni(m: str) -> str:
    if not m:
        return ""
    parcalar = m.split(" · ")
    if len(parcalar) == 1 and parcalar[0].startswith("Markalama: "):
        deger = parcalar[0][len("Markalama: "):].strip()
        return "markasız " if deger.lower() in ("yok", "—", "") else f"{deger} markalı "
    return f"[{m}] "


def _kaynak_metni(ie, girdi_id, vpk, kaynaklar) -> str:
    op = kaynaklar["ureten"].get(girdi_id)
    if op is None:
        return "satınalma bekleniyor"
    x = kaynaklar["ie"].get((ie.uretim_emri_id, op.pk, vpk)) or kaynaklar["ie"].get((ie.uretim_emri_id, op.pk, None))
    return f"önce {op.istasyon.kod} {op.istasyon.ad} ({x.no if x is not None else 'bu siparişte açık emir yok'})"


def malzeme_durumu(ie, kalan, girdi_satirlari, kullanilabilir, kaynaklar=None) -> dict:
    """Emrin girdilerinin bugünkü kullanılabilir stoğuna göre ``yapilabilir`` referans çıktı adedi (tam çalıştırma zorunluysa çalıştırma katı),
    ``hazir`` = min(kalan, yapılabilir); ``durum`` HAZIR (hazır ≥ kalan) / KISMI (0 < hazır < kalan) / YOK (0); ``eksik`` = sınırlayan girdilerin
    metinleri ("<varyant> markalı <ad> yok — önce 11 LAZER MARKALAMA (IE-…)" / "… — satınalma bekleniyor"); ``metin`` kart satırı."""
    op = ie.operasyon
    ref = uretim._referans_miktar(op)
    kalan = max(Decimal(kalan), Decimal("0"))
    kaynaklar = kaynaklar or {"ureten": {}, "ie": {}}
    en_az, adetler = None, []
    for g, vpk in girdi_satirlari:
        kull = kullanilabilir.get((g.girdi_id, vpk), Decimal("0"))
        adet = (kull * ref / g.miktar) if kull > 0 else Decimal("0")          # bu girdiyle üretilebilecek referans çıktı adedi
        adetler.append((g, vpk, adet.to_integral_value(rounding=ROUND_FLOOR)))
        en_az = adet if en_az is None else min(en_az, adet)
    if en_az is None:
        yapilabilir = kalan
    elif op.tam_calistirma:
        yapilabilir = (en_az / ref).to_integral_value(rounding=ROUND_FLOOR) * ref
    else:
        yapilabilir = en_az.to_integral_value(rounding=ROUND_FLOOR)
    hazir = max(min(kalan, yapilabilir), Decimal("0"))
    durum = "HAZIR" if hazir >= kalan else ("KISMI" if hazir > 0 else "YOK")
    eksik = []
    for g, vpk, adet in adetler:
        if (durum == "YOK" and adet < 1) or (durum == "KISMI" and adet < kalan):
            ad = _varyant_on_eki(vpk) + g.girdi.ad
            kaynak = _kaynak_metni(ie, g.girdi_id, vpk, kaynaklar)
            eksik.append(f"{ad} yok — {kaynak}" if adet < 1 else f"{ad} ({_tam_metin(adet)} adetlik) — {kaynak}")
    if durum == "HAZIR":
        metin = f"Malzeme hazır: {_tam_metin(hazir)} adet yapılabilir"
    elif durum == "KISMI":
        metin = f"Kısmen hazır: {_tam_metin(hazir)} / {_tam_metin(kalan)} — eksik: " + "; ".join(eksik)
    else:
        metin = "Malzeme yok: " + ("; ".join(eksik) or "girdi stoğu yok")
    return {"yapilabilir": yapilabilir, "hazir": hazir, "hazir_tam": _tam_metin(hazir), "kalan_tam": _tam_metin(kalan), "durum": durum,
            "eksik": eksik, "eksik_metni": "; ".join(eksik), "metin": metin, "yok": durum == "YOK"}


def oturum_satiri(oturum, personel=None) -> dict:
    """Ekran satırı: ``is_oturumu.ozet`` + ``benim`` (kişi aktif üye) + dünkü oturum için önerilen bitiş (datetime-local değeri, TR)."""
    s = is_oturumu.ozet(oturum)
    s["sorumlu_benim"] = personel is not None and s["sorumlu"].pk == personel.pk
    s["uye_benim"] = personel is not None and any(u.personel_id == personel.pk and u.ayrilma_zamani is None for u in s["uyeler"])
    s["benim"] = s["uye_benim"] or (s["adet_bekliyor"] and s["sorumlu_benim"])
    s["devir_adaylari"] = [u.personel for u in s["acik_uyeler"] if personel is not None and u.personel_id != personel.pk]
    if oturum.karma:                                       # karma kesim: emire bağlı değil; parça listesi formu
        s["emir_kapali"], s["kalan_tam"], s["karma_form"] = False, "", karma_formu(oturum)
    else:
        ie, us = oturum.istasyon_emri, oturum.uretim_emri
        s["emir_kapali"] = bool(ie.silindi or ie.durum in (IstasyonEmri.Durum.BITTI, IstasyonEmri.Durum.IPTAL) or us.silindi or us.durum != UretimEmri.Durum.ACIK)
        s["kalan_tam"] = _tam_metin(uretim.istasyon_emri_satiri(ie, us)["acik"])
    s["bitis_varsayilan"] = ""
    if s["dunku"] and personel is not None:
        vb = is_oturumu.varsayilan_bitis(oturum, personel)
        s["bitis_varsayilan"] = vb.astimezone(_TR).strftime("%Y-%m-%dT%H:%M") if vb else ""
    return s


def karma_formu(oturum) -> dict:
    """Karma kesim Bitir/Adet gir formu: profil + boy + ayarlar ve her aday parça için {stok, parca_mm, ihtiyac} (``ihtiyac`` = açık emirlerin toplam net kalan ihtiyacı — yalnız ipucu)."""
    profil = oturum.karma_profil
    adaylar = []
    for a in karma_kesim.parca_adaylari(oturum.istasyon_id, profil.pk):
        ihtiyac = sum((karma_kesim._net_kalan(ie) for ie in karma_kesim._acik_emirler(a["operasyon"], False)), Decimal("0"))
        adaylar.append({"stok": a["stok"], "parca_mm": a["parca_mm"], "ihtiyac": ihtiyac.to_integral_value(rounding=ROUND_CEILING), "ihtiyac_tam": _tam_metin(ihtiyac)})
    return {"profil": profil, "boy_mm": profil.boy_uzunluk_mm, "adaylar": adaylar, **karma_kesim.ayarlar()}


def karma_istasyon(istasyon, user=None) -> dict:
    """İstasyon sayfasındaki KARMA KESİM bölümü: {profiller (başlatılabilir profil seçenekleri), acik (açık oturum satırları), adet_bekleyen}."""
    p = personel_coz(user) if user is not None else None
    son = (KarmaKesim.objects.filter(silindi=False, istasyon=istasyon, created_by=user, tarih=tr_bugun()).order_by("-pk").first()
           if getattr(user, "pk", None) else None)                                               # bugünkü kendi son karma kesimi (geri alınabilir)
    return {"profiller": karma_kesim.profil_secenekleri(istasyon.pk), "son_kesim": son,
            "acik": [oturum_satiri(o, p) for o in is_oturumu.karma_oturumlari(istasyon.pk)],
            "adet_bekleyen": [oturum_satiri(o, p) for o in is_oturumu.karma_oturumlari(istasyon.pk, IsOturumu.Durum.ADET)]}


def acik_islerim(user) -> list:
    """Kişinin "Açık işin" kartları — mesai açık/kapalı, emir açık/tamamlanmış FARK ETMEZ: üyesi olduğu açık oturumlar (dünden kalanlar dahil) + sorumlusu olduğu
    "Adet bekliyor" oturumlar. Ekranın en üstünde; yönetici izlemede boş."""
    p = personel_coz(user)
    if p is None:
        return []
    return [oturum_satiri(o, p) for o in is_oturumu.kisinin_acik_oturumlari(p) + is_oturumu.kisinin_adet_bekleyenleri(p)]


def ekip_adaylari(user) -> list:
    p = personel_coz(user)
    return is_oturumu.ekip_adaylari(p) if p is not None else []


def _tam_metin(d: Decimal) -> str:
    d = Decimal(d)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def _miktar_coz(ham, *, ad="Miktar", zorunlu=True) -> Decimal:
    """Tam sayı adet; boş: zorunluysa "Adet girin." (Bitir'de sağlam adet ön dolu gelmez), değilse 0 (fire)."""
    s = (ham or "").strip().replace(".", "").replace(" ", "")
    if not s:
        if zorunlu:
            raise AtolyeHatasi("Adet girin.")
        return Decimal("0")
    if not s.isdigit():
        raise AtolyeHatasi(f"{ad} tam sayı olmalı (ör. 12).")
    return Decimal(int(s))


def _bitis_coz(ham):
    """datetime-local ("YYYY-MM-DDTHH:MM", TR saati) → aware datetime; boş → None; bozuk → hata."""
    ham = (ham or "").strip()
    if not ham:
        return None
    try:
        return datetime.datetime.fromisoformat(ham).replace(tzinfo=_TR)
    except ValueError:
        raise AtolyeHatasi("Bitiş saati anlaşılamadı (örn. 17:30).")


def _ag_kontrol(ip, ne):
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi(f"{ne} yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")


def oturum_baslat(user, ie: IstasyonEmri, ekip_pks, ip):
    """Operatör "Başla": mesai açık + fabrika ağı; ekip = seçilen personel pk'ları (``is_oturumu.baslat`` kuralları)."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "İş başlatma")
    pks = [int(x) for x in (ekip_pks or []) if str(x).isdigit()]
    ekip = list(Personel.objects.filter(pk__in=pks, silindi=False)) if pks else []
    if len(ekip) != len(set(pks)):
        raise AtolyeHatasi("Ekipteki bir personel bulunamadı.")
    try:
        return is_oturumu.baslat(p, ie, ekip, ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_bitir(user, oturum, miktar_ham, bitis_ham, ip, fire_ham=None):
    """Operatör "Bitir": SAĞLAM adet (tam sayı, zorunlu) + FİRE adet (tam sayı, boş = 0); (kayıt, uyarı, sağlam, fire) döner; üretim hatasında
    oturum açık kalır (mesaj aynen)."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Üretim girişi")
    miktar = _miktar_coz(miktar_ham, ad="Sağlam adet")
    fire = _miktar_coz(fire_ham, ad="Fire", zorunlu=False)
    try:
        kayit, uyari = is_oturumu.bitir(p, oturum, miktar, fire=fire, bitis=_bitis_coz(bitis_ham), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))
    return kayit, uyari, miktar, fire


def oturum_baslat_karma(user, istasyon, profil_id, ekip_pks, ip):
    """Operatör "Karma kesim başlat": mesai açık + fabrika ağı; istasyon başına tek açık karma oturum (``is_oturumu.baslat_karma`` kuralları)."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Karma kesim başlatma")
    profil = Stok.objects.filter(pk=int(profil_id), silindi=False).first() if str(profil_id or "").isdigit() else None
    if profil is None:
        raise AtolyeHatasi("Profili seçin.")
    pks = [int(x) for x in (ekip_pks or []) if str(x).isdigit()]
    ekip = list(Personel.objects.filter(pk__in=pks, silindi=False)) if pks else []
    if len(ekip) != len(set(pks)):
        raise AtolyeHatasi("Ekipteki bir personel bulunamadı.")
    try:
        return is_oturumu.baslat_karma(p, istasyon, profil, ekip, ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def _karma_satirlari(adetler) -> list:
    """{parça stok pk: ham adet} → [(pk, adet)]: boş ve 0 satırlar atlanır; tam sayı olmayan hata verir."""
    sonuc = []
    for pk, ham in (adetler or {}).items():
        if not str(pk).isdigit() or not (ham or "").strip():
            continue
        m = _miktar_coz(ham, ad="Parça adedi")
        if m > 0:
            sonuc.append((int(pk), int(m)))
    return sonuc


def oturum_karma_bitir(user, oturum, boy_ham, adetler, bitis_ham, ip):
    """Sorumlu karma kesimi Bitir (açık oturum) ya da Adet gir ("Adet bekliyor" oturum): kullanılan boy sayısı + parça adetleri. Mesai açık + fabrika ağı. Hatada (bütçe aşımı,
    yetersiz stok…) oturum olduğu gibi kalır; dönen: ``KarmaKesim``."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Üretim girişi")
    if not (boy_ham or "").strip():
        raise AtolyeHatasi("Kullanılan boy sayısını girin.")
    boy = _miktar_coz(boy_ham, ad="Kullanılan boy sayısı")
    satirlar = _karma_satirlari(adetler)
    try:
        if oturum.durum == IsOturumu.Durum.ADET:
            return is_oturumu.adet_gir_karma(oturum, boy, satirlar, personel=p, kullanici=user, ip=ip)
        return is_oturumu.bitir_karma(p, oturum, boy, satirlar, bitis=_bitis_coz(bitis_ham), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def karma_onizleme(istasyon_id, profil_id, boy_ham, adetler) -> dict:
    """Formun canlı önizlemesi (hiçbir şey yazılmaz): {"ok", "hata"} + ok ise uzunluk/fire dökümü ve parça × emir dağılımı (metin olarak biçimli)."""
    try:
        satirlar = _karma_satirlari(adetler)
        if not satirlar:
            return {"ok": False, "hata": ""}
        if not (boy_ham or "").strip():
            return {"ok": False, "hata": "Kullanılan boy sayısını girin."}
        boy = _miktar_coz(boy_ham, ad="Kullanılan boy sayısı")
        o = karma_kesim.onizleme(istasyon_id=istasyon_id, profil_id=profil_id, boy_sayisi=boy, satirlar=satirlar)
    except (AtolyeHatasi, uretim.UretimHatasi) as e:
        return {"ok": False, "hata": str(e)}
    return karma_kesim.onizleme_ozeti(o)


def oturum_iptal(user, oturum, bitis_ham, ip):
    p = _personel(user)                                                   # mesai şartı yok: açık iş hiçbir zaman "kilitli" kalmasın
    _ag_kontrol(ip, "İptal")
    try:
        return is_oturumu.iptal(p, oturum, bitis=_bitis_coz(bitis_ham), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_katil(user, oturum, ip):
    """"Ekibe katıl": mesai açık + fabrika ağı; çalışır, süresi işçiliğe sayılır, adet giremez."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Ekibe katılma")
    try:
        return is_oturumu.katil(p, oturum, ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_ayril(user, oturum, ip):
    p = _personel(user)
    try:
        return is_oturumu.ayril(p, oturum, ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_devret(user, oturum, yeni_pk, ayril, ip):
    """Sorumlu "Sorumluluğu devret": ekipten birini seçer; ``ayril`` ise devreden aynı anda ekipten ayrılır."""
    p = _personel(user)
    yeni = Personel.objects.filter(pk=yeni_pk, silindi=False).first() if str(yeni_pk or "").isdigit() else None
    try:
        return is_oturumu.devret(p, oturum, yeni, ayril_=bool(ayril), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_adet_gir(user, oturum, miktar_ham, fire_ham, ip):
    """Sorumlu, "Adet bekliyor" oturuma adet/fire girer (mesai açık + fabrika ağı): normal üretim kaydı oluşur."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Üretim girişi")
    miktar = _miktar_coz(miktar_ham, ad="Sağlam adet")
    fire = _miktar_coz(fire_ham, ad="Fire", zorunlu=False)
    try:
        kayit, uyari = is_oturumu.adet_gir(oturum, miktar, fire=fire, personel=p, kullanici=user, ip=ip)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))
    return kayit, uyari, miktar, fire


@transaction.atomic
def karma_geri_al(user, karma, ip):
    """Bugünkü KENDİ SON karma kesimini bütün olarak geri alır (tüm kayıtların stok hareketleri, maliyet fişleri, ÜS ayırması/tamamlananı, daraltılan emir planlananı)."""
    _acik_mesai(user)
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi("Geri alma yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")
    son = KarmaKesim.objects.filter(silindi=False, istasyon_id=karma.istasyon_id, created_by=user, tarih=tr_bugun()).order_by("-pk").first()
    if karma.silindi or son is None or son.pk != karma.pk:
        raise AtolyeHatasi("Yalnız bugünkü kendi SON karma kesiminizi geri alabilirsiniz.")
    try:
        return karma_kesim.karma_geri_al(karma, kullanici=user)
    except uretim.UretimHatasi as e:
        raise AtolyeHatasi(str(e))


@transaction.atomic
def geri_al(user, kayit: OperasyonKaydi, ip) -> OperasyonKaydi:
    """Bugünkü KENDİ SON kaydını geri alır (stok hareketleri + maliyet fişi geri sarılır, ``geri_alan`` izi); başka bir kayıt reddedilir. Karma kesim kaydıysa kesimin
    TÜM kayıtları birlikte geri alınır (``karma_geri_al``)."""
    if kayit.karma_id:
        karma_geri_al(user, kayit.karma, ip)
        return kayit
    _acik_mesai(user)
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi("Geri alma yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")
    ie = kayit.istasyon_emri
    if ie is None or son_kaydim(user, ie) is None or son_kaydim(user, ie).pk != kayit.pk:
        raise AtolyeHatasi("Yalnız bugünkü kendi SON kaydınızı geri alabilirsiniz.")
    if kayit.uretim_emri is None or kayit.uretim_emri.durum != UretimEmri.Durum.ACIK:
        raise AtolyeHatasi("Üretim siparişi kapalı; kayıt atölyeden geri alınamaz.")
    try:
        return uretim.operasyon_kaydi_sil(kayit, kullanici=user, onayli_geri_al=True)
    except uretim.UretimHatasi as e:
        raise AtolyeHatasi(str(e))


# --- yetki: "Atölye operatörü" (personel kartından tek düğme) ---

def operator_mu(personel: Personel) -> bool:
    return bool(personel.kullanici_id and EkranYetki.objects.filter(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU, silindi=False).exists())


@transaction.atomic
def operator_ayarla(personel: Personel, acik: bool, yapan=None) -> bool:
    """Personelin mesai hesabına ``atolye`` ekran yetkisini verir/kaldırır (EkranYetki satırı; soft-delete ile geri alınır)."""
    if not personel.kullanici_id:
        raise AtolyeHatasi("Önce personele mesai hesabı oluşturun.")
    satir = EkranYetki.objects.filter(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU).order_by("-pk").first()
    if acik:
        if satir is None:
            EkranYetki.objects.create(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU, created_by=yapan, updated_by=yapan)
        elif satir.silindi:
            satir.silindi, satir.silindi_at, satir.updated_by = False, None, yapan
            satir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    elif satir is not None and not satir.silindi:
        satir.silindi, satir.silindi_at, satir.updated_by = True, timezone.now(), yapan
        satir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return acik
