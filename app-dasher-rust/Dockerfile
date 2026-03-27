# Multi-stage build for Rust add-on

ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie

# Build stage - use Rust to compile the binary
FROM rust:trixie AS builder

WORKDIR /build

# Copy Cargo files first for better caching
COPY Cargo.toml Cargo.lock ./
COPY src ./src

# Build release binary
RUN cargo build --release

# Runtime stage - use Home Assistant base image
FROM ${BUILD_FROM}

# Copy binary from builder
COPY --from=builder /build/target/release/dasher /usr/local/bin/dasher

# Make executable
RUN chmod +x /usr/local/bin/dasher

# S6 service files are already in rootfs/etc/services.d/dasher/
# The base image includes s6-overlay
COPY rootfs /

ARG BUILD_NAME="Dasher"
ARG BUILD_DESCRIPTION="Filter websocket events for dashboard entities"
ARG TARGETARCH
ARG BUILD_VERSION
ARG BUILD_REPOSITORY
ARG BUILD_DATE
ARG BUILD_REF

LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${TARGETARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    maintainer="Gene Hand" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.authors="Gene Hand" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.version="${BUILD_VERSION}"

EXPOSE 8125