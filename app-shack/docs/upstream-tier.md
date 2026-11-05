# Upstream Home Assistant Code (imported, not vendored)

Home Assistant is a dependency: `homeassistant==<pin>` in `rootfs/app/pyproject.toml`.
The shim imports the modules HA keeps free of its model layer **verbatim** and
re-implements only the rest. There is nothing to fetch and nothing to clean up by
hand any more — the wheel is the source of truth, `uv.lock` is the pin.

Short version for `AGENTS.md` readers: **do not copy upstream code into the shim, and
do not grow the stub layer when the wheel module already works here.** Check the tier
below first.

## The upstream tier

`shim/import_patch.py` lists it: `UPSTREAM_UTIL_MODULES`,
`UPSTREAM_HELPER_MODULES`, plus `const`, `exceptions` and `generated.*`.

| imported from the wheel | why it qualifies |
|---|---|
| `const`, `exceptions`, `generated.*` | leaf modules; only import util/helpers |
| `util.*` (whole package object) | HA keeps util pure by design |
| `helpers.typing`, `helpers.deprecation` | leaf modules |
| `helpers.json` | pure; only util |
| `helpers.storage`, `.dispatcher`, `.start`, `.instance_id` | import `homeassistant.core` for types/callbacks only; imported after `shim.core` is installed |
| `helpers.issue_registry`, `.floor_registry`, `.label_registry`, `.category_registry`, `.area_registry` | same, plus `helpers.{storage,registry,singleton}` |
| `helpers.network` | real internal/external URL resolution; `helpers.http`/`hassio` come along |
| `helpers.debounce`, `.ratelimit`, `.location`, `.sun`, `.trace`, `.field_visibility` | pure leaves; never needed the model layer |

`homeassistant.util` is handed to integrations as the **real package object**, so a
`util` submodule nobody listed still resolves from the wheel. `components` stays
synthetic: an unstubbed name there must fail loudly rather than half-load the real
model layer.

`homeassistant.helpers` stays synthetic too, but it gets a `__path__` pointing at
the wheel's `helpers/` directory, so an adopted helper can be loaded as a real module
*behind* the stubs the shim still owns. Consequence to know about: any helper the
shim does not stub is now importable and half-loads (failing inside upstream)
instead of raising `ModuleNotFoundError`.

Adopted helper modules import `homeassistant.core`, which the shim replaces — so the
patcher imports them **last**, after `sys.modules["homeassistant.core"] = shim.core`.
`shim/core.py` re-exports the names they need (`CoreState`, `HassJob`,
`HassJobType`, `ReleaseChannel`, `get_release_channel`,
`get_hassjob_callable_job_type`, `split_entity_id`, `valid_entity_id`, `DOMAIN`,
`async_get_hass(_or_none)`, `EventState*Data`) from the wheel; `shim/hass.py`
imports `CoreState`/`HassJob` from it too. Get the order wrong and you get two
`HassJobType` enums, and `hass.async_run_hass_job` (which compares `job_type` by
identity) silently stops running jobs. `tests/test_upstream_modules.py` asserts
the identity.

## Adding a module to the upstream tier

1. Prove it is model-layer-free *in a fresh process*: importing it must not pull in
   `homeassistant.core`'s model layer (its names are fine — adopted helpers use
   them), `config_entries`, `loader`, `setup`, `data_entry_flow`, or any
   entity/device registry. Already-imported modules hide the dependency, so a scan
   in a warm interpreter will lie to you. Measure with
   `scripts/scan_upstream.py <module>` — it reports both `core` (fine, means
   "import it late") and `blockers` (not fine).
2. Add it to the appropriate `UPSTREAM_*` tuple in `shim/import_patch.py`. A
   helper module is imported *after* the patch is installed (see above), and it
   needs `hass`-side support: if it calls a method `shim/hass.py` does not have,
   add it there (`async_run_hass_job`, `async_create_task_internal`,
   `verify_event_loop_thread` and friends already are).
3. Add every helper module it imports *transitively* to `KEEP_IN_HELPERS` in
   `scripts/prune_homeassistant.py`, and any new top-level module to
   `KEEP_TOP_LEVEL`. `tests/test_upstream_modules.py` measures the closure in a
   fresh subprocess and fails if the keep-list misses one.
4. Delete the stub it replaces — from `create_helpers_stubs()` *and*
   `sys.modules`. Leaving it is not a no-op: `sys.modules` wins, so the upstream
   import silently returns the stub (this happened with `issue_registry`).
5. Run the suite, then the pruned-image check in [`image-size.md`](image-size.md).

## Known blockers

So nobody re-derives them: `helpers.config_validation` (+84 modules)
and `helpers.selector` (+86) pull `entity_registry` and `loader`; `helpers.event`
pulls `helpers.template` plus the real device/entity registries and `loader`;
`helpers.translation`, `.frame` and `.script_variables` need `homeassistant.loader`
(the shim has no such module); `helpers.update_coordinator` and
`helpers.restore_state` pull the full model layer; every `components.<domain>`
pulls the full model layer. Those need a booted `hass` (or a real registry), not a
bigger keep-list.

