"""Ödeme takvimi iki düzeltme (2026-11-02): (1) FATURA VADE PLANI (çoklu vade): doğrulama (toplam = ödenecek tutar), plan yoksa tek vade aynen, FIFO dağıtım (ödeme en erken vadeden düşer),
ödeme takvimi / yaşlandırma / vadesi geçenler plan satırlarıyla, fiş değişmez; (2) DÖVİZ CARİYE VERİLEN ÇEK (ara hesapta, ``ara_hesapta``) takvimde çift sayılmaz: çek toplamı kadar fatura açık
kalanı düşülür, fark kalırsa yalnız fark, çek ≥ borç ise fatura satırı çıkmaz, mutabakatta ayrı neden satırı. Tarih bağımlılığı yok: hesaplar ``bugun`` parametresiyle sabitlenir."""
import datetime
from decimal import Decimal

from django.http import QueryDict
from django.urls import reverse

from core.models import CekSenet, Fatura, YevmiyeSatir
from core.services import cari_rapor, fatura_odeme, fatura_vade_plani as vp, odeme_takvimi as ot
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur
from core.tests import test_fatura_liste as tfl

D = datetime.date
Dc = Decimal
BUGUN = D(2026, 4, 25)


def plan(*satirlar):
    return [{"vade": v, "tutar": t} for v, t in satirlar]


def uc_vade():
    """1.200 TL faturanın 3 eşit vadesi (nisan 2026)."""
    return plan((D(2026, 4, 10), "400,00"), (D(2026, 4, 20), "400,00"), (D(2026, 4, 30), "400,00"))


def takvim(bugun=BUGUN, **kw):
    return [r for r in ot.tum_satirlar(bugun)["satirlar"] if all(r.get(k) == v for k, v in kw.items())]


