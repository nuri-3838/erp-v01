"""FIFO nakit iade kuralı (2026-10-16): ticari carilerde (120/121/320/321) doğal yöndeki fatura-dışı NAKİT hareketi (BANKA/KASA/KREDI_KARTI/CEK_SENET: cariden gelen para = ödemenin iadesi) ödeme
havuzundan DÜŞÜLÜR. Vaka: 5G (alınan 900k + gönderilen 900k net 0 → faturalar açık), Sedat Bakıcı (ödeme + iade), Ertekpa (müşteri avansı). Değişmez: Σ kalan − avans = defter bakiyesi (fatura-dışı DİĞER
kaynaklar — fason tahakkuku vb. — yükümlülüktür, havuza dokunmaz); ortak/personel/emanet (335/336/500) değişmez; liste, cari özet, yaşlandırma, vadesi geçenler, fatura detayı ve ödeme takvimi AYNI sayıyı verir."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from core.models import Fatura, YevmiyeSatir
from core.services import cari_ozet, cari_rapor, fatura_odeme, odeme_takvimi as ot
from core.services.fatura import fatura_olustur
from core.services.kasa_hareket import hareket_olustur as kasa_hareket
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests import test_fatura_liste as tfl
from core.tests.test_fatura import _hesap

D = datetime.date
Dc = Decimal
BUGUN = D(2026, 3, 25)
NAKIT = ("BANKA", "KASA", "KREDI_KARTI", "CEK_SENET")


class IadeTaban(tfl.ListeTaban):
    def gelen(self, tutar, tarih, cari=None):
        """Cariden gelen para (alacak doğal yön / müşteride tahsilat ters yön)."""
        kasa_hareket(kasa=self.kasa, tip="cari_tahsilat", karsi=cari or self.tedarikci, tutar=tutar, tarih=tarih)

    def giden(self, tutar, tarih, cari=None):
        kasa_hareket(kasa=self.kasa, tip="cari_odeme", karsi=cari or self.tedarikci, tutar=tutar, tarih=tarih)

    def satirlar_of(self, cari):
        return list(YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id=cari.muhasebe_kodu)
                    .values("fis_id", "islem_pb", "borc", "alacak", "islem_tutari", "fis__kaynak"))

    def fifo(self, cari):
        faturalar = {f.fis_id: f for f in Fatura.objects.filter(cari=cari, silindi=False, durum="ONAYLI", fis__isnull=False)}
        return fatura_odeme.fifo_kalemler(cari, faturalar, self.satirlar_of(cari))


class BesGVakasiTest(IadeTaban):
    def test_gidip_gelen_para_faturayi_kapatmaz(self):
        f = self.a_fatura(no="5G-1", miktar="10", fiyat="100", vade_tarihi=D(2026, 4, 30))                  # 1.200
        self.gelen("900", D(2026, 3, 11))                                                                    # alınan havale
        self.giden("900", D(2026, 3, 12))                                                                    # gönderilen (cariye istinaden)
        kalemler, kredi = self.fifo(self.tedarikci)
        self.assertEqual(kalemler[f.pk]["kalan"], Dc("1200.00"))
        self.assertEqual(kredi, {"TRY": Dc("0")})                                                           # sahte "eşleşmemiş avans" yok
        d = fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]
        self.assertEqual((d["durum"], d["kalan"]), ("ACIK", Dc("1200.00")))

    def test_net_odeme_kadar_kapanir(self):
        f = self.a_fatura(no="5G-2", miktar="10", fiyat="100", vade_tarihi=D(2026, 4, 30))
        self.gelen("900", D(2026, 3, 11))
        self.giden("900", D(2026, 3, 12))
        self.giden("500", D(2026, 3, 13))                                                                    # gerçek ödeme
        d = fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]
        self.assertEqual((d["durum"], d["kalan"]), ("KISMI", Dc("700.00")))

    def test_sedat_bakici_odeme_ve_iade(self):
        f = self.a_fatura(no="SB-1", miktar="5", fiyat="100", vade_tarihi=D(2026, 4, 30))                   # 600
        self.giden("4000", D(2026, 3, 11))                                                                   # 4.000 ödendi (avans)
        self.gelen("4000", D(2026, 3, 12))                                                                   # 4.000 iade geldi
        d = fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]
        self.assertEqual((d["durum"], d["kalan"]), ("ACIK", Dc("600.00")))                                  # eskiden: Kapandı + 3.400 sahte avans

    def test_ertekpa_musteri_avansi(self):
        """Müşteri (120, doğal borç): tahsilat ters yön (avans), müşteriye geri ödeme doğal yön nakit → avans düşer; fatura durumu aynı."""
        self.a_fatura(no="ST-0", miktar="100", fiyat="10")                                                   # stok girişi
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="TS-9", satirlar=self._satir("5", "100"))   # 600
        self.gelen("1000", D(2026, 3, 11), cari=self.musteri)                                                # tahsilat
        self.giden("250", D(2026, 3, 12), cari=self.musteri)                                                 # müşteriye geri ödeme
        kalemler, kredi = self.fifo(self.musteri)
        self.assertEqual(kalemler[f.pk]["kalan"], Dc("0.00"))
        self.assertEqual(kredi, {"TRY": Dc("150.00")})                                                      # 1000 − 600 (fatura) − 250 (iade); eskiden 400
        self.assertEqual(cari_ozet.acik_faturalar(self.musteri, BUGUN)["esitlenmemis"], {"TRY": Dc("150.00")})


class KapsamDisiTest(IadeTaban):
    def test_fason_tahakkuku_havuza_dokunmaz(self):
        """Doğal yönde fatura-dışı NAKİT olmayan hareket (fason tahakkuku) gerçek yükümlülüktür: faturaları kapatan ödemeyi azaltmaz."""
        _hesap("120.99", "DİĞER")
        f = self.a_fatura(no="FT-1", miktar="10", fiyat="10", vade_tarihi=D(2026, 4, 1))                     # 120
        fis_olustur(tarih=D(2026, 3, 12), satirlar=[SatirGirdi(hesap_kodu="120.99", taraf="B", islem_tutari="1000"), SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="1000")],
                    kaynak="FASON_TAHAKKUK")
        self.giden("120", D(2026, 3, 15))
        kalemler, kredi = self.fifo(self.tedarikci)
        self.assertEqual(kalemler[f.pk]["kalan"], Dc("0.00"))
        self.assertEqual(kredi, {"TRY": Dc("0")})

    def test_ortak_personel_emanet_kokleri_degismez(self):
        """500/335/336 carilerinde kural UYGULANMAZ: aynı satırlarla eski (nakit düşmeyen) sonuç."""
        for kod in ("500.10.0001", "335.10.0001", "336.10.0001"):
            cari = SimpleNamespace(muhasebe_kodu=kod, pk=1)
            f = SimpleNamespace(pk=7, tarih=D(2026, 3, 1), vade_tarihi=D(2026, 4, 1))
            satirlar = [{"fis_id": 1, "islem_pb": "TRY", "borc": Dc("0"), "alacak": Dc("100"), "islem_tutari": Dc("100"), "fis__kaynak": "FATURA"},
                        {"fis_id": 2, "islem_pb": "TRY", "borc": Dc("0"), "alacak": Dc("900"), "islem_tutari": Dc("900"), "fis__kaynak": "BANKA"},        # doğal yönde banka
                        {"fis_id": 3, "islem_pb": "TRY", "borc": Dc("900"), "alacak": Dc("0"), "islem_tutari": Dc("900"), "fis__kaynak": "BANKA"}]
            kalemler, kredi = fatura_odeme.fifo_kalemler(cari, {1: f}, satirlar)
            self.assertEqual(kalemler[7]["kalan"], Dc("0"), kod)                                              # 900 ödeme havuza girdi (eski davranış)
            self.assertEqual(kredi, {"TRY": Dc("800")}, kod)

    def test_ticari_olmayan_satir_kaynagi_yoksa_kural_dusmez(self):
        cari = SimpleNamespace(muhasebe_kodu="320.10.0001", pk=1)
        f = SimpleNamespace(pk=7, tarih=D(2026, 3, 1), vade_tarihi=D(2026, 4, 1))
        satirlar = [{"fis_id": 1, "islem_pb": "TRY", "borc": Dc("0"), "alacak": Dc("100"), "islem_tutari": Dc("100")},                      # fis__kaynak yok → nakit sayılmaz
                    {"fis_id": 3, "islem_pb": "TRY", "borc": Dc("100"), "alacak": Dc("0"), "islem_tutari": Dc("100")}]
        kalemler, _ = fatura_odeme.fifo_kalemler(cari, {1: f}, satirlar)
        self.assertEqual(kalemler[7]["kalan"], Dc("0"))


class DegismezTest(IadeTaban):
    """Σ kalan − artan avans = defter bakiyesi (doğal yönde) — fatura-dışı NAKİT dışı doğal hareketler (fason tahakkuku vb.) hariç."""

    def _degismez(self, cari):
        kalemler, kredi = self.fifo(cari)
        dogal = fatura_odeme.dogal_yon(cari)
        bakiye = Dc("0")
        digerleri = Dc("0")                                                                                  # FIFO'nun bilerek dışında tuttuğu: doğal yönde, fatura olmayan, nakit olmayan
        faturalar = {f.fis_id for f in Fatura.objects.filter(cari=cari, silindi=False, durum="ONAYLI", fis__isnull=False)}
        for s in self.satirlar_of(cari):
            yon = "borc" if s["borc"] > 0 else "alacak"
            t = s["islem_tutari"]
            bakiye += t if yon == dogal else -t
            if yon == dogal and s["fis_id"] not in faturalar and s["fis__kaynak"] not in NAKIT:
                digerleri += t
        self.assertEqual(sum(k["kalan"] for k in kalemler.values()) - sum(kredi.values()), bakiye - digerleri)

    def test_tum_ticari_cariler(self):
        _hesap("120.99", "DİĞER")
        self.a_fatura(no="D-1", miktar="10", fiyat="100", vade_tarihi=D(2026, 4, 30))
        self.a_fatura(no="D-2", miktar="3", fiyat="100", tarih=D(2026, 3, 12), vade_tarihi=D(2026, 5, 30))
        self.gelen("900", D(2026, 3, 13))
        self.giden("900", D(2026, 3, 14))
        self.giden("400", D(2026, 3, 15))
        fis_olustur(tarih=D(2026, 3, 16), satirlar=[SatirGirdi(hesap_kodu="120.99", taraf="B", islem_tutari="333"), SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="333")], kaynak="FASON_TAHAKKUK")
        self.a_fatura(no="U-1", miktar="1", fiyat="100", cari=self.usd, para_birimi="USD")
        self.a_fatura(no="ST-0", miktar="100", fiyat="10", cari=self.tedarikci)
        for cari in (self.tedarikci, self.usd):
            with self.subTest(cari=cari.kod):
                self._degismez(cari)

    def test_fazla_iade_avansi_eksiye_dusurmez(self):
        f = self.a_fatura(no="E-1", miktar="1", fiyat="100", vade_tarihi=D(2026, 4, 30))
        self.gelen("500", D(2026, 3, 11))                                                                    # ödeme olmadan gelen para: havuz eksiye iner, sıfırlanır
        kalemler, kredi = self.fifo(self.tedarikci)
        self.assertEqual(kalemler[f.pk]["kalan"], Dc("120.00"))
        self.assertEqual(kredi, {"TRY": Dc("0")})


class TumEkranlarAyniSayiTest(IadeTaban):
    def test_liste_ozet_rapor_detay_takvim_ayni(self):
        f = self.a_fatura(no="AS-1", miktar="10", fiyat="100", vade_tarihi=D(2026, 3, 1))                   # 1.200, vadesi geçmiş
        self.gelen("900", D(2026, 3, 11))
        self.giden("900", D(2026, 3, 12))
        self.giden("200", D(2026, 3, 13))
        beklenen = Dc("1000.00")
        self.assertEqual(fatura_odeme.odeme_durumlari([self.tedarikci], BUGUN)[f.pk]["kalan"], beklenen)                    # fatura listesi
        self.assertEqual(cari_ozet.acik_faturalar(self.tedarikci, BUGUN)["toplam"], {"TRY": beklenen})                       # cari özet
        self.assertEqual(cari_ozet.acik_faturalar(self.tedarikci, BUGUN)["esitlenmemis"], {})
        toplu = [r for r in cari_rapor.acik_faturalar_toplu(BUGUN) if r["cari"].pk == self.tedarikci.pk]
        self.assertEqual(sum(r["kalan"] for r in toplu), beklenen)                                                          # yaşlandırma / vadesi geçenler çekirdeği
        yas = cari_rapor.yaslandirma(BUGUN, sekme="borc")
        self.assertIn(str(beklenen), str(yas))                                                                              # yaşlandırma toplamında
        gec = cari_rapor.vadesi_gecenler(BUGUN)
        self.assertIn(str(beklenen), str(gec))                                                                              # vadesi geçenlerde
        det = fatura_odeme.fatura_odemeleri(Fatura.objects.get(pk=f.pk), BUGUN)                                              # fatura detay kartı
        self.assertEqual((det["kalan"], det["odenen"], det["iade_toplam"]), (beklenen, Dc("200.00"), Dc("900.00")))
        self.assertEqual(sum(h["tutar"] for h in det["hareketler"]), det["odenen"])                                          # dağılım toplamı = tutar − kalan
        (satir,) = [r for r in ot.tum_satirlar(BUGUN)["satirlar"] if r["kaynak"] == "FATURA_ALIS"]                           # ödeme takvimi
        self.assertEqual(satir["tutar"], beklenen)
        self.assertEqual([r for r in ot.tum_satirlar(BUGUN)["satirlar"] if r["kaynak"] == "CARI_BORC"], [])

    def test_detay_kartinda_iade_dusulen_notu(self):
        f = self.a_fatura(no="DT-1", miktar="10", fiyat="100", vade_tarihi=D(2026, 4, 30))
        self.giden("1000", D(2026, 3, 11))                                                                   # ödeme
        self.gelen("300", D(2026, 3, 12))                                                                    # kısmi iade
        det = fatura_odeme.fatura_odemeleri(Fatura.objects.get(pk=f.pk), BUGUN)
        self.assertEqual((det["odenen"], det["kalan"], det["iade_toplam"]), (Dc("700.00"), Dc("500.00"), Dc("300.00")))
        (h,) = det["hareketler"]
        self.assertEqual((h["tutar"], h["hareket_tutari"], h["iade_dusulen"]), (Dc("700.00"), Dc("1000.00"), Dc("300.00")))
        self.client.force_login(self.su)
        r = self.client.get(f"/faturalar/{f.pk}/")
        self.assertContains(r, "iade olarak düşüldü")
