"""Tests for the DeviceInfo dict-style compatibility layer.

In real HA, ``DeviceInfo`` is a ``TypedDict`` - a plain dict at runtime - so
integrations read and write it with item access as well as constructor kwargs
(e.g. localtuya's ``device_info[ATTR_VIA_DEVICE] = (DOMAIN, gateway_id)``).
The shim backs it with a dataclass for attribute access, so these tests pin
down the dict protocol added for integration compatibility.
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.stubs.helpers import DeviceInfo


class TestDeviceInfoDictProtocol:
    """Tests for item access on the DeviceInfo dataclass."""

    def test_item_assignment_maps_to_known_field(self):
        """Item assignment on a field name updates the attribute."""
        info = DeviceInfo(identifiers={('tuya', 'local_d1')}, name='Kitchen')
        info['via_device'] = ('tuya', 'local_gw')
        assert info.via_device == ('tuya', 'local_gw')
        assert info['via_device'] == ('tuya', 'local_gw')

    def test_item_read_of_known_field(self):
        """Item reads and get() return the field value."""
        info = DeviceInfo(name='Kitchen', sw_version='3.5')
        assert info['name'] == 'Kitchen'
        assert info.get('name') == 'Kitchen'
        assert info.get('missing_key', 'fallback') == 'fallback'

    def test_item_read_of_unknown_key_raises_key_error(self):
        """Reading a key that is neither a field nor stored raises KeyError."""
        info = DeviceInfo(name='Kitchen')
        with pytest.raises(KeyError):
            info['no_such_key']

    def test_unknown_keys_kept_in_extra(self):
        """Keys that are not fields are stored and retrieved unchanged."""
        info = DeviceInfo(name='Kitchen')
        info['future_ha_key'] = 'some value'
        assert info['future_ha_key'] == 'some value'
        assert info.get('future_ha_key') == 'some value'
        assert 'future_ha_key' in info
        assert info.keys()[-1] == 'future_ha_key'

    def test_contains_known_fields_and_defaults(self):
        """All field names are present, with dataclass defaults applied."""
        info = DeviceInfo()
        assert 'identifiers' in info
        assert 'via_device' in info
        assert info.identifiers == set()
        assert info.via_device is None
        assert 'no_such_key' not in info

    def test_update_from_mapping_pairs_and_kwargs(self):
        """update() accepts mappings, iterables of pairs, and kwargs."""
        info = DeviceInfo()
        info.update({'name': 'Kitchen'})
        info.update([('model', 'Model X')])
        info.update(manufacturer='Tuya')
        assert info.name == 'Kitchen'
        assert info.model == 'Model X'
        assert info.manufacturer == 'Tuya'

    def test_items_values_keys_include_fields_and_extra(self):
        """keys()/values()/items() cover field and extra keys."""
        info = DeviceInfo(name='Kitchen')
        info['future_ha_key'] = 1
        assert info.keys()[:1] == ['identifiers']
        assert info['name'] == 'Kitchen'
        items = dict(info.items())
        assert items['name'] == 'Kitchen'
        assert items['future_ha_key'] == 1
        assert 'via_device' in info.keys()

    def test_iteration_yields_keys(self):
        """Iterating the object yields its keys like a dict."""
        info = DeviceInfo(name='Kitchen')
        assert 'name' in list(info)

    def test_delitem_resets_field_to_default(self):
        """Deleting a field key resets it to its dataclass default."""
        info = DeviceInfo(name='Kitchen')
        info['via_device'] = ('tuya', 'local_gw')
        del info['via_device']
        assert info.via_device is None
        assert 'via_device' in info  # fields remain present

    def test_delitem_removes_extra_key(self):
        """Deleting a non-field key removes it from the extras storage."""
        info = DeviceInfo()
        info['future_ha_key'] = 1
        del info['future_ha_key']
        assert 'future_ha_key' not in info

    def test_constructor_kwargs_still_populate_fields(self):
        """Constructor kwargs and attribute access keep working."""
        info = DeviceInfo(
            identifiers={('tuya', 'local_d1')},
            name='Kitchen',
            via_device_id='abc123',
        )
        assert info.identifiers == {('tuya', 'local_d1')}
        assert info.name == 'Kitchen'
        assert info.via_device_id == 'abc123'

    def test_equality_and_repr_ignore_extra(self):
        """Equality and repr are unaffected by the extras storage."""
        info = DeviceInfo(name='Kitchen')
        info['future_ha_key'] = 1
        assert DeviceInfo(name='Kitchen') == info
        assert 'future_ha_key' not in repr(info)

    def test_get_device_info_attr_still_reads_both_styles(self):
        """The shim's attr helper reads fields set either way."""
        from shim.entity import get_device_info_attr

        info = DeviceInfo(name='Kitchen')
        info['via_device'] = ('tuya', 'local_gw')
        assert get_device_info_attr(info, 'name') == 'Kitchen'
        assert get_device_info_attr(info, 'via_device') == ('tuya', 'local_gw')


