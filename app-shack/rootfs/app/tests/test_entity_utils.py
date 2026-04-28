"""Tests for entity utilities and helpers."""

import pytest
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.entity import (
    get_mqtt_entity_id,
    format_device_identifiers,
    get_device_info_attr,
)


class TestGetMqttEntityId:
    """Test cases for get_mqtt_entity_id function."""

    def test_simple_entity_id(self):
        """Test conversion of simple entity ID."""
        # Input: fan.living_room
        # Expected: living-room (underscores become dashes)
        result = get_mqtt_entity_id("fan.living_room")
        assert result == "living-room"

    def test_entity_id_with_domain(self):
        """Test that domain prefix is removed."""
        result = get_mqtt_entity_id("sensor.temperature_sensor")
        assert result == "temperature-sensor"

    def test_entity_id_with_multiple_underscores(self):
        """Test conversion with multiple underscores."""
        result = get_mqtt_entity_id("light.bedroom_ceiling_light")
        assert result == "bedroom-ceiling-light"

    def test_entity_id_with_dots_in_name(self):
        """Test conversion with dots in entity name."""
        # This is an edge case - dots in the entity name itself
        result = get_mqtt_entity_id("switch.living.room")
        assert result == "living-room"

    def test_entity_id_already_dash_separated(self):
        """Test entity ID that already uses dashes."""
        result = get_mqtt_entity_id("sensor.air-quality")
        assert result == "air-quality"

    def test_entity_id_no_domain(self):
        """Test entity ID without domain prefix."""
        result = get_mqtt_entity_id("living_room")
        assert result == "living-room"

    def test_entity_id_deduplicates_consecutive_segments(self):
        """Test that consecutive duplicate segments are deduplicated."""
        # Flightradar24 pattern: domain.object_id with redundant domain
        result = get_mqtt_entity_id(
            "sensor.flightradar24_40897_75808525_flightradar24_in_area"
        )
        # Should remove the duplicate "flightradar24" segment
        assert result == "flightradar24-40897-75808525-in-area"

    def test_entity_id_deduplicates_multiple_duplicates(self):
        """Test deduplication with multiple duplicate segments."""
        result = get_mqtt_entity_id("switch.a_b_a_b_test")
        # Should keep first 'a' and 'b', skip duplicates
        assert result == "a-b-test"

    def test_entity_id_removes_all_duplicates(self):
        """Test that all duplicate segments are removed, not just consecutive."""
        # Even non-consecutive duplicates should be removed
        result = get_mqtt_entity_id("sensor.flightradar24_123_test_flightradar24_end")
        assert result == "flightradar24-123-test-end"


class TestFormatDeviceIdentifiers:
    """Test cases for format_device_identifiers function."""

    def test_tuple_identifiers(self):
        """Test formatting of tuple identifiers."""
        identifiers = {("flightradar24", "12345"), ("other", "67890")}
        result = format_device_identifiers(identifiers)
        assert "flightradar24-12345" in result
        assert "other-67890" in result
        assert len(result) == 2

    def test_list_identifiers(self):
        """Test formatting of list identifiers."""
        # Note: Lists can't be set elements, but function should handle them if passed
        identifiers = [("integration", "device1"), ("integration", "device2")]
        result = format_device_identifiers(identifiers)
        assert "integration-device1" in result
        assert "integration-device2" in result

    def test_string_identifiers(self):
        """Test formatting of string identifiers."""
        identifiers = {"simple_id", "another_id"}
        result = format_device_identifiers(identifiers)
        assert "simple_id" in result
        assert "another_id" in result

    def test_empty_identifiers(self):
        """Test formatting of empty identifiers set."""
        identifiers = set()
        result = format_device_identifiers(identifiers)
        assert result == []

    def test_mixed_identifiers(self):
        """Test formatting of mixed identifier types."""
        identifiers = {("tuple", "id"), "string_id", ("list", "id")}
        result = format_device_identifiers(identifiers)
        assert len(result) == 3
        assert "tuple-id" in result
        assert "string_id" in result
        assert "list-id" in result


class TestGetDeviceInfoAttr:
    """Test cases for get_device_info_attr function."""

    def test_dict_device_info(self):
        """Test getting attribute from dict device_info."""
        device_info = {"name": "Test Device", "model": "Model X"}
        result = get_device_info_attr(device_info, "name")
        assert result == "Test Device"

    def test_dataclass_device_info(self):
        """Test getting attribute from dataclass device_info."""
        from dataclasses import dataclass

        @dataclass
        class DeviceInfo:
            name: str
            model: str

        device_info = DeviceInfo(name="Test Device", model="Model X")
        result = get_device_info_attr(device_info, "name")
        assert result == "Test Device"

    def test_none_device_info(self):
        """Test getting attribute when device_info is None."""
        result = get_device_info_attr(None, "name")
        assert result is None

    def test_default_value(self):
        """Test that default value is returned when attribute not found."""
        device_info = {"name": "Test Device"}
        result = get_device_info_attr(device_info, "missing_attr", "default")
        assert result == "default"


class TestGetMqttObjectId:
    """Test cases for get_mqtt_object_id function."""

    def test_get_mqtt_object_id_with_dots(self):
        """Test get_mqtt_object_id with dots in entity_id (flightradar24 style)."""
        from shim.entity import get_mqtt_object_id

        # Flightradar24 uses dots in unique_ids
        entity_id = "switch.flightradar24_41831.336792666_scanning"
        result = get_mqtt_object_id(entity_id)

        # Should convert dots and underscores to dashes
        assert result == "flightradar24-41831-336792666-scanning"

    def test_get_mqtt_object_id_simple(self):
        """Test get_mqtt_object_id with simple entity_id."""
        from shim.entity import get_mqtt_object_id

        entity_id = "sensor.living_room_temperature"
        result = get_mqtt_object_id(entity_id)

        assert result == "living-room-temperature"


