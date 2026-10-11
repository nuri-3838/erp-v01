"""E-6: VARYANTLI PLANLAMA — planlama motoru (stok, varyant) düğümleriyle (docs/varyant_kararlari.md, adım E-6).

Anahtar: AYARLAR › Üretim Ayarları › "Varyantlı planlama" (``UretimAyari.varyantli_planlama``, varsayılan KAPALI). KAPALI iken
``uretim.ihtiyac_hesapla`` / ÜS açılışı / istasyon emirleri / kayıtlar bugünkü gibi KART bazında çalışır (bu modül hiç devreye girmez) ve
özel varyantlı siparişten ÜS açılmaz. AÇIK iken:

* Planın her düğümü ``Dugum(stok, varyant)``: varyantlı kartta istenen ``StokVaryant``, varyantsız kartta None. Kullanılabilir stok düğüm
  bazındadır (o varyantın eldekisi − o varyantın ayrılanı; ``stok_ayirma.kullanilabilir_varyant_haritasi``) — standart dışı mamul 152'nin
  serbest (standart) stoğundan karşılanmaz, markasız ayak stoğu markalı ihtiyacı karşılamaz.
* Operasyon seçimi (``Cozucu.tanim_sec``): kartı üreten tanımın çıktı kuralı TAŞI ise o tanım istenen varyantı üretir; SABİT ise yalnız sabit
  değeri üretir — istenen başka bir varyantsa kartın DÖNÜŞTÜR tanımı (lazer markalama: girdi SABİT YOK → çıktı TAŞI) seçilir, onun girdisi
  (kart, YOK) düğümü ise kesimden gelir. Dönüştür tanımı yoksa UretimHatasi.
* Girdi varyantı (``girdi_varyanti``): girdi varyantsızsa None; SABİT → tanımdaki sabit değer (tüm boyutlar); TAŞI → çıktı varyantının
  aynı boyuttaki değeri, çıktıda olmayan boyut için girdi kartının varsayılan varyantındaki değer. Satın alınan (operasyonsuz) varyantlı
  girdinin TÜM boyutları YOK çıkıyorsa girdi satırı planda/kayıtta YOKTUR (müşteri afiş/kılavuz/yan etiket istemiyor: tüketim yok).
* Aynı tanım bir ÜS'de çıktı varyantı başına AYRI istasyon emridir (``IstasyonEmri.cikti_varyant``; çift çıkış birleştirme: markalı + markasız).

``ihtiyac_hesapla(..., cozucu=Cozucu())`` motorun kendisini değiştirmez: düğümler ``pk``/``kod`` taşıyan hafif nesnelerdir; operasyon koşumu
``Kosum`` (pk = (tanım pk, referans çıktı varyantı)) tanıma vekâlet eder. Önbellekler çağrı/çözücü ömürlüdür (süreç geneli önbellek YOK)."""
from __future__ import annotations

from core.models import Operasyon, Stok, StokVaryant, UretimAyari, Varyant
from core.services import varyant as vs

TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT
_ALAN = {"MARKALAMA": "markalama", "KILAVUZ": "kilavuz", "AFIS": "afis", "YAN_ETIKET": "yan_etiket"}
_SV_ILISKI = ("stok", "markalama", "kilavuz", "afis", "yan_etiket")


def acik() -> bool:
    """Varyantlı planlama anahtarı AÇIK mı (kayıt yoksa KAPALI; okuma yazmaz)."""
    return bool(UretimAyari.objects.filter(pk=1).values_list("varyantli_planlama", flat=True).first())


def anahtar_ayarla(deger: bool, kullanici=None) -> UretimAyari:
    ayar = UretimAyari.get()
    if ayar.varyantli_planlama != bool(deger):
        ayar.varyantli_planlama = bool(deger)
        ayar.updated_by = kullanici
        ayar.save(update_fields=["varyantli_planlama", "updated_by", "updated_at"])
    return ayar


def _hata(mesaj):
    from core.services.uretim import UretimHatasi
    return UretimHatasi(mesaj)


