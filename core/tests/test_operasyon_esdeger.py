"""EŞDEĞERLİK REFERANSI (Operasyon ÜRET/PARÇALA geçişi): ÜRET tanımlı sabit bir zincirin ihtiyaç hesabı, ürün ağacı (malzeme/maliyet/karşılaştır/nerede
kullanılıyor), fason kesim listesi, operasyon listesi, üretim emri kayıtları ile operasyon kaydı onayı (stok, snapshot, maliyet payı, fiş) çıktıları,
geçişten ÖNCE alınmış ``golden/operasyon_esdeger.json`` ile BİREBİR karşılaştırılır. Referansta olan her alan aynı kalmalıdır (yeni alanlar eklenebilir).
Referansı yeniden yazmak için: ``GOLDEN_YAZ=1 python manage.py test core.tests.test_operasyon_esdeger`` (yalnızca bilinçli bir davranış değişikliğinde)."""
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from core.models import (
    Birim, Depo, FaturaTipi, HesapPlani, IsIstasyonu, Kategori, KategoriHesap, Kur, Operasyon, OperasyonKaydi, OperasyonKaydiCikti, Stok, StokHareket, YevmiyeFisi,
)
from core.services import urun_agaci as ua
from core.services.fason import fason_listesi_operasyondan
from core.services.hareket import hareket_ekle
from core.services.uretim import (
    emir_hedef_cikti, ihtiyac_hesapla, kaydi_girdi_satirlari, operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_kaydi_sil, operasyon_liste, operasyon_olustur,
    operasyon_serileri, uretim_emri_olustur,
)

D = Decimal
GOLDEN = Path(__file__).parent / "golden" / "operasyon_esdeger.json"
BUGUN = date(2026, 10, 9)


def serilestir(x):
    """Model/Decimal içeren sonucu JSON'a çevirir (pk yerine kod; Decimal normalize edilmiş metin)."""
    if isinstance(x, Decimal):
        return format(x.normalize(), "f")
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    if isinstance(x, float):
        return round(x, 9)
    if isinstance(x, Operasyon):
        return "OP:" + x.cikti.kod
    if isinstance(x, (Stok, IsIstasyonu)):
        return x.kod
    if isinstance(x, dict):
        return {str(serilestir(k)): serilestir(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple, set, frozenset)):
        liste = [serilestir(v) for v in x]
        return sorted(liste, key=lambda v: json.dumps(v, sort_keys=True)) if isinstance(x, (set, frozenset)) else liste
    if hasattr(x, "_meta"):
        return f"{x._meta.model_name}:{x}"
    return str(x)


def esit_mi(beklenen, gercek, yol="$"):
    """Referanstaki her anahtar/değer gerçekte AYNEN bulunmalı (gerçekte fazladan dict anahtarı olabilir). Hata metni döner ('' = eşit)."""
    if isinstance(beklenen, dict):
        if not isinstance(gercek, dict):
            return f"{yol}: sözlük bekleniyordu, {type(gercek).__name__} geldi"
        for k, v in beklenen.items():
            if k not in gercek:
                return f"{yol}.{k}: anahtar yok"
            h = esit_mi(v, gercek[k], f"{yol}.{k}")
            if h:
                return h
        return ""
    if isinstance(beklenen, list):
        if not isinstance(gercek, list) or len(beklenen) != len(gercek):
            return f"{yol}: liste uzunluğu {len(beklenen)} ≠ {len(gercek) if isinstance(gercek, list) else '?'}"
        for i, (b, g) in enumerate(zip(beklenen, gercek)):
            h = esit_mi(b, g, f"{yol}[{i}]")
            if h:
                return h
        return ""
    return "" if beklenen == gercek else f"{yol}: {beklenen!r} ≠ {gercek!r}"


class EsdegerlikTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.boy = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        cls.mt = Birim.objects.create(ad="METRE", kisa_ad="MT", ondalik=3)
        tip = FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS")
        kats = {}
        for kod, hesap_kodu in (("H", "150.10"), ("P", "151.10"), ("M", "152.10")):
            h = HesapPlani.objects.create(hesap_kodu=hesap_kodu, hesap_adi=f"TEST {hesap_kodu}", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
            kats[kod] = Kategori.objects.create(kod="E" + kod, ad="EŞDEĞERLİK " + kod)
            KategoriHesap.objects.create(kategori=kats[kod], hesap=h, fatura_tipi=tip)
        cls.kesim = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        cls.montaj = IsIstasyonu.objects.create(kod="70", ad="MONTAJ")
        cls.depo = Depo.objects.create(kod="MRK", ad="MERKEZ")
        Kur.objects.create(tarih=BUGUN, usd_alis=D("40"), eur_alis=D("44"))

        def stok(kod, birim=None, **kw):
            kat = kats["H" if kod.startswith("150") else "M" if kod.startswith("152") else "P"]
            return Stok.objects.create(kod=kod, ad=kod.lower(), kategori=kat, uretim_birimi=birim or cls.adet, fatura_birimi=birim or cls.adet,
                                       uretim_urunu=kw.pop("uretim", True), satinalma_urunu=kw.pop("satinalma", False), satis_urunu=kw.pop("satis", False), **kw)
        cls.p1 = stok("150-10-0001", cls.boy, uretim=False, satinalma=True)
        cls.p2 = stok("150-10-0002", cls.boy, uretim=False, satinalma=True)
        cls.m1 = stok("150-20-0001", cls.mt, uretim=False, satinalma=True)
        cls.h1 = stok("150-30-0001", uretim=False, satinalma=True, alis_fiyati=D("2.5"), alis_fiyati_pb="TRY")
        cls.a1, cls.b1, cls.y1 = stok("151-10-0001"), stok("151-10-0002"), stok("151-10-0003")
        cls.c1, cls.d1, cls.e1 = stok("151-20-0001"), stok("151-20-0002"), stok("151-30-0001")
        cls.u1, cls.u2 = stok("152-10-0001", satis=True), stok("152-10-0002", satis=True)
        cls.u3, cls.u4 = stok("152-22-0001", satis=True), stok("152-22-0002", satis=True)
        Stok.objects.filter(pk=cls.p1.pk).update(ort_maliyet_try=D("640"), ort_maliyet_usd=D("16"))
        Stok.objects.filter(pk=cls.p2.pk).update(ort_maliyet_try=D("700"), ort_maliyet_usd=D("17.5"))
        Stok.objects.filter(pk=cls.m1.pk).update(ort_maliyet_try=D("12"), ort_maliyet_usd=D("0.3"))
        o = operasyon_olustur
        o(istasyon_id=cls.kesim.pk, cikti_id=cls.a1.pk, cikti_miktar=D("18"), satirlar=[(cls.p1, D("1"))])                                    # tam boy (BOY girdi)
        o(istasyon_id=cls.kesim.pk, cikti_id=cls.b1.pk, cikti_miktar=D("3"), satirlar=[(cls.p2, D("1"))], boy_mm=D("1292.60"),
          yan_ciktilar=[(cls.y1, D("1"), D("1063.53"))])                                                                                     # yan çıktılı, tam boy
        o(istasyon_id=cls.kesim.pk, cikti_id=cls.c1.pk, cikti_miktar=D("4"), satirlar=[(cls.m1, D("2.5"))])                                  # kesirli (MT)
        o(istasyon_id=cls.kesim.pk, cikti_id=cls.d1.pk, cikti_miktar=D("1"), satirlar=[(cls.p1, D("0.015625"))], tam_boy=False)              # BOY ama tam boy kapalı
        o(istasyon_id=cls.montaj.pk, cikti_id=cls.e1.pk, cikti_miktar=D("1"), satirlar=[(cls.a1, D("2"))])
        o(istasyon_id=cls.montaj.pk, cikti_id=cls.u1.pk, cikti_miktar=D("1"), satirlar=[(cls.e1, D("1")), (cls.h1, D("2"))])
        o(istasyon_id=cls.montaj.pk, cikti_id=cls.u2.pk, cikti_miktar=D("1"), satirlar=[(cls.a1, D("1")), (cls.b1, D("3")), (cls.y1, D("1"))])
        o(istasyon_id=cls.montaj.pk, cikti_id=cls.u3.pk, cikti_miktar=D("1"), satirlar=[(cls.c1, D("1")), (cls.d1, D("2")), (cls.a1, D("1"))])
        o(istasyon_id=cls.montaj.pk, cikti_id=cls.u4.pk, cikti_miktar=D("1"), satirlar=[(cls.e1, D("2")), (cls.b1, D("1"))])

    def sonuc(self):
        s = {}
        k = [(self.u1, D("5")), (self.u2, D("3")), (self.u3, D("7")), (self.u4, D("2"))]
        graf = ua.graf_yukle(BUGUN)
        s["ihtiyac_varsayilan"] = ihtiyac_hesapla(k)
        s["ihtiyac_graf"] = ihtiyac_hesapla(k, graf=graf)
        s["ihtiyac_kesirli"] = ihtiyac_hesapla(k, boy_yuvarla=False)
        s["ihtiyac_maliyet_gorunumu"] = ihtiyac_hesapla([(self.u2, D("1"))], boy_yuvarla=False, pay_dus=True)
        s["ihtiyac_ara_stok"] = ihtiyac_hesapla([(self.b1, D("4")), (self.a1, D("19"))])
        s["birim_tuketim"] = {u.kod: {v["stok"].kod: v for v in graf.birim_tuketim(u).values()} for u in (self.u1, self.u2, self.u3, self.u4)}   # pk değil kod
        s["kokler"] = graf.kokler()
        s["malzeme"] = ua.urun_malzeme(graf, self.u2, D("3"))
        s["maliyet"] = {u.kod: ua.urun_maliyet(graf, u, D("2")) for u in (self.u2, self.u3)}
        s["nerede_p1"] = ua.nerede_kullaniliyor(graf, self.p1)
        s["nerede_y1"] = ua.nerede_kullaniliyor(graf, self.y1)
        s["karsilastir_miktar"] = ua.karsilastir(graf, [self.u1, self.u2, self.u3, self.u4], "miktar")
        s["karsilastir_maliyet"] = ua.karsilastir(graf, [self.u1, self.u2, self.u3, self.u4], "maliyet", "TL")
        s["fason_listesi"] = fason_listesi_operasyondan([(self.u2, 5), (self.u3, 7)])
        s["operasyon_serileri"] = {Operasyon.objects.get(pk=pk).cikti.kod: v for pk, v in operasyon_serileri().items()}
        liste = operasyon_liste()
        s["operasyon_liste"] = {"satirlar": [{k: r[k] for k in ("op", "seri", "tam_boy", "yan_sayisi", "kullanan_sayisi", "bitmis_urun", "ozet")} for r in liste["satirlar"]],
                                "ozet": liste["ozet"], "seri_dagilimi": liste["seri_dagilimi"]}
        s["operasyon_liste_yan"] = [r["op"] for r in operasyon_liste(yan_cikti=True)["satirlar"]]
        s["operasyon_liste_tam"] = [r["op"] for r in operasyon_liste(tam_boy=True)["satirlar"]]
        # üretim emri: plan satırlarından açılan kayıtlar
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.u3.pk, "hedef_miktar": D("7")}, {"hedef_urun_id": self.u2.pk, "hedef_miktar": D("3")}],
                                   depo_id=self.depo.pk, tarih=BUGUN)
        # Emir artık taslak kayıt açmaz: istasyon emri hedefinden kayıt açılınca eski taslak kayıtla (hedef, girdi planı) BİREBİR aynı değerler (referans değişmedi)
        kayitlar = [operasyon_kaydi_olustur(operasyon_id=ie.operasyon_id, depo_id=self.depo.pk, tarih=BUGUN, hedef_cikti_miktari=emir_hedef_cikti(ie), uretim_emri=emir)
                    for ie in emir.istasyon_emirleri.select_related("operasyon__cikti")]
        s["emir_kayitlari"] = sorted([(k.operasyon.cikti.kod, k.hedef_cikti_miktari, [(g.girdi.kod, g.planlanan_miktar) for g in kaydi_girdi_satirlari(k).order_by("girdi__kod")])
                                      for k in kayitlar], key=lambda r: r[0])
        for k in kayitlar:
            operasyon_kaydi_sil(k)                                                                       # sonraki onay bölümünü etkilemesin
        # kayıt onayı: yan çıktılı + normal + kesirli
        hareket_ekle(stok_id=self.p2.pk, depo_id=self.depo.pk, tarih=BUGUN, tur=StokHareket.Tur.GIRIS, miktar=D("20"), giris_tutar_try=D("14000"), giris_tutar_usd=D("350"))
        hareket_ekle(stok_id=self.p1.pk, depo_id=self.depo.pk, tarih=BUGUN, tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("6400"), giris_tutar_usd=D("160"))
        hareket_ekle(stok_id=self.m1.pk, depo_id=self.depo.pk, tarih=BUGUN, tur=StokHareket.Tur.GIRIS, miktar=D("30"), giris_tutar_try=D("360"), giris_tutar_usd=D("9"))
        onay = {}
        for cikti, hedef in ((self.b1, D("7")), (self.a1, D("40")), (self.c1, D("10")), (self.d1, D("5"))):
            op = Operasyon.objects.get(cikti=cikti, silindi=False)
            k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=BUGUN, hedef_cikti_miktari=hedef)
            operasyon_kaydi_onayla(k)
            onay[cikti.kod] = {
                "hedef": k.hedef_cikti_miktari,
                "ciktilar": [(c.stok.kod, c.miktar, c.boy_mm, c.pay_orani, c.ana_mi) for c in OperasyonKaydiCikti.objects.filter(kayit=k, silindi=False).select_related("stok").order_by("stok__kod")],
                "hareketler": [(h.stok.kod, h.tur, h.miktar, h.tutar_try, h.tutar_usd) for h in StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).select_related("stok").order_by("stok__kod", "tur")],
                "fis": sorted((sat.hesap_id, sat.borc, sat.alacak) for sat in k.fis.satirlar.filter(silindi=False)) if k.fis_id else None}
        s["onay"] = onay
        s["stok_ortalama"] = {st.kod: (st.maliyet_miktar, st.ort_maliyet_try, st.ort_maliyet_usd) for st in Stok.objects.filter(kod__startswith="15").order_by("kod")}
        return serilestir(s)

    def test_esdegerlik(self):
        gercek = self.sonuc()
        if os.environ.get("GOLDEN_YAZ"):
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(gercek, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
            self.skipTest("referans yazıldı")
        beklenen = json.loads(GOLDEN.read_text(encoding="utf-8"))
        hata = esit_mi(beklenen, json.loads(json.dumps(gercek, sort_keys=True)))
        self.assertEqual(hata, "", hata)
