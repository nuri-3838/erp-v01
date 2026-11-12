"""GEÇMİŞ MENTEŞE TÜKETİMİNİ BOYALI MENTEŞEYE ÇEVİRME (tek seferlik, idempotent; bkz. core/management/commands/mentese_boyali_gecis.py).

Montaj ham menteşeyi (151-20-0014 SAĞ / 0015 SOL) tüketiyordu; gerçekte menteşe fasoncuda (Kaygun, istasyon 55 TOZ BOYA) boyanıp boyalı menteşe
(151-20-0016 / 0017) olarak dönüyor. Bu servis ``baslangic`` tarihinden itibaren ham menteşeyi tüketen TÜM onaylı montaj kayıtlarını bulur ve
her tüketim tarihi için:

1. montaj kaydının girdi satırı + bağlı stok çıkışı YERİNDE ham → boyalı kartına çevrilir (miktar, tarih, depo aynı; kayıt no, ÜS bağı, fiş aynı kalır);
2. aynı tarihte ham menteşe ana depodan fason deposuna transfer edilir (transfer maliyeti değiştirmez);
3. o tarihli, fasoncu carisine bağlı bir FASON DÖNÜŞ belgesi (FD) açılıp onaylanır: girdi fason depodan düşer, boyalı menteşe ana depoya girer
   (gelen adet = o gün tüketilen adet), fason bedeli (FasonFiyat) boyalının maliyetine eklenir; faturasız fasoncuda cariye tahakkuk fişi yazılır;
4. maliyetler baştan hesaplanır: boyalı kart, montaj çıktıları ve aşağı zincir (maliyet aktarım fişleri, irsaliye maliyet fişleri) güncellenir.

Tek atomik işlem; ``uygula=False`` ise GERİ ALINIR (kuru çalıştırma — raporlanan FD/fiş numaraları gerçek çalıştırmada da aynı olur).
Bitişte doğrulama: ham ve boyalı menteşenin her depodaki eldekisi değişmemiş (tüketim yalnız kartı değiştirir; geçmişte kalan ham boyatılıp boyalı stoğa alınmış olabilir), fason deposu 0, hiçbir gün/depo negatif değil, tahakkuk = Σ adet × fiyat,
kartlar sıfırdan hesapla eşit; biri tutmazsa MenteseGecisHatasi (işlem geri alınır). İdempotent: boyalıya çevrilmiş kayıt yeniden aday olmaz."""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.models import (
    Depo, FasonDonus, Operasyon, OperasyonKaydi, OperasyonKaydiGirdi, Stok, StokHareket, YevmiyeSatir,
)
from core.services import depo as depo_servis
from core.services import fason_donus as fd
from core.services import stok_fis, stok_ortalama
from core.services.depo_transfer import depo_transferi_yap
from core.services.fason import gecerli_fiyat

SIFIR = Decimal("0")
ESLEME = (("151-20-0014", "151-20-0016"), ("151-20-0015", "151-20-0017"))      # (ham, boyalı): SAĞ, SOL
VARSAYILAN_BASLANGIC = datetime.date(2026, 9, 25)
ISARET = "MENTEŞE BOYALI GEÇİŞİ"                                              # transfer ve FD açıklamasında iz


class MenteseGecisHatasi(ValueError):
    """Çevirme kuralı ihlali (Türkçe mesaj); işlem geri alınır."""


def _stoklar():
    kodlar = [k for cift in ESLEME for k in cift]
    bul = {s.kod: s for s in Stok.objects.filter(kod__in=kodlar, silindi=False)}
    eksik = [k for k in kodlar if k not in bul]
    if eksik:
        raise MenteseGecisHatasi(f"Stok bulunamadı: {', '.join(eksik)}")
    for s in bul.values():
        if s.varyantli:
            raise MenteseGecisHatasi(f"{s.kod} varyantlı kart; bu çevirme yalnız varyantsız kartlar içindir.")
    return bul


