# Config Entry Setup / Unload / Migration Results

Since Home Assistant 2026.11 the config entry functions return `None` and signal
failure by raising ([blog, 2026-10-23](https://contributors.home-assistant.io/blog/2026/10/23/config-entry-methods-return-none/),
[setup failures](https://contributors.home-assistant.io/core/integrations/setup-failures/)):

| Return / raise | Meaning | Entry ends in |
|---|---|---|
| `None` | success (new pattern) | `loaded` |
| `True` | success (custom integrations may keep this) | `loaded` |
| `False` | failure (custom integrations may keep this) | `setup_error` / `migration_error` |
| `raise ConfigEntryNotReady` | transient, retry later | `setup_retry` + scheduled retry |
| `raise ConfigEntryAuthFailed` | credentials dead, user must act | `setup_error` + reauth flow |
| `raise ConfigEntryError` | fatal | `setup_error` / `migration_error` |

Custom integrations may keep returning booleans, so the shim must accept both —
but it must never read the value for truthiness: `None` is success. The shim
previously did `if result:` in `setup_integration`, so every integration that
migrated to the new scaffold template silently failed to set up, and a `None`
migration result meant the migrated version was never persisted (the migration
re-ran on every start).

## Where this lives

- `shim/integrations/loader.py::setup_integration` — `result is False` is the
  only boolean failure; `ConfigEntryNotReady` / `ConfigEntryAuthFailed` /
  `ConfigEntryError` are handled by type, not by a generic `except Exception`.
- `shim/integrations/loader.py::_async_schedule_setup_retry` — mirrors HA:
  state `setup_retry`, retry after `min(2**(tries-1) * 5, 600)` seconds, counter
  reset on success, pending retry cancelled by unload/reload.
- `shim/integrations/loader.py::unload_integration` — `async_unload_entry`
  returning `None` is a clean unload; only `False` warns.
- migration — `None`/`True` persist the new entry version, `False` or a raised
  `ConfigEntryError` set `migration_error` and skip setup.
- `shim/registries.py::ConfigEntries.async_unload_platforms_or_raise` — the
  2026.11 replacement for `async_unload_platforms`, raising `ConfigEntryError`
  instead of returning `False`.
- The integration detail page shows the real state (`Setup retry`, `Setup
  error`, `Migration error`, …) instead of a binary Loaded/Not loaded badge.

## Migration target version

The entry is migrated only when `entry.version` is below the integration's
version. The shim resolves that from the module-level `VERSION` /
`ENTRIES_VERSION`, else from the **flow handler**'s `VERSION`
(`_get_config_flow_class`). Reading `ConfigFlow.VERSION` from the base class was
wrong: it is always `1`, so integrations that only set `VERSION` on their own
handler subclass never migrated.
