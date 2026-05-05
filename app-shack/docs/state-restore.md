# State Restoration

Entity state restoration allows entities to persist their last known state so that when the shim restarts (or an integration is reloaded), entities pick up where they left off instead of starting from defaults.

## Architecture

State restoration is built on three layers:

| Layer | File | Role |
|-------|------|------|
| **Storage** | `shim/storage.py` | JSON-file-backed persistence of entity states |
| **Mixin** | `shim/restore.py` | `RestoreEntity` mixin with `async_get_last_state()` / `_save_state_for_restore()` |
| **Platform hooks** | `shim/platforms/*.py` | Per-domain `async_write_ha_state()` overrides that auto-save for `RestoreEntity` subclasses |

### Storage Layer

Entity states are saved to `<shim_dir>/entity_states.json`. Each entry stores:

```json
{
  "sensor.temperature": {
    "state": "22.5",
    "attributes": { "unit_of_measurement": "°C" },
    "extra_data": { ... },
    "last_updated": "2026-05-05T10:30:00"
  }
}
```

- **`state`** — The string value of the entity (e.g. `"ON"`, `"22.5"`, `"LAX"`)
- **`attributes`** — Optional dict of state attributes (e.g. `unit_of_measurement`)
- **`extra_data`** — Optional domain-specific extra data (used for restoring numeric min/max/step in `RestoreNumber`)
- **`last_updated`** — ISO-8601 timestamp of when the state was saved

### RestoreEntity Mixin

The shared `RestoreEntity` mixin in `shim/restore.py` provides:

| Method / Property | Purpose |
|-------------------|---------|
| `async_get_last_state()` | Returns a `State`-like object (`entity_id`, `state`, `attributes`) from storage, or `None` |
| `async_get_last_extra_data()` | Returns `RestoredExtraData` from storage, or `None` |
| `extra_restore_state_data` | Property returning domain-specific `ExtraStoredData` (overridden by subclasses like `RestoreNumber`) |
| `_save_state_for_restore()` | Persists the current entity state to storage |

### Platform Hooks

Each platform overrides `async_write_ha_state()` to detect whether the entity inherits from `RestoreEntity` and, if so, automatically saves state:

```python
def async_write_ha_state(self) -> None:
    """Write state to the state machine and save for restoration."""
    super().async_write_ha_state()
    if isinstance(self, RestoreEntity):
        self._save_state_for_restore()
```

This ensures that every call to write HA state (whether from the entity itself, coordinator updates, or MQTT state changes) also persists the state for restoration.

## Supported Platforms

| Platform | Restore Class | Inherits From | Extra Data |
|----------|---------------|---------------|------------|
| **Sensor** | `RestoreSensor` | `RestoreEntity` | `native_value` + `native_unit_of_measurement` (via `_save_state_for_restore` override) |
| **Text** | `RestoreEntity` (imported) | — | None |
| **Switch** | `RestoreEntity` (imported) | — | None |
| **Number** | `RestoreNumber` | `NumberEntity` + `RestoreEntity` | `native_value`, `native_min_value`, `native_max_value`, `native_step`, `native_unit_of_measurement` |

## Comparison: HA's Approach

In real Home Assistant, state restoration is managed by a central `RestoreStateData` service (`homeassistant/helpers/restore_state.py`):

| Aspect | Home Assistant | Shim |
|--------|---------------|------|
| **Storage** | Core `Store` with periodic dumps (every 15 min) + on-stop dump | Per-entity save on each `async_write_ha_state()` |
| **Registration** | Auto-registered via `async_internal_added_to_hass()` | Opt-in via `RestoreEntity` mixin + `async_write_ha_state` hook |
| **Extra data** | `extra_restore_state_data` property → `Store` | Same property, saved inline in the entity state entry |
| **RestoreX** | `RestoreSensor(SensorEntity, RestoreEntity)`, `RestoreNumber(NumberEntity, RestoreEntity)` | Identical pattern |

The shim trades central batch-saving for simplicity: every state write is persisted immediately. This avoids the complexity of a periodic dump scheduler while achieving the same practical result.

## Code Reference

| Component | File |
|-----------|------|
| Shared `RestoreEntity` mixin | `shim/restore.py` |
| State persistence | `shim/storage.py` — `save_entity_state()` / `load_entity_state()` |
| Sensor restore | `shim/platforms/sensor.py` — `RestoreSensor` |
| Number restore | `shim/platforms/number.py` — `RestoreNumber` |
| Switch restore | `shim/platforms/switch.py` — `async_write_ha_state()` hook |
| Text restore | `shim/platforms/text.py` — `async_write_ha_state()` hook |
| HA helper stub | `shim/stubs/helpers.py` — registers `homeassistant.helpers.restore_state.RestoreEntity` |
| Tests | `tests/test_restore_entity.py` |
