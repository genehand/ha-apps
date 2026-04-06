"""Tests for manager functionality including command routing."""

import pytest
import asyncio
import json
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestManagerCommandRouting:
    """Test cases for manager command routing."""

    def _create_manager(self):
        """Helper to create a ShimManager with mocked dependencies."""
        from shim.manager import ShimManager
        from mqtt_bridge import MqttBridge

        mock_config_dir = Path("/tmp/test_config")
        mock_mqtt_client = MagicMock()

        # Create a mock MqttBridge that returns our mock client
        mock_bridge = MagicMock(spec=MqttBridge)
        mock_bridge.client = mock_mqtt_client

        with patch("shim.manager.HomeAssistant") as MockHass:
            mock_hass = MagicMock()
            mock_hass.shim_dir = Path("/tmp/test_shim")
            mock_hass._storage = MagicMock()
            MockHass.return_value = mock_hass

            with patch("shim.manager.IntegrationManager"):
                with patch("shim.manager.IntegrationLoader"):
                    manager = ShimManager(mock_config_dir, mock_bridge)
                    return manager

    @pytest.mark.asyncio
    async def test_route_command_text_entity_set_value(self):
        """Test that text entity set commands are properly routed to async_set_value."""
        from shim.platforms.text import TextEntity

        manager = self._create_manager()

        # Create a mock text entity
        entity = MagicMock(spec=TextEntity)
        entity.entity_id = "text.test_entity"
        entity.async_set_value = AsyncMock()

        # Route a set command
        await manager._route_command(entity, "set", "SAN")

        # Verify async_set_value was called with the payload
        entity.async_set_value.assert_called_once_with("SAN")

    @pytest.mark.asyncio
    async def test_route_command_turn_on(self):
        """Test that turn on commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_turn_on
        # Use spec to avoid async_set_value being auto-created
        class MockSwitch:
            entity_id = "switch.test_entity"

            async def async_turn_on(self):
                pass

        entity = Mock(spec=MockSwitch)
        entity.entity_id = "switch.test_entity"
        entity.async_turn_on = AsyncMock()

        # Route an ON command
        await manager._route_command(entity, "set", "ON")

        # Verify async_turn_on was called
        entity.async_turn_on.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_command_turn_off(self):
        """Test that turn off commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_turn_off
        # Use spec to avoid async_set_value being auto-created
        class MockSwitch:
            entity_id = "switch.test_entity"

            async def async_turn_off(self):
                pass

        entity = Mock(spec=MockSwitch)
        entity.entity_id = "switch.test_entity"
        entity.async_turn_off = AsyncMock()

        # Route an OFF command
        await manager._route_command(entity, "set", "OFF")

        # Verify async_turn_off was called
        entity.async_turn_off.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_command_percentage_set(self):
        """Test that percentage set commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_set_percentage
        entity = MagicMock()
        entity.entity_id = "fan.test_entity"
        entity.async_set_percentage = AsyncMock()

        # Route a percentage set command
        await manager._route_command(entity, "percentage_set", "75")

        # Verify async_set_percentage was called with the parsed integer
        entity.async_set_percentage.assert_called_once_with(75)

    @pytest.mark.asyncio
    async def test_route_command_preset_mode_set(self):
        """Test that preset mode set commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_set_preset_mode
        entity = MagicMock()
        entity.entity_id = "fan.test_entity"
        entity.async_set_preset_mode = AsyncMock()

        # Route a preset mode set command
        await manager._route_command(entity, "preset_mode_set", "auto")

        # Verify async_set_preset_mode was called with the payload
        entity.async_set_preset_mode.assert_called_once_with("auto")

    @pytest.mark.asyncio
    async def test_route_command_temperature_set(self):
        """Test that temperature set commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_set_temperature
        entity = MagicMock()
        entity.entity_id = "climate.test_entity"
        entity.async_set_temperature = AsyncMock()

        # Route a temperature set command
        await manager._route_command(entity, "temperature_set", "22.5")

        # Verify async_set_temperature was called with the parsed float
        entity.async_set_temperature.assert_called_once_with(temperature=22.5)

    @pytest.mark.asyncio
    async def test_route_command_mode_set(self):
        """Test that HVAC mode set commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_set_hvac_mode
        entity = MagicMock()
        entity.entity_id = "climate.test_entity"
        entity.async_set_hvac_mode = AsyncMock()

        # Route a mode set command
        await manager._route_command(entity, "mode_set", "heat")

        # Verify async_set_hvac_mode was called with the payload
        entity.async_set_hvac_mode.assert_called_once_with("heat")

    @pytest.mark.asyncio
    async def test_route_command_oscillation_set(self):
        """Test that oscillation set commands are properly routed."""
        manager = self._create_manager()

        # Create a mock entity with async_oscillate
        entity = MagicMock()
        entity.entity_id = "fan.test_entity"
        entity.async_oscillate = AsyncMock()

        # Route an oscillation set command (ON)
        await manager._route_command(entity, "oscillation_set", "ON")

        # Verify async_oscillate was called with True
        entity.async_oscillate.assert_called_once_with(True)

        # Reset mock
        entity.async_oscillate.reset_mock()

        # Route an oscillation set command (OFF)
        await manager._route_command(entity, "oscillation_set", "OFF")

        # Verify async_oscillate was called with False
        entity.async_oscillate.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_route_command_error_handling(self):
        """Test that errors in command routing are properly caught and logged."""
        manager = self._create_manager()

        # Create a mock entity that raises an exception
        # Use spec to avoid async_set_value being auto-created
        class MockSwitch:
            entity_id = "switch.test_entity"

            async def async_turn_on(self):
                pass

        entity = Mock(spec=MockSwitch)
        entity.entity_id = "switch.test_entity"
        entity.async_turn_on = AsyncMock(side_effect=Exception("Test error"))

        # Route a command that will raise an exception
        # Should not raise, just log the error
        await manager._route_command(entity, "set", "ON")

        # Verify the method was called even though it raised
        entity.async_turn_on.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_command_fallback_to_sync_method(self):
        """Test that sync methods are used as fallback when async methods don't exist."""
        manager = self._create_manager()

        # Create a mock entity with only sync turn_on (no async_turn_on)
        # Use spec that doesn't define async_turn_on
        class MockSwitch:
            entity_id = "switch.test_entity"

            def turn_on(self):
                pass

        entity = Mock(spec=MockSwitch)
        entity.entity_id = "switch.test_entity"
        entity.turn_on = Mock()

        # Route an ON command
        await manager._route_command(entity, "set", "ON")

        # Verify sync turn_on was called
        entity.turn_on.assert_called_once()


