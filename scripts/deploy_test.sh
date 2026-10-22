#!/bin/bash
# scripts/deploy.sh için yalıtılmış sınama: geçici dizinde "yerel repo + sunucu repo + origin" üçlüsü kurar,
# sunucu komutlarını (yedek/migrate/restart/is-active) kayıt tutan taslaklarla değiştirir.
# CANLIYA DOKUNMAZ (ssh yerine `env`, gerçek sunucu adresi hiç kullanılmaz).
# Çalıştır: bash scripts/deploy_test.sh
set -uo pipefail
BETIK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh"
GECTI=0; KALDI=0

kur() {  # temiz sandbox kurar; $T, $LOC, $SRV ayarlanır
    T="$(mktemp -d)"; export T_LOG="$T/olay.log"; : > "$T_LOG"
    git init -q --bare -b main "$T/origin.git"
    LOC="$T/loc"; SRV="$T/srv"
    git init -q -b main "$LOC"
    git -C "$LOC" config user.email t@t; git -C "$LOC" config user.name t
    mkdir -p "$LOC/scripts"
    cat > "$LOC/scripts/db_backup.sh" <<'YEDEK'
#!/bin/bash
if [ -n "${T_YEDEK_HATA:-}" ]; then echo "yedek patladı" >&2; exit 1; fi
echo yedek >> "$T_LOG"; mkdir -p backups; touch backups/erp_v01_20260101_000000.sql.gz
YEDEK
    echo bir > "$LOC/f.txt"
    git -C "$LOC" add -A; git -C "$LOC" commit -q -m A
    git -C "$LOC" remote add origin "$T/origin.git"; git -C "$LOC" push -q origin main
    git clone -q "$T/origin.git" "$SRV"
    git -C "$SRV" config user.email t@t; git -C "$SRV" config user.name t
    mkdir -p "$T/stub"
    printf '#!/bin/bash\nif [ -n "${T_MIGRATE_HATA:-}" ]; then echo "migrate patladı" >&2; exit 1; fi\necho "migrate $*" >> "$T_LOG"\n' > "$T/stub/py"
    printf '#!/bin/bash\nif [ -n "${T_RESTART_HATA:-}" ]; then echo "restart patladı" >&2; exit 1; fi\necho restart >> "$T_LOG"\n' > "$T/stub/restart"
    printf '#!/bin/bash\nif [ -n "${T_PASIF:-}" ]; then echo inactive; exit 3; fi\necho active\n' > "$T/stub/aktif"
    chmod +x "$T/stub/"*
    git -C "$LOC" checkout -q -b feat
    echo iki >> "$LOC/f.txt"; git -C "$LOC" commit -q -am B
}

calistir() {  # deploy.sh'ı sandbox'ta çalıştırır; çıktı $T/cikti
    ( cd "$LOC" && env DEPLOY_SSH=env DEPLOY_GIT_URL="${GIT_URL:-$SRV}" DEPLOY_DIR="$SRV" DEPLOY_PY="$T/stub/py" \
        DEPLOY_RESTART="$T/stub/restart" DEPLOY_AKTIF="$T/stub/aktif" bash "$BETIK" "$@" ) > "$T/cikti" 2>&1
    RC=$?
}

kontrol() {  # kontrol "ad" komut...
    local ad="$1"; shift
    if "$@"; then GECTI=$((GECTI+1)); else KALDI=$((KALDI+1)); echo "  ✗ $ad"; fi
}
olay_yok() { ! grep -q "$1" "$T_LOG"; }
olay_var() { grep -q "$1" "$T_LOG"; }
cikti_var() { grep -q "$1" "$T/cikti"; }

basla() { echo "== $1"; kur; unset GIT_URL T_YEDEK_HATA T_MIGRATE_HATA T_RESTART_HATA T_PASIF; }

# 1) mutlu yol
basla "1 mutlu yol: sıra yedek→migrate→restart, sunucu HEAD == yerel HEAD, origin güncel"
calistir feat
kontrol "çıkış 0" test $RC -eq 0
kontrol "sıra yedek,migrate,restart" test "$(tr '\n' ' ' < "$T_LOG")" = "yedek migrate manage.py migrate core restart "
kontrol "sunucu HEAD == dal" test "$(git -C "$SRV" rev-parse HEAD)" = "$(git -C "$LOC" rev-parse feat)"
kontrol "origin main == dal" test "$(git -C "$T/origin.git" rev-parse main)" = "$(git -C "$LOC" rev-parse feat)"
kontrol "geçici dal silindi" test -z "$(git -C "$SRV" branch --list 'wsl-deploy-*')"
kontrol "TAMAM yazdı" cikti_var "TAMAM"