class PlanDogrulamaTest(tfl.ListeTaban):
    def test_plan_kaydedilir_vade_ilk_plan_satiri_fis_degismez(self):
        yok = self.a_fatura(no="VP-0", tarih=D(2026, 3, 9))
        fis_oncesi = sorted(yok.fis.satirlar.values_list("hesap_id", "borc", "alacak"))
        f = self.a_fatura(no="VP-1", vade_plani=uc_vade())
        f.refresh_from_db()
        self.assertEqual(f.vade_plani, [{"vade": "2026-04-10", "tutar": "400.00"}, {"vade": "2026-04-20", "tutar": "400.00"}, {"vade": "2026-04-30", "tutar": "400.00"}])
        self.assertEqual(f.vade_tarihi, D(2026, 4, 10))                                         # eski ekranlar için ilk vade
        self.assertEqual(sorted(f.fis.satirlar.values_list("hesap_id", "borc", "alacak")), fis_oncesi)   # aynı tutarlı fatura = aynı fiş satırları

    def test_toplam_ödenecek_tutara_esit_olmali_degilse_kaydedilmez(self):
        n = Fatura.objects.count()
        with self.assertRaisesMessage(FaturaHatasi, "eşit olmalı"):
            self.a_fatura(no="VP-2", vade_plani=plan((D(2026, 4, 10), "400,00"), (D(2026, 4, 20), "400,00")))        # 800 ≠ 1.200
        with self.assertRaisesMessage(FaturaHatasi, "fazla"):
            self.a_fatura(no="VP-3", vade_plani=plan((D(2026, 4, 10), "1.300,00")))
        self.assertEqual(Fatura.objects.count(), n)                                              # atomik: fatura da fiş de oluşmadı
        self.assertEqual(YevmiyeSatir.objects.filter(fis__faturalar__fatura_no="VP-2").count(), 0)

    def test_gecersiz_satirlar(self):
        for girdi in (plan((D(2026, 4, 10), "")), plan(("", "400,00")), plan((D(2026, 4, 10), "abc")), plan((D(2026, 4, 10), "0")), plan(("2026-13-40", "1200"))):
            with self.assertRaises(FaturaHatasi, msg=girdi):
                self.a_fatura(no="VP-4", vade_plani=girdi)
        with self.assertRaisesMessage(FaturaHatasi, "en fazla 36"):
            self.a_fatura(no="VP-5", vade_plani=plan(*[(D(2026, 4, 1) + datetime.timedelta(days=i), "1,00") for i in range(37)]))

    def test_plan_yok_bos_ya_da_none_tek_vade_aynen(self):
        a = self.a_fatura(no="VP-6", vade_tarihi=D(2026, 4, 12))
        b = self.a_fatura(no="VP-7", vade_tarihi=D(2026, 4, 12), vade_plani=[])
        c = self.a_fatura(no="VP-8", vade_tarihi=D(2026, 4, 12), vade_plani=plan(("", "")))      # tamamen boş satır atlanır
        for f in (a, b, c):
            f.refresh_from_db()
            self.assertEqual((f.vade_plani, f.vade_tarihi), (None, D(2026, 4, 12)))

    def test_tutar_biçimleri_ve_sira(self):
        f = self.a_fatura(no="VP-9", vade_plani=plan((D(2026, 4, 30), "600"), (D(2026, 4, 10), "600,00")))
        f.refresh_from_db()
        self.assertEqual([x["vade"] for x in f.vade_plani], ["2026-04-10", "2026-04-30"])        # vadeye göre sıralı saklanır

    def test_guncelle_plan_ekle_degistir_kaldir_none_dokunmaz(self):
        f = self.a_fatura(no="VP-10", vade_tarihi=D(2026, 4, 12))
        kw = dict(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=f.tarih, fatura_no="VP-10", satirlar=self._satir(), vade_tarihi=D(2026, 4, 12))
        fatura_guncelle(f, vade_plani=uc_vade(), **kw)                                           # fişi etkilemeyen düzenleme: yalnız başlık yolu
        f.refresh_from_db()
        self.assertEqual(len(f.vade_plani), 3)
        fatura_guncelle(f, **kw)                                                                 # vade_plani verilmedi: plana DOKUNULMAZ
        f.refresh_from_db()
        self.assertEqual(len(f.vade_plani), 3)
        with self.assertRaises(FaturaHatasi):                                                    # toplam tutmuyor: kayıt reddedilir, eski plan korunur
            fatura_guncelle(f, vade_plani=plan((D(2026, 5, 1), "100,00")), **kw)
        f.refresh_from_db()
        self.assertEqual(len(f.vade_plani), 3)
        fatura_guncelle(f, vade_plani=plan((D(2026, 5, 1), "700,00"), (D(2026, 6, 1), "500,00")), **kw)
        f.refresh_from_db()
        self.assertEqual([x["vade"] for x in f.vade_plani], ["2026-05-01", "2026-06-01"])
        self.assertEqual(f.vade_tarihi, D(2026, 5, 1))
        fatura_guncelle(f, vade_plani=[], **kw)                                                  # boş liste = planı kaldır; vade_tarihi formdaki değer
        f.refresh_from_db()
        self.assertEqual((f.vade_plani, f.vade_tarihi), (None, D(2026, 4, 12)))

    def test_tutar_degisince_plan_yeniden_girilmeli(self):
        f = self.a_fatura(no="VP-11", vade_plani=uc_vade())
        kw = dict(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=f.tarih, fatura_no="VP-11")
        with self.assertRaisesMessage(FaturaHatasi, "eşit olmalı"):
            fatura_guncelle(f, satirlar=self._satir("20", "100"), vade_plani=uc_vade(), **kw)   # 2.400 oldu, plan 1.200
        fatura_guncelle(f, satirlar=self._satir("20", "100"), vade_plani=plan((D(2026, 4, 10), "2400")), **kw)
        f.refresh_from_db()
        self.assertEqual(f.genel_toplam, Dc("2400.00"))

    def test_yalniz_kdv_tipinde_net_kdv_toplam(self):
        f = fatura_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="K-1", satirlar=self._satir(),
                           vade_plani=plan((D(2026, 4, 10), "100"), (D(2026, 4, 20), "100")))   # ödenecek = 200 (yalnız KDV)
        self.assertEqual(len(Fatura.objects.get(pk=f.pk).vade_plani), 2)

    def test_dogrula_ve_plan_oku_birim(self):
        self.assertEqual(vp.dogrula([], Dc("5")), [])
        self.assertEqual(vp.plan_oku(None), [])
        self.assertEqual(vp.plan_oku([{"vade": "bozuk", "tutar": "1"}]), [])                    # bozuk kayıt → plan yok
        self.assertEqual(vp.plan_oku([{"vade": "2026-04-10", "tutar": "5"}]), [(D(2026, 4, 10), Dc("5"))])
        self.assertIsNone(vp.dagit([], Dc("10"), Dc("10")))
        self.assertIsNone(vp.dagit([(D(2026, 4, 10), Dc("4"))], Dc("10"), Dc("10")))             # toplam tutmuyor → plan yok sayılır


