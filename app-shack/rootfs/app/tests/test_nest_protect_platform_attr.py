"""Regression tests for shim gaps that broke nest_protect 0.5.0 entity setup.

nest_protect 0.5.0 (HA 2026.10 minimum) reads
``self.platform.config_entry.runtime_data`` inside
``NestEntity.async_added_to_hass`` and calls
``entity_registry.async_entries_for_device`` plus
``config_entries.async_schedule_reload``. Any of those missing makes every
entity of the platform fail to add, silently, because the per-platform task
result was discarded.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

from shim import HomeAssistant
from shim.entity import Entity, EntityRegistry, async_get_entity_platform
from shim.import_patch import setup_import_patching
from shim.models import ConfigEntry

NEST_DIR = Path(__file__).parent.parent / "data" / "shim"
NEST_COMPONENT = NEST_DIR / "custom_components" / "nest_protect"


@pytest.fixture
async def hass():
    with tempfile.TemporaryDirectory() as tmpdir:
        hass = HomeAssistant(Path(tmpdir))
        setup_import_patching(hass).patch()
        yield hass


@pytest.fixture
def nest_importable():
    """Make custom_components.nest_protect importable, then clean up."""
    prefixes = ("custom_components",)
    for name in [n for n in sys.modules if n.startswith(prefixes)]:
        sys.modules.pop(name, None)
    added = str(NEST_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(NEST_DIR))
    yield
    if added and str(NEST_DIR) in sys.path:
        sys.path.remove(str(NEST_DIR))
    for name in [n for n in sys.modules if n.startswith(prefixes)]:
        sys.modules.pop(name, None)


def test_entity_gets_platform_with_config_entry(hass):
    """A platform handed to an entity exposes the config entry it came from."""
    entry = ConfigEntry(entry_id="entry1", domain="nest_protect", title="Nest Protect")
    platform = async_get_entity_platform(hass, "sensor", entry)

    assert platform.platform_name == "sensor"
    assert platform.config_entry is entry
    assert platform.config_entry.runtime_data is None

    # Cached per (entry, platform), like HA's EntityPlatform.
    assert async_get_entity_platform(hass, "sensor", entry) is platform
    assert async_get_entity_platform(hass, "binary_sensor", entry) is not platform

    entity = Entity()
    entity.add_to_platform_start(hass, platform, None)
    assert entity.platform is platform
    assert entity.platform.config_entry is entry


def test_entity_registry_async_entries_for_device(hass):
    """The entity registry answers the per-device lookup nest_protect uses."""
    from homeassistant.helpers import entity_registry as er

    registry = EntityRegistry()
    registry.setup(hass)
    registry._reset_for_test()
    registry.setup(hass)

    enabled = Entity()
    enabled.entity_id = "sensor.enabled"
    enabled._attr_unique_id = "topaz.AAA-temperature"
    enabled._attr_config_entry_id = "entry1"
    enabled._attr_device_info = {"identifiers": {("nest_protect", "AAA")}}
    enabled.device_entry = type("Device", (), {"id": "dev1"})()
    registry.register(enabled)

    disabled = Entity()
    disabled.entity_id = "sensor.disabled"
    disabled._attr_unique_id = "topaz.AAA-co_test"
    disabled._attr_config_entry_id = "entry1"
    disabled.device_entry = type("Device", (), {"id": "dev1"})()
    disabled._attr_entity_registry_enabled_default = False
    registry.register(disabled)

    other = Entity()
    other.entity_id = "sensor.other"
    other._attr_unique_id = "topaz.BBB-temperature"
    other._attr_config_entry_id = "entry1"
    other.device_entry = type("Device", (), {"id": "dev2"})()
    registry.register(other)

    entries = registry.async_entries_for_device("dev1")
    assert {e.entity_id for e in entries} == {"sensor.enabled"}

    entries = registry.async_entries_for_device(
        "dev1", include_disabled_entities=True
    )
    assert {e.entity_id for e in entries} == {"sensor.enabled", "sensor.disabled"}

    # The stub is called as er.async_entries_for_device(er.async_get(hass), ...).
    stub_entries = er.async_entries_for_device(
        er.async_get(hass), "dev1", include_disabled_entities=True
    )
    assert {e.entity_id for e in stub_entries} == {
        "sensor.enabled",
        "sensor.disabled",
    }


async def test_config_entries_schedule_reload_dedupes(hass):
    """async_schedule_reload schedules one reload per entry, not one per call."""
    calls = []

    async def fake_reload(entry_id):
        calls.append(entry_id)

    hass.config_entries.async_reload = fake_reload

    hass.config_entries.async_schedule_reload("abc")
    hass.config_entries.async_schedule_reload("abc")
    await asyncio.sleep(0)

    assert calls == ["abc"]


async def test_async_entry_for_domain_unique_id(hass):
    """Config flows can look up an entry by domain + unique_id."""
    entry = ConfigEntry(
        entry_id="entry1", domain="nest_protect", title="Nest Protect"
    )
    entry.unique_id = "user.123"
    await hass.config_entries.async_add(entry)

    assert (
        hass.config_entries.async_entry_for_domain_unique_id(
            "nest_protect", "user.123"
        )
        is entry
    )
    assert (
        hass.config_entries.async_entry_for_domain_unique_id(
            "nest_protect", "user.456"
        )
        is None
    )
    assert (
        hass.config_entries.async_entry_for_domain_unique_id("other", "user.123")
        is None
    )
    await hass.config_entries.async_remove("entry1")


async def test_nest_entity_async_added_to_hass(hass, nest_importable):
    """NestEntity.async_added_to_hass works once the entity has its platform."""
    from custom_components.nest_protect.binary_sensor import (
        BINARY_SENSOR_DESCRIPTIONS,
        NestProtectBinarySensor,
    )
    from custom_components.nest_protect.pynest.models import Bucket

    entry = ConfigEntry(
        entry_id="entry1", domain="nest_protect", title="Nest Protect"
    )
    entry.runtime_data = type(
        "NestData", (), {"stream_health": _FakeStreamHealth()}
    )()
    await hass.config_entries.async_add(entry)

    bucket = Bucket(
        object_key="topaz.TEST",
        object_revision=1,
        object_timestamp=1,
        type="topaz",
        value={
            "description": "Hallway",
            "smoke_status": 0,
            "co_status": 0,
            "wired_or_battery": 1,
            "where_id": "where1",
        },
    )
    description = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key in bucket.value)
    entity = NestProtectBinarySensor(bucket, description, {"where1": "Hallway"}, None)
    entity.entity_id = "binary_sensor.topaz_test_smoke_status"
    entity.add_to_platform_start(
        hass, async_get_entity_platform(hass, "binary_sensor", entry), None
    )
    entity.hass = hass

    await entity.async_added_to_hass()

    assert entity._stream_health is entry.runtime_data.stream_health
    await hass.config_entries.async_remove("entry1")


class _FakeStreamHealth:
    """Stand-in for nest_protect's StreamHealth."""

    def __init__(self):
        self.signal = "nest_protect_stream_health_entry1"
        self.available = True


