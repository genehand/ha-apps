# AGENTS.md - Coding Guidelines for HA Dasher (Legacy Python)

Home Assistant WebSocket event proxy - legacy Python implementation.

## Overview

This is the legacy Python implementation of HA Dasher. Consider using the Rust implementation (app-dasher-rust) for new work.

## Working Directory

**Always run commands from this directory.** Do not run from the repo root.

```bash
cd app-dasher/rootfs/app/
```

## Build Commands

```bash
# Install dependencies locally (for IDE support)
pip3 install -r requirements.txt

# Run the proxy locally for development
python3 dasher.py
```

## Testing

**Note**: This legacy implementation has no test suite configured.

## Linting

```bash
# Install linting tools
pip3 install flake8 black isort mypy

# Run flake8
flake8 dasher.py

# Format with black
black dasher.py

# Sort imports
isort dasher.py

# Type check
mypy dasher.py
```

## Code Style Guidelines

### Style

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use single quotes for strings unless double quotes are needed
- **Docstrings**: Use triple quotes for function/class documentation
- **Type hints**: Not currently used but encouraged for new code

### Naming Conventions

- **Variables**: `snake_case` (e.g., `client_entities`, `ha_host`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `HA_HOST`, `PROXY_PORT`)
- **Functions**: `snake_case` (e.g., `proxy_handler`, `get_client_ip`)
- **Classes**: `PascalCase`
- **Private functions**: `_leading_underscore` (e.g., `_check_attribute_match`)

## Error Handling

Use try-except blocks for I/O operations:

```python
try:
    with open(CONFIG_FILE_PATH, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error("Configuration file not found: /data/options.json")
```

## Logging

Use the pre-configured colorlog logger:

```python
logger.debug("Detailed debugging info")
logger.info("General information")
logger.warning("Warning messages")
logger.error("Error messages with exception info")
```

## Async Patterns

```python
async def proxy_handler(request):
    # Use await for async operations
    return await proxy_websocket_filtered(request, client_ip)
```

## WebSocket Message Processing

Messages are JSON-encoded. Handle both single messages and message arrays:

```python
messages_to_process = data if isinstance(data, list) else [data]
```

## Home Assistant Integration

- WebSocket protocol follows Home Assistant's format
- Entity IDs follow pattern: `domain.object_id` (e.g., `light.kitchen`)
- Supports wildcard patterns (e.g., `light.kitchen_*`)
- Supports regex filters: `/pattern/`

## Docker/Container Guidelines

- Base image: `ghcr.io/hassio-addons/base-python:stable`
- Supports architectures: `aarch64`, `amd64`
- Service managed by S6 overlay
- Configuration mounted at `/data/options.json` in container

## Common Tasks

**Add a New Dependency**: Add to `requirements.txt`, then rebuild Docker image.

**Update App Version**: Edit `config.yaml`, increment `version`, commit and tag.

**Local Testing**: Copy `proxy-config.yaml`, modify for environment, run `python3 dasher.py`, access at `http://localhost:8124`.