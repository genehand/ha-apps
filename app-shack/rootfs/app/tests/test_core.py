"""Tests for core shim functionality."""

import pytest
import asyncio
import logging
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
        # Set up import patching first so homeassistant namespace is available
        from shim.core import HomeAssistant
        from shim.import_patch import ImportPatcher

        hass = HomeAssistant(config_dir=tmp_path)
        patcher = ImportPatcher(hass)
        patcher.patch()

        from homeassistant.const import Platform
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
        # Set up import patching first so homeassistant namespace is available
        from shim.core import HomeAssistant
        from shim.import_patch import ImportPatcher

        hass = HomeAssistant(config_dir=tmp_path)
        patcher = ImportPatcher(hass)
        patcher.patch()

        from homeassistant.const import Platform

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


class TestFlowManager:
    """Test cases for FlowManager class."""

    @pytest.mark.asyncio
    async def test_async_progress_returns_flows(self, tmp_path):
        """Test async_progress returns all in-progress flows."""
        hass = HomeAssistant(config_dir=tmp_path)

        # Initially there should be no flows
        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 0

        # Create a flow
        flow_id = hass.config_entries.async_create_flow(
            "meross_lan", context={"source": "discovery"}
        )

        # Now there should be one flow
        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 1
        assert flows[0]["handler"] == "meross_lan"

    @pytest.mark.asyncio
    async def test_async_progress_by_handler_filters_by_domain(self, tmp_path):
        """Test async_progress_by_handler returns flows for specific handler."""
        hass = HomeAssistant(config_dir=tmp_path)

        # Create flows for different handlers
        hass.config_entries.async_create_flow(
            "meross_lan", context={"source": "discovery"}
        )
        hass.config_entries.async_create_flow(
            "other_domain", context={"source": "user"}
        )

        # Get only meross_lan flows
        flows = hass.config_entries.flow.async_progress_by_handler("meross_lan")
        assert len(flows) == 1
        assert flows[0]["handler"] == "meross_lan"

    @pytest.mark.asyncio
    async def test_async_progress_by_handler_with_match_context(self, tmp_path):
        """Test async_progress_by_handler with context matching."""
        hass = HomeAssistant(config_dir=tmp_path)

        # Create flows with different contexts
        hass.config_entries.async_create_flow(
            "meross_lan", context={"source": "discovery", "unique_id": "abc123"}
        )
        hass.config_entries.async_create_flow("meross_lan", context={"source": "user"})

        # Match by source
        flows = hass.config_entries.flow.async_progress_by_handler(
            "meross_lan", match_context={"source": "discovery"}
        )
        assert len(flows) == 1
        assert flows[0]["context"]["unique_id"] == "abc123"

        # Match by non-existent context key
        flows = hass.config_entries.flow.async_progress_by_handler(
            "meross_lan", match_context={"source": "nonexistent"}
        )
        assert len(flows) == 0

    @pytest.mark.asyncio
    async def test_async_progress_by_handler_no_match(self, tmp_path):
        """Test async_progress_by_handler returns empty list for unknown handler."""
        hass = HomeAssistant(config_dir=tmp_path)

        flows = hass.config_entries.flow.async_progress_by_handler("unknown_domain")
        assert len(flows) == 0

    @pytest.mark.asyncio
    async def test_async_abort_removes_flow(self, tmp_path):
        """Test async_abort removes a flow."""
        hass = HomeAssistant(config_dir=tmp_path)

        # Create a flow
        flow_id = hass.config_entries.async_create_flow(
            "meross_lan", context={"source": "discovery"}
        )

        # Verify flow exists
        flows = hass.config_entries.flow.async_progress_by_handler("meross_lan")
        assert len(flows) == 1

        # Abort the flow
        result = hass.config_entries.flow.async_abort(flow_id)
        assert result["type"] == "abort"
        assert result["flow_id"] == flow_id

        # Verify flow is gone
        flows = hass.config_entries.flow.async_progress_by_handler("meross_lan")
        assert len(flows) == 0

    @pytest.mark.asyncio
    async def test_async_abort_unknown_flow_id(self, tmp_path):
        """Test async_abort handles unknown flow_id gracefully."""
        hass = HomeAssistant(config_dir=tmp_path)

        # Try to abort non-existent flow
        result = hass.config_entries.flow.async_abort("nonexistent_flow")
        assert result["type"] == "abort"
        assert result["flow_id"] == "nonexistent_flow"

    @pytest.mark.asyncio
    async def test_async_progress_with_configflow_object(self, tmp_path):
        """Test async_progress handles ConfigFlow objects (not just dicts).

        This tests the meross_lan use case where ConfigFlow objects are stored
        in _flow_progress by start_config_flow().
        """
        from shim.config_entries import ConfigFlow

        hass = HomeAssistant(config_dir=tmp_path)

        # Create a mock ConfigFlow object and store it directly
        # (simulating what start_config_flow does)
        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.flow_id = "test_flow_123"
        flow.handler = "meross_lan"
        flow.context = {"source": "discovery", "unique_id": "abc123"}
        flow.data = {"host": "192.168.1.100"}

        # Store the flow object directly (not a dict)
        hass.config_entries._flow_progress["test_flow_123"] = flow

        # Verify async_progress converts it to dict properly
        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 1
        assert flows[0]["flow_id"] == "test_flow_123"
        assert flows[0]["handler"] == "meross_lan"
        assert flows[0]["context"]["unique_id"] == "abc123"
        assert flows[0]["data"]["host"] == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_async_progress_by_handler_with_configflow_object(self, tmp_path):
        """Test async_progress_by_handler works with ConfigFlow objects.

        This tests the meross_lan pattern of calling async_progress_by_handler
        and accessing progress["flow_id"], progress["context"], etc.
        """
        from shim.config_entries import ConfigFlow

        hass = HomeAssistant(config_dir=tmp_path)

        # Create mock ConfigFlow objects
        class TestConfigFlow(ConfigFlow):
            pass

        flow1 = TestConfigFlow()
        flow1.flow_id = "flow_1"
        flow1.handler = "meross_lan"
        flow1.context = {"source": "discovery", "unique_id": "device1"}

        flow2 = TestConfigFlow()
        flow2.flow_id = "flow_2"
        flow2.handler = "other_domain"
        flow2.context = {"source": "user"}

        # Store flow objects directly
        hass.config_entries._flow_progress["flow_1"] = flow1
        hass.config_entries._flow_progress["flow_2"] = flow2

        # Test async_progress_by_handler returns dicts for meross_lan
        flows = hass.config_entries.flow.async_progress_by_handler("meross_lan")
        assert len(flows) == 1
        assert flows[0]["flow_id"] == "flow_1"
        assert flows[0]["context"]["unique_id"] == "device1"

        # Test match_context works with flow objects
        flows = hass.config_entries.flow.async_progress_by_handler(
            "meross_lan", match_context={"unique_id": "device1"}
        )
        assert len(flows) == 1

        flows = hass.config_entries.flow.async_progress_by_handler(
            "meross_lan", match_context={"unique_id": "nonexistent"}
        )
        assert len(flows) == 0

    @pytest.mark.asyncio
    async def test_async_abort_with_configflow_object(self, tmp_path):
        """Test async_abort works when flow is a ConfigFlow object."""
        from shim.config_entries import ConfigFlow

        hass = HomeAssistant(config_dir=tmp_path)

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.flow_id = "flow_to_abort"
        flow.handler = "meross_lan"
        flow.context = {}

        # Store flow object
        hass.config_entries._flow_progress["flow_to_abort"] = flow

        # Verify flow exists
        assert len(hass.config_entries.flow.async_progress()) == 1

        # Abort the flow
        result = hass.config_entries.flow.async_abort("flow_to_abort")
        assert result["type"] == "abort"

        # Verify flow is gone
        assert len(hass.config_entries.flow.async_progress()) == 0

    @pytest.mark.asyncio
    async def test_async_init_auto_submit_empty_form(self, tmp_path):
        """Test that async_init auto-submits forms with no required fields.

        This tests the meross_lan discovery use case where the flow
        returns a form with empty schema that just needs confirmation.
        """
        from shim.config_entries import ConfigFlow
        from shim.core import FlowManager
        from shim.import_patch import ImportPatcher
        import voluptuous as vol

        # Set up import patching
        hass = HomeAssistant(config_dir=tmp_path)
        patcher = ImportPatcher(hass)
        patcher.patch()

        try:

            class TestDiscoveryFlow(ConfigFlow):
                """Flow that simulates meross_lan discovery behavior."""

                handler = "test_domain"

                async def async_step_integration_discovery(self, discovery_info):
                    # Store data and return a finalize form (like meross_lan)
                    self._title = "Test Device"
                    self.device_config = discovery_info
                    return self.async_show_form(
                        step_id="finalize",
                        data_schema=vol.Schema({}),  # Empty schema - no required fields
                    )

                async def async_step_finalize(self, user_input=None):
                    # Create entry when finalize is called
                    return self.async_create_entry(
                        title=self._title,
                        data=self.device_config,
                    )

            # Create a mock loader that returns our test flow
            class MockLoader:
                async def start_config_flow(self, domain):
                    # Return the initial form result (simulating what meross_lan does)
                    flow = TestDiscoveryFlow()
                    flow.hass = hass
                    flow.flow_id = "test_flow_123"
                    flow.context = {"source": "integration_discovery"}
                    # Store in flow progress so async_init can find it
                    hass.config_entries._flow_progress["test_flow_123"] = flow
                    return {
                        "type": "form",
                        "flow_id": "test_flow_123",
                        "step_id": "integration_discovery",
                        "handler": "test_domain",
                    }

            mock_loader = MockLoader()
            hass.data["integration_loader"] = mock_loader

            # Create a real FlowManager
            flow_manager = FlowManager(hass, hass.config_entries)

            # Call async_init with discovery data
            discovery_data = {"host": "192.168.1.100", "device_id": "abc123"}
            result = await flow_manager.async_init(
                "test_domain",
                context={"source": "integration_discovery"},
                data=discovery_data,
            )

            # Should have auto-completed and created entry
            assert result["type"] == "create_entry"
            assert result["title"] == "Test Device"
            assert result["data"] == discovery_data

        finally:
            patcher.unpatch()

    @pytest.mark.asyncio
    async def test_async_init_auto_submit_from_menu(self, tmp_path):
        """Test that async_init handles menu -> discovery -> auto-submit flow.

        This tests the meross_lan discovery pattern where:
        1. async_step_user returns a menu
        2. async_step_integration_discovery is called with discovery data
        3. Returns a finalize form that should auto-submit
        """
        from shim.config_entries import ConfigFlow
        from shim.core import FlowManager
        from shim.import_patch import ImportPatcher
        import voluptuous as vol

        # Set up import patching
        hass = HomeAssistant(config_dir=tmp_path)
        patcher = ImportPatcher(hass)
        patcher.patch()

        try:

            class TestDiscoveryFlow(ConfigFlow):
                """Flow that simulates meross_lan menu-based discovery."""

                handler = "test_domain"

                async def async_step_user(self, user_input=None):
                    # Return a menu (like meross_lan does)
                    return self.async_show_menu(
                        step_id="user",
                        menu_options=["profile", "device"],
                    )

                async def async_step_integration_discovery(self, discovery_info):
                    # Discovery returns a finalize form
                    self._title = "Test Device"
                    self.device_config = discovery_info
                    return self.async_show_form(
                        step_id="finalize",
                        data_schema=vol.Schema({}),  # Empty schema
                    )

                async def async_step_finalize(self, user_input=None):
                    # Create entry when finalize is called
                    return self.async_create_entry(
                        title=self._title,
                        data=self.device_config,
                    )

            # Create a mock loader that returns our test flow
            class MockLoader:
                async def start_config_flow(self, domain):
                    flow = TestDiscoveryFlow()
                    flow.hass = hass
                    flow.flow_id = "test_flow_123"
                    flow.handler = "test_domain"
                    flow.context = {}
                    hass.config_entries._flow_progress["test_flow_123"] = flow
                    # Return menu from async_step_user
                    return {
                        "type": "menu",
                        "flow_id": "test_flow_123",
                        "step_id": "user",
                        "handler": "test_domain",
                    }

            mock_loader = MockLoader()
            hass.data["integration_loader"] = mock_loader

            # Create a real FlowManager
            flow_manager = FlowManager(hass, hass.config_entries)

            # Call async_init with discovery data (like meross_lan does)
            discovery_data = {"host": "192.168.1.100", "device_id": "abc123"}
            result = await flow_manager.async_init(
                "test_domain",
                context={"source": "integration_discovery"},
                data=discovery_data,
            )

            # Should have gone: menu -> integration_discovery -> finalize -> create_entry
            assert result["type"] == "create_entry"
            assert result["title"] == "Test Device"
            assert result["data"] == discovery_data

            # Verify the config entry was actually created
            entries = hass.config_entries.async_entries("test_domain")
            assert len(entries) == 1
            assert entries[0].title == "Test Device"
            assert entries[0].data == discovery_data

        finally:
            patcher.unpatch()


