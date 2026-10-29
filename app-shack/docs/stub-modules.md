# Stub Modules

A stub is a synthetic `homeassistant.*` module the shim installs into `sys.modules`
so an integration's import succeeds even though the real HA module needs the model
layer the shim does not have. The upstream tier ([`upstream-tier.md`](upstream-tier.md))
is the other half of the same decision: prefer the wheel, stub only what it cannot
give you here.

## Adding a new stub

Before writing a stub, check whether Home Assistant already provides the module in
a form that works here. If it does not, prefer a stub over a hand-copy:

1. **Determine the namespace**: `homeassistant.util.X` → `stubs/util.py` is gone;
   `homeassistant.util` is upstream's real package, so util modules need no stub at
   all. `homeassistant.helpers.X` → `stubs/helpers.py` — unless it is already in
   `UPSTREAM_HELPER_MODULES` (storage, dispatcher, start, instance_id, json, the
   floor/label/category/issue registries), in which case there must be no stub.
2. **Create the stub function**: Add a `create_X_stubs()` function that builds and registers the module
3. **Call from import_patch.py**: Add the function call in `ImportPatcher.patch()`
4. **Export from `stubs/__init__.py`**: Add the function to `__all__`
5. **Write tests**: Verify the stub works with real integration patterns

## Guidelines

- Keep implementations minimal but compatible with HA's API
- Match function signatures exactly (including return types)
- Include all constants and enums that integrations might import
- Use `FrozenOrThawed` metaclass for EntityDescription classes
- **Do not re-implement a module that upstream's pure modules cover.** Check the
  table in [`upstream-tier.md`](upstream-tier.md) first; a stub that quietly
  diverges from upstream is the bug class this repository is trying to delete
  (`util.color.brightness_to_value` was written as `int(brightness * 255 / 255)`,
  i.e. it ignored the range entirely).
- **Import third-party packages lazily, never at stub-creation time.** Stubs are built
  during import patching, before `IntegrationManager.install_requirements()` installs
  an integration's manifest requirements into `/data/.venv`, so a package can legitimately
  be missing on the first boot after a venv rebuild. Follow the pattern in
  `stubs/components.py` (`_load_zeroconf_classes`) — resolve on first use, cache only
  successful resolutions, and expose classes to integrations through a module-level
  `__getattr__` so they appear without a restart.

## Testing a stub

See [`testing.md`](testing.md#testing-stub-modules).
