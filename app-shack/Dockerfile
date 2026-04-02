ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.14-alpine3.23
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}

# Install uv
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh uv-install.sh && rm uv-install.sh
ENV PATH="/root/.local/bin:${PATH}"
ENV UV_LINK_MODE=copy

# Install build dependencies for packages with C/Rust extensions
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    rust \
    cargo

WORKDIR /app

# Copy dependency files first to populate cache
COPY rootfs/app/pyproject.toml rootfs/app/uv.lock /app/

# Populate uv cache with base dependencies (no venv created, just cache)
RUN uv sync --frozen --no-install-project

# Copy the rest of the application
COPY rootfs /

LABEL \
    org.opencontainers.image.title="Home Assistant App: HACS Shack" \
    org.opencontainers.image.description="Run HACS integrations separately" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"