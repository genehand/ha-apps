# app-greenroom

A Home Assistant Add-on that monitors Spotify playback and exposes it via MQTT discovery.

> **🔑 Key Feature**: Does **NOT** use the Spotify Web API - works with any Spotify account including Free, Family, and Duo plans without API keys or developer credentials.

## Overview

Greenroom connects to your Spotify account using librespot, monitors playback from any Spotify device, and publishes the current track info to Home Assistant via MQTT discovery. It appears as a single sensor with rich attributes that can be used in dashboards, automations, and media player cards.

## Why No Web API?

Unlike other Spotify integrations, Greenroom uses the **Spotify Connect protocol** (the same protocol smart speakers use) rather than the Web API:

- ✅ **Works with any plan**: Free, Premium, Family, Duo - no restrictions
- ✅ **No developer account**: No API keys, client secrets, or app registration needed
- ✅ **Avoids developer restrictions**: [Spotify's February 2026 policy changes](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) block Web API playback control for non-premium accounts
- ✅ **More reliable**: Direct WebSocket connection, not HTTP polling
- ✅ **Real-time updates**: Sub-second state changes via the cluster protocol

**Note on paid accounts:** Even paid plans like "Basic Family" are affected by the new developer restrictions. Only "Premium" individual accounts maintain full Web API access. The Connect protocol works with all account types without these limitations.

The trade-off is that Greenroom is currently **monitor-only** (no playback control). See [PLAYER.md](./PLAYER.md) for potential approaches to add control without the Web API.

## Architecture

- **Spotify Integration**: Uses librespot 0.8 to connect to Spotify's WebSocket cluster
  - Monitors playback from any device on your account (phone, desktop, smart speaker)
  - Receives real-time track/artist/album metadata via Spotify's internal protocol
  - Joins the Spotify Connect cluster to receive cross-device updates
  - **Does not use Spotify Web API** - connects as a Connect device instead
- **MQTT Bridge**: Publishes discovery configs and state to Home Assistant
  - Single sensor with state (playing/paused) and JSON attributes
  - Auto-discovery via `homeassistant/sensor/greenroom/config`
  - All track info, artwork, volume, position, shuffle/repeat state as attributes

## File Structure

```
app-greenroom/
├── AGENTS.md              # This file
├── PLAYER.md              # Design doc for playback control (without Web API)
├── config.yaml            # Add-on configuration
├── Dockerfile             # Multi-stage Rust build
├── run.sh                 # Container entry point
└── rootfs/app/            # Rust application
    ├── Cargo.toml         # Rust dependencies
    └── src/
        ├── main.rs        # Entry point
        ├── librespot/     # Spotify client (OAuth, cluster monitoring)
        └── mqtt.rs        # MQTT bridge (HA discovery)
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

### Spotify Account Requirements

Greenroom uses the **Spotify Connect protocol** (like a smart speaker), not the Web API. See [Why No Web API?](#why-no-web-api) for details on why this matters.

**Requirements:**
- ✅ Any Spotify plan: Free, Premium, Family, Duo
- ✅ No developer account or API keys needed
- ⚠️ Currently monitor-only (playback control not yet implemented)

**OAuth Login:**
1. Start the add-on
2. Open the web UI (or check logs for auth URL)
3. Log in to Spotify in your browser
4. Token is saved for future restarts

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
3. The sensor will auto-discover as `sensor.greenroom` (or `sensor.<device_id>`)
4. The device will appear in Settings → Devices → MQTT

## How It Works

1. **OAuth Authentication**: User logs in via browser using PKCE (no client secrets), token saved to `/config`
2. **Spotify Session**: Establishes session with `streaming` OAuth scope (not Web API scopes)
3. **Cluster Join**: Sends `PutStateRequest` with `NEW_DEVICE` to join Spotify's Connect cluster via WebSocket
4. **WebSocket Monitoring**: Listens to `hm://connect-state/v1/cluster` for real-time playback updates
5. **Metadata Fetching**: Fetches track/album/artist metadata via librespot's internal Mercury API (not Web API)
6. **MQTT Publishing**: Publishes to `greenroom/<device_id>/state` and `.../attributes`

**Note**: All communication uses the Connect protocol (WebSocket cluster + internal HTTP endpoints), not the public Web API. This is the same protocol used by smart speakers and the official Spotify app.

## Home Assistant Sensor

### State
The sensor state is simple: `playing` or `paused`

### Attributes
All playback info is in attributes following HA's media player naming:

```yaml
media_title: "Song Name"
media_artist: "Artist Name"
media_album_name: "Album Name"
media_image_url: "https://i.scdn.co/image/..."
volume: 75
is_volume_muted: false
media_position: 45
media_duration: 180
media_position_updated_at: "2026-04-07T16:19:09.375933+00:00"
media_content_id: "spotify:playlist:..."
source: "Living Room Speaker"
shuffle: true
repeat: "context"  # off, context, or track
```

### Using in Dashboards

**Template sensor for media player card:**
```yaml
# configuration.yaml
template:
  - sensor:
      - name: "Spotify Now Playing"
        state: "{{ states('sensor.greenroom') }}"
        attributes:
          media_title: "{{ state_attr('sensor.greenroom', 'media_title') }}"
          media_artist: "{{ state_attr('sensor.greenroom', 'media_artist') }}"
          entity_picture: "{{ state_attr('sensor.greenroom', 'media_image_url') }}"
```

**Picture card with artwork:**
```yaml
type: picture-entity
entity: sensor.greenroom
image: "{{ state_attr('sensor.greenroom', 'media_image_url') }}"
```

## Spotify Cluster Protocol

The key insight for monitoring cross-device playback:

1. **Dealer WebSocket**: `wss://guc3-dealer.spotify.com/`
2. **Connection ID**: Received on `hm://pusher/v1/connections/` - required for HTTP API calls
3. **Cluster Hello**: `PUT /connect-state/v1/devices/{device_id}` with `PutStateRequest`
   - Must include `NEW_DEVICE` reason
   - DeviceInfo with capabilities
4. **Cluster Updates**: Received on `hm://connect-state/v1/cluster` as `ClusterUpdate` protobuf

This is the same protocol used by official Spotify clients to sync playback state across devices.

## Dependencies

- tokio (async runtime)
- rumqttc (MQTT client)
- librespot-* 0.8 (Spotify libraries)
- chrono (timestamp handling)
- serde_json (attribute serialization)

## Dependency Fix

The vergen crate conflict was resolved by pinning specific versions:
```toml
vergen = "=9.0.6"
vergen-lib = "=9.1.0"
vergen-gitcl = "=1.0.8"
```

This approach was learned from [spotatui](https://github.com/LargeModGames/spotatui).
