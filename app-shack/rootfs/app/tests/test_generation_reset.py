"""Module generation resets: an update must not run new code on old instances.

Regression tests for the meross_lan 5.8.0 -> 6.0.0 update in the shack.

meross_lan keeps a ``ComponentApi`` singleton in ``hass.data[DOMAIN]`` and only
drops it from an ``EVENT_HOMEASSISTANT_STOP`` listener. Home Assistant never
re-imports an integration in-process, so that is fine there; the shack does
(unload purges ``custom_components.<domain>`` from ``sys.modules``, and the
update path replaces the files on disk). The singleton survived both, so the new
``__init__`` was handed ``Device`` instances built by the old module::

    ERROR: shim.integrations.loader - Error setting up entry Meross LAN for
           meross_lan: 'Device' object has no attribute 'profile'
      File ".../helpers/mqtt_profile.py", line 623, in link
        assert (device.id not in self.linkeddevices) and (device.profile is not self)

6.0.0 renamed ``Device._profile`` to ``Device.profile``, and the stale 5.8.0
``Device`` has no ``profile`` at all.

Two independent causes, both covered here:

* ``unload_integration`` removed the domain from ``_loaded_integrations`` after
  the *first* entry, so every later entry of a multi-entry domain (meross_lan
  has one entry per device plus profiles and a hub) silently skipped its
  ``async_unload_entry``::

      ERROR: ... - Error during async_setup_entry for meross_lan: device already initialized

* the fresh module import inherited ``hass.data[domain]`` from the previous
  generation.
"""

import sys
import textwrap
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

# A multi-entry "hub" integration shaped like meross_lan: one hass.data singleton
# shared by every entry, each entry owning a device object.
FAKE_HUB_V1 = '''
"""Fake hub integration, version 1 (pre-rename Device)."""

DOMAIN = "fake_hub"
VERSION = 1

# Module-level, so a test can tell which entries got torn down by generation 1.
UNLOADED = []


class Device:
    """Stands in for meross_lan 5.8.0's Device: the attribute is _profile."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        self._profile = None
        self.closed = False

    def close(self):
        self.closed = True


class Api:
    """The hass.data[DOMAIN] singleton (meross_lan's ComponentApi)."""

    def __init__(self, hass):
        self.hass = hass
        self.devices = {}
        hass.data[DOMAIN] = self

    def active_devices(self):
        return (device for device in self.devices.values() if device)


def get_api(hass):
    try:
        return hass.data[DOMAIN]
    except KeyError:
        return Api(hass)


async def async_setup_entry(hass, entry):
    api = get_api(hass)
    assert entry.entry_id not in api.devices, "device already initialized"
    device = Device(entry.entry_id)
    api.devices[entry.entry_id] = device
    entry.runtime_data = device
    entry.async_on_unload(device.close)
    return True


async def async_unload_entry(hass, entry):
    UNLOADED.append(entry.entry_id)
    get_api(hass).devices.pop(entry.entry_id, None)
    return True
'''

# Version 2 renames the attribute and, like MQTTProfile.link(), reads it off
# every device the singleton already knows about.
FAKE_HUB_V2 = '''
"""Fake hub integration, version 2 (Device.profile)."""

DOMAIN = "fake_hub"
VERSION = 2

UNLOADED = []


class Device:

    def __init__(self, entry_id):
        self.entry_id = entry_id
        self.profile = None
        self.closed = False

    def close(self):
        self.closed = True


class Api:

    def __init__(self, hass):
        self.hass = hass
        self.devices = {}
        hass.data[DOMAIN] = self

    def active_devices(self):
        return (device for device in self.devices.values() if device)


def get_api(hass):
    try:
        return hass.data[DOMAIN]
    except KeyError:
        return Api(hass)


def link_devices(api):
    # Exactly what MQTTProfile.link does to devices it finds in the domain.
    for device in api.active_devices():
        device.profile = None


async def async_setup_entry(hass, entry):
    api = get_api(hass)
    assert entry.entry_id not in api.devices, "device already initialized"
    link_devices(api)
    device = Device(entry.entry_id)
    api.devices[entry.entry_id] = device
    entry.runtime_data = device
    entry.async_on_unload(device.close)
    return True


async def async_unload_entry(hass, entry):
    UNLOADED.append(entry.entry_id)
    get_api(hass).devices.pop(entry.entry_id, None)
    return True
'''


def _reset_custom_components_imports():
    """Drop cached custom_components packages from earlier temp data dirs."""
    for name in list(sys.modules):
        if name == "custom_components" or name.startswith("custom_components."):
            del sys.modules[name]
    import importlib

    importlib.invalidate_caches()


@pytest.fixture(autouse=True)
def _isolated_custom_components():
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


@pytest.fixture
def shim_dir(temp_data_dir):
    directory = temp_data_dir / "shim"
    (directory / "custom_components" / "fake_hub").mkdir(parents=True)
    return directory


