# AGENTS.md - Coding Guidelines for HA Dasher

Home Assistant add-on that filters WebSocket updates for dashboard entities.

## Build Commands

This project uses Home Assistant's build system. No traditional build commands available.

### Local Development

```bash
# Install dependencies locally (for IDE support)
pip3 install -r ha-dasher/rootfs/app/requirements.txt

# Run the proxy locally for development
cd ha-dasher/rootfs/app && python3 dasher.py
```

### Testing

No test suite is currently configured. Tests could be added using pytest.

### Linting (Recommended Setup)

```bash
# Install linting tools
pip3 install flake8 black isort mypy

# Run flake8
flake8 ha-dasher/rootfs/app/dasher.py

# Format with black
black ha-dasher/rootfs/app/dasher.py

# Sort imports
isort ha-dasher/rootfs/app/dasher.py

# Type check
mypy ha-dasher/rootfs/app/dasher.py
```

## Code Style Guidelines

### Python Style

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use single quotes for strings unless double quotes are needed
- **Docstrings**: Use triple quotes for function/class documentation
- **Type hints**: Not currently used but encouraged for new code

### Naming Conventions

- **Variables**: `snake_case` (e.g., `client_entities`, `ha_host`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `HA_HOST`, `PROXY_PORT`)
- **Functions**: `snake_case` (e.g., `proxy_handler`, `get_client_ip`)
- **Classes**: `PascalCase` (none currently used)
- **Private functions**: `_leading_underscore` (e.g., `_check_attribute_match`)

### Global State

Global dictionaries are used to track per-client state:

```python
SUBSCRIBE_ENTITIES_IDS = {}   # Maps WebSocket to entity subscription IDs
CLIENT_ALL_STATES = {}        # Stores all states for rule resolution
LOVELACE_CONFIG_IDS = {}      # Tracks Lovelace config request IDs
CLIENT_LOVELACE_ENTITIES = {} # Per-client entity sets
CLIENT_FILTER_RULES = {}      # Auto-entities filter rules per client
```

### Error Handling

Use try-except blocks for I/O operations and provide informative error messages:

```python
try:
    with open(CONFIG_FILE_PATH, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error("Configuration file not found: /data/options.json")
```

### Logging

Use the pre-configured colorlog logger:

```python
logger.debug("Detailed debugging info")
logger.info("General information")
logger.warning("Warning messages")
logger.error("Error messages with exception info")
```

### Async/Await Patterns

All I/O operations should be async:

```python
async def proxy_handler(request):
    # Use await for async operations
    return await proxy_websocket_filtered(request, client_ip)
```

### WebSocket Message Processing

Messages are JSON-encoded. Handle both single messages and message arrays:

```python
messages_to_process = data if isinstance(data, list) else [data]
```

### Home Assistant Integration

- WebSocket protocol follows Home Assistant's format
- Entity IDs follow pattern: `domain.object_id` (e.g., `light.kitchen`)
- Supports wildcard patterns (e.g., `light.kitchen_*`)
- Supports regex filters: `/pattern/`

## Docker/Container Guidelines

- Base image: `ghcr.io/hassio-addons/base-python:stable`
- Supports architectures: `aarch64`, `amd64`
- Service managed by S6 overlay
- Configuration mounted at `/data/options.json` in container

## Git Workflow

```bash
# Stage changes
git add ha-dasher/

# Commit with descriptive message
git commit -m "feat: add support for custom filter rules"

# No push - user will handle deployment
```

## Common Tasks

**Add a New Dependency**: Add to `ha-dasher/rootfs/app/requirements.txt`, then rebuild Docker image.

**Update Add-on Version**: Edit `ha-dasher/config.yaml`, increment `version`, commit and tag.

**Local Testing**: Copy `proxy-config.yaml`, modify for environment, run `cd ha-dasher/rootfs/app && python3 dasher.py`, access at `http://localhost:8124`.
