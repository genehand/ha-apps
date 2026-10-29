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
polls `async_progress_by_handler()`. The same section lists the other shapes the
shim does not implement today, with usage counts (`async_update_reload_and_abort`
in 362 modules, reauth in 322, reconfigure in 207).

## Tests

`tests/test_config_flow.py`:

- `test_start_config_flow_backfills_missing_step_id`
- `test_continue_config_flow_backfills_missing_step_id_for_menu`
- `test_start_options_flow_backfills_missing_step_id`
- `TestApplyStepIdFallback` — form/menu/external backfill, explicit `step_id`
  untouched, `create_entry` untouched
