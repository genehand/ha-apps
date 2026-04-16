# Greenroom CLI

Command-line options and environment variables available for running Greenroom directly.

## Usage

```bash
greenroom [OPTIONS]
```

## Options

| Variable | CLI Option | Default | Description |
|----------|------------|---------|-------------|
| `SPOTIFY_USERNAME` | `--spotify-username` | - | Spotify account email (optional, legacy OAuth) |
| `DEVICE_NAME` | `--device-name` | "Greenroom" | Name shown in HA and Spotify |
| `MQTT_HOST` | `--mqtt-host` | "core-mosquitto" | MQTT broker hostname |
| `MQTT_PORT` | `--mqtt-port` | 1883 | MQTT broker port |
| `MQTT_USERNAME` | `--mqtt-username` | - | MQTT auth username (auto-fetched in add-on) |
| `MQTT_PASSWORD` | `--mqtt-password` | - | MQTT auth password (auto-fetched in add-on) |
| `MQTT_DEVICE_ID` | `--mqtt-device-id` | "greenroom" | MQTT device ID (used in topic names) |
| `GREENROOM_WEB_PORT` | `--web-port` | 8099 | Port for web UI (internal, via ingress) |
| `TOKEN_FILE` | - | `/data/greenroom_token.json` | Path to store OAuth credentials |
| `RUST_LOG` | `--log-level` | "info" | Log level (trace/debug/info/warn/error) |

## Priority

CLI options take precedence over environment variables.

## Examples

### Run with custom device name and MQTT broker

```bash
greenroom --device-name "Living Room" --mqtt-host 192.168.1.100
```

### Run with environment variables

```bash
export DEVICE_NAME="Kitchen Spotify"
export MQTT_HOST="mosquitto.local"
export RUST_LOG=debug
greenroom
```

### Development mode with local token file

```bash
export TOKEN_FILE="./greenroom_token.json"
export GREENROOM_WEB_PORT=8099
cargo run
```

## Home Assistant Add-on

When running as a Home Assistant add-on, most options are automatically configured:

- MQTT credentials are fetched from the Supervisor API
- `TOKEN_FILE` defaults to `/data/greenroom_token.json`
- Web UI port is internal-only (accessed via ingress)

Only `DEVICE_NAME` is typically customized by users.
