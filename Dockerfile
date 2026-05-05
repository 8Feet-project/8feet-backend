FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    DJANGO_SETTINGS_MODULE="eightfeet.settings"

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY efeet ./efeet
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY scripts/docker/entrypoint.sh ./scripts/docker/entrypoint.sh
COPY config.example.yaml ./config.example.yaml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

RUN chmod +x ./scripts/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./scripts/docker/entrypoint.sh"]