class TakvimVadePlaniTest(tfl.ListeTaban):
    """(a) 3 vadeli fatura + kısmi ödeme."""

    def test_uc_vade_kismi_odeme_takvim_toplami(self):
        f = self.a_fatura(no="TP-1", vade_plani=uc_vade())
        self.odeme("500")                                                                       # en erken vadeden: 400 → 1. satır kapanır, 2. satırdan 100 düşer
        rows = sorted(takvim(kaynak="FATURA_ALIS"), key=lambda r: r["tarih"])
        self.assertEqual([(r["tarih"], r["tutar"], r["tl"]) for r in rows], [(D(2026, 4, 20), Dc("300.00"), Dc("300.00")), (D(2026, 4, 30), Dc("400.00"), Dc("400.00"))])
        self.assertEqual(sum(r["tl"] for r in rows), fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]["kalan"])   # 700 = listedeki kalan
        self.assertEqual([r["gecikmis"] for r in rows], [True, False])                            # bugün 25.04: 20.04 gecikmiş, 30.04 değil
        self.assertIn("vade planı 2/3", rows[0]["aciklama"])
        self.assertIn("vade planı 3/3", rows[1]["aciklama"])
        self.assertEqual({r["url"] for r in rows}, {("core:fatura_detay", f.pk)})

    def test_odeme_yok_uc_satir_her_biri_kendi_tarihinde(self):
        self.a_fatura(no="TP-2", vade_plani=uc_vade())
        rows = sorted(takvim(kaynak="FATURA_ALIS"), key=lambda r: r["tarih"])
        self.assertEqual([(r["tarih"], r["tutar"]) for r in rows], [(D(2026, 4, 10), Dc("400.00")), (D(2026, 4, 20), Dc("400.00")), (D(2026, 4, 30), Dc("400.00"))])
        self.assertEqual([r["gecikmis"] for r in rows], [True, True, False])                    # bugün 25.04: ilk ikisi gecikmiş
        g = ot.grupla(ot.tum_satirlar(BUGUN)["satirlar"], bugun=BUGUN, bas=BUGUN, bit=D(2026, 5, 31))
        self.assertEqual(g["gecikmis_cikis"], Dc("800.00"))                                      # gecikmiş = 10.04 + 20.04

    def test_odeme_tum_plani_asarsa_satir_kalmaz(self):
        self.a_fatura(no="TP-3", vade_plani=uc_vade())
        self.odeme("1200")
        self.assertEqual(takvim(kaynak="FATURA_ALIS"), [])

    def test_plan_olmayan_fatura_degismedi_ve_iki_fatura_fifo(self):
        eski = self.a_fatura(no="TP-4", tarih=D(2026, 3, 5), vade_tarihi=D(2026, 4, 1))          # planlı olmayan, daha eski
        self.a_fatura(no="TP-5", vade_plani=uc_vade())
        self.odeme("1300")                                                                       # eski faturayı 1.200 kapatır, planlıdan 100 düşer (en erken vadeden)
        rows = sorted(takvim(kaynak="FATURA_ALIS"), key=lambda r: r["tarih"])
        self.assertEqual([(r["belge_no"], r["tarih"], r["tutar"]) for r in rows],
                         [("TP-5", D(2026, 4, 10), Dc("300.00")), ("TP-5", D(2026, 4, 20), Dc("400.00")), ("TP-5", D(2026, 4, 30), Dc("400.00"))])
        self.assertNotIn(eski.pk, {r["url"][1] for r in rows})

    def test_plan_yoksa_tek_vade_satiri_aynen(self):
        self.a_fatura(no="TP-6", vade_tarihi=D(2026, 4, 12))
        (r,) = takvim(kaynak="FATURA_ALIS")
        self.assertEqual((r["tarih"], r["tutar"]), (D(2026, 4, 12), Dc("1200.00")))
        self.assertNotIn("vade planı", r["aciklama"])

    def test_plan_uyumsuzsa_yok_sayilir_tek_vade(self):
        f = self.a_fatura(no="TP-7", vade_tarihi=D(2026, 4, 12), vade_plani=uc_vade())
        Fatura.objects.filter(pk=f.pk).update(vade_plani=[{"vade": "2026-04-10", "tutar": "100.00"}])    # fatura tutarı başka yoldan değişmiş gibi bozuk plan
        (r,) = takvim(kaynak="FATURA_ALIS")
        self.assertEqual(r["tutar"], Dc("1200.00"))
        d = fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]
        self.assertTrue(d["plan_uyumsuz"])
        self.assertIsNone(d["taksitler"])

    def test_satis_faturasi_plani_giris_satirlari(self):
        fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="TS-1", satirlar=self._satir("10", "100"),
                       vade_plani=plan((D(2026, 5, 1), "600"), (D(2026, 6, 1), "600")))
        rows = sorted(takvim(kaynak="FATURA_SATIS"), key=lambda r: r["tarih"])
        self.assertEqual([(r["yon"], r["tarih"], r["tutar"]) for r in rows], [("GIRIS", D(2026, 5, 1), Dc("600.00")), ("GIRIS", D(2026, 6, 1), Dc("600.00"))])

    def test_mutabakat_320_toplami_planla_degismez(self):
        self.a_fatura(no="TP-8", vade_plani=uc_vade())
        self.odeme("500")
        blok = [b for b in ot.mutabakat(ot.tum_satirlar(BUGUN)) if b["ad"].startswith("320")][0]
        self.assertEqual((blok["defter"], blok["takvim"], blok["fark"]), (Dc("700.00"), Dc("700.00"), Dc("0.00")))


