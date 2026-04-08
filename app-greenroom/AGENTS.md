# app-greenroom

A Home Assistant Add-on that monitors Spotify playback via the Connect protocol (not Web API).

> **🔑 Key Feature**: Does **NOT** use the Spotify Web API - works with any Spotify account including Free, Basic, and Premium plans without API keys or developer credentials.

## Overview

Greenroom connects to your Spotify account using librespot, monitors playback from any Spotify device, and publishes the current track info to Home Assistant via MQTT discovery. It provides real-time monitoring of what's playing on any Spotify device on your account.

### Current Capability: Monitor-Only

Greenroom **displays** playback information (track, artist, artwork, volume, shuffle state) from any Spotify device on your account. This is a pure monitoring integration - **no control capabilities** are provided.

## Why No Web API?

Unlike other Spotify integrations, Greenroom uses the **Spotify Connect protocol** (the same protocol smart speakers use) rather than the Web API:

- ✅ **Works with any plan**: Free, Basic, Premium
- ✅ **No developer account**: No API keys, client secrets, or app registration needed
- ✅ **Avoids developer restrictions**: Spotify's [February 2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) blocks Web API playback control for non-premium accounts

**Note on paid accounts:** Even paid plans like "Basic Family" are affected by the new developer restrictions, only "Premium" individual accounts maintain full Web API access. The Connect protocol works with all account types.

## Architecture

- **Spotify Integration**: Uses librespot to connect to Spotify's WebSocket cluster
  - Monitors playback from any device on your account (phone, desktop, smart speaker)
  - Receives real-time track/artist/album metadata via Spotify's internal protocol
  - Joins the Spotify Connect cluster to receive playback updates from any device
  - **Does not use Spotify Web API** - connects as a Connect device instead
- **MQTT Bridge**: Publishes discovery configs and state to Home Assistant
  - Main sensor with state (playing/paused) and JSON attributes
  - Auto-discovery via `homeassistant/*/greenroom/config`

## File Structure

```
app-greenroom/
├── AGENTS.md              # This file
├── config.yaml            # Add-on configuration
├── Dockerfile             # Multi-stage Rust build
└── rootfs/app/            # Rust application
    ├── Cargo.toml         # Rust dependencies
    └── src/
        ├── main.rs        # Entry point
        ├── librespot/     # Spotify client (OAuth, cluster monitoring, command sender)
        └── mqtt.rs        # MQTT bridge (HA discovery, command receiver)
```

## Build

```bash
# Build locally
cd rootfs/app
cargo build
cargo run
```

Or with Docker:
```bash
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest -t app-greenroom .
```

## Configuration

**OAuth Login:**

1. Start the add-on
2. Open the web UI (or check logs for auth URL)
3. Log in to Spotify in your browser
4. Token is saved for future sessions

### Environment Variables / CLI Options

| Variable | CLI Option | Default | Description |
|----------|------------|---------|-------------|
| `SPOTIFY_USERNAME` | `--spotify-username` | - | Spotify account email (optional) |
| `DEVICE_NAME` | `--device-name` | "Greenroom" | Name shown in HA and Spotify |
| `MQTT_HOST` | `--mqtt-host` | "homeassistant.local" | MQTT broker hostname |
| `MQTT_PORT` | `--mqtt-port` | 1883 | MQTT broker port |
| `MQTT_USERNAME` | `--mqtt-username` | - | MQTT auth username |
| `MQTT_PASSWORD` | `--mqtt-password` | - | MQTT auth password |
| `MQTT_DEVICE_ID` | `--mqtt-device-id` | "greenroom" | Unique device ID for topics |
| `RUST_LOG` | `--log-level` | "info" | Log level (trace/debug/info/warn/error) |

CLI options take precedence over environment variables.

### Home Assistant MQTT Setup

1. Ensure MQTT integration is configured in HA (Mosquitto addon or external broker)
2. Start Greenroom add-on
3. The entities will auto-discover:
   - `sensor.<device name>` - Main playback state and attributes (track, artist, artwork, volume, etc.)

## How It Works

1. **OAuth Authentication**: User logs in via browser using PKCE (no client secrets), token saved to `/config`
2. **Spotify Session**: Establishes session with `streaming` OAuth scope (not Web API scopes)
3. **Cluster Join**: Sends `PutStateRequest` with `NEW_DEVICE` to join Spotify's Connect cluster via WebSocket
4. **WebSocket Monitoring**: Listens to `hm://connect-state/v1/cluster` for real-time playback updates
5. **Metadata Fetching**: Fetches track/album/artist metadata via librespot's internal Mercury API (not Web API)
6. **MQTT Publishing**: Publishes to `greenroom/<device_id>/state` and `.../attributes`

## Home Assistant Entities

### Main Sensor: `sensor.<device name>`

#### State
The sensor state is simple: `playing`, `paused`, or `idle`

#### Attributes
All playback info is in attributes following HA's media player naming:

```yaml
media_title: "Song Name"
media_artist: "Artist Name"
media_album_name: "Album Name"
media_image_url: "https://i.scdn.co/image/..."
volume: 0.75
is_volume_muted: false
media_position: 45
media_duration: 180
media_position_updated_at: "2026-04-07T16:19:09.375933+00:00"
media_content_id: "spotify:playlist:..."
source: "Living Room Speaker"
shuffle: true
repeat: "context"
```

## Spotify Cluster Protocol

How Greenroom monitors playback across devices:

1. **Dealer WebSocket**: `wss://guc3-dealer.spotify.com/`
2. **Connection ID**: Received on `hm://pusher/v1/connections/` - identifies our session
3. **Cluster Hello**: `PUT /connect-state/v1/devices/{device_id}` with `PutStateRequest`
   - Includes `NEW_DEVICE` reason
   - DeviceInfo with monitoring capabilities
4. **Cluster Updates**: Received on `hm://connect-state/v1/cluster` as `ClusterUpdate` protobuf
   - Contains `active_device_id` showing which device is currently playing
   - Contains player state (track, position, volume, shuffle, repeat, etc.)
5. **Metadata Fetching**: Mercury API calls to get track/artist/album details

This is the same protocol used by official Spotify clients to sync playback across devices.

### Why No Cross-Device Control?

Spotify intentionally separates authentication for different use cases:

- **Connect Protocol** (librespot): `streaming` scope, can only control SELF when active
- **Web Player API** (open.spotify.com): Different auth flow, can control any device  
- **Web API** (api.spotify.com): Developer account required, premium-only after Feb 2026

The Web Player API requires TOTP-based authentication with dynamic secrets that change frequently.  Legal concerns (cease and desist letters to similar projects) prevent us from pursuing this approach.

## Dependency Fix

The vergen crate conflict was resolved by pinning specific versions simiar to [spotatui](https://github.com/LargeModGames/spotatui):

```toml
vergen = "=9.0.6"
vergen-lib = "=9.1.0"
vergen-gitcl = "=1.0.8"
```