class Dugum:
    """(stok, varyant) planlama düğümü — ``ihtiyac_hesapla``nın "stok" nesnesi: ``pk`` = (stok pk, varyant pk | None), ``kod`` = 'KOD [VARYANT]'.
    Diğer öznitelikler (ad, uretim_birimi…) stok kartına vekâlet eder."""
    __slots__ = ("stok", "varyant", "pk", "kod")

    def __init__(self, stok, varyant=None):
        self.stok, self.varyant = stok, varyant
        self.pk = (stok.pk, varyant.pk if varyant is not None else None)
        self.kod = stok.kod if varyant is None else f"{stok.kod} [{vs.varyant_kisa(varyant)}]"

    def __getattr__(self, ad):
        return getattr(self.stok, ad)

    def __eq__(self, diger):
        return isinstance(diger, Dugum) and diger.pk == self.pk

    def __hash__(self):
        return hash(self.pk)

    def __repr__(self):
        return f"Dugum({self.kod})"


class _Girdi:
    """Koşumun girdi satırı: ``girdi`` (Dugum), ``girdi_id``, ``miktar``; ``satir`` = OperasyonGirdi."""
    __slots__ = ("girdi", "girdi_id", "miktar", "satir", "pk")

    def __init__(self, satir, dugum):
        self.satir, self.girdi, self.girdi_id, self.miktar, self.pk = satir, dugum, dugum.pk, satir.miktar, satir.pk


class _Cikti:
    """Koşumun çıktı satırı (OperasyonCikti görünümü): ``stok`` (Dugum), ``stok_id``, ``miktar``, ``boy_mm``, ``yuzde``, ``surucu``, ``sira``."""
    __slots__ = ("stok", "stok_id", "miktar", "boy_mm", "yuzde", "surucu", "sira", "pk")

    def __init__(self, dugum, miktar, boy_mm=None, yuzde=None, surucu=True, sira=0, pk=None):
        self.stok, self.stok_id, self.miktar, self.boy_mm, self.yuzde, self.surucu, self.sira, self.pk = (
            dugum, dugum.pk, miktar, boy_mm, yuzde, surucu, sira, pk)


class Kosum:
    """Bir tanımın belirli bir çıktı varyantıyla koşumu: ``pk`` = (tanım pk, referans çıktı varyant pk | None); ``op`` tanım; ``cikti_varyant``
    referans çıktının StokVaryant'ı. Diğer öznitelikler (tur, istasyon, tam_calistirma, cikti, cikti_miktar…) tanıma vekâlet eder."""
    __slots__ = ("op", "cikti_varyant", "pk")

    def __init__(self, op, cikti_varyant):
        self.op, self.cikti_varyant = op, cikti_varyant
        self.pk = (op.pk, cikti_varyant.pk if cikti_varyant is not None else None)

    def __getattr__(self, ad):
        return getattr(self.op, ad)

    def __eq__(self, diger):
        return isinstance(diger, Kosum) and diger.pk == self.pk

    def __hash__(self):
        return hash(self.pk)

    def __repr__(self):
        return f"Kosum({self.op.pk}, {self.cikti_varyant and self.cikti_varyant.anahtar})"


# --- varyant aritmetiği -------------------------------------------------------------------------------------------------------------

def _secimler(sv: StokVaryant) -> dict:
    """{boyut: Varyant pk} — StokVaryant satırının boyut değerleri (yalnız dolu olanlar)."""
    sonuc = {}
    for b in vs.stok_boyutlari(sv.stok):
        pk = getattr(sv, f"{_ALAN[b]}_id")
        if pk:
            sonuc[b] = pk
    return sonuc


