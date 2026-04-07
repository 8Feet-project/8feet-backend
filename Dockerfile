FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    DJANGO_SETTINGS_MODULE="eightfeet.settings"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY scripts/docker/entrypoint.sh ./scripts/docker/entrypoint.sh
COPY config.example.yaml ./config.example.yaml

RUN chmod +x ./scripts/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./scripts/docker/entrypoint.sh"]