class TestEntityMqttObjectId:
    """Test cases for Entity.mqtt_object_id property."""

    def test_mqtt_object_id_from_entity_id(self):
        """Test mqtt_object_id property returns correct value."""
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "switch.flightradar24_41831.336792666_scanning"

        assert entity.mqtt_object_id == "flightradar24-41831-336792666-scanning"

    def test_mqtt_object_id_none_when_no_entity_id(self):
        """Test mqtt_object_id returns None when entity_id is not set."""
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = None

        assert entity.mqtt_object_id is None


class TestGetEntityNameForDiscovery:
    """Test cases for get_entity_name_for_discovery function."""

    def test_strips_device_name_prefix(self):
        """Test that device name prefix is stripped from entity name."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Living Room Temperature"
        device_info = {"name": "Living Room"}

        result = get_entity_name_for_discovery(entity_name, device_info)
        assert result == "Temperature"

    def test_preserves_name_without_prefix(self):
        """Test that names without device prefix are preserved."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Kitchen Light"
        device_info = {"name": "Living Room"}

        result = get_entity_name_for_discovery(entity_name, device_info)
        assert result == "Kitchen Light"

    def test_returns_none_when_same_as_device(self):
        """Test that None is returned when entity name equals device name (HA convention)."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Living Room"
        device_info = {"name": "Living Room"}

        result = get_entity_name_for_discovery(entity_name, device_info)
        assert result is None

    def test_returns_none_for_none_input(self):
        """Test that None is returned for None input."""
        from shim.entity import get_entity_name_for_discovery

        result = get_entity_name_for_discovery(None, {"name": "Device"})
        assert result is None

    def test_handles_none_device_info(self):
        """Test that entity name is returned when device_info is None."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Test Sensor"
        result = get_entity_name_for_discovery(entity_name, None)
        assert result == "Test Sensor"

    def test_handles_device_without_name(self):
        """Test that entity name is returned when device has no name."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Test Sensor"
        device_info = {"model": "Model X"}  # No name key
        result = get_entity_name_for_discovery(entity_name, device_info)
        assert result == "Test Sensor"

    def test_has_entity_name_returns_suffix_as_is(self):
        """Test that has_entity_name=True returns entity name as-is (it's already a suffix)."""
        from shim.entity import get_entity_name_for_discovery

        # When has_entity_name=True, the entity name is a suffix, not a full name
        entity_name = "Alerts"  # Just the suffix, not "NWS Alerts Alerts"
        device_info = {"name": "NWS Alerts (Zone: CAZ043)"}

        result = get_entity_name_for_discovery(
            entity_name, device_info, has_entity_name=True
        )
        assert result == "Alerts"

    def test_has_entity_name_returns_none_for_empty_name(self):
        """Test that has_entity_name=True returns None for empty entity name."""
        from shim.entity import get_entity_name_for_discovery

        result = get_entity_name_for_discovery(
            None, {"name": "Device"}, has_entity_name=True
        )
        assert result is None

    def test_has_entity_name_returns_none_when_matches_device(self):
        """Test that has_entity_name=True returns None when entity name matches device."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "NWS Alerts"
        device_info = {"name": "NWS Alerts"}

        result = get_entity_name_for_discovery(
            entity_name, device_info, has_entity_name=True
        )
        assert result is None

    def test_has_entity_name_false_uses_legacy_behavior(self):
        """Test that has_entity_name=False (default) uses legacy naming behavior."""
        from shim.entity import get_entity_name_for_discovery

        # Legacy naming: entity name includes device prefix
        entity_name = "NWS Alerts Alerts"  # Full name with duplication issue
        device_info = {"name": "NWS Alerts"}

        result = get_entity_name_for_discovery(
            entity_name, device_info, has_entity_name=False
        )
        # Should strip the "NWS Alerts " prefix
        assert result == "Alerts"

    def test_has_entity_name_default_is_false(self):
        """Test that default has_entity_name value is False."""
        from shim.entity import get_entity_name_for_discovery

        entity_name = "Device Temperature"
        device_info = {"name": "Device"}

        # Not passing has_entity_name - should default to False
        result = get_entity_name_for_discovery(entity_name, device_info)
        assert result == "Temperature"


class TestEntityHasEntityName:
    """Test cases for Entity.has_entity_name property."""

    def test_has_entity_name_from_entity_description(self):
        """Test that has_entity_name comes from entity_description."""
        from shim.entity import Entity, EntityDescription

        entity = Entity()
        entity.entity_description = EntityDescription(key="test", has_entity_name=True)

        assert entity.has_entity_name is True

    def test_has_entity_name_from_attr(self):
        """Test that has_entity_name can come from _attr_has_entity_name."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_has_entity_name = True

        assert entity.has_entity_name is True

    def test_has_entity_name_defaults_to_false(self):
        """Test that has_entity_name defaults to False."""
        from shim.entity import Entity

        entity = Entity()

        assert entity.has_entity_name is False

    def test_has_entity_name_from_description_defaults_to_false(self):
        """Test that has_entity_name from description defaults to False if not set."""
        from shim.entity import Entity, EntityDescription

        entity = Entity()
        entity.entity_description = EntityDescription(
            key="test"
        )  # No has_entity_name specified

        assert entity.has_entity_name is False


class TestEntityNameFromTranslationKey:
    """Test cases for Entity name property with _attr_translation_key."""

    def test_name_from_attr_translation_key(self):
        """Test that name falls back to _attr_translation_key when no other name is set."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_name = None
        entity._attr_translation_key = "recirculation_switch"

        assert entity.name == "Recirculation Switch"

    def test_name_from_translation_key_underscore_conversion(self):
        """Test that translation_key is converted from snake_case to Title Case."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_name = None
        entity._attr_translation_key = "outlet_temperature"

        assert entity.name == "Outlet Temperature"

    def test_attr_name_priority_over_translation_key(self):
        """Test that _attr_name takes priority over _attr_translation_key."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_name = "Custom Name"
        entity._attr_translation_key = "recirculation_switch"

        assert entity.name == "Custom Name"

    def test_entity_description_name_priority_over_translation_key(self):
        """Test that entity_description.name takes priority over _attr_translation_key."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor._attr_name = None
        sensor._attr_translation_key = "recirculation_switch"
        sensor.entity_description = SensorEntityDescription(
            key="test", name="Description Name"
        )

        assert sensor.name == "Description Name"

    def test_entity_description_translation_key_priority_over_attr_translation_key(
        self,
    ):
        """Test that entity_description.translation_key takes priority over _attr_translation_key."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor._attr_name = None
        sensor._attr_translation_key = "recirculation_switch"
        sensor.entity_description = SensorEntityDescription(
            key="test", translation_key="outlet_temperature"
        )

        assert sensor.name == "Outlet Temperature"

    def test_entity_description_key_takes_priority_over_attr_translation_key(self):
        """Test that entity_description.key takes priority over _attr_translation_key."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor._attr_name = None
        sensor._attr_translation_key = "recirculation_switch"
        sensor.entity_description = SensorEntityDescription(
            key="outlet_temp"  # This would become "Outlet Temp"
        )

        # entity_description.key should win over _attr_translation_key
        # (entity_description is more specific)
        assert sensor.name == "Outlet Temp"

    def test_attr_translation_key_used_when_no_entity_description(self):
        """Test that _attr_translation_key is used when no entity_description exists."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_name = None
        entity._attr_translation_key = "recirculation_switch"
        # No entity_description set

        assert entity.name == "Recirculation Switch"

    def test_name_none_when_no_sources_available(self):
        """Test that name returns None when no name sources are available."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_name = None
        # No entity_description, no _attr_translation_key

        assert entity.name is None


class TestBuildMqttDeviceConfig:
    """Test cases for build_mqtt_device_config function."""

    def test_build_device_config_with_all_fields(self):
        """Test building config with all device info fields."""
        from shim.entity import build_mqtt_device_config

        device_info = {
            "identifiers": {("flightradar24", "12345")},
            "name": "FlightRadar24 Device",
            "manufacturer": "FlightRadar24",
            "model": "Integration",
            "sw_version": "1.0.0",
        }

        result = build_mqtt_device_config(device_info)

        assert result["identifiers"] == ["flightradar24-12345"]
        assert result["name"] == "FlightRadar24 Device"
        assert result["manufacturer"] == "FlightRadar24"
        assert result["model"] == "Integration"
        assert result["sw_version"] == "1.0.0"

    def test_build_device_config_skips_none_values(self):
        """Test that None values are skipped in device config."""
        from shim.entity import build_mqtt_device_config

        device_info = {
            "identifiers": {("test", "123")},
            "name": "Test Device",
            "manufacturer": None,
            "model": None,
        }

        result = build_mqtt_device_config(device_info)

        assert "manufacturer" not in result
        assert "model" not in result
        assert "name" in result

    def test_build_device_config_partial_fields(self):
        """Test building config with only some fields."""
        from shim.entity import build_mqtt_device_config

        device_info = {
            "identifiers": {("test", "123")},
            "name": "Test Device",
            "manufacturer": "Test Corp",
        }

        result = build_mqtt_device_config(device_info)

        assert result["manufacturer"] == "Test Corp"
        assert "model" not in result
        assert "sw_version" not in result

    def test_build_device_config_none_device_info(self):
        """Test building config when device_info is None."""
        from shim.entity import build_mqtt_device_config

        result = build_mqtt_device_config(None)
        assert result == {}

    def test_build_device_config_fallback_to_registry(self):
        """Test that manufacturer/model/sw_version fallback to device registry."""
        from unittest.mock import MagicMock, patch
        from shim.entity import build_mqtt_device_config
        from shim.stubs.helpers import DeviceEntry, DeviceRegistry

        # Create a registry with a device that has manufacturer/model/sw_version
        registry = DeviceRegistry(None)
        registry.async_get_or_create(
            config_entry_id="test_entry",
            identifiers={("smartcar", "test-vin")},
            manufacturer="Volkswagen",
            model="ID.4 (2024)",
            name="Volkswagen ID.4",
            sw_version="5.4.3",
        )

        # Patch the device registry lookup to return our registry
        with patch("shim.stubs.helpers._global_device_registry", registry):
            device_info = {"identifiers": {("smartcar", "test-vin")}}
            result = build_mqtt_device_config(device_info)

        assert result["name"] == "Volkswagen ID.4"
        assert result["manufacturer"] == "Volkswagen"
        assert result["model"] == "ID.4 (2024)"
        assert result["sw_version"] == "5.4.3"

    def test_build_device_config_prefers_device_info_over_registry(self):
        """Test that device_info values take precedence over registry."""
        from unittest.mock import patch
        from shim.entity import build_mqtt_device_config
        from shim.stubs.helpers import DeviceRegistry

        registry = DeviceRegistry(None)
        registry.async_get_or_create(
            config_entry_id="test_entry",
            identifiers={("smartcar", "test-vin")},
            manufacturer="Volkswagen",
            model="ID.4 (2024)",
            name="Volkswagen ID.4",
            sw_version="5.4.3",
        )

        with patch("shim.stubs.helpers._global_device_registry", registry):
            device_info = {
                "identifiers": {("smartcar", "test-vin")},
                "manufacturer": "Override Manufacturer",
                "model": "Override Model",
                "sw_version": "Override Version",
                "name": "Override Name",
            }
            result = build_mqtt_device_config(device_info)

        assert result["name"] == "Override Name"
        assert result["manufacturer"] == "Override Manufacturer"
        assert result["model"] == "Override Model"
        assert result["sw_version"] == "Override Version"

    def test_build_device_config_partial_registry_fallback(self):
        """Test mixed device_info and registry fallback."""
        from unittest.mock import patch
        from shim.entity import build_mqtt_device_config
        from shim.stubs.helpers import DeviceRegistry

        registry = DeviceRegistry(None)
        registry.async_get_or_create(
            config_entry_id="test_entry",
            identifiers={("smartcar", "test-vin")},
            manufacturer="Volkswagen",
            model="ID.4 (2024)",
            name="Volkswagen ID.4",
            sw_version="5.4.3",
        )

        with patch("shim.stubs.helpers._global_device_registry", registry):
            device_info = {
                "identifiers": {("smartcar", "test-vin")},
                "manufacturer": "Override Manufacturer",
            }
            result = build_mqtt_device_config(device_info)

        assert result["manufacturer"] == "Override Manufacturer"
        assert result["model"] == "ID.4 (2024)"
        assert result["sw_version"] == "5.4.3"
        assert result["name"] == "Volkswagen ID.4"


class TestMqttCleanup:
    """Test cases for MQTT cleanup on entity removal."""

    @pytest.mark.asyncio
    async def test_mqtt_cleanup_on_entity_removal(self):
        """Test that MQTT topics are cleaned up when entity is removed."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        # Create entity
        entity = Entity()
        entity._attr_name = "Test Sensor"
        entity.entity_id = "sensor.test_device"
        entity._attr_unique_id = "test_unique_123"

        # Create mock hass with mqtt client
        mock_mqtt = MagicMock()
        mock_mqtt.publish = MagicMock()

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        mock_hass.states = MagicMock()
        mock_hass.states.async_remove = MagicMock()
        entity.hass = mock_hass
        entity._added = True

        # Call async_remove
        await entity.async_remove(cleanup_mqtt=True)

        # Verify mqtt.publish was called (cleanup happened)
        assert mock_mqtt.publish.called

    @pytest.mark.asyncio
    async def test_no_mqtt_cleanup_when_disabled(self):
        """Test that MQTT cleanup is skipped when cleanup_mqtt=False."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        # Create entity
        entity = Entity()
        entity._attr_name = "Test Sensor"
        entity.entity_id = "sensor.test_device"
        entity._attr_unique_id = "test_unique_123"

        # Create mock hass with mqtt client
        mock_mqtt = MagicMock()
        mock_mqtt.publish = MagicMock()

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        mock_hass.states = MagicMock()
        mock_hass.states.async_remove = MagicMock()
        entity.hass = mock_hass
        entity._added = True

        # Call async_remove with cleanup_mqtt=False
        await entity.async_remove(cleanup_mqtt=False)

        # Verify mqtt.publish was NOT called (cleanup skipped)
        assert not mock_mqtt.publish.called, (
            "MQTT publish should NOT have been called when cleanup_mqtt=False"
        )

    @pytest.mark.asyncio
    async def test_mqtt_cleanup_topic_names(self):
        """Test that correct MQTT topics are cleaned up."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        # Create entity with underscores in unique_id (like flightradar24 sensors)
        entity = Entity()
        entity._attr_name = "FlightRadar24 Sensor"
        entity.entity_id = "sensor.flightradar24_40243_843390375_flightradar24_airport_departures_canceled"
        entity._attr_unique_id = (
            "flightradar24_40243.843390375_flightradar24_airport_departures_canceled"
        )

        # Create mock hass with mqtt client
        mock_mqtt = MagicMock()
        mock_mqtt.publish = MagicMock()

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        mock_hass.states = MagicMock()
        mock_hass.states.async_remove = MagicMock()
        entity.hass = mock_hass
        entity._added = True

        # Call async_remove
        await entity.async_remove(cleanup_mqtt=True)

        # Get all publish calls
        publish_calls = mock_mqtt.publish.call_args_list

        # Verify topics use correct entity_id (lowercase platform)
        for call in publish_calls:
            args, kwargs = call
            topic = args[0]
            # Topic should start with homeassistant/sensor/, not homeassistant/SENSOR/
            assert (
                "homeassistant/sensor/" in topic or "homeassistant/SENSOR/" not in topic
            ), f"Topic should use lowercase 'sensor': {topic}"

            # Verify dashes are used (from get_mqtt_entity_id conversion)
            assert "flightradar24" in topic or "flight-radar" in topic.lower(), (
                f"Topic should contain entity identifier: {topic}"
            )


class TestSensorEntityName:
    """Test cases for SensorEntity name property."""

    def test_sensor_name_from_attr_name(self):
        """Test that name returns _attr_name when set."""
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor._attr_name = "Test Sensor Name"

        assert sensor.name == "Test Sensor Name"

    def test_sensor_name_from_entity_description(self):
        """Test that name falls back to entity_description.name when _attr_name not set."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor._attr_name = None
        sensor.entity_description = SensorEntityDescription(
            key="test_key", name="Description Name"
        )

        assert sensor.name == "Description Name"

    def test_sensor_name_priority_attr_over_description(self):
        """Test that _attr_name takes priority over entity_description.name."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor._attr_name = "Attr Name"
        sensor.entity_description = SensorEntityDescription(
            key="test_key", name="Description Name"
        )

        # _attr_name should win
        assert sensor.name == "Attr Name"

    def test_sensor_name_none_when_both_empty(self):
        """Test that name returns None when neither _attr_name nor entity_description is set."""
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor._attr_name = None
        sensor.entity_description = None

        assert sensor.name is None

    def test_sensor_name_with_flightradar24_style_entity(self):
        """Test name extraction for flightradar24-style sensors."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        # Simulate a flightradar24 sensor
        sensor = SensorEntity()
        sensor._attr_name = None
        sensor.entity_description = SensorEntityDescription(
            key="in_area", name="Current in area"
        )

        assert sensor.name == "Current in area"

    def test_sensor_name_falls_back_to_parent_class(self):
        """Test that SensorEntity.name falls back to parent class name property.

        This ensures integrations like dyson_local that define their own name
        property (e.g., DysonEntity.name) still work correctly.
        """
        from shim.platforms.sensor import SensorEntity
        from shim.entity import Entity

        # Create a parent class that defines its own name property
        # (simulating DysonEntity)
        class ParentEntity(Entity):
            def __init__(self):
                super().__init__()
                self._device_name = "Device Name"
                self._sub_name = "Humidity"

            @property
            def name(self):
                return f"{self._device_name} {self._sub_name}"

        class TestSensor(SensorEntity, ParentEntity):
            pass

        sensor = TestSensor()
        sensor._attr_name = None
        sensor.entity_description = None

        # Should use parent class name property
        assert sensor.name == "Device Name Humidity"

    def test_sensor_name_prefers_attr_over_parent(self):
        """Test that _attr_name takes priority over parent class name."""
        from shim.platforms.sensor import SensorEntity
        from shim.entity import Entity

        class ParentEntity(Entity):
            @property
            def name(self):
                return "Parent Name"

        class TestSensor(SensorEntity, ParentEntity):
            pass

        sensor = TestSensor()
        sensor._attr_name = "Sensor Attr Name"

        # _attr_name should win over parent
        assert sensor.name == "Sensor Attr Name"

    def test_sensor_name_prefers_description_over_parent(self):
        """Test that entity_description.name takes priority over parent class name."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from shim.entity import Entity

        class ParentEntity(Entity):
            @property
            def name(self):
                return "Parent Name"

        class TestSensor(SensorEntity, ParentEntity):
            pass

        sensor = TestSensor()
        sensor._attr_name = None
        sensor.entity_description = SensorEntityDescription(
            key="test", name="Description Name"
        )

        # Should use entity_description.name, not parent class name
        assert sensor.name == "Description Name"


class TestDirectAttributeAssignment:
    """Test cases for directly assigned attributes (e.g., entity.icon = '...')."""

    def test_entity_icon_direct_assignment(self):
        """Test that icon property returns directly assigned icon."""
        from shim.entity import Entity

        entity = Entity()
        # Direct assignment via __dict__ (bypasses property)
        entity.__dict__["icon"] = "mdi:airplane"

        assert entity.icon == "mdi:airplane"

    def test_entity_icon_attr_fallback(self):
        """Test that icon falls back to _attr_icon when no direct assignment."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_icon = "mdi:default"

        assert entity.icon == "mdi:default"

    def test_entity_device_class_direct_assignment(self):
        """Test that device_class property returns directly assigned device_class."""
        from shim.entity import Entity

        entity = Entity()
        # Direct assignment via __dict__
        entity.__dict__["device_class"] = "temperature"

        assert entity.device_class == "temperature"

    def test_entity_device_class_from_entity_description(self):
        """Test that device_class comes from entity_description."""
        from shim.entity import Entity, EntityDescription

        entity = Entity()
        entity.entity_description = EntityDescription(
            key="test", name="Test", device_class="occupancy"
        )

        assert entity.device_class == "occupancy"

    def test_entity_device_class_attr_fallback(self):
        """Test that device_class falls back to _attr_device_class."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_device_class = "power"

        assert entity.device_class == "power"

    def test_entity_icon_from_entity_description(self):
        """Test that icon comes from entity_description."""
        from shim.entity import Entity, EntityDescription

        entity = Entity()
        entity.entity_description = EntityDescription(
            key="test", name="Test", icon="mdi:account"
        )

        assert entity.icon == "mdi:account"

    def test_entity_icon_attr_fallback(self):
        """Test that icon falls back to _attr_icon."""
        from shim.entity import Entity

        entity = Entity()
        entity._attr_icon = "mdi:home"

        assert entity.icon == "mdi:home"

    def test_sensor_state_class_direct_assignment(self):
        """Test that state_class property returns directly assigned state_class."""
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        # Direct assignment via __dict__
        sensor.__dict__["state_class"] = "measurement"

        assert sensor.state_class == "measurement"

    def test_sensor_state_class_attr_fallback(self):
        """Test that state_class falls back to _attr_state_class."""
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor._attr_state_class = "total"

        assert sensor.state_class == "total"

    def test_sensor_state_class_from_entity_description(self):
        """Test that state_class comes from entity_description."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor.entity_description = SensorEntityDescription(
            key="test", name="Test", state_class="measurement"
        )

        assert sensor.state_class == "measurement"

    def test_sensor_icon_from_entity_description(self):
        """Test that icon comes from entity_description."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription

        sensor = SensorEntity()
        sensor.entity_description = SensorEntityDescription(
            key="test", name="Test", icon="mdi:airplane"
        )

        assert sensor.icon == "mdi:airplane"

    def test_direct_icon_triggers_discovery_update(self):
        """Test that directly assigned icon triggers discovery republish."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"

        mock_hass = MagicMock()
        entity.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # Set icon directly via __dict__
        entity.__dict__["icon"] = "mdi:airplane"

        # Check for discovery update
        result = entity._check_and_publish_discovery_update(["icon"])

        assert result is True
        assert len(jobs_added) == 1


