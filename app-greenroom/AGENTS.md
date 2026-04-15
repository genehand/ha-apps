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
  - **No manual token refresh** - librespot's internal TokenProvider handles token refresh via Mercury keymaster API

- **MQTT Bridge**: Publishes discovery configs and state to Home Assistant
  - Main sensor with state (playing/paused) and JSON attributes
  - Auto-discovery via `homeassistant/*/greenroom/config`

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
    ├── token.rs           # OAuth credentials storage
    └── librespot/         # Spotify client (cluster monitoring, session management)
        ├── mod.rs
        └── client.rs

```

## Build

```bash
# Format code
cargo fmt

# Build locally
cargo build --release

# Run locally (requires TOKEN_FILE env or creates local file)
cargo run
```

The binary will be at `target/release/greenroom`

## Configuration

### OAuth Setup (Web UI)

Due to Spotify's OAuth restrictions on librespot's client ID (which only permits localhost redirects), the web UI uses a manual code exchange flow:

1. Install and start the Greenroom add-on
2. Open the Greenroom Web UI (uses ingress)
3. Click "Connect Spotify" button - opens instructions in a new window
4. Click "Open Spotify Authorization" to open Spotify in a new tab
5. Log in to Spotify and authorize Greenroom
6. The page will redirect to `127.0.0.1:5588` and show an error (expected!)
7. Copy the **entire URL** from the address bar (or just the code value if you prefer)
8. Paste into the form on the Greenroom instructions page
9. Click "Complete Connection" - credentials are saved and the daemon connects

The web UI remains accessible for checking connection status and playback info. Use "Disconnect" to clear stored credentials.

### Legacy OAuth (CLI)

If you prefer not to use the web UI, you can still use librespot's built-in OAuth flow:

1. Set `spotify_username` in add-on configuration
2. Start the add-on
3. Check logs for auth URL or use the web UI button
4. Credentials are saved to `/data/greenroom_token.json`

### Environment Variables / CLI Options

Configuration is via CLI options or environment variables. See [docs/CLI.md](docs/CLI.md) for the complete reference.

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

### Initial Setup

1. **Web UI OAuth**: User opens Greenroom Web UI, clicks "Connect Spotify"
2. **Instructions Page**: Server shows instructions with "Open Spotify Authorization" button
3. **PKCE Flow**: User clicks button, Spotify OAuth opens in new tab with PKCE challenge
4. **Manual Code/URL Entry**: Spotify redirects to localhost (fails in browser), user copies URL or code
5. **Token Exchange**: User pastes URL or code into form, server extracts code and exchanges for tokens via PKCE
6. **Credential Storage**: Credentials saved to `/data/greenroom_token.json`

### Runtime Operation

1. **Credential Detection**: Daemon detects stored credentials and connects to Spotify using librespot
2. **Spotify Session**: Establishes session with `streaming` OAuth scope (not Web API scopes)
3. **Token Management**: Once connected, librespot's internal **TokenProvider** automatically manages token refresh via the Mercury keymaster API (`hm://keymaster/token/authenticated`). No manual refresh needed.
4. **Cluster Join**: Sends `PutStateRequest` with `NEW_DEVICE` to join Spotify's Connect cluster via WebSocket
5. **WebSocket Monitoring**: Listens to `hm://connect-state/v1/cluster` for real-time playback updates
6. **Metadata Fetching**: Fetches track/album/artist metadata via librespot's internal Mercury API (not Web API)
7. **MQTT Publishing**: Publishes to `greenroom/<device_id>/state` via MQTT discovery
8. **Reconnection**: On WebSocket failures, daemon automatically reconnects with exponential backoff
9. **Auth Revocation**: If Spotify returns "Bad credentials" or "invalid_grant", credentials are cleared and HA notification is sent

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
6. **Token Refresh**: Internal TokenProvider fetches fresh tokens via `hm://keymaster/token/authenticated` when needed

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

### Notable: No Direct librespot-oauth

We don't directly use `librespot-oauth` crate because its `OAuthClientBuilder` is designed for desktop apps with localhost callbacks. Instead, we implement PKCE directly using `reqwest` in `web.rs`, which is more appropriate for the Home Assistant add-on environment (behind ingress with manual code/URL entry flow).

`librespot-oauth` is still pulled in transitively via `librespot-core` but not used directly.

### Dependency Fix

The vergen crate conflict was resolved by pinning specific versions similar to [spotatui](https://github.com/LargeModGames/spotatui):

```toml
vergen = "=9.0.6"
vergen-lib = "=9.1.0"
vergen-gitcl = "=1.0.8"
```

## Development Notes

### Testing OAuth Flow Locally

The manual code entry flow works without full ingress setup:

1. Run the binary with `GREENROOM_WEB_PORT=8099`
2. Navigate to `http://localhost:8099/`
3. Click "Connect Spotify" - opens instructions page
4. Click "Open Spotify Authorization" - opens Spotify OAuth in a new tab
5. After Spotify auth, the redirect to `127.0.0.1:5588` will fail (expected)
6. Copy the **entire URL** from the address bar (or just the code)
7. Paste into the form - the server will extract the code automatically
8. The credentials will be saved locally

Note: Without the `X-Ingress-Path` header, navigation links may not work correctly. For full testing with proper URL handling, build the add-on and install in a test HA instance.

### Token File Format

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1234567890,
  "scopes": ["streaming"]
}
```

Note: This file stores the initial OAuth credentials. Once a librespot session is established, token refresh is handled internally by librespot's TokenProvider via the Mercury keymaster API - the stored credentials are only used for initial connection.

## Implementation Details

### TokenProvider Management Strategy

- Store `AuthCredentials` (initial OAuth tokens) only for session establishment
- Once `Session::connect()` succeeds, librespot manages tokens internally
- TokenProvider uses Mercury keymaster API (`hm://keymaster/token/authenticated`) to fetch fresh tokens
- We only reconnect on actual WebSocket/connection failures, not token expiry
- Session stays alive as long as WebSocket is connected

### Code/URL Parsing

The `extract_oauth_code()` function in `web.rs` accepts:
- Full URL: `http://127.0.0.1:5588/login?code=AQABC123XYZ&state=...`
- Just the code: `AQABC123XYZ`
- Handles trimming and various URL formats

This is more user-friendly than requiring precise extraction of just the code value.
