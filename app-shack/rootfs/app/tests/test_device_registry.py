"""Device registry semantics: ``async_get_or_create`` and friends.

The shim used to accept device info into a registry nothing ever wrote to, so
``DeviceEntry`` rows never existed. Once the entity-add paths started
registering them (see ``tests/test_device_registration.py``), the registry
itself has to behave like HA's:

* match an existing device by identifier/connection *intersection* (an entity
  reporting a superset of identifiers must update, not duplicate),
* merge identifiers/connections and only overwrite supplied fields,
* resolve the deprecated ``via_device`` identifier tuple to ``via_device_id``,
* expose ``config_entry_id`` (required since HA 2026.8) next to the legacy
  ``config_entries`` set,
* implement ``async_config_entry_unloaded`` / ``async_clear_config_entry`` the
  way ``ConfigEntry._async_process_on_unload`` expects.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching


@pytest.fixture
async def devices():
    """Yield the patched, per-test device registry.

    ``HomeAssistant`` captures the running loop at construction, so this has to
    be an async fixture (built inside the test's event loop).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        hass = HomeAssistant(Path(tmpdir))
        patcher = setup_import_patching(hass)
        patcher.patch()
        try:
            from homeassistant.helpers import device_registry as dr

            yield dr, dr.async_get(hass)
        finally:
            patcher.unpatch()


class TestDeviceEntry:
    """``DeviceEntry`` attribute surface."""

    def test_config_entry_id_returns_single_owner(self, devices):
        """``config_entry_id`` is the HA >= 2026.8 single-owner attribute."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen",
        )
        assert device.config_entry_id == "entry1"
        # Legacy property kept for older integrations.
        assert device.config_entries == {"entry1"}

    def test_config_entry_id_is_none_when_unowned(self, devices):
        """An entry with no config entry reports None rather than raising."""
        dr, _registry = devices
        entry = dr.DeviceEntry(id="x", config_entries=set(), connections=set(),
                               identifiers=set())
        assert entry.config_entry_id is None

    def test_disabled_property_follows_disabled_by(self, devices):
        """``disabled`` mirrors HA's ``BaseDeviceEntry.disabled``."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d2")},
        )
        assert device.disabled is False
        device.disabled_by = "user"
        assert device.disabled is True