class Onbellek:
    """Çağrı/çözücü ömürlü küçük önbellek: StokVaryant satırları, kart varsayılanları, YOK ve genel varsayılan değer."""

    def __init__(self):
        self.sv = {}            # (stok pk, anahtar) -> StokVaryant
        self.varsayilan = {}    # stok pk -> {boyut: pk}
        self.yok = Varyant.objects.filter(kod=vs.YOK_KODU, silindi=False).values_list("pk", flat=True).first()
        self.genel = vs.varsayilan_varyant()

    def sv_bul(self, stok: Stok, secimler: dict, kullanici=None) -> StokVaryant:
        anahtar = vs.anahtar_uret(stok, secimler)
        k = (stok.pk, anahtar)
        if k not in self.sv:
            sv = vs.stok_varyanti_bul_veya_olustur(stok, dict(secimler), kullanici)
            self.sv[k] = StokVaryant.objects.select_related(*_SV_ILISKI).get(pk=sv.pk)
        return self.sv[k]

    def varsayilan_secimler(self, stok: Stok) -> dict:
        if stok.pk not in self.varsayilan:
            sv = stok.varsayilan_varyant if stok.varsayilan_varyant_id else None
            if sv is not None:
                self.varsayilan[stok.pk] = _secimler(sv)
            else:
                self.varsayilan[stok.pk] = {b: self.genel.pk for b in vs.stok_boyutlari(stok)} if self.genel else {}
        return self.varsayilan[stok.pk]


def sabit_varyant(stok: Stok, deger, kullanici=None, ob=None) -> StokVaryant | None:
    """Kartın TÜM boyutları ``deger`` (Varyant|pk) olan StokVaryant'ı; varyantsız kartta None. ``ob``: çağıranın önbelleği (yoksa tek seferlik)."""
    if not stok.varyantli:
        return None
    pk = getattr(deger, "pk", deger)
    if not pk:
        raise _hata(f"{stok.kod}: sabit varyant kuralı için değer tanımlı değil.")
    return (ob or Onbellek()).sv_bul(stok, {b: pk for b in vs.stok_boyutlari(stok)}, kullanici)


def tasi_varyant(stok: Stok, kaynak: StokVaryant | None, kullanici=None, ob=None) -> StokVaryant | None:
    """TAŞI: ``stok``un her boyutu için kaynak varyantın aynı boyuttaki değeri; kaynakta olmayan boyut kartın varsayılanı. Varyantsız kartta None."""
    if not stok.varyantli:
        return None
    ob = ob or Onbellek()
    kaynak_secim = _secimler(kaynak) if kaynak is not None else {}
    varsayilan = ob.varsayilan_secimler(stok)
    secimler = {}
    for b in vs.stok_boyutlari(stok):
        secimler[b] = kaynak_secim.get(b) or varsayilan.get(b) or ob.yok
    return ob.sv_bul(stok, secimler, kullanici)


def cikti_varyanti(op: Operasyon, stok: Stok, istenen: StokVaryant | None, kullanici=None, ob=None) -> StokVaryant | None:
    """Tanımın ``stok`` çıktısının varyantı: SABİT → tanımdaki sabit değer; TAŞI → istenen (referans) çıktı varyantından taşınır."""
    if not stok.varyantli:
        return None
    ob = ob or Onbellek()
    if op.cikti_varyant_kurali == SABIT:
        return sabit_varyant(stok, op.cikti_sabit_varyant_id, kullanici, ob)
    return tasi_varyant(stok, istenen, kullanici, ob)


def hepsi_yok_mu(sv: StokVaryant | None, ob=None) -> bool:
    """Varyantın tüm boyutları YOK mu (boş boyut da YOK sayılır)."""
    if sv is None:
        return False
    yok = (ob or Onbellek()).yok
    return all((getattr(sv, f"{_ALAN[b]}_id") or yok) == yok for b in vs.stok_boyutlari(sv.stok))


def girdi_varyanti(satir, cikti_varyant: StokVaryant | None, kullanici=None, ob=None) -> tuple:
    """Girdi satırının (OperasyonGirdi) tüketeceği varyant → (StokVaryant|None, atla). ``atla``: satın alınan (operasyonsuz) varyantlı girdinin
    tüm boyutları YOK — satır planda ve kayıtta yer almaz."""
    from core.services.uretim import ureten_operasyon
    girdi = satir.girdi
    if not girdi.varyantli:
        return None, False
    ob = ob or Onbellek()
    if satir.varyant_kurali == SABIT:
        sv = sabit_varyant(girdi, satir.sabit_varyant_id, kullanici, ob)
    else:
        sv = tasi_varyant(girdi, cikti_varyant, kullanici, ob)
    atla = hepsi_yok_mu(sv, ob) and ureten_operasyon(girdi) is None
    return sv, atla


