"""Device registration while adding entities (``entity.device_info`` -> registry).

Regression tests: localtuya 2026.10.0 links a sub-device to
its gateway like this (``custom_components/localtuya/entity.py``)::

    device_info["via_device_id"] = dr.async_get_device_id_by_identifier(
        self.hass, gateway_identifier, config_entry_id=self._device._entry.entry_id,
    )

``async_get_device_id_by_identifier`` raises ``ValueError`` when the gateway has
no ``DeviceEntry``. Upstream HA is fine because its entity platform creates the
device from ``entity.device_info`` while adding the entity; the shim never did,
so any gateway-attached device broke when it was added.

The fake integration below reproduces that shape: a gateway entity, then a
sub-device entity whose ``device_info`` resolves through the registry. Its
sub-device ``device_info`` is a *property* (evaluated at add time, not at
construction) exactly like localtuya's.
"""

import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

DOMAIN = "fake_devices"
GATEWAY_IDENTIFIER = (DOMAIN, "local_gw1")
SUBDEVICE_IDENTIFIER = (DOMAIN, "local_sub1")

FAKE_INTEGRATION = '''
"""Fake gateway/sub-device integration used by the device registration tests."""

DOMAIN = "fake_devices"


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass, entry):
    return True
'''

FAKE_SENSOR_PLATFORM = '''
"""Fake sensor platform mirroring localtuya's gateway + sub-device pattern."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from shim.platforms.sensor import SensorEntity

DOMAIN = "fake_devices"


class _FakeSensor(SensorEntity):
    """Sensor whose device_info is computed when the entity is added."""

    _attr_native_value = 1

    def __init__(self, unique_id, name, device_info_factory):
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._device_info_factory = device_info_factory

    @property
    def device_info(self):
        # A property, like localtuya's LocalTuyaEntity.device_info: this is what
        # raises when the gateway device row is missing.
        return self._device_info_factory()


async def async_setup_entry(hass, entry, async_add_entities):
    """Add a gateway entity and a sub-device entity linked through the registry."""

    def gateway_info():
        return DeviceInfo(
            identifiers={(DOMAIN, "local_gw1")},
            name="Gateway",
            manufacturer="Tuya",
            model="GW model",
        )

    def subdevice_info():
        return DeviceInfo(
            identifiers={(DOMAIN, "local_sub1")},
            name="Sub device",
            manufacturer="Tuya",
            # HA >= 2026.8 path: raises ValueError if the gateway row is absent.
            via_device_id=dr.async_get_device_id_by_identifier(
                hass, (DOMAIN, "local_gw1"), config_entry_id=entry.entry_id
            ),
        )

    async_add_entities(
        [
            _FakeSensor("gw_sensor", "Gateway sensor", gateway_info),
            _FakeSensor("sub_sensor", "Sub device sensor", subdevice_info),
        ]
    )
'''

FAKE_DEVICE_INFO_LESS_PLATFORM = '''
"""Fake platform whose entities carry no device_info at all."""

from shim.platforms.sensor import SensorEntity

DOMAIN = "fake_devices"


class _NoDeviceSensor(SensorEntity):
    _attr_native_value = 1


async def async_setup_entry(hass, entry, async_add_entities):
    sensor = _NoDeviceSensor()
    sensor._attr_unique_id = "no_device_sensor"
    sensor._attr_name = "No device sensor"
    async_add_entities([sensor])
'''


def _reset_custom_components_imports():
    """Drop cached ``custom_components`` packages from earlier temp data dirs."""
    for name in list(sys.modules):
        if name == "custom_components" or name.startswith("custom_components."):
            del sys.modules[name]
    import importlib

    importlib.invalidate_caches()


@pytest.fixture(autouse=True)
def _isolated_custom_components():
    """Keep the temp-dir integration package out of other test modules.

    ``custom_components`` is a real package imported from whichever temp dir
    loaded it first; leaving it in ``sys.modules`` makes every later test that
    imports an integration (e.g. ``tests/test_device_info.py``) fail with
    ``ModuleNotFoundError``.
    """
    _reset_custom_components_imports()
    yield
    _reset_custom_components_imports()


@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
async def hass(temp_data_dir):
    hass = HomeAssistant(temp_data_dir)
    setup_import_patching(hass).patch()
    return hass


