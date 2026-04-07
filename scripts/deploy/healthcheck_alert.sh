#!/usr/bin/env bash

set -euo pipefail

APP_READY_URL="${APP_READY_URL:-http://127.0.0.1:8000/readyz}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
ALERT_NAME="${ALERT_NAME:-8feet-backend-readyz}"

response="$(curl -sS --max-time 5 "${APP_READY_URL}" || true)"

if [[ "${response}" == *"\"status\": \"ready\""* || "${response}" == *"\"status\":\"ready\""* ]]; then
    echo "[healthcheck] ready"
    exit 0
fi

echo "[healthcheck] not ready: ${response:-request_failed}"

if [[ -n "${ALERT_WEBHOOK_URL}" ]]; then
    payload="{\"alert\":\"${ALERT_NAME}\",\"message\":\"8feet-backend readyz failed\",\"ready_url\":\"${APP_READY_URL}\"}"
    curl -sS -X POST "${ALERT_WEBHOOK_URL}" \
        -H "Content-Type: application/json" \
        -d "${payload}" >/dev/null || true
fi

exit 1