# ---------------------------------------------------------------------------
# End-to-end through the real platform forwarding path (registries._do_add).
# ---------------------------------------------------------------------------

import importlib  # noqa: E402
import textwrap  # noqa: E402
import logging  # noqa: E402

from shim.integrations.loader import IntegrationLoader  # noqa: E402
from shim.integrations.manager import IntegrationInfo, IntegrationManager  # noqa: E402
from shim.storage import Storage

FAKE_DOMAIN = "fake_runtime_data"

FAKE_INTEGRATION = '''
"""Fake integration shaped like nest_protect >= 0.5.0."""

async def async_setup_entry(hass, entry):
    entry.runtime_data = type("Data", (), {"marker": "runtime"})()
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass, entry):
    return True
'''

FAKE_SENSOR_PLATFORM = '''
"""Entities that read entry.runtime_data through self.platform, like nest_protect."""

from shim.platforms.sensor import SensorEntity

DOMAIN = "fake_runtime_data"


class _RuntimeDataSensor(SensorEntity):
    _attr_native_value = 1
    _attr_should_poll = False

    def __init__(self, unique_id, explode=False):
        self._attr_unique_id = unique_id
        self._attr_name = unique_id.replace("_", " ").title()
        self._explode = explode

    async def async_added_to_hass(self):
        # nest_protect: entry_data = self.platform.config_entry.runtime_data
        data = self.platform.config_entry.runtime_data
        self._attr_native_value = 42 if data.marker == "runtime" else 0
        if self._explode:
            raise RuntimeError("boom")


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([_RuntimeDataSensor("good_sensor"), _RuntimeDataSensor("bad_sensor", explode=True)])
'''


