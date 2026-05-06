FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    DJANGO_SETTINGS_MODULE="eightfeet.settings"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY efeet ./efeet
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project \
    && ./.venv/bin/python -m playwright install --only-shell chromium \
    && test -x /ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell

COPY src ./src
COPY scripts/docker/entrypoint.sh ./scripts/docker/entrypoint.sh
COPY config.example.yaml ./config.example.yaml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

RUN chmod +x ./scripts/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./scripts/docker/entrypoint.sh"]