class TestPublishMqttAttributes:
    """Test cases for _publish_mqtt_attributes method."""

    def test_publish_attributes_when_present(self):
        """Test that attributes are published when extra_state_attributes is set."""
        from unittest.mock import MagicMock
        import json
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = {"key1": "value1", "key2": 42}

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        entity._publish_mqtt_attributes()

        mock_mqtt.publish.assert_called_once()
        args, kwargs = mock_mqtt.publish.call_args
        assert args[0] == "homeassistant/sensor/test-entity/attributes"
        assert json.loads(args[1]) == {"key1": "value1", "key2": 42}
        assert kwargs == {"qos": 0, "retain": True}

    def test_no_publish_when_no_attributes(self):
        """Test that nothing is published when extra_state_attributes is None."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = None

        mock_mqtt = MagicMock()
        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        entity._publish_mqtt_attributes()

        mock_mqtt.publish.assert_not_called()

    def test_no_publish_when_mqtt_not_connected(self):
        """Test that nothing is published when MQTT is not connected."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = {"test": "value"}

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = False

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        entity._publish_mqtt_attributes()

        mock_mqtt.publish.assert_not_called()

    def test_no_publish_without_entity_id(self):
        """Test that nothing is published when entity_id is not set."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = None
        entity._attr_extra_state_attributes = {"test": "value"}

        mock_mqtt = MagicMock()
        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        entity._publish_mqtt_attributes()

        mock_mqtt.publish.assert_not_called()


class TestAddMqttAttributesToConfig:
    """Test cases for _add_mqtt_attributes_to_config method."""

    def test_add_attributes_topic_to_config(self):
        """Test that json_attributes_topic is added when attributes exist."""
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = {"test": "value"}

        config = {}
        entity._add_mqtt_attributes_to_config(config)

        assert (
            config["json_attributes_topic"]
            == "homeassistant/sensor/test-entity/attributes"
        )

    def test_no_add_when_no_attributes(self):
        """Test that json_attributes_topic is not added when attributes don't exist."""
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = None

        config = {}
        entity._add_mqtt_attributes_to_config(config)

        assert "json_attributes_topic" not in config

    def test_no_add_without_entity_id(self):
        """Test that json_attributes_topic is not added when entity_id is None."""
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = None
        entity._attr_extra_state_attributes = {"test": "value"}

        config = {}
        entity._add_mqtt_attributes_to_config(config)

        assert "json_attributes_topic" not in config


