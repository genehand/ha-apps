# AGENTS.md - Coding Guidelines for HACS Shack

Home Assistant compatibility layer for running integrations outside of HA.

## Overview

HACS Shack provides a compatibility layer that allows Home Assistant custom integrations to run outside of the HA core environment, enabling standalone MQTT discovery and control.

## Shim Architecture

The compatibility layer is organized into modular components:

```
shim/
├── import_patch.py      # Orchestrates import patching (~240 lines)
├── stubs/               # Home Assistant stub modules
│   ├── base.py          # Shared utilities (make_module, simple_method)
│   ├── coordinator.py   # DataUpdateCoordinator, UpdateFailed
│   ├── util.py          # dt, yaml, color, unit_conversion, percentage
│   ├── helpers.py       # device_registry, config_validation, storage, etc.
│   ├── components.py    # alarm_control_panel, cover, mqtt, image, etc.
│   ├── network.py       # Network utilities
│   ├── oauth2.py        # OAuth2 flow with PKCE, token refresh, JWT
│   └── application_credentials.py  # HA Application Credentials component
├── entity.py            # Entity base classes and EntityDescription
├── core.py              # Re-exports from split modules (for integration compatibility)
├── models.py            # Data classes: ConfigEntry, State, Event, ServiceCall, callback
├── registries.py        # StateMachine, ServiceRegistry, ConfigEntries, FlowManager
├── hass.py              # HomeAssistant core orchestrator
├── mocks.py             # MockConfig, MockUnitSystem, MockEventBus
├── platforms/           # Platform-specific entity implementations
│   ├── fan.py
│   ├── sensor.py
│   ├── switch.py
│   └── ...
├── ha_fetched/          # Upstream HA code (const.py, exceptions.py)
└── hacs_fetched/        # Upstream HACS utilities
```

> OAuth2 and Application Credentials details → [`docs/oauth.md`](docs/oauth.md)

### Import patterns

For all code (internal and integrations), use `shim.core`:

- `from shim.core import HomeAssistant, ConfigEntry, State, callback` ✓
- `from shim.core import StateMachine, ConfigEntries`
- `from shim.core import MockConfig`

For integrations (they import from `homeassistant.core` which is patched to `shim.core`):

- `from homeassistant.core import HomeAssistant, ConfigEntry, callback` ✓ (patched to `shim.core`)

**We dogfood `shim.core`** - all internal code (tests, scripts) imports from `shim.core` rather than the split modules directly. This ensures the re-export layer is always tested and working for integrations.

### Adding New Stub Modules

When adding support for new Home Assistant modules:

1. **Determine the namespace**: `homeassistant.util.X` → `stubs/util.py`, `homeassistant.helpers.X` → `stubs/helpers.py`
2. **Create the stub function**: Add a `create_X_stubs()` function that builds and registers the module
3. **Call from import_patch.py**: Add the function call in `ImportPatcher.patch()`
4. **Export from `stubs/__init__.py`**: Add the function to `__all__`
5. **Write tests**: Verify the stub works with real integration patterns

### Stub Module Guidelines

- Keep implementations minimal but compatible with HA's API
- Match function signatures exactly (including return types)
- Include all constants and enums that integrations might import
- Use `FrozenOrThawed` metaclass for EntityDescription classes
- Reference `shim/stubs/util.py` for percentage functions as an example of exact HA compatibility

## Working Directory

**CRITICAL: Always run commands from `app-shack/rootfs/app/`.** The tool calls include `cd app-shack/rootfs/app &&` for a reason - many Python imports and relative paths break if you run from the repo root.

```bash
cd app-shack/rootfs/app/
```

**When using the Bash tool:** Either use the `workdir` parameter or include the `cd` in the command. The examples throughout this file include the proper `cd` prefix.

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
cd app-shack/rootfs/app && python3 scripts/fetch_ha_files.py

# Fetch latest HACS release files
cd app-shack/rootfs/app && python3 scripts/fetch_hacs_files.py

# Preview without downloading
cd app-shack/rootfs/app && python3 scripts/fetch_ha_files.py --dry-run
cd app-shack/rootfs/app && python3 scripts/fetch_hacs_files.py --dry-run
```

Files are written to `shim/ha_fetched/` and `shim/hacs_fetched/` with necessary import path adjustments.

## Build Commands

```bash
# Sync dependencies (install from lock file)
cd app-shack/rootfs/app && uv sync

# Run the application
cd app-shack/rootfs/app && uv run python3 main.py
```

**Important**: Always use `uv sync` and `uv run` to ensure consistent dependencies. The `.venv` is managed by uv.

## Testing

### Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: Add or update tests to cover changes
- **Bug fixes**: Include a test that would have caught the bug
- **Integration compatibility**: Test with real integration patterns (flightradar24, dreo, leviton, etc.)

### Running Tests

```bash
# Unit tests only (fast)
cd app-shack/rootfs/app && uv run pytest tests/ -v -m "not integration"