Note what `helpers.area_registry` shows: a module that imports `device_registry`
*can* still be adopted, because inside the shim that import binds the shim's stub.
Adoption only needs the stub to carry the module-level API the upstream helper
actually calls — here `dr.async_get()` plus `async_clear_area_id()`, which the stub
did not have (and no `area_id` field to clear). Expect the same shape for
`helpers.event`: it needs `frame`, `template` and real registries, so it needs the
model layer, not a bigger keep-list.

## Adoption runs both ways

The dependency runs both ways, and that is the trap: an adopted helper calls *back
into* a stub, and the stub has to match upstream's *behaviour*, not just its
signatures. Upstream's `device_registry.async_get_or_create` maps a
`suggested_area` name to an `area_id` through `area_registry.async_get_or_create()`
while creating the row; the shim's registry carried the hint as an inert string
until it was made to do the same (see [`device-registry.md`](device-registry.md)).

## 2026.11 notes (pinned)

The pin is `2026.11.0`; the beta checklist below is what the bump actually cost.
Verified against the release (`git diff 2026.10.4 2026.11.0`), not the beta.

- **HACS is absorbed into core as the Marketplace** (`components/marketplace/`).
  "The HACS installer stops at Home Assistant 2026.11." app-shack runs *outside*
  HA and already owns the install/update/device-flow UI, so the policy is: **keep
  the installer and do not port the Marketplace UI**. Adopt Marketplace *leaf*
  utilities where the shim duplicates them, by loading the wheel file from
  `shim/marketplace.py` (upstream stays the source of truth; nothing is copied).
  `utils/version.py` (`is_newer_version`, `is_same_or_newer_version`) is adopted
  and used by the integration manager. The package itself is still not
  importable: `components/marketplace/__init__.py` pulls `homeassistant.auth`,
  `components.frontend`, `components.http`, `config_entries` and `loader`, so
  only relative-import-free `utils/*` files qualify.
- **`httpx==0.28.1` → `httpx2==2.14.0`** in core's deps. `homeassistant/__init__.py`
  now calls `httpx2.alias_httpx()`, so `import httpx` inside an integration
  resolves to httpx2. The dev group no longer pins the old `httpx` (it was only
  there for `starlette.testclient`, which resolves through the alias once the
  shim's `tests/__init__.py` has imported `homeassistant`).
- **New core re-exports the adopted helpers need.** `helpers/start.py` imports
  `async_noop`, and the area/floor/label/category/issue registries decorate with
  the new `verify_event_loop_thread`; both are re-exported from `shim/core.py`.
  `helpers/area_registry.py` also subscribes to `entity_registry_updated`, so the
  entity-registry stub now exposes `EVENT_ENTITY_REGISTRY_UPDATED` and
  `EventEntityRegistryUpdatedData`.
- **Integration-facing API changes** mirrored by the shim for this release (each
  is covered by tests): `DeviceInfo`/`ChildDeviceInfo` as dataclasses with
  `UNDEFINED` unset fields and `as_dict()`; child devices
  (`async_get_or_create_child(_from_info)`, `async_get_or_create_from_info`,
  `DeviceInfoError`, delete/disable cascade, effective-area inheritance, the MQTT
  `child_device` block); `UnitOfMeasurementSelector`; update entities' `has_update`;
  climate/water-heater `native_*` temperature properties and `temperature_unit`;
  `ScannerEntity` device handling and disabled-by-default; and
  `RestoreStateData.async_get_stored_state` replacing `last_states`.
- Upstream-tier files all moved between 2026.10.4 and 2026.11.0 (`util/dt.py`,
  `util/unit_system.py`, `util/color.py`, `const.py`, `exceptions.py`,
  `helpers/{storage,issue_registry,deprecation,network,area_registry,sun}.py`).
  The suite (`tests/test_upstream_modules.py` measures the transitive closure in a
  fresh process) is what catches a keep-list miss.
- `exceptions.ConfigEntryNotReady` gained `retry_after`; `helpers/typing.py`'s
  `VolSchemaType` accepts `probatio.Union`. Both come from the wheel.

## Bumping the pin

The upstream tier is internal API and moves every release — that is why the pin is
exact. To bump: edit the version in `pyproject.toml`, `uv lock`, `uv sync`, run the
suite, and check `tests/test_platforms.py` / `tests/test_upstream_modules.py` for
behaviour deltas. Expect them: the old stubs were approximations, and moving to the
real module is how those approximations get corrected.

Do not float the pin or `uv lock --upgrade` it casually: a bump also drags in HA's
own transitive dependency tree (~80 packages, including a bluetooth and a cloud
stack this app never uses).