class TestDeviceInfoLocaltuyaPattern:
    """Regression tests reproducing the localtuya device_info pattern.

    localtuya's entity.py builds a DeviceInfo and then, for sub-devices,
    item-assigns the legacy via-device link:

        device_info[ATTR_VIA_DEVICE] = (DOMAIN, f"local_{gateway.id}")

    Under real HA this works because DeviceInfo is a dict; it must not raise
    under the shim either.
    """

    @pytest.mark.asyncio
    async def test_subdevice_device_info_does_not_raise(self):
        """Evaluate the real localtuya property for a sub-device entity."""
        from shim.core import HomeAssistant
        from shim.import_patch import ImportPatcher

        data_shim = Path(__file__).parent.parent / 'data' / 'shim'
        sys.path.insert(0, str(data_shim))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hass = HomeAssistant(Path(tmpdir))
                patcher = ImportPatcher(hass)
                patcher.patch()
                try:
                    from custom_components.localtuya.entity import LocalTuyaEntity
                    from homeassistant.const import ATTR_VIA_DEVICE
                    from homeassistant.helpers.device_registry import DeviceInfo

                    entity = object.__new__(LocalTuyaEntity)
                    entity._device_config = SimpleNamespace(
                        id='d1',
                        name='Sub device',
                        model='Model',
                        protocol_version='3.5',
                    )
                    entity._device = SimpleNamespace(
                        id='sub1',
                        is_subdevice=True,
                        gateway=SimpleNamespace(id='gw1'),
                    )

                    info = entity.device_info
                    assert isinstance(info, DeviceInfo)
                    assert info['via_device'] == ('localtuya', 'local_gw1')
                    assert info.via_device == ('localtuya', 'local_gw1')
                    assert info.identifiers == {('localtuya', 'local_d1')}
                    assert ATTR_VIA_DEVICE == 'via_device'
                finally:
                    patcher.unpatch()
        finally:
            sys.path.remove(str(data_shim))

    @pytest.mark.asyncio
    async def test_gateway_device_info_unaffected(self):
        """Non-sub-device entities keep a plain DeviceInfo (no via link)."""
        from shim.core import HomeAssistant
        from shim.import_patch import ImportPatcher

        data_shim = Path(__file__).parent.parent / 'data' / 'shim'
        sys.path.insert(0, str(data_shim))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hass = HomeAssistant(Path(tmpdir))
                patcher = ImportPatcher(hass)
                patcher.patch()
                try:
                    from custom_components.localtuya.entity import LocalTuyaEntity
                    from homeassistant.helpers.device_registry import DeviceInfo

                    entity = object.__new__(LocalTuyaEntity)
                    entity._device_config = SimpleNamespace(
                        id='gw1',
                        name='Gateway',
                        model='Model',
                        protocol_version='3.5',
                    )
                    entity._device = SimpleNamespace(
                        id='gw1',
                        is_subdevice=False,
                        gateway=SimpleNamespace(id='gw1'),
                    )

                    info = entity.device_info
                    assert isinstance(info, DeviceInfo)
                    assert info['via_device'] is None
                    assert info.via_device is None
                    assert info.identifiers == {('localtuya', 'local_gw1')}
                finally:
                    patcher.unpatch()
        finally:
            sys.path.remove(str(data_shim))