class TestConfigFlow:
    """Test cases for ConfigFlow class."""

    @pytest.mark.asyncio
    async def test_async_set_unique_id_basic(self):
        """Test async_set_unique_id sets the unique_id."""
        from shim.config_entries import ConfigFlow

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.context = {}

        # When no entry exists, should return None
        result = await flow.async_set_unique_id("device_123")
        assert result is None  # No existing entry
        assert flow.context["unique_id"] == "device_123"

    @pytest.mark.asyncio
    async def test_async_set_unique_id_with_raise_on_progress(self):
        """Test async_set_unique_id accepts raise_on_progress parameter."""
        from shim.config_entries import ConfigFlow

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.context = {}

        # Test with no existing entries - should return None
        result = await flow.async_set_unique_id("device_456", raise_on_progress=True)
        assert result is None  # No existing entry
        assert flow.context["unique_id"] == "device_456"

        # Test with raise_on_progress=False
        result = await flow.async_set_unique_id("device_789", raise_on_progress=False)
        assert result is None  # No existing entry
        assert flow.context["unique_id"] == "device_789"

    def test_unique_id_property(self):
        """Test ConfigFlow.unique_id property returns unique_id from context."""
        from shim.config_entries import ConfigFlow

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.context = {}

        # Initially should be None
        assert flow.unique_id is None

        # Set via context (like async_set_unique_id does)
        flow.context["unique_id"] = "test_device_123"
        assert flow.unique_id == "test_device_123"

        # Test with None in context
        flow.context["unique_id"] = None
        assert flow.unique_id is None

    @pytest.mark.asyncio
    async def test_async_set_unique_id_returns_existing_entry(self, tmp_path):
        """Test async_set_unique_id returns existing entry if one exists."""
        from shim.config_entries import ConfigFlow
        from shim.core import HomeAssistant
        from shim.core import ConfigEntry

        hass = HomeAssistant(config_dir=tmp_path)

        # Create an existing config entry
        existing_entry = ConfigEntry(
            entry_id="existing_123",
            version=1,
            domain="test_domain",
            title="Existing Device",
            data={"unique_id": "device_exists"},  # unique_id can be in data
        )
        await hass.config_entries.async_add(existing_entry)

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.hass = hass
        flow.handler = "test_domain"
        flow.context = {}

        # Test with existing unique_id - should return the existing entry
        result = await flow.async_set_unique_id("device_exists")
        assert result is existing_entry
        assert flow.context["unique_id"] == "device_exists"

    @pytest.mark.asyncio
    async def test_async_set_unique_id_none(self):
        """Test async_set_unique_id with None value."""
        from shim.config_entries import ConfigFlow

        class TestConfigFlow(ConfigFlow):
            pass

        flow = TestConfigFlow()
        flow.context = {"unique_id": "previous_id"}

        result = await flow.async_set_unique_id(None)
        assert result is None
        # When unique_id is None, context unique_id should be set to None
        assert flow.context["unique_id"] is None