def kayit_cikti_varyantlari(kayit, ciktilar) -> dict:
    """Operasyon kaydının çıktı varyantları {anahtar: StokVaryant|None} (``kayit_ciktilari`` listesi üzerinden): ``cikti_varyant`` boşsa hepsi None
    (kartın varsayılanı: bugünkü davranış); doluysa ana çıktı o, diğer çıktılar tanım kuralına göre türetilir."""
    if not kayit.cikti_varyant_id:
        return {a: None for a, *_ in ciktilar}
    ana = kayit.cikti_varyant
    ob = Onbellek()
    sonuc = {}
    for anahtar, stok, _m, _b in ciktilar:
        sonuc[anahtar] = ana if anahtar == "ana" else cikti_varyanti(kayit.operasyon, stok, ana, None, ob)
    return sonuc


# --- planlama çözücüsü ----------------------------------------------------------------------------------------------------------------

class Cozucu:
    """``uretim.ihtiyac_hesapla(..., cozucu=)`` için (stok, varyant) düğümleri ve koşumlar. Her düğüm için tanım seçimi ve girdi/çıktı
    düğümleri tek kez çözülür (önbellekli; çözücü ömürlü)."""

    def __init__(self, kullanici=None):
        self.kullanici = kullanici
        self.ob = Onbellek()
        self._dugum = {}
        self._ureten = {}
        self._donustur = {}
        self._girdiler = {}
        self._ciktilar = {}
        self._kosum = {}

    def dugum(self, stok, varyant=None) -> Dugum:
        if isinstance(stok, Dugum):
            return stok
        k = (stok.pk, varyant.pk if varyant is not None else None)
        if k not in self._dugum:
            self._dugum[k] = Dugum(stok, varyant)
        return self._dugum[k]

    def kalem_dugumu(self, stok, varyant=None) -> Dugum:
        """ÜS kalemi: varyantlı kartta verilen varyant, yoksa STANDART (SEMTA); varyantsız kartta None."""
        if stok.varyantli:
            if varyant is None:
                varyant = vs.standart_varyant(stok, self.kullanici)
            elif not isinstance(varyant, StokVaryant):
                varyant = StokVaryant.objects.select_related(*_SV_ILISKI).get(pk=varyant)
            if varyant.stok_id != stok.pk:
                raise _hata(f"{stok.kod}: seçilen varyant bu karta ait değil.")
        else:
            varyant = None
        return self.dugum(stok, varyant)

    def _ureten_op(self, stok):
        from core.services.uretim import ureten_operasyon
        if stok.pk not in self._ureten:
            self._ureten[stok.pk] = ureten_operasyon(stok)
        return self._ureten[stok.pk]

    def _donustur_op(self, stok):
        if stok.pk not in self._donustur:
            self._donustur[stok.pk] = (Operasyon.objects.filter(cikti=stok, tur=Operasyon.Tur.DONUSTUR, silindi=False)
                                       .select_related("istasyon", "cikti", "cikti_sabit_varyant").order_by("pk").first())
        return self._donustur[stok.pk]

    def _tanim_girdileri(self, op):
        if op.pk not in self._girdiler:
            self._girdiler[op.pk] = list(op.girdiler.filter(silindi=False).select_related("girdi__varsayilan_varyant", "sabit_varyant")
                                         .order_by("sira", "pk"))
        return self._girdiler[op.pk]

    def _tanim_ciktilari(self, op):
        from core.services.uretim import tanim_ciktilari
        if op.pk not in self._ciktilar:
            self._ciktilar[op.pk] = tanim_ciktilari(op)
        return self._ciktilar[op.pk]

    def tanim_sec(self, dugum: Dugum):
        """Düğümü üretecek tanım ve referans çıktı varyantı → (Operasyon|None, StokVaryant|None)."""
        stok, sv = dugum.stok, dugum.varyant
        ureten = self._ureten_op(stok)
        if ureten is None:
            return None, None
        if sv is None:
            return ureten, None
        ref = self._tanim_ciktilari(ureten)
        ref_stok = ref[0].stok if ref else ureten.cikti
        if ureten.cikti_varyant_kurali == SABIT:
            if sabit_varyant(stok, ureten.cikti_sabit_varyant_id, self.kullanici, self.ob).pk != sv.pk:
                d = self._donustur_op(stok)
                if d is None:
                    sabit = ureten.cikti_sabit_varyant.kod if ureten.cikti_sabit_varyant_id else "—"
                    raise _hata(f"{stok.kod}: {vs.varyant_kisa(sv)} varyantı üretilemiyor — üreten tanım sabit {sabit} üretiyor ve "
                                f"dönüştür (markalama) tanımı yok.")
                return d, sv
            return ureten, sabit_varyant(ref_stok, ureten.cikti_sabit_varyant_id, self.kullanici, self.ob)
        return ureten, (sv if ref_stok.pk == stok.pk else tasi_varyant(ref_stok, sv, self.kullanici, self.ob))

    def kosum(self, op, ref_varyant) -> Kosum:
        k = (op.pk, ref_varyant.pk if ref_varyant is not None else None)
        if k not in self._kosum:
            self._kosum[k] = Kosum(op, ref_varyant)
        return self._kosum[k]

    def girdi_dugumleri(self, op, cikti_varyant) -> list:
        """Koşumun girdi satırları (atlananlar hariç) → [_Girdi]."""
        sonuc = []
        for g in self._tanim_girdileri(op):
            sv, atla = girdi_varyanti(g, cikti_varyant, self.kullanici, self.ob)
            if atla:
                continue
            sonuc.append(_Girdi(g, self.dugum(g.girdi, sv)))
        return sonuc

    def cikti_dugumleri(self, op, ref_varyant) -> list:
        """Koşumun çıktı satırları → [_Cikti]. DÖNÜŞTÜR: tek çıktı (istenen varyant, sürücü)."""
        if op.tur == Operasyon.Tur.DONUSTUR:
            return [_Cikti(self.dugum(op.cikti, ref_varyant), op.cikti_miktar, op.boy_mm, None, True, 0, None)]
        sonuc = []
        for c in self._tanim_ciktilari(op):
            sv = cikti_varyanti(op, c.stok, ref_varyant, self.kullanici, self.ob) if c.stok.varyantli else None
            sonuc.append(_Cikti(self.dugum(c.stok, sv), c.miktar, c.boy_mm, c.yuzde, c.surucu, c.sira, c.pk))
        return sonuc

    def op_bul(self, dugum):
        """``ihtiyac_hesapla`` çözücü arabirimi: (Kosum|None, girdi satırları, çıktı satırları)."""
        op, ref = self.tanim_sec(dugum)
        if op is None:
            return None, [], []
        k = self.kosum(op, ref)
        return k, self.girdi_dugumleri(op, ref), self.cikti_dugumleri(op, ref)


