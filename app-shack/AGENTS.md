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
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python3 main.py
```

**Important**: The `.venv` directory is local to this directory (not at repo root). Always activate the venv before running or testing. Many imports fail without the venv due to missing dependencies (fastapi, paho-mqtt, etc.).

## Testing

### Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: When modifying existing code, add or update tests to cover the changes
- **Goal**: Maintain test coverage for critical paths and edge cases

### Automated Testing Workflow

**All code changes must include corresponding tests. Never commit without running tests.**

#### Before Making Changes:
1. Identify what you're changing and why
2. Determine if existing tests cover this scenario
3. If modifying code that affects external integrations (like EntityDescription, MQTT, platform classes), add tests for ALL integration patterns

#### During Implementation:
1. Write or update tests FIRST or concurrently with the implementation
2. For EntityDescription/dataclass changes, test:
   - Base class behavior
   - All platform EntityDescription patterns
   - External integration patterns (frozen and non-frozen @dataclass)
   - Import patch stub behavior if affected
3. For MQTT changes, test:
   - Topic naming conventions
   - Entity ID conversions
   - State/command topic handling

#### After Implementation - Mandatory Test Checklist:

```bash
# 1. Run unit tests (fast)
python3 -m pytest tests/ -v -m "not integration"

# 2. Run all tests
python3 -m pytest tests/ -v

# 3. Run specific tests for what you changed
# Example for EntityDescription changes:
python3 -m pytest tests/test_dataclass_inheritance.py -v
python3 -m pytest tests/test_shim_additions.py::TestEntityDescriptionWorksWithIntegrations -v

# 4. Check test counts - should increase or stay same, never decrease
python3 -m pytest tests/ --tb=no -q
```

#### Test Coverage Requirements:
- **New functionality**: Must have comprehensive unit tests covering happy path and edge cases
- **Bug fixes**: Must have a test that would have caught the bug
- **Refactoring**: All existing tests must pass, add tests if coverage gaps exposed
- **External integration compatibility**: For any changes to integration interfaces, test with patterns from real integrations (flightradar24, dreo, leviton, etc.)

#### Red Flags - Stop and Add Tests:
- Changing core classes without tests
- Modifying platform descriptions without testing external integration patterns
- Any change to import patches without verifying stub behavior
- Tests failing or count decreasing
- "It should work" without test verification

#### Integration Test Patterns to Always Test:
When modifying EntityDescription or related classes, verify these patterns work:
1. `@dataclass` (non-frozen) on platform descriptions - Flightradar24/Dreo style
2. `@dataclass(frozen=True)` on platform descriptions - Leviton style
3. Custom methods in descriptions (like Dreo's `__repr__`)
4. Multiple inheritance with mixins
5. Import patch stub behavior when homeassistant.components.X is used

### Running Tests

```bash
# Install test dependencies
pip3 install -r requirements-dev.txt

# Run unit tests only (fast)
python3 -m pytest tests/ -v -m "not integration"

# Run all tests including integration tests
python3 -m pytest tests/ -v

# Run integration tests only (requires integrations to be installed)
python3 -m pytest tests/ -v -m integration

# Run specific integration test
python3 -m pytest tests/test_integrations.py::test_flightradar24_setup -v

# Run tests with coverage
python3 -m pytest tests/ -v --cov=. --cov-report=term-missing

# Run specific test file
python3 -m pytest tests/test_mqtt_bridge.py -v

# Run specific test
python3 -m pytest tests/test_entity_utils.py::TestGetMqttEntityId -v
```

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
   - Entity IDs with colons (e.g., from some integrations) also get converted: `device:001:section` → `device-001-section`

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

3. **Reverse conversion**: When receiving commands from HA, convert dashes back to underscores (for dots and underscores in entity IDs):

   ```python
   # MQTT topic: homeassistant/fan/living-room/set
   parts = topic.split("/")
   object_id = parts[2].replace("-", "_")  # "living-room" -> "living_room"
   entity_id = f"{parts[1]}.{object_id}"   # "fan.living_room"
   ```

4. **Why convert colons too?**

   - Home Assistant doesn't handle colons well in MQTT discovery topic names
   - Dashes are safe for MQTT topics while colons can cause issues with discovery
   - The Entity base class has an `mqtt_object_id` property that handles this conversion automatically

### Entity Descriptions and FrozenOrThawed

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
External integrations can use standard `@dataclass` decorator with either frozen or non-frozen:

1. **Using `@dataclass` (non-frozen) - for maximum compatibility:**

```python
from dataclasses import dataclass
from homeassistant.backports.entity import EntityDescription

@dataclass  # Non-frozen (default)
class CustomEntityDescription(EntityDescription):
    """Custom entity description."""
    custom_field: str = "default"
```

2. **Using `@dataclass(frozen=True)`:**

```python
from dataclasses import dataclass
from homeassistant.backports.entity import EntityDescription

@dataclass(frozen=True)
class CustomEntityDescription(EntityDescription):
    """Custom entity description."""
    custom_field: str = "default"
```

**Note:** The `FrozenOrThawed` metaclass works around Python dataclass inheritance rules by creating frozen dataclasses internally while allowing subclasses to choose their frozen status via `@dataclass` decorator.

### Why Dashes?

- MQTT topic naming conventions recommend avoiding underscores in topic names
- Dashes are more readable and standard in MQTT ecosystems
- Home Assistant's MQTT discovery format expects this convention

## Common Tasks

**Add a New Dependency**: Edit `requirements.txt`, run `pip install -r requirements.txt` in the venv.

**Update App Version**: Edit `config.yaml`, increment `version`, commit and tag.

**Local Testing**: Run `python3 main.py` (ensure venv is activated first).