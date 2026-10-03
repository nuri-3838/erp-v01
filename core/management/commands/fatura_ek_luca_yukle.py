"""Tek seferlik: Luca'dan indirilen GELEN e-fatura paketlerindeki (zip) XML (UBL) ve PDF
dosyalarını ERP alış faturalarına EK olarak yükler. Dosya adı: FaturaNo_SaticiVKN_Unvan.(xml|pdf).

Eşleştirme (XML'den okunan fatura no + satıcı VKN/TCKN ile; XML yoksa dosya adından):
  1) fatura no + cari VKN/TCKN   2) yalnız fatura no (VKN uyuşmuyor — raporda işaretlenir)
  3) eşleşmedi. Birden fazla aday varsa dosya YÜKLENMEZ, "çok adaylı" olarak raporlanır.
Tutar/tarih farkı olan eşleşmelere de yükleme yapılır, raporda işaretlenir. Faturada aynı adlı
ek zaten varsa atlanır (komut tekrar çalıştırılabilir). Hiçbir fatura kaydı DEĞİŞTİRİLMEZ;
yalnız FaturaEk eklenir.

Varsayılan DRY-RUN (hiçbir şey yazmaz). ``--uygula`` ekleri yükler.

    python manage.py fatura_ek_luca_yukle --zip A.zip --zip B.zip [--uygula]

İRSALİYE MODU (``--irsaliye-zip``): Luca'dan indirilen gelen e-irsaliye XML/PDF paketlerini ERP alış
faturalarına ek olarak bağlar (bkz. core.services.luca_irsaliye). ``--zip`` bu modda fatura XML
paketleridir (irsaliye referanslarını okumak için; fatura dosyası YÜKLENMEZ).

    python manage.py fatura_ek_luca_yukle --zip FATURA_XML.zip --irsaliye-zip IR_XML.zip \
        --irsaliye-zip IR_PDF.zip [--haric-irsaliye NO ...] [--uygula]
"""
import re
from collections import defaultdict
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from core.models import Fatura, FaturaEk, FaturaTipi
from core.services import fatura_ek as ek_servis
from core.services.fatura import _fatura_no_anahtar
from core.services.luca_paket import paketleri_oku as _paketleri_oku
from core.services.ubl_fatura import UblHatasi, ubl_oku

GERCEK_TARIH = re.compile(r"^GERÇEK TARİH: (\d{2})\.(\d{2})\.(\d{4})\. ")
TOLERANS = Decimal("0.01")


def _erp_tarihi(f):
    """Tarih taşıma işleminden geçen faturada gerçek tarih açıklamanın başında durur."""
    import datetime
    m = GERCEK_TARIH.match(f.aciklama or "")
    if m:
        return datetime.date(int(m[3]), int(m[2]), int(m[1]))
    return f.tarih