def _boya_operasyonlari(stoklar):
    """{ham kod: (Operasyon, 1 adet boyalı için ham girdi miktarı)} — boyalıyı üreten tek aktif fason operasyon (girdisi yalnız ham menteşe)."""
    sonuc, cariler = {}, set()
    for ham, boyali in ESLEME:
        ops = list(Operasyon.objects.filter(silindi=False, cikti=stoklar[boyali]).select_related("istasyon__fasoncu"))
        if len(ops) != 1:
            raise MenteseGecisHatasi(f"{boyali} için tam 1 aktif operasyon tanımı olmalı (bulunan {len(ops)}).")
        op = ops[0]
        if not op.istasyon.fasoncu_id:
            raise MenteseGecisHatasi(f"{boyali} operasyonunun istasyonunda ({op.istasyon.kod}) fasoncu tanımlı değil.")
        cariler.add(op.istasyon.fasoncu_id)
        girdiler = list(op.girdiler.filter(silindi=False))
        if len(girdiler) != 1 or girdiler[0].girdi_id != stoklar[ham].pk:
            raise MenteseGecisHatasi(f"{boyali} operasyonunun tek girdisi {ham} olmalı.")
        sonuc[ham] = (op, girdiler[0].miktar / op.cikti_miktar)
    if len(cariler) != 1:
        raise MenteseGecisHatasi("Boyalı menteşe operasyonlarının fasoncusu tek bir cari olmalı.")
    return sonuc


def adaylar(stoklar, baslangic):
    """Ham menteşeyi tüketen, ``baslangic`` ve sonrası, onaylı, fasonsuz montaj kayıtlarının girdi satırları (tarih, pk sırasıyla)."""
    return list(OperasyonKaydiGirdi.objects.filter(
        silindi=False, kayit__silindi=False, kayit__durum=OperasyonKaydi.Durum.ONAYLI, kayit__tarih__gte=baslangic,
        kayit__fason_cari__isnull=True, kayit__fason_donus__isnull=True, gerceklesen_miktar__gt=0,
        girdi__in=[stoklar[h] for h, _b in ESLEME])
        .select_related("kayit__operasyon__istasyon", "girdi").order_by("kayit__tarih", "kayit__pk", "pk"))


def _bakiyeler(stoklar, depolar):
    """{(kod, depo kodu veya 'TOPLAM'): eldeki} — 4 kart için."""
    sonuc = {}
    for kod, s in stoklar.items():
        for d in depolar:
            g = StokHareket.objects.filter(stok=s, depo=d, silindi=False, tur=StokHareket.Tur.GIRIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
            c = StokHareket.objects.filter(stok=s, depo=d, silindi=False, tur=StokHareket.Tur.CIKIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
            sonuc[(kod, d.kod)] = g - c
        sonuc[(kod, "TOPLAM")] = sum((v for (k, dk), v in sonuc.items() if k == kod and dk != "TOPLAM"), SIFIR)
    return sonuc


def _negatif_gunler(stoklar, depolar):
    """[(kod, depo kodu, tarih, gün sonu bakiye)] — gün sonu bakiyesi negatif olan her kart/depo/gün."""
    sorun = []
    for kod, s in stoklar.items():
        for d in depolar:
            gunluk = defaultdict(lambda: SIFIR)
            for h in StokHareket.objects.filter(stok=s, depo=d, silindi=False).only("tarih", "tur", "miktar"):
                gunluk[h.tarih] += h.miktar if h.tur == StokHareket.Tur.GIRIS else -h.miktar
            toplam = SIFIR
            for t in sorted(gunluk):
                toplam += gunluk[t]
                if toplam < 0:
                    sorun.append((kod, d.kod, t, toplam))
    return sorun


def _cari_bakiye(cari):
    """Carinin muhasebe hesabındaki ALACAK − BORÇ (fiş silinmemiş)."""
    if not cari.muhasebe_kodu:
        return None
    t = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap__hesap_kodu=cari.muhasebe_kodu).aggregate(b=Sum("borc"), a=Sum("alacak"))
    return (t["a"] or SIFIR) - (t["b"] or SIFIR)


def _stok_degeri():
    return Stok.objects.filter(silindi=False).aggregate(t=Sum("maliyet_deger_try"))["t"] or SIFIR


def _mizan():
    """{hesap kodu: borç − alacak} (silinmemiş fişler) — önce/sonra farkı raporda görünür."""
    return {r["hesap__hesap_kodu"]: (r["b"] or SIFIR) - (r["a"] or SIFIR)
            for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).values("hesap__hesap_kodu").annotate(b=Sum("borc"), a=Sum("alacak"))}