class TestAsyncGetOrCreate:
    """Create/merge/update behaviour of ``async_get_or_create``."""

    def test_creates_a_device_with_identifiers(self, devices):
        """A fresh call creates one row with the expected fields."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen",
            manufacturer="Tuya",
            model="Model (d1)",
            sw_version="3.5",
        )
        assert registry.async_get(device.id) is device
        assert device.identifiers == {("tuya", "local_d1")}
        assert device.manufacturer == "Tuya"
        assert device.model == "Model (d1)"
        assert device.sw_version == "3.5"

    def test_identifier_superset_updates_instead_of_duplicating(self, devices):
        """A later device_info with more identifiers reuses the same row.

        HA matches on any single identifier, so an entity that reports a
        superset (a serial number added on a later firmware, say) must update
        the existing device rather than create a second one.
        """
        dr, registry = devices
        first = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen",
        )
        second = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1"), ("tuya", "serial_123")},
        )
        assert second.id == first.id
        assert len(registry._devices) == 1
        assert second.identifiers == {("tuya", "local_d1"), ("tuya", "serial_123")}
        # Name from the first call survives because the second omitted it.
        assert second.name == "Kitchen"

    def test_repeated_registration_is_idempotent(self, devices):
        """Registering the same device_info N times leaves one row."""
        dr, registry = devices
        info = {
            "identifiers": {("tuya", "local_d1")},
            "name": "Kitchen",
            "manufacturer": "Tuya",
        }
        ids = {
            registry.async_get_or_create(config_entry_id="entry1", **info).id
            for _ in range(10)
        }
        assert len(ids) == 1
        assert len(registry._devices) == 1

    def test_omitted_fields_do_not_wipe_existing_values(self, devices):
        """An entity reporting only identifiers keeps the device's metadata."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen",
            manufacturer="Tuya",
            sw_version="3.5",
        )
        registry.async_get_or_create(
            config_entry_id="entry1", identifiers={("tuya", "local_d1")}
        )
        assert device.name == "Kitchen"
        assert device.manufacturer == "Tuya"
        assert device.sw_version == "3.5"

    def test_explicit_value_overwrites(self, devices):
        """Supplied values always win, like HA."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen",
        )
        registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            name="Kitchen (renamed)",
        )
        assert device.name == "Kitchen (renamed)"

    def test_defaults_only_fill_gaps(self, devices):
        """``default_*`` values fill missing fields and never override."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            default_name="Default name",
            default_manufacturer="Default maker",
            default_model="Default model",
        )
        assert device.name == "Default name"
        assert device.manufacturer == "Default maker"
        assert device.model == "Default model"

        registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            default_name="Other name",
            name="Real name",
        )
        assert device.name == "Real name"
        assert device.manufacturer == "Default maker"

    def test_same_identifier_in_two_entries_makes_two_devices(self, devices):
        """Matching is scoped to the config entry, like HA."""
        dr, registry = devices
        a = registry.async_get_or_create(
            config_entry_id="entry_a", identifiers={("tuya", "local_1")}
        )
        b = registry.async_get_or_create(
            config_entry_id="entry_b", identifiers={("tuya", "local_1")}
        )
        assert a.id != b.id
        assert len(registry._devices) == 2
        assert a.config_entry_id == "entry_a"
        assert b.config_entry_id == "entry_b"

    def test_connections_match_the_legacy_string_form(self, devices):
        """``{"mac:aa:bb"}`` and ``{("mac", "aa:bb")}`` are the same device."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1", connections={"mac:aa:bb:cc:dd:ee"}
        )
        again = registry.async_get_or_create(
            config_entry_id="entry1", connections={("mac", "aa:bb:cc:dd:ee")}
        )
        assert again.id == device.id
        assert len(registry._devices) == 1
        assert device.connections == {("mac", "aa:bb:cc:dd:ee")}

    def test_config_subentry_id_is_accepted(self, devices):
        """``config_subentry_id`` is stored, not rejected."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            config_subentry_id="sub1",
            identifiers={("tuya", "local_d1")},
        )
        assert device.config_subentry_id == "sub1"

    def test_unknown_kwargs_are_ignored_not_fatal(self, devices):
        """A future/extra device_info key must not abort entity setup."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_d1")},
            some_future_device_info_key="whatever",
        )
        assert device.identifiers == {("tuya", "local_d1")}

    def test_device_id_is_deterministic(self, devices):
        """Ids derive from the first sorted identifier, so restarts are stable."""
        dr, registry = devices
        first = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("tuya", "local_b"), ("tuya", "local_a")},
        )
        assert first.id == "tuya_local_a"

    def test_via_device_tuple_resolves_to_via_device_id(self, devices):
        """The deprecated ``via_device`` tuple becomes a device id."""
        dr, registry = devices
        gateway = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("localtuya", "local_gw1")},
            name="Gateway",
        )
        sub = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("localtuya", "local_d1")},
            via_device=("localtuya", "local_gw1"),
        )
        assert sub.via_device_id == gateway.id
        assert sub.via_device == ("localtuya", "local_gw1")

    def test_unresolvable_via_device_is_not_fatal(self, devices):
        """A missing parent is logged, not raised (HA is lenient here)."""
        dr, registry = devices
        sub = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("localtuya", "local_d1")},
            via_device=("localtuya", "local_missing"),
        )
        assert sub.via_device_id is None
        assert sub.via_device == ("localtuya", "local_missing")

    def test_self_referencing_via_device_is_ignored(self, devices):
        """A device cannot be its own via device."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("localtuya", "local_d1")},
            via_device=("localtuya", "local_d1"),
        )
        assert device.via_device_id is None

    def test_explicit_via_device_id_wins(self, devices):
        """``via_device_id`` (HA >= 2026.8) is stored as given."""
        dr, registry = devices
        registry.async_get_or_create(
            config_entry_id="entry1", identifiers={("localtuya", "local_gw1")}
        )
        sub = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("localtuya", "local_d1")},
            via_device_id="entry1_localtuya_local_gw1",
        )
        assert sub.via_device_id == "entry1_localtuya_local_gw1"


