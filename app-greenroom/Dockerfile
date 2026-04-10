ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}

# ====== Build stage ======
FROM rust:trixie AS builder

WORKDIR /build

# Copy cargo files first for caching
COPY Cargo.toml Cargo.lock ./
COPY src ./src

# Build release binary
RUN cargo build --release

# ====== Runtime stage ======
FROM ${BUILD_FROM}

# Install runtime dependencies (even with null audio backend, librespot links these)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    libssl3 \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY rootfs /

# Copy binary from build stage
COPY --from=builder /build/target/release/greenroom /usr/bin/

LABEL \
    org.opencontainers.image.title="Home Assistant App: Greenroom" \
    org.opencontainers.image.description="Spotify Connect monitor for track info without the Web API" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"

EXPOSE 8099