class Command(BaseCommand):
    help = "Luca gelen e-fatura zip'lerini (XML+PDF) alis faturalarina ek yukler (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--zip", action="append", required=True, dest="zipler")
        parser.add_argument("--uygula", action="store_true", help="Ekleri yukle.")
        parser.add_argument("--irsaliye-zip", action="append", dest="irsaliye_zipleri", default=[],
                            help="Gelen e-irsaliye zip'i (irsaliye modu).")
        parser.add_argument("--haric-irsaliye", action="append", default=[],
                            help="Bu irsaliye no'lari yuklenmez (irsaliye modu).")

    def handle(self, *args, **opts):
        if opts["irsaliye_zipleri"]:
            return self._irsaliye_modu(opts)
        gruplar, tanimayan, tekrar = _paketleri_oku(opts["zipler"])

        # ERP alış faturaları: normalize fatura no -> [fatura]
        indeks = defaultdict(list)
        for f in (Fatura.objects.filter(yon=FaturaTipi.Yon.ALIS, silindi=False)
                  .exclude(fatura_no="").select_related("cari")):
            indeks[_fatura_no_anahtar(f.fatura_no)].append(f)

        okunamadi, eslesmeyen, cok_adayli, satirlar = [], [], [], []
        for (no_anahtar, vkn), dosyalar in sorted(gruplar.items()):
            xml = None
            if "xml" in dosyalar:
                try:
                    xml = ubl_oku(dosyalar["xml"][1])
                except UblHatasi as e:
                    okunamadi.append((dosyalar["xml"][0], str(e)))
                    continue
            no = _fatura_no_anahtar(xml["fatura_no"]) if xml else no_anahtar
            vkn_x = (xml["vkn"] if xml and xml["vkn"] else vkn)
            adaylar = indeks.get(no, [])
            tam = [f for f in adaylar if f.cari.vkn_tckn and f.cari.vkn_tckn == vkn_x]
            if len(tam) == 1:
                f, tur = tam[0], "VKN+NO"
            elif len(tam) > 1:
                cok_adayli.append((no, vkn_x, xml, adaylar_ozet(tam)))
                continue
            elif len(adaylar) == 1:
                f, tur = adaylar[0], "YALNIZ NO"
            elif len(adaylar) > 1:
                cok_adayli.append((no, vkn_x, xml, adaylar_ozet(adaylar)))
                continue
            else:
                eslesmeyen.append((no, vkn_x, xml, dosyalar))
                continue
            satirlar.append((f, tur, xml, dosyalar))

        # aynı faturaya birden çok paket mi eşleşti?
        sayac = defaultdict(int)
        for f, *_ in satirlar:
            sayac[f.pk] += 1
        ayni_fatura = sorted(pk for pk, n in sayac.items() if n > 1)

        yuklenecek = atlanacak = hatali = 0
        rapor = []          # (f, tur, farklar, yuklenen_adlar, atlanan_adlar)
        for f, tur, xml, dosyalar in satirlar:
            mevcut = set(FaturaEk.objects.filter(fatura=f, silindi=False)
                         .values_list("orijinal_ad", flat=True))
            yuklenen, atlanan, farklar = [], [], []
            if xml:
                if xml["para_birimi"] != f.para_birimi:
                    farklar.append(f"para birimi ERP {f.para_birimi} / XML {xml['para_birimi']}")
                adaylar_t = {t for t in (f.odenecek, f.genel_toplam) if t is not None}
                xml_t = [t for t in (xml["odenecek"], xml["kdv_dahil"]) if t is not None]
                if not any(abs(a - x) <= TOLERANS for a in adaylar_t for x in xml_t):
                    farklar.append(f"tutar ERP {f.odenecek} (KDV dahil {f.genel_toplam}) / "
                                   f"XML ödenecek {xml['odenecek']} (KDV dahil {xml['kdv_dahil']})")
                if _erp_tarihi(f) != xml["tarih"]:
                    farklar.append(f"tarih ERP {_erp_tarihi(f):%d.%m.%Y} / XML {xml['tarih']:%d.%m.%Y}")
            if tur == "YALNIZ NO":
                farklar.insert(0, f"VKN uyuşmuyor: ERP cari '{f.cari.vkn_tckn or '-'}' / dosya "
                                  f"'{xml['vkn'] if xml else '?'}'")
            for uz in ("xml", "pdf"):
                if uz not in dosyalar:
                    continue
                ad, bayt = dosyalar[uz]
                if ad in mevcut:
                    atlanan.append(ad)
                    atlanacak += 1
                    continue
                if opts["uygula"]:
                    try:
                        ek_servis.ek_ekle(f, dosya=ContentFile(bayt, name=ad))
                        yuklenen.append(ad)
                        yuklenecek += 1
                    except ek_servis.FaturaEkHatasi as e:
                        farklar.append(f"YÜKLENEMEDİ {ad}: {e}")
                        hatali += 1
                else:
                    yuklenen.append(ad)
                    yuklenecek += 1
            rapor.append((f, tur, farklar, yuklenen, atlanan))

        self._yaz(opts["uygula"], gruplar, tanimayan, tekrar, rapor, eslesmeyen, cok_adayli,
                  okunamadi, ayni_fatura, yuklenecek, atlanacak, hatali)

    def _irsaliye_modu(self, opts):
        from core.services import luca_irsaliye
        uygula = opts["uygula"]
        r = luca_irsaliye.eslestir(opts["zipler"], opts["irsaliye_zipleri"],
                                   haric=opts["haric_irsaliye"], uygula=uygula)
        w = self.stdout.write
        w(f"{'UYGULANDI' if uygula else 'DRY-RUN (hicbir sey yazilmadi)'} — IRSALIYE MODU — "
          f"{r['grup_sayisi']} irsaliye grubu\n")
        w("ÖZET")
        w(f"  Eşleşen                : {len(r['eslesen'])}")
        w(f"  Eşleşmeyen             : {len(r['eslesmeyen'])}")
        w(f"  Birden fazla adaylı    : {len(r['cok_adayli'])}")
        w(f"  Hariç tutulan          : {len(r['haric'])}")
        w(f"  XML okunamadı          : {len(r['okunamadi'])}")
        w(f"  {'Yüklenen' if uygula else 'Yüklenecek'} dosya : {r['yuklenen']}"
          f"   (zaten ekli, atlanan: {r['atlanan']})")
        w(f"  Fatura XML'inde yazım hatalı irsaliye no: {len(r['yazim_hatalari'])}")
        w("\nEŞLEŞENLER  irsaliye no → fatura id / no | kaynak | yüklenecek (X=xml P=pdf)")
        for e, yuk, atl in r["eslesen"]:
            f = e["fatura"]
            k = "".join("X" if a.lower().endswith(".xml") else "P" for a in yuk) or "-"
            w(f"  {e['no']:<18} → {f.pk:<5} {f.fatura_no:<20} | {e['kaynak']:<28} | {k}"
              f"{'  (zaten ekli: ' + str(len(atl)) + ')' if atl else ''}")
        if r["yazim_hatalari"]:
            w("\nFATURA XML'İNDE YAZIM HATALI İRSALİYE NO")
            for y in r["yazim_hatalari"]:
                f = y["fatura"]
                s = y["sonuc"]
                w(f"  fatura {f.pk} {f.fatura_no}: XML'de '{y['xml_ref']}' — doğrusu '{y['dogru_no']}' | "
                  + (f"eşleşti → fatura {s['fatura'].pk} ({s['kaynak']})" if s else "irsaliye eşleşmedi"))
        if r["eslesmeyen"]:
            w("\nEŞLEŞMEYEN (yüklenmez)  irsaliye no | satıcı | tarih | neden")
            for no, vkn, xml, neden, oneri in r["eslesmeyen"]:
                w(f"  {no} | {(xml['unvan'] if xml else '?')[:32]} ({vkn}) | "
                  f"{xml['tarih']:%d.%m.%Y}" if xml else f"  {no} | ? ({vkn}) | ?")
                w(f"      neden: {neden}")
                for ham, f in oneri:
                    w(f"      öneri (yüklenmez): fatura {f.pk} {f.fatura_no} XML'inde benzer ref '{ham}'")
        if r["cok_adayli"]:
            w("\nBİRDEN FAZLA ADAYLI (yüklenmez)")
            for no, vkn, fl in r["cok_adayli"]:
                w(f"  {no} ({vkn}) -> " + ", ".join(f"fatura {f.pk} {f.fatura_no}" for f in fl))
        if r["haric"]:
            w("\nHARİÇ TUTULAN (yüklenmez)")
            for no, vkn, xml, ad in r["haric"]:
                w(f"  {no} | {(xml['unvan'] if xml else ad)[:35]} ({vkn})"
                  + (f" | {xml['tarih']:%d.%m.%Y}" if xml else ""))
        if r["okunamadi"]:
            w("\nXML OKUNAMADI")
            for ad, e in r["okunamadi"]:
                w(f"  {ad}: {e}")
        if r["tanimayan"]:
            w("\nAD DESENİ TANINMAYAN: " + "; ".join(r["tanimayan"]))
        if not uygula:
            w(self.style.WARNING("\nDRY-RUN: yuklemek icin --uygula."))

    def _yaz(self, uygula, gruplar, tanimayan, tekrar, rapor, eslesmeyen, cok_adayli, okunamadi,
             ayni_fatura, yuklenecek, atlanacak, hatali):
        w = self.stdout.write
        xml_n = sum("xml" in d for d in gruplar.values())
        pdf_n = sum("pdf" in d for d in gruplar.values())
        w(f"{'UYGULANDI' if uygula else 'DRY-RUN (hicbir sey yazilmadi)'} — "
          f"{len(gruplar)} fatura grubu ({xml_n} XML, {pdf_n} PDF)\n")
        tam = [r for r in rapor if r[1] == "VKN+NO"]
        yalniz = [r for r in rapor if r[1] == "YALNIZ NO"]
        farkli = [r for r in rapor if any(not x.startswith("VKN") for x in r[2])]
        w("ÖZET")
        w(f"  Eşleşen (fatura no + VKN)   : {len(tam)}")
        w(f"  Eşleşen (yalnız fatura no)  : {len(yalniz)}   <- VKN uyuşmuyor, kontrol edin")
        w(f"  Tutar/tarih/para farkı olan : {len(farkli)}")
        w(f"  Eşleşmeyen                  : {len(eslesmeyen)}")
        w(f"  Birden fazla adaylı         : {len(cok_adayli)}")
        w(f"  XML okunamadı               : {len(okunamadi)}")
        w(f"  {'Yüklenen' if uygula else 'Yüklenecek'} dosya        : {yuklenecek}"
          f"   (zaten ekli, atlanan: {atlanacak}" + (f", HATALI: {hatali})" if hatali else ")"))
        if tanimayan or tekrar or ayni_fatura:
            w(f"  Ad deseni tanınmayan: {len(tanimayan)}; zip'lerde tekrar eden: {len(tekrar)}; "
              f"aynı faturaya birden çok paket: {len(ayni_fatura)} {ayni_fatura or ''}")

        if farkli or yalniz:
            w("\nFARKLI / DİKKAT (yine de yüklenir)")
            for f, tur, farklar, *_ in rapor:
                if farklar:
                    w(f"  fatura {f.pk} {f.fatura_no} [{tur}] {f.cari.unvan[:30]}")
                    for x in farklar:
                        w(f"      - {x}")
        if eslesmeyen:
            w("\nEŞLEŞMEYEN (yüklenmez)  fatura no | satıcı | tarih | tutar")
            for no, vkn, xml, dosyalar in eslesmeyen:
                ad = next(iter(dosyalar.values()))[0]
                if xml:
                    w(f"  {xml['fatura_no']} | {xml['unvan'][:35]} ({vkn}) | {xml['tarih']:%d.%m.%Y}"
                      f" | {xml['odenecek']} {xml['para_birimi']}")
                else:
                    w(f"  {no} | {ad} (XML yok: tarih/tutar okunamadı)")
        if cok_adayli:
            w("\nBİRDEN FAZLA ADAYLI (yüklenmez)")
            for no, vkn, xml, aday in cok_adayli:
                w(f"  {no} (VKN {vkn}"
                  + (f", {xml['tarih']:%d.%m.%Y}, {xml['odenecek']}" if xml else "") + f") -> {aday}")
        if okunamadi:
            w("\nXML OKUNAMADI (yüklenmez)")
            for ad, e in okunamadi:
                w(f"  {ad}: {e}")
        if tanimayan:
            w("\nAD DESENİ TANINMAYAN: " + "; ".join(tanimayan))
        w("\nEŞLEŞENLER  fatura | no | tür | yüklenecek (X=xml P=pdf) | atlanan")
        for f, tur, farklar, yuk, atl in rapor:
            k = "".join(("X" if a.lower().endswith(".xml") else "P") for a in yuk) or "-"
            w(f"  {f.pk:<5} {f.fatura_no:<18} {tur:<9} {k:<3} {len(atl) or ''}"
              f"{'  [FARK]' if any(not x.startswith('VKN') for x in farklar) else ''}")
        if not uygula:
            w(self.style.WARNING("\nDRY-RUN: yuklemek icin --uygula."))


def adaylar_ozet(adaylar):
    return ", ".join(f"fatura {f.pk} ({f.cari.unvan[:25]}, {f.tarih:%d.%m.%Y})" for f in adaylar)