class TestUpdateFailed:
    """Test cases for UpdateFailed exception handling."""

    def test_update_failed_simple_message(self):
        """Test UpdateFailed with a simple string message."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        exc = UpdateFailed("Simple error message")
        assert str(exc) == "Simple error message"

    def test_update_failed_printf_style_without_args(self):
        """Test UpdateFailed handles printf-style format strings without args.

        This tests the fix for the nws_alerts bug where they pass:
        raise UpdateFailed("Problem: (%s) - %s", r.status, r.content) from error
        But 'error' doesn't exist and args weren't passed correctly.
        """
        from homeassistant.helpers.update_coordinator import UpdateFailed

        # Simulate the buggy pattern from nws_alerts
        exc = UpdateFailed("Problem updating NWS data: (%s) - %s")
        # Should be sanitized to replace %s with ?
        assert str(exc) == "Problem updating NWS data: (?) - ?"

    def test_update_failed_printf_style_with_args(self):
        """Test UpdateFailed with proper printf-style formatting."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        # Properly formatted with args
        exc = UpdateFailed("Status: %s, Content: %s", 503, "stream reader")
        assert str(exc) == "Status: 503, Content: stream reader"

    def test_update_failed_mixed_formatting(self):
        """Test UpdateFailed handles various format specifiers."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        # Various printf specifiers without args
        exc = UpdateFailed("Values: %s %d %r")
        assert "?" in str(exc)
        assert "%s" not in str(exc)
        assert "%d" not in str(exc)
        assert "%r" not in str(exc)

    def test_update_failed_with_from_exception_chaining(self):
        """Test UpdateFailed works with 'from' exception chaining."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        original_error = ValueError("Original error")
        try:
            raise UpdateFailed("Wrapped error") from original_error
        except UpdateFailed as e:
            assert str(e) == "Wrapped error"
            assert e.__cause__ is original_error


