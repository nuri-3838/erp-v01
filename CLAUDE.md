# CLAUDE.md — ERP v0.1 (Alüminyum Merdiven İmalatı)

Bu dosya her oturumda otomatik okunur. Aşağıdaki kurallar **bağlayıcıdır.**

## Bu proje nedir
Muhasebe çekirdeği (yevmiye motoru + TDHP/7-A hesap planı + manuel fiş + canlı mizan/bilanço/gelir tablosu + USD raporlama) üstüne, `docs/ERP_v0.1_kapsam.md`'nin kendi yol haritasında (§6) öngördüğü sırayla inşa edilen bir ERP. Yığın: **Django + PostgreSQL** (UTF-8 + Türkçe collation).

**Şu an neredeyiz (2026-09-15):** v0.1 (muhasebe çekirdeği), v0.2 (Stok) ve v0.3 (Cari + satış/satınalma + KDV) tamamlandı ve kapsam bunların ötesine de genişledi — Faturalar, Finans (Kasa/Banka/Kredi/Kredi Kartı/Çek-Senet), Teklif&Sipariş&İrsaliye, bağımsız Satış Teklifi, Yemek Takibi, FASON (kesim tanımları + kayıtları) canlıda çalışıyor. Ayrıntı ve kalıcı desenler için memory'deki "İnşa durumu" kaydına bak. **v0.4 (üretim kaydı) İş İstasyonu + Operasyon (rota) modeliyle teslim edildi** (bkz. kapsam dosyası §6) — düz "Ürün Ağacı" tasarımı 2026-09-16'da kullanıcı tarafından reddedilip kaldırıldı (`e3e9053`), geri getirme; ÜRETİM > Ürün Ağacı yalnız `ihtiyac_hesapla()` üzerinden çizilen salt-okunur bir görünümdür (yeni tablo yok). Maliyet yansıtması yine ay sonu manuel kalır, yazılım yalnız operasyonel veriyi (hangi bileşen ne kadar tüketildi, hangi mamul ne kadar üretildi) toplar.

## Tek doğruluk kaynağı
- `docs/ERP_v0.1_kapsam.md` — mimari, veri modeli, temel kurallar, "bitti" tanımı, yol haritası.
- `docs/hesap_plani_seed.csv` — hesap planı seed'i.
Çelişki olursa **spec geçerlidir.** Bu dosya sadece özet + disiplindir.

## ALTIN KURAL — kapsam disiplini (en kritik)
- Her zaman **o an üstünde çalışılan konunun dışına** çıkma — "şunu da ekleyelim / es geçmeyelim" deme, her ek **koda değil, yol haritasına not** olur.
- Aynı anda birden çok iş yapma. **En ince uçtan uca dilim** önce (local test → deploy → doğrula, sonra sıradaki dilim).
- Yeni bir modül/büyük özellik, kullanıcının o oturumda açıkça istediği veya `docs/ERP_v0.1_kapsam.md` §6 yol haritasında zaten planlı olan şeyin ötesine geçmemeli.
- Kararsız kalınca kapsamı **genişletme → DUR ve sor.**

## Çiğnenmeyecek invariant'lar (model/servis katmanında zorlanır, UI'a güvenilmez)
- **Sayı/para:** tek parser + tek formatter, DECIMAL (asla float), ROUND_HALF_UP. TR: nokta=binlik, virgül=ondalık. Testler zorunlu.
- **TR büyük harf:** tek fonksiyon, i→İ ı→I. İstisna: şifre, e-posta, URL, sistem kimlikleri. Testler zorunlu.
- **Dengeli fiş:** SUM(borc)=SUM(alacak) değilse fiş kaydedilmez.
- **Bakiyeler HESAPLANIR, saklanmaz:** mizan/bilanço/gelir tablosu her zaman yevmiye satırlarından; "mevcut bakiye" alanı tutulmaz.
- **Para birimi:** TL fonksiyonel; USD raporlama; EUR/GBP işlem para birimi. Her fişe TCMB kuru.
- **Tarih:** muhasebe tarihi (kullanıcı) ≠ kayıt zamanı (sistem). UTC sakla, TR göster.
- **Numara:** insana görünen fiş no boşluksuz/müteselsil; iç PK ayrı.
- **Audit + soft delete:** her tabloda created/updated by/at; fiziksel silme yok.
- **Görsel:** yüklemede en uzun kenar ~1600px, ~%80 JPEG/WebP.

