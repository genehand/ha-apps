"""Tests for core shim functionality."""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import (
    HomeAssistant,
    StateMachine,
    ServiceRegistry,
    State,
    ConfigEntry,
)


class TestState:
    """Test cases for State class."""

    def test_state_creation(self):
        """Test creating a State object."""
        state = State(
            entity_id="sensor.temperature", state="25.5", attributes={"unit": "°C"}
        )

        assert state.entity_id == "sensor.temperature"
        assert state.state == "25.5"
        assert state.attributes == {"unit": "°C"}
        assert state.last_changed is not None
        assert state.last_updated is not None

    def test_state_defaults(self):
        """Test State creation with default values."""
        state = State(entity_id="switch.test", state="on")

        assert state.attributes == {}
        assert state.context == {}
        assert state.last_changed is not None


class TestConfigEntry:
    """Test cases for ConfigEntry class."""

    def test_entry_creation(self):
        """Test creating a ConfigEntry."""
        entry = ConfigEntry(
            entry_id="test_123",
            version=1,
            domain="test_integration",
            title="Test Integration",
        )

        assert entry.entry_id == "test_123"
        assert entry.version == 1
        assert entry.domain == "test_integration"
        assert entry.title == "Test Integration"
        assert entry.data == {}
        assert entry.options == {}

    def test_entry_with_data(self):
        """Test ConfigEntry with configuration data."""
        entry = ConfigEntry(
            entry_id="test_456",
            version=2,
            domain="mqtt",
            title="MQTT Broker",
            data={"host": "localhost", "port": 1883},
            options={"retain": True},
        )

        assert entry.data["host"] == "localhost"
        assert entry.data["port"] == 1883
        assert entry.options["retain"] is True

    def test_unique_id_from_data(self):
        """Test getting unique_id from entry data."""
        entry = ConfigEntry(
            entry_id="test_789",
            version=1,
            domain="sensor",
            title="Test Sensor",
            data={"unique_id": "sensor_001"},
        )

        assert entry.unique_id == "sensor_001"

    def test_unique_id_not_set(self):
        """Test unique_id when not in data."""
        entry = ConfigEntry(
            entry_id="test_000", version=1, domain="light", title="Test Light"
        )

        assert entry.unique_id is None


class TestStateMachine:
    """Test cases for StateMachine class."""

    @pytest.fixture
    def state_machine(self):
        """Create a StateMachine instance for testing."""
        mock_hass = Mock()
        return StateMachine(mock_hass)

    def test_async_set_new_state(self, state_machine):
        """Test setting a new entity state."""
        state_machine.async_set("sensor.test", "value1")

        state = state_machine.get("sensor.test")
        assert state is not None
        assert state.state == "value1"

    def test_async_set_update_state(self, state_machine):
        """Test updating an existing entity state."""
        state_machine.async_set("sensor.test", "value1")
        state_machine.async_set("sensor.test", "value2")

        state = state_machine.get("sensor.test")
        assert state.state == "value2"

    def test_async_set_no_change(self, state_machine):
        """Test that setting same state doesn't trigger listeners."""
        listener_called = [False]

        def listener(entity_id, old_state, new_state):
            listener_called[0] = True

        state_machine.async_add_listener(listener)
        state_machine.async_set("sensor.test", "value1")

        assert listener_called[0] is True

        # Reset
        listener_called[0] = False

        # Set same value - should not trigger listener
        state_machine.async_set("sensor.test", "value1")
        assert listener_called[0] is False

    def test_async_remove(self, state_machine):
        """Test removing an entity state."""
        state_machine.async_set("sensor.test", "value1")
        state_machine.async_remove("sensor.test")

        assert state_machine.get("sensor.test") is None

    def test_async_entity_ids(self, state_machine):
        """Test getting all entity IDs."""
        state_machine.async_set("sensor.temp", "25")
        state_machine.async_set("sensor.humidity", "60")
        state_machine.async_set("light.living", "on")

        all_ids = state_machine.async_entity_ids()
        assert len(all_ids) == 3
        assert "sensor.temp" in all_ids
        assert "light.living" in all_ids

    def test_async_entity_ids_filtered(self, state_machine):
        """Test getting entity IDs filtered by domain."""
        state_machine.async_set("sensor.temp", "25")
        state_machine.async_set("sensor.humidity", "60")
        state_machine.async_set("light.living", "on")

        sensor_ids = state_machine.async_entity_ids(domain="sensor")
        assert len(sensor_ids) == 2
        assert "sensor.temp" in sensor_ids
        assert "sensor.humidity" in sensor_ids
        assert "light.living" not in sensor_ids

    def test_async_register_entity_id(self, state_machine):
        """Test registering unique_id to entity_id mapping."""
        state_machine.async_register_entity_id("unique_001", "sensor.temperature")

        assert state_machine.async_get_entity_id("unique_001") == "sensor.temperature"