class TestCheckAndPublishDiscoveryUpdate:
    """Test cases for _check_and_publish_discovery_update method."""

    def test_republish_when_new_property_becomes_available(self):
        """Test that discovery is republished when a new property becomes available."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        # Set icon property
        entity._attr_icon = "mdi:test"

        # Track if async_add_job was called with _publish_mqtt_discovery
        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # Use actual attribute name
        result = entity._check_and_publish_discovery_update(["_attr_icon"])

        assert result is True
        assert len(jobs_added) == 1
        assert jobs_added[0] == entity._publish_mqtt_discovery

    def test_no_republish_when_property_already_registered(self):
        """Test that discovery is not republished for already registered properties."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_icon = "mdi:test"

        mock_hass = MagicMock()
        entity.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # First call registers the property
        result1 = entity._check_and_publish_discovery_update(["_attr_icon"])
        assert result1 is True
        assert len(jobs_added) == 1

        # Second call should not trigger republish
        result2 = entity._check_and_publish_discovery_update(["_attr_icon"])
        assert result2 is False
        assert len(jobs_added) == 1

    def test_no_republish_when_property_is_none(self):
        """Test that discovery is not republished when property value is None."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_icon = None

        mock_hass = MagicMock()
        entity.hass = mock_hass

        discovery_called = []

        async def mock_publish_discovery():
            discovery_called.append(True)

        entity._publish_mqtt_discovery = mock_publish_discovery

        result = entity._check_and_publish_discovery_update(["icon"])

        assert result is False
        assert len(discovery_called) == 0

    def test_multiple_properties_tracking(self):
        """Test tracking multiple properties independently."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"

        mock_hass = MagicMock()
        entity.hass = mock_hass

        entity._attr_icon = "mdi:test"
        entity._attr_state_class = None

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # First call registers icon (using actual attribute names)
        result1 = entity._check_and_publish_discovery_update(
            ["_attr_icon", "_attr_state_class"]
        )
        assert result1 is True

        # Set state_class and call again
        entity._attr_state_class = "measurement"
        result2 = entity._check_and_publish_discovery_update(
            ["_attr_icon", "_attr_state_class"]
        )
        assert result2 is True

        assert len(jobs_added) == 2


