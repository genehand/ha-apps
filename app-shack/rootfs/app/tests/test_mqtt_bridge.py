"""Tests for MQTT bridge functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import asyncio

import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from mqtt_bridge import MqttBridge


class TestMqttBridge:
    """Test cases for MqttBridge class."""

    def test_init_default_values(self):
        """Test that MqttBridge initializes with default values."""
        bridge = MqttBridge(host="localhost")

        assert bridge.host == "localhost"
        assert bridge.port == 1883
        assert bridge.username is None
        assert bridge.password is None
        assert bridge._client is None

    def test_init_custom_values(self):
        """Test that MqttBridge initializes with custom values."""
        bridge = MqttBridge(
            host="mqtt.example.com",
            port=8883,
            username="test_user",
            password="test_pass",
        )

        assert bridge.host == "mqtt.example.com"
        assert bridge.port == 8883
        assert bridge.username == "test_user"
        assert bridge.password == "test_pass"

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful MQTT connection."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            # Simulate successful connection
            def simulate_connect(*args, **kwargs):
                # Call the on_connect callback with success code
                mock_client.on_connect(mock_client, None, {}, 0)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.username_pw_set = Mock()

            await bridge.connect()

            assert bridge._client is not None
            mock_client.connect.assert_called_once_with("localhost", 1883)
            mock_client.loop_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_with_auth(self):
        """Test MQTT connection with authentication."""
        bridge = MqttBridge(
            host="localhost", username="test_user", password="test_pass"
        )

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.username_pw_set = Mock()

            await bridge.connect()

            mock_client.username_pw_set.assert_called_once_with(
                "test_user", "test_pass"
            )

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test MQTT connection failure."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_failure(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 5)  # Auth error

            mock_client.connect = Mock(side_effect=simulate_failure)
            mock_client.loop_start = Mock()
            mock_client.loop_stop = Mock()
            mock_client.disconnect = Mock()

            with pytest.raises(Exception, match="not authorised"):
                await bridge.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test MQTT disconnection."""
        bridge = MqttBridge(host="localhost")
        mock_client = MagicMock()
        bridge._client = mock_client
        bridge._connected = True

        await bridge.disconnect()

        # Assert on the saved mock reference since bridge._client is now None
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_disconnect_no_client(self):
        """Test disconnect when no client is connected."""
        bridge = MqttBridge(host="localhost")

        # Should not raise any errors
        await bridge.disconnect()

        assert bridge._client is None

    def test_client_property(self):
        """Test the client property returns the internal client."""
        bridge = MqttBridge(host="localhost")

        # Initially None
        assert bridge.client is None

        # After setting client
        mock_client = MagicMock()
        bridge._client = mock_client
        assert bridge.client is mock_client
