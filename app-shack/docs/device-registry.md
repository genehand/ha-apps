# Device Registry

The shim keeps a device registry (`shim/stubs/helpers.py`) so integrations can
read back the `DeviceEntry` rows that Home Assistant would have created for them.
It is populated while entities are added, from `entity.device_info`.

## Why It Exists

HA's entity platform creates a device from `entity.device_info` while adding the
entity (`EntityPlatform._async_get_entity_device`), *before* calling
`async_added_to_hass`. Integrations rely on that:

- localtuya ≥ 2026.10.0 links a sub-device to its gateway with
  `dr.async_get_device_id_by_identifier(hass, gateway_identifier, config_entry_id=...)`,
  which **raises `ValueError` when the gateway has no device row**. With an empty
  registry, adding any gateway-attached (zigbee) device failed mid platform setup.
- Integrations read `device_entry.config_entry_id` / `device_entry.disabled`, list
  devices with `dr.async_entries_for_config_entry(...)`, or look up a device via
  `dr.async_get_device(identifiers=..., connections=...)`.

## Where Devices Get Registered

`shim/entity.py::register_entity_device()` is the single implementation. It is
called from every live entity-add path, immediately after `entity.hass` is set and
**before** `async_added_to_hass()`:

| Path | Code |
|------|------|
| Integration platforms (`async_forward_entry_setups`) | `shim/registries.py::_do_add` |
| Shim-owned entities (the reload button) | `shim/integrations/loader.py::_register_internal_entity` |
| Integrations calling `entity.add_to_platform_finish()` | `shim/entity.py` (not a live path, kept in sync) |

Ordering is load-bearing: integrations evaluate `device_info` (and resolve
`via_device_id`) from `async_added_to_hass`, so registering afterwards is too late.

`register_entity_device` also stamps the row on the entity as `entity.device_entry`
(HA parity) and writes the id onto the entity registry entry (`device_id`).

Failure handling: `entity.device_info` is a property on integration entities and
can raise. One broken entity is logged at error level and skipped — it does not
abort the rest of the platform setup.

## Registry Semantics (`DeviceRegistry.async_get_or_create`)

Faithful to HA where it matters:

- **Matching is by identifier/connection intersection**, scoped to the config
  entry — an entity reporting a superset of identifiers updates the existing row
  instead of creating a second device. Connections are normalized, so the legacy
  `{"mac:aa:bb"}` string form matches `{("mac", "aa:bb")}`.
- **Only supplied fields overwrite.** An entity that reports nothing but
  identifiers never wipes name/manufacturer/model that another entity of the same
  device supplied. `default_*` values only fill gaps.
- **`via_device`** (deprecated `(domain, identifier)` tuple) is resolved to
  `via_device_id`, preferring a parent in the same config entry.
- **`config_entry_id`** (required since HA 2026.8) is exposed next to the legacy
  `config_entries` set (one entry per device).
- **`async_config_entry_unloaded(entry_id)`** is called from
  `ConfigEntry._run_unload_callbacks()` (HA's
  `_async_process_on_unload`). Devices survive an unload — only the "live"
  bookkeeping is dropped.
- **`async_clear_config_entry(entry_id)`** is called when a config entry is
  removed (`ConfigEntries.async_remove`), which deletes that entry's devices
  (HA's `ConfigEntries._async_clean_up`).
- **`async_entries_for_config_entry(registry, entry_id)`** is implemented (it used
  to be a stub returning `[]`).
- **`area_id` is a real attribute**, settable through
  `async_update_device(area_id=...)` (it used to be accepted and silently
  ignored), and **`async_clear_area_id(area_id)`** exists because upstream's
  `helpers.area_registry.async_delete()` calls it here *and* on the entity
  registry when an area is deleted.
- **`suggested_area` becomes an `area_id`, once.** Upstream applies the hint
  inside `DeviceRegistry.async_get_or_create` and only when it creates the row:
  it resolves the name through `area_registry.async_get_or_create(name)`, i.e. an
  unknown room name *creates* the area. An existing device keeps the area it has,
  so an integration that keeps sending its hint cannot undo a user's move — and
  an unmapped hint must not litter the registry with areas. The hint itself stays
  on the entry (`DeviceEntry.suggested_area`, deprecated upstream and
  runtime-only there) because that is what integrations read back.

Deliberate deviations:

- `config_entry_id` is not validated against a real config entry, so the registry
  is usable standalone (tests, cli scripts).
- Unknown keyword arguments are logged at debug level and ignored instead of
  raising `TypeError` — integrations ship `DeviceInfo` subclasses with extra keys,
  and aborting a platform setup over one is worse than dropping it.
- A new device is **not** named after its config entry title (HA does this). MQTT
  discovery falls back to the registry for name/manufacturer/model/`sw_version`,
  so inventing a name would silently rename every device in Home Assistant.
- Applying `suggested_area` is **best effort**: if the area registry throws, the
  hint is dropped and logged at warning level. Losing a device row is worse —
  integrations resolve `via_device_id` through this registry, and a failed row
  breaks their platform setup (the regression this registry exists for). Upstream
  lets the error escape instead.
- The area is resolved through the **loaded** registry. `ShimManager` loads
  `helpers.area_registry` at startup, so this holds; a standalone registry (tests)
  that never called `area_registry.async_load(hass)` creates the area in a
  registry that has nothing on disk to merge with.

## MQTT Discovery Interaction

`build_mqtt_device_config(device_info, hass)` fills any field `device_info` leaves
empty from the registry. It is always called with the entity's `hass` now (it used
to look up the process-global registry), so the payload comes from the right
instance. A `device_info` that supplies identifiers + name/manufacturer/model still
produces exactly those fields; one that supplies only identifiers stays
identifiers-only (see `tests/test_device_registration.py`) — unless it has a
parent, see below.

