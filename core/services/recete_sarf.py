"""REÇETE EKSİK SARF DÜZELTMESİ — operasyon tanımındaki bir girdi miktarı geçmişte eksik tanımlanmış (ör. A 5+1/6+1 montajında kesilmiş kayış
660 mm 1 adet yerine 2 adet) ve tanım sonradan düzeltilmişse, o tanımla ONAYLANMIŞ geçmiş kayıtların eksik tükettiği girdi için TEK SEFERLİK
düzeltme (``docs/stok_maliyet_duzelt_uygulandi_20261011.txt`` yaklaşımı: kuru çalıştırma → onay → uygula).

Kural (kullanıcı kararı, 2026-10-12):
1. TESPİT: güncel tanımda girdi miktarı > kayıttaki birim tüketim olan onaylı kayıtlar; eksik = çalıştırma × güncel miktar − fiilen tüketilen.
   (Çalıştırma = kayıttaki ana çıktı adedi / tanım çıktı miktarı.) Kayıtlara DOKUNULMAZ (girdi satırı/hareketi değişmez); düzeltme ayrı hareketlerdir
   ama ``operasyon_kaydi`` bağıyla kayda iliştirilir (ikinci koşuda eksik 0 → idempotent; üretim maliyet senkronu bu hareketi DIŞLAR; düzeltilmiş
   kayıt geri alınamaz).
2. GİRDİ STOĞU: eksik sarf kadar ÇIKIŞ (bugün tarihli, kayıt başına bir hareket, kaynak DUZELTME); değer = kaydın KENDİ TARİHİNDEKİ birim maliyet
   (kaydın o girdi çıkışının birim maliyeti; bilinmiyorsa düzeltme günündeki ortalama) — çıkış bu tutarla SABİT değerlenir (motorun açık tutarlı
   çıkış yolu), sonraki hareketler etkilenmez.
3. DAĞITIM (ORAN bazında, FIFO değil): o kayıttan çıkan ürünün (ürün + varyant) bugünkü akışına göre — stokta kalan / satılan (fatura, iade düşülmüş) /
   numune / üretimde tüketilen (sonraki kaydın çıktısına ZİNCİRLEME aynı kural) / sarf (karşı hesabına) / diğer çıkış (satış maliyeti sayılır, uyarı).
   Stokta kalan pay o kartın maliyetine EKLENİR: 0 ADETLİK "maliyet düzeltmesi" GİRİŞİ (kaynak DUZELTME, miktar 0, tutar = pay) — ortalama
   yükselir, eldeki miktar değişmez. Ara mamul aşamasında stokta kalan (151-99 paketsiz vb.) aynı şekilde o kartın maliyetine eklenir.
4. TEK YEVMİYE FİŞİ (tarih = düzeltme günü, kaynak STOK_DUZELTME, elle silinemez/düzenlenemez): stok hesapları BORÇ (stokta kalan paylar), 620/621/623
   BORÇ (satılan), numune gider hesabı BORÇ (numune), sarf karşı hesabı BORÇ; girdinin stok hesabı ALACAK (eksik sarfın tamamı). Tüm hareketler bu fişe
   bağlıdır (``StokHareket.fis``); fiş ya da hareketler tek tek silinemez.

Varsayılan KURU çalıştırma: her şey bir transaction içinde gerçekten yapılır, rapor alınır ve GERİ ALINIR. ``uygula=True`` kalıcı yazar.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.models import Depo, OperasyonGirdi, OperasyonKaydi, Stok, StokHareket, StokVaryant, YevmiyeFisi, YevmiyeSatir
from core.sayi import yuvarla
from core.services import hareket as hareket_servis
from core.services import stok_fis
from core.services import stok_ortalama as so
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur, kur_usd_birebir

SIFIR = Decimal("0")
GIRIS, CIKIS = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
K = StokHareket.Kaynak
KOVA_ADI = {"STOK": "stokta kalan", "SATIS": "satılan", "NUMUNE": "numune", "URETIM": "üretimde tüketilen", "SARF": "sarf", "DIGER": "diğer çıkış"}
MIZAN_HESAPLARI = ("150", "151", "152", "153", "620", "621", "623", "760")
FIS_KAYNAKLARI = (YevmiyeFisi.Kaynak.URETIM, YevmiyeFisi.Kaynak.STOK_SATIS, YevmiyeFisi.Kaynak.STOK_SARF, YevmiyeFisi.Kaynak.NUMUNE,
                  YevmiyeFisi.Kaynak.STOK_DUZELTME)


class ReceteDuzeltHatasi(ValueError):
    """Düzeltme kural ihlali / eksik veri (Türkçe mesaj)."""


@dataclass
class KayitSatiri:
    """Eksik sarflı onaylı kayıt."""
    kayit: OperasyonKaydi
    cikti: Stok
    varyant: StokVaryant | None
    uretilen: Decimal
    calistirma: Decimal
    tanim_miktar: Decimal          # güncel tanımdaki birim (1 çalıştırma) miktar
    beklenen: Decimal
    gercek: Decimal
    eksik: Decimal
    birim: Decimal
    birim_kaynagi: str             # "kayıt tarihi" | "güncel ortalama"
    tutar: Decimal
    hareket: StokHareket | None = None


@dataclass
class Pay:
    """Bir kartın (ürün + varyant) bir kovasına düşen pay."""
    stok: Stok
    varyant: StokVaryant | None
    kova: str
    miktar: Decimal
    toplam_akis: Decimal
    tutar: Decimal
    seviye: int
    hesap: str = ""                # fiş borç hesabı
    hareket: StokHareket | None = None
    uyari: str = ""


def _varyant_etiketi(v):
    return v.anahtar if v is not None else "-"


# === 1) Tespit =========================================================================

def tanim_miktarlari(girdi: Stok, operasyon_idler=None) -> dict:
    """{operasyon: 1 çalıştırmadaki güncel girdi miktarı} — girdiyi kullanan silinmemiş tanımlar (aynı kart TASI+SABIT iki satırsa toplanır)."""
    qs = OperasyonGirdi.objects.filter(girdi=girdi, silindi=False, operasyon__silindi=False).select_related("operasyon__cikti")
    if operasyon_idler:
        qs = qs.filter(operasyon_id__in=list(operasyon_idler))
    toplam = {}
    for g in qs:
        toplam[g.operasyon] = toplam.get(g.operasyon, SIFIR) + g.miktar
    return toplam


def _kayit_birim_maliyeti(girdi_cikislari) -> Decimal | None:
    """Kaydın o girdi çıkışlarının ağırlıklı birim maliyeti (motorun o tarihte yazdığı değer); maliyeti bilinmeyen/sıfırsa None."""
    bilinen = [h for h in girdi_cikislari if h.maliyet_durumu != StokHareket.MaliyetDurumu.YOK and (h.tutar_try or SIFIR) > 0]
    m = sum((h.miktar for h in bilinen), SIFIR)
    if m <= 0:
        return None
    return yuvarla(sum((h.tutar_try for h in bilinen), SIFIR) / m, 6)


def tespit(girdi: Stok, operasyon_idler=None) -> tuple[list[KayitSatiri], list[dict], dict]:
    """(eksik sarflı kayıtlar, atlanan kayıtlar [{kayit, neden}], {operasyon: güncel miktar})."""
    tanimlar = tanim_miktarlari(girdi, operasyon_idler)
    if not tanimlar:
        raise ReceteDuzeltHatasi(f"{girdi.kod} girdisini kullanan (silinmemiş) operasyon tanımı yok.")
    satirlar, atlanan = [], []
    kayitlar = (OperasyonKaydi.objects.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI, operasyon__in=[o.pk for o in tanimlar])
                .select_related("operasyon__cikti", "cikti_varyant", "depo").order_by("tarih", "pk"))
    for kayit in kayitlar:
        op = kayit.operasyon
        hs = list(StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False).select_related("stok", "varyant").order_by("pk"))
        ciktilar = [h for h in hs if h.tur == GIRIS]
        girdi_cikislari = [h for h in hs if h.tur == CIKIS and h.stok_id == girdi.pk]          # kaydın tüketimi + önceki düzeltme çıkışları
        kayit_cikislari = [h for h in girdi_cikislari if h.kaynak != K.DUZELTME]
        ana = next((h for h in ciktilar if h.stok_id == op.cikti_id), ciktilar[0] if ciktilar else None)
        if ana is None:
            atlanan.append({"kayit": kayit, "neden": "çıktı girişi yok"})
            continue
        calistirma = ana.miktar / op.cikti_miktar
        tanim_miktar = tanimlar[next(o for o in tanimlar if o.pk == op.pk)]
        beklenen = calistirma * tanim_miktar
        gercek = sum((h.miktar for h in girdi_cikislari), SIFIR)
        eksik = beklenen - gercek
        if eksik <= 0:
            atlanan.append({"kayit": kayit, "neden": f"tüketim tam ({gercek.normalize():f} ≥ beklenen {beklenen.normalize():f})"})
            continue
        birim, kaynak = _kayit_birim_maliyeti(kayit_cikislari), "kayıt tarihi"
        if birim is None:
            birim, kaynak = girdi.ort_maliyet_try, "güncel ortalama"
        if not birim or birim <= 0:
            raise ReceteDuzeltHatasi(f"{girdi.kod}: {kayit.no} için birim maliyet bilinmiyor (kayıtta da güncel ortalamada da yok).")
        satirlar.append(KayitSatiri(kayit=kayit, cikti=ana.stok, varyant=ana.varyant, uretilen=ana.miktar, calistirma=calistirma,
                                    tanim_miktar=tanim_miktar, beklenen=beklenen, gercek=gercek, eksik=eksik, birim=birim, birim_kaynagi=kaynak,
                                    tutar=yuvarla(eksik * birim, 2)))
    return satirlar, atlanan, tanimlar


# === 2) Oran bazında dağıtım ===========================================================

def kart_akisi(stok: Stok, varyant_id=None) -> dict:
    """Kartın (varyantlı kartta o varyantın) bugüne kadarki akışı: {'STOK','SATIS','NUMUNE','URETIM','SARF','DIGER'} miktarları + 'uretim' {kayıt: miktar},
    'sarf' {karşı hesap kodu: miktar}. Depo transferi bacakları dışarıda (net sıfır). Satış iadesi (ortalamadan giriş) satılandan düşer."""
    qs = StokHareket.objects.filter(stok=stok, silindi=False).exclude(kaynak=K.TRANSFER).select_related("operasyon_kaydi", "karsi_hesap")
    if stok.varyantli:
        qs = qs.filter(varyant_id=varyant_id)
    akis = {k: SIFIR for k in KOVA_ADI}
    uretim, sarf = {}, {}
    giris = cikis = SIFIR
    for h in qs:
        if h.tur == GIRIS:
            giris += h.miktar
            if h.kaynak == K.FATURA and h.giris_ortalama:
                akis["SATIS"] -= h.miktar
            continue
        cikis += h.miktar
        if h.kaynak == K.FATURA and h.giris_tutar_try is None:
            akis["SATIS"] += h.miktar
        elif h.kaynak == K.NUMUNE:
            akis["NUMUNE"] += h.miktar
        elif h.kaynak == K.URETIM and h.operasyon_kaydi_id and not h.operasyon_kaydi.silindi:
            akis["URETIM"] += h.miktar
            uretim[h.operasyon_kaydi] = uretim.get(h.operasyon_kaydi, SIFIR) + h.miktar
        elif h.kaynak == K.SARF and h.karsi_hesap_id:
            akis["SARF"] += h.miktar
            sarf[h.karsi_hesap.hesap_kodu] = sarf.get(h.karsi_hesap.hesap_kodu, SIFIR) + h.miktar
        else:
            akis["DIGER"] += h.miktar
    akis["STOK"] = max(giris - cikis, SIFIR)
    akis["SATIS"] = max(akis["SATIS"], SIFIR)
    akis["uretim"], akis["sarf"] = uretim, sarf
    return akis


def _oranla(toplam_tutar: Decimal, agirliklar: dict) -> dict:
    """Tutarı ağırlık oranında paylaştırır (2 ondalık); yuvarlama kalıntısı en büyük ağırlığa — toplam birebir eşit."""
    pozitif = {k: a for k, a in agirliklar.items() if a and a > 0}
    if not pozitif or toplam_tutar == 0:
        return {}
    toplam = sum(pozitif.values(), SIFIR)
    buyuk = max(pozitif, key=lambda k: pozitif[k])
    pay = {k: yuvarla(toplam_tutar * a / toplam, 2) for k, a in pozitif.items() if k != buyuk}
    pay[buyuk] = toplam_tutar - sum(pay.values(), SIFIR)
    return pay


def _kayit_ciktilari(kayit) -> list:
    return list(StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False, tur=GIRIS).select_related("stok", "varyant").order_by("pk"))


def dagit(stok: Stok, varyant, tutar: Decimal, *, seviye=0) -> list[Pay]:
    """``tutar``ı kartın akışına göre kovalara böler; üretimde tüketilen pay tüketen kaydın çıktılarına (maliyet payı oranında) geçip orada
    aynı kuralla yeniden dağıtılır. Akışı olmayan kart (hiç çıkmamış) → tamamı stokta kalan."""
    if seviye > 10:
        raise ReceteDuzeltHatasi(f"{stok.kod}: dağıtım zinciri çok derin (döngü olabilir).")
    varyant_id = getattr(varyant, "pk", varyant)
    akis = kart_akisi(stok, varyant_id)
    agirlik = {k: akis[k] for k in ("STOK", "SATIS", "NUMUNE", "URETIM", "SARF", "DIGER")}
    toplam_akis = sum(agirlik.values(), SIFIR)
    paylar = _oranla(tutar, agirlik) if toplam_akis > 0 else {"STOK": tutar}
    sonuc = []
    for kova in ("STOK", "SATIS", "NUMUNE", "SARF", "DIGER"):
        t = paylar.get(kova, SIFIR)
        if t == 0:
            continue
        if kova == "SARF":
            for hesap, pt in _oranla(t, akis["sarf"]).items():
                sonuc.append(Pay(stok, varyant, kova, akis["sarf"][hesap], toplam_akis, pt, seviye, hesap=hesap))
            continue
        p = Pay(stok, varyant, kova, agirlik.get(kova, SIFIR), toplam_akis, t, seviye)
        if kova == "DIGER":
            p.uyari = "manuel/iade çıkışı: satış maliyeti sayıldı"
        sonuc.append(p)
    uretim_payi = paylar.get("URETIM", SIFIR)
    if uretim_payi:
        sonuc.append(Pay(stok, varyant, "URETIM", agirlik["URETIM"], toplam_akis, uretim_payi, seviye))
        for kayit, pk_ in _oranla(uretim_payi, akis["uretim"]).items():
            ciktilar = _kayit_ciktilari(kayit)
            if not ciktilar:
                raise ReceteDuzeltHatasi(f"{kayit.no}: tüketen kaydın çıktı girişi yok; dağıtım yapılamadı.")
            agirlik_c = {c.pk: (c.tutar_try if all((x.tutar_try or SIFIR) > 0 for x in ciktilar) else c.miktar) for c in ciktilar}
            for cpk, pc in _oranla(pk_, agirlik_c).items():
                c = next(x for x in ciktilar if x.pk == cpk)
                sonuc.extend(dagit(c.stok, c.varyant, pc, seviye=seviye + 1))
    return sonuc


def birlestir(paylar: list[Pay]) -> list[Pay]:
    """Aynı (ürün, varyant, kova, hesap, seviye) payları tek satırda toplar — kayıt başına dağıtım aynı karta birden çok yoldan düşebilir
    (ör. iki paketleme kaydı aynı mamule); rapor ve 0 adetlik giriş birleşik yazılır. Miktar/akış aynı kartın akışıdır, toplanmaz."""
    sonuc = {}
    for p in paylar:
        key = (p.stok.pk, getattr(p.varyant, "pk", None), p.kova, p.hesap, p.seviye)
        if key in sonuc:
            sonuc[key].tutar += p.tutar
            sonuc[key].uyari = sonuc[key].uyari or p.uyari
        else:
            sonuc[key] = p
    return list(sonuc.values())


# === 3) Uygulama (fiş + hareketler) ====================================================

def _depo_sec(stok, varyant_id, tarih, miktar, tercih=None):
    """Çıkış için yeterli eldekisi olan depo (önce tercih edilen); miktar 0 (değer girişi) için en çok stoğu olan depo."""
    adaylar = []
    if tercih is not None:
        adaylar.append(tercih)
    adaylar += [d for d in Depo.objects.filter(silindi=False).order_by("pk") if tercih is None or d.pk != tercih.pk]
    en_cok, en_cok_m = None, None
    for d in adaylar:
        e = hareket_servis.eldeki_miktar(stok, d, tarih=tarih, varyant=varyant_id)
        if e >= miktar and miktar > 0:
            return d
        if en_cok_m is None or e > en_cok_m:
            en_cok, en_cok_m = d, e
    if miktar > 0:
        raise ReceteDuzeltHatasi(f"{stok.kod}: {tarih:%d.%m.%Y} tarihinde hiçbir depoda {miktar.normalize():f} adet yok (en çok {en_cok_m}).")
    if en_cok is None:
        raise ReceteDuzeltHatasi("Aktif depo yok.")
    return en_cok


def _mizan():
    return {k: so._mizan_bakiyesi(k) for k in MIZAN_HESAPLARI}


def _stok_degerleri():
    return {k["kod"]: (k["toplam_deger"], k["mizan"], k["ham_fark"]) for k in so.degerleme_raporu()["karsilastirma"]}


def _fis_toplamlari():
    return {r["fis_id"]: r["t"] for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, fis__kaynak__in=FIS_KAYNAKLARI)
            .values("fis_id").annotate(t=Sum("borc"))}


def duzelt(girdi_kodu: str, *, tarih: datetime.date, aciklama: str, operasyon_idler=None, uygula=False, kullanici=None) -> dict:
    """Tek transaction: tespit → dağıtım → fiş → hareketler → rapor; ``uygula=False`` ise GERİ ALINIR."""
    with transaction.atomic():
        rapor = _isle(girdi_kodu, tarih=tarih, aciklama=aciklama, operasyon_idler=operasyon_idler, kullanici=kullanici)
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    return rapor


def _isle(girdi_kodu, *, tarih, aciklama, operasyon_idler, kullanici):
    girdi = Stok.objects.filter(kod=girdi_kodu, silindi=False).select_related("kategori").first()
    if girdi is None:
        raise ReceteDuzeltHatasi(f"Stok bulunamadı: {girdi_kodu}")
    kur = kur_usd_birebir(tarih)
    if kur is None:
        raise ReceteDuzeltHatasi(f"{tarih:%d.%m.%Y} için USD kuru yok; Kurlar ekranından bu tarihi çekmeden düzeltme yapılamaz.")
    once_mizan, once_deger, once_fis = _mizan(), _stok_degerleri(), _fis_toplamlari()
    once_h = {h.pk: (h.tutar_try, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False)}
    girdi_once = (girdi.maliyet_miktar, girdi.maliyet_deger_try, girdi.ort_maliyet_try)

    satirlar, atlanan, tanimlar = tespit(girdi, operasyon_idler)
    if not satirlar:
        return {"girdi": girdi, "kayitlar": [], "atlanan": atlanan, "tanimlar": tanimlar, "paylar": [], "fis": None, "fis_satirlari": [],
                "toplam_eksik": SIFIR, "toplam_tutar": SIFIR, "girdi_once": girdi_once, "girdi_sonra": girdi_once, "mizan": [], "deger": [],
                "degisen": [], "fis_degisen": [], "kontrol": [], "tarih": tarih, "kur": kur}
    toplam_tutar = sum((s.tutar for s in satirlar), SIFIR)

    # Dağıtım: kayıt başına çıktı (ürün + varyant) → oran; aynı (ürün, varyant, kova, hesap) payları fişte birleşir
    paylar = []
    for s in satirlar:
        paylar.extend(dagit(s.cikti, s.varyant, s.tutar))
    net = {}
    girdi_hesabi = stok_fis.stok_hesabi_kodu(girdi)
    for p in [p for p in paylar if p.kova != "URETIM"]:
        if p.kova == "STOK":
            p.hesap = stok_fis.stok_hesabi_kodu(p.stok)
        elif p.kova in ("SATIS", "DIGER"):
            p.hesap = stok_fis.maliyet_hesabi_kodu(stok_fis.stok_hesabi_kodu(p.stok))
        elif p.kova == "NUMUNE":
            from core.services import numune as numune_servis
            try:
                p.hesap = numune_servis.gider_hesabi().hesap_kodu
            except numune_servis.NumuneHatasi as e:
                raise ReceteDuzeltHatasi(str(e))
        net[p.hesap] = net.get(p.hesap, SIFIR) + p.tutar
    paylar = birlestir(paylar)
    net[girdi_hesabi] = net.get(girdi_hesabi, SIFIR) - toplam_tutar
    fis_satirlari = [SatirGirdi(kod, "B", t) if t > 0 else SatirGirdi(kod, "A", -t) for kod, t in net.items() if t != 0]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=fis_satirlari, aciklama=aciklama, kaynak=YevmiyeFisi.Kaynak.STOK_DUZELTME, kur_usd=kur, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise ReceteDuzeltHatasi(f"Fiş kesilemedi: {e}")

    # Girdi çıkışları (kayıt başına, sabit tutarlı) — bu fişe bağlı
    try:
        for s in satirlar:
            depo = _depo_sec(girdi, None, tarih, s.eksik, tercih=s.kayit.depo)
            h = hareket_servis.hareket_ekle(stok_id=girdi.pk, depo_id=depo.pk, tarih=tarih, tur=CIKIS, miktar=s.eksik, kaynak=K.DUZELTME,
                                            giris_tutar_try=s.tutar, giris_tutar_usd=yuvarla(s.tutar / kur, 2),
                                            aciklama=f"{aciklama} ({s.kayit.no})", operasyon_kaydi=s.kayit, kullanici=kullanici)
            h.fis = fis
            h.save(update_fields=["fis", "updated_at"])
            s.hareket = h
        # Stokta kalan paylar → 0 adetlik maliyet düzeltmesi girişi (ürün + varyant başına birleşik)
        birlesik = {}
        for p in paylar:
            if p.kova == "STOK":
                birlesik.setdefault((p.stok.pk, getattr(p.varyant, "pk", None)), []).append(p)
        for (spk, vpk), grup in birlesik.items():
            tutar = sum((p.tutar for p in grup), SIFIR)
            stok = grup[0].stok
            depo = _depo_sec(stok, vpk, tarih, SIFIR)
            h = hareket_servis.maliyet_duzeltme_giris_ekle(stok=stok, depo=depo, tarih=tarih, tutar_try=tutar, tutar_usd=yuvarla(tutar / kur, 2),
                                                          fis=fis, aciklama=f"{aciklama} – {girdi.kod} payı", varyant=vpk, kullanici=kullanici)
            for p in grup:
                p.hareket = h
    except hareket_servis.HareketHatasi as e:
        raise ReceteDuzeltHatasi(str(e))

    girdi.refresh_from_db()
    fis_dokumu = [(s.hesap.hesap_kodu, s.hesap.hesap_adi, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False).order_by("id").select_related("hesap")]
    bagli = list(fis.stok_sarf_hareketleri.filter(silindi=False).order_by("pk").values_list("pk", flat=True))
    duzeltme_girisleri = []
    for h in {p.hareket.pk: p.hareket for p in paylar if p.kova == "STOK" and p.hareket is not None}.values():
        h.refresh_from_db()
        duzeltme_girisleri.append(h)
    sonra_mizan, sonra_deger, sonra_fis = _mizan(), _stok_degerleri(), _fis_toplamlari()
    yeni_pk = {s.hareket.pk for s in satirlar} | {p.hareket.pk for p in paylar if p.hareket is not None}
    degisen = []
    for h in StokHareket.objects.filter(silindi=False).exclude(pk__in=yeni_pk).select_related("stok"):
        o = once_h.get(h.pk)
        if o is not None and o != (h.tutar_try, h.maliyet_durumu):
            degisen.append({"hareket": h, "eski": o[0], "yeni": h.tutar_try, "eski_durum": o[1], "yeni_durum": h.maliyet_durumu})
    fis_degisen = sorted(pk for pk in set(once_fis) | set(sonra_fis) if once_fis.get(pk) != sonra_fis.get(pk) and pk != fis.pk)
    return {"girdi": girdi, "kayitlar": satirlar, "atlanan": atlanan, "tanimlar": tanimlar, "paylar": paylar, "fis": fis, "fis_satirlari": fis_satirlari,
            "fis_dokumu": fis_dokumu, "bagli_hareketler": bagli, "duzeltme_girisleri": duzeltme_girisleri,
            "toplam_eksik": sum((s.eksik for s in satirlar), SIFIR), "toplam_tutar": toplam_tutar, "tarih": tarih, "kur": kur,
            "girdi_once": girdi_once, "girdi_sonra": (girdi.maliyet_miktar, girdi.maliyet_deger_try, girdi.ort_maliyet_try),
            "mizan": [(k, once_mizan[k], sonra_mizan[k], sonra_mizan[k] - once_mizan[k]) for k in MIZAN_HESAPLARI],
            "deger": [(k, once_deger.get(k, (SIFIR, SIFIR, SIFIR))[0], sonra_deger.get(k, (SIFIR, SIFIR, SIFIR))[0]) for k in ("150", "151", "152", "153")],
            "kontrol": [(k, sonra_deger[k][0], sonra_deger[k][1], sonra_deger[k][2], once_deger[k][2]) for k in ("150", "151", "152", "153") if k in sonra_deger],
            "degisen": degisen, "fis_degisen": fis_degisen, "fisler": {pk: (once_fis.get(pk), sonra_fis.get(pk)) for pk in fis_degisen}}


# === Rapor metni =======================================================================

def _m(d):
    return f"{d.normalize():f}" if d is not None else "-"


def _tl(d):
    return f"{d:,.2f}" if d is not None else "-"


def rapor_metni(r: dict, *, girdi_kodu: str) -> str:
    """Komutun ekrana/dosyaya yazdığı KURU/UYGULANDI raporu (fiziki sayımla karşılaştırma için girdi stoğu dahil)."""
    g = r["girdi"]
    s = [f"{'UYGULANDI' if r['uygulandi'] else 'KURU ÇALIŞTIRMA (geri alındı, kalıcı değişiklik YOK)'} — reçete eksik sarf düzeltmesi: {girdi_kodu} {g.ad} "
         f"| tarih {r['tarih']:%d.%m.%Y} | USD kuru {r['kur']}"]
    s.append("\nTANIMLAR (güncel girdi miktarı, 1 çalıştırma):")
    for op, m in r["tanimlar"].items():
        s.append(f"  op {op.pk} [{op.istasyon.kod}] → {op.cikti.kod} {op.cikti.ad[:40]} (çıktı {_m(op.cikti_miktar)}): {girdi_kodu} × {_m(m)}")
    s.append(f"\n1) EKSİK SARFLI ONAYLI KAYITLAR ({len(r['kayitlar'])}):")
    s.append(f"  {'kayıt':<14} {'tarih':<10} {'ÜS':<5} {'çıktı ürün':<13} {'varyant':<34} {'üretilen':>8} {'beklenen':>8} {'tüketilen':>9} {'eksik':>6} {'birim TL':>10} {'tutar TL':>10}  birim kaynağı")
    for k in r["kayitlar"]:
        us = k.kayit.uretim_emri.no if k.kayit.uretim_emri_id else "-"
        s.append(f"  {k.kayit.no:<14} {k.kayit.tarih:%d.%m.%Y} {us:<5} {k.cikti.kod:<13} {_varyant_etiketi(k.varyant)[:34]:<34} {_m(k.uretilen):>8} {_m(k.beklenen):>8} "
                 f"{_m(k.gercek):>9} {_m(k.eksik):>6} {k.birim:>10.4f} {_tl(k.tutar):>10}  {k.birim_kaynagi}")
    s.append(f"  TOPLAM eksik sarf: {_m(r['toplam_eksik'])} adet = {_tl(r['toplam_tutar'])} TL")
    if r["atlanan"]:
        s.append(f"  Atlanan kayıtlar ({len(r['atlanan'])}): " + "; ".join(f"{a['kayit'].no} ({a['neden']})" for a in r["atlanan"]))
    if not r["kayitlar"]:
        s.append("\nEksik sarflı kayıt yok; yapılacak düzeltme yok.")
        return "\n".join(s)
    s.append(f"\n2) GİRDİ STOĞU {girdi_kodu} — düzeltme ÇIKIŞLARI (bugün tarihli, kayıt başına, sabit tutarlı):")
    for k in r["kayitlar"]:
        h = k.hareket
        s.append(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {h.depo.kod} ÇIKIŞ {_m(h.miktar)} × {k.birim:.4f} = {_tl(h.tutar_try)} TL  ({k.kayit.no})")
    o, n = r["girdi_once"], r["girdi_sonra"]
    s.append(f"  {girdi_kodu} STOĞU: önce {_m(o[0])} adet / {_tl(o[1])} TL (ort {o[2] or 0:.4f})  →  SONRA {_m(n[0])} adet / {_tl(n[1])} TL (ort {n[2] or 0:.4f})"
             f"   [fiziki sayımla karşılaştırılacak eldeki: {_m(n[0])} adet]")
    s.append("\n3) DAĞITIM (ürün / varyant / kova: akıştaki adet / toplam akış → pay TL; üretimde tüketilen pay alt satırlardaki çıktıya geçer):")
    for p in r["paylar"]:
        girinti = "  " + "    " * p.seviye
        ek = f"  [{p.hesap}]" if p.hesap else ""
        uy = f"  ⚠ {p.uyari}" if p.uyari else ""
        s.append(f"{girinti}{p.stok.kod} {p.stok.ad[:32]:<32} [{_varyant_etiketi(p.varyant)[:34]}] {KOVA_ADI[p.kova]:<18} {_m(p.miktar):>8} / {_m(p.toplam_akis):<8} → {_tl(p.tutar):>10} TL{ek}{uy}")
    s.append("  Özet (ürün / varyant / kova: akıştaki adet → pay TL [hesap]):")
    for p in r["paylar"]:
        if p.kova != "URETIM":
            s.append(f"    {p.stok.kod} [{_varyant_etiketi(p.varyant)[:34]}] {KOVA_ADI[p.kova]:<18} {_m(p.miktar):>8} adet  {_tl(p.tutar):>10} TL  [{p.hesap}]")
    f = r["fis"]
    s.append(f"\n4) YEVMİYE FİŞİ {f.yil}/{f.fis_no} {f.tarih:%d.%m.%Y} kaynak {f.kaynak} — {f.aciklama}")
    for kod, ad, b, a in r["fis_dokumu"]:
        s.append(f"  {kod:<10} {ad[:40]:<40} B {_tl(b):>12}  A {_tl(a):>12}")
    s.append("  Bağlı stok hareketleri: " + ", ".join(str(pk) for pk in r["bagli_hareketler"]))
    s.append("\n5) MALİYET DÜZELTMESİ GİRİŞLERİ (0 adet, yalnız değer; stokta kalan paylar):")
    for h in r["duzeltme_girisleri"]:
        s.append(f"  hareket {h.pk} {h.stok.kod} [{h.varyant.anahtar if h.varyant_id else '-'}] {h.depo.kod} +0 adet / {_tl(h.giris_tutar_try)} TL → kart ort {_m(h.sonrasi_ort_try)} TL")
    s.append("\nMİZAN FARKLARI (hesap: önce / sonra / fark)")
    for k, o_, n_, fk in r["mizan"]:
        s.append(f"  {k}: {o_:>16,.2f} / {n_:>16,.2f} / {fk:>12,.2f}{'  <--' if fk else ''}")
    s.append("\nSTOK DEĞERİ (grup: önce / sonra / fark)")
    for k, o_, n_ in r["deger"]:
        s.append(f"  {k}: {o_:>16,.2f} / {n_:>16,.2f} / {n_ - o_:>12,.2f}{'  <--' if n_ != o_ else ''}")
    s.append("\nKONTROL — stok değeri / mizan / fark (fark DEĞİŞMEMELİ: önceki fark)")
    for k, sd, mz, fk, fk_once in r["kontrol"]:
        s.append(f"  {k}: {sd:>16,.2f} / {mz:>16,.2f} / {fk:>12,.2f}  (önce {fk_once:,.2f}){'  = değişmedi' if fk == fk_once else '  <-- DEĞİŞTİ'}")
    s.append(f"\nDEĞERİ DEĞİŞEN BAŞKA HAREKETLER ({len(r['degisen'])}; bugün tarihli sonraki çıkışlar yeni ortalamayı alır):")
    for x in r["degisen"]:
        h = x["hareket"]
        s.append(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {h.stok.kod} {h.tur} {h.kaynak} {_m(h.miktar)}  {_tl(x['eski'])} -> {_tl(x['yeni'])} TL [{x['eski_durum']}->{x['yeni_durum']}]")
    s.append(f"DEĞİŞEN BAŞKA FİŞLER ({len(r['fis_degisen'])}): " + ", ".join(f"{pk}: {_tl(r['fisler'][pk][0])} -> {_tl(r['fisler'][pk][1])}" for pk in r["fis_degisen"]))
    if not r["uygulandi"]:
        s.append("\nKURU ÇALIŞTIRMA: kalıcı uygulamak için --uygula.")
    return "\n".join(s)
