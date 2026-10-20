# Google Drive yedeği (şifreli, dış kopya)

Gece 03:00'te (TR) alınan yerel yedeğin (`db_backup.sh`: veritabanı + özel dosyalar + medya) **şifrelenmiş** bir kopyasını
`nuri@semtahome.com` Google Drive hesabındaki **`ERP_YEDEK`** klasörüne yükler. Amaç: sunucu/disk/Hetzner hesabı kaybında geri dönebilmek.

## Parçalar
| Parça | Yer |
|---|---|
| Kod | `core/services/drive_yedek.py`, komut `python manage.py drive_yedek` |
| Aktarım | `rclone` (`~/bin/rclone`), uzak adı `erpdrive`, kapsam **`drive.file`** (uygulama yalnız kendi oluşturduğu dosya/klasörleri görür; Drive'ın geri kalanına erişemez) |
| Şifreleme | GnuPG simetrik AES256; parola dosyası `~/.erp_yedek_anahtar` (yalnız uygulama kullanıcısı okur, `chmod 600`; repoda DEĞİL) |
| Durum / log | `logs/drive_yedek_durum.json`, `logs/drive_yedek.log` |
| Ekran | Ayarlar > Yedek sayfasındaki "Google Drive yedeği" kartı (salt-okunur) |

## Çalışma kuralları
* Sunucu yedeği 00:00 UTC'de (03:00 TR) alınır; Drive yüklemesi **00:30 ve 12:30 UTC** çalışır (ikincisi hata olduysa yeniden deneme).
* Her takvim günü için **en erken** yedek takımı (DB + özel + medya) yüklenir; deploy öncesi ara yedekler yüklenmez.
* Önce yerelde şifrelenir (`.gpg`), sonra yüklenir; yüklenen dosyanın boyutu uzaktan doğrulanır, uyuşmazsa uzak kopya silinip hata yazılır.
* Hata olursa `logs/drive_yedek.log`'a yazılır, çıkış kodu 1 olur; yüklenemeyen takım sonraki çalışmada tekrar denenir. Ekran kartı uyarı gösterir.
* **Saklama:** son 30 gün günlük + son 12 hafta haftalık (her ISO haftasının son yedeği — normalde pazar günü). En yeni takım hiçbir zaman silinmez.
* Silme **yalnız** `ERP_YEDEK` içinde, `erp_v01_…(sql.gz|tar.gz).gpg` desenine uyan **ve** durum dosyasında "yüklendi" diye kayıtlı dosyalar için yapılır. Elle konan başka dosyaya dokunulmaz.
* Silme `--drive-use-trash=false` ile kalıcıdır (çöp kutusunda kota harcamaz); `drive.file` kapsamında zaten yalnız uygulamanın kendi dosyalarına erişilir.

## Kurulum (bir kez) — kullanıcı adımları ✋
1. Sunucuya rclone (resmî sürüm, `~/bin/rclone`, ~20 MB) kurulur.
2. Sunucuda şifre parolası üretilir: `~/.erp_yedek_anahtar` (`chmod 600`). **Parola sohbette/mesajda gösterilmez.**
3. **Kullanıcı kopyası:** parola dosyasının bir kopyası kullanıcının bilgisayarına bir dosya olarak indirilir (`scp`) ve sunucudan ayrı güvenli yerde (parola yöneticisi / USB) saklanır. Parola kaybolursa yedekler **açılamaz**.
4. Google izni: sunucuda `rclone authorize "drive" "scope=drive.file"` çalışır; SSH tünelinden (`ssh -L 53682:localhost:53682 nuri@159.69.218.128`) verilen adres **kullanıcının kendi tarayıcısında** açılır, `nuri@semtahome.com` ile "İzin ver" tıklanır. (Parola kimseye yazılmaz; yalnız Google'ın kendi izin ekranı.) Çıkan jeton sunucuda `~/.config/rclone/rclone.conf` dosyasına (`chmod 600`) yazılır.
5. `ERP_YEDEK` klasörü rclone ile oluşturulur; `drive_yedek --kuru` ile deneme, sonra ilk yükleme.
6. Cron (uygulama kullanıcısı):
   ```
   30 0,12 * * * cd /home/nuri/erp_v01 && flock -n /tmp/drive_yedek.lock /home/nuri/erp_v01/.venv/bin/python manage.py drive_yedek >> /home/nuri/erp_v01/logs/drive_yedek_cron.log 2>&1
   ```

## Geri yükleme (felaket durumu)
1. Drive'dan ilgili tarihin 3 dosyasını indir: `erp_v01_YYYYMMDD_HHMMSS.sql.gz.gpg` (+ `ozel_`, `medya_` önekli `.tar.gz.gpg`).
2. Çöz: `gpg --batch --pinentry-mode loopback --passphrase-file <parola_dosyasi> -o erp.sql.gz -d erp_v01_….sql.gz.gpg`
3. DB'yi boş veritabanına yükle: `gunzip -c erp.sql.gz | psql -d <yeni_db>`; özel dosya/medya `tar -xzf` ile ilgili klasöre.
4. Doğrula: mizan toplamları canlıyla aynı olmalı.

## Komutlar
```
python manage.py drive_yedek --kuru           # ne yüklenir/silinir, yazmaz
python manage.py drive_yedek                  # yükle + sakla
python manage.py drive_yedek --sadece-sakla   # yalnız saklama
python manage.py drive_yedek --bugun 2026-12-01 --kuru   # saklama planını başka bir "bugün" için dene
```
Ortam değişkenleri (testte/prova için): `DRIVE_YEDEK_DIZIN`, `DRIVE_YEDEK_DURUM`, `DRIVE_YEDEK_LOG`, `DRIVE_YEDEK_ANAHTAR`, `DRIVE_YEDEK_UZAK`, `RCLONE_YOLU`, `GPG_YOLU`.