class TestSensorMqttDiscoveryUpdates:
    """Test cases for sensor platform MQTT discovery updates."""

    def test_sensor_checks_discovery_updates_on_publish(self):
        """Test that sensor checks for discovery updates when publishing state."""
        from unittest.mock import MagicMock
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor.entity_id = "sensor.test_sensor"
        sensor._attr_unique_id = "test_unique_123"
        sensor._attr_native_value = "test_value"

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        sensor.hass = mock_hass

        check_update_calls = []

        def mock_check_update(properties):
            check_update_calls.append(properties)
            return False

        sensor._check_and_publish_discovery_update = mock_check_update

        sensor._mqtt_publish()

        assert len(check_update_calls) == 1
        assert set(check_update_calls[0]) == {
            "device_class",
            "state_class",
            "icon",
            "native_unit_of_measurement",
        }

    def test_sensor_republish_when_icon_becomes_available(self):
        """Test that sensor republishes discovery when icon is set after initial creation."""
        from unittest.mock import MagicMock
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor.entity_id = "sensor.test_sensor"
        sensor._attr_unique_id = "test_unique_123"
        sensor._attr_native_value = "42"

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        sensor.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # Initially no icon
        sensor._attr_icon = None

        # First publish without icon
        sensor._mqtt_publish()

        # No jobs should be added yet
        initial_jobs = len(jobs_added)

        # Now set the icon
        sensor._attr_icon = "mdi:airplane"

        # Publish again
        sensor._mqtt_publish()

        # Discovery should be republished
        assert len(jobs_added) == initial_jobs + 1

    def test_sensor_republish_when_state_class_becomes_available(self):
        """Test that sensor republishes discovery when state_class is set after initial creation."""
        from unittest.mock import MagicMock
        from shim.platforms.sensor import SensorEntity

        sensor = SensorEntity()
        sensor.entity_id = "sensor.test_sensor"
        sensor._attr_unique_id = "test_unique_123"
        sensor._attr_native_value = "42"
        sensor._attr_icon = "mdi:test"

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        sensor.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # First publish with icon but no state_class
        sensor._mqtt_publish()
        initial_jobs = len(jobs_added)

        # Now set state_class
        sensor._attr_state_class = "measurement"

        # Publish again
        sensor._mqtt_publish()

        # Discovery should be republished
        assert len(jobs_added) == initial_jobs + 1


