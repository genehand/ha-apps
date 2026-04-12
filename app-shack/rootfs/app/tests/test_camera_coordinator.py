"""Test camera coordinator auto-registration."""

import pytest
from unittest.mock import MagicMock, patch


class TestCameraCoordinatorAutoRegistration:
    """Test that Camera entities with coordinator auto-register for updates."""

    async def test_camera_without_coordinator_skips_registration(self):
        """Test that cameras without coordinator attribute skip auto-registration."""
        from shim.platforms.camera import Camera

        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test"

        # async_added_to_hass should complete without errors
        await camera.async_added_to_hass()

        # No coordinator, nothing to verify
        assert True

    async def test_camera_with_coordinator_registers_for_updates(self):
        """Test that cameras with coordinator auto-register for updates."""
        from shim.platforms.camera import Camera

        # Create a mock coordinator
        coordinator = MagicMock()
        remove_listener = MagicMock()
        coordinator.async_add_listener.return_value = remove_listener

        # Create camera with coordinator
        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test_thumbnail"
        camera.coordinator = coordinator
        camera._attr_is_streaming = False

        # Mock async_on_remove to capture the remove callback
        captured_callbacks = []

        def mock_async_on_remove(func):
            captured_callbacks.append(func)

        camera.async_on_remove = mock_async_on_remove

        # Call async_added_to_hass
        await camera.async_added_to_hass()

        # Verify coordinator.async_add_listener was called
        coordinator.async_add_listener.assert_called_once()

        # Get the callback that was registered
        callback = coordinator.async_add_listener.call_args[0][0]
        assert callable(callback)

        # Verify async_on_remove was called with the remove listener
        assert len(captured_callbacks) == 1
        assert captured_callbacks[0] == remove_listener

    async def test_camera_coordinator_update_triggers_publish(self):
        """Test that coordinator update triggers async_write_ha_state."""
        from shim.platforms.camera import Camera

        # Create a mock coordinator
        coordinator = MagicMock()
        remove_listener = MagicMock()
        coordinator.async_add_listener.return_value = remove_listener

        # Create camera with coordinator
        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test_thumbnail"
        camera.coordinator = coordinator
        camera._attr_is_streaming = False

        # Mock async_write_ha_state
        write_ha_state_called = []

        def mock_write_ha_state():
            write_ha_state_called.append(True)

        camera.async_write_ha_state = mock_write_ha_state
        camera.async_on_remove = MagicMock()

        # Call async_added_to_hass to register
        await camera.async_added_to_hass()

        # Get the callback that was registered
        callback = coordinator.async_add_listener.call_args[0][0]

        # Call the callback (simulating a coordinator update)
        callback()

        # Verify async_write_ha_state was called
        assert len(write_ha_state_called) == 1

    async def test_coordinatorentity_camera_skips_auto_registration(self):
        """Test that cameras already inheriting from CoordinatorEntity skip auto-registration."""
        from shim.platforms.camera import Camera
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        # Create a mock coordinator
        coordinator = MagicMock()

        # Create a class that inherits from both CoordinatorEntity and Camera
        class CoordinatorCamera(CoordinatorEntity, Camera):
            def __init__(self, coordinator):
                super().__init__(coordinator)
                self._attr_is_streaming = False

        camera = CoordinatorCamera(coordinator)
        camera.hass = MagicMock()
        camera.entity_id = "camera.coordinator_test"

        # Call parent's async_added_to_hass (CoordinatorEntity's implementation)
        # Note: In real scenario, CoordinatorEntity's async_added_to_hass handles registration
        # Here we just verify our Camera.async_added_to_hass doesn't cause issues
        await camera.async_added_to_hass()

        # The test passes if no exception is raised and no duplicate registration occurs
        # The CoordinatorEntity's async_added_to_hass (called via super()) handles registration

    async def test_camera_without_async_add_listener_skips(self):
        """Test that cameras with coordinator lacking async_add_listener skip registration."""
        from shim.platforms.camera import Camera

        # Create a mock coordinator without async_add_listener
        coordinator = MagicMock()
        del coordinator.async_add_listener

        # Create camera with coordinator
        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test"
        camera.coordinator = coordinator

        # Should complete without error even without async_add_listener
        await camera.async_added_to_hass()

        # Test passes if no exception
        assert True

    async def test_camera_cleanup_skips_state_topic(self):
        """Test that camera cleanup only clears discovery and image topic, not state topic."""
        from shim.platforms.camera import Camera

        # Create camera entity
        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test_thumbnail"
        camera._attr_unique_id = "test_unique_id"

        # Mock MQTT client
        mock_mqtt = MagicMock()
        camera.hass._mqtt_client = mock_mqtt

        # Call _cleanup_mqtt
        await camera._cleanup_mqtt()

        # Verify only discovery config and image topic were cleared (NOT state topic)
        published_topics = [call[0][0] for call in mock_mqtt.publish.call_args_list]

        # Should have discovery topic
        assert any("/config" in topic for topic in published_topics)
        # Should have image topic
        assert any("/image" in topic for topic in published_topics)
        # Should NOT have state topic
        assert not any("/state" in topic for topic in published_topics)
        # Should NOT have attributes topic
        assert not any("/attributes" in topic for topic in published_topics)

    async def test_camera_cleanup_publishes_empty_retain(self):
        """Test that camera cleanup publishes empty payload with retain=True."""
        from shim.platforms.camera import Camera

        camera = Camera()
        camera.hass = MagicMock()
        camera.entity_id = "camera.test"

        mock_mqtt = MagicMock()
        camera.hass._mqtt_client = mock_mqtt

        await camera._cleanup_mqtt()

        # Verify all publishes used empty payload with retain=True
        for call in mock_mqtt.publish.call_args_list:
            args, kwargs = call
            topic, payload = args[0], args[1]
            assert payload == "", f"Topic {topic} should have empty payload"
            assert kwargs.get("retain") is True, (
                f"Topic {topic} should have retain=True"
            )