class RaporVadePlaniTest(tfl.ListeTaban):
    def test_vadesi_gecenler_ve_yaslandirma_plan_satirlariyla(self):
        f = self.a_fatura(no="RP-1", vade_plani=uc_vade())
        self.odeme("500")
        tarih = D(2026, 5, 5)
        v = cari_rapor.vadesi_gecenler(tarih, sekme="borc")
        satirlar = sorted(v["satirlar"], key=lambda s: s["vade"])
        self.assertEqual([(s["vade"], s["kalan"], s["gecikme"], s["taksit"]) for s in satirlar],
                         [(D(2026, 4, 20), Dc("300.00"), 15, (2, 3)), (D(2026, 4, 30), Dc("400.00"), 5, (3, 3))])
        self.assertEqual({s["fatura"].pk for s in satirlar}, {f.pk})
        self.assertEqual(v["toplam"], [("TRY", Dc("700.00"))])
        y = cari_rapor.yaslandirma(tarih, sekme="borc")
        (t,) = y["tablolar"]
        (r,) = t["satirlar"]
        self.assertEqual((r["dilimler"]["d30"], r["toplam"], r["adet"]), (Dc("700.00"), Dc("700.00"), 1))          # iki plan satırı TEK fatura sayılır
        self.assertEqual(y["fatura_adedi"], 1)

    def test_dilimler_satir_vadesine_gore(self):
        self.a_fatura(no="RP-2", vade_plani=plan((D(2026, 3, 1), "400"), (D(2026, 4, 1), "400"), (D(2026, 6, 1), "400")))
        y = cari_rapor.yaslandirma(D(2026, 5, 5), sekme="borc")
        d = y["tablolar"][0]["satirlar"][0]["dilimler"]
        self.assertEqual((d["d90"], d["d60"], d["gelmemis"]), (Dc("400.00"), Dc("400.00"), Dc("400.00")))   # 01.03 → 65 gün (61-90), 01.04 → 34 gün (31-60), 01.06 gelmedi
        self.assertEqual(d["d30"] + d["d90p"], Dc("0"))
        self.assertEqual(sum(d.values(), Dc("0")), Dc("1200.00"))

    def test_plansiz_fatura_raporlari_degismedi(self):
        self.a_fatura(no="RP-3", vade_tarihi=D(2026, 4, 1))
        v = cari_rapor.vadesi_gecenler(D(2026, 5, 5), sekme="borc")
        (s,) = v["satirlar"]
        self.assertEqual((s["vade"], s["kalan"], s.get("taksit")), (D(2026, 4, 1), Dc("1200.00"), None))


