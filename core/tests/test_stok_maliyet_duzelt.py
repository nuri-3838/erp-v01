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

    def test_fis_ile_uygula_giderden_stoga_aktarim_ve_fis_korumali(self):
        from django.urls import reverse
        from core.models import HesapPlani, Kur
        from core.tests.test_teklif_siparis import _hesap
        Kur.objects.get_or_create(tarih=date(2026, 1, 2), defaults={"usd_alis": D("40")})
        _hesap("770.08", "OFİS MALZEMELERİ GİDERİ", grup="GELIR_TABLOSU", kalem="A")
        sip = self.kur_fiyatsiz()
        out = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=1", "--fis", "150-50-0004=770.08:Kullanma kılavuzu iç baskı maliyeti – giderden stoğa aktarım",
                     stdout=out)
        self.assertIn("YENI FISLER (1;", out.getvalue())
        self.assertIn("MIZAN KONTROLU", out.getvalue())
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="STOK_DUZELTME").exists())                       # dry-run: fiş kalmadı
        call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=1", "--fis", "150-50-0004=770.08:Kullanma kılavuzu iç baskı maliyeti – giderden stoğa aktarım",
                     "--uygula", stdout=StringIO())
        giris = StokHareket.objects.get(stok=self.ham2, tur="GIRIS")
        fis = giris.fis
        self.assertEqual((giris.giris_tutar_try, fis.kaynak, fis.tarih, fis.aciklama),
                         (D("50.00"), YevmiyeFisi.Kaynak.STOK_DUZELTME, date(2026, 1, 2), "KULLANMA KILAVUZU İÇ BASKI MALİYETİ – GİDERDEN STOĞA AKTARIM"))   # fiş açıklaması büyük harf
        stok_hesabi = HesapPlani.objects.get(hesap_kodu="153.10").hesap_kodu                                    # fixture: ham kategorisi 153.10
        self.assertEqual(sorted((s.hesap.hesap_kodu, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False)),
                         sorted([(stok_hesabi, D("50.00"), D("0.00")), ("770.08", D("0.00"), D("50.00"))]))
        cikti = StokHareket.objects.get(stok=self.stok, tur="GIRIS", kaynak="URETIM", silindi=False)
        self.assertEqual((cikti.tutar_try, cikti.maliyet_durumu), (D("33.00"), "KESIN"))                      # 30 + 3 × 1,00
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:fis_sil", args=[fis.pk]), follow=True)                            # elle silinemez
        self.assertTrue(any("elle silinemez" in str(m) for m in r.context["messages"]))
        fis.refresh_from_db()
        self.assertFalse(fis.silindi)
        r = self.client.get(reverse("core:fis_duzenle", args=[fis.pk]), follow=True)
        self.assertTrue(any("elle düzenlenemez" in str(m) for m in r.context["messages"]))

    def test_irsaliye_girisine_fis_kesilmez_fatura_gelince_fiyat_yerinde_ezilir(self):
        from core.models import Kur
        from core.services import stok_ortalama as so
        from core.tests.test_teklif_siparis import _hesap
        _hesap("770.08", "OFİS MALZEMELERİ GİDERİ", grup="GELIR_TABLOSU", kalem="A")
        Kur.objects.get_or_create(tarih=date(2026, 1, 2), defaults={"usd_alis": D("40")})
        tapa = Stok.objects.create(kod="151-40-0007", ad="PLASTİK Q16 TAPA", kategori=self.ham.kategori, kdv=self.kdv,
                                   uretim_birimi=self.ham.uretim_birimi, fatura_birimi=self.ham.fatura_birimi, satinalma_urunu=True, uretim_urunu=False)
        giris = hareket_ekle(stok_id=tapa.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("100"),
                             kaynak=StokHareket.Kaynak.IRSALIYE)                                                 # faturasız irsaliye girişi (fiyatsız)
        out = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", "151-40-0007=1", "--fis", "151-40-0007=770.08", "--uygula", stdout=out)
        self.assertIn("YENI FISLER (0;", out.getvalue())
        self.assertIn("irsaliye girişi: fatura gelince", out.getvalue())
        giris.refresh_from_db()
        self.assertEqual((giris.giris_tutar_try, giris.fis_id, giris.maliyet_durumu), (D("100.00"), None, "KESIN"))
        # Fatura gelince: irsaliye_girislerini_fiyatla girişi önce fiyatsıza döndürür, sonra fatura tutarını AYNI harekete yazar (silme yok;
        # gerçek akış test_stok_ortalama.IrsaliyeliFaturaTest'te) — burada aynı adımlar elle
        giris.giris_tutar_try = None
        giris.save(update_fields=["giris_tutar_try"])
        giris.giris_tutar_try = D("130.00")                                                                       # fatura 100 × 1,30
        giris.save(update_fields=["giris_tutar_try"])
        so.yeniden_hesapla(tapa)
        self.assertEqual(StokHareket.objects.filter(stok=tapa, silindi=False).count(), 1)
        self.assertEqual(Stok.objects.get(pk=tapa.pk).ort_maliyet_try, D("1.300000"))

    def test_hatali_argumanlar(self):
        with self.assertRaisesMessage(CommandError, "KOD=TUTAR"):
            call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "Stok bulunamadı: YOK-KOD"):
            call_command("stok_maliyet_duzelt", "--fiyat", "YOK-KOD=1", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "sıfırdan büyük"):
            call_command("stok_maliyet_duzelt", "--fiyat", "150-50-0004=0", stdout=StringIO())
