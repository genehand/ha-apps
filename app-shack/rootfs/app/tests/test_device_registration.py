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
from types import SimpleNamespace
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

FAKE_CHILD_FIRST_PLATFORM = '''
"""Fake platform that adds its child-device entity before the parent's."""

from homeassistant.helpers.device_registry import DeviceInfo
from shim.platforms.sensor import SensorEntity

DOMAIN = "fake_devices"


class _FakeSensor(SensorEntity):
    """Sensor whose device_info is a property, like the integration entities."""

    _attr_native_value = 1

    def __init__(self, unique_id, name, device_info_factory):
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._device_info_factory = device_info_factory

    @property
    def device_info(self):
        return self._device_info_factory()


async def async_setup_entry(hass, entry, async_add_entities):
    """Add the sub-device entity first: entity order must not matter."""

    def subdevice_info():
        return DeviceInfo(
            identifiers={(DOMAIN, "local_sub1")},
            name="Sub device",
            # Deprecated tuple form: the gateway row may not exist yet.
            via_device=(DOMAIN, "local_gw1"),
        )

    def gateway_info():
        return DeviceInfo(
            identifiers={(DOMAIN, "local_gw1")},
            name="Gateway",
        )

    async_add_entities(
        [
            _FakeSensor("sub_sensor", "Sub device sensor", subdevice_info),
            _FakeSensor("gw_sensor", "Gateway sensor", gateway_info),
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


@pytest.mark.asyncio
async def test_discovery_device_block_reads_registry_through_upstream_api(hass, loader):
    """The discovery device block must resolve through the registry's real API.

    ``build_mqtt_device_config`` used to find its fallback row by iterating
    ``registry._devices`` - the shim's private dict, which a real
    ``DeviceRegistry`` does not have (``docs/tier-c-spike.md`` §4/§6.5), so the
    fallback silently became a no-op. It now goes through
    ``async_get_device_by_identifier(identifier, config_entry_id)`` with
    ``registry.devices`` as the entry-agnostic fallback.

    The stand-in below implements *only* the upstream surface - ``devices`` as a
    collection plus the entry-scoped lookup, no ``_devices`` - which is what the
    old code needed and the real registry will not provide.
    """
    from shim.entity import _async_lookup_registry_device, build_mqtt_device_config

    class UpstreamOnlyRegistry:
        def __init__(self):
            self.scoped_calls = []
            self._rows = [
                SimpleNamespace(
                    identifiers={(DOMAIN, "local_gw1")},
                    name="Gateway",
                    manufacturer="Tuya",
                    model="GW model",
                    sw_version="1.2.3",
                )
            ]

        @property
        def devices(self):
            return list(self._rows)

        def async_get_device_by_identifier(self, identifier, config_entry_id):
            self.scoped_calls.append((identifier, config_entry_id))
            for row in self._rows:
                if identifier in row.identifiers:
                    return row
            return None

    registry = UpstreamOnlyRegistry()
    row = _async_lookup_registry_device(
        registry, {(DOMAIN, "local_gw1")}, "fake_devices_entry"
    )
    assert row is not None and row.name == "Gateway"
    assert registry.scoped_calls == [((DOMAIN, "local_gw1"), "fake_devices_entry")]

    # Without a config entry id the scoped lookup is not attempted at all; the
    # entry-agnostic ``devices`` fallback still finds the row.
    registry = UpstreamOnlyRegistry()
    row = _async_lookup_registry_device(registry, {(DOMAIN, "local_gw1")})
    assert row is not None and row.sw_version == "1.2.3"
    assert registry.scoped_calls == []

    # Non-tuple identifiers (bare strings are tolerated in the payload) cannot be
    # looked up and must not raise.
    assert _async_lookup_registry_device(registry, {"bare-string"}, "entry") is None

    # End to end through the shim's own registry: an identifiers-only
    # device_info gains the row's metadata once the device exists.
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    from homeassistant.helpers import device_registry as dr

    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            GATEWAY_IDENTIFIER, entry.entry_id
        )
        is not None
    )

    block = build_mqtt_device_config(
        {"identifiers": {GATEWAY_IDENTIFIER}}, hass, entry.entry_id
    )
    assert block == {
        "identifiers": [f"{DOMAIN}-local_gw1"],
        "name": "Gateway",
        "manufacturer": "Tuya",
        "model": "GW model",
    }


@pytest.mark.asyncio
async def test_entity_discovery_block_passes_its_config_entry(hass, loader):
    """``build_entity_mqtt_device_config`` must scope the lookup to the entity's entry.

    The row ``register_entity_device`` created is owned by the entity's config
    entry, and the entry-scoped lookup is the documented one, so the wrapper has
    to hand over ``_attr_config_entry_id`` instead of relying on the fallback.
    """
    from unittest.mock import patch

    from shim.entity import build_entity_mqtt_device_config

    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    entity = next(
        entity
        for entity in loader.get_entities()
        if entity.entity_id == "sensor.gw_sensor"
    )
    # The platform stamps the entry id on every entity it adds.
    assert entity._attr_config_entry_id == entry.entry_id

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    real_lookup = registry.async_get_device_by_identifier
    calls = []

    def spy(identifier, config_entry_id):
        calls.append((identifier, config_entry_id))
        return real_lookup(identifier, config_entry_id)

    with patch.object(registry, "async_get_device_by_identifier", side_effect=spy):
        # An identifiers-only device_info (name/manufacturer dropped) must still
        # get them back from the row the entity owns.
        entity._attr_device_info = {"identifiers": {GATEWAY_IDENTIFIER}}
        block = build_entity_mqtt_device_config(entity)

    assert calls == [(GATEWAY_IDENTIFIER, entry.entry_id)]
    assert block["name"] == "Gateway"
    assert block["manufacturer"] == "Tuya"
    assert block["model"] == "GW model"


@pytest.mark.asyncio
async def test_subdevice_discovery_block_links_its_gateway(hass, loader):
    """The sub-device's discovery block carries the parent link (HA parity).

    HA's MQTT discovery has no ``via_device_id``: the parent link is
    ``device.via_device``, a *string identifier* of the parent
    (``mqtt.entity._via_device_identifier`` turns it into
    ``("mqtt", <string>)``, matching the identifiers the parent publishes). The
    block used to drop the link entirely, so every multi-channel / hub-attached
    device showed up as an unrelated root device in the user's Home Assistant -
    meross_lan's per-channel devices for an MSS620 look like three devices.
    """
    import json
    from unittest.mock import MagicMock

    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    by_entity_id = {entity.entity_id: entity for entity in loader.get_entities()}
    mock_mqtt = MagicMock()
    mock_mqtt.is_connected.return_value = True
    hass._mqtt_client = mock_mqtt

    async def _discovery_config(entity, topic):
        mock_mqtt.publish.reset_mock()
        await entity._publish_mqtt_discovery()
        for call in mock_mqtt.publish.call_args_list:
            if call.args[0] == topic:
                return json.loads(call.args[1])
        raise AssertionError(f"no discovery published to {topic}")

    gateway_block = (
        await _discovery_config(
            by_entity_id["sensor.gw_sensor"],
            "homeassistant/sensor/gw-sensor/config",
        )
    )["device"]
    subdevice_block = (
        await _discovery_config(
            by_entity_id["sensor.sub_sensor"],
            "homeassistant/sensor/sub-sensor/config",
        )
    )["device"]

    # The parent must not point anywhere.
    assert "via_device" not in gateway_block
    # The child points at the identifier the gateway publishes in its own block.
    assert subdevice_block["via_device"] == f"{DOMAIN}-local_gw1"
    assert subdevice_block["via_device"] in gateway_block["identifiers"]


@pytest.mark.asyncio
async def test_via_device_follows_a_multi_channel_registry_row(hass):
    """The meross_lan shape: identifiers-only entity, link only in the registry.

    meross_lan registers a per-channel device itself with ``via_device_id`` and
    hands the channel entity a ``DeviceInfo(identifiers=...)``; nothing about the
    link is visible in that ``device_info``. The discovery block has to pick it
    up from the registry row so the channel lands under the plug, not next to it.
    """
    from shim.entity import build_mqtt_device_config

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id="ml_entry",
        identifiers={("meross_lan", "mss620")},
        name="Desk plug",
        manufacturer="Meross",
        model="mss620",
    )
    channel = registry.async_get_or_create(
        config_entry_id="ml_entry",
        identifiers={("meross_lan", "mss620_1")},
        name="Desk plug Channel 1",
        manufacturer="Meross",
        model="mss620",
        via_device_id=parent.id,
    )

    block = build_mqtt_device_config(
        {"identifiers": {("meross_lan", "mss620_1")}}, hass, "ml_entry"
    )
    assert block["via_device"] == "meross_lan-mss620"
    assert block["identifiers"] == ["meross_lan-mss620_1"]
    assert block["name"] == "Desk plug Channel 1"

    # The parent stays a root device.
    parent_block = build_mqtt_device_config(
        {"identifiers": {("meross_lan", "mss620")}}, hass, "ml_entry"
    )
    assert "via_device" not in parent_block
    assert channel.via_device_id == parent.id


@pytest.mark.asyncio
async def test_via_device_from_device_info_without_a_parent_row(hass):
    """A deprecated ``via_device`` tuple must be emitted even before the parent exists.

    HA's own discovery path never looks the parent up: ``ensure_via_device_exists``
    stub-creates it, because MQTT discovery order is not deterministic. Doing a
    registry lookup instead would drop the link for whichever device is announced
    first.
    """
    from shim.entity import build_mqtt_device_config

    block = build_mqtt_device_config(
        {
            "identifiers": {("localtuya", "sub1")},
            "via_device": ("localtuya", "gw1"),
        },
        hass,
        "tuya_entry",
    )
    assert block["via_device"] == "localtuya-gw1"

    # Identifiers that cannot render as a parent identifier are dropped rather
    # than published (a non-matching value would create an orphan stub device).
    for malformed in ("gw1", ("localtuya",), ("localtuya", "gw1", "extra"), 42):
        block = build_mqtt_device_config(
            {"identifiers": {("localtuya", "sub1")}, "via_device": malformed},
            hass,
            "tuya_entry",
        )
        assert "via_device" not in block, malformed


@pytest.mark.asyncio
async def test_via_device_pick_is_stable_and_never_self_referential(hass):
    """One parent, several identifiers: pick deterministically, never itself."""
    from shim.entity import build_mqtt_device_config

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id="e1",
        identifiers={("dl", "zzz"), ("dl", "aaa")},
        name="Parent",
    )
    registry.async_get_or_create(
        config_entry_id="e1",
        identifiers={("dl", "child")},
        name="Child",
        via_device_id=parent.id,
    )

    blocks = {
        build_mqtt_device_config({"identifiers": {("dl", "child")}}, hass, "e1")[
            "via_device"
        ]
        for _ in range(5)
    }
    assert blocks == {"dl-aaa"}

    # A row linked to itself (possible after a bad integration update) must not
    # publish a self-reference; HA's registry migration clears those too.
    self_linked = registry.async_get_or_create(
        config_entry_id="e1", identifiers={("dl", "loner")}, name="Loner"
    )
    self_linked.via_device_id = self_linked.id
    assert "via_device" not in build_mqtt_device_config(
        {"identifiers": {("dl", "loner")}}, hass, "e1"
    )


@pytest.mark.asyncio
async def test_reload_button_prefers_the_root_device(hass, temp_data_dir):
    """A per-entry reload button must not land on a child device.

    ``_get_reload_button_device_info`` inherits the device of the first entity it
    finds, and for an integration whose entities are per channel / per sub-device
    that first entity can be a child (meross_lan's MSS620 outlets publish their
    reload button on "<plug> Channel 1"). Preferring a device without a parent
    keeps the plug-level control on the plug, whatever the entity order.
    """
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    _reset_custom_components_imports()
    _write_fake_integration(shim_dir, FAKE_CHILD_FIRST_PLATFORM)
    integration_manager._integrations[DOMAIN] = IntegrationInfo(
        domain=DOMAIN,
        name="Fake Devices",
        version="1.0.0",
        description="Child entity first",
        source="local",
        repository_url="https://example.com/fake_devices",
        enabled=True,
    )

    entry = await _add_entry(hass)
    assert await integration_loader.setup_integration(entry) is True

    from shim.entity import build_entity_mqtt_device_config

    _dr, registry = _device_registry(hass)
    gateway = registry.async_get_device_by_identifier(GATEWAY_IDENTIFIER, entry.entry_id)
    subdevice = registry.async_get_device_by_identifier(
        SUBDEVICE_IDENTIFIER, entry.entry_id
    )
    assert gateway is not None and subdevice is not None
    # This entity was added first, so its parent row did not exist yet: the
    # deprecated tuple is what marks it as a child (HA leaves via_device_id unset
    # in the same situation), and the discovery block still transports it.
    assert subdevice.via_device == (DOMAIN, "local_gw1")

    button = integration_loader._reload_buttons[entry.entry_id]
    assert button.device_entry.id == gateway.id
    assert "via_device" not in build_entity_mqtt_device_config(button)

    # The sub-device entity's own discovery block keeps the link.
    sub_entity = next(
        entity
        for entity in integration_loader.get_entities()
        if entity.entity_id == "sensor.sub_sensor"
    )
    assert (
        build_entity_mqtt_device_config(sub_entity)["via_device"]
        == f"{DOMAIN}-local_gw1"
    )