### Child devices: `device.via_device`

HA's MQTT discovery has no `via_device_id`. The parent link is `device.via_device`,
a **string** identifier of the parent
(`homeassistant/components/mqtt/entity.py::_via_device_identifier` turns it into a
`("mqtt", <string>)` registry identifier, matching the ones the parent device gets
from its own `device.identifiers` list). `build_mqtt_device_config` therefore emits
`via_device` whenever the device has a parent:

| Source | When |
|--------|------|
| `device_info.via_device` (`(domain, identifier)` tuple) | always, even when the parent row does not exist yet |
| the registry row's `via_device_id` → parent row → its first identifier (sorted) | the meross_lan / hub shape, where the entity's `device_info` carries identifiers only |
| the registry row's own `via_device` tuple | fallback for a link that never resolved |

The value is produced by `format_device_identifiers`, i.e. the same rendering the
parent publishes in `device.identifiers`, because HA needs an exact match. Two
deliberate choices:

- **No registry lookup gates the deprecated tuple.** HA stub-creates a missing
  parent (`mqtt.entity.ensure_via_device_exists`) since discovery order is not
  deterministic; resolving through the registry instead would drop the link for
  whichever device is announced first.
- **Self-references are dropped** (a row whose `via_device_id` is its own id), which
  is what HA's registry migration does too.

Root devices must stay root: the payload only gains the key when a link exists, and
an identifiers-only `device_info` for an unlinked device still produces an
identifiers-only block.

This fixed meross_lan's multi-channel devices: an MSS620 (2 outlets) showed up as
three unrelated devices because the per-channel blocks carried the channel
identifiers and nothing else. `tests/test_integrations.py::
test_meross_lan_mss620_channel_devices_are_children` pins it against the real
integration.

A per-entry control also must not inherit a child device: the reload button takes
the `device_info` of an entity of the same integration and now prefers one on a
device without a parent (`IntegrationLoader._get_reload_button_device_info`). It
used to take the first entity it found, which for a channel-based integration meant
the button lived on "<plug> Channel 1".

### Not supported: registry-level child devices

HA 2026.8+ has real child devices (`ChildDeviceEntry.parent_device_id`,
`async_get_or_create_child_from_info`, `dr.ChildDeviceInfo`, and MQTT's
`device.child_device` block). The shim does not model them:

- `register_entity_device` drops `parent_device_id` (a `DeviceInfo` carrying it
  registers a root device), and the stub `device_registry` module exports no
  `ChildDeviceInfo`, so `DeviceInfo(parent_device_id=...)` / `dr.ChildDeviceInfo(...)`
  raise in an integration that migrates to them.
- No bundled integration uses it yet, and meross_lan (the reason this matters) still
  registers `via_device_id` — its own TODO is to migrate. When it does, the shim
  needs `ChildDeviceInfo`, `async_get_or_create_child(_from_info)`,
  `DeviceEntry.parent_device_id`, and the MQTT `device.child_device` block (an
  inverted payload: the `device` block describes the *parent*, `child_device` the
  identifiers/name of the entity's device).

## Tests

- `tests/test_device_registry.py` — matching/merging, `via_device` resolution,
  `config_entry_id`, lookups, unload/removal hooks.
- `tests/test_upstream_helpers.py` — the area cascade (create/assign/delete through
  the upstream `helpers.area_registry`) and `suggested_area` → `area_id` (reuse an
  existing area, create a new one, never move an existing device).
- `tests/test_device_registration.py` — the entity-add paths end to end with a fake
  gateway/sub-device integration, reload idempotency (no duplicate rows), reload
  button placement (root-device preference included), `via_device` in the discovery
  device block (tuple, registry row, self-reference, multi-identifier parent), and
  the discovery device-block shape.
- `tests/test_device_info.py` — `DeviceInfo` dict-protocol and the real localtuya
  `device_info` property.
- `tests/test_integrations.py::test_meross_lan_mss620_channel_devices_are_children`
  — the real meross_lan integration: channel device rows, channel discovery blocks
  carrying `via_device`, and the reload button staying on the plug.
