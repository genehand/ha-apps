# app-soloist

Home Assistant add-on that runs Spotify Soloist (official headless Spotify client) as a
Spotify Connect device and bridges its WebSocket API to Home Assistant via MQTT discovery.

No librespot, no Spotify Web API, no OAuth — pairing is done from the Spotify app (select the device).

## Official References

Spotify's Soloist docs are the source of truth for everything the bridge speaks over
the wire. Read the relevant page before touching the protocol code:

- **[WebSocket API](https://developer.spotify.com/documentation/soloist/reference/websocket-api)**
  — the wire contract: inbound events (`soloist/events.rs`) and outbound commands
  (`soloist/commands.rs`).
- [Command line](https://developer.spotify.com/documentation/soloist/reference/command-line)
  — daemon flags, storage paths, exit codes (`soloist/daemon.rs`).
- [Downloads and updates](https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates)
  — archive URLs (stable names per arch) and the 90-day build lifetime.
- [Authentication](https://developer.spotify.com/documentation/soloist/concepts/authentication)
  — API key vs. Spotify Connect session, `--pair`, re-pairing.
- [Basic integration](https://developer.spotify.com/documentation/soloist/howtos/basic-integration)
  — event/command round-trip and the `speed`-based position interpolation pattern.

Protocol facts worth not rediscovering (API reference unless noted):

- **The WebSocket API has no authentication, authorization, TLS, Origin validation or
  network-exposure policy.** It is a same-machine surface: `soloist/daemon.rs` binds
  `127.0.0.1:0`, and the bridge only reads `ws.port`. Never bind it wider.
- `ws.addr` and `ws.port` are written *after* the server starts and removed on shutdown;
  port `0` lets the OS choose (`soloist/client.rs` polls the file and drops a stale one
  when the advertised port refuses connections). If `--ws` cannot listen, soloist still
  starts **without** the API and only logs a warning — a missing `ws.port` is a real
  symptom, not a slow start.
- `auth_state` is pushed on connect and on every authentication change, so `logged_in`
  never needs polling; `playback_state` is pushed on connect when already logged in.
  Query commands (`get_auth_state`, `get_state`, `get_queue`) reply with their event and
  send **no** `command_result`.
- `command_result` means "accepted and dispatched", not "state changed": playback changes
  arrive asynchronously as granular events (`track_changed`, `playback_changed`,
  `volume_changed`, `device_changed`, `context_changed`, `options_changed`,
  `position_sync`, `queue_changed`).
- `error` is sent only to the client that sent the bad message and the connection stays
  open — an error is never a reason to reconnect.
- Broadcast `queue_changed` is capped at 10 entries; `get_queue` takes `limit` (omitted
  or `0` = all available). `upcoming[].uid` is the stable queue-entry id, `source` is
  `context` | `queue` | `autoplay`.
- `available_actions` in `playback_state` lists what the current state supports
  (`pause`, `play`, `seek`, `seek_forward`/`seek_backward` with `step_ms`, `skip_next`,
  `skip_prev`, `add_to_queue`, `shuffle`, `set_repeat`). It uses player action names, not
  command names — `shuffle` → `set_shuffle`, `set_repeat` → the two repeat commands.
- Entity envelope: an item's `entity_type` may be `track`, `episode`, `artist`, `album`,
  `playlist`, `show`, `ad` or `unknown`; nested entities may omit `uri`;
  `decorations.parent.entity` is the album for a track but the **show** for an episode;
  `decorations.playback.content_ratings` carries labels such as `explicit`;
  `decorations.visual_identity.cover[].size` is one of `small`, `default`, `large`,
  `xlarge`.
- Repeat needs two coordinated commands and the order differs per target — see
  `repeat_commands` in `mqtt.rs` and the mapping under Key Design Decisions.
- Daemon exit codes: `0` clean, `1` general failure (invalid args, unwritable data or
  cache dir, **data dir already in use by another instance**, invalid build timestamp),
  `10` build expired (handled by a forced re-download). The data dir also holds
  `soloist.pid` while running and local crash reports (never uploaded) — check both
  before assuming a download problem.
- Debugging a live daemon: `soloist ctl trace` prints the raw event stream,
  `soloist ctl now --json` / `queue --json` print machine-readable state, and both use
  the same `ws.addr`/`ws.port` discovery as the bridge. `--pair` replaces a stored
  session (the normal flow is picking the device in the Spotify app — see README).

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
  - `soloist.rs` — module root; re-exports `run_client`, `SoloistCommand`, `SoloistDaemon`
  - `soloist/events.rs` — wire-format model for the Soloist WebSocket API
  - `soloist/commands.rs` — command serialization (`SoloistCommand`)
  - `soloist/client.rs` — WebSocket client lifecycle (`run_client`, connection handling)
  - `soloist/apply.rs` — event parsing + application to state (`handle_text`, `apply_*` helpers)
  - `soloist/oembed.rs` — oEmbed artist-name fallback (`OembedResolver`)
  - `soloist/embed.rs` — track-metadata fallback via the public embed page (`EmbedResolver`)
  - `soloist/daemon.rs` — soloist binary download/refresh + daemon supervisor (`SoloistDaemon`)
  - `state.rs` — shared `PlaybackState` + position anchor passthrough
  - `mqtt.rs` — MQTT discovery (sensor + active + power switches), state publishing, command translation

## Key Design Decisions

- **Audio**: soloist needs a PulseAudio backend. The s6 `run` script starts a null-sink
  PulseAudio
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
  Last resort when *none* of that helps — a track started without ever being
  seen in the queue whose snapshot is stripped to just its URI and duration —
  is the public embed page (`open.spotify.com/embed/track/<id>`,
  `soloist/embed.rs`): its `__NEXT_DATA__` payload carries the title, the
  artists (name **and** URI), the duration and the cover, all unauthenticated.
  One request per track URI per session (bounded FIFO of resolved metadata +
  of completed attempts), and the result is only ever used to *fill gaps* —
  anything soloist itself provided wins. Only fired when the item is still
  missing its title or artist. The embed payload has no album field, so the
  album falls back to the playback context instead: when the context entity
  *is* an album, its identity name and cover fill the album name and artwork
  for items that shipped no `parent` album and no cover (the item's own
  values always win, and a playlist/mosaic context is never borrowed).
  Creator entities may also omit their `uri` (the API reference allows nested
  entities to omit it "when unknown"), so a creator that ships a *name* but no
  URI still labels the artist — only lookups need a URI, and such a creator is
  never queued for one.
- **Sticky per-track labels**: `apply_entity` resolves each field as *snapshot
  value → embed-page value → previously published value for the same track URI*.
  A new URI inherits nothing, but a repeated bare snapshot of the track that is
  already playing can no longer blank the title, artist, album or artwork back
  to "Unknown" (which soloist's repeated decoration-less snapshots used to do).
- **Volume**: MQTT accepts 0-100 or 0-1; published back as 0-1 (HA `volume_level`).
  Mute is implemented in the bridge (mute→0, unmute→restore last non-zero).
- **Hardened wire decoding** (`soloist/events.rs`): every event payload field is
  `#[serde(default)]`, so a payload that omits something still applies the rest, and all
  numbers are decoded through `f64` coercion (a float-encoded `65.0` volume or
  `position_ms` no longer discards the whole snapshot — that failure mode was silent
  apart from a `debug!` line, and showed up as stale metadata in HA). Fields whose
  *absence must not be defaulted* (`auth_state.logged_in`, `volume_changed.volume`,
  `device_changed.is_active`, `position_sync.position`, `options_changed.options`) are
  `Option`s: `apply.rs` skips that field (or ignores the event) instead of applying a
  zero/false/off value.
- **Doc-advertised state is published**: `available_actions` (+ `seek_step_ms`, the step
  advertised for `seek_forward`/`seek_backward`), `media_content_rating` (the first
  `playback.content_ratings` label, held in `PlaybackState::content_rating`),
  `media_content_type` (`music`/`podcast` from `entity_type`), `media_series_title` (an
  **episode's** `parent` is its show, not an album) and `is_ad`. An `ad` item never
  overwrites the track identity (it only flips `is_ad`, keeping the track URI the sticky
  rules key on), and a queue entry without a name is skipped rather than published as a
  bare `spotify:track:` URI.
- **Connection health drives MQTT availability**: `ws_connected` is set by the client on
  connect/disconnect and published to the availability topic (also while the power switch
  gates the playback state, and only when the value differs from what this broker
  connection already sent — otherwise the per-event offer would add a retained publish to
  every `position_sync` tick). Without it, a dead daemon — or one that failed to enable
  `--ws`, which is non-fatal per the API docs — left HA showing a phantom idle device.
- **A lost WebSocket clears the session**: the client sets `logged_in = false` and calls
  `PlaybackState::clear_session` (item, context, queue, advertised actions → idle);
  `auth_state` re-establishes it on the next connect. While `ws_connected` is false the
  MQTT auth gate rejects commands outright (`mqtt.rs`), so nothing stale is buffered for
  replay on reconnect — do not "help" by draining the command channel on connect, which
  can only ever discard commands accepted while the link was still up. A documented
  `auth_state` logout clears exactly the same state (`clear_session`).
  The metadata caches (oEmbed names, embed-page metadata, learned track→artist URIs) are
  session-independent and deliberately survive. Once per run, if the API stays connected
  and unauthenticated for a while, the watchdog logs the pairing hint (the documented
  first-run state is `logged_in: false` until the device is picked in the Spotify app).
- **Daemon options**: `pipewire_device` (`--pipewire-device`; only meaningful when soloist
  ends up on PipeWire — the app runs PulseAudio, so it is normally inert),
  `cache_size_mb` (`--cache-size`, only sent for values >= 100: 0 is soloist's own
  documented "no limit" default, so the flag is omitted; 1..=99 is ignored with a warning
  because soloist would exit 1 and restart forever) and `pair`
  (`soloist --pair` once at bridge start, bounded by a 10-minute timeout, then the normal
  daemon). Exit code 1 now names the documented "another process is using the data dir"
  cause by checking `soloist.pid` for a live pid (`live_pid_hint`).

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
```

The soloist binary is not part of the image: the bridge downloads it at startup
into the persistent data dir (see the architecture notes above).

The binary will be at `target/release/soloist-bridge`.

## Run Locally (no add-on)

```bash
# Connect to an already-running soloist (dev loop: SOLOIST_WS_URL + MQTT)
SOLOIST_WS_URL=ws://127.0.0.1:9090 MQTT_HOST=homeassistant.local cargo run

# Let the bridge spawn soloist itself (needs a real API key)
SOLOIST_API_KEY=xxx MQTT_HOST=homeassistant.local cargo run
```

The bridge downloads and refreshes the soloist binary itself into
`<data-dir>/bin/soloist` (locally: `soloist-data/bin/soloist`) — no manual install
or PATH setup needed, same as in the add-on. Requires network access to
`soloist-builds.spotifycdn.com`.

Config is via env vars (see `config.rs`): `DEVICE_NAME`, `SOLOIST_API_KEY`,
`SOLOIST_WS_URL`, `SOLOIST_DATA_DIR`, `SOLOIST_CACHE_DIR`, `INITIAL_VOLUME`, `MQTT_HOST`,
`MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_DEVICE_ID`, `RUST_LOG`.

## Versioning

Version lives in `config.yaml`. Bump `Cargo.toml` with `sync-versions.sh`.

## Gotchas

- The soloist WebSocket API is unauthenticated (no TLS, no Origin checks): keep it on
  loopback (`--ws 127.0.0.1:0`) and never re-expose it — no `EXPOSE`/port publish, no
  proxying, no binding to `ws.addr` on a non-loopback value. The MQTT topics are the only
  intended control surface.
- The `pair` option re-pairs on **every** app start while it is enabled (`--pair` clears
  the stored session by design) — the bridge warns loudly, and it must be turned back off
  after a successful pairing. A failed/timed-out pair run must never wedge startup: pair
  mode always hands over to the supervised daemon.
- Do not "fix" availability by publishing a static `online`: it is now the signal that
  tells HA the daemon/WebSocket API is gone (see `publish_availability`). It must keep
  tracking `ws_connected` even while the power switch suppresses playback publishes.
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
