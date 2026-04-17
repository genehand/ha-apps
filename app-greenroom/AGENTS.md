# app-greenroom

Home Assistant app that monitors Spotify playback via the Connect protocol.

> **🔑 Key Feature**: Does **NOT** use the Spotify Web API - works with any Spotify account.

## Overview

Greenroom connects to your Spotify account using librespot, monitors playback from any Spotify device, and publishes the current track info to Home Assistant via MQTT discovery. It provides real-time monitoring of what's playing on any Spotify device on your account.

### Current Capability: Monitor-Only

Greenroom **displays** playback information (track, artist, artwork, volume, shuffle state) from any Spotify device on your account. This is a pure monitoring integration - **no control capabilities** are provided.

## Why No Web API?

Unlike other Spotify integrations, Greenroom uses the **Spotify Connect protocol** (the protocol smart speakers use) rather than the Web API:

- ✅ **Works with any plan**: Free, Basic, Premium
- ✅ **No developer account**: No API keys, client secrets, or app registration needed
- ✅ **Avoids developer restrictions**: Spotify's [February 2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) blocks Web API playback control for non-premium accounts

**Note on paid accounts:** Even paid plans like "Basic Family" are affected by the new developer restrictions, only "Premium" individual accounts maintain full Web API access. The Connect protocol works with all account types.

## Architecture

- **Web UI**: Axum-based HTTP server for OAuth authentication (Home Assistant ingress)
  - PKCE OAuth flow to Spotify (no client secrets)
  - Minimal HTMX frontend with status display
  - Accepts full URL or just code from OAuth callback

- **Spotify Integration**: Uses librespot to connect to Spotify's WebSocket cluster
  - Monitors playback from any device on your account (phone, desktop, smart speaker)
  - Receives real-time track/artist/album metadata via Spotify's internal protocol
  - Joins the Spotify Connect cluster to receive playback updates from any device
  - **Does not use the Spotify Web API** - connects as a Connect device instead
  - **Dual token management**: OAuth tokens (for initial auth) + librespot session credentials (for reconnection)

- **MQTT Bridge**: Publishes discovery configs and state to Home Assistant
  - Main sensor with state (playing/paused) and JSON attributes
  - Auto-discovery via `homeassistant/*/greenroom/config`
  - **Active switch**: `switch.<device name> Active` to enable/disable the Spotify connection

All three components (web UI, Spotify daemon, MQTT bridge) run concurrently in one binary.

## File Structure

```
app-greenroom/
├── config.yaml            # Add-on configuration
├── Dockerfile             # Multi-stage Rust build
├── Cargo.toml             # Rust dependencies
├── build.rs               # Build script for vergen
└── src/                   # Rust application
    ├── main.rs            # Entry point - spawns web server + daemon + mqtt
    ├── web.rs             # Web UI (OAuth flow, status page)
    ├── mqtt.rs            # MQTT bridge (HA discovery, state publishing)
    ├── mqtt_state.rs      # Connection state persistence (MQTT switch state)
    ├── token.rs           # OAuth credentials storage
    └── librespot/         # Spotify client (cluster monitoring, session management)
        ├── mod.rs         # Module exports
        ├── client.rs      # Core orchestration
        ├── cluster.rs     # Cluster update handling (playback metadata extraction)
        ├── connection.rs  # Connection control
        ├── demo.rs        # Demo mode (wait for OAuth credentials)
        ├── helpers.rs     # Protocol helpers
        └── state.rs       # Playback state helpers
```

## Build

```bash
# Format code
cd app-greenroom && cargo fmt

# Build locally
cd app-greenroom && cargo build --release

# Run locally (requires TOKEN_FILE env or creates local file)
cd app-greenroom && cargo run
```

The binary will be at `app-greenroom/target/release/greenroom`

## Configuration

### Authentication Setup

See [docs/auth.md](docs/auth.md) for detailed OAuth setup instructions. Quick summary:

