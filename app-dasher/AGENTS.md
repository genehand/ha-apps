# AGENTS.md - Coding Guidelines for HA Dasher

Home Assistant WebSocket event proxy

## Overview

HA Dasher is a proxy service that filters Home Assistant WebSocket events for dashboard entities.

## Working Directory

**Always run commands from this directory.** Do not run from the repo root.

```bash
cd app-dasher/
```

## Build Commands

```bash
# Build the project
cargo build

# Build for release (optimized)
cargo build --release

# Run locally
cargo run

# Run with specific log level
RUST_LOG=debug cargo run

# Check for errors without building
cargo check

# Format code
cargo fmt

# Run linter
cargo clippy

# Run tests
cargo test
```

## Testing

### Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: When modifying existing code, add or update tests to cover the changes
- **Goal**: Maintain test coverage for critical paths and edge cases

### Running Tests

```bash
# Run all tests
cargo test

# Run tests with output
cargo test -- --nocapture

# Run specific test
cargo test test_name
```

### Coverage Requirements

- **New functionality**: Must have comprehensive unit tests covering happy path and edge cases
- **Bug fixes**: Must have a test that would have caught the bug
- **Refactoring**: All existing tests must pass, add tests if coverage gaps exposed

## Linting

```bash
# Format code
cargo fmt

# Run clippy (linter)
cargo clippy -- -D warnings

# Check formatting without applying
cargo fmt -- --check
```

## Code Style Guidelines

### Style

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use double quotes for strings
- **Documentation**: Use `///` for public items, `//` for internal comments
- **Type hints**: Use Rust's strong type system; prefer explicit types in public APIs
- **Error handling**: Use `anyhow` for application errors, `thiserror` for library errors

### Naming Conventions

- **Variables**: `snake_case` (e.g., `client_entities`, `ha_host`)
- **Constants**: `SCREAMING_SNAKE_CASE` (e.g., `MAX_CONNECTIONS`)
- **Functions**: `snake_case` (e.g., `proxy_handler`, `get_client_ip`)
- **Structs/Enums**: `PascalCase` (e.g., `AppState`, `ClientInfo`)
- **Traits**: `PascalCase` (e.g., `EntityFilter`)
- **Private items**: No prefix, use `pub(crate)` or omit `pub`

## Error Handling

Use `anyhow` for error propagation and `thiserror` for custom error types:

```rust
use anyhow::{Context, Result};

async fn load_config() -> Result<Config> {
    let config = fs::read_to_string("config.yaml")
        .context("Failed to read config file")?;
    serde_yaml::from_str(&config)
        .context("Failed to parse config")
}
```

## Logging

Use the `tracing` crate with appropriate levels:

```rust
use tracing::{debug, info, warn, error};

debug!("Detailed debugging info");
info!("General information");
warn!("Warning messages");
error!("Error messages: {}", error_details);
```

## Async Patterns

Use `tokio` for async runtime:

```rust
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let listener = TcpListener::bind("0.0.0.0:8125").await?;
    // ...
}
```

## WebSocket Message Processing

Messages are JSON-encoded using `serde_json`. Handle with strongly-typed structures:

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct WsMessage {
    id: u64,
    #[serde(rename = "type")]
    msg_type: String,
}
```

## Home Assistant Integration

- WebSocket protocol follows Home Assistant's format
- Entity IDs follow pattern: `domain.object_id` (e.g., `light.kitchen`)

## Dashboard Filtering Architecture

Dasher filters WebSocket events based on which dashboard entities are visible to the user. Filtering is gated per browser tab so that dashboard pages receive only relevant entity updates, while settings and other pages receive all events.

### How It Works

1. **HTML Injection** (`src/http/inject.rs`):
   - The proxy intercepts all `text/html` responses from Home Assistant
   - Decompresses if `Content-Encoding: deflate` or `gzip`
   - Injects a small tracking `<script>` at the top of `<head>`
   - Recompresses if needed; skips if already injected

2. **In-browser Script** (injected into HTML):
   - Generates a unique `tab_id` per browser tab via `crypto.randomUUID()`
   - Patches `window.WebSocket` so every `/api/websocket` connection appends `?dasher_tab=<id>`
   - Sends `POST /dasher/panel` with `{tab_id, url_path}` on page load and every SPA navigation (`history.pushState`, `replaceState`, `popstate`)

3. **Panel Tracker** (`src/http/panel_tracker.rs`):
   - Receives `POST /dasher/panel` with the tab's current URL path
   - Looks up the active WebSocket connection by `tab_id`
   - Sets `filtering_active = true` for dashboard paths (`/lovelace*`, `/home`, `/dashboard*`)
   - Sets `filtering_active = false` for all other paths
   - **Restores the cached entity set** for the dashboard from `dashboard_configs` so navigation back to a previously viewed dashboard resumes correct filtering immediately
   - Caches panel updates for 30 seconds if the websocket hasn't connected yet (handles the race where the POST arrives before the websocket upgrade)

4. **WebSocket Filtering Gate** (`src/websocket/filtered.rs`):
   - Reads `?dasher_tab` from the WebSocket upgrade URL and stores it in `ClientState`
   - Checks the panel update cache on connection (handles early POSTs)
   - Tracks `lovelace/config` requests that have a non-null string `url_path` by mapping `request_id → url_path` in `pending_configs`
   - On `lovelace/config` response, parses entities/rules and saves them into `dashboard_configs` keyed by dashboard URL path
   - On `subscribe_entities`, tracks the subscription ID
   - Only filters when `filtering_active == true`
   - All other messages pass through regardless of filtering state

### Key Files

| File | Role |
|------|------|
| `src/http/inject.rs` | HTML decompression, script injection, recompression |
| `src/http/panel_tracker.rs` | Receives `POST /dasher/panel`, toggles filtering per tab |
| `src/http/proxy.rs` | Routes HTML responses through injection, proxies everything else |
| `src/websocket/filtered.rs` | Reads `tab_id` from query params, gates filtering on `filtering_active` |
| `src/state.rs` | Stores `tab_id`, `filtering_active`, per-dashboard `dashboard_configs`, in-flight `pending_configs`, and cached panel updates |

### Targeted Logging

Use `EnvFilter` syntax for targeted debugging:

```bash
# Debug HTML injection and panel tracking
RUST_LOG=info,dasher::http=debug cargo run

# Debug websocket filtering decisions
RUST_LOG=info,dasher::websocket::filtered=debug cargo run

# Debug everything in dasher, deps at warn
RUST_LOG=info,dasher=debug cargo run
```

## Docker/Container Guidelines

- Base image: Multi-stage build with `rust:trixie` and `base-debian:trixie`
- Supports architectures: `aarch64`, `amd64`
- Binary built statically for minimal image size
- Configuration mounted at `/data/options.json` in container

## Common Tasks

**Add a New Dependency**: Edit `Cargo.toml`, add to `[dependencies]`, then run `cargo build`.

**Update App Version**: Edit `config.yaml`, increment `version`, commit and tag.

**Local Testing**: Copy `proxy-config.yaml`, modify for environment, run `cargo run`, access at `http://localhost:8125`.

**Build Docker Image**: Run `./build.sh` (requires Docker).