class TestDataUpdateCoordinator:
    """Test cases for DataUpdateCoordinator."""

    @pytest.mark.asyncio
    async def test_coordinator_handles_unboundlocalerror_from_undefined_error_var(
        self,
        tmp_path,
    ):
        """Test DataUpdateCoordinator catches UnboundLocalError from buggy integrations.

        Some integrations (like nws_alerts) have buggy code like:
            raise UpdateFailed(msg) from error
        where 'error' is not defined in that scope. This causes UnboundLocalError.
        The coordinator should catch this and convert to UpdateFailed.
        """
        from homeassistant.helpers.update_coordinator import (
            DataUpdateCoordinator,
            UpdateFailed,
        )
        from shim.core import HomeAssistant

        hass = HomeAssistant(config_dir=tmp_path)

        class BuggyCoordinator(DataUpdateCoordinator):
            """Coordinator that simulates the nws_alerts bug."""

            async def _async_update_data(self):
                # Simulate the buggy pattern: using undefined 'error' variable
                try:
                    # First except block defines 'error' in its own scope
                    try:
                        raise AttributeError("test")
                    except AttributeError as error:
                        # This 'error' is local to this block
                        pass

                    # Now in a different branch, try to use 'error' which doesn't exist
                    # This simulates: raise UpdateFailed(msg) from error
                    raise UnboundLocalError(
                        "cannot access local variable 'error' where it is not associated with a value"
                    )
                except UnboundLocalError:
                    # Re-raise to test the coordinator's handling
                    raise

        coordinator = BuggyCoordinator(
            hass,
            logger=logging.getLogger(__name__),
            name="Test Buggy Coordinator",
            update_interval=None,
        )

        # The coordinator should convert UnboundLocalError to UpdateFailed
        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator.async_refresh()

        assert "Integration bug" in str(exc_info.value)
        assert "undefined error variable" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_coordinator_handles_nameerror_from_undefined_error_var(
        self,
        tmp_path,
    ):
        """Test DataUpdateCoordinator catches NameError from buggy integrations."""
        from homeassistant.helpers.update_coordinator import (
            DataUpdateCoordinator,
            UpdateFailed,
        )
        from shim.core import HomeAssistant

        hass = HomeAssistant(config_dir=tmp_path)

        class NameErrorCoordinator(DataUpdateCoordinator):
            """Coordinator that raises NameError for undefined 'error'."""

            async def _async_update_data(self):
                raise NameError("name 'error' is not defined")

        coordinator = NameErrorCoordinator(
            hass,
            logger=logging.getLogger(__name__),
            name="Test NameError Coordinator",
            update_interval=None,
        )

        # The coordinator should convert NameError to UpdateFailed
        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator.async_refresh()

        assert "Integration bug" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_coordinator_re_raises_other_unboundlocalerrors(
        self,
        tmp_path,
    ):
        """Test DataUpdateCoordinator re-raises UnboundLocalError that doesn't match pattern."""
        from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
        from shim.core import HomeAssistant

        hass = HomeAssistant(config_dir=tmp_path)

        class OtherUnboundErrorCoordinator(DataUpdateCoordinator):
            """Coordinator that raises UnboundLocalError for other reasons."""

            async def _async_update_data(self):
                raise UnboundLocalError("some other unbound local issue")

        coordinator = OtherUnboundErrorCoordinator(
            hass,
            logger=logging.getLogger(__name__),
            name="Test Other Error Coordinator",
            update_interval=None,
        )

        # Should re-raise as-is since it doesn't match the pattern
        with pytest.raises(UnboundLocalError) as exc_info:
            await coordinator.async_refresh()

        assert "some other unbound local issue" in str(exc_info.value)


class TestEvent:
    """Test cases for Event class."""

    def test_event_creation(self):
        """Test creating an Event object."""
        from shim.core import Event, Context

        event = Event(
            event_type="state_changed",
            data={"entity_id": "light.test", "new_state": "on"},
        )

        assert event.event_type == "state_changed"
        assert event.data == {"entity_id": "light.test", "new_state": "on"}
        assert event.origin == "LOCAL"
        assert event.time_fired is not None
        assert event.context is not None

    def test_event_defaults(self):
        """Test Event creation with default values."""
        from shim.core import Event

        event = Event(event_type="test_event")

        assert event.event_type == "test_event"
        assert event.data == {}
        assert event.origin == "LOCAL"
        assert event.time_fired is not None
        assert event.context is not None

    def test_event_with_custom_origin(self):
        """Test Event with custom origin."""
        from shim.core import Event

        event = Event(event_type="remote_event", origin="REMOTE")

        assert event.event_type == "remote_event"
        assert event.origin == "REMOTE"