1. Open the Greenroom Web UI
2. Click "Connect Spotify" and follow the PKCE OAuth flow
3. Copy the URL from the 127.0.0.1 redirect, paste into the form
4. Credentials saved to `/data/greenroom_token.json`

### Environment Variables / CLI Options

Configuration is via CLI options or environment variables. See [docs/cli.md](docs/cli.md) for the complete reference.

Key options for development:

- `GREENROOM_WEB_PORT` - Web UI port (default: 8099)
- `TOKEN_FILE` - Path to credentials file (default: `/data/greenroom_token.json`)
- `RUST_LOG` - Log level (default: "info")

### Home Assistant MQTT Setup

1. Ensure MQTT integration is configured in HA (Mosquitto addon or external broker)
2. Start Greenroom add-on
3. The entities will auto-discover:
   - `sensor.<device name>` - Main playback state and attributes (track, artist, artwork, volume, etc.)

## How It Works

### Runtime Operation

1. **Authentication**: Establishes Spotify session
2. **Cluster Join**: Sends `PutStateRequest` with `NEW_DEVICE` to join Spotify's Connect cluster via WebSocket
3. **WebSocket Monitoring**: Listens to `hm://connect-state/v1/cluster` for real-time playback updates
4. **Metadata Fetching**: Fetches track/album/artist metadata via librespot's internal Mercury API (not Web API)
5. **MQTT Publishing**: Publishes to `greenroom/<device_id>/state` via MQTT discovery

### Demo Mode

If no credentials exist at startup:

- Daemon enters demo mode with "Not Connected" status
- Web UI shows "Connect Spotify" button
- Daemon polls every 10 seconds for new credentials
- When credentials appear, automatically connects

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

### Connection Control Switch: `switch.<device name> Active`

Controls whether the Spotify client connection is active:

- **ON**: Greenroom connects to Spotify and monitors playback
- **OFF**: Connection is disabled, Greenroom waits without connecting

The switch state is persisted across restarts in `greenroom_connection_state.json`.

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
6. **Session Token Refresh**: librespot's TokenProvider fetches fresh internal session tokens via `hm://keymaster/token/authenticated` when needed (different from OAuth tokens)

This is the same protocol used by official Spotify clients to sync playback across devices.

### Why No Cross-Device Control?

Spotify intentionally separates authentication for different use cases:

- **Connect Protocol** (librespot): `streaming` scope, can only control SELF when active
- **Web Player API** (open.spotify.com): Different auth flow, can control any device  
- **Web API** (api.spotify.com): Developer account required, premium-only after Feb 2026

The Web Player API requires TOTP-based authentication with dynamic secrets that change frequently. Legal concerns (cease and desist letters to similar projects) prevent us from pursuing this approach.

## Ingress Configuration

The web UI uses Home Assistant's ingress feature:

- **Port**: 8099 (internal, not exposed externally)
- **Access**: Via HA sidebar panel or `.../hassio/ingress/<addon_slug>`
- **URL Handling**: Uses `X-Ingress-Path` HTTP header (set by HA Supervisor) to construct proper URLs for all links and forms
- **Readiness**: Notifies S6-overlay via `/dev/fd/3` when web server is bound and ready

## Dependencies

### Direct Dependencies

- `librespot-core`: Spotify Connect protocol implementation
- `librespot-metadata`: Track/album metadata fetching
- `librespot-protocol`: Protobuf definitions

### Dependency Fix

The vergen crate conflict was resolved by pinning specific versions similar to [spotatui](https://github.com/LargeModGames/spotatui):

```toml
vergen = "=9.0.6"
vergen-lib = "=9.1.0"
vergen-gitcl = "=1.0.8"
```

## Development Notes

## Implementation Details

### Authentication

See [docs/auth.md](docs/auth.md) for detailed documentation on:

- OAuth setup instructions and PKCE flow
- Testing OAuth locally during development
- Dual token system (OAuth + librespot session credentials)
- Background token refresh strategy
- Reconnection flow and error handling
- Token file format and why we don't use librespot-oauth directly
