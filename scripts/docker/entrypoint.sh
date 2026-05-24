#!/usr/bin/env bash

set -euo pipefail

cd /app

if [ "$#" -gt 0 ]; then
  echo "[entrypoint] running supplied command: $*"
  exec "$@"
fi

echo "[entrypoint] running migrate..."
python src/manage.py migrate --noinput

echo "[entrypoint] collecting static files..."
python src/manage.py collectstatic --noinput

echo "[entrypoint] starting daphne..."
exec daphne -b 0.0.0.0 -p "${PORT:-8000}" eightfeet.asgi:application