def _write_fake_integration(shim_dir, sensor_platform=FAKE_SENSOR_PLATFORM):
    integration_dir = shim_dir / "custom_components" / DOMAIN
    integration_dir.mkdir(parents=True)
    (integration_dir / "__init__.py").write_text(textwrap.dedent(FAKE_INTEGRATION))
    (integration_dir / "sensor.py").write_text(textwrap.dedent(sensor_platform))
    (integration_dir / "manifest.json").write_text(
        f'{{"domain": "{DOMAIN}", "name": "Fake Devices", "version": "1.0.0",'
        ' "config_flow": false, "requirements": []}'
    )


@pytest.fixture
async def loader(hass, temp_data_dir):
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    _reset_custom_components_imports()
    _write_fake_integration(shim_dir)

    integration_manager._integrations[DOMAIN] = IntegrationInfo(
        domain=DOMAIN,
        name="Fake Devices",
        version="1.0.0",
        description="Fake gateway/sub-device integration",
        source="local",
        repository_url="https://example.com/fake_devices",
        enabled=True,
    )
    return integration_loader


async def _add_entry(hass, entry_id="fake_devices_entry"):
    entry = ConfigEntry(
        entry_id=entry_id,
        version=1,
        domain=DOMAIN,
        title="Fake Devices",
    )
    await hass.config_entries.async_add(entry)
    return entry


def _device_registry(hass):
    from homeassistant.helpers import device_registry as dr

    return dr, dr.async_get(hass)


@pytest.mark.asyncio
async def test_gateway_entity_creates_a_device_entry(hass, loader):
    """Adding an entity with device_info registers its device (HA parity)."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    dr, registry = _device_registry(hass)
    gateway = registry.async_get_device_by_identifier(
        GATEWAY_IDENTIFIER, entry.entry_id
    )
    assert gateway is not None, "gateway entity did not register a device"
    assert gateway.identifiers == {GATEWAY_IDENTIFIER}
    assert gateway.config_entry_id == entry.entry_id
    assert gateway.name == "Gateway"
    assert gateway.manufacturer == "Tuya"
    assert gateway.model == "GW model"


@pytest.mark.asyncio
async def test_subdevice_entity_resolves_via_device_id(hass, loader):
    """A sub-device entity links to its gateway instead of raising.

    This is the regression: before the fix the gateway had no device row, so
    ``async_get_device_id_by_identifier`` raised ``ValueError`` mid platform
    setup and the sub-device (plus anything after it) was never added.
    """
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    dr, registry = _device_registry(hass)
    gateway = registry.async_get_device_by_identifier(
        GATEWAY_IDENTIFIER, entry.entry_id
    )
    assert dr.async_get_device_id_by_identifier(
        hass, GATEWAY_IDENTIFIER, config_entry_id=entry.entry_id
    ) == gateway.id

    subdevice = registry.async_get_device_by_identifier(
        SUBDEVICE_IDENTIFIER, entry.entry_id
    )
    assert subdevice is not None, "sub-device entity did not register a device"
    assert subdevice.via_device_id == gateway.id
    assert subdevice.name == "Sub device"

    # Both entities made it into the state machine, not just the gateway.
    entity_ids = {entity.entity_id for entity in loader.get_entities()}
    assert "sensor.gw_sensor" in entity_ids
    assert "sensor.sub_sensor" in entity_ids


@pytest.mark.asyncio
async def test_reload_button_joins_its_integration_device(hass, loader):
    """The shim-owned reload button registers a device too (internal path)."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    _dr, registry = _device_registry(hass)
    button = loader._reload_buttons[entry.entry_id]
    assert button.device_entry is not None
    # The button inherits the integration's device_info, so it lands on the
    # gateway device rather than a second "Shack - ..." row.
    gateway = registry.async_get_device_by_identifier(
        GATEWAY_IDENTIFIER, entry.entry_id
    )
    assert button.device_entry.id == gateway.id


@pytest.mark.asyncio
async def test_entity_registry_entry_carries_the_device_id(hass, loader):
    """Registry entries expose ``device_id`` (localtuya reads it)."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    from shim.entity import EntityRegistry

    _dr, registry = _device_registry(hass)
    gateway = registry.async_get_device_by_identifier(
        GATEWAY_IDENTIFIER, entry.entry_id
    )
    registry_entry = EntityRegistry().get_registry_entry("sensor.gw_sensor")
    assert registry_entry is not None
    assert registry_entry.device_id == gateway.id


@pytest.mark.asyncio
async def test_reload_does_not_duplicate_devices(hass, loader):
    """Unload -> setup -> unload keeps the device row count stable."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    _dr, registry = _device_registry(hass)
    after_first_setup = dict(registry._devices)

    assert await loader.unload_integration(entry) is True
    assert await loader.setup_integration(entry) is True
    assert await loader.unload_integration(entry) is True

    assert set(registry._devices) == set(after_first_setup)
    assert len(registry._devices) == 2, registry._devices


