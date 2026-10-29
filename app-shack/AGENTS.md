# AGENTS.md - Coding Guidelines for HACS Shack

Home Assistant compatibility layer for running integrations outside of HA.

This file holds what you need *before* you touch anything: the layout, the
invariants, and the commands. Deep dives live in [`docs/`](docs/) — read the one for
the area you are changing.

## Documentation map

| Doc | Read it when you are… |
|-----|----------------------|
| [`upstream-tier.md`](docs/upstream-tier.md) | importing/stubbing `homeassistant.*`, bumping the pin, wondering why a module is not lifted from the wheel |
| [`image-size.md`](docs/image-size.md) | changing `KEEP_TOP_LEVEL`/`KEEP_IN_HELPERS`, pruning, or booting the app end-to-end |
| [`stub-modules.md`](docs/stub-modules.md) | adding or editing a stub in `shim/stubs/` |
| [`probatio.md`](docs/probatio.md) | touching config validation or `vol.Required`/`vol.UNDEFINED` checks — and before adding `voluptuous` to `pyproject.toml`, which is forbidden |
| [`oauth.md`](docs/oauth.md) | OAuth2 flows, PKCE, JWT |
| [`integration-requirements.md`](docs/integration-requirements.md) | runtime `pip install` of integration requirements |
| [`integration-reloads.md`](docs/integration-reloads.md) | the per-integration reload button |
| [`device-registry.md`](docs/device-registry.md) | entities → `DeviceEntry` rows, `device_info`, `via_device_id` |
| [`state-restore.md`](docs/state-restore.md) | `RestoreEntity` |
| [`entity-enable-disable.md`](docs/entity-enable-disable.md) | entity enable/disable and polling control |
| [`entity-lifecycle.md`](docs/entity-lifecycle.md) | `async_prepare_to_add_to_hass`, `async_entity_id_changed` |
| [`entity-descriptions.md`](docs/entity-descriptions.md) | `EntityDescription` subclasses, `FrozenOrThawed` |
| [`config-entry-returns.md`](docs/config-entry-returns.md) | setup/unload/migration return values (`None` vs bool, `ConfigEntry*` exceptions) |
| [`config-flows.md`](docs/config-flows.md) | config flow step results (`step_id` fallback, progress-step gap) |
| [`blocking-calls.md`](docs/blocking-calls.md) | the blocking-call detector warns |
| [`async-patterns.md`](docs/async-patterns.md) | async I/O, `asyncio.to_thread`, aiohttp sessions against `github.com` |
| [`mqtt-topics.md`](docs/mqtt-topics.md) | publishing or parsing MQTT discovery topics |
| [`web-layer.md`](docs/web-layer.md) | adding a route or template to the FastAPI UI |
| [`web-ingress.md`](docs/web-ingress.md) | `HX-Redirect` and HA ingress paths |
| [`build.md`](docs/build.md) | building, `sync-versions.py`, `run.sh`, Docker, s6 layout |
| [`testing.md`](docs/testing.md) | writing tests, running the suite, integration-compat patterns |
| [`code-style.md`](docs/code-style.md) | style, naming, error handling, logging |

## Shim Architecture

The compatibility layer is organized into modular components:

```
shim/
├── import_patch.py      # Orchestrates import patching; defines the upstream tier
├── block_async_io.py    # Warn-only blocking call detection on the event loop
├── stubs/               # Stubs for HA modules that need the model layer
│   ├── base.py          # Shared utilities (make_module, simple_method)
│   ├── coordinator.py   # DataUpdateCoordinator, UpdateFailed
│   ├── helpers.py       # device_registry, config_validation, selector, etc.
│   ├── components.py    # alarm_control_panel, cover, mqtt, image, etc.
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
└── web/                 # FastAPI UI + config flow wizards
```

Everything else comes from the installed `homeassistant` wheel — see
[`docs/upstream-tier.md`](docs/upstream-tier.md).

### Import patterns

For all code (internal and integrations), use `shim.core`:

- `from shim.core import HomeAssistant, ConfigEntry, State, callback` ✓
- `from shim.core import StateMachine, ConfigEntries`
- `from shim.core import MockConfig`

For integrations (they import from `homeassistant.core` which is patched to `shim.core`):

- `from homeassistant.core import HomeAssistant, ConfigEntry, callback` ✓ (patched to `shim.core`)

