"""Tests for entity enable/disable persistence + registry mutability.

Covers:
- ``RegistryEntry.disabled_by`` + ``EntityRegistry.async_update_entity``
  (mutability of the disabled flag, HA-style).
- ``Entity.enabled`` and the registry-aware overload of
  ``entity_registry_enabled_default``.
- Persisted overrides: storage-backed cache loaded by ``register()`` so the
  enabled state survives app-shack restarts, and ``async_update_entity``
  persisting changes back to storage.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.entity import Entity, EntityRegistry, RegistryEntry


class _FakeStorage:
    """Minimal Storage stand-in exposing load/save_entity_overrides."""

    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def load_entity_overrides(self):
        return dict(self._overrides)

    def save_entity_overrides(self, overrides):
        self._overrides = dict(overrides)


@pytest.fixture(autouse=True)
def _reset_registry():
    EntityRegistry._reset_for_test()
    yield
    EntityRegistry._reset_for_test()


# --- RegistryEntry / EntityRegistry mutability -----------------------------


class TestRegistryMutability:
    def test_disabled_entry_has_disabled_by_integration(self):
        entry = RegistryEntry("sensor.x", "uid", "ce", disabled=True)
        assert entry.disabled is True
        assert entry.disabled_by == "integration"

    def test_enabled_entry_has_disabled_by_none(self):
        entry = RegistryEntry("sensor.x", "uid", "ce", disabled=False)
        assert entry.disabled is False
        assert entry.disabled_by is None

    def test_explicit_disabled_by_wins(self):
        entry = RegistryEntry("sensor.x", "uid", "ce", disabled=True,
                              disabled_by="user")
        assert entry.disabled_by == "user"

    def test_async_update_entity_enables_with_none(self):
        registry = EntityRegistry()
        entity = SimpleNamespace(
            entity_id="sensor.odometer",
            unique_id="dev_odometer",
            _attr_config_entry_id="ce",
            entity_registry_enabled_default=False,
        )
        registry.register(entity)
        assert registry.get_registry_entry("sensor.odometer").disabled is True

        result = registry.async_update_entity("sensor.odometer", disabled_by=None)
        assert result is not None
        entry = registry.get_registry_entry("sensor.odometer")
        assert entry.disabled is False
        assert entry.disabled_by is None

    def test_async_update_entity_disables_with_user(self):
        registry = EntityRegistry()
        entity = SimpleNamespace(
            entity_id="sensor.odometer",
            unique_id="dev_odometer",
            _attr_config_entry_id="ce",
            entity_registry_enabled_default=True,
        )
        registry.register(entity)
        registry.async_update_entity("sensor.odometer", disabled_by="user")
        assert registry.get_registry_entry("sensor.odometer").disabled is True
        assert registry.get_registry_entry("sensor.odometer").disabled_by == "user"

    def test_async_update_entity_unknown_returns_none(self):
        registry = EntityRegistry()
        assert registry.async_update_entity("sensor.missing",
                                              disabled_by=None) is None

    def test_async_update_entity_preserves_name_icon_path(self):
        registry = EntityRegistry()
        entity = SimpleNamespace(
            entity_id="sensor.x",
            unique_id="uid",
            _attr_config_entry_id="ce",
            entity_registry_enabled_default=True,
            _attr_name="old",
            _attr_icon="mdi:old",
            custom_field="a",
        )
        registry.register(entity)
        registry.async_update_entity(
            "sensor.x", name="new", icon="mdi:new", custom_field="b"
        )
        assert entity._attr_name == "new"
        assert entity._attr_icon == "mdi:new"
        assert entity.custom_field == "b"

    def test_async_update_entity_new_unique_id(self):
        registry = EntityRegistry()
        entity = SimpleNamespace(
            entity_id="sensor.x",
            unique_id="uid",
            _attr_config_entry_id="ce",
            entity_registry_enabled_default=True,
        )
        registry.register(entity)
        registry.async_update_entity("sensor.x", new_unique_id="uid2")
        assert registry.get_registry_entry("sensor.x").unique_id == "uid2"


# --- Entity.enabled + entity_registry_enabled_default overload --------------


def _make_entity(*, entity_id, unique_id, enabled_default, key=None):
    entity = Entity()
    entity._attr_entity_registry_enabled_default = enabled_default
    entity.entity_id = entity_id
    entity._attr_unique_id = unique_id
    entity._attr_config_entry_id = "ce"
    if key is not None:
        desc = SimpleNamespace(key=key, entity_registry_enabled_default=False,
                                disabled_by_default=False)
        entity.entity_description = desc
    return entity


class TestEntityEnabled:
    def test_enabled_unregistered_falls_back_to_default(self):
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        assert entity.enabled is False
        assert entity.entity_registry_enabled_default is False

    def test_enabled_unregistered_default_true(self):
        entity = _make_entity(entity_id="sensor.battery",
                               unique_id="dev_battery",
                               enabled_default=True)
        assert entity.enabled is True
        assert entity.entity_registry_enabled_default is True

    def test_registry_override_enables(self):
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry = EntityRegistry()
        registry.register(entity)
        assert entity.enabled is False  # default-disabled
        registry.async_update_entity("sensor.odometer", disabled_by=None)
        assert entity.enabled is True
        assert entity.entity_registry_enabled_default is True

    def test_registry_override_disables(self):
        entity = _make_entity(entity_id="sensor.battery",
                               unique_id="dev_battery",
                               enabled_default=True)
        registry = EntityRegistry()
        registry.register(entity)
        assert entity.enabled is True
        registry.async_update_entity("sensor.battery", disabled_by="user")
        assert entity.enabled is False
        assert entity.entity_registry_enabled_default is False


# --- Persisted overrides (storage-backed) ---------------------------------


def _hass_with_storage(storage):
    hass = MagicMock()
    hass._storage = storage
    return hass


class TestPersistedOverrides:
    def test_register_applies_stored_force_enable(self):
        # Previously force-enabled (disabled_by=None) despite default-disabled.
        storage = _FakeStorage({"sensor.odometer": {"disabled_by": None}})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        entry = registry.get_registry_entry("sensor.odometer")
        assert entry.disabled is False
        assert entry.disabled_by is None
        # Entity reports enabled too (drives discovery payload).
        assert entity.entity_registry_enabled_default is True

    def test_register_applies_stored_disable(self):
        # Previously disabled-by-user despite default-enabled.
        storage = _FakeStorage({"sensor.battery": {"disabled_by": "user"}})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.battery",
                               unique_id="dev_battery",
                               enabled_default=True)
        registry.register(entity)
        entry = registry.get_registry_entry("sensor.battery")
        assert entry.disabled is True
        assert entry.disabled_by == "user"
        assert entity.entity_registry_enabled_default is False

    def test_no_override_falls_back_to_default(self):
        storage = _FakeStorage({})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        assert registry.get_registry_entry("sensor.odometer").disabled is True

    def test_async_update_entity_persists_enable(self):
        storage = _FakeStorage({})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        registry.async_update_entity("sensor.odometer", disabled_by=None)
        assert storage.load_entity_overrides() == {
            "sensor.odometer": {"disabled_by": None}
        }

    def test_async_update_entity_persists_disable(self):
        storage = _FakeStorage({"sensor.odometer": {"disabled_by": None}})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        # now enabled (from override); flip to disabled
        registry.async_update_entity("sensor.odometer", disabled_by="user")
        assert storage.load_entity_overrides() == {
            "sensor.odometer": {"disabled_by": "user"}
        }

    def test_enable_then_disable_then_re_enable_persists_latest(self):
        # Disabling with user persists; re-enabling with None persists None
        # (force-enable) so a default-disabled entity stays enabled on restart.
        storage = _FakeStorage({})
        registry = EntityRegistry()
        registry.setup(_hass_with_storage(storage))
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        registry.async_update_entity("sensor.odometer", disabled_by="user")
        assert storage.load_entity_overrides() == {
            "sensor.odometer": {"disabled_by": "user"}
        }
        registry.async_update_entity("sensor.odometer", disabled_by=None)
        assert storage.load_entity_overrides() == {
            "sensor.odometer": {"disabled_by": None}
        }

    def test_register_without_storage_does_not_persist_but_works(self):
        # No hass._storage attached — persistence is gracefully skipped.
        registry = EntityRegistry()  # no setup()
        entity = _make_entity(entity_id="sensor.odometer",
                               unique_id="dev_odometer",
                               enabled_default=False)
        registry.register(entity)
        # Could not load overrides; entry disabled by default.
        assert registry.get_registry_entry("sensor.odometer").disabled is True
        # async_update_entity still mutates in-memory.
        registry.async_update_entity("sensor.odometer", disabled_by=None)
        assert registry.get_registry_entry("sensor.odometer").disabled is False