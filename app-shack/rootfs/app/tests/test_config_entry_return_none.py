"""Config entry setup/unload/migrate now return None (HA 2026.11).

Upstream blog, 2026-10-23: ``async_setup_entry``, ``async_unload_entry`` and
``async_migrate_entry`` return ``None`` on success and raise a ``ConfigEntry*``
exception on failure. Custom integrations may still return a boolean:
``True`` is success, ``False`` is a failure.

The shim read these values for truthiness, so any integration that migrated to
``return None`` (the new scaffold template) silently failed to set up, and a
``None`` migration result re-ran the migration on every start. The exceptions
that replaced the booleans (``ConfigEntryNotReady`` -> retry,
``ConfigEntryAuthFailed`` -> reauth) were treated as generic setup errors.
"""

import importlib
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from shim.core import ConfigEntry, HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationInfo, IntegrationManager
from shim.storage import Storage

DOMAIN = "fake_returns_none"

INIT_TEMPLATE = '''
"""Fake integration with configurable config entry hooks."""

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

{imports}


async def async_setup_entry(hass, entry):
{setup_body}


async def async_unload_entry(hass, entry):
{unload_body}


async def async_migrate_entry(hass, entry):
{migrate_body}
'''

CONFIG_FLOW = """
from homeassistant.config_entries import ConfigFlow


class FakeFlow(ConfigFlow, domain="{domain}"):
    VERSION = {version}
"""


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


def _write_integration(
    shim_dir,
    domain=DOMAIN,
    setup_body="    return None",
    unload_body="    return None",
    migrate_body="    return None",
    version=1,
):
    integration_dir = shim_dir / "custom_components" / domain
    integration_dir.mkdir(parents=True, exist_ok=True)
    (integration_dir / "__init__.py").write_text(
        textwrap.dedent(
            INIT_TEMPLATE.format(
                imports="", setup_body=setup_body, unload_body=unload_body,
                migrate_body=migrate_body,
            )
        )
    )
    (integration_dir / "config_flow.py").write_text(
        textwrap.dedent(CONFIG_FLOW.format(domain=domain, version=version))
    )
    (integration_dir / "manifest.json").write_text(
        f'{{"domain": "{domain}", "name": "Fake", "version": "1.0.0",'
        ' "config_flow": true, "requirements": []}'
    )