# 2) push başarısız (bugünkü olay)
basla "2 push düşerse: migrate/restart/yedek ÇALIŞMAZ, hata net"
GIT_URL="$T/yok/repo.git" calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "hiçbir sunucu adımı çalışmadı" test ! -s "$T_LOG"
kontrol "adım adı yazıldı" cikti_var "başarısız adım: sunucuya push"
kontrol "sunucu HEAD değişmedi" test "$(git -C "$SRV" rev-parse HEAD)" = "$(git -C "$LOC" rev-parse main)"

# 3) yedek patlarsa
basla "3 yedek düşerse: merge/migrate/restart yok"
T_YEDEK_HATA=1 calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "merge olmadı" test "$(git -C "$SRV" rev-parse HEAD)" = "$(git -C "$LOC" rev-parse main)"
kontrol "migrate yok" olay_yok migrate
kontrol "restart yok" olay_yok restart
kontrol "sunucu adımı yazıldı" cikti_var "SUNUCU ADIMI BAŞARISIZ: \[2/9\]"

# 4) ff-merge reddi (sunucu kirli dosya)
basla "4 ff-merge düşerse (sunucuda yerel değişiklik): migrate/restart yok"
echo kirli >> "$SRV/f.txt"
calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "yedek alındı (merge'den önce)" olay_var yedek
kontrol "migrate yok" olay_yok migrate
kontrol "restart yok" olay_yok restart
kontrol "origin main değişmedi" test "$(git -C "$T/origin.git" rev-parse main)" = "$(git -C "$LOC" rev-parse main)"
kontrol "merge adımı yazıldı" cikti_var "SUNUCU ADIMI BAŞARISIZ: \[3/9\]"

# 5) HEAD eşitsizliği (merge sonrası hook ekstra commit atar)
basla "5 HEAD != yerel HEAD: migrate'ten ÖNCE durur"
printf '#!/bin/bash\ngit commit -q --allow-empty -m sapma\n' > "$SRV/.git/hooks/post-merge"; chmod +x "$SRV/.git/hooks/post-merge"
calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "migrate yok" olay_yok migrate
kontrol "restart yok" olay_yok restart
kontrol "origin main değişmedi" test "$(git -C "$T/origin.git" rev-parse main)" = "$(git -C "$LOC" rev-parse main)"
kontrol "HEAD adımı yazıldı" cikti_var "SUNUCU ADIMI BAŞARISIZ: \[4/9\]"

# 6) migrate düşerse
basla "6 migrate düşerse: restart yok"
T_MIGRATE_HATA=1 calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "restart yok" olay_yok restart
kontrol "migrate adımı yazıldı" cikti_var "SUNUCU ADIMI BAŞARISIZ: \[7/9\]"

# 7) restart düşerse
basla "7 restart düşerse: çıkış != 0, TAMAM yazmaz"
T_RESTART_HATA=1 calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "TAMAM yazmadı" bash -c '! grep -q "^TAMAM" "$0"' "$T/cikti"

# 8) servis aktif değil
basla "8 servis active değilse: çıkış != 0"
T_PASIF=1 calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "TAMAM yazmadı" bash -c '! grep -q "^TAMAM" "$0"' "$T/cikti"

# 9) dal sunucu main'in devamı değil (preflight; push bile edilmez)
basla "9 rebase gerekli: push edilmeden durur"
git -C "$SRV" commit -q --allow-empty -m sunucuda-ayri
calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "push edilmedi" test -z "$(git -C "$SRV" branch --list 'wsl-deploy-*')"
kontrol "hiçbir sunucu adımı yok" test ! -s "$T_LOG"
kontrol "rebase mesajı" cikti_var "önce .git fetch origin. + dalı origin/main üstüne rebase"

# 9b) sunucu main yerelde biliniyor ama dal onun devamı değil
basla "9b sunucu main dalın atası değil: push edilmeden durur"
git -C "$LOC" checkout -q main; echo x >> "$LOC/g.txt"; git -C "$LOC" add g.txt; git -C "$LOC" commit -q -m X
git -C "$SRV" fetch -q "$LOC" main; git -C "$SRV" merge -q --ff-only FETCH_HEAD
calistir feat
kontrol "çıkış != 0" test $RC -ne 0
kontrol "devamı değil mesajı" cikti_var "devamı değil"
kontrol "push edilmedi" test -z "$(git -C "$SRV" branch --list 'wsl-deploy-*')"
kontrol "hiçbir sunucu adımı yok" test ! -s "$T_LOG"

# 10) argüman hataları + zaten canlıda
basla "10 argümansız / olmayan dal / zaten canlıda"
calistir;               kontrol "argümansız: çıkış != 0" test $RC -ne 0
calistir yokdal;        kontrol "olmayan dal: çıkış != 0" test $RC -ne 0
calistir main;          kontrol "zaten canlıda: çıkış != 0" test $RC -ne 0
kontrol "hiçbir sunucu adımı yok" test ! -s "$T_LOG"

echo ""
echo "SONUÇ: ${GECTI} kontrol geçti, ${KALDI} kaldı"
[ "$KALDI" -eq 0 ]
