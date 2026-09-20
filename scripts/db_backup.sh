#!/bin/bash
# erp_v01 — Günlük PostgreSQL yedeği: pg_dump | gzip + bütünlük testi + retention + log.
# Eski sistemden (semta_erp) TAMAMEN bağımsızdır; ona/yedeğine/cronuna dokunmaz.
# Şifre dahil DB ayarları repodaki .env'den okunur (Django ile aynı kaynak; repoda sır yok).
set -euo pipefail

# ERP_REPO yalnız testler içindir (geçici dizinde çalıştırmak için); canlıda tanımsızdır.
REPO="${ERP_REPO:-/home/nuri/erp_v01}"
BACKUP_DIR="${REPO}/backups"
LOG="${REPO}/logs/backup.log"
RETENTION_DAYS=15
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SQL_FILE="erp_v01_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# .env'den yalnız DB_* değişkenlerini güvenle oku (eval/source yok)
getenv() { grep -E "^$1=" "${REPO}/.env" | head -1 | cut -d= -f2-; }
DB_NAME="$(getenv DB_NAME)"
DB_USER="$(getenv DB_USER)"
DB_PASSWORD="$(getenv DB_PASSWORD)"
DB_HOST="$(getenv DB_HOST)"
DB_PORT="$(getenv DB_PORT)"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"

if [ -z "$DB_NAME" ] || [ -z "$DB_USER" ] || [ -z "$DB_PASSWORD" ]; then
    log "HATA: .env'den DB ayarları okunamadı (DB_NAME/USER/PASSWORD boş)."
    exit 1
fi

# 1) pg_dump | gzip  (pipefail: pg_dump çökerse pipeline çöker)
if ! PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
        -d "$DB_NAME" --no-owner 2>>"$LOG" | gzip > "${BACKUP_DIR}/${SQL_FILE}"; then
    log "HATA: pg_dump başarısız — ${SQL_FILE} alınamadı, kısmi dosya siliniyor."
    rm -f "${BACKUP_DIR}/${SQL_FILE}"
    exit 1
fi

# 2) gzip bütünlük testi — bozuk yedek yedek değildir
if ! gunzip -t "${BACKUP_DIR}/${SQL_FILE}" 2>/dev/null; then
    log "HATA: ${SQL_FILE} bütünlük testi BAŞARISIZ — dosya siliniyor."
    rm -f "${BACKUP_DIR}/${SQL_FILE}"
    exit 1
fi

# 3) Retention — 15 günden eski erp_v01 yedeklerini sil (semta_erp dosyalarına dokunmaz)
find "$BACKUP_DIR" -maxdepth 1 -name 'erp_v01_*.sql.gz' -mtime +${RETENTION_DAYS} -delete

SIZE="$(du -h "${BACKUP_DIR}/${SQL_FILE}" | cut -f1)"
COUNT="$(find "$BACKUP_DIR" -maxdepth 1 -name 'erp_v01_*.sql.gz' | wc -l)"
log "OK: ${SQL_FILE} (${SIZE}) — saklanan toplam yedek: ${COUNT}."

# 4) Özel dosyalar (MEDIA_ROOT dışı özel dizin: İK özlük evrakları/fotoğrafları, cari/aday ekleri,
#    çek/senet görselleri) arşivi — dizin yoksa/boşsa atlanır.
#    Aynı TIMESTAMP ile erp_v01_ozel_*.tar.gz; kısmi dosya .part adıyla yazılır, doğrulanınca
#    yeniden adlandırılır (Yedek ekranı yarım arşivi listelemesin). Ayrı retention (yukarıdaki
#    sql.gz retention'ı tar'lara dokunmaz). Geri yükleme (elle):
#    tar -xzf erp_v01_ozel_<zaman>.tar.gz -C /home/nuri/erp_v01
OZEL_DIR="${REPO}/ozel_dosyalar"
if [ -d "$OZEL_DIR" ] && [ -n "$(ls -A "$OZEL_DIR" 2>/dev/null)" ]; then
    OZEL_FILE="erp_v01_ozel_${TIMESTAMP}.tar.gz"
    OZEL_PART="${BACKUP_DIR}/${OZEL_FILE}.part"
    if (umask 077; tar -czf "$OZEL_PART" -C "$REPO" ozel_dosyalar 2>>"$LOG") \
       && tar -tzf "$OZEL_PART" >/dev/null 2>&1 \
       && mv "$OZEL_PART" "${BACKUP_DIR}/${OZEL_FILE}"; then
        find "$BACKUP_DIR" -maxdepth 1 -name 'erp_v01_ozel_*.tar.gz' -mtime +${RETENTION_DAYS} -delete
        log "OK: ${OZEL_FILE} ($(du -h "${BACKUP_DIR}/${OZEL_FILE}" | cut -f1))."
    else
        log "HATA: ${OZEL_FILE} alınamadı/bozuk — kısmi dosya siliniyor."
        rm -f "$OZEL_PART" "${BACKUP_DIR}/${OZEL_FILE}"
        exit 1
    fi
fi

# 5) Yüklenen görseller (MEDIA_ROOT = media/: stok görselleri, banka/firma logoları) arşivi —
#    dizin yoksa/boşsa atlanır. Adım 4 ile aynı desen: erp_v01_medya_*.tar.gz, .part → tar -tzf
#    doğrulaması → yeniden adlandırma, ayrı retention, hata → exit 1. Geri yükleme (elle):
#    tar -xzf erp_v01_medya_<zaman>.tar.gz -C /home/nuri/erp_v01
MEDYA_DIR="${REPO}/media"
if [ -d "$MEDYA_DIR" ] && [ -n "$(ls -A "$MEDYA_DIR" 2>/dev/null)" ]; then
    MEDYA_FILE="erp_v01_medya_${TIMESTAMP}.tar.gz"
    MEDYA_PART="${BACKUP_DIR}/${MEDYA_FILE}.part"
    if (umask 077; tar -czf "$MEDYA_PART" -C "$REPO" media 2>>"$LOG") \
       && tar -tzf "$MEDYA_PART" >/dev/null 2>&1 \
       && mv "$MEDYA_PART" "${BACKUP_DIR}/${MEDYA_FILE}"; then
        find "$BACKUP_DIR" -maxdepth 1 -name 'erp_v01_medya_*.tar.gz' -mtime +${RETENTION_DAYS} -delete
        log "OK: ${MEDYA_FILE} ($(du -h "${BACKUP_DIR}/${MEDYA_FILE}" | cut -f1))."
    else
        log "HATA: ${MEDYA_FILE} alınamadı/bozuk — kısmi dosya siliniyor."
        rm -f "$MEDYA_PART" "${BACKUP_DIR}/${MEDYA_FILE}"
        exit 1
    fi
fi
