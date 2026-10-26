"""KARMA KESİM (basamak kesiminde operatör karması): aynı profilin N boyundan, aynı istasyonda FARKLI parçaların birlikte kesilmesi.

Önceden kesim planı tanımlanmaz: aday parçalar istasyonun mevcut ÜRET tanımlarından türetilir (tek girdisi bu profil olan, parça boyu (``boy_mm``) bilinen).
Uzunluk bütçesi:  Σ(adet × parça mm) + toplam adet × testere payı  ≤  boy sayısı × (boy mm − çene payı).  Aşan kayıt reddedilir.
FİRE = boy sayısı × boy mm − Σ(adet × parça mm) = testere + çene + uç. Fire stoğa GİRMEZ ve ayrıca maliyetlenmez: profilin TOPLAM maliyeti (boy sayısı × ortalama,
fire dahil) parçalara kesilen uzunluk oranıyla dağılır → parça birim maliyeti = toplam × parça mm / Σ(adet × parça mm).

Uygulama: her (parça × emir) ayrımı normal bir ``OperasyonKaydi`` olarak açılıp MEVCUT onay servisiyle onaylanır (stok çıkış/giriş, FIFO/ortalama maliyet, 15x→15x fişi,
ÜS ayırması, tamamlanan); profil girdisinin kayıt payı = boy sayısı × (kayıt uzunluğu / toplam uzunluk), 6 ondalık, yuvarlama farkı en uzun kayda. Emir dağıtımı: parçanın
açık istasyon emirleri ``uretim.siralama`` (Acil → termin → ÜS tarihi/no) sırasıyla NET kalan ihtiyaç kadar doldurulur; artan adet emirsiz kayıt olarak serbest stoğa girer.
Emrin net ihtiyacı karşılanınca planlananı tamamlanana çekilir (``uretim._karma_emir_daralt``). Grup tek işlemde yazılır ve birlikte geri alınır.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from core.models import (
    Depo, IsIstasyonu, IstasyonEmri, KarmaKesim, KarmaKesimSatir, Operasyon, OperasyonKaydi, Stok, UretimAyari, UretimEmri,
)
from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla
from core.services import uretim
from core.services.uretim import UretimHatasi
from core.tarih import tr_bugun

S = Decimal("0")
MM = Decimal("0.01")


class KarmaKesimHatasi(UretimHatasi):
    """Karma kesim kural ihlali (Türkçe mesaj)."""


def _tam_sayi(ham, ad: str) -> int:
    """Pozitif tam sayı (boş/kesirli/≤0 reddedilir)."""
    try:
        d = ham if isinstance(ham, Decimal) else parse_tr(ham)
    except SayiHatasi:
        raise KarmaKesimHatasi(f"{ad} geçerli bir sayı olmalı.")
    if d != d.to_integral_value() or d <= 0:
        raise KarmaKesimHatasi(f"{ad} sıfırdan büyük tam sayı olmalı.")
    return int(d)


def _mm(d: Decimal) -> str:
    return format_tr(d, 2)


# --- ayar / aday parçalar ---

def ayarlar() -> dict:
    a = UretimAyari.get()
    return {"testere_payi_mm": a.testere_payi_mm, "cene_payi_mm": a.cene_payi_mm}


def parca_adaylari(istasyon_id, profil_id) -> list:
    """İstasyonda bu profilden kesilebilen parçalar: [{"stok", "operasyon", "parca_mm"|None}] (stok koduna göre). Aday = fason olmayan istasyonda, TEK girdisi bu profil olan
    ÜRET tanımı; ``parca_mm`` = tanımın referans çıktı boyu (``boy_mm``) — boş ise parça listelenir ama kesim girilemez ("boy tanımsız")."""
    sonuc = []
    qs = (Operasyon.objects.filter(silindi=False, istasyon_id=istasyon_id, istasyon__fason=False, tur=Operasyon.Tur.URET)
          .select_related("cikti", "istasyon").order_by("cikti__kod"))
    for op in qs:
        girdiler = uretim.operasyon_girdileri(op)
        if len(girdiler) != 1 or girdiler[0].girdi_id != profil_id:
            continue
        tanim = uretim.tanim_ciktilari(op)
        boy = tanim[0].boy_mm if tanim else op.boy_mm
        sonuc.append({"stok": op.cikti, "operasyon": op, "parca_mm": boy})
    return sonuc


def profil_secenekleri(istasyon_id) -> list:
    """İstasyonda karma kesim yapılabilen profiller: [{"profil": Stok, "parca_sayisi": n}] — BOY birimli, kartında boy uzunluğu tanımlı ve boyu bilinen en az bir
    tek girdili (bu profil) ÜRET parçası olan profiller (stok koduna göre)."""
    sayilar = {}
    qs = Operasyon.objects.filter(silindi=False, istasyon_id=istasyon_id, istasyon__fason=False, tur=Operasyon.Tur.URET)
    for op in qs:
        girdiler = list(uretim.operasyon_girdileri(op))
        tanim = uretim.tanim_ciktilari(op)
        boy = tanim[0].boy_mm if tanim else op.boy_mm
        if len(girdiler) == 1 and boy:
            sayilar[girdiler[0].girdi_id] = sayilar.get(girdiler[0].girdi_id, 0) + 1
    profiller = (Stok.objects.filter(pk__in=list(sayilar), silindi=False, boy_uzunluk_mm__isnull=False).select_related("uretim_birimi").order_by("kod"))
    return [{"profil": p, "parca_sayisi": sayilar[p.pk]} for p in profiller if uretim._boy_birimli_mi(p)]


def varsayilan_depo(istasyon_id, profil_id):
    """Karma kesim kayıtlarının deposu (operatör seçmez): adaylardan açık emri olanların EN ÖNCELİKLİ ÜS'sinin deposu (siralama: Acil → termin → ÜS tarihi/no); emir yoksa
    ilk fabrika (fason olmayan) deposu. Hiç depo yoksa None."""
    ops = [a["operasyon"].pk for a in parca_adaylari(istasyon_id, profil_id)]
    ie = (IstasyonEmri.objects.filter(silindi=False, operasyon_id__in=ops, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI], istasyon__fason=False)
          .select_related("uretim_emri__depo").order_by(*uretim.siralama("uretim_emri__"), "pk").first())
    if ie is not None and not ie.uretim_emri.depo.fason_cari_id:
        return ie.uretim_emri.depo
    return Depo.objects.filter(silindi=False, fason_cari__isnull=True).order_by("pk").first()


# --- uzunluk bütçesi (saf hesap) ---

def butce(boy_sayisi: int, boy_mm: Decimal, testere_mm: Decimal, cene_mm: Decimal, parcalar) -> dict:
    """``parcalar``: [(adet, parça mm)]. Dönen: parca_mm (Σ adet × mm), toplam_adet, testere_mm, cene_mm, uc_mm, fire_mm, fire_orani (0-1), kullanilabilir_mm,
    gerekli_mm (parça + testere), fazla_mm (≤ 0 ise sığıyor). Fire = toplam − Σ parça; uç = fire − testere − çene (negatif olamaz: bütçe sağlanıyorsa)."""
    toplam = Decimal(boy_sayisi) * boy_mm
    parca = sum((Decimal(a) * m for a, m in parcalar), S)
    adet = sum(int(a) for a, _m in parcalar)
    testere = Decimal(adet) * testere_mm
    cene = Decimal(boy_sayisi) * cene_mm
    kullanilabilir = Decimal(boy_sayisi) * (boy_mm - cene_mm)
    gerekli = parca + testere
    fire = toplam - parca
    return {"toplam_mm": toplam, "parca_mm": parca, "toplam_adet": adet, "testere_mm": testere, "cene_mm": cene, "uc_mm": fire - testere - cene,
            "fire_mm": fire, "fire_orani": (fire / toplam) if toplam else S, "kullanilabilir_mm": kullanilabilir, "gerekli_mm": gerekli,
            "fazla_mm": gerekli - kullanilabilir}


def _butce_dogrula(b: dict, boy_sayisi, boy_mm, cene_mm) -> None:
    if b["fazla_mm"] > 0:
        raise KarmaKesimHatasi(
            f"Kesim uzunluğu aşıldı: gerekli {_mm(b['gerekli_mm'])} mm (parçalar {_mm(b['parca_mm'])} + testere {_mm(b['testere_mm'])}), "
            f"kullanılabilir {_mm(b['kullanilabilir_mm'])} mm ({boy_sayisi} boy × ({_mm(boy_mm)} − çene {_mm(cene_mm)})); {_mm(b['fazla_mm'])} mm fazla.")


# --- doğrulama / plan ---

def _profil_coz(profil_id) -> Stok:
    profil = Stok.objects.filter(pk=profil_id, silindi=False).select_related("uretim_birimi").first()
    if profil is None:
        raise KarmaKesimHatasi("Profil bulunamadı.")
    if not uretim._boy_birimli_mi(profil):
        raise KarmaKesimHatasi(f"{profil.kod}: karma kesim yalnız BOY birimli profilde yapılır.")
    if not profil.boy_uzunluk_mm:
        raise KarmaKesimHatasi(f"{profil.kod}: profil kartında boy uzunluğu (mm) tanımlı değil; karma kesim yapılamaz.")
    return profil


def _satirlari_coz(istasyon_id, profil: Stok, satirlar) -> list:
    """[(aday dict, adet int)] — aynı parça birden çok satırda toplanır; aday olmayan / boyu tanımsız parça reddedilir."""
    adaylar = {a["stok"].pk: a for a in parca_adaylari(istasyon_id, profil.pk)}
    toplam = {}
    for stok_id, adet in satirlar or []:
        try:
            sid = int(stok_id)
        except (TypeError, ValueError):
            raise KarmaKesimHatasi("Parça seçimi geçersiz.")
        a = adaylar.get(sid)
        if a is None:
            ad = Stok.objects.filter(pk=sid).values_list("kod", flat=True).first() or str(sid)
            raise KarmaKesimHatasi(f"{ad}: bu istasyonda bu profilden kesilen bir parça değil.")
        if not a["parca_mm"]:
            raise KarmaKesimHatasi(f"{a['stok'].kod}: parça boyu (mm) tanımlı değil; karma kesimde kullanılamaz.")
        toplam[sid] = toplam.get(sid, 0) + _tam_sayi(adet, f"{a['stok'].kod} adedi")
    if not toplam:
        raise KarmaKesimHatasi("En az bir parça satırı girin.")
    return [(adaylar[sid], adet) for sid, adet in sorted(toplam.items(), key=lambda x: adaylar[x[0]]["stok"].kod)]


def _acik_emirler(operasyon: Operasyon, kilitle: bool):
    qs = (IstasyonEmri.objects.filter(silindi=False, operasyon=operasyon, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI], istasyon__fason=False)
          .select_related("uretim_emri", "operasyon", "cikti_varyant")
          .order_by(*uretim.siralama("uretim_emri__"), "-seviye", "pk"))
    return qs.select_for_update(of=("self",)) if kilitle else qs


def _net_kalan(ie: IstasyonEmri) -> Decimal:
    """Emrin bu parça için henüz karşılanmamış NET ihtiyacı (adet): ihtiyaç − onaylı kayıtların ayırdığı − açık taslakların hedefi. ``ihtiyac`` boşsa (eski emir)
    açık kalan çalıştırma × referans miktar."""
    ref = uretim._referans_miktar(ie.operasyon)
    ihtiyac = uretim._ihtiyac_coz(ie)
    anahtar = (ie.operasyon.cikti_id, ie.cikti_varyant_id)
    if ihtiyac:
        gerek = ihtiyac.get(anahtar, S)
        if gerek <= 0:
            return S
        kalan = gerek - uretim._onceki_ayrilan(ie, ie.operasyon.cikti_id, ie.cikti_varyant_id)
        kalan -= sum((k.hedef_cikti_miktari for k in ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK)), S)
    else:
        kalan = max(uretim.istasyon_emri_acik_kalan(ie), S) * ref
    return max(kalan, S)


def _dagit(aday: dict, adet: int, kilitle: bool) -> list:
    """Parçanın adedini açık emirlere (net kalan ihtiyaç kadar, siralama sırasıyla) dağıtır: [(IstasyonEmri|None, adet)]; artan adet emirsiz (None) → serbest stok."""
    kalan, sonuc = adet, []
    for ie in _acik_emirler(aday["operasyon"], kilitle):
        if kalan <= 0:
            break
        al = min(kalan, int(_net_kalan(ie).to_integral_value(rounding=ROUND_CEILING)))
        if al > 0:
            sonuc.append((ie, al))
            kalan -= al
    if kalan > 0:
        sonuc.append((None, kalan))
    return sonuc


def _planla(istasyon_id, profil: Stok, boy_sayisi: int, satirlar, kilitle: bool) -> dict:
    """Doğrulanmış plan: bütçe + parça satırları + (parça × emir) ayrımları ve profil payları. Hiçbir şey yazmaz (``kilitle`` yalnız emir satırı kilidi)."""
    a = ayarlar()
    boy_mm = profil.boy_uzunluk_mm
    cozulen = _satirlari_coz(istasyon_id, profil, satirlar)
    b = butce(boy_sayisi, boy_mm, a["testere_payi_mm"], a["cene_payi_mm"], [(adet, c["parca_mm"]) for c, adet in cozulen])
    _butce_dogrula(b, boy_sayisi, boy_mm, a["cene_payi_mm"])
    ayrimlar = []
    for aday, adet in cozulen:
        for ie, n in _dagit(aday, adet, kilitle):
            ayrimlar.append({"aday": aday, "ie": ie, "adet": n, "agirlik": Decimal(n) * aday["parca_mm"]})
    toplam_agirlik = sum((x["agirlik"] for x in ayrimlar), S)
    payi = {}
    for i, x in enumerate(ayrimlar):
        payi[i] = yuvarla(Decimal(boy_sayisi) * x["agirlik"] / toplam_agirlik, 6)
    en_buyuk = max(range(len(ayrimlar)), key=lambda i: (ayrimlar[i]["agirlik"], -i))
    payi[en_buyuk] += Decimal(boy_sayisi) - sum(payi.values(), S)                                        # yuvarlama farkı: toplam tam boy sayısı
    for i, x in enumerate(ayrimlar):
        x["profil_payi"] = payi[i]
    return {"butce": b, "satirlar": cozulen, "ayrimlar": ayrimlar, "ayar": a, "boy_mm": boy_mm}


def onizleme(*, istasyon_id, profil_id, boy_sayisi, satirlar) -> dict:
    """KURU çalıştırma (hiçbir şey yazılmaz): bütçe/fire dökümü + parça × emir dağılımı + her ayrımın profil payı. Kural ihlalinde ``KarmaKesimHatasi``."""
    profil = _profil_coz(profil_id)
    return _planla(istasyon_id, profil, _tam_sayi(boy_sayisi, "Kullanılan boy sayısı"), satirlar, kilitle=False) | {"profil": profil}


# --- kayıt ---

def _sonraki_sira(yil) -> int:
    return (KarmaKesim.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"] or 0) + 1


@transaction.atomic
def karma_kes(*, istasyon_id, profil_id, depo_id, boy_sayisi, satirlar, tarih=None, aciklama="", kullanici=None, personel=None, mesai_kaydi=None) -> KarmaKesim:
    """Karma kesimi tek işlemde yazar ve ONAYLAR (herhangi bir hata — bütçe, stok yetersizliği… — hiçbir şey bırakmaz). ``satirlar``: [(parça stok pk, adet)].
    Boy sayısı tam sayı olmalıdır; profil girdisi kayıtlara uzunluk oranıyla dağılır (toplam = boy sayısı)."""
    istasyon = IsIstasyonu.objects.filter(pk=istasyon_id, silindi=False).first()
    if istasyon is None:
        raise KarmaKesimHatasi("İstasyon bulunamadı.")
    if istasyon.fason:
        raise KarmaKesimHatasi("Fason istasyonunda karma kesim yapılamaz.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise KarmaKesimHatasi("Depo bulunamadı.")
    if depo.fason_cari_id:
        raise KarmaKesimHatasi("Fason deposunda karma kesim yapılamaz; fabrika deposunu seçin.")
    tarih = tarih or tr_bugun()
    if tarih > tr_bugun():
        raise KarmaKesimHatasi("Tarih bugünden ileri olamaz.")
    profil = _profil_coz(profil_id)
    n = _tam_sayi(boy_sayisi, "Kullanılan boy sayısı")
    if personel is None and kullanici is not None and getattr(kullanici, "pk", None):
        from core.models import Personel
        personel = Personel.objects.filter(kullanici_id=kullanici.pk, silindi=False).first()
    plan = _planla(istasyon.pk, profil, n, satirlar, kilitle=True)
    b = plan["butce"]

    karma = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(tarih.year)
                karma = KarmaKesim.objects.create(
                    yil=tarih.year, sira=sira, no=f"KK-{tarih.year}-{sira:04d}", istasyon=istasyon, profil=profil, depo=depo, tarih=tarih, boy_sayisi=n,
                    boy_mm=plan["boy_mm"], testere_payi_mm=plan["ayar"]["testere_payi_mm"], cene_payi_mm=plan["ayar"]["cene_payi_mm"], toplam_adet=b["toplam_adet"],
                    parca_mm=b["parca_mm"].quantize(MM), testere_mm=b["testere_mm"].quantize(MM), cene_mm=b["cene_mm"].quantize(MM), uc_mm=b["uc_mm"].quantize(MM),
                    fire_mm=b["fire_mm"].quantize(MM), fire_orani=b["fire_orani"].quantize(Decimal("0.00000001")), personel=personel, mesai_kaydi=mesai_kaydi,
                    aciklama=(aciklama or "").strip(), created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if karma is None:
        raise KarmaKesimHatasi("Karma kesim numarası üretilemedi; tekrar deneyin.")
    for aday, adet in plan["satirlar"]:
        KarmaKesimSatir.objects.create(karma=karma, stok=aday["stok"], adet=adet, parca_mm=aday["parca_mm"], created_by=kullanici, updated_by=kullanici)

    for x in plan["ayrimlar"]:
        ie, aday = x["ie"], x["aday"]
        kayit = uretim.operasyon_kaydi_olustur(
            operasyon_id=aday["operasyon"].pk, depo_id=depo.pk, tarih=tarih, hedef_cikti_miktari=Decimal(x["adet"]),
            uretim_emri=ie.uretim_emri if ie else None, istasyon_emri=ie, cikti_varyant=ie.cikti_varyant if ie else None,
            aciklama=f"Karma kesim {karma.no}", kullanici=kullanici, personel=personel, mesai_kaydi=mesai_kaydi, karma=karma)
        satir = next((g for g in uretim.kaydi_girdi_satirlari(kayit) if g.girdi_id == profil.pk), None)
        if satir is None:
            raise KarmaKesimHatasi(f"{aday['stok'].kod}: kaydın profil girdisi bulunamadı.")
        satir.planlanan_miktar = satir.gerceklesen_miktar = x["profil_payi"]
        satir.updated_by = kullanici
        satir.save(update_fields=["planlanan_miktar", "gerceklesen_miktar", "updated_by", "updated_at"])
        try:
            uretim.operasyon_kaydi_onayla(kayit, kullanici=kullanici)
        except UretimHatasi as e:
            raise KarmaKesimHatasi(uretim._eksik_mesaji(str(e)))
    return KarmaKesim.objects.get(pk=karma.pk)


@transaction.atomic
def karma_geri_al(karma: KarmaKesim, kullanici=None) -> KarmaKesim:
    """Karma kesimin TÜM kayıtlarını birlikte geri alır (stok hareketleri, maliyet fişleri, ÜS ayırması/tamamlanan, daraltılan emir planlananı); biri alınamazsa
    (çıktı başka üretimde kullanılmış vb.) hiçbir şey değişmez."""
    karma = KarmaKesim.objects.select_for_update().get(pk=karma.pk)
    if karma.silindi:
        return karma
    for k in karma.kayitlar.filter(silindi=False).order_by("-pk"):
        uretim.operasyon_kaydi_sil(OperasyonKaydi.objects.get(pk=k.pk), kullanici=kullanici, onayli_geri_al=True)
    simdi = timezone.now()
    karma.satirlar.filter(silindi=False).update(silindi=True, silindi_at=simdi)
    karma.silindi, karma.silindi_at, karma.updated_by = True, simdi, kullanici
    karma.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return karma