class TestAttributesRepublishDiscovery:
    """Test cases for attributes triggering discovery republish."""

    def test_attributes_trigger_discovery_republish(self):
        """Test that first attributes publish triggers discovery republish."""
        from unittest.mock import MagicMock
        import json
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = {"test": "value"}

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        entity._publish_mqtt_attributes()

        # Should publish attributes
        assert mock_mqtt.publish.called
        args, kwargs = mock_mqtt.publish.call_args
        assert args[0] == "homeassistant/sensor/test-entity/attributes"

        # Should trigger discovery republish
        assert len(jobs_added) == 1
        assert jobs_added[0] == entity._publish_mqtt_discovery

    def test_attributes_no_republish_on_subsequent_calls(self):
        """Test that discovery is only republished once for attributes."""
        from unittest.mock import MagicMock
        from shim.entity import Entity

        entity = Entity()
        entity.entity_id = "sensor.test_entity"
        entity._attr_extra_state_attributes = {"test": "value"}

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        jobs_added = []

        def mock_async_add_job(f):
            jobs_added.append(f)

        mock_hass.async_add_job = mock_async_add_job

        # First call
        entity._publish_mqtt_attributes()
        assert len(jobs_added) == 1

        # Second call should not trigger republish
        entity._publish_mqtt_attributes()
        assert len(jobs_added) == 1