@pytest.fixture
async def loader(hass, shim_dir):
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    _reset_custom_components_imports()
    integration_dir = shim_dir / "custom_components" / "fake_hub"
    (integration_dir / "__init__.py").write_text(textwrap.dedent(FAKE_HUB_V1))
    (integration_dir / "manifest.json").write_text(
        '{"domain": "fake_hub", "name": "Fake Hub", "version": "1.0.0",'
        ' "config_flow": false, "requirements": []}'
    )

    integration_manager._integrations["fake_hub"] = IntegrationInfo(
        domain="fake_hub",
        name="Fake Hub",
        version="1.0.0",
        description="Multi-entry fake hub",
        source="local",
        repository_url="https://example.com/fake_hub",
        enabled=True,
    )
    return integration_loader


async def _add_entry(hass, entry_id):
    entry = ConfigEntry(
        entry_id=entry_id,
        version=1,
        domain="fake_hub",
        title=f"Fake Hub {entry_id}",
    )
    await hass.config_entries.async_add(entry)
    return entry


@pytest.mark.asyncio
async def test_multi_entry_unload_runs_every_entries_hook(hass, loader):
    """Unloading one entry must not cut the domain off from the others.

    ``unload_integration`` used to delete ``_loaded_integrations[domain]`` for
    the first entry it saw and return early for the rest, so entry #2's devices
    were never torn down and the next setup failed with "device already
    initialized".
    """
    first = await _add_entry(hass, "entry_1")
    second = await _add_entry(hass, "entry_2")
    assert await loader.setup_integration(first) is True
    assert await loader.setup_integration(second) is True

    module = loader._loaded_integrations["fake_hub"]
    api = hass.data["fake_hub"]
    assert set(api.devices) == {"entry_1", "entry_2"}

    assert await loader.unload_integration(first) is True

    # Generation 1 is still the one entry_2 runs: keep it, and its singleton.
    assert module.UNLOADED == ["entry_1"]
    assert loader._loaded_integrations.get("fake_hub") is module
    assert "custom_components.fake_hub" in sys.modules
    assert hass.data["fake_hub"] is api
    assert set(api.devices) == {"entry_2"}

    assert await loader.unload_integration(second) is True

    # Both hooks ran; the domain is fully down and its generation discarded.
    assert module.UNLOADED == ["entry_1", "entry_2"]
    assert "fake_hub" not in loader._loaded_integrations
    assert "custom_components.fake_hub" not in sys.modules


@pytest.mark.asyncio
async def test_update_does_not_hand_new_code_old_instances(hass, loader, shim_dir):
    """A new module generation starts from an empty hass.data[domain].

    This is the meross_lan update: 6.0.0's setup read ``device.profile`` off the
    ``Device`` objects 5.8.0 had left in the cached ``ComponentApi``.
    """
    entry = await _add_entry(hass, "entry_1")
    assert await loader.setup_integration(entry) is True

    old_module = loader._loaded_integrations["fake_hub"]
    old_api = hass.data["fake_hub"]
    assert old_module.VERSION == 1

    # What install_integration does: replace the files, same path.
    (shim_dir / "custom_components" / "fake_hub" / "__init__.py").write_text(
        textwrap.dedent(FAKE_HUB_V2)
    )

    assert await loader.unload_integration(entry) is True
    assert "custom_components.fake_hub" not in sys.modules

    # Would raise AttributeError on the stale v1 Device if hass.data survived.
    assert await loader.setup_integration(entry) is True
    assert loader._loaded_integrations["fake_hub"].VERSION == 2
    assert hass.data["fake_hub"] is not old_api
    assert set(hass.data["fake_hub"].devices) == {"entry_1"}


@pytest.mark.asyncio
async def test_reset_domain_discards_a_generation_that_never_unloaded(hass, loader):
    """The update path resets the domain even when unload had nothing to do.

    A domain whose entry setup failed after caching ``hass.data[domain]`` is not
    in ``_loaded_integrations``, so unloading finds nothing to purge — but the
    installed files still changed.
    """
    entry = await _add_entry(hass, "entry_1")
    assert await loader.setup_integration(entry) is True
    old_api = hass.data["fake_hub"]

    # Pretend the unload never happened (e.g. the hook raised).
    loader._loaded_integrations.pop("fake_hub")
    loader.reset_domain("fake_hub")

    assert "custom_components.fake_hub" not in sys.modules
    assert "fake_hub" not in hass.data
    assert old_api is not hass.data.get("fake_hub")


class _Entity:
    """Minimal entity for the per-entry removal assertions."""

    def __init__(self, entity_id, entry_id):
        self.entity_id = entity_id
        self.integration_domain = "fake_hub"
        self._attr_config_entry_id = entry_id
        self.removed = False

    async def async_remove(self, cleanup_mqtt=True):
        self.removed = True


@pytest.mark.asyncio
async def test_partial_unload_leaves_sibling_entities_alone(hass, loader):
    """Only the unloaded entry's entities go; the siblings are not re-set up."""
    first = await _add_entry(hass, "entry_1")
    second = await _add_entry(hass, "entry_2")
    assert await loader.setup_integration(first) is True
    assert await loader.setup_integration(second) is True

    ours = _Entity("sensor.ours", "entry_1")
    theirs = _Entity("sensor.theirs", "entry_2")
    loader._entities["sensor"] = [ours, theirs]

    assert await loader.unload_integration(first) is True
    assert ours.removed is True
    assert theirs.removed is False

    assert await loader.unload_integration(second) is True
    assert theirs.removed is True