def _reset_custom_components():
    for name in [n for n in sys.modules if n == "custom_components" or n.startswith("custom_components.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


@pytest.fixture
def fake_loader(temp_data_dir):
    _reset_custom_components()
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    hass_holder = {}

    def _make(hass):
        hass_holder["hass"] = hass
        loader = IntegrationLoader(hass, integration_manager)
        hass.data["integration_loader"] = loader

        integration_dir = shim_dir / "custom_components" / FAKE_DOMAIN
        integration_dir.mkdir(parents=True)
        (integration_dir / "__init__.py").write_text(textwrap.dedent(FAKE_INTEGRATION))
        (integration_dir / "sensor.py").write_text(textwrap.dedent(FAKE_SENSOR_PLATFORM))
        (integration_dir / "manifest.json").write_text(
            f'{{"domain": "{FAKE_DOMAIN}", "name": "Fake", "version": "1.0.0",'
            ' "config_flow": false, "requirements": []}'
        )
        integration_manager._integrations[FAKE_DOMAIN] = IntegrationInfo(
            domain=FAKE_DOMAIN,
            name="Fake",
            version="1.0.0",
            description="Fake runtime_data integration",
            source="local",
            repository_url="https://example.com/fake_runtime_data",
            enabled=True,
        )
        return loader

    yield _make
    _reset_custom_components()


@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


async def test_platform_forwarding_gives_entities_their_config_entry(
    hass, fake_loader
):
    """A platform entity sees self.platform.config_entry during async_added_to_hass.

    Before the shim fix this raised AttributeError on the first entity and every
    sensor of the platform silently disappeared.
    """
    loader = fake_loader(hass)
    entry = ConfigEntry(
        entry_id="fake_entry", version=1, domain=FAKE_DOMAIN, title="Fake"
    )
    await hass.config_entries.async_add(entry)

    assert await loader.setup_integration(entry) is True

    good = EntityRegistry().get("sensor.good_sensor")
    assert good is not None, "entity never added"
    assert good.platform is not None
    assert good.platform.config_entry is entry
    assert good.native_value == 42


async def test_entity_failure_is_logged_and_isolated(hass, fake_loader, caplog):
    """A raising entity is reported, and the rest of the platform still lands."""
    loader = fake_loader(hass)
    entry = ConfigEntry(
        entry_id="fake_entry", version=1, domain=FAKE_DOMAIN, title="Fake"
    )
    await hass.config_entries.async_add(entry)

    with caplog.at_level(logging.ERROR):
        assert await loader.setup_integration(entry) is True

    assert any(
        "Failed to add entity sensor.bad_sensor" in record.message
        for record in caplog.records
    ), [r.message for r in caplog.records]

    # HA runs the async_on_remove cleanup for an aborted add, so nothing is
    # left behind and the previous entity stays added.
    assert EntityRegistry().get("sensor.good_sensor") is not None
    assert EntityRegistry().get("sensor.bad_sensor") is None
