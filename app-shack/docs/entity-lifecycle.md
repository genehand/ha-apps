# Entity Lifecycle Hooks

Home Assistant tightened the entity add/remove lifecycle in 2026.10/2026.11
([contributors blog, 2026-10-12](https://contributors.home-assistant.io/blog/2026/10/12/new-entity-lifecycle-hooks/)
and [entity lifecycle docs](https://contributors.home-assistant.io/core/entities/lifecycle/)).
Integrations depend on those hooks being *called*, not just present, so the
shim's entity-add path mirrors HA's order.

## The hooks

| Hook | Since | Shim support |
|---|---|---|
| `async_prepare_to_add_to_hass()` | 2026.10 | Called on every add attempt (`shim/registries.py::_do_add`, `shim/integrations/loader.py::_register_internal_entity`) |
| `async_added_to_hass()` | long standing | Called after the registry entry is processed |
| `async_entity_id_changed(old_entity_id)` | 2026.11 | Base no-op exists; called on in-place renames (see below) |
| `async_registry_entry_updated()` | long standing | Base no-op exists; called before `async_entity_id_changed` |

`add_to_platform_start()` / `add_to_platform_finish()` / `add_to_platform_abort()`
are **platform** internals: the shim calls them, integrations must not override
them (use `async_prepare_to_add_to_hass` instead).

## Add order in the shim

```
entity_id generated
  -> entity.hass + entity.platform assigned      (add_to_platform_start)
  -> await entity.async_prepare_to_add_to_hass() (abort on exception)
  -> registry entry processed (enabled/disabled, entity filters)
  -> device registered, entity registered
  -> await entity.async_added_to_hass()
  -> first state written, MQTT discovery published
```

Everything from `add_to_platform_start` onwards runs inside a `try` per entity,
so one broken entity is logged (with traceback) and the rest of the platform
still lands. Before that, a single `AttributeError` in the first entity's
`async_added_to_hass` silently dropped the whole platform: the task was awaited
with `asyncio.gather(return_exceptions=True)` and nothing logged the result.

`async_prepare_to_add_to_hass` runs before the enabled/disabled check, matching
HA, and must clean up with `async_on_remove` — an aborted add never reaches
`async_will_remove_from_hass`.

Divergence kept on purpose: HA *aborts* the add for an entity that is disabled
in the entity registry. The shim still adds and publishes it with
`enabled_by_default: false` so it can be enabled in HA or in the Shack UI (see
[`entity-enable-disable.md`](entity-enable-disable.md)).

## In-place `entity_id` changes

`entity_registry.async_update_entity(entity_id, new_entity_id=...)` follows the
2026.11 behaviour:

1. state under the old `entity_id` is removed,
2. the entity and its registry entry move to the new id (domain must not change),
3. `async_registry_entry_updated()` then `async_entity_id_changed(old_entity_id)`
   are called — `self.entity_id` already holds the new id,
4. MQTT topics are moved (the topics derive from the `entity_id`), and the state
   is written under the new id.

`async_will_remove_from_hass` / `async_added_to_hass` are **not** called, so a
hook that keyed a dispatcher signal or listener on `entity_id` must update it and
call `super()`. Exceptions from the hook are logged and the rename still
completes. HA keeps the old remove-and-re-add behaviour for custom integrations
that have not implemented the hook until 2027.11; the shim never did
remove-and-re-add on rename, so it always renames in place.

```python
def async_entity_id_changed(self, old_entity_id: str) -> None:
    """Opt in to in-place entity_id changes."""
    super().async_entity_id_changed(old_entity_id)   # required
    self._unsub()                                    # update entity_id-keyed work
    self._subscribe()
```

Registered integrations already rely on this: nest_protect 0.5.0 calls
`super().async_entity_id_changed(...)` from `NestEntity`, which is why the base
no-op must exist even on a 2026.10 core (where upstream has no such method yet).
