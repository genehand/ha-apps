# Entity Enable/Disable & Polling Control

Some integrations ship certain sensors **disabled by default** and
decide at runtime which endpoints to poll based on the **entity registry's**
`disabled` flag:

- Smartcar's coordinator only adds an endpoint to its API batch when the
  matching registry entry is enabled (`not entity.disabled`), see
  `_batch_add_defaults` in the integration's `coordinator.py`.
- A disabled-by-default entity therefore never gets polled until it is
  *enabled* in the entity registry.

Home Assistant normally handles the enable toggle in its own local entity
registry — toggling it does **not** publish anything to MQTT. App-shack runs
outside HA, so it mirrors the relevant pieces of that registry and exposes
three mechanisms to control it: the **Web UI** (primary, persisted),
**MQTT enable/disable** (runtime, also persists), and **MQTT refresh**
(on-demand, single-endpoint).

## 1. Web UI toggle (primary, persisted across restarts)

Open the Shack web UI (HA **Settings → Add-ons → Shack → Open Web UI**),
navigate to an integration's detail page, and each entity in the
**Entities** list shows an `enabled` / `disabled` badge with an **Enable**
or **Disable** button. Clicking it:

- flips app-shack's internal entity registry entry
  (`disabled_by = None` to enable, `"user"` to disable),
- **persists** the override to `entity_overrides.json` in the shim data dir
  (via `shim/storage.py`) so it survives app-shack restarts,
- republishes the entity's MQTT discovery so the new `enabled_by_default`
  flag reaches HA,
- on **enable**, requests a coordinator refresh so the endpoint is polled
  immediately (the refresh is skipped on disable to avoid a needless API
call).

Because integration coordinators such as Smartcar's read the same registry
(`async_entries_for_config_entry`) to decide which endpoints to batch, the
toggle is the single source of truth for polling control. On restart,
`EntityRegistry.register()` re-applies the persisted overrides, so a
force-enabled disabled-by-default entity (e.g. `odometer`) is polled again
without any re-toggling.

> **HA-side caveat:** republishing discovery with `enabled_by_default: true`
> updates HA's cached discovery, but if you previously enabled the entity in
> HA's own registry HA keeps your explicit choice. For a freshly discovered
> entity that HA created disabled-by-default, the republished discovery will
> flip it to enabled. If in doubt, enable it in HA's entity registry too.

The earlier `force_enable_entities` config option (and matching add-on schema
field) has been **removed** in favor of this persisted UI toggle. There is no
bulk/seeding mechanism anymore — every entity is toggled one-by-one via the
UI (or the MQTT channel below, which can be driven by an HA automation).

### Endpoints

| Method & path | Effect |
|---------------|--------|
| `POST /entity/{entity_id}/enable`  | Enable + persist + republish discovery + refresh. |
| `POST /entity/{entity_id}/disable` | Disable + persist + republish discovery (no refresh). |

`{entity_id}` is the full entity id (e.g. `sensor.smartcar_odometer`); dots
are fine in the path segment. Both endpoints redirect (HTMX) back to the
integration detail page.

## 2. Runtime enable/disable via MQTT

Two per-entity topics let HA stop or start app-shack polling an endpoint.
These handlers delegate to the same `apply_entity_enabled` as the web UI
(§1), so they also **persist** the override and **republish** MQTT discovery:

| Topic | Effect |
|-------|--------|
| `homeassistant/<domain>/<object_id>/enable`  | `disabled_by = None`, persist, republish discovery, request a coordinator refresh (polls immediately). |
| `homeassistant/<domain>/<object_id>/disable` | `disabled_by = "user"`, persist, republish discovery; subsequent polls omit the endpoint (no refresh). |

`<object_id>` is the MQTT object id (dashes for underscores, e.g.
`dev-odometer`).

### Wiring it from HA

HA does not auto-publish on a registry toggle, so publish from a small
automation (or the MQTT integration's "Publish" action):

```yaml
# Example: when the Smartcar Odometer sensor is enabled in HA, start polling.
alias: "Poll smartcar odometer when enabled"
trigger:
  - platform: state
    entity_id: sensor.smartcar_odometer
    to: ~    # fires on enable (state becomes available/idle)
action:
  - service: mqtt.publish
    data:
      topic: homeassistant/sensor/smartcar-odometer/enable
      payload: "1"
```

A corresponding `disable` publish tells app-shack to stop polling (useful to
conserve API quota).

### What happens

- **Enable** — `ShimManager._on_entity_enable` parses the topic, finds the
  entity, and dispatches `_apply_entity_enabled(entity, True)`, which flips
  the internal registry entry and calls `coordinator.async_request_refresh()`
  so the endpoint is fetched now rather than waiting for the next scheduled
  interval.
- **Disable** — `_apply_entity_enabled(entity, False)` marks
  `disabled_by = "user"`. No refresh is triggered; in-flight requests keep
  their result, but subsequent coordinator batches (`_batch_add_defaults`)
  omit the endpoint.

## 3. On-demand refresh (customized polling) via MQTT

Smartcar (and similar integrations) recommend per-entity refreshes to
**minimize API calls** rather than polling every endpoint on a schedule. In
real Home Assistant you'd call the `homeassistant.update_entity` service on the
entity; that works in real HA because the integration's entity code runs inside
HA's process (its `async_update()` queues only that endpoint on the
coordinator, then refreshes).

