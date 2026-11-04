"""``entry.add_update_listener`` callbacks must run on config entry updates.

Regression tests for the meross_lan option that "didn't take":

    WARNING: custom_components.meross_lan.mss620_... - MQTT:
    ValueError(MQTT publish is not allowed by configuration) in async_connect

Home Assistant calls every registered update listener from
``ConfigEntries._async_save_and_notify`` whenever an entry changes; that is how
integrations apply new options to objects they already built. meross_lan's
listener re-applies ``allow_mqtt_publish`` to its live ``MQTTConnection``s. The
shim stored the listeners and never called them, so the connection kept the
disabled publisher installed at construction and the device binding to it kept
raising, even though the option showed as enabled.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant


@pytest.fixture
async def hass():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield HomeAssistant(Path(tmpdir))


async def _add_entry(hass, entry_id="entry_1"):
    entry = ConfigEntry(
        entry_id=entry_id,
        version=1,
        domain="fake_domain",
        title="Fake",
        data={"allow_mqtt_publish": False},
    )
    await hass.config_entries.async_add(entry)
    return entry


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_update_listener_runs_on_data_change(hass):
    entry = await _add_entry(hass)
    seen = []

    async def listener(hass_, entry_):
        seen.append(dict(entry_.data))

    entry.add_update_listener(listener)

    assert hass.config_entries.async_update_entry(
        entry, data={"allow_mqtt_publish": True}
    )
    await _settle()

    assert seen == [{"allow_mqtt_publish": True}]
    assert entry.data == {"allow_mqtt_publish": True}


@pytest.mark.asyncio
async def test_update_listener_does_not_run_when_nothing_changed(hass):
    entry = await _add_entry(hass)
    seen = []
    entry.add_update_listener(lambda h, e: seen.append(1))

    assert (
        hass.config_entries.async_update_entry(
            entry, data={"allow_mqtt_publish": False}
        )
        is False
    )
    await _settle()

    assert seen == []


@pytest.mark.asyncio
async def test_removed_update_listener_is_not_called(hass):
    entry = await _add_entry(hass)
    seen = []

    async def listener(hass_, entry_):
        seen.append(entry_.data["allow_mqtt_publish"])

    remove = entry.add_update_listener(listener)
    remove()

    assert hass.config_entries.async_update_entry(
        entry, data={"allow_mqtt_publish": True}
    )
    await _settle()

    assert seen == []


@pytest.mark.asyncio
async def test_a_raising_listener_does_not_break_the_update(hass):
    entry = await _add_entry(hass)
    good = []

    def boom(hass_, entry_):
        raise RuntimeError("listener exploded")

    async def listener(hass_, entry_):
        good.append(entry_.data["allow_mqtt_publish"])

    entry.add_update_listener(boom)
    entry.add_update_listener(listener)

    assert hass.config_entries.async_update_entry(
        entry, data={"allow_mqtt_publish": True}
    )
    await _settle()

    # The entry is still updated and the healthy listener still ran.
    assert entry.data == {"allow_mqtt_publish": True}
    assert good == [True]


@pytest.mark.asyncio
async def test_listener_sees_the_updated_entry(hass):
    """The listener receives (hass, entry), like HA's UpdateListenerType."""
    entry = await _add_entry(hass)
    seen = []

    async def listener(hass_, entry_):
        seen.append((hass_, entry_))

    entry.add_update_listener(listener)
    hass.config_entries.async_update_entry(entry, title="Renamed")
    await _settle()

    assert seen == [(hass, entry)]
    assert entry.title == "Renamed"