class TestLookups:
    """Lookup helpers integrations rely on."""

    def test_async_get_device_by_identifier(self, devices):
        """Identifier lookup is scoped to the config entry."""
        dr, registry = devices
        gateway = registry.async_get_or_create(
            config_entry_id="entry1", identifiers={("localtuya", "local_gw1")}
        )
        assert (
            registry.async_get_device_by_identifier(
                ("localtuya", "local_gw1"), "entry1"
            )
            is gateway
        )
        assert (
            registry.async_get_device_by_identifier(
                ("localtuya", "local_gw1"), "other_entry"
            )
            is None
        )

    def test_async_get_device_id_by_identifier_raises_when_missing(self, devices):
        """Module helper keeps HA's ValueError contract."""
        dr, registry = devices
        from homeassistant.helpers.device_registry import (
            async_get_device_id_by_identifier,
        )

        with pytest.raises(ValueError):
            async_get_device_id_by_identifier(
                registry.hass, ("localtuya", "nope"), config_entry_id="e1"
            )

    def test_async_entries_for_config_entry_is_scoped(self, devices):
        """The module-level helper returns only that entry's devices."""
        dr, registry = devices
        a = registry.async_get_or_create(
            config_entry_id="entry_a", identifiers={("d", "a")}
        )
        b = registry.async_get_or_create(
            config_entry_id="entry_a", identifiers={("d", "b")}
        )
        registry.async_get_or_create(
            config_entry_id="entry_b", identifiers={("d", "c")}
        )
        entries = dr.async_entries_for_config_entry(registry, "entry_a")
        assert {entry.id for entry in entries} == {a.id, b.id}
        assert dr.async_entries_for_config_entry(registry, "unknown") == []
        # Instance method + shim-only alias return the same thing.
        assert registry.async_get_or_create_for_config_entry("entry_a") == entries

    def test_async_get_device_by_identifiers_or_connections(self, devices):
        """The module helper matches on both identifier and connection."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("d", "1")},
            connections={("mac", "aa:bb")},
        )
        assert dr.async_get_device(registry.hass, identifiers={("d", "1")}) is device
        assert (
            dr.async_get_device(registry.hass, connections={("mac", "aa:bb")})
            is device
        )
        assert dr.async_get_device(registry.hass, identifiers={("d", "2")}) is None


class TestLifecycle:
    """Unload/removal hooks called by the config entry machinery."""

    def test_async_config_entry_unloaded_keeps_devices(self, devices):
        """Unloading an entry forgets liveness but keeps the rows (HA parity)."""
        dr, registry = devices
        device = registry.async_get_or_create(
            config_entry_id="entry1", identifiers={("d", "1")}
        )
        registry.async_config_entry_unloaded("entry1")
        assert registry.async_get(device.id) is device
        assert registry._live_device_ids == {}

    def test_async_clear_config_entry_removes_only_its_devices(self, devices):
        """Removing a config entry drops exactly its own devices."""
        dr, registry = devices
        gone = registry.async_get_or_create(
            config_entry_id="entry1", identifiers={("d", "1")}
        )
        kept = registry.async_get_or_create(
            config_entry_id="entry2", identifiers={("d", "2")}
        )
        registry.async_clear_config_entry("entry1", "d")
        assert registry.async_get(gone.id) is None
        assert registry.async_get(kept.id) is kept


class TestChildDevices:
    """HA 2026.11 child devices: a logical part of one physical product."""

    def _parent(self, registry, identifiers=None):
        return registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers=identifiers or {("d", "strip")},
            name="Power strip",
        )

    def test_get_or_create_child_creates_a_child_row(self, devices):
        """A child carries parent_device_id and none of the parent's identity."""
        dr, registry = devices
        parent = self._parent(registry)
        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_1")},
            name="Outlet 1",
        )

        assert isinstance(child, dr.ChildDeviceEntry)
        assert child.parent_device_id == parent.id
        assert child.name == "Outlet 1"
        assert child.connections == set()
        assert registry.async_get(child.id) is child
        assert registry.async_entries_for_parent_device(parent.id) == [child]
        assert registry.async_child_entries_for_config_entry("entry1") == [child]
        # Main-device listings do not include children, like upstream.
        assert registry.async_entries_for_config_entry("entry1") == [parent]

    def test_child_from_info_uses_only_set_fields(self, devices):
        """``async_get_or_create_child_from_info`` takes a ChildDeviceInfo as-is."""
        dr, registry = devices
        parent = self._parent(registry)
        info = dr.ChildDeviceInfo(
            identifiers={("d", "strip_outlet_2")},
            parent_device_id=parent.id,
            name="Outlet 2",
        )

        child = registry.async_get_or_create_child_from_info(
            config_entry_id="entry1", device_info=info
        )
        # Re-registering without a name keeps it (UNDEFINED means "unchanged").
        again = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_2")},
        )
        assert again.id == child.id
        assert again.name == "Outlet 2"

    def test_existing_main_device_converts_to_child_in_place(self, devices):
        """Migration from via_device keeps the device id (automations survive)."""
        dr, registry = devices
        parent = self._parent(registry)
        outlet = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("d", "strip_outlet_3")},
            name="Power strip Outlet 3",
        )

        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_3")},
            name="Outlet 3",
        )

        assert child.id == outlet.id
        assert isinstance(child, dr.ChildDeviceEntry)
        assert child.parent_device_id == parent.id
        assert child.name == "Outlet 3"
        assert registry.async_get(outlet.id) is child

    def test_removing_a_parent_removes_its_children(self, devices):
        """Deletion cascades: a part has no meaning without the product."""
        dr, registry = devices
        parent = self._parent(registry)
        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_1")},
        )

        registry.async_remove_device(parent.id)
        assert registry.async_get(parent.id) is None
        assert registry.async_get(child.id) is None

    def test_disabling_a_parent_disables_its_children(self, devices):
        """Disable cascades down, and enabling the parent releases them."""
        dr, registry = devices
        parent = self._parent(registry)
        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_1")},
        )

        registry.async_update_device(parent.id, disabled_by="user")
        assert child.disabled_by == "device"
        assert child.disabled

        registry.async_update_device(parent.id, disabled_by=None)
        assert child.disabled_by is None

    def test_child_area_inherits_from_parent(self, devices):
        """A child with no area of its own reports the parent's area."""
        dr, registry = devices
        parent = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("d", "strip")},
            name="Power strip",
            area_id=None,
        )
        parent.area_id = "garage"
        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_1")},
        )

        assert dr.async_get_effective_area_id(registry.hass, child) == "garage"
        child.area_id = "garden"
        assert dr.async_get_effective_area_id(registry.hass, child) == "garden"

    def test_child_requires_identifiers_and_parent(self, devices):
        """Upstream rejects both; the shim raises ``ValueError``."""
        dr, registry = devices
        with pytest.raises(ValueError):
            registry.async_get_or_create_child(
                config_entry_id="entry1",
                parent_device_id="missing",
                identifiers=set(),
            )
        with pytest.raises(ValueError):
            registry.async_get_or_create_child_from_info(
                config_entry_id="entry1",
                device_info=dr.ChildDeviceInfo(
                    identifiers={("d", "x")}, parent_device_id=""
                ),
            )

    def test_a_child_cannot_be_a_parent(self, devices):
        """Child devices are a single level (HA 2026.11)."""
        dr, registry = devices
        parent = self._parent(registry)
        child = registry.async_get_or_create_child(
            config_entry_id="entry1",
            parent_device_id=parent.id,
            identifiers={("d", "strip_outlet_1")},
        )
        with pytest.raises(ValueError):
            registry.async_get_or_create_child(
                config_entry_id="entry1",
                parent_device_id=child.id,
                identifiers={("d", "strip_outlet_1_gang_1")},
            )


class TestGetOrCreateFromInfo:
    """``async_get_or_create_from_info`` (HA 2026.11)."""

    def test_unset_fields_leave_the_existing_row_alone(self, devices):
        """Only set fields are applied; ``UNDEFINED`` means "unchanged"."""
        dr, registry = devices
        created = registry.async_get_or_create(
            config_entry_id="entry1",
            identifiers={("d", "1")},
            name="Kitchen",
            manufacturer="Acme",
        )

        updated = registry.async_get_or_create_from_info(
            config_entry_id="entry1",
            device_info=dr.DeviceInfo(
                identifiers={("d", "1")}, sw_version="2.0"
            ),
        )

        assert updated is created
        assert updated.sw_version == "2.0"
        assert updated.name == "Kitchen"
        assert updated.manufacturer == "Acme"
