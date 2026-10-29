"""Home Assistant entity lifecycle hooks in the shim (2026.10 / 2026.11).

Upstream added two hooks (contributors blog, 2026-10-12):

- ``Entity.async_prepare_to_add_to_hass`` (HA 2026.10) — awaited on every add
  attempt, including adds that end up aborted (disabled entity), before the
  platform processes the registry entry; raising aborts that add.
- ``Entity.async_entity_id_changed`` (HA 2026.11) — an ``entity_id`` change is
  applied in place instead of removing and re-adding the entity.

Integrations already call ``super().async_entity_id_changed(...)`` (nest_protect
0.5.0 does), so the base implementations must exist even before 2026.11.
"""

import importlib
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from shim.core import ConfigEntry, HomeAssistant
from shim.entity import Entity, EntityRegistry
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

DOMAIN = "fake_lifecycle"

FAKE_INTEGRATION = '''
"""Fake integration using the entity lifecycle hooks."""

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass, entry):
    return True
'''

FAKE_SENSOR_PLATFORM = '''
"""Sensors exercising async_prepare_to_add_to_hass."""

from shim.platforms.sensor import SensorEntity

DOMAIN = "fake_lifecycle"

CALLS = []


class _HookedSensor(SensorEntity):
    _attr_native_value = 1
    _attr_should_poll = False

    def __init__(self, unique_id, mode="ok"):
        self._attr_unique_id = unique_id
        self._attr_name = unique_id.replace("_", " ").title()
        self._mode = mode
        self.removed = False

    async def async_prepare_to_add_to_hass(self):
        CALLS.append(("prepare", self.unique_id, self.hass is not None, self.platform))
        if self._mode == "raise":
            raise RuntimeError("prepare failed")

    async def async_added_to_hass(self):
        CALLS.append(("added", self.unique_id))
        self.async_on_remove(self._cleanup)

    def _cleanup(self):
        self.removed = True


class _DisabledSensor(_HookedSensor):
    _attr_entity_registry_enabled_default = False


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            _HookedSensor("ok_sensor"),
            _HookedSensor("raising_sensor", mode="raise"),
            _DisabledSensor("disabled_sensor"),
        ]
    )
'''


def _reset_custom_components():
    for name in [
        n for n in sys.modules if n == "custom_components" or n.startswith("custom_components.")
    ]:
        del sys.modules[name]
    importlib.invalidate_caches()


@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
async def hass(temp_data_dir):
    hass = HomeAssistant(temp_data_dir)
    setup_import_patching(hass).patch()
    yield hass


@pytest.fixture
def loader(hass, temp_data_dir):
    _reset_custom_components()
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    integration_dir = shim_dir / "custom_components" / DOMAIN
    integration_dir.mkdir(parents=True)
    (integration_dir / "__init__.py").write_text(textwrap.dedent(FAKE_INTEGRATION))
    (integration_dir / "sensor.py").write_text(textwrap.dedent(FAKE_SENSOR_PLATFORM))
    (integration_dir / "manifest.json").write_text(
        f'{{"domain": "{DOMAIN}", "name": "Fake", "version": "1.0.0",'
        ' "config_flow": false, "requirements": []}'
    )
    integration_manager._integrations[DOMAIN] = IntegrationInfo(
        domain=DOMAIN,
        name="Fake",
        version="1.0.0",
        description="Fake lifecycle integration",
        source="local",
        repository_url="https://example.com/fake_lifecycle",
        enabled=True,
    )

    yield integration_loader
    _reset_custom_components()


async def _setup(hass, loader):
    entry = ConfigEntry(entry_id="lifecycle_entry", version=1, domain=DOMAIN, title="Fake")
    await hass.config_entries.async_add(entry)
    assert await loader.setup_integration(entry) is True
    return entry


async def test_prepare_hook_runs_before_the_add(hass, loader):
    """The hook gets hass + platform, and the add only completes after it."""
    await _setup(hass, loader)

    platform_module = sys.modules[f"custom_components.{DOMAIN}.sensor"]
    calls = platform_module.CALLS

    prepare_calls = [c for c in calls if c[0] == "prepare"]
    added_calls = [c for c in calls if c[0] == "added"]

    assert {c[1] for c in prepare_calls} == {
        "ok_sensor",
        "raising_sensor",
        "disabled_sensor",
    }
    # hass and a platform object are already assigned when the hook runs.
    assert all(c[2] for c in prepare_calls)
    assert all(c[3] is not None for c in prepare_calls)
    assert prepare_calls[0][3].platform_name == "sensor"
    assert prepare_calls[0][3].config_entry.domain == DOMAIN

    # Each prepare for a completed add precedes its async_added_to_hass.
    assert [c[1] for c in added_calls] == ["ok_sensor", "disabled_sensor"]