**We dogfood `shim.core`** - all internal code (tests, scripts) imports from `shim.core` rather than the split modules directly. This ensures the re-export layer is always tested and working for integrations.

## Working Directory

**CRITICAL: Always run commands from `app-shack/rootfs/app/`.** The tool calls include `cd app-shack/rootfs/app &&` for a reason - many Python imports and relative paths break if you run from the repo root.

```bash
cd app-shack/rootfs/app/
```

**When using the Bash tool:** Either use the `workdir` parameter or include the `cd` in the command. The examples throughout this file include the proper `cd` prefix.

**Exception:** `scripts/prune_homeassistant.py` is called with an explicit path (it
operates on a site-packages directory), so it works from anywhere.

## Upstream Home Assistant code (imported, not vendored)

Home Assistant is a dependency: `homeassistant==<pin>` in `rootfs/app/pyproject.toml`.
The shim imports the modules HA keeps free of its model layer **verbatim** and
re-implements only the rest. The wheel is the source of truth, `uv.lock` is the pin.

Two rules follow, and they are the ones people get wrong:

- **Do not copy upstream code into the shim.** Check the tier before writing a stub.
- **Do not grow the stub layer when the wheel module already works here.** A stub
  that quietly diverges from upstream is the bug class this repo is deleting.

→ [`docs/upstream-tier.md`](docs/upstream-tier.md) for the tier table, how to adopt a
module (`scripts/scan_upstream.py`), known blockers, the 2026.11 status, and pin bump
policy.

## Integration Code Policy

**IMPORTANT: Never edit integration code directly.**

The files in `data/shim/custom_components/` are external integrations (Leviton, Dreo, etc.) that come from upstream sources. Do not modify them directly. Instead:

1. **For bugs in integrations**: Report them upstream or work around them in the shim code
2. **For missing features**: Implement them in the shim layer (`shim/`)
3. **For display/translations issues**: Handle them in the MQTT discovery layer

The shim layer (in `shim/`) is where we add compatibility code, bridges, and workarounds. That's the appropriate place to fix integration issues.

## Build & Run

```bash
# Sync dependencies (install from lock file)
cd app-shack/rootfs/app && uv sync

# Release: bumps config.yaml -> pyproject.toml -> integration versions, then syncs
cd app-shack && ./sync-versions.py

# Run it the way it deploys: throwaway venv, pruned, then main.py
cd app-shack && ./run.sh

# Run the application against the project venv as-is
cd app-shack/rootfs/app && uv run python3 main.py
```

**Always use `uv sync` and `uv run`** so dependencies match the lock. `uv sync`
silently prunes packages not declared in the project, so declare anything a test
needs (the dev group owns those) instead of installing it ad hoc.

→ [`docs/build.md`](docs/build.md) for `run.sh`/Docker/s6 details and
[`docs/image-size.md`](docs/image-size.md) for pruning and the keep-list.

## Testing

```bash
# Unit tests only (fast, this is the default loop)
cd app-shack/rootfs/app && uv run pytest tests/ -v -m "not integration"

# Everything, including integration tests
cd app-shack/rootfs/app && uv run pytest tests/ -v
```

**New code must come with tests; bug fixes must come with a test that would have
caught the bug.** Changes to the upstream tier or the keep-list also need the
pruned-image check.

→ [`docs/testing.md`](docs/testing.md) for coverage runs, integration-compat
patterns, and stub-module testing.

## Entity Registry and Config Entry Tracking

The `EntityRegistry` tracks entities by config entry ID:

- Each entity registered during `async_setup_entry` gets `_attr_config_entry_id` set to the current entry's ID
- `EntityRegistry` maintains `_entries_by_config_entry: Dict[str, List[RegistryEntry]]` for lookups
- `RegistryEntry` entity registry entries (compatible with HA's entity registry format) track `entity_id`, `unique_id`, `config_entry_id`, and `disabled` status
- Cleanup on unregister: when an entity is unregistered, it's removed from both the flat `_entities` dict and the config-entry-bucketed `_entries_by_config_entry` dict

## Code conventions

4-space indent, 100 columns, single quotes, `snake_case`/`PascalCase`,
`UPPER_SNAKE_CASE` for constants, try-except around I/O, colorlog `logger`.

→ [`docs/code-style.md`](docs/code-style.md) for the full list and examples.