class CekMahsubuTest(tfl.ListeTaban):
    """(b) Döviz cariye verilen (ara hesapta) çek: takvimde çift sayılmaz."""

    def usd_fatura(self, no="UF-1", **kw):
        return self.a_fatura(no=no, miktar="10", fiyat="10", cari=self.usd, para_birimi="USD", **kw)      # 10×10×1,2 = 120 USD ⇒ bugünkü kurla 3.600 TL

    def cek(self, tutar, cari=None, ara=True, vade=D(2026, 6, 30), no="CK-1", durum="VERILDI", yon="VERILEN"):
        return CekSenet.objects.create(tip="CEK", yon=yon, tutar=Dc(tutar), para_birimi="TRY", vade=vade, durum=durum, belge_no=no, cari=cari or self.usd, ara_hesapta=ara)

    def alis_satirlari(self, **kw):
        return sorted(takvim(kaynak="FATURA_ALIS", **kw), key=lambda r: r["tarih"] or D(2100, 1, 1))

    def test_cek_borctan_kucuk_yalniz_fark_kalir(self):
        self.usd_fatura(vade_tarihi=D(2026, 4, 10))
        self.cek("1000")
        (r,) = self.alis_satirlari()
        self.assertEqual(r["tl"], Dc("2600.00"))                                                  # 3.600 − 1.000
        self.assertEqual(r["tutar"], Dc("86.67"))                                                 # 120 USD × 2.600/3.600
        self.assertEqual(r["pb"], "USD")
        self.assertIn("verilen çekle 1,000.00 TL karşılandı", r["aciklama"])
        (c,) = takvim(kaynak="CEK_VERILEN")
        self.assertEqual((c["tl"], c["tarih"]), (Dc("1000.00"), D(2026, 6, 30)))                  # çek kendi vadesinde çıkış
        toplam = sum(x["tl"] for x in takvim() if x["yon"] == "CIKIS")
        self.assertEqual(toplam, Dc("3600.00"))                                                   # 2.600 + 1.000 = borç; çift sayım yok

    def test_cek_borctan_buyuk_fatura_satiri_hic_cikmaz(self):
        self.usd_fatura()
        self.cek("5000")
        self.assertEqual(self.alis_satirlari(), [])
        (c,) = takvim(kaynak="CEK_VERILEN")
        self.assertEqual(c["tl"], Dc("5000.00"))

    def test_cek_esit_borc_satir_cikmaz(self):
        self.usd_fatura()
        self.cek("3600")
        self.assertEqual(self.alis_satirlari(), [])

    def test_iki_cek_toplami_en_erken_vadeden(self):
        self.usd_fatura(no="UF-2", vade_tarihi=D(2026, 4, 5))                                      # 3.600 TL, erken
        self.usd_fatura(no="UF-3", vade_tarihi=D(2026, 5, 5))                                      # 3.600 TL, geç
        self.cek("4000", no="CK-A")
        self.cek("1000", no="CK-B", vade=D(2026, 7, 31))                                           # toplam 5.000
        rows = self.alis_satirlari()
        self.assertEqual([(r["belge_no"], r["tl"]) for r in rows], [("UF-3", Dc("2200.00"))])      # 3.600 + 1.400 karşılandı → ikinci faturadan 2.200 kalır
        self.assertEqual(sum(r["tl"] for r in takvim(kaynak="CEK_VERILEN")), Dc("5000.00"))

    def test_vade_planli_fatura_plan_satirlari_en_erken_vadeden_dusulur(self):
        self.usd_fatura(no="UF-4", vade_plani=plan((D(2026, 5, 1), "60"), (D(2026, 6, 1), "60")))   # 60 USD + 60 USD = 1.800 + 1.800 TL
        self.cek("2400")                                                                            # ilk satır (1.800) tamamen, ikinci satırdan 600
        (r,) = self.alis_satirlari()
        self.assertEqual((r["tarih"], r["tl"], r["tutar"]), (D(2026, 6, 1), Dc("1200.00"), Dc("40.00")))

    def test_tl_cariye_verilen_cek_cift_dusulmez(self):
        """TL cariye verilen çek cari hesabına yazılır (ara_hesapta=False): FIFO zaten düşer; takvimde ikinci kez düşülmez."""
        self.a_fatura(no="TL-1", vade_tarihi=D(2026, 4, 10))
        self.cek("1000", cari=self.tedarikci, ara=False, no="CK-TL")
        (r,) = self.alis_satirlari()
        self.assertEqual(r["tl"], Dc("1200.00"))                                                    # (bu testte çek fişi yazılmadı → FIFO'da düşüş yok; satır olduğu gibi)
        self.assertNotIn("verilen çekle", r["aciklama"])

    def test_odenmis_ya_da_alinan_cek_mahsup_etmez(self):
        self.usd_fatura()
        self.cek("1000", durum="ODENDI", no="CK-O")
        self.cek("1000", yon="ALINAN", durum="PORTFOYDE", no="CK-AL")
        (r,) = self.alis_satirlari()
        self.assertEqual(r["tl"], Dc("3600.00"))

    def test_mutabakat_ayri_neden_satiri(self):
        self.usd_fatura()
        self.cek("1000")
        tum = ot.tum_satirlar(BUGUN)
        (m,) = tum["cek_mahsuplari"]
        self.assertEqual((m["cek_adedi"], m["cek_tl"], m["mahsup_tl"]), (1, Dc("1000.00"), Dc("1000.00")))
        blok = [b for b in ot.mutabakat(tum) if b["ad"].startswith("320")][0]
        neden = [k for k in blok["kalemler"] if "verilen çekle karşılanan" in k["ad"]]
        self.assertEqual(len(neden), 1)
        self.assertEqual((neden[0]["defter"], neden[0]["takvim"], neden[0]["fark"]), (Dc("-1000.00"), Dc("0.00"), Dc("-1000.00")))
        self.assertIn("ara hesapta (159)", neden[0]["not"])
        ana = [k for k in blok["kalemler"] if k["ad"].startswith("320-10-0002") or "USD TEDARİKÇİ" in k["ad"]][0]
        self.assertIn("ayrı satırda", ana["not"])
        self.assertAlmostEqual(float(blok["fark"]), float(blok["defter"] - blok["takvim"]), places=2)

    def test_mahsup_yoksa_mutabakata_neden_satiri_eklenmez(self):
        self.usd_fatura()
        tum = ot.tum_satirlar(BUGUN)
        self.assertEqual(tum["cek_mahsuplari"], [])
        blok = [b for b in ot.mutabakat(tum) if b["ad"].startswith("320")][0]
        self.assertFalse([k for k in blok["kalemler"] if "verilen çekle karşılanan" in k["ad"]])

    def test_takvim_sayfasi_mutabakat_aciklamasi(self):
        self.usd_fatura()
        self.cek("1000")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:odeme_takvimi"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "verilen çekle karşılanan")


