# syntax=docker/dockerfile:1.7
ARG UV_VERSION=0.12.5
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra onthespot --no-install-project

COPY app ./app
RUN uv sync --locked --no-dev --extra onthespot --no-editable


FROM python:3.12-slim-bookworm AS runtime

ARG OCI_SOURCE=""
ARG OCI_REVISION=""
ARG OCI_CREATED=""
LABEL org.opencontainers.image.source=${OCI_SOURCE} \
      org.opencontainers.image.revision=${OCI_REVISION} \
      org.opencontainers.image.created=${OCI_CREATED}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ffmpeg \
        libdbus-1-3 \
        libegl1 \
        libgl1 \
        libglib2.0-0 \
        libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 musicbot \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin musicbot \
    && install -d -o musicbot -g musicbot -m 0750 \
        /data /data/onthespot /data/xdg-config \
        /tmp/musicbot /tmp/musicbot/cache /tmp/musicbot/home /tmp/musicbot/os

WORKDIR /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/musicbot.db \
    TEMP_DIR=/tmp/musicbot \
    TMPDIR=/tmp/musicbot/os \
    HOME=/tmp/musicbot/home \
    XDG_CONFIG_HOME=/data/xdg-config \
    XDG_CACHE_HOME=/tmp/musicbot/cache \
    ONTHESPOTDIR=/data/onthespot \
    QT_QPA_PLATFORM=offscreen

COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root app ./app
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root alembic.ini pyproject.toml ./

USER 10001:10001
CMD ["python", "-m", "app.main"]
