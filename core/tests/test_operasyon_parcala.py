"""Operasyon PARÇALA (1 girdi → N eşit düzey çıktı): tanım kuralları, ihtiyaç hesabı (çalıştırma = max_i(talep_i / miktar_i), tam boy açık/kapalı,
fazla mahsubu — çift sayım yok), pay_dus (birim tüketim) payları, graf/DB eşitliği, üretim emri (operasyon başına tek kayıt), ÜRET ↔ PARÇALA geçişi."""
from datetime import date
from decimal import Decimal

from core.models import Operasyon, OperasyonKaydi
from core.services import urun_agaci as ua
from core.services.uretim import (
    UretimHatasi, emir_hedef_cikti, ihtiyac_hesapla, kaydi_girdi_satirlari, operasyon_guncelle, operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur,
    operasyon_yan_ciktilari, tanim_ciktilari, uretim_emri_olustur,
)
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
PARCALA, URET = Operasyon.Tur.PARCALA, Operasyon.Tur.URET
ESIT, BOY, YUZDE = Operasyon.PayAnahtari.ESIT, Operasyon.PayAnahtari.BOY, Operasyon.PayAnahtari.YUZDE


class ParcalaBase(TamBoyBase):
    def parcala(self, girdi, ciktilar, *, tam_boy=None, anahtar=ESIT, girdi_miktar="1"):
        return operasyon_olustur(istasyon_id=self.kesim.pk, satirlar=[(girdi, D(girdi_miktar))], tur=PARCALA, pay_anahtari=anahtar, tam_boy=tam_boy,
                                 ciktilar=[(st, D(str(m)), (D(str(b)) if b is not None else None), (D(str(y)) if y is not None else None))
                                           for st, m, b, y in ciktilar])

    def ozet(self, sonuc):
        return {o["stok"].kod: o for o in sonuc["ozet"]}

    def plan(self, sonuc):
        return {p["stok"].kod: p for p in sonuc["plan"]}