class TestConfig:
    """Tests for Config loading."""

    def test_config_from_dict_basic(self):
        """Test loading config from dictionary."""
        from config import Config

        data = {
            "mqtt_host": "test-host",
            "mqtt_port": 1884,
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "log_level": "DEBUG",
        }
        config = Config.from_dict(data)

        assert config.mqtt_host == "test-host"
        assert config.mqtt_port == 1884
        assert config.mqtt_username == "user"
        assert config.mqtt_password == "pass"
        assert config.log_level == "DEBUG"
        assert config.integration_log_levels == {}

    def test_config_from_dict_with_integration_log_levels(self):
        """Test loading config with per-integration log levels (dict format)."""
        from config import Config

        data = {
            "log_level": "INFO",
            "integration_log_levels": {
                "custom_components.dreo.pydreo.pydreobasedevice": "WARNING",
                "custom_components.nws_alerts": "ERROR",
            },
        }
        config = Config.from_dict(data)

        assert config.log_level == "INFO"
        assert config.integration_log_levels == {
            "custom_components.dreo.pydreo.pydreobasedevice": "WARNING",
            "custom_components.nws_alerts": "ERROR",
        }

    def test_config_from_dict_with_integration_log_levels_list_format(self):
        """Test loading config with per-integration log levels (list format)."""
        from config import Config

        data = {
            "log_level": "INFO",
            "integration_log_levels": [
                {
                    "name": "custom_components.dreo.pydreo.pydreobasedevice",
                    "level": "WARNING",
                },
                {"name": "custom_components.nws_alerts", "level": "ERROR"},
            ],
        }
        config = Config.from_dict(data)

        assert config.log_level == "INFO"
        assert config.integration_log_levels == {
            "custom_components.dreo.pydreo.pydreobasedevice": "WARNING",
            "custom_components.nws_alerts": "ERROR",
        }

    def test_config_defaults(self):
        """Test config defaults when fields missing."""
        from config import Config

        config = Config.from_dict({})

        assert config.mqtt_host == "core-mosquitto"
        assert config.mqtt_port == 1883
        assert config.mqtt_username is None
        assert config.mqtt_password is None
        assert config.log_level == "INFO"
        assert config.integration_log_levels == {}


