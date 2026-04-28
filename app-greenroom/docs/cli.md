# Greenroom CLI

Command-line options and environment variables available for running Greenroom directly.

## Usage

```bash
greenroom [OPTIONS]
```

## Options

| Variable | CLI Option | Default | Description |
|----------|------------|---------|-------------|
| `DEVICE_NAME` | `--device-name` | "Greenroom" | Name shown in HA and Spotify |
| `MQTT_HOST` | `--mqtt-host` | "homeassistant.local" | MQTT broker hostname |
| `MQTT_PORT` | `--mqtt-port` | 1883 | MQTT broker port |
| `MQTT_USERNAME` | `--mqtt-username` | - | MQTT auth username |
| `MQTT_PASSWORD` | `--mqtt-password` | - | MQTT auth password |
| `MQTT_DEVICE_ID` | `--mqtt-device-id` | slugified device name | MQTT client ID and topic namespace |
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

- MQTT credentials are checked against the Supervisor API first, then fall back to add-on config options (for external MQTT brokers)
- `MQTT_DEVICE_ID` is derived from `DEVICE_NAME` (slugified) unless explicitly set
- `TOKEN_FILE` defaults to `/data/greenroom_token.json`
- Web UI port is internal-only (accessed via ingress)

Only `DEVICE_NAME` is typically customized by users.