def _kart_degerleri():
    return {s.kod: (s.maliyet_deger_try or SIFIR) for s in Stok.objects.filter(silindi=False).only("kod", "maliyet_deger_try")}


def _anlik(stoklar, depolar, cari):
    return {"bakiye": _bakiyeler(stoklar, depolar), "cari": _cari_bakiye(cari), "deger": _stok_degeri(), "mizan": _mizan(), "kartlar": _kart_degerleri()}


def gecis(*, baslangic=VARSAYILAN_BASLANGIC, uygula=False, kullanici=None) -> dict:
    """Tek transaction: tespit → yerinde çevirme → transfer + FD → maliyet → doğrulama → rapor; ``uygula=False`` ise GERİ ALINIR."""
    with transaction.atomic():
        rapor = _isle(baslangic, kullanici)
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    return rapor


def _isle(baslangic, kullanici):
    stoklar = _stoklar()
    boya = _boya_operasyonlari(stoklar)
    cari = boya[ESLEME[0][0]][0].istasyon.fasoncu
    fason_depo = depo_servis.fason_deposu(cari)
    if fason_depo is None:
        raise MenteseGecisHatasi(f"{cari.unvan} için bağlı fason deposu yok.")
    satirlar = adaylar(stoklar, baslangic)
    rapor = {"baslangic": baslangic, "cari": cari, "fason_depo": fason_depo, "stoklar": stoklar, "once": {}, "sonra": {}, "tarihler": [],
             "kayitlar": [], "uyarilar": [], "kontrol": [], "degisen_cikis": 0}
    if not satirlar:
        rapor["uyarilar"].append("Çevrilecek kayıt yok (hepsi zaten boyalıya çevrilmiş ya da aday yok).")
        return rapor
    # --- tespit + doğrulama (hiçbir şey yazmadan) ---
    hareket_of, ayirmali = {}, []
    for sat in satirlar:
        hs = list(StokHareket.objects.filter(operasyon_kaydi_girdi=sat, silindi=False, tur=StokHareket.Tur.CIKIS))
        if len(hs) != 1 or hs[0].stok_id != sat.girdi_id or hs[0].miktar != sat.gerceklesen_miktar or hs[0].kaynak != StokHareket.Kaynak.URETIM:
            raise MenteseGecisHatasi(f"{sat.kayit.no}: girdi satırının stok çıkışı beklenen biçimde değil (tek, aynı kart/miktar, üretim kaynaklı).")
        if hs[0].varyant_id:
            raise MenteseGecisHatasi(f"{sat.kayit.no}: varyantlı çıkış; bu çevirme varyantsız kartlar içindir.")
        hareket_of[sat.pk] = hs[0]
        if sat.ayrilan_dusen:
            ayirmali.append(sat)
    if ayirmali:
        rapor["uyarilar"].append(
            f"{len({s.kayit_id for s in ayirmali})} montaj kaydının {len(ayirmali)} girdi satırında 'ayırmadan düşen' miktar var (toplam {sum((s.ayrilan_dusen for s in ayirmali), SIFIR).normalize():f}): "
            "ÜS ayırma kayıtlarına DOKUNULMAZ (ayırma o gün ham menteşeden düşmüştü); yalnız bu kayıtlar ileride geri alınırsa ayırma boyalı karta geri eklenir.")
    depo_idleri = {h.depo_id for h in hareket_of.values()}
    if len(depo_idleri) != 1:
        raise MenteseGecisHatasi("Montaj girdi çıkışları tek bir depodan olmalı.")
    ana_depo = Depo.objects.get(pk=depo_idleri.pop())
    depolar = [ana_depo, fason_depo]
    once = _anlik(stoklar, depolar, cari)
    rapor["once"] = once
    gunluk = defaultdict(lambda: defaultdict(lambda: SIFIR))                  # {tarih: {ham kod: adet}}
    for sat in satirlar:
        gunluk[sat.kayit.tarih][sat.girdi.kod] += sat.gerceklesen_miktar
    toplam_adet = {h: sum((g[h] for g in gunluk.values()), SIFIR) for h, _b in ESLEME}
    fiyat = {}
    for ham, boyali in ESLEME:
        for tarih in gunluk:
            f = gecerli_fiyat(cari.pk, stoklar[boyali], tarih)
            if f is None:
                raise MenteseGecisHatasi(f"{boyali}: {tarih:%d.%m.%Y} için {cari.unvan} fason fiyatı yok (Fason Fiyatları; geçerlilik başlangıcı bu tarihten önce olmalı).")
            if f.para_birimi != "TRY":
                raise MenteseGecisHatasi(f"{boyali}: fason fiyatı TL değil ({f.para_birimi}); beklenen tahakkuk toplamı hesaplanamıyor.")
            fiyat[(boyali, tarih)] = f.birim_fiyat
    beklenen_tahakkuk = sum((gunluk[t][h] * fiyat[(b, t)] for t in gunluk for h, b in ESLEME), SIFIR)
    boyali_of = dict(ESLEME)
    # --- 1) YERİNDE ÇEVİRME: girdi satırı + stok çıkışı ham → boyalı ---
    degisen_kayitlar = {}
    for sat in satirlar:
        yeni = stoklar[boyali_of[sat.girdi.kod]]
        h = hareket_of[sat.pk]
        OperasyonKaydiGirdi.objects.filter(pk=sat.pk).update(girdi=yeni)
        StokHareket.objects.filter(pk=h.pk).update(stok=yeni)
        degisen_kayitlar[sat.kayit_id] = sat.kayit
        rapor["kayitlar"].append({"kayit": sat.kayit, "tarih": sat.kayit.tarih, "ham": sat.girdi.kod, "boyali": yeni.kod, "adet": sat.gerceklesen_miktar})
    # --- 2+3) her tarih: ham transfer + FD (SAĞ + SOL satırları) ---
    for tarih in sorted(gunluk):
        satir_listesi = []
        for ham, boyali in ESLEME:
            adet = gunluk[tarih].get(ham, SIFIR)
            if adet <= 0:
                continue
            depo_transferi_yap(stok_id=stoklar[ham].pk, kaynak_depo_id=ana_depo.pk, hedef_depo_id=fason_depo.pk, tarih=tarih, miktar=adet,
                               aciklama=ISARET, kullanici=kullanici)
            op, oran = boya[ham]
            satir_listesi.append((op.pk, adet / oran))                          # beklenen boyalı adedi = ham tüketimi / (ham girdi ÷ boyalı)
        donus = fd.donus_olustur(cari_id=cari.pk, depo_id=ana_depo.pk, tarih=tarih, satirlar=satir_listesi, aciklama=f"{ISARET} (geriye dönük)",
                                 onayla=True, kullanici=kullanici)
        donus.refresh_from_db()
        rapor["tarihler"].append({"tarih": tarih, "adetler": dict(gunluk[tarih]), "donus": donus, "fis": donus.tahakkuk_fis})
    # --- 4) maliyetler baştan: montaj çıktıları ve aşağı zincir ---
    for kayit in sorted(degisen_kayitlar.values(), key=lambda k: (k.tarih, k.pk)):
        kayit.refresh_from_db()
        for st in stok_fis.uretim_senkronla(kayit):
            stok_ortalama.yeniden_hesapla(st)
    for kod in [k for cift in ESLEME for k in cift]:
        rapor["degisen_cikis"] += len(stok_ortalama.yeniden_hesapla(stoklar[kod]))
    for k in rapor["kayitlar"]:
        k["fis"] = OperasyonKaydi.objects.select_related("fis").get(pk=k["kayit"].pk).fis
    # --- doğrulama ---
    sonra = _anlik(stoklar, depolar, cari)
    rapor["sonra"] = sonra
    kontrol = rapor["kontrol"]

    def kontrol_et(ad, tamam, ayrinti=""):
        kontrol.append((ad, bool(tamam), ayrinti))
        if not tamam:
            raise MenteseGecisHatasi(f"Doğrulama başarısız — {ad}: {ayrinti}")

    for kod in [k for cift in ESLEME for k in cift]:                              # ham VE boyalı: hiçbir depoda eldeki değişmez (tüketim yalnız kart değiştirir)
        for dk in sorted({d.kod for d in depolar}) + ["TOPLAM"]:
            kontrol_et(f"{kod} {dk} eldeki aynı", once["bakiye"][(kod, dk)] == sonra["bakiye"][(kod, dk)],
                       f"önce {once['bakiye'][(kod, dk)]} sonra {sonra['bakiye'][(kod, dk)]}")
    kontrol_et(f"fason deposu ({fason_depo.kod}) bakiyesi 0", all(sonra["bakiye"][(h, fason_depo.kod)] == 0 for h, _b in ESLEME))
    negatif = _negatif_gunler(stoklar, depolar)
    kontrol_et("hiçbir kart/depo/gün negatif değil", not negatif, "; ".join(f"{k} {d} {t:%d.%m.%Y} {b}" for k, d, t, b in negatif[:5]))
    tahakkuk = sum((sum((s.alacak for s in x["fis"].satirlar.filter(silindi=False)), SIFIR) for x in rapor["tarihler"] if x["fis"] is not None), SIFIR)
    rapor["tahakkuk_toplam"] = tahakkuk
    if cari.fason_faturasiz:
        kontrol_et(f"tahakkuk toplamı = Σ adet × fiyat ({beklenen_tahakkuk})", tahakkuk == beklenen_tahakkuk, f"fiş toplamı {tahakkuk}")
        kontrol_et("cari bakiyesi tahakkuk kadar arttı", sonra["cari"] - once["cari"] == tahakkuk, f"fark {sonra['cari'] - once['cari']}")
    for kod, s in stoklar.items():
        hs = stok_ortalama.kart_hareketleri(s)
        sapma = [h.pk for h, sn in zip(hs, stok_ortalama.hesapla(hs)) if (h.maliyet_durumu, h.tutar_try, h.sonrasi_deger_try) != (sn.durum, sn.tutar_try, sn.sonrasi_deger_try)]
        kontrol_et(f"{kod} kartı sıfırdan hesapla eşit", not sapma, f"{len(sapma)} hareket sapıyor")
    for k in rapor["kayitlar"]:
        kayit_girdi = k["kayit"].girdi_satirlari.filter(silindi=False, girdi__kod=k["boyali"]).count()
        if not kayit_girdi:
            kontrol_et(f"{k['kayit'].no} girdisi boyalıya çevrildi", False)
    rapor["toplam_adet"] = toplam_adet
    rapor["beklenen_tahakkuk"] = beklenen_tahakkuk
    return rapor