class TestEntityFilters:
    """Tests for entity filter functionality."""

    def test_config_entry_entity_filters_empty(self):
        """Test that empty filters are returned as empty list."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={},
        )

        assert entry.entity_filters == []

    def test_config_entry_entity_filters_from_options(self):
        """Test that filters are read from options."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.*_temp", "binary_sensor.motion_*"]},
        )

        assert entry.entity_filters == ["sensor.*_temp", "binary_sensor.motion_*"]

    def test_config_entry_entity_filters_string_legacy(self):
        """Test that legacy string format is handled."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": "sensor.*_temp"},
        )

        assert entry.entity_filters == ["sensor.*_temp"]

    def test_entity_matches_filter_simple(self):
        """Test simple filter matching."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.living_room_temp"]},
        )

        assert entry.entity_matches_filter("sensor.living_room_temp") is True
        assert entry.entity_matches_filter("sensor.kitchen_temp") is False

    def test_entity_matches_filter_wildcard_star(self):
        """Test wildcard * matching."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.*_temperature"]},
        )

        assert entry.entity_matches_filter("sensor.living_room_temperature") is True
        assert entry.entity_matches_filter("sensor.kitchen_temperature") is True
        assert entry.entity_matches_filter("sensor.outside_temperature") is True
        assert entry.entity_matches_filter("sensor.temperature_living") is False
        assert (
            entry.entity_matches_filter("binary_sensor.living_room_temperature")
            is False
        )

    def test_entity_matches_filter_wildcard_question(self):
        """Test wildcard ? matching."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.device?_temp"]},
        )

        assert entry.entity_matches_filter("sensor.device1_temp") is True
        assert entry.entity_matches_filter("sensor.deviceA_temp") is True
        assert entry.entity_matches_filter("sensor.device12_temp") is False

    def test_entity_matches_filter_multiple_patterns(self):
        """Test that multiple patterns are checked."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={
                "entity_filters": [
                    "sensor.*_temp",
                    "binary_sensor.motion_*",
                    "light.kitchen_*",
                ]
            },
        )

        assert entry.entity_matches_filter("sensor.living_room_temp") is True
        assert entry.entity_matches_filter("binary_sensor.motion_front") is True
        assert entry.entity_matches_filter("light.kitchen_overhead") is True
        assert entry.entity_matches_filter("sensor.living_room_humidity") is False

    def test_validate_entity_filters_valid(self):
        """Test validation of valid filter patterns."""
        from shim.integrations.loader import IntegrationLoader

        patterns = ["sensor.*_temp", "binary_sensor.motion_*", "light.kitchen_?"]
        is_valid, error = IntegrationLoader.validate_entity_filters(patterns)

        assert is_valid is True
        assert error is None

    def test_validate_entity_filters_empty(self):
        """Test validation of empty filter list."""
        from shim.integrations.loader import IntegrationLoader

        patterns = []
        is_valid, error = IntegrationLoader.validate_entity_filters(patterns)

        assert is_valid is True
        assert error is None

    def test_validate_entity_filters_empty_pattern(self):
        """Test validation rejects empty patterns."""
        from shim.integrations.loader import IntegrationLoader

        patterns = ["sensor.*_temp", "", "binary_sensor.motion_*"]
        is_valid, error = IntegrationLoader.validate_entity_filters(patterns)

        assert is_valid is False
        assert "Empty pattern" in error

    @pytest.mark.asyncio
    async def test_apply_entity_filters_removes_matching(self):
        """Test that apply_entity_filters removes matching entities."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        # Create mock hass and entry
        mock_hass = MagicMock()
        mock_entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.*_temp"]},
        )

        # Create mock entities
        entity1 = MagicMock()
        entity1.entity_id = "sensor.living_room_temp"
        entity1.integration_domain = "test_domain"
        entity1.async_remove = AsyncMock()

        entity2 = MagicMock()
        entity2.entity_id = "sensor.kitchen_temp"
        entity2.integration_domain = "test_domain"
        entity2.async_remove = AsyncMock()

        entity3 = MagicMock()
        entity3.entity_id = "sensor.outside_humidity"
        entity3.integration_domain = "test_domain"
        entity3.async_remove = AsyncMock()

        # Create loader with mock entities
        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)
        loader._entities = {
            "sensor": [entity1, entity2, entity3],
        }

        # Apply filters
        result = await loader.async_apply_entity_filters(mock_entry)

        # Verify matching entities were removed
        assert result["removed"] == 2
        assert "sensor.living_room_temp" in result["filtered_entities"]
        assert "sensor.kitchen_temp" in result["filtered_entities"]

        # Verify async_remove was called for matching entities
        entity1.async_remove.assert_called_once_with(cleanup_mqtt=True)
        entity2.async_remove.assert_called_once_with(cleanup_mqtt=True)
        entity3.async_remove.assert_not_called()

        # Verify entities list was updated
        assert len(loader._entities["sensor"]) == 1
        assert loader._entities["sensor"][0] == entity3

    @pytest.mark.asyncio
    async def test_apply_entity_filters_no_matches(self):
        """Test that apply_entity_filters handles no matches gracefully."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.unknown_*"]},
        )

        entity1 = MagicMock()
        entity1.entity_id = "sensor.living_room_temp"
        entity1.integration_domain = "test_domain"
        entity1.async_remove = AsyncMock()

        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)
        loader._entities = {"sensor": [entity1]}

        result = await loader.async_apply_entity_filters(mock_entry)

        assert result["removed"] == 0
        assert len(result["filtered_entities"]) == 0
        entity1.async_remove.assert_not_called()
        assert len(loader._entities["sensor"]) == 1

    @pytest.mark.asyncio
    async def test_apply_entity_filters_different_domain(self):
        """Test that entities from other domains are not affected."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_filters": ["sensor.*_temp"]},
        )

        entity1 = MagicMock()
        entity1.entity_id = "sensor.living_room_temp"
        entity1.integration_domain = "other_domain"  # Different domain
        entity1.async_remove = AsyncMock()

        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)
        loader._entities = {"sensor": [entity1]}

        result = await loader.async_apply_entity_filters(mock_entry)

        # Should not remove entity from different domain
        assert result["removed"] == 0
        entity1.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_register_entity_with_filters(self):
        """Test that register_entity respects filters."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = [
            ConfigEntry(
                entry_id="test_123",
                domain="test_domain",
                title="Test",
                options={"entity_filters": ["sensor.*_filtered"]},
            )
        ]

        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)

        # Create entity that matches filter
        entity_filtered = MagicMock()
        entity_filtered.entity_id = "sensor.test_filtered"
        entity_filtered.integration_domain = "test_domain"

        # Create entity that doesn't match filter
        entity_allowed = MagicMock()
        entity_allowed.entity_id = "sensor.test_allowed"
        entity_allowed.integration_domain = "test_domain"

        # Register entities
        result_filtered = loader.register_entity("sensor", entity_filtered)
        result_allowed = loader.register_entity("sensor", entity_allowed)

        # Verify filtered entity was not registered
        assert result_filtered is False
        assert "sensor.test_filtered" not in [
            e.entity_id for e in loader._entities.get("sensor", [])
        ]

        # Verify allowed entity was registered
        assert result_allowed is True
        assert len(loader._entities.get("sensor", [])) == 1
        assert loader._entities["sensor"][0].entity_id == "sensor.test_allowed"

    def test_register_entity_duplicate_detection(self):
        """Test that duplicate entities (same MQTT topic) are rejected."""
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)

        # Create first entity with lowercase unique_id
        entity1 = MagicMock()
        entity1.entity_id = "button.moonraker_123_firmware_restart"
        entity1.integration_domain = "moonraker"
        entity1.unique_id = "123_firmware_restart"

        # Register first entity
        result1 = loader.register_entity("button", entity1)
        assert result1 is True
        assert len(loader._entities.get("button", [])) == 1

        # Create second entity with uppercase unique_id (would map to same MQTT topic)
        entity2 = MagicMock()
        entity2.entity_id = "button.moonraker_123_FIRMWARE_RESTART"
        entity2.integration_domain = "moonraker"
        entity2.unique_id = "123_FIRMWARE_RESTART"

        # Try to register second entity - should be rejected as duplicate
        result2 = loader.register_entity("button", entity2)
        assert result2 is False
        assert len(loader._entities.get("button", [])) == 1  # Still only 1 entity

    def test_config_entry_entity_name_filters_empty(self):
        """Test that empty name filters are returned as empty list."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={},
        )

        assert entry.entity_name_filters == []

    def test_config_entry_entity_name_filters_from_options(self):
        """Test that name filters are read from options."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_name_filters": ["Macro *", "Temp*"]},
        )

        assert entry.entity_name_filters == ["Macro *", "Temp*"]

    def test_entity_matches_filter_by_name(self):
        """Test filtering by entity name."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_name_filters": ["Macro *"]},
        )

        # Should match by name pattern
        assert (
            entry.entity_matches_filter("button.moonraker_save", "Macro Save Config")
            is True
        )
        # Should not match different name
        assert (
            entry.entity_matches_filter("button.moonraker_restart", "Restart Printer")
            is False
        )

    def test_entity_matches_filter_by_id_or_name(self):
        """Test that OR logic works - matches by entity_id OR name."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={
                "entity_filters": ["sensor.*_temp"],
                "entity_name_filters": ["Macro *"],
            },
        )

        # Matches by entity_id
        assert (
            entry.entity_matches_filter("sensor.living_room_temp", "Living Room")
            is True
        )
        # Matches by name
        assert (
            entry.entity_matches_filter("button.moonraker_save", "Macro Save Config")
            is True
        )
        # Matches neither
        assert entry.entity_matches_filter("switch.other", "Other Switch") is False

    def test_entity_matches_filter_name_no_match_without_name(self):
        """Test that name patterns don't match when no name provided."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            domain="test_domain",
            title="Test",
            options={"entity_name_filters": ["Macro *"]},
        )

        # Without name, should not match even if entity_id would conceptually match
        assert entry.entity_matches_filter("button.moonraker_save", None) is False
        assert entry.entity_matches_filter("button.moonraker_save", "") is False

    @pytest.mark.asyncio
    async def test_register_entity_with_name_filters(self):
        """Test that register_entity respects name filters."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = [
            ConfigEntry(
                entry_id="test_123",
                domain="test_domain",
                title="Test",
                options={"entity_name_filters": ["Macro *"]},
            )
        ]

        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)

        # Create entity with matching name
        entity_filtered = MagicMock()
        entity_filtered.entity_id = "button.moonraker_save"
        entity_filtered.name = "Macro Save Config"
        entity_filtered._attr_name = None
        entity_filtered.integration_domain = "test_domain"

        # Create entity with non-matching name
        entity_allowed = MagicMock()
        entity_allowed.entity_id = "button.moonraker_restart"
        entity_allowed.name = "Restart Printer"
        entity_allowed._attr_name = None
        entity_allowed.integration_domain = "test_domain"

        # Register entities
        result_filtered = loader.register_entity("button", entity_filtered)
        result_allowed = loader.register_entity("button", entity_allowed)

        # Verify filtered entity was not registered
        assert result_filtered is False
        # Verify allowed entity was registered
        assert result_allowed is True
        assert len(loader._entities.get("button", [])) == 1

    @pytest.mark.asyncio
    async def test_register_entity_with_attr_name_filters(self):
        """Test that register_entity respects name filters using _attr_name."""
        from shim.core import ConfigEntry
        from shim.integrations.loader import IntegrationLoader

        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = [
            ConfigEntry(
                entry_id="test_123",
                domain="test_domain",
                title="Test",
                options={"entity_name_filters": ["Temp*"]},
            )
        ]

        mock_integration_manager = MagicMock()
        loader = IntegrationLoader(mock_hass, mock_integration_manager)

        # Create entity with _attr_name matching
        entity_filtered = MagicMock()
        entity_filtered.entity_id = "sensor.living_room"
        entity_filtered.name = None
        entity_filtered._attr_name = "Temperature Living Room"
        entity_filtered.integration_domain = "test_domain"

        # Register entity
        result = loader.register_entity("sensor", entity_filtered)
        assert result is False


class TestUpdateNotification:
    """Test cases for update notification functionality."""

    def _create_manager(self, mqtt_client=None):
        """Helper to create a ShimManager with mocked dependencies."""
        from shim.manager import ShimManager
        from mqtt_bridge import MqttBridge

        mock_config_dir = Path("/tmp/test_config")
        mock_mqtt_client = mqtt_client or MagicMock()

        # Create a mock MqttBridge that returns our mock client
        mock_bridge = MagicMock(spec=MqttBridge)
        mock_bridge.client = mock_mqtt_client
        mock_bridge.subscribe = MagicMock(return_value=(0, 1))

        with patch("shim.manager.HomeAssistant") as MockHass:
            mock_hass = MagicMock()
            mock_hass.shim_dir = Path("/tmp/test_shim")
            mock_hass._storage = MagicMock()
            MockHass.return_value = mock_hass

            with patch("shim.manager.IntegrationManager") as MockIntegrationManager:
                with patch("shim.manager.IntegrationLoader"):
                    manager = ShimManager(mock_config_dir, mock_bridge)
                    manager._mqtt_base_topic = "shack"
                    return manager, MockIntegrationManager

    @pytest.mark.asyncio
    async def test_publish_update_notification_with_updates(self):
        """Test that update notification publishes correct discovery config with updates available."""
        mock_mqtt = MagicMock()
        manager, _ = self._create_manager(mock_mqtt)

        # Create mock update info
        mock_update = MagicMock()
        mock_update.domain = "test_integration"
        mock_update.name = "Test Integration"
        mock_update.version = "1.0.0"
        mock_update.latest_version = "1.1.0"
        updates = [mock_update]

        await manager._publish_update_notification(updates)

        # Verify discovery topic is correct
        calls = mock_mqtt.publish.call_args_list
        discovery_call = calls[0]
        assert discovery_call[0][0] == "homeassistant/update/shack_updates/config"

        # Verify discovery config has correct values
        config = json.loads(discovery_call[0][1])
        assert config["name"] == "Shack Updates"
        assert config["unique_id"] == "shack_updates"
        assert config["state_topic"] == "homeassistant/update/shack_updates/state"
        assert config["payload_on"] == "on"
        assert config["payload_off"] == "off"
        assert config["device"]["identifiers"] == ["shack"]
        assert config["device"]["name"] == "Shack"
        assert config["device"]["manufacturer"] == "Custom"
        assert config["device"]["model"] == "Integration Bridge"

        # Verify state topic is published with "on" when updates exist
        state_call = calls[1]
        assert state_call[0][0] == "homeassistant/update/shack_updates/state"
        assert state_call[0][1] == "on"

        # Verify details topic is published
        details_call = calls[2]
        assert details_call[0][0] == "shack/updates/available"
        details = json.loads(details_call[0][1])
        assert len(details) == 1
        assert details[0]["domain"] == "test_integration"
        assert details[0]["name"] == "Test Integration"
        assert details[0]["current_version"] == "1.0.0"
        assert details[0]["latest_version"] == "1.1.0"

    @pytest.mark.asyncio
    async def test_publish_update_notification_no_updates(self):
        """Test that update notification publishes 'off' state when no updates."""
        mock_mqtt = MagicMock()
        manager, _ = self._create_manager(mock_mqtt)

        await manager._publish_update_notification([])

        # Verify state topic is published with "off" when no updates
        calls = mock_mqtt.publish.call_args_list
        state_call = calls[1]
        assert state_call[0][0] == "homeassistant/update/shack_updates/state"
        assert state_call[0][1] == "off"

        # Verify details topic is published with empty list
        details_call = calls[2]
        assert details_call[0][0] == "shack/updates/available"
        details = json.loads(details_call[0][1])
        assert details == []

    @pytest.mark.asyncio
    async def test_publish_update_notification_no_mqtt_client(self):
        """Test that update notification returns early when no MQTT client."""
        manager, _ = self._create_manager(None)
        manager._mqtt_client = None

        # Should not raise any errors
        await manager._publish_update_notification([])

    @pytest.mark.asyncio
    async def test_publish_update_notification_multiple_updates(self):
        """Test that update notification handles multiple updates."""
        mock_mqtt = MagicMock()
        manager, _ = self._create_manager(mock_mqtt)

        # Create multiple mock updates
        updates = []
        for i in range(3):
            mock_update = MagicMock()
            mock_update.domain = f"integration_{i}"
            mock_update.name = f"Integration {i}"
            mock_update.version = f"1.0.{i}"
            mock_update.latest_version = f"1.1.{i}"
            updates.append(mock_update)

        await manager._publish_update_notification(updates)

        # Verify details topic contains all updates
        calls = mock_mqtt.publish.call_args_list
        details_call = calls[2]
        details = json.loads(details_call[0][1])
        assert len(details) == 3
        for i, detail in enumerate(details):
            assert detail["domain"] == f"integration_{i}"
            assert detail["name"] == f"Integration {i}"

    @pytest.mark.asyncio
    async def test_initial_update_check_finds_updates(self):
        """Test that initial update check publishes notification when updates found."""
        mock_mqtt = MagicMock()
        manager, MockIntegrationManager = self._create_manager(mock_mqtt)

        # Mock integration manager to return updates
        mock_update = MagicMock()
        mock_update.domain = "test_integration"
        mock_update.name = "Test Integration"
        mock_update.version = "1.0.0"
        mock_update.latest_version = "1.1.0"

        mock_integration_manager = MagicMock()
        mock_integration_manager.check_for_updates = AsyncMock(
            return_value=[mock_update]
        )
        manager._integration_manager = mock_integration_manager
        manager._running = True

        with patch("shim.manager.create_persistent_notification", return_value=True):
            with patch("asyncio.sleep"):  # Skip the delay
                await manager._initial_update_check()

        # Verify that update notification was published
        calls = mock_mqtt.publish.call_args_list
        assert len(calls) >= 2  # Discovery and state

    @pytest.mark.asyncio
    async def test_periodic_update_check_finds_updates(self):
        """Test that periodic update check publishes notification when updates found."""
        mock_mqtt = MagicMock()
        manager, _ = self._create_manager(mock_mqtt)

        # Mock integration manager to return updates
        mock_update = MagicMock()
        mock_update.domain = "test_integration"
        mock_update.name = "Test Integration"
        mock_update.version = "1.0.0"
        mock_update.latest_version = "1.1.0"

        mock_integration_manager = MagicMock()
        mock_integration_manager.check_for_updates = AsyncMock(
            return_value=[mock_update]
        )
        manager._integration_manager = mock_integration_manager
        manager._running = True

        # Cancel the periodic check after first iteration
        async def cancel_after_first():
            await asyncio.sleep(0.1)
            manager._running = False

        # Start cancellation and periodic check
        with patch("asyncio.sleep", side_effect=[0, asyncio.CancelledError()]):
            try:
                await manager._periodic_update_checks()
            except asyncio.CancelledError:
                pass

        # Verify that update notification was published
        calls = mock_mqtt.publish.call_args_list
        assert len(calls) >= 2  # Discovery and state
