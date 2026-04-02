ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}

# Install uv
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh uv-install.sh && rm uv-install.sh
ENV PATH="/root/.local/bin:${PATH}"
ENV UV_LINK_MODE=copy

# Install build dependencies
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    g++ \
    libyaml-dev \
    cmake \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY rootfs/app/pyproject.toml rootfs/app/uv.lock rootfs/app/.python-version /app
RUN uv sync --frozen --no-dev --no-install-project

COPY rootfs /

RUN uv sync --frozen --no-dev

LABEL \
    org.opencontainers.image.title="Home Assistant App: HACS Shack" \
    org.opencontainers.image.description="Run HACS integrations separately" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"