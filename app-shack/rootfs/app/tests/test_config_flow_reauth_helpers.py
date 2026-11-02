"""Tests for the reauth/reconfigure helpers on the shim's ConfigFlow.

Reproduces the nest_protect failure at logout:

    AttributeError: 'ConfigFlow' object has no attribute '_get_reauth_entry'

The reported traceback is the first of three helpers a migrated integration
touches while reauthenticating, so all of them are covered here:

- ``_get_reauth_entry`` / ``_get_reconfigure_entry`` (+ the ``_*_entry_id``
  properties and ``ConfigEntries.async_get_known_entry``)
- ``_abort_if_unique_id_mismatch`` (raises ``AbortFlow``, which the flow
  manager has to turn into an abort result instead of a failed flow)
- ``async_update_reload_and_abort`` / ``async_update_and_abort``
"""

import importlib
import sys
import uuid
from pathlib import Path

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_REAUTH,
    AbortFlow,
    ConfigFlow,
    UnknownEntry,
    abort_result_from_flow_error,
)
from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage


# --------------------------------------------------------------------------- #
# Unit-level helpers: a flow object wired to a real hass + config entry
# --------------------------------------------------------------------------- #


async def _hass_with_entry(tmp_path, entry_id="entry_1", domain="reauth_helpers"):
    hass = HomeAssistant(tmp_path)
    entry = ConfigEntry(
        entry_id=entry_id,
        version=1,
        domain=domain,
        title="Test Entry",
        data={"host": "192.0.2.1", "unique_id": "abc123"},
    )
    await hass.config_entries.async_add(entry)
    return hass, entry


def _make_flow(hass, source, entry_id=None, handler="reauth_helpers"):
    flow = ConfigFlow()
    flow.hass = hass
    flow.handler = handler
    flow.flow_id = "flow_1"
    flow.context = {"source": source}
    if entry_id is not None:
        flow.context["entry_id"] = entry_id
    return flow


