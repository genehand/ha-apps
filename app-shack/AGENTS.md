# AGENTS.md - Coding Guidelines for HACS Shack

Home Assistant compatibility layer for running integrations outside of HA.

## Overview

HACS Shack provides a compatibility layer that allows Home Assistant custom integrations to run outside of the HA core environment, enabling standalone MQTT discovery and control.

## Working Directory

**Always run commands from this directory.** Do not run from the repo root.

```bash
cd app-shack/rootfs/app/
```

## Upstream Code Reference

We maintain scripts to fetch code from upstream Home Assistant and HACS repositories for reference and reuse:

### Fetch Scripts

- `scripts/fetch_ha_files.py`: Downloads HA core files (const.py, exceptions.py, utils)
- `scripts/fetch_hacs_files.py`: Downloads HACS utility modules (version, validation, queue management)

### Integration Code Policy

**IMPORTANT: Never edit integration code directly.**

The files in `data/shim/custom_components/` are external integrations (Leviton, Dreo, etc.) that come from upstream sources. Do not modify them directly. Instead:

1. **For bugs in integrations**: Report them upstream or work around them in the shim code
2. **For missing features**: Implement them in the shim layer (`shim/`)
3. **For display/translations issues**: Handle them in the MQTT discovery layer

The shim layer (in `shim/`) is where we add compatibility code, bridges, and workarounds. That's the appropriate place to fix integration issues.

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
cd scripts && python3 fetch_ha_files.py

# Fetch latest HACS release files
cd scripts && python3 fetch_hacs_files.py

# Preview without downloading
python3 fetch_ha_files.py --dry-run
python3 fetch_hacs_files.py --dry-run
```

Files are written to `shim/ha_fetched/` and `shim/hacs_fetched/` with necessary import path adjustments.

## Build Commands

```bash
# Sync dependencies (install from lock file)
uv sync

# Run the application
uv run python3 main.py
```

**Important**: Always use `uv sync` and `uv run` to ensure consistent dependencies. The `.venv` is managed by uv.

## Testing

### Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: Add or update tests to cover changes
- **Bug fixes**: Include a test that would have caught the bug
- **Integration compatibility**: Test with real integration patterns (flightradar24, dreo, leviton, etc.)

### Running Tests

**Important:** Always run tests from the app directory:

```bash
cd app-shack/rootfs/app
```

```bash
# Unit tests only (fast)
uv run pytest tests/ -v -m "not integration"

# All tests including integration tests
uv run pytest tests/ -v

# Integration tests only (requires integrations to be installed)
uv run pytest tests/ -v -m integration

# With coverage
uv run pytest tests/ -v --cov=. --cov-report=term-missing

# Specific test file or pattern
uv run pytest tests/test_mqtt_bridge.py -v
uv run pytest tests/test_entity_utils.py::TestGetMqttEntityId -v
```

### Key Integration Patterns to Test

When modifying `EntityDescription` or related classes, verify these patterns work:

1. `@dataclass` (non-frozen) - Flightradar24/Dreo style
2. `@dataclass(frozen=True)` - Leviton style
3. Custom methods in descriptions (like Dreo's `__repr__`)
4. Multiple inheritance with mixins
5. Import patch stub behavior when `homeassistant.components.X` is used

## Code Style Guidelines

### Style

- **Indentation**: 4 spaces (no tabs)
- **Line length**: 100 characters maximum
- **Quotes**: Use single quotes for strings unless double quotes are needed
- **Docstrings**: Use triple quotes for function/class documentation
- **Type hints**: Encouraged for new code

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

## MQTT Topic Naming

MQTT topics used for Home Assistant discovery must follow specific naming conventions:

### Topic Structure

Topics follow the pattern: `homeassistant/<platform>/<entity_id>/<suffix>`

Examples:
- Discovery config: `homeassistant/sensor/living-room-temperature/config`
- State topic: `homeassistant/sensor/living-room-temperature/state`
- Command topic: `homeassistant/switch/living-room/set`

### Naming Rules

1. **Use dashes (`-`) not underscores (`_`) or colons (`:`)** in MQTT topic names
   - Entity ID: `sensor.living_room` (Home Assistant format)
   - MQTT topic: `living-room` (MQTT format)
   - Colons from some integrations (e.g., `device:001:section`) also convert to dashes: `device-001-section`
   - *Why?* Home Assistant doesn't handle colons well in MQTT discovery. Dashes are safe while colons can cause issues. The Entity class has an `mqtt_object_id` property that handles this automatically.

2. **Conversion function**: Use `get_mqtt_entity_id()` from `shim/entity.py`:

   ```python
   from shim.entity import get_mqtt_entity_id
   
   entity_id = "fan.living_room"
   mqtt_id = get_mqtt_entity_id(entity_id)  # Returns: "living-room"
   topic = f"homeassistant/fan/{mqtt_id}/state"

   # Also handles colons from integrations like Dreo
   entity_id = "fan.device:001:section"
   mqtt_id = get_mqtt_entity_id(entity_id)  # Returns: "device-001-section"
   ```

3. **Reverse conversion**: When receiving commands from HA, convert dashes back to underscores:

   ```python
   # MQTT topic: homeassistant/fan/living-room/set
   parts = topic.split("/")
   object_id = parts[2].replace("-", "_")  # "living-room" -> "living_room"
   entity_id = f"{parts[1]}.{object_id}"   # "fan.living_room"
   ```

**Why dashes?** MQTT topic naming conventions recommend avoiding underscores in topic names. Dashes are more readable and standard in MQTT ecosystems. Home Assistant's MQTT discovery format expects this convention.

## Entity Descriptions and FrozenOrThawed

When creating `EntityDescription` subclasses for platform entities, use the `FrozenOrThawed` metaclass with `frozen_or_thawed=True`:

```python
from ..entity import EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed

class MyEntityDescription(EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True):
    """Description for my entity type."""

    custom_field: Optional[str] = None
```

**Why use FrozenOrThawed?**

- `EntityDescription` uses `FrozenOrThawed` metaclass to create frozen dataclasses internally
- This allows child classes to be either frozen or non-frozen via standard `@dataclass` decorator
- All platform EntityDescriptions should use `frozen_or_thawed=True` for consistency with Home Assistant
- External integrations can use either `@dataclass(frozen=True)` or `@dataclass` (non-frozen)

**For external integrations:**

Use standard `@dataclass` decorator with either frozen or non-frozen:

```python
from dataclasses import dataclass
from homeassistant.backports.entity import EntityDescription

# Non-frozen (default) - for maximum compatibility
@dataclass
class CustomEntityDescription(EntityDescription):
    """Custom entity description."""
    custom_field: str = "default"

# Or frozen
@dataclass(frozen=True)
class FrozenEntityDescription(EntityDescription):
    """Frozen entity description."""
    custom_field: str = "default"
```

**Note:** The `FrozenOrThawed` metaclass works around Python dataclass inheritance rules by creating frozen dataclasses internally while allowing subclasses to choose their frozen status via `@dataclass` decorator.

## Common Tasks

**Add a New Dependency**: Edit `pyproject.toml`, add to `dependencies` or `dev`, run `uv sync` to update the lock file.

**Update App Version** (`config.yaml` is source of truth):

```bash
# 1. Edit config.yaml and update version: X.Y.Z

# 2. Sync to pyproject.toml (for uv)
./sync-version.sh
```

**Version locations:**

- `config.yaml` → HA app version (source of truth)
- `pyproject.toml` → For uv dependency management

Keep them in sync for consistency.

**Local Testing**: Run `uv run python3 main.py` (uv will auto-sync dependencies).