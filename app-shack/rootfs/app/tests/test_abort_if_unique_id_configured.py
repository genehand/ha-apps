"""``ConfigFlow._abort_if_unique_id_configured`` parity with Home Assistant.

Regression tests for the meross_lan 6.0.0 discovery failure::

    WARNING: custom_components.meross_lan.profile_@0 - MerossMQTTConnection(...):
    TypeError(ConfigFlow._abort_if_unique_id_configured() got an unexpected
    keyword argument 'reload_on_update') in async_try_discovery (uuid:...)

meross_lan calls it as::

    self._abort_if_unique_id_configured(
        updates=device_config, reload_on_update=False
    )

HA's contract: abort with ``already_configured`` when an entry with this
``unique_id`` exists for the handler, after merging ``updates`` into the entry's
data and, unless ``reload_on_update`` is off, reloading it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.config_entries import SOURCE_IGNORE, SOURCE_MQTT, SOURCE_USER, AbortFlow
from shim.config_entries import ConfigEntry as ShimConfigEntry
from shim.config_entries import ConfigFlow


class _FakeConfigEntries:
    """The three ConfigEntries methods the helper touches."""

    def __init__(self, entries):
        self._entries = list(entries)
        self.updates = []
        self.scheduled_reloads = []

    def async_entry_for_domain_unique_id(self, domain, unique_id):
        for entry in self._entries:
            if entry.domain == domain and entry.unique_id == unique_id:
                return entry
        return None

    def async_update_entry(self, entry, **kwargs):
        changed = False
        for key, value in kwargs.items():
            if getattr(entry, key) != value:
                setattr(entry, key, value)
                changed = True
        if changed:
            self.updates.append((entry.entry_id, kwargs))
        return changed

    def async_schedule_reload(self, entry_id):
        self.scheduled_reloads.append(entry_id)


class _FakeHass:
    def __init__(self, config_entries):
        self.config_entries = config_entries


def _flow(
    config_entries,
    *,
    unique_id="device-uuid",
    source=SOURCE_USER,
    handler="test_domain",
):
    flow = ConfigFlow()
    flow.hass = _FakeHass(config_entries)
    flow.handler = handler
    flow.context = {"unique_id": unique_id, "source": source}
    return flow


def _entry(unique_id="device-uuid", *, domain="test_domain", **kwargs):
    entry = ShimConfigEntry(
        entry_id="entry_1",
        domain=domain,
        title="Device",
        data={"host": "10.0.0.1"},
        **kwargs,
    )
    entry.unique_id = unique_id
    return entry


def test_accepts_meross_lan_call_signature():
    """``updates=...`` + ``reload_on_update=...`` must not raise TypeError."""
    entries = _FakeConfigEntries([])
    flow = _flow(entries)

    # Exact shape of meross_lan's async_try_discovery call, no entry configured.
    flow._abort_if_unique_id_configured(
        updates={"host": "10.0.0.2"}, reload_on_update=False
    )
    assert entries.updates == []


def test_aborts_when_unique_id_is_configured():
    entry = _entry()
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries)

    with pytest.raises(AbortFlow) as excinfo:
        flow._abort_if_unique_id_configured()

    assert excinfo.value.reason == "already_configured"


def test_merges_updates_without_reloading_when_disabled():
    """The meross_lan call refreshes the stored config but does not reload."""
    entry = _entry()
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries)

    with pytest.raises(AbortFlow):
        flow._abort_if_unique_id_configured(
            updates={"host": "10.0.0.2"}, reload_on_update=False
        )

    assert entry.data == {"host": "10.0.0.2"}
    assert entries.scheduled_reloads == []


def test_reloads_a_loaded_entry_when_updates_change_data():
    entry = _entry(state="loaded")
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries)

    with pytest.raises(AbortFlow):
        flow._abort_if_unique_id_configured(updates={"host": "10.0.0.3"})

    assert entry.data == {"host": "10.0.0.3"}
    assert entries.scheduled_reloads == [entry.entry_id]


def test_does_not_reload_when_the_entry_is_not_loaded():
    entry = _entry(state="setup_error")
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries)

    with pytest.raises(AbortFlow):
        flow._abort_if_unique_id_configured(updates={"host": "10.0.0.4"})

    assert entries.scheduled_reloads == []


def test_aborts_even_when_the_data_did_not_change():
    """A duplicate discovery is an abort regardless of the payload."""
    entry = _entry()
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries)

    with pytest.raises(AbortFlow):
        flow._abort_if_unique_id_configured(updates={"host": "10.0.0.1"})

    assert entries.updates == []
    assert entries.scheduled_reloads == []


def test_discovery_reloads_an_entry_in_setup_retry():
    """Rediscovering a retrying entry proves it is back online: retry now."""
    entry = _entry(state="setup_retry")
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries, source=SOURCE_MQTT)

    with pytest.raises(AbortFlow):
        flow._abort_if_unique_id_configured()

    assert entries.scheduled_reloads == [entry.entry_id]


def test_ignored_entry_may_be_configured_again_by_the_user():
    entry = _entry(source=SOURCE_IGNORE)
    entries = _FakeConfigEntries([entry])
    flow = _flow(entries, source=SOURCE_USER)

    # No abort: the manual user step is allowed to take over an ignored entry.
    flow._abort_if_unique_id_configured()


def test_no_unique_id_never_aborts():
    entries = _FakeConfigEntries([_entry()])
    flow = _flow(entries, unique_id=None)

    flow._abort_if_unique_id_configured()
    assert entries.scheduled_reloads == []


def test_other_domain_entry_with_same_unique_id_is_ignored():
    entries = _FakeConfigEntries([_entry(domain="another_domain")])
    flow = _flow(entries)

    # The lookup is scoped to this flow's handler, like HA's.
    flow._abort_if_unique_id_configured()