class TestGetEntry:
    """ConfigFlow._get_reauth_entry / _get_reconfigure_entry."""

    @pytest.mark.asyncio
    async def test_get_reauth_entry(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        assert flow._reauth_entry_id == entry.entry_id
        assert flow._get_reauth_entry() is entry

    @pytest.mark.asyncio
    async def test_get_reconfigure_entry(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_RECONFIGURE, entry.entry_id)

        assert flow._reconfigure_entry_id == entry.entry_id
        assert flow._get_reconfigure_entry() is entry

    @pytest.mark.asyncio
    async def test_get_reauth_entry_rejects_wrong_source(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, "user", entry.entry_id)

        with pytest.raises(ValueError, match="expected reauth"):
            flow._get_reauth_entry()

    @pytest.mark.asyncio
    async def test_get_reconfigure_entry_rejects_wrong_source(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        with pytest.raises(ValueError, match="expected reconfigure"):
            flow._get_reconfigure_entry()

    @pytest.mark.asyncio
    async def test_get_reauth_entry_unknown_entry(self, tmp_path):
        hass, _ = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, "does_not_exist")

        with pytest.raises(UnknownEntry):
            flow._get_reauth_entry()

    @pytest.mark.asyncio
    async def test_async_get_known_entry_returns_entry(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)

        assert hass.config_entries.async_get_known_entry(entry.entry_id) is entry

    @pytest.mark.asyncio
    async def test_async_get_known_entry_raises_for_unknown_id(self, tmp_path):
        hass, _ = await _hass_with_entry(tmp_path)

        with pytest.raises(UnknownEntry):
            hass.config_entries.async_get_known_entry("nope")


class TestUpdateAndAbort:
    """async_update_reload_and_abort / async_update_and_abort."""

    @pytest.mark.asyncio
    async def test_update_reload_and_abort_reauth(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        result = flow.async_update_reload_and_abort(
            entry, data_updates={"host": "192.0.2.2"}
        )

        assert entry.data == {"host": "192.0.2.2", "unique_id": "abc123"}
        assert reloads == [entry.entry_id]
        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert result["translation_domain"] == "homeassistant"

    @pytest.mark.asyncio
    async def test_update_reload_and_abort_reconfigure(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        hass.config_entries.async_schedule_reload = lambda entry_id: None
        flow = _make_flow(hass, SOURCE_RECONFIGURE, entry.entry_id)

        result = flow.async_update_reload_and_abort(entry, data_updates={"host": "h"})

        assert result["reason"] == "reconfigure_successful"

    @pytest.mark.asyncio
    async def test_update_reload_and_abort_custom_reason(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        hass.config_entries.async_schedule_reload = lambda entry_id: None
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        result = flow.async_update_reload_and_abort(
            entry, data_updates={"host": "h"}, reason="all_good"
        )

        assert result["reason"] == "all_good"
        assert "translation_domain" not in result

    @pytest.mark.asyncio
    async def test_update_reload_and_abort_skips_reload_when_unchanged(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        # data_updates rewrites the same value: async_update_entry reports no
        # change, so an unchanged entry is not reloaded.
        flow.async_update_reload_and_abort(
            entry,
            data_updates={"host": "192.0.2.1"},
            reload_even_if_entry_is_unchanged=False,
        )

        assert reloads == []

    @pytest.mark.asyncio
    async def test_update_reload_and_abort_sets_unique_id(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        hass.config_entries.async_schedule_reload = lambda entry_id: None
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        flow.async_update_reload_and_abort(entry, unique_id="user.new")

        assert entry.unique_id == "user.new"

    @pytest.mark.asyncio
    async def test_update_and_abort_does_not_reload(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        result = flow.async_update_and_abort(entry, data_updates={"host": "h"})

        assert reloads == []
        assert result["reason"] == "reauth_successful"

    @pytest.mark.asyncio
    async def test_data_and_data_updates_are_mutually_exclusive(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        with pytest.raises(ValueError, match="Cannot set both"):
            flow.async_update_and_abort(
                entry, data={"host": "a"}, data_updates={"host": "b"}
            )


class TestAbortIfUniqueIdMismatch:
    """_abort_if_unique_id_mismatch raises AbortFlow, like upstream."""

    @pytest.mark.asyncio
    async def test_raises_for_reauth_with_other_account(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)
        flow.context["unique_id"] = "different"

        with pytest.raises(AbortFlow) as exc_info:
            flow._abort_if_unique_id_mismatch(reason="wrong_account")

        assert exc_info.value.reason == "wrong_account"

    @pytest.mark.asyncio
    async def test_passes_for_matching_unique_id(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)
        flow.context["unique_id"] = "abc123"

        flow._abort_if_unique_id_mismatch(reason="wrong_account")

    @pytest.mark.asyncio
    async def test_ignores_flows_without_entry_source(self, tmp_path):
        hass, _ = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, "user")
        flow.context["unique_id"] = "whatever"

        flow._abort_if_unique_id_mismatch()


class TestAbortResultFromFlowError:
    """The AbortFlow -> abort result conversion."""

    def test_includes_reason_flow_id_and_places(self):
        exc = AbortFlow(
            "wrong_account",
            {"name": "Nest Protect"},
            translation_domain="homeassistant",
        )

        result = abort_result_from_flow_error("flow_7", exc)

        assert result == {
            "type": "abort",
            "reason": "wrong_account",
            "flow_id": "flow_7",
            "description_placeholders": {"name": "Nest Protect"},
            "translation_domain": "homeassistant",
        }


class TestShowFormReauthPlaceholder:
    """async_show_form adds the entry name to reauth descriptions."""

    @pytest.mark.asyncio
    async def test_name_placeholder_added_for_reauth(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        result = flow.async_show_form(
            step_id="reauth_confirm", description_placeholders={"extra": "x"}
        )

        assert result["description_placeholders"] == {
            "extra": "x",
            "name": "Test Entry",
        }

    @pytest.mark.asyncio
    async def test_explicit_name_is_kept(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH, entry.entry_id)

        result = flow.async_show_form(
            step_id="reauth_confirm", description_placeholders={"name": "Mine"}
        )

        assert result["description_placeholders"]["name"] == "Mine"

    @pytest.mark.asyncio
    async def test_no_placeholder_outside_reauth(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, "user", entry.entry_id)

        result = flow.async_show_form(step_id="user")

        assert result["description_placeholders"] == {}

    @pytest.mark.asyncio
    async def test_no_placeholder_without_entry_id(self, tmp_path):
        hass, entry = await _hass_with_entry(tmp_path)
        flow = _make_flow(hass, SOURCE_REAUTH)

        result = flow.async_show_form(step_id="reauth_confirm")

        assert result["description_placeholders"] == {}


# --------------------------------------------------------------------------- #
# End-to-end: a migrated integration reauthenticating through the flow manager
# --------------------------------------------------------------------------- #

REAUTH_FLOW_SOURCE = '''
from homeassistant.config_entries import ConfigFlow

CONF_HOST = "host"


class ReauthFlow(ConfigFlow, domain="{domain}"):
    """Flow using the reauth helpers nest_protect >= 0.5.0 relies on."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Test", data=user_input)
        return self.async_show_form(step_id="user", data_schema=None)

    async def async_step_reauth(self, entry_data):
        self._entry_on_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        if user_input is not None:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry, data_updates={{CONF_HOST: user_input[CONF_HOST]}}
            )
        return self.async_show_form(step_id="reauth_confirm", data_schema=None)
'''


def _create_integration(shim_dir, domain):
    custom_components = shim_dir / "custom_components"
    custom_components.mkdir(parents=True, exist_ok=True)
    (custom_components / "__init__.py").write_text("# package\n")
    integration_dir = custom_components / domain
    integration_dir.mkdir(exist_ok=True)
    (integration_dir / "__init__.py").write_text(
        f'''
async def async_setup_entry(hass, entry):
    hass.data.setdefault("{domain}", {{}})[entry.entry_id] = {{}}
    return True


async def async_unload_entry(hass, entry):
    return True
'''
    )
    (integration_dir / "manifest.json").write_text(
        f'''
{{
    "domain": "{domain}",
    "name": "Reauth Test",
    "version": "1.0.0",
    "config_flow": true
}}
'''
    )
    (integration_dir / "config_flow.py").write_text(
        REAUTH_FLOW_SOURCE.format(domain=domain)
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


@pytest.mark.asyncio
async def test_reauth_flow_uses_entry_helpers_end_to_end(tmp_path):
    """A reauth flow reaches the entry, updates it and aborts successfully."""
    domain = f"reauth_{uuid.uuid4().hex[:8]}"

    hass = HomeAssistant(tmp_path)
    setup_import_patching(hass).patch()

    shim_dir = tmp_path / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    _create_integration(shim_dir, domain)
    integration_manager._integrations[domain] = IntegrationInfo(
        domain=domain,
        name="Reauth Test",
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
        title="Reauth Test",
        data={"host": "192.0.2.1", "unique_id": "user.abc"},
    )
    await hass.config_entries.async_add(entry)

    reloads = []
    hass.config_entries.async_schedule_reload = reloads.append

    try:
        # What ConfigEntry.async_start_reauth does, minus the background task.
        result = await hass.config_entries.flow.async_init(
            domain,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=dict(entry.data),
        )

        # The reauth entry point ran: it read the entry through the helper and
        # showed the confirm form (with the entry name in the description).
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

        flow = hass.config_entries._flow_progress[result["flow_id"]]
        assert flow._entry_on_entry is entry

        result = await loader.continue_config_flow(
            domain, result["flow_id"], {"host": "192.0.2.2"}
        )

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert entry.data == {"host": "192.0.2.2", "unique_id": "user.abc"}
        assert reloads == [entry.entry_id]
        # The finished flow is not left in progress.
        assert result["flow_id"] not in hass.config_entries._flow_progress
    finally:
        _cleanup(domain)


@pytest.mark.asyncio
async def test_reauth_with_other_account_aborts_with_reason(tmp_path):
    """_abort_if_unique_id_mismatch surfaces as an abort result, not a crash."""
    domain = f"reauth_{uuid.uuid4().hex[:8]}"

    hass = HomeAssistant(tmp_path)
    setup_import_patching(hass).patch()

    shim_dir = tmp_path / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    _create_integration(shim_dir, domain)
    integration_manager._integrations[domain] = IntegrationInfo(
        domain=domain,
        name="Reauth Test",
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
        title="Reauth Test",
        data={"host": "192.0.2.1", "unique_id": "user.abc"},
    )
    await hass.config_entries.async_add(entry)

    reloads = []
    hass.config_entries.async_schedule_reload = reloads.append

    try:
        result = await hass.config_entries.flow.async_init(
            domain,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=dict(entry.data),
        )
        flow = hass.config_entries._flow_progress[result["flow_id"]]
        # The account that just authenticated does not own this entry.
        flow.context["unique_id"] = "user.somebody_else"

        result = await loader.continue_config_flow(
            domain, result["flow_id"], {"host": "192.0.2.2"}
        )

        assert result["type"] == "abort"
        assert result["reason"] == "wrong_account"
        assert entry.data["host"] == "192.0.2.1"
        assert reloads == []
        assert result["flow_id"] not in hass.config_entries._flow_progress
    finally:
        _cleanup(domain)
