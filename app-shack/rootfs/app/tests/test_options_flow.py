"""Tests for the options flow: results must reach ``entry.options``.

Home Assistant's ``OptionsFlowManager.async_finish_flow`` applies a finished
options flow to the entry: on ``create_entry`` the step's ``data`` *replaces*
``entry.options`` (and an ``OptionsFlowWithReload`` reloads the entry when they
changed).

The shim's ``continue_options_flow`` only re-saved ``entry.data``, so every
option picked in the web UI was silently dropped unless the integration wrote
``entry.options`` itself — the form submitted, the UI said "Configuration
updated successfully!", and the entry was unchanged on the next visit.
"""

import importlib
import sys
import uuid
from pathlib import Path

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.config_entries import OptionsFlow, OptionsFlowWithReload
from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

FLOW_SOURCE = '''
from homeassistant.config_entries import (
    ConfigFlow,
    OptionsFlow,
    OptionsFlowWithReload,
)


class {domain}ConfigFlow(ConfigFlow, domain="{domain}"):
    """Config flow, needed so the loader can find a handler."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_form(step_id="user", data_schema=None)

    @staticmethod
    def async_get_options_flow(config_entry):
        return {options_flow}(config_entry)


class {options_flow}({base}):
    """Options flow used by the tests."""

    def __init__(self, config_entry=None):
        super().__init__(config_entry)

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=None,
            description_placeholders={{"current": str(self.config_entry.options)}},
        )

    async def async_step_second(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="second", data_schema=None)
'''


def _create_integration(shim_dir, domain, options_flow="TestOptionsFlow", base="OptionsFlow"):
    custom_components = shim_dir / "custom_components"
    custom_components.mkdir(parents=True, exist_ok=True)
    (custom_components / "__init__.py").write_text("# package\n")
    integration_dir = custom_components / domain
    integration_dir.mkdir(exist_ok=True)
    (integration_dir / "__init__.py").write_text(
        f'''
async def async_setup_entry(hass, entry):
    hass.data.setdefault("{domain}", {{}})[entry.entry_id] = {{}}
    return None


async def async_unload_entry(hass, entry):
    return None
'''
    )
    (integration_dir / "manifest.json").write_text(
        f'''
{{
    "domain": "{domain}",
    "name": "Options Test",
    "version": "1.0.0",
    "config_flow": true
}}
'''
    )
    (integration_dir / "config_flow.py").write_text(
        FLOW_SOURCE.format(domain=domain, options_flow=options_flow, base=base)
    )
    return integration_dir


def _cleanup(domain):
    for name in (
        f"custom_components.{domain}",
        f"custom_components.{domain}.config_flow",
        # The parent package binds the tmp shim dir in its __path__; leaving it
        # cached makes the next test import the previous test's integrations.
        "custom_components",
    ):
        sys.modules.pop(name, None)


async def _setup_env(tmp_path, options=None, options_flow="TestOptionsFlow", base="OptionsFlow"):
    domain = f"options_{uuid.uuid4().hex[:8]}"

    hass = HomeAssistant(tmp_path)
    setup_import_patching(hass).patch()

    shim_dir = tmp_path / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    _create_integration(shim_dir, domain, options_flow, base)
    integration_manager._integrations[domain] = IntegrationInfo(
        domain=domain,
        name="Options Test",
        version="1.0.0",
        description="Test",
        source="local",
        repository_url="https://example.com/test",
        enabled=True,
        config_flow=True,
    )
    if str(shim_dir) not in sys.path:
        sys.path.insert(0, str(shim_dir))
    importlib.invalidate_caches()

    entry = ConfigEntry(
        entry_id=f"{domain}_entry",
        version=1,
        domain=domain,
        title="Options Test",
        data={"host": "192.0.2.1"},
        options=dict(options or {}),
    )
    entry.state = "loaded"
    await hass.config_entries.async_add(entry)

    return hass, loader, entry, domain


@pytest.mark.asyncio
async def test_options_flow_result_reaches_entry_options(tmp_path):
    """The submitted values become ``entry.options``."""
    hass, loader, entry, domain = await _setup_env(tmp_path)
    try:
        result = await loader.start_options_flow(entry)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        result = await loader.continue_options_flow(
            entry, result["flow_id"], {"threshold": "5", "enabled": "true"}
        )

        assert result["type"] == "create_entry"
        assert entry.options == {"threshold": "5", "enabled": "true"}
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_options_flow_replaces_options_like_ha(tmp_path):
    """HA replaces ``entry.options`` with the flow's data; it does not merge."""
    hass, loader, entry, domain = await _setup_env(
        tmp_path, options={"stale": "old", "threshold": "1"}
    )
    try:
        result = await loader.start_options_flow(entry)
        result = await loader.continue_options_flow(
            entry, result["flow_id"], {"threshold": "9"}
        )

        assert entry.options == {"threshold": "9"}
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_options_flow_persists_across_reload(tmp_path):
    """Written options survive a reload of the entry (stored, not just in memory)."""
    hass, loader, entry, domain = await _setup_env(tmp_path)
    try:
        result = await loader.start_options_flow(entry)
        await loader.continue_options_flow(entry, result["flow_id"], {"threshold": "7"})

        # The store the app is reloaded from.
        stored = {e.entry_id: e for e in hass.config_entries.async_entries(domain)}
        assert stored[entry.entry_id].options == {"threshold": "7"}
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_form_step_leaves_options_alone(tmp_path):
    """Only a finished (create_entry) flow writes options."""
    hass, loader, entry, domain = await _setup_env(tmp_path, options={"threshold": "1"})
    try:
        result = await loader.start_options_flow(entry)
        assert result["type"] == "form"
        assert entry.options == {"threshold": "1"}

        # Ask for the second step instead of finishing.
        result = await loader.continue_options_flow(
            entry, result["flow_id"], {"next_step": "second"}
        )
        assert result["type"] == "form"
        assert result["step_id"] == "second"
        assert entry.options == {"threshold": "1"}
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_options_flow_with_reload_subclass_finishes(tmp_path):
    """Integrations using OptionsFlowWithReload go through the same path."""
    hass, loader, entry, domain = await _setup_env(
        tmp_path, options_flow="ReloadOptionsFlow", base="OptionsFlowWithReload"
    )
    try:
        result = await loader.start_options_flow(entry)
        assert result["type"] == "form"

        result = await loader.continue_options_flow(
            entry, result["flow_id"], {"threshold": "3"}
        )

        assert result["type"] == "create_entry"
        assert entry.options == {"threshold": "3"}
    finally:
        _cleanup(domain)


def test_options_flow_with_reload_is_an_options_flow():
    """HA's marker class: an OptionsFlow that reloads on changed options."""
    assert issubclass(OptionsFlowWithReload, OptionsFlow)
    assert OptionsFlowWithReload.automatic_reload is True
    # HA defines the attribute only on the subclass, so a plain OptionsFlow
    # does not reload on its own.
    assert not hasattr(OptionsFlow, "automatic_reload")
