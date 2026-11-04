# Entity Attribute Surface (assignable, like HA's)

`shim/entity.py` and `shim/platforms/*.py` serve the entity attribute getters
(`device_info`, `available`, `native_value`, `supported_features`, `state`, …).
**They are `EntityAttribute` descriptors, not plain `@property`.**

## Why

Home Assistant declares these getters as `cached_property` (propcache), which is a
**non-data descriptor**: no `__set__`, so an instance attribute wins over the class
attribute. That is what makes the legacy assignment style legal upstream:

```python
self.device_info = parent.get_device_entry_info(id)   # meross_lan
self.available = False                                # meross_lan set_unavailable()
setattr(self, "entity_registry_enabled_default", …)   # meross_lan kwargs loop
self.native_value = device_value                      # meross_lan parsers
```

A plain `@property` is a **data descriptor**. Reads behave the same, but the same
assignment raises:

```
ERROR: shim.integrations.loader - Error during async_setup_entry for meross_lan:
       property 'device_info' of 'ProtocolSensor' object has no setter
```

That is meross_lan 6.0.0's `Entity.__init__` — it assigns `device_info`, `available`,
`force_update`, `has_entity_name`, `should_poll` and loops over
`HA_ENTITY_ATTRIBUTES` (`device_class`, `entity_category`,
`entity_registry_enabled_default`, `name`, `icon`, `unique_id`) on **every** entity,
so nothing in the integration could be constructed.

`EntityAttribute` restores the non-data behaviour: the getter still computes when
nothing was assigned, and assignment shadows it, exactly like HA.

```python
class Entity:
    @EntityAttribute
    def available(self) -> bool:
        return self._attr_available

class SensorEntity(Entity):
    @EntityAttribute
    def available(self) -> bool:
        # derived fallback; integrations may still assign
        return self.native_value is not None
```

## It is deliberately not cached

`EntityAttribute` does **not** cache the value the way `cached_property` does. The
shim reads these attributes on every state publish, and integrations flip the
`_attr_*` values they feed at runtime (`_attr_available`, `_attr_icon`, …) — a
one-shot cached value would freeze the entity. HA integrations that change these
after the first read override the property instead.

## When adding a getter

Ask what upstream declares it as (check the wheel:
`.venv/lib/python3.*/site-packages/homeassistant/helpers/entity.py`, and the
platform's `CACHED_PROPERTIES`):

| upstream | shim |
|---|---|
| `cached_property` (assignable) | `@EntityAttribute` |
| `property` (read-only by design, e.g. `Entity.enabled`) | `@property` |

`tests/test_entity_attribute_surface.py` pins the descriptive surface
(`test_entity_accepts_the_legacy_assignment` iterates the names meross_lan assigns),
the "assignment beats the derived getter" precedence, and that unassigned getters
still compute. `tests/test_integrations.py::test_meross_lan_v6_device_entry_setup`
runs a real meross_lan 6.0.0 device entry end to end (skipped when the integration
is not installed in `data/shim/custom_components`).
