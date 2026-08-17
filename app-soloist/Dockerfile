# ====== Build stage: Rust bridge ======
FROM rust:trixie AS builder

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
COPY src ./src

# Build release binary
RUN cargo build --release --locked


# ====== Runtime stage ======
FROM ghcr.io/home-assistant/base-debian:trixie

# - pulseaudio: null-sink daemon so soloist has a working (silent) audio backend
# - libpulse0: pulseaudio client library soloist dlopens
# - libatomic1: hard dependency of the soloist binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    pulseaudio \
    libpulse0 \
    libatomic1 \
    ca-certificates \
    curl && \
    rm -rf /var/lib/apt/lists/*

COPY rootfs /

# Bridge binary
COPY --from=builder /build/target/release/soloist-bridge /usr/bin/soloist-bridge

# The soloist binary is NOT baked into the image.  The bridge downloads it at
# startup into the persistent data dir (/data/soloist/bin/soloist).

LABEL \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64" \
    org.opencontainers.image.title="Home Assistant App: Soloist" \
    org.opencontainers.image.description="Spotify playback device with MQTT discovery for Home Assistant" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"
