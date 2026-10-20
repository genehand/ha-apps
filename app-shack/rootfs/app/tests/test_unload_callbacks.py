"""Config entry unload lifecycle: ``entry.async_on_unload`` callbacks must run.

Regression tests for the localtuya reconnect storm after an in-app update.

localtuya (2026.10.0) keeps a long-lived TCP connection to each Tuya device and
registers its teardown exactly the way upstream Home Assistant expects::

    entry.async_create_task(hass, dev.async_connect())
    entry.async_on_unload(dev.close)

Home Assistant runs those callbacks as part of ``ConfigEntry.async_unload``. The
shim accepted the registrations but never ran them, so an update/reload unloaded
the platforms, created a *second* ``TuyaDevice`` for the same host, and left the
first one connected and heartbeating. Tuya devices accept one local client, so
the device reset both of them forever::

    INFO: custom_components.localtuya.coordinator - [... - Living room fan]
          Disconnected due to: [Errno 104] Connection reset by peer

The tests below use a fake integration with the same lifecycle to prove the old
connection is closed before the replacement is set up.
"""

import asyncio
import sys
import textwrap
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

# A fake integration that opens a "connection" (a long-lived task) on setup and
# registers its close() via entry.async_on_unload, exactly like localtuya.
FAKE_INTEGRATION = '''
"""Fake socket-holding integration used by the unload lifecycle tests."""

import asyncio

DOMAIN = "fake_socket"


class FakeDevice:
    """Stands in for a TuyaDevice: one live socket + heartbeat task."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        self.connected = False
        self.closed = False
        self._heartbeat = None

    async def async_connect(self):
        self.connected = True
        self._heartbeat = asyncio.get_running_loop().create_task(self._beating())

    async def _beating(self):
        while True:
            await asyncio.sleep(3600)

    async def close(self):
        self.closed = True
        self.connected = False
        if self._heartbeat:
            self._heartbeat.cancel()
            self._heartbeat = None


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    dev = FakeDevice(entry.entry_id)
    # Same registration order as localtuya's async_setup_entry.
    entry.async_create_task(hass, dev.async_connect())
    entry.async_on_unload(dev.close)
    hass.data.setdefault(DOMAIN, []).append(dev)
    if hass.data.get("fake_socket_fail_setup"):
        return False
    return True


async def async_unload_entry(hass, entry):
    return True
'''


def _reset_custom_components_imports():
    """Drop cached custom_components packages from earlier temp data dirs.

    Each test gets a fresh temp dir, but ``custom_components`` is a regular
    package imported from the first one, so a stale entry hides the new files.
    """
    for name in list(sys.modules):
        if name == "custom_components" or name.startswith("custom_components."):
            del sys.modules[name]
    import importlib

    importlib.invalidate_caches()


