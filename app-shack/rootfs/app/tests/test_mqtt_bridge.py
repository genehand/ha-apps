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
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.username_pw_set = Mock()
            mock_client.reconnect_delay_set = Mock()

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
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.username_pw_set = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            mock_client.username_pw_set.assert_called_once_with(
                "test_user", "test_pass"
            )

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test MQTT connection failure - app continues with built-in reconnection."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_failure(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 5, None)  # Auth error

            mock_client.connect = Mock(side_effect=simulate_failure)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            # Should not raise - built-in reconnection handles it
            await bridge.connect()

            # Verify we're not connected but watchdog is monitoring
            assert not bridge._connected
            assert bridge._connection_error is not None  # connection_error is set

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

    def test_connection_status_initial(self):
        """Test connection_status before connection."""
        bridge = MqttBridge(host="localhost", port=1883)

        status = bridge.connection_status

        assert status["connected"] is False
        assert status["host"] == "localhost"
        assert status["port"] == 1883
        assert status["status_text"] == "Connecting..."
        assert status["status_class"] == "connecting"

    @pytest.mark.asyncio
    async def test_connection_status_connected(self):
        """Test connection_status after successful connection."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            status = bridge.connection_status
            assert status["connected"] is True
            assert status["status_text"] == "Connected"
            assert status["status_class"] == "connected"
            assert "error" not in status

    @pytest.mark.asyncio
    async def test_connection_status_after_disconnect(self):
        """Test connection_status after disconnection."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            # Simulate disconnect with error code
            mock_client.on_disconnect(mock_client, None, {}, 7, None)

            status = bridge.connection_status
            assert status["connected"] is False
            assert status["status_class"] == "disconnected"
            assert "error" in status
            # Error message contains the reason code (format: "(7): 7")
            assert "7" in status["error"]

    def test_last_disconnect_error_initial(self):
        """Test last_disconnect_error is initially None."""
        bridge = MqttBridge(host="localhost")
        assert bridge.last_disconnect_error is None

    @pytest.mark.asyncio
    async def test_last_disconnect_error_on_disconnect(self):
        """Test last_disconnect_error is set on unexpected disconnect."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            # Simulate unexpected disconnect with error code 7
            mock_client.on_disconnect(mock_client, None, {}, 7, None)

            assert bridge.last_disconnect_error is not None
            # Error message format is: "MQTT disconnect (7): 7"
            assert "7" in bridge.last_disconnect_error

    @pytest.mark.asyncio
    async def test_last_disconnect_error_cleared_on_connect(self):
        """Test last_disconnect_error is cleared on successful reconnect."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            # Simulate disconnect
            mock_client.on_disconnect(mock_client, None, {}, 7, None)
            assert bridge.last_disconnect_error is not None

            # Simulate reconnect
            mock_client.on_connect(mock_client, None, {}, 0, None)
            assert bridge.last_disconnect_error is None

    @pytest.mark.asyncio
    async def test_clean_disconnect_clears_error(self):
        """Test clean disconnect (rc=0) clears the error."""
        bridge = MqttBridge(host="localhost")

        with patch("mqtt_bridge.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            def simulate_connect(*args, **kwargs):
                mock_client.on_connect(mock_client, None, {}, 0, None)

            mock_client.connect = Mock(side_effect=simulate_connect)
            mock_client.loop_start = Mock()
            mock_client.reconnect_delay_set = Mock()

            await bridge.connect()

            # Simulate error disconnect
            mock_client.on_disconnect(mock_client, None, {}, 7, None)
            assert bridge.last_disconnect_error is not None

            # Simulate clean disconnect
            mock_client.on_disconnect(mock_client, None, {}, 0, None)
            assert bridge.last_disconnect_error is None