async def test_prepare_hook_runs_for_disabled_by_default_entities(hass, loader):
    """HA runs the hook even for adds that will not complete.

    The shim deliberately still publishes disabled-by-default entities (with
    ``enabled_by_default: false``) so they can be enabled in HA, so their add
    completes; what matters here is that the prepare hook is not skipped.
    """
    await _setup(hass, loader)

    registry = EntityRegistry()
    assert registry.get("sensor.disabled_sensor") is not None
    entry = registry.get_registry_entry("sensor.disabled_sensor")
    assert entry.disabled is True


async def test_prepare_failure_aborts_only_that_entity(hass, loader):
    """Raising aborts the add, runs async_on_remove cleanup, and the rest land."""
    await _setup(hass, loader)

    registry = EntityRegistry()
    assert registry.get("sensor.ok_sensor") is not None
    assert registry.get("sensor.raising_sensor") is None
    assert hass.states.get("sensor.raising_sensor") is None


async def test_base_hooks_exist_for_super_calls(hass):
    """Overrides can call super() on both hooks; the base ones are no-ops."""

    class _Sub(Entity):
        def __init__(self):
            super().__init__()
            self.seen = []

        async def async_prepare_to_add_to_hass(self):
            await super().async_prepare_to_add_to_hass()
            self.seen.append("prepare")

        def async_entity_id_changed(self, old_entity_id):
            super().async_entity_id_changed(old_entity_id)
            self.seen.append(old_entity_id)

    entity = _Sub()
    entity.hass = hass
    await entity.async_prepare_to_add_to_hass()
    entity.async_entity_id_changed("sensor.old")
    assert entity.seen == ["prepare", "sensor.old"]

    # The base implementations are safe to call directly.
    await Entity().async_prepare_to_add_to_hass()
    Entity().async_entity_id_changed("sensor.old")
    Entity().async_registry_entry_updated()


async def test_entity_id_change_happens_in_place(hass):
    """async_update_entity(new_entity_id=...) renames in place (HA 2026.11)."""
    registry = EntityRegistry()
    registry.setup(hass)

    events = []

    class _Hooked(Entity):
        async def async_added_to_hass(self):
            events.append("added")

        async def async_will_remove_from_hass(self):
            events.append("removed")

        def async_registry_entry_updated(self):
            events.append("registry_entry_updated")

        def async_entity_id_changed(self, old_entity_id):
            super().async_entity_id_changed(old_entity_id)
            events.append(("entity_id_changed", old_entity_id, self.entity_id))

    entity = _Hooked()
    entity.entity_id = "sensor.old_name"
    entity._attr_unique_id = "uid1"
    entity._attr_config_entry_id = "entry1"
    entity.hass = hass
    entity._added = True
    registry.register(entity)
    entity.async_write_ha_state()
    assert hass.states.get("sensor.old_name") is not None

    registry.async_update_entity("sensor.old_name", new_entity_id="sensor.new_name")

    # Renamed in place: no remove/add cycle, hook ran with the old id.
    assert entity.entity_id == "sensor.new_name"
    assert events == [
        "registry_entry_updated",
        ("entity_id_changed", "sensor.old_name", "sensor.new_name"),
    ]
    assert registry.get("sensor.old_name") is None
    assert registry.get("sensor.new_name") is entity
    assert registry.get_registry_entry("sensor.new_name").entity_id == "sensor.new_name"
    assert hass.states.get("sensor.old_name") is None
    assert hass.states.get("sensor.new_name") is not None


async def test_entity_id_change_hook_errors_do_not_block_the_rename(hass):
    """HA logs hook failures and still completes the rename."""
    registry = EntityRegistry()
    registry.setup(hass)

    class _Boom(Entity):
        def async_entity_id_changed(self, old_entity_id):
            raise RuntimeError("boom")

    entity = _Boom()
    entity.entity_id = "sensor.boom_old"
    entity._attr_unique_id = "uid2"
    entity._attr_config_entry_id = "entry1"
    entity.hass = hass
    entity._added = True
    registry.register(entity)

    registry.async_update_entity("sensor.boom_old", new_entity_id="sensor.boom_new")

    assert entity.entity_id == "sensor.boom_new"
    assert registry.get("sensor.boom_new") is entity
    assert hass.states.get("sensor.boom_new") is not None


def test_entity_id_change_rejects_domain_change(hass):
    """A rename may not move an entity to another domain."""
    registry = EntityRegistry()
    registry.setup(hass)
    entity = Entity()
    entity.entity_id = "sensor.one"
    entity._attr_unique_id = "uid3"
    entity._attr_config_entry_id = "entry1"
    entity.hass = hass
    registry.register(entity)

    with pytest.raises(ValueError):
        registry.async_update_entity("sensor.one", new_entity_id="binary_sensor.one")
    assert entity.entity_id == "sensor.one"
