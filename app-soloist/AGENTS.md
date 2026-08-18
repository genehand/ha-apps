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
  - `mqtt.rs` — MQTT discovery (sensor + active + power switches), state publishing, command translation

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
  track starts to fill in the artist once the metadata has loaded. It also captures
  artist identity from the latest `queue_changed` `upcoming` metadata (track URI →
  artists, track URI → artist URIs, artist URI → name) so a track seen in the queue
  is resolved immediately, by URI, without waiting for a refetch. The snapshot is
  replaced on every `queue_changed` (only the playing track's entry is carried over)
  so the cache stays bounded. Final fallback: creator entities that ship a
  `spotify:artist:` URI but no name are resolved via Spotify's public oEmbed API
  (`open.spotify.com/oembed`, no auth — the artist embed `title` is the artist
  name). Results are cached per artist URI (bounded FIFO, survives queue rotation)
  and deduplicated against in-flight lookups, so repeated snapshots never re-hit
  the API. Track → artist-URI learning is a separate persistent bounded cache
  (fed from the `previous` and `upcoming` entries, FIFO-capped): it survives
  queue rotation, so a track whose playback snapshot ships with *no* creator
  URIs at all still resolves its artist from the cached oEmbed names — even
  when the rotation event races the snapshot of the newly started track.
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
- The soloist daemon persists a playback-state restore snapshot into
  `<data-dir>/cache/Users/<user>/context_player_state_restore` (tied to the data dir, NOT
  `--cache-dir` which is volatile) and never flushes it on shutdown — not even on SIGTERM:
  every restart replays the last *paused* track before the live session state arrives (and
  indefinitely, if the session stays quiet). The bridge clears that one file before spawning
  the daemon so it boots idle and reports the live session state; `primary.ldb` and the other
  `cache/Users/*` files are left alone (may hold device identity).
- Shutdown is graceful: the bridge SIGTERMs the soloist child and SIGKILLs only after a 5s
  grace period (a hard kill skips daemon cleanup and leaves the crashpad handler orphaned),
  and main.rs waits for the daemon task to finish instead of aborting it, so soloist is
  never orphaned on Ctrl+C.
- Exit code 10 from soloist = expired build → the bridge re-downloads a fresh build
  automatically and restarts the daemon. Download failures are retried with backoff.
- `cargo build --release --locked` in the Dockerfile requires a committed `Cargo.lock`.
- Rust drops let-bound guards at the end of their **scope**, not at last use: never
  `.await` while a state `RwLock` guard is in scope — the same task then blocks on
  itself (e.g. `queue_changed` once held its write guard across the oEmbed
  dispatch, whose `maybe_lookup` takes the read lock → hard deadlock on the first
  event with an unresolved artist URI). Guard scopes must be explicit `{}` blocks.
- Never block on the MQTT client channel while handling state: the eventloop that
  drains it runs in the same select loop, so a full channel would deadlock the
  bridge. `publish_state` snapshots the payloads under the lock, releases it, and
  sends via non-blocking `try_publish` (a full channel drops the update; the next
  state change re-publishes).
