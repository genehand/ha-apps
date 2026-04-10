# Build stage - use Rust to compile the binary
FROM rust:trixie AS builder

WORKDIR /build

# Copy Cargo files first for better caching
COPY Cargo.toml Cargo.lock ./
COPY src ./src

# Build release binary
RUN cargo build --release


# Runtime stage
FROM ghcr.io/home-assistant/base-debian:trixie

# Copy binary from builder
COPY --from=builder /build/target/release/dasher /usr/local/bin/dasher

# Make executable
RUN chmod +x /usr/local/bin/dasher

# S6 service files are in rootfs/etc/s6-overlay/
COPY rootfs /

LABEL \
    org.opencontainers.image.title="Home Assistant App: Dasher" \
    org.opencontainers.image.description="Filter websocket events for dashboard entities" \
    org.opencontainers.image.source="https://github.com/genehand/ha-apps"

EXPOSE 8125