def rapor_metni(r: dict) -> str:
    """İnsan okuyabilir rapor: tarih bazında adetler + FD + tahakkuk fişi, değişen OP kayıtları, önce/sonra stok ve cari bakiyeleri, doğrulamalar."""
    def sayi(d):
        return "-" if d is None else format(Decimal(d).normalize(), "f")

    def fisno(f):
        return f"{f.yil}/{f.fis_no}" if f is not None else "-"

    c = r["cari"]
    s = [f"MENTEŞE BOYALI GEÇİŞİ — {'UYGULANDI' if r.get('uygulandi') else 'KURU ÇALIŞTIRMA (geri alındı)'}",
         f"Başlangıç: {r['baslangic']:%d.%m.%Y}    Fasoncu: {c.unvan} (cari {c.pk}, {c.kod}, faturasız={c.fason_faturasiz})    Fason deposu: {r['fason_depo'].kod} {r['fason_depo'].ad}", ""]
    for u in r["uyarilar"]:
        s.append(f"UYARI: {u}")
    if not r["tarihler"]:
        return "\n".join(s)
    s.append("TARİH BAZINDA (ham menteşe 150 → fason deposu transferi + fason dönüş + tahakkuk):")
    for x in r["tarihler"]:
        adet = ", ".join(f"{k}→{dict(ESLEME)[k]}: {sayi(v)}" for k, v in sorted(x["adetler"].items()))
        s.append(f"  {x['tarih']:%d.%m.%Y}  {x['donus'].no}  {adet}  tahakkuk fişi {fisno(x['fis'])}")
    s.append(f"  TOPLAM adet: " + ", ".join(f"{k} {sayi(v)}" for k, v in r["toplam_adet"].items()) + f"   tahakkuk {r.get('tahakkuk_toplam')} (beklenen {sayi(r['beklenen_tahakkuk'])})")
    s += ["", f"DEĞİŞEN MONTAJ KAYITLARI ({len(r['kayitlar'])} girdi satırı):"]
    for k in r["kayitlar"]:
        s.append(f"  {k['kayit'].no}  {k['tarih']:%d.%m.%Y}  {k['ham']} → {k['boyali']}  {sayi(k['adet'])}  maliyet aktarım fişi {fisno(k.get('fis'))}")
    s += ["", "ÖNCE / SONRA ELDEKİ:"]
    kodlar = [k for cift in ESLEME for k in cift]
    depo_kodlari = sorted({dk for (_k, dk) in r["once"]["bakiye"]})
    for kod in kodlar:
        s.append(f"  {kod}: " + "   ".join(f"{dk} {sayi(r['once']['bakiye'][(kod, dk)])}→{sayi(r['sonra']['bakiye'][(kod, dk)])}" for dk in depo_kodlari))
    s.append(f"  Cari bakiyesi (alacak−borç): {r['once']['cari']} → {r['sonra']['cari']}")
    s.append(f"  Toplam stok değeri (TL): {r['once']['deger']} → {r['sonra']['deger']}   (fark {r['sonra']['deger'] - r['once']['deger']})")
    kart_fark = [(k, r["once"]["kartlar"].get(k, SIFIR), v) for k, v in sorted(r["sonra"]["kartlar"].items()) if v != r["once"]["kartlar"].get(k, SIFIR)]
    s += ["", f"STOK DEĞERİ DEĞİŞEN KARTLAR ({len(kart_fark)}):"] + [f"  {k}: {a} → {b}  ({b - a:+})" for k, a, b in kart_fark]
    hesaplar = sorted(set(r["once"]["mizan"]) | set(r["sonra"]["mizan"]))
    miz_fark = [(h, r["once"]["mizan"].get(h, SIFIR), r["sonra"]["mizan"].get(h, SIFIR)) for h in hesaplar if r["once"]["mizan"].get(h, SIFIR) != r["sonra"]["mizan"].get(h, SIFIR)]
    s += ["", f"MİZAN (borç−alacak) DEĞİŞEN HESAPLAR ({len(miz_fark)}):"] + [f"  {h}: {a} → {b}  ({b - a:+})" for h, a, b in miz_fark]
    s += ["", f"Maliyet yeniden hesabında son turda değişen çıkış: {r['degisen_cikis']} (0 = yakınsadı)", "", "DOĞRULAMALAR:"]
    for ad, tamam, ayr in r["kontrol"]:
        s.append(f"  [{'OK' if tamam else 'HATA'}] {ad}" + (f" — {ayr}" if ayr and not tamam else ""))
    return "\n".join(s)
