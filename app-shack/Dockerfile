FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24

ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}

# Install uv
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh uv-install.sh && rm uv-install.sh
ENV PATH="/root/.local/bin:${PATH}"
ENV UV_LINK_MODE=copy

# Install build dependencies for packages with C/Rust extensions
RUN apk add --no-cache \
    git \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    rust \
    cargo

WORKDIR /app

# Copy dependency files first to populate cache
COPY rootfs/app/pyproject.toml rootfs/app/uv.lock /app/
COPY rootfs/app/scripts/prune_homeassistant.py /app/scripts/prune_homeassistant.py

# Sync dependencies, then drop the ~350 MB of Home Assistant (installed as a
# library) that the shim can never import: components/, core, bootstrap and the
# helpers that need a running hass. See scripts/prune_homeassistant.py.
# Pruning in the same layer keeps those bytes out of the image entirely.
RUN uv sync --frozen --no-install-project \
    && .venv/bin/python scripts/prune_homeassistant.py

# Copy the rest of the application
COPY rootfs /

# The prune only keeps what shim/import_patch.py's UPSTREAM_* lists need, so
# running the patcher in the pruned tree proves the keep-list still matches the
# code: it imports the util/const modules, then installs shim.core and imports
# the upstream helpers behind it, exactly as the app does at start. This fails the
# build instead of a user's first start.
RUN .venv/bin/python -c "from shim.import_patch import setup_import_patching, UPSTREAM_HELPER_MODULES; setup_import_patching(None); import importlib; [importlib.import_module(f'homeassistant.helpers.{n}') for n in UPSTREAM_HELPER_MODULES]; print(len(UPSTREAM_HELPER_MODULES), 'upstream HA helper modules importable')"

LABEL \
    org.opencontainers.image.title="Home Assistant App: HACS Shack" \
    org.opencontainers.image.description="Run HACS integrations separately" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"