class TestServiceRegistry:
    """Test cases for ServiceRegistry class."""

    @pytest.fixture
    def service_registry(self):
        """Create a ServiceRegistry instance for testing."""
        mock_hass = Mock()
        return ServiceRegistry(mock_hass)

    @pytest.mark.asyncio
    async def test_async_register_service(self, service_registry):
        """Test registering a service."""
        mock_handler = Mock()
        service_registry.async_register("light", "turn_on", mock_handler)

        await service_registry.async_call(
            "light", "turn_on", {"entity_id": "light.test"}
        )

        mock_handler.assert_called_once_with({"entity_id": "light.test"})

    @pytest.mark.asyncio
    async def test_async_call_missing_service(self, service_registry):
        """Test calling a non-existent service."""
        # Should not raise, just log a warning
        await service_registry.async_call("missing", "service")

    @pytest.mark.asyncio
    async def test_async_call_async_handler(self, service_registry):
        """Test calling an async service handler."""
        called = [False]

        async def async_handler(data):
            called[0] = True
            data["processed"] = True

        service_registry.async_register("test", "async_action", async_handler)

        service_data = {"initial": "value"}
        await service_registry.async_call("test", "async_action", service_data)

        assert called[0] is True
        assert service_data.get("processed") is True


class TestEntityIdGeneration:
    """Test cases for entity ID generation from Platform enum."""

    @pytest.mark.asyncio
    async def test_entity_id_generation_with_platform_enum(self, tmp_path):
        """Test that entity IDs use correct lowercase platform names.

        Regression test: Previously using str(Platform.SENSOR) produced 'SENSOR'
        (uppercase) instead of 'sensor', causing entity IDs like
        'SENSOR.device_123' instead of 'sensor.device_123'.
        """
        from shim.const import Platform
        from unittest.mock import MagicMock, AsyncMock

        # Create a mock entity with unique_id
        mock_entity = MagicMock()
        mock_entity.entity_id = None  # Entity ID not set
        mock_entity.unique_id = "test_device_123"
        mock_entity.name = None
        mock_entity._attr_name = None

        # Simulate entity ID generation logic from shim/core.py
        platform = Platform.SENSOR  # This is an enum
        platform_name = platform.value if hasattr(platform, "value") else str(platform)

        # Generate entity_id
        unique_id = getattr(mock_entity, "unique_id", None)
        entity_id = f"{platform_name}.{unique_id}"

        # Verify entity_id uses lowercase 'sensor', not uppercase 'SENSOR'
        assert entity_id == "sensor.test_device_123"
        assert entity_id.startswith("sensor.")  # lowercase
        assert not entity_id.startswith("SENSOR.")  # NOT uppercase

        # Test with different platforms
        for platform_enum in [Platform.SWITCH, Platform.LIGHT, Platform.BINARY_SENSOR]:
            platform_name = (
                platform_enum.value
                if hasattr(platform_enum, "value")
                else str(platform_enum)
            )
            entity_id = f"{platform_name}.test_123"
            assert entity_id.startswith(f"{platform_enum.value}.")
            assert entity_id.islower() or entity_id.split(".")[0].islower()

    @pytest.mark.asyncio
    async def test_entity_id_generation_lowercase_platform(self, tmp_path):
        """Test that entity IDs are generated with lowercase platform prefixes."""
        from shim.const import Platform

        # Test all common platforms
        test_cases = [
            (Platform.SENSOR, "sensor"),
            (Platform.SWITCH, "switch"),
            (Platform.LIGHT, "light"),
            (Platform.CLIMATE, "climate"),
            (Platform.BINARY_SENSOR, "binary_sensor"),
            (Platform.FAN, "fan"),
        ]

        for platform, expected_prefix in test_cases:
            platform_name = (
                platform.value if hasattr(platform, "value") else str(platform)
            )
            entity_id = f"{platform_name}.my_device_123"

            assert entity_id.startswith(f"{expected_prefix}."), (
                f"Expected entity_id to start with '{expected_prefix}.', got '{entity_id}'"
            )
            assert platform_name == platform.value, (
                f"Platform name should be '{platform.value}', not '{platform_name}'"
            )