## İnşa sırası (v0.1 — tamamlandı, tarihsel kayıt)
1. İskelet + PostgreSQL (UTF-8/TR) + temel kurallar modülü + testleri.
2. HESAP_PLANI + CSV seed.
3. YEVMIYE_FISI + YEVMIYE_SATIR + KUR + dengeli fiş kuralı.
4. Manuel fiş giriş ekranı (kur alanı dahil).
5. Mizan → bilanço + gelir tablosu (6'lı türeme haritası) → USD görünümü.

Sıradaki adımlar için `docs/ERP_v0.1_kapsam.md` §6 (Yol haritası) geçerli.

## ŞİMDİ YAPMA (kesin kapsam dışı — kapsam dosyasının kendi kararı)
e-Fatura / e-Arşiv / e-İrsaliye / e-Defter / beyanname üretimi. Bu ERP resmi/sertifikalı kanala hiç girmez, yalnız iç yönetim + muhasebe sistemidir (bkz. kapsam dosyası §0). Otomatik ay sonu yansıtma (7xx→6xx) da bilinçli olarak hep manuel kalacak.

## Dokunma
Eski `semta_erp` projesiyle ilgisi yok. Buraya hiçbir şey kopyalama; o sadece ayrı bir referans.

## Kapsam genişletme notu (bilinçli, 2026-05-30)
Giriş + kullanıcı bazlı ekran yetkisi v0.1'e **bilinçli** eklendi (normalde v0.7). Gerekçe: sistem ekipçe kullanılacak. Mimari: modül (MUHASEBE) → ekran (7 rapor/giriş ekranı) → **kullanıcı bazlı** erişim (sabit rol yok). Şimdilik yalnızca erişim düzeyi (görür/göremez); "görür ama değiştiremez" ileride. Yeni modüller (Stok/Cari/Üretim) aynı mantıkla eklenecek.

## Test disiplini
- **Geliştirme sırasında yalnız ilgili test modüllerini çalıştır** (ör. `python manage.py test core.tests -p "test_uretim*.py"` ya da `python manage.py test core.tests.test_uretim core.tests.test_uretim_tam_boy`). Her küçük değişiklikte tam paketi çalıştırma.
- **Tam paket her işin SONUNDA bir kez** (toplam test, hata/başarısız sayısı ve exit code raporlanır): `python manage.py test --parallel --keepdb` (12 çekirdekte ~1,5-2,5 dk; eski sıralı koşu ~7 dk). Geliştirme ortamı bağımlılığı: `pip install -r requirements-dev.txt` (tblib; canlıya KURULMAZ).
  - *Paralel/keepdb notu (2026-10-09, sorun giderildi):* Testler sabit pk varsaymamalı — komutların canlıdaki sabit kayıtlarına (fiş/banka hesap/proje pk'leri) bağlı testler kendi doğal pk'lerini komut sabitlerine `mock.patch.object` ile verir. `TransactionTestCase` sonrası tohum verisi sınıf sonunda geri yüklenir (`tearDownClass`). Doğrulama: `--parallel --keepdb` art arda 3 kez + `--parallel` 1 kez hepsi OK.
  - Test koşusunu YARIDA KESME (timeout/Ctrl-C): `--keepdb` ile kalan test veritabanı bozuk/kirli kalır ve sonraki koşular sahte hata verir. Kesildiyse önce `python manage.py test --parallel --noinput` (eski test DB'sini siler, sıfırdan kurar) çalıştır.
- **Ekran görüntüsü yalnız arayüz değişikliği olan işlerde** al; servis/veri/test işlerinde alma.

## Canlıya alma — standart deploy (her seferinde bu sırayla, komutlar birebir)
Sunucu: Hetzner `nuri@159.69.218.128` (SSH, WSL'deki `~/.ssh/id_ed25519`), klasör `/home/nuri/erp_v01`, venv `.venv` (aktivasyon YOK, doğrudan `.venv/bin/python`), servis `erp_v01` (gunicorn 127.0.0.1:8001), adres https://erp.semtahome.com. WSL'den GitHub'a doğrudan push ÇALIŞMAZ (HTTPS, kimlik yok) — GitHub'a yazma sunucunun deploy key'iyle, sunucudan yapılır. Local kontroller (ilgili testler + tam paket) temizse onay beklemeden canlıya al.

```bash
# 0) Local: tam test paketi OK, commit atılmış, çalışma ağacı temiz.

# 1) WSL → sunucuya geçici dal (worktree/feature dalı: önce origin/main üstüne rebase, yoksa ff-only reddeder)
cd ~/erp-v01 && git fetch origin && git push nuri@159.69.218.128:erp_v01 main:refs/heads/wsl-X
#    feature dalı için: git push nuri@159.69.218.128:erp_v01 feature/<dal>:refs/heads/wsl-<ad>

# 2) Sunucuda TEK ssh betiği: yedek → pull(ff-merge)+GitHub push → migrate → restart
ssh nuri@159.69.218.128 "bash -s" <<'EOF'
set -e
cd ~/erp_v01
bash scripts/db_backup.sh
git merge --ff-only wsl-X && git push origin main && git branch -d wsl-X
.venv/bin/python manage.py migrate core
sudo systemctl restart erp_v01
systemctl is-active erp_v01
EOF
#    yedek: DB + ozel_dosyalar + media → backups/ (çıktı yok, logs/backup.log'a yazar) — deploy öncesi geri dönüş noktası
#    restart: sudoers NOPASSWD (/etc/sudoers.d/90-erp-restart). Sudo bozulursa şifresiz eşdeğer:
#    ssh nuri@159.69.218.128 'kill -HUP $(systemctl show erp_v01 -p MainPID --value)'

# 3) HEAD eşitliği — local HEAD == sunucu HEAD olmalı
cd ~/erp-v01 && git rev-parse HEAD && ssh nuri@159.69.218.128 'cd ~/erp_v01 && git rev-parse HEAD && git status --short'

# 4) Bağımsız doğrulama — AYRI ssh (deploy çıktısına güvenme; stdout/stderr iç içe karışır)
ssh nuri@159.69.218.128 'cd ~/erp_v01 && systemctl show erp_v01 -p ActiveEnterTimestamp && .venv/bin/python manage.py check && .venv/bin/python manage.py migrate --plan | tail -3 && journalctl -u erp_v01 -n 30 --no-pager && tail -n 30 logs/gunicorn-error.log'
#    ActiveEnterTimestamp gerçekten yeni olmalı; migrate --plan "No planned migration operations" demeli; logda Traceback olmamalı.
#    Dışarıdan: curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://erp.semtahome.com/<yeni-rota>/   → 302→login (rota var); olmayan yol 404.

# 5) WSL'i senkronla
cd ~/erp-v01 && git fetch origin
```
Kurallar: `;` değil `&&` zinciri (bir adım düşerse sonrakiler ÇALIŞMASIN); `2>/dev/null` ile stderr ASLA bastırma; `source venv/bin/activate` YAZMA (sunucuda klasör `.venv`, aktivasyon gereksiz — bir kez migration uygulanmadan "active" görünmüştü); migration ya SADECE şema ya SADECE veri içersin (Postgres "pending trigger events"). Git Bash'te `$(...)` genişler → `'EOF'` tırnaklı heredoc kullan veya betik dosyasından çalıştır.
