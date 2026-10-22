"""FİRE KAYNAKLI EK PLAN — GERİYE DÖNÜK (bkz. core.services.uretim_fire): fire'lı ve fire kuralı devreye girmeden önce onaylanmış kayıtlar için ek ayırma / girdi emri
artışını TEK SEFERLİK uygular. Varsayılan KURU çalıştırma (hiçbir şey yazılmaz, geri alınır); ``--uygula`` kalıcı yazar. Zaten uygulanmış kayıt atlanır.

    python manage.py fire_ek_uygula OP-2026-0761 OP-2026-0777            # kuru
    python manage.py fire_ek_uygula OP-2026-0761 OP-2026-0777 --uygula
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import OperasyonKaydi
from core.services import stok_ayirma, uretim_fire


def _ds(x):
    return format(Decimal(str(x)).normalize(), "f")


class Command(BaseCommand):
    help = "Fireli onayli kayitlar icin fire ek planini geriye donuk uygular (varsayilan kuru)."

    def add_arguments(self, parser):
        parser.add_argument("kayitlar", nargs="+", help="operasyon kaydı numaraları (OP-2026-0761 …)")
        parser.add_argument("--uygula", action="store_true")
        parser.add_argument("--yeniden", action="store_true", help="zaten ek plan uygulanmış kaydın ekini geri sarıp güncel kuralla yeniden hesaplar")

    def handle(self, *args, **opts):
        kullanici = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        with transaction.atomic():
            for no in opts["kayitlar"]:
                kayit = OperasyonKaydi.objects.select_related("uretim_emri", "istasyon_emri", "operasyon__cikti").filter(
                    no=no, silindi=False, durum=OperasyonKaydi.Durum.ONAYLI).first()
                if kayit is None:
                    raise CommandError(f"{no}: onaylı, silinmemiş kayıt bulunamadı.")
                self.stdout.write(f"\n### {kayit.no} — {kayit.uretim_emri.no if kayit.uretim_emri_id else '-'} / {kayit.istasyon_emri.no if kayit.istasyon_emri_id else '-'} "
                                  f"{kayit.operasyon.cikti.kod} | fire {_ds(uretim_fire.kayit_fire(kayit))}")
                if kayit.fire_ek and opts["yeniden"] and not kayit.fire_ek.get("yenilendi") and not kayit.fire_ek.get("geri_alindi"):
                    onceki_ek = uretim_fire.fire_ek_geri_al(kayit, kullanici, yeniden=True)
                    for a in (onceki_ek or {}).get("ayirma", []):
                        self.stdout.write(f"   (geri sarıldı) AYIRMA {a['kod']} [{a['varyant']}]: −{a['miktar']}")
                    for x in (onceki_ek or {}).get("istasyon_emirleri", []):
                        self.stdout.write(f"   (geri sarıldı) {x['no']}: planlanan {x['once']} → {x['sonra']}")
                    kayit.refresh_from_db()
                if kayit.fire_ek:
                    self.stdout.write("   zaten uygulanmış / işaretli — atlandı")
                    continue
                if not uretim_fire.uygulanabilir(kayit):
                    self.stdout.write("   ÜS/istasyon emrine bağlı fasonsuz ÜRET kaydı değil — kapsam dışı")
                    continue
                once = {(a.stok.kod, a.varyant_id): a.miktar for a in stok_ayirma.emir_ayirmalari(kayit.uretim_emri)}
                sonuc = uretim_fire.fire_ek_planla(kayit, kullanici, geriye_donuk=not opts["yeniden"], hata_yut=False)
                if not sonuc or sonuc.get("uygulanmadi"):
                    self.stdout.write("   ek gerekmiyor (ÜS'nin açık kalan ihtiyacı ayırmayla karşılanıyor / emir tamam)")
                    if sonuc:
                        for y in sonuc.get("yaprak_eksik", []):
                            self.stdout.write(f"   ! karşılanamayan: {y}")
                    continue
                for a in sonuc["ayirma"]:
                    onceki = once.get((a["kod"], a["varyant_id"]), Decimal("0"))
                    self.stdout.write(f"   AYIRMA   {a['kod']} [{a['varyant']}]: +{a['miktar']}  (ÜS ayırması {_ds(onceki)} → {_ds(onceki + Decimal(a['miktar']))})")
                for f in sonuc["fason_siparis"]:
                    self.stdout.write(f"   FS REZERV {f['kod']} [{f['varyant']}]: +{f['miktar']}")
                for x in sonuc["istasyon_emirleri"]:
                    if x.get("sadece_ihtiyac"):
                        self.stdout.write(f"   İSTASYON EMRİ {x['no']} {x['operasyon']}: planlanan {x['once']} DEĞİŞMEZ, ihtiyaç +{x['ihtiyac_ek']} (mevcut plan fazlasından)")
                        continue
                    self.stdout.write(f"   İSTASYON EMRİ {x['no']} {x['operasyon']}: planlanan {x['once']} → {x['sonra']} (+{x['ek']}){' YENİ' if x['yeni'] else ''}"
                                      f" {x['durum_once'] or '-'} → {x['durum_sonra']}{' FASON' if x['fason'] else ''}")
                for y in sonuc.get("yaprak_eksik", []):
                    self.stdout.write(f"   ! karşılanamayan (eksik malzeme tablosunda görünür): {y}")
            if not opts["uygula"]:
                transaction.set_rollback(True)
                self.stdout.write("\n(KURU: hiçbir şey yazılmadı, geri alındı)")
            else:
                self.stdout.write("\nUYGULANDI.")