class OrnekTest(ParcalaBase):
    def test_ornek_A_tam_boy_acik(self):
        """A tipi 2+1 ön ayak: 1 BOY 5480 → 2 AD, tam boy AÇIK, ihtiyaç 3 → çalıştırma ⌈3/2⌉ = 2, girdi 2 BOY, üretilecek 4, fazla 1."""
        profil = self.stok("5480", self.boy, satinalma=True)
        ayak = self.stok("ONAYAK")
        self.parcala(profil, [(ayak, 2, None, None)], tam_boy=True)
        s = ihtiyac_hesapla([(ayak, D("3"))])
        p = self.plan(s)["ONAYAK"]                                                                 # hedef kök → plan satırı (özet kökleri içermez)
        self.assertEqual((p["calistirma"], p["uretilecek"], p["fazla"], p["tam"]), (D("2"), D("4"), D("1"), True))
        self.assertEqual(self.ozet(s)["5480"]["toplam_miktar"], D("2"))

    def test_ornek_C_tam_boy_kapali(self):
        """C 2+2: 1 BOY 5090 → 12 AD SAĞ, tam boy KAPALI, ihtiyaç 20 → çalıştırma 20/12 = 1,666667, girdi 1,666667 BOY, üretilecek 20, fazla 0."""
        profil = self.stok("5090", self.boy, satinalma=True)
        sag = self.stok("SAG")
        self.parcala(profil, [(sag, 12, None, None)], tam_boy=False)
        s = ihtiyac_hesapla([(sag, D("20"))])
        p = self.plan(s)["SAG"]
        self.assertEqual((p["uretilecek"], p["fazla"], p["tam"]), (D("20"), D("0"), False))
        self.assertEqual(p["calistirma"].quantize(D("0.000001")), D("1.666667"))
        self.assertEqual(self.ozet(s)["5090"]["toplam_miktar"].quantize(D("0.000001")), D("1.666667"))

    def test_sag_sol_max_kurali_kapali_ve_acik(self):
        """1 BOY → 12 SAĞ + 12 SOL (EŞİT): SAĞ ihtiyacı 20, SOL 10 → çalıştırma = max(20/12, 10/12) = 1,666667; girdi 1,666667 BOY (2,5 DEĞİL);
        SOL 20 üretilir, 10 fazla (mahsup). Tam boy açıkken çalıştırma 2: girdi 2 BOY, SAĞ 24 (fazla 4), SOL 24 (fazla 14)."""
        profil = self.stok("5090", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        u1, u2 = self.stok("U1", satis=True), self.stok("U2", satis=True)
        op = self.parcala(profil, [(sag, 12, None, None), (sol, 12, None, None)], tam_boy=False)
        self.op(self.montaj, u1, 1, [(sag, 2)])
        self.op(self.montaj, u2, 1, [(sol, 1)])
        s = ihtiyac_hesapla([(u1, D("10")), (u2, D("10"))])
        o = self.ozet(s)
        self.assertEqual((o["SAG"]["ihtiyac"], o["SAG"]["uretilecek_miktar"], o["SAG"]["fazla_miktar"]), (D("20"), D("20"), D("0")))
        self.assertEqual((o["SOL"]["ihtiyac"], o["SOL"]["fazla_miktar"]), (D("10"), D("10")))
        self.assertEqual(o["SOL"]["uretilecek_miktar"].quantize(D("0.000001")), D("20"))
        self.assertEqual(o["5090"]["toplam_miktar"].quantize(D("0.000001")), D("1.666667"))
        self.assertEqual([p["operasyon"].pk for p in s["plan"] if p["tur"] == PARCALA], [op.pk])                    # op başına TEK plan satırı
        p = self.plan(s)["SAG"]
        self.assertEqual([(c["stok"].kod, c["ihtiyac"], c["fazla"].quantize(D("0.000001"))) for c in p["ciktilar"]], [("SAG", D("20"), D("0")), ("SOL", D("10"), D("10"))])
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], tam_boy=True)
        o = self.ozet(ihtiyac_hesapla([(u1, D("10")), (u2, D("10"))]))
        self.assertEqual((o["SAG"]["calistirma_sayisi"], o["SAG"]["uretilecek_miktar"], o["SAG"]["fazla_miktar"]), (D("2"), D("24"), D("4")))
        self.assertEqual((o["SOL"]["uretilecek_miktar"], o["SOL"]["fazla_miktar"]), (D("24"), D("14")))
        self.assertEqual(o["5090"]["toplam_miktar"], D("2"))

    def test_mentese_senaryosu(self):
        """1 BOY 150-20-0002 → 64 SAĞ + 64 SOL, EŞİT, tam boy kapalı; SAĞ 10 + SOL 3 → çalıştırma 10/64, girdi 0,15625 BOY, SOL 10 üretilir, 7 fazla."""
        profil = self.stok("150-20-0002", self.boy, satinalma=True)
        sag, sol = self.stok("151-20-0014"), self.stok("151-20-0015")
        self.parcala(profil, [(sag, 64, None, None), (sol, 64, None, None)], tam_boy=False)
        s = ihtiyac_hesapla([(sag, D("10")), (sol, D("3"))])
        self.assertEqual(self.ozet(s)["150-20-0002"]["toplam_miktar"], D("0.15625"))
        c = {x["stok"].kod: x for x in self.plan(s)["151-20-0014"]["ciktilar"]}                    # iki hedef de kök → op'un plan satırı
        self.assertEqual((c["151-20-0014"]["uretilecek"], c["151-20-0014"]["fazla"]), (D("10"), D("0")))
        self.assertEqual((c["151-20-0015"]["uretilecek"], c["151-20-0015"]["fazla"]), (D("10"), D("7")))
        self.assertEqual(len(s["plan"]), 1)

    def test_fazla_mahsup_cift_sayim_yok(self):
        """SOL'u iki ürün kullanır: talep toplanır, girdi max kuralıyla TEK kez hesaplanır; SAĞ'ın fazlası ayrıca sayılmaz."""
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        u1, u2, u3 = self.stok("U1", satis=True), self.stok("U2", satis=True), self.stok("U3", satis=True)
        self.parcala(profil, [(sag, 10, None, None), (sol, 10, None, None)], tam_boy=True)
        self.op(self.montaj, u1, 1, [(sol, 3)])
        self.op(self.montaj, u2, 1, [(sol, 4)])
        self.op(self.montaj, u3, 1, [(sag, 1)])
        o = self.ozet(ihtiyac_hesapla([(u1, D("1")), (u2, D("1")), (u3, D("12"))]))      # SOL 7, SAĞ 12 → ⌈12/10⌉ = 2 boy
        self.assertEqual((o["SOL"]["ihtiyac"], o["SAG"]["ihtiyac"], o["P"]["toplam_miktar"]), (D("7"), D("12"), D("2")))
        self.assertEqual((o["SOL"]["uretilecek_miktar"], o["SOL"]["fazla_miktar"], o["SAG"]["fazla_miktar"]), (D("20"), D("13"), D("8")))

    def test_talebi_olmayan_kardes_planda_fazla_olarak_gorunur(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        self.parcala(profil, [(sag, 12, None, None), (sol, 12, None, None)], tam_boy=True)
        s = ihtiyac_hesapla([(sag, D("5"))])
        self.assertNotIn("SOL", self.ozet(s))                                                   # zincirde geçilmedi → özet satırı yok
        c = {x["stok"].kod: x for x in self.plan(s)["SAG"]["ciktilar"]}["SOL"]
        self.assertEqual((c["ihtiyac"], c["uretilecek"], c["fazla"]), (D("0"), D("12"), D("12")))


class PayTest(ParcalaBase):
    def test_pay_dus_birim_tuketim_esit_boy_yuzde(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        op = self.parcala(profil, [(sag, 12, None, None), (sol, 4, None, None)], tam_boy=False)            # EŞİT: 12/16, 4/16
        t = self.ozet(ihtiyac_hesapla([(sag, D("1"))], boy_yuvarla=False, pay_dus=True))["P"]["toplam_miktar"]
        self.assertEqual(t.quantize(D("0.0000001")), (D("1") / 12 * D("12") / 16).quantize(D("0.0000001")))   # 1/16 boy
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], pay_anahtari=BOY,
                           ciktilar=[(sag, D("12"), D("300"), None), (sol, D("4"), D("600"), None)])        # BOY: 3600 / 2400 → 0,6 / 0,4
        t = self.ozet(ihtiyac_hesapla([(sag, D("1"))], boy_yuvarla=False, pay_dus=True))["P"]["toplam_miktar"]
        self.assertEqual(t.quantize(D("0.0000001")), D("0.05"))                                                # 1/12 × 0,6
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], pay_anahtari=YUZDE,
                           ciktilar=[(sag, D("12"), None, D("25")), (sol, D("4"), None, D("75"))])
        t = self.ozet(ihtiyac_hesapla([(sol, D("1"))], boy_yuvarla=False, pay_dus=True))["P"]["toplam_miktar"]
        self.assertEqual(t.quantize(D("0.0000001")), D("0.1875"))                                              # 1/4 × 0,75
        # pay_dus KAPALI (planlama): payla ilgisi yok, max kuralı — tam boy kapalı tanımda 1 SOL = 1/4 boy; açılınca 1 boy
        self.assertEqual(self.ozet(ihtiyac_hesapla([(sol, D("1"))]))["P"]["toplam_miktar"], D("0.25"))
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], tam_boy=True)
        self.assertEqual(self.ozet(ihtiyac_hesapla([(sol, D("1"))]))["P"]["toplam_miktar"], D("1"))
        self.assertEqual(self.ozet(ihtiyac_hesapla([(sol, D("1"))], boy_yuvarla=False))["P"]["toplam_miktar"], D("0.25"))

    def test_graf_ve_db_yolu_ayni(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        u1 = self.stok("U1", satis=True)
        self.parcala(profil, [(sag, 12, None, None), (sol, 12, None, None)], tam_boy=False)
        self.op(self.montaj, u1, 1, [(sag, 2), (sol, 1)])
        graf = ua.graf_yukle(date(2026, 10, 9))
        for kw in ({}, {"boy_yuvarla": False, "pay_dus": True}):
            a, b = ihtiyac_hesapla([(u1, D("7"))], **kw), ihtiyac_hesapla([(u1, D("7"))], graf=graf, **kw)
            self.assertEqual([(o["stok"].kod, o["ihtiyac"], o["uretilecek_miktar"], o["fazla_miktar"]) for o in a["ozet"]],
                             [(o["stok"].kod, o["ihtiyac"], o["uretilecek_miktar"], o["fazla_miktar"]) for o in b["ozet"]])
        self.assertEqual(graf.op_of[sol.pk].pk, graf.op_of[sag.pk].pk)
        self.assertEqual(graf.birim_tuketim(u1)[profil.pk]["ihtiyac"].quantize(D("0.000001")), D("0.125"))    # (2/12)×0,5 + (1/12)×0,5


class TanimKuraliTest(ParcalaBase):
    def test_tek_girdi_tekrar_girdi_esit_cikti(self):
        profil, p2 = self.stok("P", self.boy, satinalma=True), self.stok("P2", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        with self.assertRaisesMessage(UretimHatasi, "tek girdi"):
            operasyon_olustur(istasyon_id=self.kesim.pk, satirlar=[(profil, D("1")), (p2, D("1"))], tur=PARCALA, ciktilar=[(sag, D("1"), None, None)])
        with self.assertRaisesMessage(UretimHatasi, "tekrarlanamaz"):
            self.parcala(profil, [(sag, 1, None, None), (sag, 2, None, None)])
        with self.assertRaisesMessage(UretimHatasi, "girdisi; aynı zamanda çıktısı"):
            self.parcala(profil, [(profil, 1, None, None)])
        with self.assertRaisesMessage(UretimHatasi, "en az bir çıktı"):
            self.parcala(profil, [])

    def test_surucu_cakismasi_ve_yan_cikti_serbest(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol, yan = self.stok("SAG"), self.stok("SOL"), self.stok("YAN")
        self.parcala(profil, [(sag, 12, None, None)])
        with self.assertRaisesMessage(UretimHatasi, "zaten SAG tanımından üretiliyor"):
            self.parcala(profil, [(sol, 1, None, None), (sag, 1, None, None)])
        with self.assertRaisesMessage(UretimHatasi, "zaten aktif bir operasyon"):
            self.op(self.kesim, sag, 2, [(profil, 1)])                                                      # ÜRET ana çıktı da çakışır
        self.op(self.kesim, self.stok("ANA"), 3, [(profil, 1)])                                            # ÜRET + yan çıktı YAN (sürücü değil)
        operasyon_guncelle(Operasyon.objects.get(cikti__kod="ANA"), istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))],
                           boy_mm=D("100"), yan_ciktilar=[(yan, D("1"), D("50"))])
        self.parcala(profil, [(sol, 1, None, None), (yan, 2, None, None)])                                 # yan çıktı olan stok PARÇALA'da üretilebilir

    def test_boy_ve_yuzde_zorunluluklari(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        with self.assertRaisesMessage(UretimHatasi, "boy (mm) girin"):
            self.parcala(profil, [(sag, 12, 300, None), (sol, 4, None, None)], anahtar=BOY)
        with self.assertRaisesMessage(UretimHatasi, "maliyet payı (%) girin"):
            self.parcala(profil, [(sag, 12, None, 50), (sol, 4, None, None)], anahtar=YUZDE)
        with self.assertRaisesMessage(UretimHatasi, "toplamı %100"):
            self.parcala(profil, [(sag, 12, None, 50), (sol, 4, None, 40)], anahtar=YUZDE)
        op = self.parcala(profil, [(sag, 12, None, "33.3333"), (sol, 4, None, "66.6667")], anahtar=YUZDE)    # tolerans içinde
        self.assertEqual([(c.stok.kod, c.yuzde, c.surucu, c.sira) for c in tanim_ciktilari(op)], [("SAG", D("33.3333"), True, 0), ("SOL", D("66.6667"), True, 10)])

    def test_tur_degistirme_snapshot_korur(self):
        from core.models import OperasyonKaydiCikti, StokHareket
        from core.services.hareket import hareket_ekle
        profil = self.stok("P", self.boy, satinalma=True)
        ana, yan, sol = self.stok("ANA"), self.stok("YAN"), self.stok("SOL")
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))],
                               boy_mm=D("1292.60"), yan_ciktilar=[(yan, D("1"), D("1063.53"))])
        hareket_ekle(stok_id=profil.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 1), tur=StokHareket.Tur.GIRIS, miktar=D("5"), giris_tutar_try=D("3500"))
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("3"))
        operasyon_kaydi_onayla(k)
        snap = [(c.stok.kod, c.miktar, c.pay_orani, c.agirlik) for c in OperasyonKaydiCikti.objects.filter(kayit=k, silindi=False).order_by("pk")]
        # ÜRET → PARÇALA (referans ANA sabit; SOL eklenir, yan çıktı kalkar)
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], tur=PARCALA, pay_anahtari=ESIT,
                           ciktilar=[(ana, D("3"), None, None), (sol, D("1"), None, None)])
        op.refresh_from_db()
        self.assertEqual((op.tur, op.pay_anahtari, op.cikti.kod, op.cikti_miktar, operasyon_yan_ciktilari(op).count()), (PARCALA, ESIT, "ANA", D("3.000"), 0))
        self.assertEqual([(c.stok.kod, c.surucu) for c in tanim_ciktilari(op)], [("ANA", True), ("SOL", True)])
        with self.assertRaisesMessage(UretimHatasi, "referans çıktısı"):
            operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], ciktilar=[(sol, D("1"), None, None), (ana, D("3"), None, None)])
        # çıktı listesi verilmezse mevcut korunur, referans miktarı güncellenir
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("6"), satirlar=[(profil, D("1"))])
        self.assertEqual([(c.stok.kod, c.miktar) for c in tanim_ciktilari(op)], [("ANA", D("6")), ("SOL", D("1"))])
        # PARÇALA → ÜRET: yan çıktı yok, SOL serbest kalır
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))], tur=URET)
        op.refresh_from_db()
        self.assertEqual((op.tur, [(c.stok.kod, c.surucu) for c in tanim_ciktilari(op)]), (URET, [("ANA", True)]))
        self.parcala(profil, [(sol, 2, None, None)])
        self.assertEqual([(c.stok.kod, c.miktar, c.pay_orani, c.agirlik) for c in OperasyonKaydiCikti.objects.filter(kayit=k, silindi=False).order_by("pk")], snap)

    def test_dongu(self):
        profil = self.stok("P", self.boy, satinalma=True)
        a, b = self.stok("A"), self.stok("B")
        self.parcala(a, [(b, 2, None, None)])                                                                # A → B
        self.op(self.montaj, a, 1, [(b, 1)])                                                                 # B → A
        with self.assertRaisesMessage(UretimHatasi, "döngü"):
            ihtiyac_hesapla([(b, D("1"))])