@pytest.fixture(autouse=True)
def _isolated_custom_components():
    """Keep this module's temp-dir package out of other test modules.

    Without the teardown, any later test that imports a real integration from
    ``data/shim/custom_components`` fails with ``ModuleNotFoundError`` because
    ``sys.modules['custom_components']`` still points at a deleted temp dir.
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
    # Built inside the test's event loop: HomeAssistant captures it at init.
    hass = HomeAssistant(temp_data_dir)
    setup_import_patching(hass).patch()
    return hass


@pytest.fixture
async def loader(hass, temp_data_dir):
    shim_dir = temp_data_dir / "shim"
    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    integration_loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = integration_loader

    _reset_custom_components_imports()
    integration_dir = shim_dir / "custom_components" / "fake_socket"
    integration_dir.mkdir(parents=True)
    (integration_dir / "__init__.py").write_text(textwrap.dedent(FAKE_INTEGRATION))
    (integration_dir / "manifest.json").write_text(
        '{"domain": "fake_socket", "name": "Fake Socket", "version": "1.0.0",'
        ' "config_flow": false, "requirements": []}'
    )

    integration_manager._integrations["fake_socket"] = IntegrationInfo(
        domain="fake_socket",
        name="Fake Socket",
        version="1.0.0",
        description="Fake socket-holding integration",
        source="local",
        repository_url="https://example.com/fake_socket",
        enabled=True,
    )
    return integration_loader


async def _settle():
    """Let entry-created tasks run (localtuya's connect performs real I/O)."""
    for _ in range(3):
        await asyncio.sleep(0)


async def _add_entry(hass, entry_id="fake_socket_entry"):
    entry = ConfigEntry(
        entry_id=entry_id,
        version=1,
        domain="fake_socket",
        title="Fake Socket",
    )
    await hass.config_entries.async_add(entry)
    return entry


@pytest.mark.asyncio
async def test_unload_runs_entry_on_unload_callbacks(hass, loader):
    """A registered async_on_unload coroutine callback is awaited on unload."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    await _settle()

    (device,) = hass.data["fake_socket"]
    assert device.connected is True
    assert device.closed is False

    assert await loader.unload_integration(entry) is True

    # Awaited, not merely scheduled: the socket must be gone when unload returns.
    assert device.closed is True
    assert device.connected is False


@pytest.mark.asyncio
async def test_unload_cancels_tasks_created_by_the_entry(hass, loader):
    """entry.async_create_task tasks are cancelled on unload (HA parity)."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    await _settle()

    (device,) = hass.data["fake_socket"]
    heartbeat = device._heartbeat
    assert heartbeat is not None and not heartbeat.done()

    await loader.unload_integration(entry)

    # close() cancels the heartbeat; let the cancellation land before asserting.
    await asyncio.gather(heartbeat, return_exceptions=True)
    assert heartbeat.cancelled() is True


@pytest.mark.asyncio
async def test_reload_leaves_exactly_one_connection_open(hass, loader):
    """Setup -> unload -> setup must not leave the old connection running.

    This is the localtuya bug: two clients for one device make the device reset
    both of them, every RECONNECT_INTERVAL seconds, forever.
    """
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is True
    await loader.unload_integration(entry)
    assert await loader.setup_integration(entry) is True
    await _settle()

    devices = hass.data["fake_socket"]
    assert len(devices) == 2
    open_devices = [d for d in devices if d.connected and not d.closed]
    assert len(open_devices) == 1, (
        f"expected one live connection after reload, got {len(open_devices)}: "
        f"{[(d.entry_id, d.connected, d.closed) for d in devices]}"
    )


@pytest.mark.asyncio
async def test_unload_callbacks_run_once_and_are_cleared(hass, loader):
    """Repeated unloads do not re-run stale callbacks from the prior generation."""
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    await _settle()

    (device,) = hass.data["fake_socket"]

    await loader.unload_integration(entry)
    assert device.closed is True
    assert entry._on_unload_callbacks == []

    # Re-setup registers a fresh generation, so a second unload closes only it.
    assert await loader.setup_integration(entry) is True
    await _settle()
    first_device = device
    second_device = hass.data["fake_socket"][-1]
    assert second_device is not first_device

    await loader.unload_integration(entry)
    assert second_device.closed is True
    assert first_device.closed is True


@pytest.mark.asyncio
async def test_unload_callback_errors_do_not_abort_unload(hass, loader):
    """A failing callback is logged, not propagated (HA parity)."""
    entry = await _add_entry(hass)

    def boom():
        raise RuntimeError("callback exploded")

    entry.async_on_unload(boom)

    assert await loader.unload_integration(entry) is True


@pytest.mark.asyncio
async def test_callbacks_run_even_if_unload_entry_raises(hass, loader):
    """A broken async_unload_entry must not leave the old connection running.

    install_and_reload() re-sets the entry up regardless of the unload result,
    so skipping the on_unload callbacks here would recreate the duplicate-client
    situation the callbacks exist to prevent.
    """
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True
    await _settle()

    (device,) = hass.data["fake_socket"]

    async def exploding_unload_entry(_hass, _entry):
        raise RuntimeError("unload exploded")

    loader._loaded_integrations["fake_socket"].async_unload_entry = (
        exploding_unload_entry
    )

    assert await loader.unload_integration(entry) is True
    assert device.closed is True


@pytest.mark.asyncio
async def test_failed_setup_runs_unload_callbacks(hass, loader):
    """A failing async_setup_entry must not leave a half-started instance alive.

    localtuya opens device connections before it finishes platform setup, so a
    setup that returns False (or raises) would otherwise leave the Tuya client
    running with no config entry owning it.
    """
    hass.data["fake_socket_fail_setup"] = True
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is False
    await _settle()

    (device,) = hass.data["fake_socket"]
    assert device.closed is True
    assert device.connected is False
    assert entry._on_unload_callbacks == []
