"""Tek seferlik: bir banka hesabının muhasebe hesabını yeni bir muavine taşır.

Yeni muavin hesap yoksa ``--ornek`` hesabın rapor ayarlarıyla (grup/kalem/parasal/aktif) açılır;
banka hesabı yeni hesaba bağlanır. Banka hesabına bağlı AKTİF hareket (fiş) varsa HİÇBİR ŞEY
yapılmaz (hareket kilidi — bakiye yevmiyeden hesaplandığı için hesap kaymamalı). Aynı muhasebe
hesabını paylaşan DİĞER banka hesaplarına dokunulmaz.

Varsayılan DRY-RUN (transaction geri alınır); ``--uygula`` kalıcı yazar.

    python manage.py banka_hesap_muhasebe_duzelt --hesap 7 --yeni-kod 102.02.0003 \\
        --yeni-ad "HALK BANKASI EUR HESABI" --ornek 102.02.0002 [--uygula]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import BankaHesap, HesapPlani
from core.services.finans import FinansHatasi, _hareket_kilidi, _yaprak_hesap_coz


class Command(BaseCommand):
    help = "Banka hesabinin muhasebe hesabini yeni muavine tasir (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--hesap", type=int, required=True, help="Banka hesabi id")
        parser.add_argument("--yeni-kod", required=True)
        parser.add_argument("--yeni-ad", required=True)
        parser.add_argument("--ornek", required=True, help="Rapor ayarlari kopyalanacak hesap kodu")
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        satirlar = []
        with transaction.atomic():
            h = (BankaHesap.objects.select_related("banka", "muhasebe")
                 .filter(pk=opts["hesap"], silindi=False).first())
            if h is None:
                raise CommandError("Banka hesabı bulunamadı.")
            eski = h.muhasebe
            satirlar.append(f"Banka hesabı {h.pk}: {h.banka.ad} · {h.ad} ({h.para_birimi}) — "
                            f"şimdiki muhasebe hesabı {eski.hesap_kodu} {eski.hesap_adi}")
            paylasan = list(BankaHesap.objects.filter(muhasebe=eski, silindi=False)
                            .exclude(pk=h.pk).values_list("pk", "ad", "para_birimi"))
            satirlar.append(f"{eski.hesap_kodu} hesabını paylaşan DİĞER banka hesapları "
                            f"(dokunulmaz): {paylasan or 'yok'}")
            n = h.fisler.filter(silindi=False).count()
            satirlar.append(f"Bu banka hesabına bağlı aktif hareket (fiş): {n}")
            if n:
                raise CommandError("Aktif hareket var; muhasebe hesabı değiştirilemez (önce raporlayın).")
            ornek = HesapPlani.objects.filter(hesap_kodu=opts["ornek"], silindi=False).first()
            if ornek is None:
                raise CommandError(f"Örnek hesap bulunamadı: {opts['ornek']}")
            yeni = HesapPlani.objects.filter(hesap_kodu=opts["yeni_kod"]).first()
            if yeni is not None and not yeni.silindi:
                satirlar.append(f"{yeni.hesap_kodu} zaten var: {yeni.hesap_adi}")
            else:
                yeni = HesapPlani.objects.update_or_create(
                    hesap_kodu=opts["yeni_kod"], defaults={
                        "hesap_adi": opts["yeni_ad"], "rapor_grubu": ornek.rapor_grubu,
                        "rapor_kalemi": ornek.rapor_kalemi, "parasal": ornek.parasal,
                        "aktif": ornek.aktif, "silindi": False, "silindi_at": None})[0]
                satirlar.append(f"{yeni.hesap_kodu} {yeni.hesap_adi}: AÇILDI (grup {yeni.rapor_grubu}, "
                                f"kalem {yeni.rapor_kalemi}, parasal {yeni.parasal}, aktif {yeni.aktif}; "
                                f"örnek {ornek.hesap_kodu})")
            try:
                yeni = _yaprak_hesap_coz(yeni.hesap_kodu)
                _hareket_kilidi(h, yeni_pb=h.para_birimi, yeni_muhasebe=yeni, ad="Banka hesabının")
            except FinansHatasi as e:
                raise CommandError(str(e))
            h.muhasebe = yeni
            h.save(update_fields=["muhasebe", "updated_at"])
            satirlar.append(f"Banka hesabı {h.pk} → muhasebe hesabı {yeni.hesap_kodu} {yeni.hesap_adi}")
            kalan = list(BankaHesap.objects.filter(muhasebe=eski, silindi=False).values_list("pk", "para_birimi"))
            satirlar.append(f"{eski.hesap_kodu} artık bağlı banka hesapları: {kalan}")
            if not uygula:
                transaction.set_rollback(True)
        w = self.stdout.write
        w(f"{'UYGULANDI' if uygula else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'}\n")
        for s in satirlar:
            w("  " + s)
        if not uygula:
            w(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))
