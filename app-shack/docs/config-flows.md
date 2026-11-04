# Config Flow Step Results (`step_id`)

The shim is the flow manager for every integration it runs — Home Assistant's
`data_entry_flow.FlowManager` is not involved. That makes two upstream rules
ours to enforce or relax:

## 1. `step_id` is optional but deprecated

Upstream ([flows blog post](https://contributors.home-assistant.io/blog/2026/10/23/flow-results-require-step-id/))
deprecated omitting `step_id` from `async_show_form`, `async_show_menu`,
`async_show_progress` and `async_external_step`. Custom integrations log a
warning until Home Assistant 2027.5.0, when `step_id` becomes required and the
manager's fallback is removed.

The shim mirrors that transition rather than failing early:

- `ConfigFlow` / `OptionsFlow` `async_show_form`, `async_show_menu` and
  `async_external_step` take `step_id: str | None = None` and **omit** the key
  from the result when it is `None`, exactly like HA's `FlowHandler`.
- `apply_step_id_fallback(flow, called_step_id, result)` (`shim/config_entries.py`)
  backfills the step the manager called and logs a deprecation warning. It is
  called by whoever invoked the `async_step_*` method — the flow class alone
  cannot know that — i.e. `IntegrationLoader.start_config_flow`,
  `continue_config_flow`, `start_options_flow`, `continue_options_flow` and
  `FlowManager.async_init` (discovery/reauth paths, including the
  auto-submit branch).
- `flow.cur_step_id` is only set when a step passes `step_id`; the loader keeps
  it in sync from the (backfilled) result so the web UI can find the next step.

Why the fallback lives in the manager matters: an integration whose
`async_step_user` calls `async_step_pair` directly would otherwise inherit the
*outer* step's id, which is the bug upstream removed.

Do **not** make `step_id` a required parameter again — that turns a supported
(if deprecated) integration into a `TypeError` that kills the flow in the web
UI with no hint about which integration broke.

## 2. Progress steps are not implemented

`async_show_progress` / `async_show_progress_done` do not exist on the shim's
flow classes, and there is no `SHOW_PROGRESS` render path:

- `shim/config_entries.py` — methods missing (integrations raise `AttributeError`)
- `shim/web/renderers.py`, `shim/web/routes/config_flows.py` — no `progress`
  result type
- `shim/integrations/loader.py` — no `progress_task` callback / re-configure
  handling (HA does this in `data_entry_flow.py` after the step returns)

`STEP_ID_OPTIONAL_RESULT_TYPES` already includes `progress`, so adding the
methods plus a "working…" page that polls is the remaining work.

**Measured alternative:** if a real Home Assistant is ever embedded, this whole
section goes away —
`docs/tier-c-spike.md` §9 runs `async_show_progress` against a real `FlowManager`
(22 of the 960 core `config_flow.py` modules at the pin use it) and the manager
itself re-enters the step from the `progress_task` done-callback, so the UI only
polls `async_progress_by_handler()`.

## 3. Reauth / reconfigure helpers

A migrated integration (HA 2025.7+ style, e.g. `nest_protect`) does not abort
and re-add the entry when its credentials die — it edits the entry in place:

```python
async def async_step_reauth(self, entry_data):
    self._default_account_type = self._get_reauth_entry().data[CONF_ACCOUNT_TYPE]
    return await self.async_step_auth_method()

async def async_step_reauth_confirm(self, user_input=None):
    entry = self._get_reauth_entry()
    self._abort_if_unique_id_mismatch(reason="wrong_account")
    return self.async_update_reload_and_abort(entry, data_updates=input)
```

All of those names are part of the flow contract, so `shim/config_entries.py`
provides them (`_get_reauth_entry` / `_get_reconfigure_entry` read
the entry through `entry_id` in the flow context, via
`ConfigEntries.async_get_known_entry`, which raises `UnknownEntry`):

- `_get_reauth_entry`, `_get_reconfigure_entry` and their `_*_entry_id`
  properties raise `ValueError` when the flow source does not match.
- `_abort_if_unique_id_mismatch` raises `AbortFlow`. The flow manager —
  `IntegrationLoader.start/continue_config_flow`, `start/continue_options_flow`
  and `FlowManager.async_init` — catches it and returns
  `{"type": "abort", "reason": …}` (`abort_result_from_flow_error`), like HA.
  Before that, the exception escaped and the UI showed "Failed to continue
  config flow" with the reason lost.
- `async_update_reload_and_abort` / `async_update_and_abort` update the entry
  (`data_updates` merges into `entry.data`; `unique_id`, `title`, `data` and
  `options` replace outright) and abort with `reauth_successful` /
  `reconfigure_successful`. The reload goes through
  `ConfigEntries.async_schedule_reload`, so it is a background task exactly as
  in HA.
- `async_show_form` adds the entry title to the reauth
  `description_placeholders` as `{name}` (HA does this in the flow base class).

**Ending a flow removes it.** HA drops the progress entry as soon as a step
returns `abort` (or creates the entry); the shim used to keep it, so
`ConfigEntry.async_get_active_flows` kept reporting a reauth/reconfigure still
running and `async_start_reauth` then refused to start a *new* reauth for that
entry until the app restarted. `IntegrationLoader._finalize_result` and
`FlowManager.async_init` now discard it (`FlowManager.async_abort`, which also
dismisses the reauth notification).

**`_abort_if_unique_id_configured` mirrors HA.** It aborts with
`already_configured` when an entry for the handler already carries this
`unique_id`, after merging `updates` into that entry's data and reloading it
unless `reload_on_update=False` (only if the entry is `loaded`/`setup_retry`),
and it reloads an entry in `setup_retry` when a *discovery* source rediscovers
it. An entry of source `ignore` is still configurable from the manual user step.
It used to be a no-op stub that only accepted `updates`, so meross_lan 6.0.0's

```python
self._abort_if_unique_id_configured(updates=device_config, reload_on_update=False)
```

raised `TypeError: ... unexpected keyword argument 'reload_on_update'` inside
`async_try_discovery` and the rediscovered device could not be added. Duplicate
discoveries now abort (like HA) instead of creating a second entry for the same
device — `DISCOVERY_SOURCES` in `shim/config_entries.py` is the source set that
triggers the `setup_retry` reload; `tests/test_abort_if_unique_id_configured.py`
pins all of it.

## 4. Options flows: `entry.options`, reloads, and subentries

Three different things get called "options flow"; keep them apart.

### 4.1 One form per entry — implemented, and it now persists

`OptionsFlow` / `OptionsFlowWithConfigEntry` (`async_get_options_flow`) cover
181 of the 960 core `config_flow.py` modules. The shim renders the form, walks
menus and reloads a loaded entry when the flow finishes.

What was **not** implemented is the manager half of the contract: HA's
`OptionsFlowManager.async_finish_flow` takes a finished options flow's
`create_entry` **`data`** and calls
`async_update_entry(entry, options=result["data"])` — replacing
`entry.options` wholesale. The shim re-saved `entry.data` and dropped the
result, so every option picked in the web UI was silently lost for every
integration that leaves persisting to the manager (the submit said
"Configuration updated successfully!", the entry was unchanged).
`IntegrationLoader._apply_options_result` now does what HA does, from both
`start_options_flow` (a flow that finishes without asking) and
`continue_options_flow`.

`OptionsFlowWithReload` exists as HA's marker class (`automatic_reload = True`,
73 of those 181 modules subclass it; without the class the import fails and the
integration cannot load). The reload itself is still done by the web route
after a successful options flow rather than by the markers' value — the shim has
no config-entry update listeners to collide with, so HA's
`automatic_reload and entry.update_listeners` conflict cannot arise here.

### 4.2 Subentries — not implemented (deliberately)

`ConfigSubentry`, `ConfigSubentryFlow`, `async_get_supported_subentry_types`,
`hass.config_entries.subentries` and `ConfigEntry.subentries` are absent. 39 of
the 960 core modules use them (Anthropic, GitHub, MQTT, Forecast Solar, … —
mostly cloud/service integrations with per-account child configs); no bundled
integration in `data/shim/custom_components/` does (`meross_lan` only passes
`subentries_data=()`, which the `ConfigEntry` dataclass accepts).

Cost if it ever matters: entry storage v1.4 (subentries live in the entry), a
second flow manager, the `SubentryFlowHandler` API, plus web UI for
list/add/edit/remove of subentries — and every entity would have to carry its
`subentry_id` through MQTT discovery. Until then, an integration that needs it
fails loudly at import (`cannot import name 'ConfigSubentryFlow'`), which is the
trade the stub layer makes everywhere else.

## Tests

`tests/test_config_flow.py`:

- `test_start_config_flow_backfills_missing_step_id`
- `test_continue_config_flow_backfills_missing_step_id_for_menu`
- `test_start_options_flow_backfills_missing_step_id`
- `TestApplyStepIdFallback` — form/menu/external backfill, explicit `step_id`
  untouched, `create_entry` untouched

`tests/test_config_flow_reauth_helpers.py`:

- `TestGetEntry` — reauth/reconfigure lookups, wrong source, unknown entry
- `TestUpdateAndAbort` — `data_updates` merge, reload scheduling, abort reasons
- `TestAbortIfUniqueIdMismatch` — raises `AbortFlow` for the other account
- `TestShowFormReauthPlaceholder` — `{name}` injection, only for reauth
- the two end-to-end tests drive a real `custom_components` fixture through
  `flow.async_init` + `continue_config_flow` (success and `wrong_account`)

`tests/test_options_flow.py`:

- `test_options_flow_result_reaches_entry_options` — the fix
- `test_options_flow_replaces_options_like_ha` — replace, not merge
- `test_options_flow_persists_across_reload` — the stored entry carries them
- `test_form_step_leaves_options_alone` — only `create_entry` writes
- `test_options_flow_with_reload_subclass_finishes` — marker class path
- `test_options_flow_with_reload_is_an_options_flow` — the marker itself
