"""Migration 0144 (Yükleme Şekli kod — önek tespiti) testi: saf fonksiyon, admin tarafından
yeniden adlandırılmış gerçek prod satırlarıyla (ör. "FOB İZMİR" -> "FOB MERSİN-TÜRKİYE",
"EXW İZMİR (FABRİKA TESLİM)" -> "EXW – KAYSERİ, TÜRKİYE") — bkz. core.views._teslim_sekli_notu
bu koda göre dallanır; satır kod'suz kalırsa PDF güvenli varsayılana (eski statik cümle)
düşer, bu yüzden önek tespitinin admin'in yeniden adlandırmalarına dayanıklı olması kritik."""
import importlib

from django.test import TestCase

_mod = importlib.import_module("core.migrations.0144_yukleme_sekli_kod_onek")
_kod_tespit_et = _mod._kod_tespit_et


class KodTespitEtTest(TestCase):
    def test_orijinal_seed_metinleri(self):
        self.assertEqual(_kod_tespit_et("FOB İZMİR"), "FOB")
        self.assertEqual(_kod_tespit_et("EXW İZMİR (FABRİKA TESLİM)"), "EXW")
        self.assertEqual(_kod_tespit_et("CFR VARIŞ LİMANI"), "CFR")
        self.assertEqual(_kod_tespit_et("CIF VARIŞ LİMANI"), "CIF")
        self.assertEqual(_kod_tespit_et("DAP ALICI ADRESİ"), "DAP")
        self.assertEqual(_kod_tespit_et("NAKLİYE DAHİL (YURT İÇİ)"), "NAKLIYE_DAHIL")
        self.assertEqual(_kod_tespit_et("NAKLİYE HARİÇ (YURT İÇİ)"), "NAKLIYE_HARIC")

    def test_admin_tarafindan_yeniden_adlandirilmis_prod_satirlari(self):
        self.assertEqual(_kod_tespit_et("FOB MERSİN-TÜRKİYE"), "FOB")
        self.assertEqual(_kod_tespit_et("EXW – KAYSERİ, TÜRKİYE"), "EXW")
        self.assertEqual(_kod_tespit_et("NAKLİYE DAHİL"), "NAKLIYE_DAHIL")

    def test_tanimsiz_metin_none_doner(self):
        self.assertIsNone(_kod_tespit_et("DDP ALICI DEPOSU"))
        self.assertIsNone(_kod_tespit_et(""))
        self.assertIsNone(_kod_tespit_et(None))

    def test_yanlis_pozitif_vermez(self):
        """Bir şeyin FOB/EXW ile başlaması yetmiyor — hemen ardından boşluk/tire olmalı,
        yoksa "FOBXYZ" gibi alakasız bir metin yanlışlıkla FOB sayılabilir."""
        self.assertIsNone(_kod_tespit_et("FOBXYZ BİR ŞEY"))
        self.assertIsNone(_kod_tespit_et("EXWORKS BAŞKA"))