class UretimEmriTest(ParcalaBase):
    def test_operasyon_basina_tek_kayit_referans_hedef(self):
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        u1, u2 = self.stok("U1", satis=True), self.stok("U2", satis=True)
        op = self.parcala(profil, [(sag, 12, None, None), (sol, 12, None, None)], tam_boy=False)
        self.op(self.montaj, u1, 1, [(sag, 2)])
        self.op(self.montaj, u2, 1, [(sol, 1)])
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": u1.pk, "hedef_miktar": D("10")}, {"hedef_urun_id": u2.pk, "hedef_miktar": D("10")}],
                                   depo_id=self.depo.pk, tarih=date(2026, 10, 9))
        iemirleri = {i.operasyon_id: i for i in emir.istasyon_emirleri.all()}
        self.assertEqual(len(iemirleri), 3)
        self.assertEqual(emir_hedef_cikti(iemirleri[op.pk]), D("20"))                                        # referans SAĞ üretilecek
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=emir_hedef_cikti(iemirleri[op.pk]),
                                    uretim_emri=emir)
        self.assertEqual(kaydi_girdi_satirlari(k).get().planlanan_miktar, D("1.666667"))


class OnayPayTest(ParcalaBase):
    """Onayda girdi maliyeti çıktılara pay anahtarıyla dağıtılır: BOY (miktar × boy), EŞİT (adet başı), YÜZDE (elle); snapshot ağırlığı saklanır ve
    yeniden paylaştırma (uretim_senkronla) tanıma değil snapshot'a bakar."""

    def kur(self, anahtar, ciktilar):
        from core.models import StokHareket
        from core.services.hareket import hareket_ekle
        profil = self.stok("P", self.boy, satinalma=True)
        sag, sol = self.stok("SAG"), self.stok("SOL")
        op = self.parcala(profil, [(sag,) + ciktilar[0], (sol,) + ciktilar[1]], anahtar=anahtar, tam_boy=True)
        hareket_ekle(stok_id=profil.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 1), tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("6400"),
                     giris_tutar_usd=D("160"))
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D(str(ciktilar[0][0])))
        operasyon_kaydi_onayla(k)
        return profil, sag, sol, k

    def girisler(self, k):
        from core.models import StokHareket
        return {h.stok.kod: h for h in StokHareket.objects.filter(operasyon_kaydi=k, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}

    def snap(self, k):
        from core.models import OperasyonKaydiCikti
        return {c.stok.kod: c for c in OperasyonKaydiCikti.objects.filter(kayit=k, silindi=False).select_related("stok")}

    def test_esit_adet_basi(self):
        profil, sag, sol, k = self.kur(ESIT, [(12, None, None), (4, None, None)])                      # 1 BOY = 640 TL → 16 adet, adet başı 40
        g = self.girisler(k)
        self.assertEqual((g["SAG"].miktar, g["SAG"].tutar_try, g["SOL"].miktar, g["SOL"].tutar_try), (D("12"), D("480.00"), D("4"), D("160.00")))
        self.assertEqual((g["SAG"].tutar_usd, g["SOL"].tutar_usd), (D("12.00"), D("4.00")))
        s = self.snap(k)
        self.assertEqual((s["SAG"].pay_orani, s["SAG"].agirlik, s["SOL"].pay_orani, s["SOL"].agirlik, s["SAG"].ana_mi, s["SOL"].ana_mi),
                         (D("0.75"), D("12"), D("0.25"), D("4"), True, False))
        for st in (sag, sol):
            st.refresh_from_db()
            self.assertEqual(st.ort_maliyet_try, D("40.000000"))                                             # adet başı eşit

    def test_boy_orani(self):
        profil, sag, sol, k = self.kur(BOY, [(12, 300, None), (4, 600, None)])                         # 3600 : 2400 → %60 / %40
        g = self.girisler(k)
        self.assertEqual((g["SAG"].tutar_try, g["SOL"].tutar_try), (D("384.00"), D("256.00")))
        self.assertEqual((self.snap(k)["SAG"].agirlik, self.snap(k)["SOL"].agirlik), (D("3600"), D("2400")))

    def test_yuzde_elle(self):
        profil, sag, sol, k = self.kur(YUZDE, [(12, None, 25), (4, None, 75)])
        g = self.girisler(k)
        self.assertEqual((g["SAG"].tutar_try, g["SOL"].tutar_try), (D("160.00"), D("480.00")))
        self.assertEqual((self.snap(k)["SAG"].agirlik, self.snap(k)["SOL"].agirlik, self.snap(k)["SOL"].pay_orani), (D("25"), D("75"), D("0.75")))

    def test_yeniden_paylastirma_snapshottan_tanim_degisse_de(self):
        from core.services import stok_fis
        profil, sag, sol, k = self.kur(ESIT, [(12, None, None), (4, None, None)])
        op = k.operasyon
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, satirlar=[(profil, D("1"))], pay_anahtari=YUZDE,
                           ciktilar=[(sag, D("12"), None, D("10")), (sol, D("4"), None, D("90"))])          # tanım değişti
        self.assertEqual(stok_fis.uretim_senkronla(k), [])                                                    # snapshot ağırlığı: pay aynı → değişen yok
        g = self.girisler(k)
        self.assertEqual((g["SAG"].tutar_try, g["SOL"].tutar_try), (D("480.00"), D("160.00")))
