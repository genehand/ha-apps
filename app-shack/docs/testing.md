# Testing

## Requirements

- **New code**: All new functionality must include unit tests
- **Modified code**: Add or update tests to cover changes
- **Bug fixes**: Include a test that would have caught the bug
- **Integration compatibility**: Test with real integration patterns (flightradar24, dreo, leviton, etc.)

## Running Tests

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

The suite must also pass against a *pruned* package — see the pruned-image check in
[`image-size.md`](image-size.md). That is the check that catches a keep-list that no
longer matches `UPSTREAM_*`.

## Key Integration Patterns to Test

When modifying `EntityDescription` or related classes, verify these patterns work:

1. `@dataclass` (non-frozen) - Flightradar24/Dreo style
2. `@dataclass(frozen=True)` - Leviton style
3. Custom methods in descriptions (like Dreo's `__repr__`)
4. Multiple inheritance with mixins
5. Import patch stub behavior when `homeassistant.components.X` is used

## Testing Stub Modules

When adding or modifying stub modules (especially `stubs/coordinator.py`, `stubs/helpers.py`):

- Test with real integrations that use the module (e.g., Moonraker for `DataUpdateCoordinator`)
- Verify all exported functions and classes are accessible
- Check that constants and enums match HA's values exactly
- Run the full test suite: `uv run pytest tests/ -v -m "not integration"`

## Testing the upstream tier

`tests/test_upstream_modules.py` measures the module closure in a fresh subprocess and
asserts the keep-list; it also asserts the `HassJobType` identity between the wheel
and `shim.core`. `scripts/scan_upstream.py <module>` is the admission test for
*candidate* modules — see
[`upstream-tier.md`](upstream-tier.md#adding-a-module-to-the-upstream-tier).

A test that passes locally but fails in CI is usually a missing dev-group
declaration: `uv sync` silently prunes packages, which is how
`protobuf`/`googleapis-common-protos` once disappeared (see [`build.md`](build.md)).
