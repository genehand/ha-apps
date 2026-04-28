# Integration Reloads

Each enabled integration includes a **reload button** that allows reloading the integration from Home Assistant.  This is similar to the Reload option that HA provides for devices.

## How It Works

When an integration is successfully set up via `IntegrationLoader.setup_integration()`, a `ButtonEntity` with `device_class="restart"` is automatically created and registered as an MQTT discovery entity.

### Button Entity Properties

| Property | Value |
|----------|-------|
| Platform | `button` |
| Device class | `restart` |
| Entity category | `config` (hidden from main UI, visible in config section) |
| Name | `Reload {Integration Name}` |
| Unique ID | `reload_{entry.entry_id}` |

The button appears on the same device as the integration's other entities (if available), or on a Shack-managed device as a fallback.

### MQTT Discovery

The reload button is published to MQTT like any other entity:

- **Command topic**: `homeassistant/button/{mqtt-entity-id}/set`
- **Payload**: `PRESS` triggers the reload

### What Happens on Press

Pressing the reload button calls `IntegrationLoader.reload_config_entry()`, which:

1. **Unloads the integration** (`unload_integration`) — preserves MQTT topics (`cleanup_mqtt=False`), removes entities from the state machine, shuts down coordinators
2. **Re-loads the integration** (`setup_integration`) — re-imports the module, calls `async_setup` + `async_setup_entry`, re-creates all entities with fresh MQTT discovery configs
3. **Re-creates the reload button** — the button itself is re-created as part of the setup

### MQTT Behavior

- The **MQTT connection is NOT disconnected** during a reload — only the entities for that integration are re-published
- Old discovery configs are overwritten by new ones (same MQTT topics, retained messages are replaced)
- No birth/will messages are sent — the MQTT bridge remains connected

## Accessing the Reload Button

### From Home Assistant

The reload button appears in HA as a standard MQTT button entity:

1. Go to **Settings → Devices & services → Devices**
2. Find the device for your integration (or the Shack device for fallback)
3. Look for a button named `Reload {Integration Name}` under the **Configuration** section
4. Press the button to trigger a reload

### From the Shack Web UI

The web UI also has a reload endpoint at `POST /config/{entry_id}/reload` (see `shim/web/routes/integrations.py`).

## Code Reference

| Component | File |
|-----------|------|
| Button entity class | `shim/integrations/loader.py` — `_create_reload_button()` |
| Button registration | `shim/integrations/loader.py` — `_register_internal_entity()` |
| Reload logic | `shim/integrations/loader.py` — `reload_config_entry()` |
| ConfigEntries delegation | `shim/registries.py` — `async_reload()` |
| Button platform | `shim/platforms/button.py` |

## Lifecycle

- **Created**: After `setup_integration()` succeeds
- **Cleaned up**: During `unload_integration()` — removed along with all other entities for the domain
- **Re-created**: After `reload_config_entry()` completes setup

## Comparison: HA's `homeassistant.reload_config_entry`

In real Home Assistant, calling `homeassistant.reload_config_entry` on the MQTT config entry:

1. Gracefully disconnects from the MQTT broker (no will message published)
2. Reconnects and publishes a birth message
3. Restores all subscriptions

In the shack, reloading is per-integration and the MQTT bridge stays connected throughout. Only the integration's entities are re-published.
