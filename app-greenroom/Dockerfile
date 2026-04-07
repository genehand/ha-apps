# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest

# ====== Build stage ======
FROM ${BUILD_FROM} AS builder

# Install Rust and build dependencies
RUN apk add --no-cache \
    rust cargo \
    protobuf-dev \
    clang-dev \
    g++ \
    make \
    cmake \
    alsa-lib-dev \
    openssl-dev \
    pkgconfig

# Set up working directory
WORKDIR /build

# Copy cargo files first for caching
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY proto ./proto
COPY build.rs ./

# Build release binary
RUN cargo build --release

# ====== Runtime stage ======
FROM ${BUILD_FROM}

# Install runtime dependencies (even with null audio backend, librespot links these)
RUN apk add --no-cache \
    alsa-lib \
    openssl \
    ca-certificates

# Copy binary from build stage
COPY --from=builder /build/target/release/greenroom /usr/bin/
COPY run.sh /run.sh

# Create working directory
WORKDIR /data

CMD ["/run.sh"]
