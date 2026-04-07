#!/usr/bin/env bash

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/8feet}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET_DIR="${BACKUP_ROOT}/${STAMP}"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-admin}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-eightfeet}"

S3_ENDPOINT="${S3_ENDPOINT:-http://127.0.0.1:9000}"
S3_SECRET_ID="${S3_SECRET_ID:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
S3_BUCKET_REPORTS="${S3_BUCKET_REPORTS:-eightfeet-reports}"

mkdir -p "${TARGET_DIR}"

echo "[backup] dumping postgres..."
PGPASSWORD="${DB_PASSWORD}" pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    -F c \
    -f "${TARGET_DIR}/postgres.dump"

if command -v mc >/dev/null 2>&1 && [[ -n "${S3_SECRET_ID}" && -n "${S3_SECRET_KEY}" ]]; then
    echo "[backup] mirroring minio bucket..."
    mc alias set local "${S3_ENDPOINT}" "${S3_SECRET_ID}" "${S3_SECRET_KEY}" >/dev/null
    mc mirror --overwrite "local/${S3_BUCKET_REPORTS}" "${TARGET_DIR}/minio/${S3_BUCKET_REPORTS}" >/dev/null
else
    echo "[backup] skip minio backup: mc 或 S3 凭据未配置。"
fi

echo "[backup] cleaning expired backups..."
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime +"${RETENTION_DAYS}" -exec rm -rf {} +

echo "[backup] done: ${TARGET_DIR}"
