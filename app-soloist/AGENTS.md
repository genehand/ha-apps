# app-soloist

Home Assistant add-on that runs Spotify Soloist (official headless Spotify client) as a
Spotify Connect device and bridges its WebSocket API to Home Assistant via MQTT discovery.

No librespot, no Spotify Web API, no OAuth — pairing is done from the Spotify app (select the device).

## Architecture

```
soloist (daemon) ── WebSocket (127.0.0.1:0, port in <data-dir>/ws.port) ──> soloist-bridge ── MQTT ──> Home Assistant
     ▲                                                                        │
     └────────────── spawned & supervised by soloist-bridge (restarts) ───────┘
```

- **soloist**: Spotify's proprietary daemon, downloaded at bridge startup from
  `https://soloist-builds.spotifycdn.com/soloist_release_<arch>.tar.gz` (stable URLs)
  into `<data-dir>/bin/soloist`. The bridge refreshes it when the binary is missing,
  older than 7 days, or when the daemon exits with code 10 (build expired).
- **soloist-bridge** (Rust, `src/`):
  - `main.rs` — orchestration: spawns soloist daemon + ws client + MQTT bridge
  - `config.rs` — CLI/env config (clap). `SOLOIST_WS_URL` override skips daemon spawning
  - `soloist.rs` — WebSocket client, event parsing (serde), command serialization, daemon supervisor
  - `state.rs` — shared `PlaybackState` + position anchor passthrough
  - `mqtt.rs` — MQTT discovery (sensor + active switch), state publishing, command translation

## Key Design Decisions

- **Audio**: soloist needs a PulseAudio backend. `run.sh` starts a null-sink PulseAudio
  (silent) unless `PULSE_SERVER` is set (supervisor-injected when `audio: true`). No audio
  passthrough by default — this is a passive track-info + controls device.
- **No MQTT media_player** (HA's MQTT integration doesn't support it): the add-on publishes a
  sensor via discovery plus raw command topics; the user wires a Template Media Player
  (`service_scripts` → `mqtt.publish`) — full config lives in README.md.
- **Repeat mapping**: soloist `off|context|track` ↔ HA `off|all|one`. Turning repeat off
  requires TWO commands (`set_repeat_track false`, then `set_repeat_context false`).
- **Seek payloads**: MQTT `cmd/seek` takes seconds; the ws `seek` command takes `position_ms`.
- **Position tracking**: soloist sends `position_sync` anchors; the bridge passes
  `media_position` + `media_position_updated_at` through as-is and Home Assistant
  interpolates the progress bar on its own.
- **Artist refetch**: soloist's first `playback_state` for a new track often ships
  empty creator decorations, so the bridge re-requests `get_state` shortly after a
  track starts to fill in the artist once the metadata has loaded.
- **Volume**: MQTT accepts 0-100 or 0-1; published back as 0-1 (HA `volume_level`).
  Mute is implemented in the bridge (mute→0, unmute→restore last non-zero).

## Build

```bash
# Format + lint
cd app-soloist && cargo fmt && cargo clippy --all-targets

# Test (event parsing, command translation)
cargo test

# Release build
cargo build --release

# Local Docker image
./build.sh            # amd64
./build.sh aarch64    # ARM64

The soloist binary is not part of the image: the bridge downloads it at startup
into the persistent data dir (see the architecture notes above).
```

The binary will be at `target/release/soloist-bridge`.

## Run Locally (no add-on)

```bash
# Connect to an already-running soloist (dev loop: SOLOIST_WS_URL + MQTT)
SOLOIST_WS_URL=ws://127.0.0.1:9090 MQTT_HOST=homeassistant.local cargo run

# Let the bridge spawn soloist itself (needs a real API key)
SOLOIST_API_KEY=xxx MQTT_HOST=homeassistant.local cargo run

The bridge downloads and refreshes the soloist binary itself into
`<data-dir>/bin/soloist` (locally: `soloist-data/bin/soloist`) — no manual install
or PATH setup needed, same as in the add-on. Requires network access to
`soloist-builds.spotifycdn.com`.
```

Config is via env vars (see `config.rs`): `DEVICE_NAME`, `SOLOIST_API_KEY`,
`SOLOIST_WS_URL`, `SOLOIST_DATA_DIR`, `SOLOIST_CACHE_DIR`, `INITIAL_VOLUME`, `MQTT_HOST`,
`MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_DEVICE_ID`, `RUST_LOG`.

## Versioning

Version lives in `config.yaml`. Bump `Cargo.toml` with `sync-versions.sh`.

## Gotchas

- Do NOT vendor the soloist binary in the repo (Spotify forbids redistribution);
  the bridge always downloads it at runtime from the official CDN into the
  persistent data dir (`/data/soloist/bin/soloist`).
- `soloist` must bind the WebSocket on loopback with port 0 and publish the actual port in
  `<data-dir>/ws.port` — the bridge polls that file (do not hardcode a fixed ws port).
- The add-on needs `host_network: true` so the Spotify app can discover the device.
- Exit code 10 from soloist = expired build → the bridge re-downloads a fresh build
  automatically and restarts the daemon. Download failures are retried with backoff.
- `cargo build --release --locked` in the Dockerfile requires a committed `Cargo.lock`.