class TestSensorDateDeviceClass:
    """Test cases for DATE and TIMESTAMP device class state formatting."""

    def test_date_device_class_with_datetime_object(self):
        """Test that DATE device_class formats datetime as YYYY-MM-DD."""
        from datetime import datetime
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.DATE
        sensor._attr_native_value = datetime(2032, 10, 12, 7, 0, 0)

        assert sensor.state == "2032-10-12"

    def test_date_device_class_with_date_object(self):
        """Test that DATE device_class formats date object as YYYY-MM-DD."""
        from datetime import date
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.DATE
        sensor._attr_native_value = date(2032, 10, 12)

        assert sensor.state == "2032-10-12"

    def test_date_device_class_with_string(self):
        """Test that DATE device_class passes through string values."""
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.DATE
        sensor._attr_native_value = "2032-10-12"

        assert sensor.state == "2032-10-12"

    def test_timestamp_device_class_with_datetime(self):
        """Test that TIMESTAMP device_class formats datetime as ISO 8601."""
        from datetime import datetime
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.TIMESTAMP
        sensor._attr_native_value = datetime(2032, 10, 12, 7, 0, 0)

        assert sensor.state == "2032-10-12T07:00:00"

    def test_timestamp_device_class_with_string(self):
        """Test that TIMESTAMP device_class passes through string values."""
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.TIMESTAMP
        sensor._attr_native_value = "2032-10-12T07:00:00+00:00"

        assert sensor.state == "2032-10-12T07:00:00+00:00"

    def test_regular_device_class_uses_str(self):
        """Test that non-date device classes still use str()."""
        from shim.platforms.sensor import SensorEntity, SensorDeviceClass

        sensor = SensorEntity()
        sensor._attr_device_class = SensorDeviceClass.TEMPERATURE
        sensor._attr_native_value = 42.5

        assert sensor.state == "42.5"


class TestCoordinatorEntityMqttPublishing:
    """Test MQTT publishing for CoordinatorEntity-based entities."""

    @pytest.mark.asyncio
    async def test_coordinator_entity_has_mqtt_publish_method(self):
        """Test that CoordinatorEntity has _mqtt_publish method via Entity base class."""
        from shim.entity import Entity
        from shim.import_patch import setup_import_patching
        from shim.core import HomeAssistant
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = setup_import_patching(hass)
            patcher.patch()

            from homeassistant.helpers.update_coordinator import CoordinatorEntity

            # CoordinatorEntity should have _mqtt_publish method from Entity base class
            assert hasattr(CoordinatorEntity, "_mqtt_publish")
            # And the generic discovery method
            assert hasattr(CoordinatorEntity, "_publish_generic_mqtt_discovery")

    @pytest.mark.asyncio
    async def test_entity_generic_mqtt_discovery(self):
        """Test that base Entity class can publish generic MQTT discovery."""
        from shim.entity import Entity
        from unittest.mock import MagicMock, patch
        import json

        # Create a mock hass with MQTT client
        mock_hass = MagicMock()
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True
        mock_hass._mqtt_client = mock_mqtt

        # Create an entity and set it up
        entity = Entity()
        entity.hass = mock_hass
        entity.entity_id = "sensor.test_entity"
        entity._attr_unique_id = "test_unique_id"
        entity._attr_name = "Test Entity"
        entity._attr_device_info = {
            "identifiers": {("test", "device1")},
            "name": "Test Device",
            "manufacturer": "Test Mfg",
            "model": "Test Model",
        }

        # Publish generic discovery
        await entity._publish_generic_mqtt_discovery()

        # Verify MQTT publish was called
        assert mock_mqtt.publish.called

        # Get the published payload
        call_args = mock_mqtt.publish.call_args
        topic = call_args[0][0]
        payload = json.loads(call_args[0][1])

        # Verify topic structure
        assert topic == "homeassistant/sensor/test-entity/config"

        # Verify payload content
        assert payload["name"] == "Test Entity"
        assert payload["unique_id"] == "test_unique_id"
        assert payload["state_topic"] == "homeassistant/sensor/test-entity/state"
        assert "device" in payload

    def test_entity_mqtt_publish_publishes_state(self):
        """Test that Entity._mqtt_publish publishes state to MQTT."""
        from shim.entity import Entity, STATE_UNAVAILABLE
        from unittest.mock import MagicMock

        # Create a mock hass with MQTT client
        mock_hass = MagicMock()
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True
        mock_hass._mqtt_client = mock_mqtt

        # Create an entity with a state by overriding the state property
        class TestEntity(Entity):
            @property
            def state(self):
                return "42"

        entity = TestEntity()
        entity.hass = mock_hass
        entity.entity_id = "sensor.test_entity"
        entity._attr_unique_id = "test_unique_id"
        entity._attr_name = "Test Entity"

        # Publish state via _mqtt_publish
        entity._mqtt_publish()

        # Verify MQTT publish was called for state
        mock_mqtt.publish.assert_called()

        # Check that state topic was published with correct value
        call_args_list = mock_mqtt.publish.call_args_list
        topics_and_values = [(call[0][0], call[0][1]) for call in call_args_list]
        state_calls = [(t, v) for t, v in topics_and_values if t.endswith("/state")]
        assert len(state_calls) > 0
        assert state_calls[0][1] == "42"

    def test_entity_mqtt_publish_skips_when_no_mqtt_client(self):
        """Test that _mqtt_publish skips when no MQTT client."""
        from shim.entity import Entity
        from unittest.mock import MagicMock

        # Create a mock hass without MQTT client
        mock_hass = MagicMock()
        # No _mqtt_client attribute

        entity = Entity()
        entity.hass = mock_hass
        entity.entity_id = "sensor.test_entity"

        # Should not raise exception
        entity._mqtt_publish()

    def test_entity_mqtt_publish_skips_when_not_connected(self):
        """Test that _mqtt_publish skips when MQTT not connected."""
        from shim.entity import Entity
        from unittest.mock import MagicMock

        # Create a mock hass with disconnected MQTT client
        mock_hass = MagicMock()
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = False
        mock_hass._mqtt_client = mock_mqtt

        entity = Entity()
        entity.hass = mock_hass
        entity.entity_id = "sensor.test_entity"

        # Publish should skip when not connected
        entity._mqtt_publish()

        # Verify no publish calls were made
        assert not mock_mqtt.publish.called