def dugum_coz(x):
    """Plan nesnesinden (Dugum ya da Stok) → (Stok, StokVaryant|None)."""
    if isinstance(x, Dugum):
        return x.stok, x.varyant
    return x, None


def kosum_coz(x):
    """Plan operasyonundan (Kosum ya da Operasyon) → (Operasyon, StokVaryant|None)."""
    if isinstance(x, Kosum):
        return x.op, x.cikti_varyant
    return x, None


def ihtiyac_anahtari(stok_pk, varyant_pk=None) -> str:
    """İstasyon emri ``ihtiyac`` / ÜS ``sevk_dusen`` JSON anahtarı: "stok" (varyantsız / kapalı) ya da "stok:varyant"."""
    return f"{stok_pk}:{varyant_pk}" if varyant_pk else str(stok_pk)


def anahtar_coz(anahtar) -> tuple:
    """"stok" → (stok pk, None); "stok:varyant" → (stok pk, varyant pk)."""
    s = str(anahtar)
    if ":" in s:
        a, b = s.split(":", 1)
        return int(a), int(b)
    return int(s), None


def etiket(sv) -> str:
    """Ekran/rapor etiketi ('SEMTA', 'MR. MOP/SEMTA/YOK/SEMTA'); None → ''."""
    return vs.varyant_kisa(sv) if sv is not None else ""