class EkranVadePlaniTest(tfl.ListeTaban):
    def veri(self, **ek):
        v = {"tip": str(self.alis.pk), "cari": str(self.tedarikci.pk), "tarih": "2026-03-10", "fatura_no": "EK-1", "para_birimi": "TRY",
             "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
             "form-0-stok": str(self.stok.pk), "form-0-miktar": "10", "form-0-birim_fiyat": "100"}
        v.update(ek)
        return v

    def plan_veri(self, veri, plan_satirlari):
        """Form verisi + plan bölümü (düz sözlük + liste: test istemcisi QueryDict.items() ile yalnız son değeri gönderirdi)."""
        d = dict(veri)
        d["vp_var"] = "1"
        d["vp_tarih"] = [t for t, _u in plan_satirlari]
        d["vp_tutar"] = [u for _t, u in plan_satirlari]
        return d

    def post(self, veri, plan_satirlari):
        self.client.force_login(self.su)
        return self.client.post(reverse("core:alis_fatura_ekle"), self.plan_veri(veri, plan_satirlari))

    def test_girdi_ayristirma(self):
        from core.views import _vade_plani_girdisi
        self.assertIsNone(_vade_plani_girdisi(QueryDict("vade_tarihi=2026-04-01")))                # bölüm hiç gönderilmedi → dokunma
        self.assertEqual(_vade_plani_girdisi(QueryDict("vp_var=1")), [])                           # gönderildi, satır yok → planı kaldır
        q = QueryDict("vp_var=1&vp_tarih=2026-04-10&vp_tutar=400%2C00&vp_tarih=&vp_tutar=")
        self.assertEqual(_vade_plani_girdisi(q), [{"vade": "2026-04-10", "tutar": "400,00"}, {"vade": "", "tutar": ""}])

    def test_ekle_ekrani_plan_bolumu_ve_kayit(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        for parca in ('id="vade-plani-bolum"', 'name="vp_var"', "+ Vade satırı ekle", "toplam, faturanın ödenecek tutarına eşit olmalı"):
            self.assertContains(r, parca)
        r = self.post(self.veri(), [("2026-04-10", "400,00"), ("2026-04-20", "400,00"), ("2026-04-30", "400,00")])
        self.assertEqual(r.status_code, 302)
        f = Fatura.objects.get(fatura_no="EK-1")
        self.assertEqual([x["vade"] for x in f.vade_plani], ["2026-04-10", "2026-04-20", "2026-04-30"])
        self.assertEqual(f.genel_toplam, Dc("1200.00"))

    def test_ekle_hatali_toplam_formda_kalir_satirlar_geri_gelir(self):
        r = self.post(self.veri(), [("2026-04-10", "400,00"), ("2026-04-20", "300,00")])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")
        self.assertContains(r, 'value="2026-04-10"')                                                # girilen satırlar kaybolmaz
        self.assertContains(r, 'value="300,00"')
        self.assertFalse(Fatura.objects.filter(fatura_no="EK-1").exists())

    def test_plansiz_ekle_eskisi_gibi(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(vade_tarihi="2026-04-12"))
        self.assertEqual(r.status_code, 302)
        f = Fatura.objects.get(fatura_no="EK-1")
        self.assertEqual((f.vade_plani, f.vade_tarihi), (None, D(2026, 4, 12)))

    def test_duzenle_ekrani_plani_gosterir_ve_degistirir_ve_kaldirir(self):
        f = self.a_fatura(no="EK-2", vade_plani=uc_vade())
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertContains(r, 'value="2026-04-10"')
        self.assertContains(r, 'value="400,00"')
        self.assertEqual(r.content.decode().count('type="date" name="vp_tarih"'), 4)                            # 3 kayıtlı satır + <template> satırı
        v = self.veri(fatura_no="EK-2")
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.plan_veri(v, [("2026-05-01", "1.200,00")]))
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(f.vade_plani, [{"vade": "2026-05-01", "tutar": "1200.00"}])
        self.assertEqual(self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.plan_veri(v, [])).status_code, 302)      # satır yok → plan kaldırılır
        f.refresh_from_db()
        self.assertIsNone(f.vade_plani)

    def test_bolum_gonderilmezse_plan_korunur(self):
        f = self.a_fatura(no="EK-3", vade_plani=uc_vade())
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(fatura_no="EK-3"))     # vp_var yok
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(len(f.vade_plani), 3)

    def test_detay_plan_ve_kalan(self):
        f = self.a_fatura(no="EK-4", vade_plani=uc_vade())
        self.odeme("500")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(r, "Vade Planı")
        self.assertContains(r, "10.04.2026 — 400,00")
        self.assertContains(r, "Ödemeler en erken vadeden başlayarak plan satırlarına düşülür")
        self.assertContains(r, "0,00 ✓")                                                            # 1. satır kapandı
        Fatura.objects.filter(pk=f.pk).update(vade_plani=[{"vade": "2026-04-10", "tutar": "1.00"}])
        self.assertContains(self.client.get(reverse("core:fatura_detay", args=[f.pk])), "plan yok sayılıyor")