class TestEntityRegistryConfigEntryTracking:
    """Test that EntityRegistry tracks entities by config entry ID."""

    def test_registry_tracks_config_entry_id(self):
        """Test that entities are bucketed by config_entry_id."""
        from shim.entity import EntityRegistry, Entity

        registry = EntityRegistry()
        # Reset singleton state for test
        registry._entities = {}
        registry._entries_by_config_entry = {}

        entity1 = Entity()
        entity1.entity_id = "sensor.battery"
        entity1._attr_unique_id = "vin123_BATTERY_LEVEL"
        entity1._attr_config_entry_id = "entry_abc"

        entity2 = Entity()
        entity2.entity_id = "sensor.odometer"
        entity2._attr_unique_id = "vin123_ODOMETER"
        entity2._attr_config_entry_id = "entry_abc"

        entity3 = Entity()
        entity3.entity_id = "switch.lock"
        entity3._attr_unique_id = "vin456_LOCK"
        entity3._attr_config_entry_id = "entry_def"

        registry.register(entity1)
        registry.register(entity2)
        registry.register(entity3)

        abc_entries = registry.async_entries_for_config_entry("entry_abc")
        def_entries = registry.async_entries_for_config_entry("entry_def")
        unknown_entries = registry.async_entries_for_config_entry("no_such_entry")

        assert len(abc_entries) == 2
        assert {e.unique_id for e in abc_entries} == {"vin123_BATTERY_LEVEL", "vin123_ODOMETER"}
        assert len(def_entries) == 1
        assert def_entries[0].unique_id == "vin456_LOCK"
        assert len(unknown_entries) == 0

    def test_registry_entry_disabled_field(self):
        """Test RegistryEntry.disabled reflects entity_registry_enabled_default."""
        from shim.entity import EntityRegistry, Entity

        registry = EntityRegistry()
        registry._entities = {}
        registry._entries_by_config_entry = {}

        enabled_entity = Entity()
        enabled_entity.entity_id = "sensor.enabled"
        enabled_entity._attr_unique_id = "enabled_1"
        enabled_entity._attr_config_entry_id = "entry_1"
        enabled_entity._attr_entity_registry_enabled_default = True

        disabled_entity = Entity()
        disabled_entity.entity_id = "sensor.disabled"
        disabled_entity._attr_unique_id = "disabled_1"
        disabled_entity._attr_config_entry_id = "entry_1"
        disabled_entity._attr_entity_registry_enabled_default = False

        registry.register(enabled_entity)
        registry.register(disabled_entity)

        entries = registry.async_entries_for_config_entry("entry_1")
        assert len(entries) == 2

        enabled_entry = next(e for e in entries if e.unique_id == "enabled_1")
        disabled_entry = next(e for e in entries if e.unique_id == "disabled_1")

        assert enabled_entry.disabled is False
        assert disabled_entry.disabled is True

    def test_registry_unregister_removes_config_entry(self):
        """Test unregister removes from both _entities and _entries_by_config_entry."""
        from shim.entity import EntityRegistry, Entity

        registry = EntityRegistry()
        registry._entities = {}
        registry._entries_by_config_entry = {}

        entity = Entity()
        entity.entity_id = "sensor.temp"
        entity._attr_unique_id = "temp_1"
        entity._attr_config_entry_id = "entry_1"

        registry.register(entity)
        assert len(registry.async_entries_for_config_entry("entry_1")) == 1

        registry.unregister("sensor.temp")
        assert len(registry.async_entries_for_config_entry("entry_1")) == 0
        assert registry.get("sensor.temp") is None

    def test_registry_entries_return_copy(self):
        """Test async_entries_for_config_entry returns a copy."""
        from shim.entity import EntityRegistry, Entity

        registry = EntityRegistry()
        registry._entities = {}
        registry._entries_by_config_entry = {}

        entity = Entity()
        entity.entity_id = "sensor.test"
        entity._attr_unique_id = "test_1"
        entity._attr_config_entry_id = "entry_1"

        registry.register(entity)
        entries = registry.async_entries_for_config_entry("entry_1")

        # Modifying the returned list should not affect the registry
        entries.clear()
        assert len(registry.async_entries_for_config_entry("entry_1")) == 1
