"""Repair issues adopted from the wheel (HA 2026.11 additions).

``helpers.issue_registry`` is imported verbatim, so 2026.11's
``config_entry_id``, the refresh API and ``async_keep_issue`` come from upstream.
These tests pin that the shim's ``hass``/``config_entries`` surface is enough for
them (the module is the source of truth; no reimplementation).
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching


@pytest.fixture
async def hass():
    with tempfile.TemporaryDirectory() as tmpdir:
        instance = HomeAssistant(Path(tmpdir))
        setup_import_patching(instance).patch()
        yield instance


async def _entry(hass, entry_id="entry1", domain="demo"):
    entry = ConfigEntry(entry_id=entry_id, version=1, domain=domain, title="Demo")
    await hass.config_entries.async_add(entry)
    return entry


def _create(ir, hass, issue_id, **kwargs):
    ir.async_create_issue(
        hass,
        "demo",
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="problem",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_issue_can_belong_to_a_config_entry(hass):
    """``config_entry_id`` links the issue and is exposed on the entry."""
    from homeassistant.helpers import issue_registry as ir

    entry = await _entry(hass)
    _create(ir, hass, "outdated_firmware", config_entry_id=entry.entry_id)

    issue = ir.async_get(hass).async_get_issue("demo", "outdated_firmware")
    assert issue is not None
    assert issue.config_entry_id == entry.entry_id


@pytest.mark.asyncio
async def test_unknown_config_entry_is_rejected(hass):
    """An unknown entry raises ``HomeAssistantError`` (upstream behaviour)."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import issue_registry as ir

    with pytest.raises(HomeAssistantError):
        _create(ir, hass, "bad", config_entry_id="no_such_entry")


@pytest.mark.asyncio
async def test_refresh_deletes_only_stale_issues(hass):
    """Re-created issues survive a refresh; the stale ones are deleted."""
    from homeassistant.helpers import issue_registry as ir

    entry = await _entry(hass)
    registry = ir.async_get(hass)

    _create(ir, hass, "firmware_old", config_entry_id=entry.entry_id)
    _create(ir, hass, "firmware_stale", config_entry_id=entry.entry_id)

    with ir.async_refresh_issues(
        hass, "demo", issue_id_pattern="firmware_", config_entry_id=entry.entry_id
    ):
        _create(ir, hass, "firmware_old", config_entry_id=entry.entry_id)
        _create(ir, hass, "firmware_new", config_entry_id=entry.entry_id)

    assert registry.async_get_issue("demo", "firmware_old") is not None
    assert registry.async_get_issue("demo", "firmware_new") is not None
    assert registry.async_get_issue("demo", "firmware_stale") is None


@pytest.mark.asyncio
async def test_keep_issue_survives_a_refresh(hass):
    """``async_keep_issue`` protects an issue that could not be checked."""
    from homeassistant.helpers import issue_registry as ir

    entry = await _entry(hass)
    registry = ir.async_get(hass)
    _create(ir, hass, "firmware_offline", config_entry_id=entry.entry_id)

    with ir.async_refresh_issues(
        hass, "demo", issue_id_pattern="firmware_", config_entry_id=entry.entry_id
    ):
        ir.async_keep_issue(hass, "demo", "firmware_offline")

    assert registry.async_get_issue("demo", "firmware_offline") is not None


@pytest.mark.asyncio
async def test_deferred_delete_is_left_to_the_refresh(hass):
    """``defer_to_refresh`` keeps an issue that the refresh re-creates."""
    from homeassistant.helpers import issue_registry as ir

    entry = await _entry(hass)
    registry = ir.async_get(hass)
    _create(ir, hass, "firmware_1", config_entry_id=entry.entry_id)

    with ir.async_refresh_issues(
        hass, "demo", issue_id_pattern="firmware_", config_entry_id=entry.entry_id
    ):
        ir.async_delete_issue(hass, "demo", "firmware_1", defer_to_refresh=True)
        _create(ir, hass, "firmware_1", config_entry_id=entry.entry_id)

    assert registry.async_get_issue("demo", "firmware_1") is not None


@pytest.mark.asyncio
async def test_removing_a_config_entry_deletes_its_issues(hass):
    """HA deletes an entry's issues with its devices (2026.11)."""
    from homeassistant.helpers import issue_registry as ir

    entry = await _entry(hass)
    _create(ir, hass, "outdated_firmware", config_entry_id=entry.entry_id)
    _create(ir, hass, "global_issue")

    await hass.config_entries.async_remove(entry.entry_id)

    registry = ir.async_get(hass)
    assert registry.async_get_issue("demo", "outdated_firmware") is None
    assert registry.async_get_issue("demo", "global_issue") is not None
