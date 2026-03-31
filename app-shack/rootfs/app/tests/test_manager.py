"""Tests for manager functionality including command routing."""

import pytest
import asyncio
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

        mock_config_dir = Path("/tmp/test_config")
        mock_mqtt_client = MagicMock()

        with patch("shim.manager.HomeAssistant") as MockHass:
            mock_hass = MagicMock()
            mock_hass.shim_dir = Path("/tmp/test_shim")
            mock_hass._storage = MagicMock()
            MockHass.return_value = mock_hass

            with patch("shim.manager.IntegrationManager"):
                with patch("shim.manager.IntegrationLoader"):
                    manager = ShimManager(mock_config_dir, mock_mqtt_client)
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