@pytest.mark.asyncio
async def test_removing_the_config_entry_removes_its_devices(hass, loader):
    """Config entry removal clears the device rows (HA parity)."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    _dr, registry = _device_registry(hass)
    assert len(registry._devices) == 2

    await loader.unload_integration(entry)
    await hass.config_entries.async_remove(entry.entry_id)
    assert registry._devices == {}


@pytest.mark.asyncio
async def test_entity_without_device_info_registers_nothing(hass, temp_data_dir):
    """An entity whose device_info is empty must not create a fallback device."""
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    _reset_custom_components_imports()
    _write_fake_integration(shim_dir, FAKE_DEVICE_INFO_LESS_PLATFORM)
    integration_manager._integrations[DOMAIN] = IntegrationInfo(
        domain=DOMAIN,
        name="Fake Devices",
        version="1.0.0",
        description="No device info",
        source="local",
        repository_url="https://example.com/fake_devices",
        enabled=True,
    )

    entry = await _add_entry(hass)
    assert await integration_loader.setup_integration(entry) is True

    _dr, registry = _device_registry(hass)
    # Only the reload button's Shack fallback device exists: the platform entity
    # carries no device_info, so nothing is registered for it.
    assert list(registry._devices) == [f"shack_{DOMAIN}"]


@pytest.mark.asyncio
async def test_discovery_device_block_unchanged_by_registration(hass, loader):
    """Registering devices must not add fields to the MQTT discovery block.

    ``build_mqtt_device_config`` falls back to the registry for name /
    manufacturer / model / sw_version when ``device_info`` omits them. Now that
    rows exist, that fallback starts returning data - so pin the shape: an
    entity whose ``device_info`` supplies identifiers + name/manufacturer/model
    still publishes exactly those fields, and a device whose registry row has no
    name does not gain one (the shim deliberately does not name devices after
    their config entry the way HA does).
    """
    import json
    from unittest.mock import MagicMock

    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    entity = next(
        entity
        for entity in loader.get_entities()
        if entity.entity_id == "sensor.gw_sensor"
    )
    mock_mqtt = MagicMock()
    mock_mqtt.is_connected.return_value = True
    hass._mqtt_client = mock_mqtt
    await entity._publish_mqtt_discovery()

    discovery_topic = "homeassistant/sensor/gw-sensor/config"
    config = None
    for call in mock_mqtt.publish.call_args_list:
        if call.args[0] == discovery_topic:
            config = json.loads(call.args[1])
            break
    assert config is not None, f"no discovery published to {discovery_topic}"
    assert config["device"] == {
        "identifiers": [f"{DOMAIN}-local_gw1"],
        "name": "Gateway",
        "manufacturer": "Tuya",
        "model": "GW model",
    }

    # An identifiers-only device_info produces an identifiers-only block, even
    # though the registry is populated now.
    from shim.entity import build_mqtt_device_config

    orphan_block = build_mqtt_device_config(
        {"identifiers": {(DOMAIN, "local_orphan")}}, hass
    )
    assert orphan_block == {"identifiers": [f"{DOMAIN}-local_orphan"]}


@pytest.mark.asyncio
async def test_failing_device_info_does_not_abort_platform_setup(hass, temp_data_dir):
    """One entity with a raising device_info must not lose the whole platform.

    ``register_entity_device`` logs loudly and skips the device; the remaining
    entities of the platform still get added.
    """
    from shim.entity import register_entity_device

    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader
    _reset_custom_components_imports()
    _write_fake_integration(shim_dir, FAKE_DEVICE_INFO_LESS_PLATFORM)
    integration_manager._integrations[DOMAIN] = IntegrationInfo(
        domain=DOMAIN,
        name="Fake Devices",
        version="1.0.0",
        description="No device info",
        source="local",
        repository_url="https://example.com/fake_devices",
        enabled=True,
    )

    class Exploding:
        entity_id = "sensor.exploding"

        @property
        def device_info(self):
            raise ValueError("no device for you")

    assert register_entity_device(hass, Exploding(), "entry1") is None
