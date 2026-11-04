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

### Unload Callbacks (`entry.async_on_unload`)

Step 1 also runs every callback registered with `entry.async_on_unload(...)`, matching
HA's `ConfigEntry.async_unload`. This is what actually tears an integration down:
integrations register socket closes and task cancels there rather than in
`async_unload_entry` (localtuya registers `dev.close`, which closes its Tuya TCP
connection and stops the reconnect loop).

Without it, a reload leaves the previous instance running next to the new one. For
localtuya that is two local clients for the same device; Tuya firmware serves one,
so it resets both and the coordinator logs
`Disconnected due to: [Errno 104] Connection reset by peer` every
`RECONNECT_INTERVAL` (5 s) forever.

Callbacks run LIFO, awaitables are awaited (the reload does not complete until they
finish), errors are logged and swallowed, and the list is cleared afterwards so the
next setup registers a fresh generation.

### Module generations (multi-entry domains)

Unloading the **last** entry of a domain is what discards the generation: the module
is purged from `sys.modules` *and* `hass.data[domain]` is dropped (see
`IntegrationLoader.reset_domain` / `_drop_domain_data`). Both go together, because a
fresh import with the previous `hass.data` value leaves the new classes holding
instances the old ones built. That is not hypothetical — it is the meross_lan update
bug:

```
ERROR: shim.integrations.loader - Error setting up entry Meross LAN for meross_lan:
       'Device' object has no attribute 'profile'
  helpers/mqtt_profile.py, line 623, in link
    assert (device.id not in self.linkeddevices) and (device.profile is not self)
```

meross_lan keeps a `ComponentApi` in `hass.data[DOMAIN]` and only removes it in its
`EVENT_HOMEASSISTANT_STOP` listener. Home Assistant never re-imports an integration
in-process, so the singleton is always the same generation there; the shack does, so
6.0.0's `Device.profile` was read off a `Device` that 5.8.0 had built with `_profile`.

While any entry of the domain is still set up, the module and the singleton stay: HA
reloads one entry at a time, and the survivors keep running that generation. Two
consequences for the unload path:

- `unload_integration` is **per entry**, not per domain (`_domain_has_loaded_entries`
  decides what may be discarded). Doing it per domain skipped every later entry's
  `async_unload_entry`, so the next setup logged `device already initialized`.
- `_remove_domain_entities` is filtered by `_attr_config_entry_id` in that case: the
  sibling entries are not re-set up, so deleting their entities would not restore
  them.

`ShimManager.install_and_reload` calls `reset_domain()` after installing the new
files and before re-setup, so the update path is deterministic even when an unload
failed or the domain never reached `loaded`.

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
