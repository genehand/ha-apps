# Schema Library: probatio (voluptuous)

HA 2026.9 moved config validation to [`probatio`](https://probatio.frenck.dev/), a
maintained drop-in reimplementation of voluptuous. Integrations now
`import probatio` directly, but `probatio` is provided by HA core and is therefore
*not* listed in integration manifests.

**The shim must not depend on `voluptuous` directly.** It now arrives anyway, as a
transitive dependency of Home Assistant's own tree (`annotatedyaml` requires it), so
"is it installed" is no longer a useful invariant. What matters is that it is
shadowed: every `import voluptuous` resolves to probatio through the alias below, so
the wheel on disk is never imported as a module. Do not add it to `pyproject.toml`,
and do not remove it from the lock — HA's constraints own it.

The shim depends on `probatio` and calls `probatio.compat.install_as_voluptuous()`
at the top of `shim/__init__.py`, before any shim module imports voluptuous. This
registers probatio under the `voluptuous` name (including the
`voluptuous.schema_builder` / `validators` / `error` / `util` / `humanize`
submodules that dependencies like `annotatedyaml` reach into) so that:

- `import probatio` (new integrations) and `import voluptuous as vol` (shim
  internals, older integrations) resolve to the same classes.
- `isinstance(key, vol.Required)` and `key.default is vol.UNDEFINED` checks in
  `shim/web/schema.py` and `shim/web/routes/config_flows.py` keep working.
  Probatio's markers and `UNDEFINED` sentinel are distinct objects from
  voluptuous's, so a mixed namespace silently renders every field as optional.

Bootstrap order used to matter more than it does now: importing the real
`homeassistant` package (which happens the moment `shim.config_entries` imports
`homeassistant.exceptions`) runs `probatio.compat.install_as_voluptuous()` and sets
`BuildPolicy.LAZY` itself, so the alias is installed even before `shim/__init__.py`
gets to it. That is a safety net, not a licence — keep
`import voluptuous as vol` out of module scope in anything that can run before
`shim/__init__.py`, because the alias is what makes `isinstance(key, vol.Required)`
checks in `shim/web/schema.py` and `shim/web/routes/config_flows.py` correct.

`BuildPolicy.LAZY` is HA's choice and now applies to every schema the shim builds:
fields are compiled on first validation, not at `Schema(...)` construction. Do not
call `set_build_policy()`; the package `__init__` documents that only the
application may set it.

One lingering hole: an integration whose manifest lists `voluptuous` in
`requirements` will get it `uv pip install`ed into `/data/.venv` by
`IntegrationManager.install_requirements()` (`shim/integrations/manager.py`). It is
harmless while the alias is in `sys.modules`, and it is already in the image; filter
core-provided packages there if that ever becomes noisy. See
[`integration-requirements.md`](integration-requirements.md) for how the
satisfaction check works (and why it must stay exact — it runs for every
integration on every start).

Known gaps in probatio's voluptuous surface: `voluptuous.Msgs` is not exported
(`Required`, `Optional`, `UNDEFINED`, `_compile_scalar`, etc. are).
