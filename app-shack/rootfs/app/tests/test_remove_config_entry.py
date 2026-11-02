"""Tests for removing config entries through the integration loader.

Reproduces the error seen when deleting a Nest Protect entry:

    ERROR: shim.integrations.loader - async_unload_entry failed for
    nest_protect: 'NoneType' object has no attribute 'subscription_task'

The entry had never been set up (its module was imported for a config flow), so
``entry.runtime_data`` was still ``None`` while the loader still called the
integration's ``async_unload_entry``. HA never does that: it only runs the hook
for an entry in state ``LOADED``.

Also covers ``async_remove_entry``, which the loader never called at all.
"""

import importlib
import sys
import uuid
from pathlib import Path

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.config_entries import SOURCE_IGNORE
from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

# Mirrors nest_protect: setup() publishes runtime_data, unload() dereferences it
# without checking, remove() cleans up what was persisted for the entry.
INTEGRATION_SOURCE = '''
from types import SimpleNamespace


def _calls(hass):
    return hass.data.setdefault("remove_entry_calls", [])


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    entry.runtime_data = SimpleNamespace(subscription_task=None)
    _calls(hass).append("setup")


async def async_unload_entry(hass, entry):
    # Recorded first so a crash inside the hook is still visible to the test.
    _calls(hass).append("unload")
    # Exactly nest_protect's shape: touches runtime_data without checking it.
    if entry.runtime_data.subscription_task:
        entry.runtime_data.subscription_task.cancel()
    return True


async def async_remove_entry(hass, entry):
    _calls(hass).append("remove")
    if entry.data.get("remove_raises"):
        raise RuntimeError("boom")
'''


def _create_integration(shim_dir, domain):
    custom_components = shim_dir / "custom_components"
    custom_components.mkdir(parents=True, exist_ok=True)
    (custom_components / "__init__.py").write_text("# package\n")
    integration_dir = custom_components / domain
    integration_dir.mkdir(exist_ok=True)
    (integration_dir / "__init__.py").write_text(INTEGRATION_SOURCE)
    (integration_dir / "manifest.json").write_text(
        f'''
{{
    "domain": "{domain}",
    "name": "Remove Test",
    "version": "1.0.0",
    "config_flow": false
}}
'''
    )
    return integration_dir


def _cleanup(domain):
    for name in (
        f"custom_components.{domain}",
        # The parent package binds the tmp shim dir in its __path__; leaving it
        # cached makes the next test import the previous test's integrations.
        "custom_components",
    ):
        sys.modules.pop(name, None)


async def _setup_env(tmp_path, state="not_loaded", data=None):
    """hass + loader with the test integration imported and an entry present."""
    domain = f"remove_{uuid.uuid4().hex[:8]}"

    hass = HomeAssistant(tmp_path)
    setup_import_patching(hass).patch()

    shim_dir = tmp_path / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    _create_integration(shim_dir, domain)
    integration_manager._integrations[domain] = IntegrationInfo(
        domain=domain,
        name="Remove Test",
        version="1.0.0",
        description="Test",
        source="local",
        repository_url="https://example.com/test",
        enabled=True,
        config_flow=False,
    )
    if str(shim_dir) not in sys.path:
        sys.path.insert(0, str(shim_dir))
    importlib.invalidate_caches()

    entry = ConfigEntry(
        entry_id=f"{domain}_entry",
        version=1,
        domain=domain,
        title="Remove Test",
        data=data or {"host": "192.0.2.1"},
    )
    entry.state = state
    await hass.config_entries.async_add(entry)

    # Importing the module (a config flow does this) puts it in
    # _loaded_integrations even though no entry was ever set up for it.
    assert await loader.load_integration(domain) is True

    return hass, loader, entry, domain


def _calls(hass):
    return hass.data.get("remove_entry_calls", [])


@pytest.mark.asyncio
async def test_never_set_up_entry_skips_unload_hook(tmp_path):
    """The reported case: deleting an entry that was never set up."""
    hass, loader, entry, domain = await _setup_env(tmp_path, state="not_loaded")
    try:
        assert entry.runtime_data is None

        assert await loader.remove_config_entry(entry) is True

        # runtime_data is None: the hook must not be called at all (it would
        # raise the reported AttributeError), it is not merely tolerated.
        assert _calls(hass) == ["remove"]
        assert hass.config_entries.async_entries(domain) == []
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_loaded_entry_runs_unload_then_remove(tmp_path):
    """An entry that went through setup is unloaded, then removed."""
    hass, loader, entry, domain = await _setup_env(tmp_path, state="loaded")
    try:
        # What setup_integration leaves behind.
        await loader._loaded_integrations[domain].async_setup_entry(hass, entry)
        assert entry.runtime_data is not None

        assert await loader.remove_config_entry(entry) is True

        assert _calls(hass) == ["setup", "unload", "remove"]
        assert entry.state == "not_loaded"
        assert hass.config_entries.async_entries(domain) == []
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_raising_remove_entry_does_not_fail_removal(tmp_path):
    """A failing async_remove_entry is logged; the entry is still gone."""
    hass, loader, entry, domain = await _setup_env(
        tmp_path, state="not_loaded", data={"remove_raises": True}
    )
    try:
        assert await loader.remove_config_entry(entry) is True

        assert _calls(hass) == ["remove"]
        assert hass.config_entries.async_entries(domain) == []
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_ignored_source_entry_skips_remove_hook(tmp_path):
    """SOURCE_IGNORE entries have no integration to run hooks for."""
    hass, loader, entry, domain = await _setup_env(tmp_path, state="not_loaded")
    try:
        entry.source = SOURCE_IGNORE

        assert await loader.remove_config_entry(entry) is True

        assert _calls(hass) == []
        assert hass.config_entries.async_entries(domain) == []
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_pending_retry_entry_skips_unload_hook(tmp_path):
    """A setup_retry entry has no instance either: only the retry is cancelled."""
    hass, loader, entry, domain = await _setup_env(tmp_path, state="setup_retry")
    try:
        cancelled = []
        entry._cancel_retry_setup = lambda: cancelled.append(True)

        assert await loader.remove_config_entry(entry) is True

        assert cancelled == [True]
        assert _calls(hass) == ["remove"]
        assert hass.config_entries.async_entries(domain) == []
    finally:
        _cleanup(domain)
