# AGENTS.md - Coding Guidelines for HA Dasher

Home Assistant app that filters WebSocket events for dashboard entities.

## Repository Structure

This repository contains three implementations in subdirectories:

- **app-dasher/**: Original Python implementation (legacy)
- **app-dasher-rust/**: New Rust implementation (current)
- **app-shack/**: HACS Shack for running Home Assistant integrations outside HA

### Important: Working Directories

Each project has its own working directory. **Do not run commands from the repo root.**

| Project | Working Directory | Example Command |
|---------|------------------|-----------------|
| app-dasher-rust | `app-dasher-rust/` | `cd app-dasher-rust && cargo build` |
| app-dasher | `app-dasher/rootfs/app/` | `cd app-dasher/rootfs/app && python3 dasher.py` |
| app-shack | `app-shack/rootfs/app/` | `cd app-shack/rootfs/app && python3 main.py` |

## Upstream Code Reference

We maintain scripts to fetch code from upstream Home Assistant and HACS repositories for reference and reuse:

### Fetch Scripts

- `app-shack/scripts/fetch_ha_files.py`: Downloads HA core files (const.py, exceptions.py, utils)
- `app-shack/scripts/fetch_hacs_files.py`: Downloads HACS utility modules (version, validation, queue management)

### When to Use

**Check upstream first when:**
- Implementing version comparison logic → use HACS's `utils/version.py`
- Need validation schemas → use HACS's `utils/validate.py`
- Building download/management systems → use HACS's `utils/queue_manager.py`
- Working with HA constants → reference HA's `const.py`
- Handling HA exceptions → reuse HA's `exception.py` classes

### How to Use

```bash
# Fetch latest HA release files
cd app-shack/scripts && python3 fetch_ha_files.py

# Fetch latest HACS release files
cd app-shack/scripts && python3 fetch_hacs_files.py

# Preview without downloading
python3 fetch_ha_files.py --dry-run
python3 fetch_hacs_files.py --dry-run
```

Files are written to `app-shack/rootfs/app/shim/ha_fetched/` and `app-shack/rootfs/app/shim/hacs_fetched/` with necessary import path adjustments.

## Build Commands

### Rust Implementation (app-dasher-rust/)

```bash
# Build the project
cd app-dasher-rust && cargo build

# Build for release (optimized)
cd app-dasher-rust && cargo build --release

# Run locally
cd app-dasher-rust && cargo run

# Run with specific log level
RUST_LOG=debug cargo run

# Check for errors without building
cd app-dasher-rust && cargo check

# Format code
cd app-dasher-rust && cargo fmt

# Run linter
cd app-dasher-rust && cargo clippy

# Run tests
cd app-dasher-rust && cargo test
```

### Python Implementation (app-dasher/) - Legacy

```bash
# Install dependencies locally (for IDE support)
pip3 install -r app-dasher/rootfs/app/requirements.txt

# Run the proxy locally for development
cd app-dasher/rootfs/app && python3 dasher.py
```

## Testing

### Test Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: When modifying existing code, add or update tests to cover the changes
- **Goal**: Maintain test coverage for critical paths and edge cases

### Rust

```bash
# Run all tests
cargo test

# Run tests with output
cargo test -- --nocapture

# Run specific test
cargo test test_name
```

### Python (app-shack/)

```bash
# Install test dependencies
pip3 install -r app-shack/rootfs/app/requirements-dev.txt

# Run unit tests only (fast)
cd app-shack/rootfs/app && python3 -m pytest tests/ -v -m "not integration"

# Run all tests including integration tests
cd app-shack/rootfs/app && python3 -m pytest tests/ -v

# Run integration tests only (requires integrations to be installed)
cd app-shack/rootfs/app && python3 -m pytest tests/ -v -m integration

# Run specific integration test
cd app-shack/rootfs/app && python3 -m pytest tests/test_integrations.py::test_flightradar24_setup -v

# Run tests with coverage
cd app-shack/rootfs/app && python3 -m pytest tests/ -v --cov=. --cov-report=term-missing

# Run specific test file
cd app-shack/rootfs/app && python3 -m pytest tests/test_mqtt_bridge.py -v

# Run specific test
cd app-shack/rootfs/app && python3 -m pytest tests/test_entity_utils.py::TestGetMqttEntityId -v
```

**Note**: The legacy Python implementation (app-dasher/) has no test suite configured.

## Linting

### Rust

```bash
# Format code
cargo fmt

# Run clippy (linter)
cargo clippy -- -D warnings

# Check formatting without applying
cargo fmt -- --check
```

### Python (Legacy)

```bash
# Install linting tools
pip3 install flake8 black isort mypy

# Run flake8
flake8 app-dasher/rootfs/app/dasher.py

# Format with black
black app-dasher/rootfs/app/dasher.py

# Sort imports
isort app-dasher/rootfs/app/dasher.py

# Type check
mypy app-dasher/rootfs/app/dasher.py
```

## Code Style Guidelines

### Rust Style

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use double quotes for strings
- **Documentation**: Use `///` for public items, `//` for internal comments
- **Type hints**: Use Rust's strong type system; prefer explicit types in public APIs
- **Error handling**: Use `anyhow` for application errors, `thiserror` for library errors

### Rust Naming Conventions

- **Variables**: `snake_case` (e.g., `client_entities`, `ha_host`)
- **Constants**: `SCREAMING_SNAKE_CASE` (e.g., `MAX_CONNECTIONS`)
- **Functions**: `snake_case` (e.g., `proxy_handler`, `get_client_ip`)
- **Structs/Enums**: `PascalCase` (e.g., `AppState`, `ClientInfo`)
- **Traits**: `PascalCase` (e.g., `EntityFilter`)
- **Private items**: No prefix, use `pub(crate)` or omit `pub`

### Python Style (Legacy)

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use single quotes for strings unless double quotes are needed
- **Docstrings**: Use triple quotes for function/class documentation
- **Type hints**: Not currently used but encouraged for new code

### Python Naming Conventions (Legacy)

- **Variables**: `snake_case` (e.g., `client_entities`, `ha_host`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `HA_HOST`, `PROXY_PORT`)
- **Functions**: `snake_case` (e.g., `proxy_handler`, `get_client_ip`)
- **Classes**: `PascalCase`
- **Private functions**: `_leading_underscore` (e.g., `_check_attribute_match`)

## Error Handling

### Rust

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

### Python (Legacy)

Use try-except blocks for I/O operations:

```python
try:
    with open(CONFIG_FILE_PATH, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error("Configuration file not found: /data/options.json")
```

## Logging

### Rust

Use the `tracing` crate with appropriate levels:

```rust
use tracing::{debug, info, warn, error};

debug!("Detailed debugging info");
info!("General information");
warn!("Warning messages");
error!("Error messages: {}", error_details);
```

### Python (Legacy)

Use the pre-configured colorlog logger:

```python
logger.debug("Detailed debugging info")
logger.info("General information")
logger.warning("Warning messages")
logger.error("Error messages with exception info")
```

## Async Patterns

### Rust

Use `tokio` for async runtime:

```rust
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let listener = TcpListener::bind("0.0.0.0:8125").await?;
    // ...
}
```

### Python (Legacy)

```python
async def proxy_handler(request):
    # Use await for async operations
    return await proxy_websocket_filtered(request, client_ip)
```

## WebSocket Message Processing

### Rust

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

### Python (Legacy)

Messages are JSON-encoded. Handle both single messages and message arrays:

```python
messages_to_process = data if isinstance(data, list) else [data]
```

## Home Assistant Integration

- WebSocket protocol follows Home Assistant's format
- Entity IDs follow pattern: `domain.object_id` (e.g., `light.kitchen`)
- Supports wildcard patterns (e.g., `light.kitchen_*`)
- Supports regex filters: `/pattern/`

## MQTT Topic Naming (app-shack/)

MQTT topics used for Home Assistant discovery must follow specific naming conventions:

### Topic Structure

Topics follow the pattern: `homeassistant/<platform>/<entity_id>/<suffix>`

Examples:
- Discovery config: `homeassistant/sensor/living-room-temperature/config`
- State topic: `homeassistant/sensor/living-room-temperature/state`
- Command topic: `homeassistant/switch/living-room/set`

### Naming Rules

1. **Use dashes (`-`) not underscores (`_`)** in MQTT topic names
   - Entity ID: `sensor.living_room` (Home Assistant format)
   - MQTT topic: `living-room` (MQTT format)

2. **Conversion function**: Use `get_mqtt_entity_id()` from `shim/entity.py`:
   ```python
   from shim.entity import get_mqtt_entity_id
   
   entity_id = "fan.living_room"
   mqtt_id = get_mqtt_entity_id(entity_id)  # Returns: "living-room"
   topic = f"homeassistant/fan/{mqtt_id}/state"
   ```

3. **Reverse conversion**: When receiving commands from HA, convert dashes back to underscores:
   ```python
   # MQTT topic: homeassistant/fan/living-room/set
   parts = topic.split("/")
   object_id = parts[2].replace("-", "_")  # "living-room" -> "living_room"
   entity_id = f"{parts[1]}.{object_id}"   # "fan.living_room"
   ```

### Why Dashes?

- MQTT topic naming conventions recommend avoiding underscores in topic names
- Dashes are more readable and standard in MQTT ecosystems
- Home Assistant's MQTT discovery format expects this convention

## Docker/Container Guidelines

### Rust

- Base image: Multi-stage build with `rust:1.75` and `debian:bookworm-slim`
- Supports architectures: `aarch64`, `amd64`
- Binary built statically for minimal image size
- Configuration mounted at `/data/options.json` in container

### Python (Legacy)

- Base image: `ghcr.io/hassio-addons/base-python:stable`
- Supports architectures: `aarch64`, `amd64`
- Service managed by S6 overlay
- Configuration mounted at `/data/options.json` in container

## Git Workflow

```bash
# Stage changes for Rust implementation
git add app-dasher-rust/

# Stage changes for Python implementation
git add app-dasher/

# Stage changes for Shack
git add app-shack/

# Commit with descriptive message
git commit -m "feat: add support for custom filter rules"

# No push - user will handle deployment
```

## Common Tasks

### Rust

**Add a New Dependency**: Edit `app-dasher-rust/Cargo.toml`, add to `[dependencies]`, then run `cargo build`.

**Update App Version**: Edit `app-dasher-rust/config.yaml`, increment `version`, commit and tag.

**Local Testing**: Copy `app-dasher-rust/proxy-config.yaml`, modify for environment, run `cd app-dasher-rust && cargo run`, access at `http://localhost:8125`.

**Build Docker Image**: Run `cd app-dasher-rust && ./build.sh` (requires Docker).

### Python (Legacy)

**Add a New Dependency**: Add to `app-dasher/rootfs/app/requirements.txt`, then rebuild Docker image.

**Update App Version**: Edit `app-dasher/config.yaml`, increment `version`, commit and tag.

**Local Testing**: Copy `app-dasher/proxy-config.yaml`, modify for environment, run `cd app-dasher/rootfs/app && python3 dasher.py`, access at `http://localhost:8124`.

### Shack (app-shack/)

**Working Directory**: All commands must be run from `app-shack/rootfs/app/` (not the repo root).

**Local Testing with Virtual Environment**:
```bash
# Create and activate virtual environment in app directory
cd app-shack/rootfs/app
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python3 main.py

# Run tests
python3 -m pytest tests/ -v
```

**Important Notes**:
- The `.venv` directory is local to `app-shack/rootfs/app/` (not at repo root)
- Always activate the venv before running or testing
- Many imports fail without the venv due to missing dependencies (fastapi, paho-mqtt, etc.)

**Add a New Dependency**: Edit `app-shack/rootfs/app/requirements.txt`, run `pip install -r requirements.txt` in the venv.