# All tests including integration tests
cd app-shack/rootfs/app && uv run pytest tests/ -v

# Integration tests only (requires integrations to be installed)
cd app-shack/rootfs/app && uv run pytest tests/ -v -m integration

# With coverage
cd app-shack/rootfs/app && uv run pytest tests/ -v --cov=. --cov-report=term-missing

# Specific test file or pattern
cd app-shack/rootfs/app && uv run pytest tests/test_mqtt_bridge.py -v
cd app-shack/rootfs/app && uv run pytest tests/test_entity_utils.py::TestGetMqttEntityId -v
```

### Key Integration Patterns to Test

When modifying `EntityDescription` or related classes, verify these patterns work:

1. `@dataclass` (non-frozen) - Flightradar24/Dreo style
2. `@dataclass(frozen=True)` - Leviton style
3. Custom methods in descriptions (like Dreo's `__repr__`)
4. Multiple inheritance with mixins
5. Import patch stub behavior when `homeassistant.components.X` is used

### Testing Stub Modules

When adding or modifying stub modules (especially `stubs/coordinator.py`, `stubs/helpers.py`):

- Test with real integrations that use the module (e.g., Moonraker for `DataUpdateCoordinator`)
- Verify all exported functions and classes are accessible
- Check that constants and enums match HA's values exactly
- Run the full test suite: `uv run pytest tests/ -v -m "not integration"`

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

## Entity Registry and Config Entry Tracking

The `EntityRegistry` now tracks entities by config entry ID:

- Each entity registered during `async_setup_entry` gets `_attr_config_entry_id` set to the current entry's ID
- `EntityRegistry` maintains `_entries_by_config_entry: Dict[str, List[RegistryEntry]]` for lookups
- `RegistryEntry` entity registry entries (compatible with HA's entity registry format) track `entity_id`, `unique_id`, `config_entry_id`, and `disabled` status
- Cleanup on unregister: when an entity is unregistered, it's removed from both the flat `_entities` dict and the config-entry-bucketed `_entries_by_config_entry` dict

## Web Layer Architecture

The web UI (`shim/web/`) is built with FastAPI and Jinja2 templates using a modular route structure:

```
shim/web/
├── app.py              # WebUI class, FastAPI setup, route registration
├── const.py            # Web-specific constants
├── schema.py           # Pydantic models for API request/response validation
├── renderers.py        # Template rendering helpers
├── supervisor.py       # Supervisor API client wrapper
├── translations.py     # Translation string utilities
└── routes/             # Route handlers by domain
    ├── __init__.py
    ├── api.py          # REST API endpoints (states, services, events)
    ├── auth.py         # Authentication routes
    ├── config_flows.py # Config flow UI (wizard steps, forms)
    ├── credentials.py  # Application credentials management
    ├── fragments.py    # HTMX fragment endpoints
    └── integrations.py # Integration list, detail, enable/disable/remove
```

### Adding New Routes

When adding new web endpoints:

1. **Pick the right module**: Add to an existing route file by domain, or create a new one under `routes/`
2. **Use `register_routes(app, shim_manager, template_dir)`**: Each module exposes this function signature
3. **Register in `WebUI._register_routes()`**: Import and call the module's `register_routes` in `app.py`
4. **Keep `app.py` thin**: Only FastAPI setup and route registration belong in `app.py`

## Web UI and HA Ingress

The Web UI uses **relative paths** for all HTMX redirects to support both direct access and Home Assistant ingress:

### Path Rules

- **Always use relative paths** in `HX-Redirect` headers
- **Never use absolute paths** starting with `/`
- **Use the `_get_detail_redirect()` helper** for redirects to integration detail pages - it detects the source page from the Referer header and returns the appropriate path:
  - From index page: returns `./integrations/{domain}`
  - From detail page: returns `.` (current page)

### Examples

| Endpoint | Source Page | Redirect Path | Result |
|----------|-------------|---------------|--------|
| `/integrations/{domain}/enable` | Index | `./integrations/{domain}` | `/integrations/dreo` |
| `/integrations/{domain}/enable` | Detail | `.` | `/integrations/dreo` (stays) |
| `/integrations/{domain}/remove` | Detail | `..` | `/` (index) |

### Why Context-Aware Redirects?

HTMX resolves `HX-Redirect` relative to the **page URL where the request originated**, not the request URL. This means:

- From index `/`: `./integrations/{domain}` → `/integrations/{domain}` ✓
- From detail `/integrations/{domain}`: `./integrations/{domain}` → `/integrations/integrations/{domain}` ✗

The `_get_detail_redirect()` helper uses the `Referer` header to detect which page the request came from and returns the appropriate relative path.