def _make_loader(hass, temp_data_dir, domain=DOMAIN, **kwargs):
    _reset_custom_components()
    shim_dir = temp_data_dir / "shim"
    integration_manager = IntegrationManager(Storage(shim_dir), shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    _write_integration(shim_dir, domain=domain, **kwargs)
    integration_manager._integrations[domain] = IntegrationInfo(
        domain=domain,
        name="Fake",
        version="1.0.0",
        description="Fake config entry hooks",
        source="local",
        repository_url="https://example.com/fake",
        enabled=True,
    )
    return loader


async def _add_entry(hass, domain=DOMAIN, version=1):
    entry = ConfigEntry(
        entry_id=f"{domain}_entry", version=version, domain=domain, title="Fake"
    )
    await hass.config_entries.async_add(entry)
    return entry


@pytest.fixture(autouse=True)
def _cleanup():
    _reset_custom_components()
    yield
    _reset_custom_components()


async def test_setup_entry_returning_none_loads_the_entry(hass, temp_data_dir):
    """``return None`` is success, not a failed setup."""
    loader = _make_loader(hass, temp_data_dir)
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is True
    assert entry.state == "loaded"


async def test_setup_entry_returning_true_still_loads(hass, temp_data_dir):
    """Custom integrations may keep returning a boolean."""
    loader = _make_loader(hass, temp_data_dir, setup_body="    return True")
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is True
    assert entry.state == "loaded"


async def test_setup_entry_returning_false_fails_with_setup_error(
    hass, temp_data_dir
):
    """``return False`` is still supported and fails the entry."""
    loader = _make_loader(hass, temp_data_dir, setup_body="    return False")
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "setup_error"


async def test_setup_entry_config_entry_not_ready_retries(hass, temp_data_dir, monkeypatch):
    """ConfigEntryNotReady sets SETUP_RETRY and schedules a retry with backoff."""
    loader = _make_loader(
        hass,
        temp_data_dir,
        setup_body="    raise ConfigEntryNotReady('device offline')",
    )

    # Patch after the loader exists: IntegrationLoader.__init__ re-runs the
    # import patcher, which replaces the stub modules in sys.modules.
    scheduled = []
    import homeassistant.helpers.event as ha_event

    monkeypatch.setattr(
        ha_event,
        "async_call_later",
        lambda hass_, delay, action: scheduled.append((delay, action)) or (lambda: None),
    )

    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "setup_retry"
    assert entry._retry_count == 1
    assert [delay for delay, _ in scheduled] == [5]

    # Second failure backs off further, third even more.
    await loader.setup_integration(entry)
    await loader.setup_integration(entry)
    assert [delay for delay, _ in scheduled] == [5, 10, 20]


async def test_scheduled_retry_really_runs_setup_again(hass, temp_data_dir, monkeypatch):
    """The retry scheduled for ConfigEntryNotReady re-runs setup."""
    loader = _make_loader(
        hass,
        temp_data_dir,
        setup_body=(
            "    if getattr(entry, '_fail_once', True):\n"
            "        entry._fail_once = False\n"
            "        raise ConfigEntryNotReady('not yet')\n"
            "    return None"
        ),
    )
    scheduled = []
    import homeassistant.helpers.event as ha_event

    monkeypatch.setattr(
        ha_event,
        "async_call_later",
        lambda hass_, delay, action: scheduled.append((delay, action)) or (lambda: None),
    )

    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is False
    assert entry.state == "setup_retry"
    assert len(scheduled) == 1

    delay, retry = scheduled[0]
    assert delay == 5
    await retry()

    assert entry.state == "loaded"
    assert entry._retry_count == 0


async def test_setup_entry_auth_failed_starts_reauth(hass, temp_data_dir):
    """ConfigEntryAuthFailed fails the entry and starts a reauth flow."""
    loader = _make_loader(
        hass,
        temp_data_dir,
        setup_body="    raise ConfigEntryAuthFailed('invalid credentials')",
    )
    entry = await _add_entry(hass)

    reauth = []
    entry.async_start_reauth = lambda hass_=None, context=None, data=None: reauth.append(True)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "setup_error"
    assert reauth == [True]


async def test_setup_entry_config_entry_error_fails_the_entry(hass, temp_data_dir):
    """ConfigEntryError is a fatal setup failure."""
    loader = _make_loader(
        hass, temp_data_dir, setup_body="    raise ConfigEntryError('boom')"
    )
    entry = await _add_entry(hass)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "setup_error"


async def test_unload_entry_returning_none_is_success(hass, temp_data_dir, caplog):
    """``return None`` unloads cleanly and does not warn about False."""
    loader = _make_loader(hass, temp_data_dir)
    entry = await _add_entry(hass)
    assert await loader.setup_integration(entry) is True

    assert await loader.unload_integration(entry) is True
    assert entry.state == "not_loaded"
    assert not [r for r in caplog.records if "unload returned False" in r.message]


async def test_migrate_entry_returning_none_persists_the_version(hass, temp_data_dir):
    """A None migration result still counts as success (version is saved)."""
    loader = _make_loader(
        hass,
        temp_data_dir,
        setup_body="    return None",
        migrate_body=(
            "    hass.config_entries.async_update_entry(entry, version=2)\n"
            "    return None"
        ),
        version=2,
    )
    entry = await _add_entry(hass, version=1)

    assert await loader.setup_integration(entry) is True
    assert entry.version == 2
    assert entry.state == "loaded"


async def test_migrate_entry_config_entry_error_fails_migration(
    hass, temp_data_dir
):
    """A raising migration marks MIGRATION_ERROR and does not run setup."""
    loader = _make_loader(
        hass,
        temp_data_dir,
        migrate_body="    raise ConfigEntryError('cannot migrate')",
        version=2,
    )
    entry = await _add_entry(hass, version=1)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "migration_error"


async def test_migrate_entry_returning_false_fails_migration(hass, temp_data_dir):
    """``return False`` from a migration is still supported as a failure."""
    loader = _make_loader(
        hass, temp_data_dir, migrate_body="    return False", version=2
    )
    entry = await _add_entry(hass, version=1)

    assert await loader.setup_integration(entry) is False
    assert entry.state == "migration_error"


async def test_unload_platforms_or_raise_exists(hass):
    """Migrated integrations call async_unload_platforms_or_raise."""
    entry = ConfigEntry(entry_id="e1", version=1, domain=DOMAIN, title="Fake")
    await hass.config_entries.async_add(entry)

    assert (
        await hass.config_entries.async_unload_platforms_or_raise(entry, ["sensor"])
        is None
    )

    async def _failing(entry, platforms):
        return False

    hass.config_entries.async_unload_platforms = _failing
    from homeassistant.exceptions import ConfigEntryError

    with pytest.raises(ConfigEntryError):
        await hass.config_entries.async_unload_platforms_or_raise(entry, ["sensor"])
