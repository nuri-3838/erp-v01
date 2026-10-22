#!/bin/bash
# erp_v01 — standart canlıya alma (WSL'den çalıştırılır):  scripts/deploy.sh <yerel-dal>
# Sıra: yerel kontroller → sunucuya geçici dal push → [sunucuda] dal doğrula → yedek → ff-merge →
#       HEAD eşitliği (migrate'ten ÖNCE) → GitHub push → migrate → restart → is-active → son HEAD.
# Herhangi bir adım hata verirse DURUR, sonraki adımlar çalışmaz; hangi adımda düştüğünü yazar.
# (Eski betik `set -e` + `a && b && c` zinciri kullanıyordu: zincirin ortasındaki hata set -e'yi tetiklemez,
#  push düşse bile migrate/restart çalışırdı. Burada hiçbir adım && zincirine bağlı değil.)
#
# Ortam değişkenleri YALNIZ testler içindir (scripts/deploy_test.sh); canlıda tanımsız bırak:
#   DEPLOY_SSH, DEPLOY_GIT_URL, DEPLOY_DIR, DEPLOY_PY, DEPLOY_RESTART, DEPLOY_AKTIF
set -Eeuo pipefail

SSH="${DEPLOY_SSH:-ssh nuri@159.69.218.128}"
GIT_URL="${DEPLOY_GIT_URL:-nuri@159.69.218.128:erp_v01}"
DIZIN="${DEPLOY_DIR:-/home/nuri/erp_v01}"
PY="${DEPLOY_PY:-.venv/bin/python}"
RESTART="${DEPLOY_RESTART:-sudo systemctl restart erp_v01}"
AKTIF="${DEPLOY_AKTIF:-systemctl is-active erp_v01}"

adim="başlangıç"
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "" >&2; echo "!!! DEPLOY DURDU — başarısız adım: ${adim} (çıkış kodu ${rc}). Sonraki adımlar ÇALIŞTIRILMADI." >&2; fi' EXIT

dur() { echo "HATA: $*" >&2; exit 1; }

[ $# -eq 1 ] || dur "kullanım: scripts/deploy.sh <yerel-dal>   (örn. main veya feature/xyz)"
DAL_YEREL="$1"

adim="yerel dal kontrolü"
SHA="$(git rev-parse --verify -q "refs/heads/${DAL_YEREL}")" || dur "yerel dal yok: ${DAL_YEREL}"
if git cat-file -e cdfc735 2>/dev/null; then
    if git merge-base --is-ancestor cdfc735 "$SHA"; then
        dur "dal 'cdfc735' (lokalde kalacak commit) içeriyor — canlıya GİTMEZ. Onu içermeyen bir dal kullan."
    fi
fi
echo "Dal: ${DAL_YEREL}  @ ${SHA:0:12}  $(git log -1 --format=%s "$SHA")"

adim="sunucu bağlantısı ve main durumu (ssh)"
SUNUCU_MAIN="$($SSH bash -s <<<"set -e; cd '${DIZIN}'; git rev-parse refs/heads/main")" || dur "sunucuya bağlanılamadı veya main okunamadı"
[ -n "$SUNUCU_MAIN" ] || dur "sunucu main sha'sı boş"

adim="ff-merge ön kontrolü (sunucu main ⊂ dal)"
git cat-file -e "${SUNUCU_MAIN}^{commit}" 2>/dev/null || dur "sunucu main (${SUNUCU_MAIN:0:12}) yerelde yok — önce 'git fetch origin' + dalı origin/main üstüne rebase et"
git merge-base --is-ancestor "$SUNUCU_MAIN" "$SHA" || dur "dal sunucu main'in (${SUNUCU_MAIN:0:12}) devamı değil — rebase gerekli, ff-merge reddedilirdi"
if [ "$SUNUCU_MAIN" = "$SHA" ]; then dur "dal zaten canlıda (sunucu main == dal); yapılacak bir şey yok"; fi

DAL_SUNUCU="wsl-deploy-$(date +%Y%m%d%H%M%S)"
adim="sunucuya push (${DAL_SUNUCU})"
git push "$GIT_URL" "refs/heads/${DAL_YEREL}:refs/heads/${DAL_SUNUCU}"

adim="sunucu betiği"
q() { printf '%q' "$1"; }
$SSH bash -s -- "$(q "$DAL_SUNUCU")" "$(q "$SHA")" "$(q "$DIZIN")" "$(q "$PY")" "$(q "$RESTART")" "$(q "$AKTIF")" <<'SUNUCU_BETIGI'
set -Eeuo pipefail
DAL="$1"; SHA="$2"; DIZIN="$3"; PY="$4"; RESTART="$5"; AKTIF="$6"
adim="başlangıç"
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "!!! SUNUCU ADIMI BAŞARISIZ: ${adim} (kod ${rc}) — sonrası çalıştırılmadı." >&2; fi' EXIT
dur() { echo "HATA: $*" >&2; exit 1; }

cd "$DIZIN"

adim="[1/9] geçici dal doğrulama (sunucudaki dal sha == yerel sha)"
[ "$(git rev-parse --verify -q "refs/heads/${DAL}")" = "$SHA" ] || dur "sunucudaki ${DAL} yerel sha (${SHA:0:12}) ile aynı değil"

adim="[2/9] yedek (scripts/db_backup.sh)"
bash scripts/db_backup.sh
ls -t backups/erp_v01_2*.sql.gz | sed -n 1p

adim="[3/9] git merge --ff-only"
git merge --ff-only "$DAL"

adim="[4/9] HEAD eşitliği (sunucu HEAD == yerel HEAD) — migrate'ten ÖNCE"
HEAD_SUNUCU="$(git rev-parse HEAD)"
[ "$HEAD_SUNUCU" = "$SHA" ] || dur "sunucu HEAD (${HEAD_SUNUCU:0:12}) != yerel HEAD (${SHA:0:12}) — migrate/restart ÇALIŞTIRILMADI"

adim="[5/9] GitHub push (origin main)"
git push origin main

adim="[6/9] geçici dal silme"
git branch -d "$DAL"

adim="[7/9] migrate core"
"$PY" manage.py migrate core

adim="[8/9] servis restart"
$RESTART

adim="[9/9] servis aktif mi"
DURUM="$($AKTIF)"
[ "$DURUM" = "active" ] || dur "servis durumu '${DURUM}' (active bekleniyordu)"
echo "$DURUM"

adim="son HEAD kontrolü"
[ "$(git rev-parse HEAD)" = "$SHA" ] || dur "deploy sonunda HEAD değişmiş"
echo "SUNUCU HEAD: $(git rev-parse --short HEAD)"
SUNUCU_BETIGI

adim="yerel son rapor"
echo ""
echo "TAMAM: ${DAL_YEREL} (${SHA:0:12}) canlıda; sunucu HEAD == yerel HEAD."
echo "Sıradaki: bağımsız doğrulama (CLAUDE.md 'Canlıya alma' 3-4. adım: check, migrate --plan, gunicorn log)."
