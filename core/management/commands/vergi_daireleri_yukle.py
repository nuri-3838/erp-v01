"""GİB resmî vergi dairesi listesini (docs/vergi_daireleri_gib.csv; kaynak/indirme tarihi dosyanın başında) VergiDairesi tanımlarına yükler.

Varsayılan ÖNİZLEME (hiçbir şey yazmaz; sonuç --uygula ile birebir aynıdır: işlem tek transaction'da yapılıp önizlemede geri alınır). ``--uygula`` tek transaction'da yazar.
İDEMPOTENT: kayıt ``kod`` ile eşleşir; varsa il / resmî ad güncellenir, yoksa eklenir; ikinci çalıştırmada 0 değişiklik. HİÇBİR kayıt silinmez/pasife alınmaz.

``ad`` = GİB adından "Vergi Dairesi Müdürlüğü" eki atılmış kısa ad (TR büyük harf; ör. "Yenikapı Vergi Dairesi Müdürlüğü" → "YENİKAPI"). Aynı kısa ad birden fazla ilde varsa
(ör. GÖLBAŞI) kartlarda ayırt edilebilsin diye "GÖLBAŞI (ANKARA)" yazılır. Cari kartında yalnız bu ``ad`` metni saklanır — MEVCUT KAYITLARIN ``ad``ı bu komut tarafından ASLA değiştirilmez
(cari bağları bozulmasın).

MEVCUT (kodsuz, eski/elle girilmiş) KAYITLAR:
  * ad AYNEN eşleşen ve listede tek daire olan kayıt (ör. ERCİYES → Kayseri 38252) SİLİNMEDEN/ÇOĞALTILMADAN aynı kayda bağlanır (kod, il, resmî ad yazılır; pk ve ad aynı kalır).
  * ad FARKIYLA eşleşen (ör. "MİMARSİNAN" ↔ "Mimar Sinan") kayıt ONAY BEKLEYEN eşleşme olarak raporlanır; kayıt değişmez, resmî daire EKLENMEZ (çoğaltma olmasın).
    Onaylanan eşleşmeler ``--eslestirme DOSYA.csv`` (sütunlar: eski_ad,kod) ile uygulanır.
  * Listede karşılığı olmayan eski kayıtlar yalnız raporlanır (pasife alma kararı kullanıcıya aittir).

    python manage.py vergi_daireleri_yukle                 # önizleme
    python manage.py vergi_daireleri_yukle --uygula
    python manage.py vergi_daireleri_yukle --uygula --eslestirme onaylanan.csv
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, VergiDairesi

VARSAYILAN_CSV = Path(settings.BASE_DIR) / "docs" / "vergi_daireleri_gib.csv"
_EK = "Vergi Dairesi Müdürlüğü"
_GURULTU = re.compile(r"\b(VERGİ DAİRESİ MÜDÜRLÜĞÜ|VERGİ DAİRESİ|VERGİ DAİRE|MÜDÜRLÜĞÜ|MÜD|V\.?D)\b\.?")


def kisa_ad(resmi_ad: str) -> str:
    return buyuk_harf_tr(resmi_ad.replace(_EK, "").strip())


def bulanik(ad: str) -> str:
    """Ad farkıyla eşleşme anahtarı: TR büyük harf, 'VERGİ DAİRESİ/MÜD./V.D.' gürültüsü atılmış, boşluk/noktalama yok ('MİMAR SİNAN' = 'MİMARSİNAN')."""
    s = _GURULTU.sub(" ", buyuk_harf_tr(ad or ""))
    return re.sub(r"[^0-9A-ZÇĞİIÖŞÜ]", "", s)


def csv_oku(yol):
    """-> (başlık yorum satırları, [satır sözlükleri])."""
    yorumlar, govde = [], []
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            (yorumlar if satir.startswith("#") else govde).append(satir)
    return yorumlar, list(csv.DictReader(govde))


def eslestirme_oku(yol):
    with open(yol, encoding="utf-8") as f:
        return {buyuk_harf_tr(r["eski_ad"].strip()): r["kod"].strip() for r in csv.DictReader(l for l in f if not l.startswith("#"))}


class Command(BaseCommand):
    help = "GİB resmî vergi dairesi listesini yükler (varsayılan önizleme; --uygula tek transaction; idempotent; kayıt silmez)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(VARSAYILAN_CSV), help="Veri dosyası (varsayılan docs/vergi_daireleri_gib.csv).")
        parser.add_argument("--uygula", action="store_true", help="Yaz (yoksa önizleme).")
        parser.add_argument("--eslestirme", default=None, help="Onaylanan ad-farkı eşleşmeleri CSV'si (eski_ad,kod).")

    def handle(self, *args, **o):
        try:
            yorumlar, satirlar = csv_oku(o["csv"])
        except OSError as e:
            raise CommandError(f"Veri dosyası okunamadı: {e}")
        onayli = eslestirme_oku(o["eslestirme"]) if o["eslestirme"] else {}
        uygula = o["uygula"]
        w = self.stdout.write

        resmi = {}
        for r in satirlar:
            if r["kod"] in resmi:
                raise CommandError(f"Veri dosyasında kod tekrarı: {r['kod']}")
            resmi[r["kod"]] = {"il": r["il"], "ilce": r["ilce"], "resmi_ad": r["resmi_ad"], "kisa": kisa_ad(r["resmi_ad"])}
        say = Counter(v["kisa"] for v in resmi.values())
        for v in resmi.values():
            v["ad"] = f"{v['kisa']} ({v['il']})" if say[v["kisa"]] > 1 else v["kisa"]            # aynı kısa ad birden çok ilde → il eklenir

        w(self.style.WARNING("UYGULAMA MODU" if uygula else "ÖNİZLEME (hiçbir şey yazılmaz; --uygula ile yazılır)"))
        for y in yorumlar[:6]:
            w("  " + y.rstrip("\n"))
        r = self._isle(resmi, onayli, uygula)

        w(f"\nResmî liste            : {len(resmi)} daire, {len({v['il'] for v in resmi.values()})} il")
        w(f"Eklenen                : {len(r['eklenen'])}")
        w(f"Mevcut kayda bağlanan  : {len(r['baglanan'])}  (ad aynen eşleşti; pk/ad değişmedi → cari bağları korunur)")
        w(f"Güncellenen            : {len(r['guncellenen'])}  (il / resmî ad)")
        w(f"Değişmeyen             : {r['degismeyen']}")
        w(f"Onay bekleyen eşleşme  : {len(r['bekleyen'])}  (ad farkıyla eşleşen eski kayıt — resmî daire EKLENMEDİ)")
        w(f"Listede olmayan eski   : {len(r['eski'])}  (silinmez/pasife alınmaz; yalnız rapor)")
        w(f"TOPLAM DEĞİŞİKLİK      : {len(r['eklenen']) + len(r['baglanan']) + len(r['guncellenen'])}")

        if r["baglanan"]:
            w("\n== MEVCUT KAYDA BAĞLANAN (eski ad = resmî kısa ad)")
            for m, kod, d, n in r["baglanan"]:
                w(f"  #{m.pk:<4} {m.ad:22} → {d['il']:12} {kod}  {d['resmi_ad']}  | bu adı kullanan cari: {n}")
        if r["bekleyen"]:
            w("\n== ONAY BEKLEYEN EŞLEŞME (ad farkı) — onaylanırsa --eslestirme ile uygulanır")
            for m, kod, d, n in r["bekleyen"]:
                w(f"  #{m.pk:<4} {m.ad:22} ↔ {d['il']:12} {kod}  {d['resmi_ad']}  | cari: {n}")
        if r["eski"]:
            w("\n== LİSTEDE OLMAYAN ESKİ KAYITLAR")
            for m, n in r["eski"]:
                w(f"  #{m.pk:<4} {m.ad:22} il={m.il or '-':8} kod={m.kod or '-'} | cari: {n}")
        if r["ad_farki"]:
            w("\n== KODU EŞLEŞEN AMA AD FARKLI (ad değiştirilmedi)")
            for m, d in r["ad_farki"]:
                w(f"  #{m.pk:<4} kayıtlı '{m.ad}' / resmî '{d['ad']}'")
        w("\n== İL BAZINDA RESMÎ DAİRE SAYISI")
        iller = Counter(v["il"] for v in resmi.values())
        satir = []
        for il, n in sorted(iller.items()):
            satir.append(f"{il} {n}")
            if len(satir) == 6:
                w("  " + " · ".join(satir))
                satir = []
        if satir:
            w("  " + " · ".join(satir))
        w("\n" + (self.style.SUCCESS("UYGULANDI (commit).") if uygula else "Önizleme bitti — hiçbir kayıt değişmedi."))

    def _isle(self, resmi, onayli, uygula):
        eklenen, baglanan, guncellenen, bekleyen, ad_farki = [], [], [], [], []
        degismeyen = 0
        with transaction.atomic():
            mevcut = list(VergiDairesi.objects.filter(silindi=False))
            by_kod = {m.kod: m for m in mevcut if m.kod}
            eski = [m for m in mevcut if not m.kod]
            sahipli = set()
            cari_sayi = defaultdict(int, Counter(Cari.objects.filter(silindi=False).exclude(vergi_dairesi="").values_list("vergi_dairesi", flat=True)))
            say = Counter(v["kisa"] for v in resmi.values())
            onayli_kodlar = {k for k in onayli.values()}
            for kod in sorted(resmi):
                d = resmi[kod]
                m = by_kod.get(kod)
                if m is not None:
                    degisti = False
                    if m.il != d["il"]:
                        m.il, degisti = d["il"], True
                    if m.resmi_ad != d["resmi_ad"]:
                        m.resmi_ad, degisti = d["resmi_ad"], True
                    if m.ad != d["ad"]:
                        ad_farki.append((m, d))                                  # ad'a DOKUNULMAZ (kartlardaki metin bağı)
                    if degisti:
                        m.save(update_fields=["il", "resmi_ad", "updated_at"])
                        guncellenen.append((m, kod, d))
                    else:
                        degismeyen += 1
                    continue
                aday = None
                if kod in onayli_kodlar:                                         # kullanıcının onayladığı ad-farkı eşleşmesi
                    aday = next((e for e in eski if e.pk not in sahipli and onayli.get(e.ad) == kod), None)
                if aday is None:                                                 # ad AYNEN: kısa ad listede tekse; ya da (ad, il) birebir
                    aday = next((e for e in eski if e.pk not in sahipli and ((e.ad == d["kisa"] and say[d["kisa"]] == 1 and e.il in ("", d["il"]))
                                                                             or (e.ad == d["ad"] and e.il == d["il"]))), None)
                if aday is not None:
                    sahipli.add(aday.pk)
                    aday.kod, aday.resmi_ad = kod, d["resmi_ad"]
                    if not aday.il:
                        aday.il = d["il"]
                    aday.save(update_fields=["kod", "il", "resmi_ad", "updated_at"])
                    baglanan.append((aday, kod, d, cari_sayi.get(aday.ad, 0)))
                    continue
                bulanik_aday = next((e for e in eski if e.pk not in sahipli and bulanik(e.ad) == bulanik(d["kisa"]) and e.il in ("", d["il"])), None)
                if bulanik_aday is not None:
                    bekleyen.append((bulanik_aday, kod, d, cari_sayi.get(bulanik_aday.ad, 0)))      # çoğaltma yok: karar kullanıcıda
                    sahipli.add(bulanik_aday.pk)
                    continue
                VergiDairesi.objects.create(ad=d["ad"], il=d["il"], kod=kod, resmi_ad=d["resmi_ad"], aktif=True)
                eklenen.append((kod, d))
            eski_kalan = [(e, cari_sayi.get(e.ad, 0)) for e in eski if e.pk not in sahipli]
            if not uygula:
                transaction.set_rollback(True)
        return {"eklenen": eklenen, "baglanan": baglanan, "guncellenen": guncellenen, "degismeyen": degismeyen, "bekleyen": bekleyen, "eski": eski_kalan, "ad_farki": ad_farki}
