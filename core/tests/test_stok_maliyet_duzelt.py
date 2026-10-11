"""``stok_maliyet_duzelt`` komutu: fiyatsız girişe elle birim fiyat → kart ortalaması → üretim çıktısı maliyeti + aktarım fişi → satış maliyet fişi
zinciri yeniden hesaplanır; varsayılan dry-run hiçbir şey bırakmaz, --uygula kalıcı yazar; rapor etkilenen çıkışları ve mizan farklarını gösterir."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Stok, StokHareket, YevmiyeFisi
from core.services.hareket import hareket_ekle
from core.services.uretim import istasyon_emri_kayit_ac, operasyon_kaydi_onayla
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class MaliyetDuzeltTest(kt.KapanisTaban):
    """KapanisTaban: Sf-HAM (satınalma, 100 adet @ 10 TL girişi 01.01.2026) → operasyon → Sf1 mamul; sipariş→ÜS→üretim→fatura zinciri gerçek servislerle."""

    def kur_fiyatsiz(self):
        """Fiyatsız ikinci ham madde girişi + üretim (girdi maliyeti kısmen fiyatsız) + depolu satış faturası (620 maliyet fişi)."""
        self.ham2 = Stok.objects.create(kod="150-50-0004", ad="KULLANMA KILAVUZU", kategori=self.ham.kategori, kdv=self.kdv,
                                        uretim_birimi=self.ham.uretim_birimi, fatura_birimi=self.ham.fatura_birimi, satinalma_urunu=True, uretim_urunu=False)
        hareket_ekle(stok_id=self.ham2.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("50"))    # FİYATSIZ
        from core.services.uretim import operasyon_guncelle
        operasyon_guncelle(self.op, istasyon_id=self.op.istasyon_id, cikti_miktar=D("1"), satirlar=[(self.ham, D("1")), (self.ham2, D("1"))])
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        ie = emir.istasyon_emirleri.get()
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie, tarih=kt.GUN))                                # üretim = sevk günü (tarih bazlı kontrol)
        _r, mesajlar = self.fatura_kes(sip)
        assert sip.fatura_id and not any("yetersiz" in m for m in mesajlar), mesajlar
        return sip

    def test_dry_run_hicbir_sey_birakmaz_rapor_verir(self):
        self.kur_fiyatsiz()
        cikti = StokHareket.objects.get(stok=self.stok, tur="GIRIS", kaynak="URETIM", silindi=False)
        self.assertEqual(cikti.tutar_try, D("30.00"))                                                   # yalnız ham (3 × 10 TL); kılavuz fiyatsız
        self.assertEqual(cikti.maliyet_durumu, "GECICI")
        fis_once = {f.pk: f.updated_at for f in YevmiyeFisi.objects.filter(silindi=False)}
        out = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=1,20", stdout=out)
        metin = out.getvalue()
        self.assertIn("DRY-RUN", metin)
        self.assertIn("FIYATLANAN GIRISLER (1)", metin)
        self.assertIn("50 x 1.2 = 60.00 TL", metin)
        self.assertIn("TUTARI/DURUMU DEGISEN HAREKETLER (4;", metin)                                   # giriş, tüketim, üretim çıktısı, satış çıkışı
        self.assertIn("Sf1           CIKIS FATURA", metin)                                                 # satış çıkışı da yeniden değerlendi
        self.assertRegex(metin, r"153:\s+970\.00 /\s+1,026\.40 /\s+56\.40  <--")                          # stok değeri farkı (47 × 1,20)
        self.assertIn("KULLANMA KILAVUZU", metin)
        giris = StokHareket.objects.get(stok=self.ham2, tur="GIRIS")
        self.assertIsNone(giris.giris_tutar_try)                                                           # geri alındı
        cikti.refresh_from_db()
        self.assertEqual((cikti.tutar_try, cikti.maliyet_durumu), (D("30.00"), "GECICI"))
        self.assertEqual({f.pk: f.updated_at for f in YevmiyeFisi.objects.filter(silindi=False)}, fis_once)

    def test_uygula_zinciri_yeniden_hesaplar(self):
        sip = self.kur_fiyatsiz()
        out = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=1,20", "--uygula", stdout=out)
        self.assertIn("UYGULANDI", out.getvalue())
        giris = StokHareket.objects.get(stok=self.ham2, tur="GIRIS")
        self.assertEqual(giris.giris_tutar_try, D("60.00"))
        tuketim = StokHareket.objects.get(stok=self.ham2, tur="CIKIS", silindi=False)
        self.assertEqual((tuketim.tutar_try, tuketim.maliyet_durumu), (D("3.60"), "KESIN"))              # 3 × 1,20
        cikti = StokHareket.objects.get(stok=self.stok, tur="GIRIS", kaynak="URETIM", silindi=False)
        self.assertEqual((cikti.tutar_try, cikti.maliyet_durumu), (D("33.60"), "KESIN"))                 # 30 + 3,60
        self.assertIsNone(Stok.objects.get(pk=self.stok.pk).ort_maliyet_try)                            # 3 üretildi, 3 satıldı → eldeki 0, ortalama yok
        satis = StokHareket.objects.get(fatura_satir__fatura=sip.fatura, tur="CIKIS", silindi=False)
        self.assertEqual((satis.tutar_try, satis.maliyet_durumu), (D("33.60"), "KESIN"))                 # satış maliyeti güncellendi (fixture tipinde maliyet fişi yok)
        out2 = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=1,20", stdout=out2)
        self.assertIn("FIYATLANAN GIRISLER (0)", out2.getvalue())                                         # ikinci koşu: fiyatsız giriş kalmadı

    def test_hatali_argumanlar(self):
        with self.assertRaisesMessage(CommandError, "KOD=TUTAR"):
            call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "Stok bulunamadı: YOK-KOD"):
            call_command("stok_maliyet_duzelt", "--fiyat", "YOK-KOD=1", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "sıfırdan büyük"):
            call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=0", stdout=StringIO())