That **does not work** for these MQTT-discovered entities: HA's MQTT
integration is passive — `MqttSensor` has no `async_update`, `should_poll` is
`False`, and `homeassistant.update_entity` only rewrites the cached state with
**no publish back to the broker**. So app-shack never learns about the request.

App-shack exposes a per-entity refresh topic instead:

| Topic | Effect |
|-------|--------|
| `homeassistant/<domain>/<object_id>/refresh` | Queue ONLY this endpoint on its coordinator, then request an immediate refresh. Because the coordinator skips its default-batch when an explicit request is queued, only this endpoint is fetched (minimizing API calls). |

### Wiring it from HA

Since `homeassistant.update_entity` won't emit to MQTT, trigger the refresh via
`mqtt.publish`. A button + automation is a convenient UX:

```yaml
# A reusable button (add per entity, or use a single mqtt.publish service call)
automation:
  - alias: "Refresh smartcar odometer on demand"
    trigger:
      - platform: state
        entity_id: button.refresh_smartcar_odometer  # you define this button
        to: "on"
    action:
      - service: mqtt.publish
        data:
          topic: homeassistant/sensor/smartcar-odometer/refresh
          payload: "1"
```

Or call `mqtt.publish` directly from a script/dashboard.

### What happens

- `_on_entity_refresh` parses the topic, finds the entity, and dispatches
  `_refresh_entity(entity)`.
- **Path 1 (smartcar-style coordinators)** — if the coordinator exposes
  `batch_sensor(entity)`, the handler guards the scope check
  (`coordinator.is_scope_enabled(key)`; smartcar's `batch_sensor` asserts it),
  then queues only that entity via `batch_sensor` and calls
  `async_request_refresh()`. The coordinator's `_batch_process` sees a
  non-empty `batch_requests` and skips `_batch_add_defaults`, so only the
  requested endpoint is fetched.
- **Path 2 (plain coordinators)** — coordinators without a per-entity batch
  API just get `async_request_refresh()` (uses the coordinator's default batch
  behavior — refreshes everything it normally would).

## How the pieces fit together

- `shim/entity.py` — `RegistryEntry.disabled_by`, `EntityRegistry.get_registry_entry`
  / `async_update_entity(disabled_by=...)`, `Entity.enabled`, the
  registry-aware overload of `entity_registry_enabled_default` (consulted by
  each platform's `_publish_mqtt_discovery`), the storage-backed override
  cache (`_load_overrides`/`_persist_override`) applied by `register()` and
  written by `async_update_entity`.
- `shim/storage.py` — `entity_overrides.json` with
  `load_entity_overrides()` / `save_entity_overrides()`.
- `shim/registries.py` — registers entities (persisted overrides are applied
  inside `EntityRegistry.register()`, so no startup-time allowlist plumbing
  remains here).
- `shim/manager.py` — `apply_entity_enabled` (shared by MQTT and the web
  routes: flip + persist + republish discovery + optional refresh),
  `_on_entity_enable` / `_on_entity_disable` (MQTT inbound),
  `_on_entity_refresh` / `_refresh_entity` (MQTT on-demand refresh), plus the
  subscriptions in `_setup_mqtt_subscriptions`.
- `shim/web/routes/integrations.py` — per-entity `registry_enabled` flag in
  the detail-page context and the `POST /entity/{entity_id}/enable|disable`
  endpoints that drive `apply_entity_enabled`.
- `shim/web/templates/integration_detail.html` — the per-row toggle button.

## Tests

`tests/test_entity_enable.py` (registry mutability, `Entity.enabled`, the
storage-backed persistence of overrides) and `tests/test_manager_entity_enable.py`
(MQTT enable/disable handlers, `apply_entity_enabled` republish+refresh
behavior, refresh handler, manager-init signature) plus render tests in
`tests/test_integration_detail_render.py` for the toggle button.