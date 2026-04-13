"""MQTT bridge for Home Assistant integration."""

import asyncio
import logging
import sys
import time
import random
import string
from typing import Callable, Dict, List, Optional, Tuple

from paho.mqtt.client import Client, CallbackAPIVersion

logger = logging.getLogger("shack.mqtt")

# Reconnection settings
RECONNECT_TIMEOUT_SECONDS = 120  # Exit if disconnected for this long
RECONNECT_CHECK_INTERVAL = 5  # Check connection status every 5 seconds
RECONNECT_DELAY_MIN = 5  # Min seconds between reconnect attempts
RECONNECT_DELAY_MAX = 300  # Max seconds between reconnect attempts


class MqttBridge:
    """Bridge between device protocols and Home Assistant MQTT discovery."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._client: Optional[Client] = None
        self._subscriptions: Dict[str, Callable] = {}  # topic -> callback
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_connected_time: float = 0
        self._initial_connect_complete: bool = False
        self._last_disconnect_error: Optional[str] = None
        self._connected: bool = False
        self._shutdown: bool = False
        self._connection_error: Optional[str] = None

    @property
    def last_disconnect_error(self) -> Optional[str]:
        """Get the last MQTT disconnection error message if any."""
        return self._last_disconnect_error

    @property
    def connection_status(self) -> dict:
        """Get the current MQTT connection status."""
        import time

        status = {
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
        }

        if not self._connected and self._last_disconnect_error:
            status["error"] = self._last_disconnect_error

        if self._connected:
            status["status_text"] = "Connected"
            status["status_class"] = "connected"
        elif self._initial_connect_complete:
            # Was connected but now disconnected
            disconnected_duration = time.time() - self._last_connected_time
            status["status_text"] = f"Disconnected ({int(disconnected_duration)}s)"
            status["status_class"] = "disconnected"
        else:
            # Never successfully connected
            status["status_text"] = "Connecting..."
            status["status_class"] = "connecting"

        return status

    async def connect(self):
        """Connect to MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {self.host}:{self.port}")

        # Create MQTT client with a unique client_id to avoid conflicts
        # when reconnecting after a crash or restart (broker may still have
        # old session). Use random suffix for uniqueness.
        random_suffix = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=6)
        )
        client_id = f"hacs-shack-{random_suffix}"

        # Configure client with built-in automatic reconnection
        self._client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
            reconnect_on_failure=True,
        )
        self._client.reconnect_delay_set(
            min_delay=RECONNECT_DELAY_MIN,
            max_delay=RECONNECT_DELAY_MAX,
        )
        logger.debug(f"Using MQTT client_id: {client_id}")

        # Reset connection state tracking
        self._connected = False
        self._connection_error = None

        def on_connect(client, userdata, flags, rc, properties):
            if rc == 0:
                self._connected = True
                self._last_connected_time = time.time()
                self._initial_connect_complete = True
                self._last_disconnect_error = None  # Clear any previous error
                logger.info("Connected to MQTT broker")
                # Re-subscribe to all topics on reconnect
                self._resubscribe_all()
            else:
                # rc is a ReasonCode object - convert to string for error message
                error_msg = str(rc)
                self._connection_error = f"Connection failed: {error_msg}"
                logger.error(f"MQTT connection failed: {error_msg}")

        def on_disconnect(client, userdata, disconnect_flags, rc, properties):
            self._connected = False
            if rc != 0:
                error_msg = str(rc)
                self._last_disconnect_error = f"MQTT disconnect ({rc}): {error_msg}"
                logger.warning(f"Unexpected MQTT disconnection ({rc})")
            else:
                self._last_disconnect_error = None
                logger.info("MQTT disconnected cleanly")

        self._client.on_connect = on_connect
        self._client.on_disconnect = on_disconnect

        if self.username and self.password:
            self._client.username_pw_set(self.username, self.password)

        try:
            self._client.connect(self.host, self.port)
            self._client.loop_start()

            # Wait for initial connection with timeout
            for _ in range(50):  # 5 seconds timeout
                if self._connected:
                    break
                if self._connection_error:
                    # Connection failed initially - log and let built-in reconnection handle it
                    logger.warning(
                        f"Initial MQTT connection failed: {self._connection_error}. "
                        f"Will retry with built-in reconnection."
                    )
                    break
                await asyncio.sleep(0.1)

            # Start the connection watchdog (monitors for prolonged disconnections)
            self._watchdog_task = asyncio.create_task(self._connection_watchdog())

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            if self._client:
                self._client.loop_stop()
                self._client = None
            # Don't raise - let the app continue so UI can show the error
            self._last_disconnect_error = str(e)

    def _resubscribe_all(self):
        """Re-subscribe to all registered topics after reconnection."""
        if not self._client:
            return

        if self._subscriptions:
            logger.debug(f"Re-subscribing to {len(self._subscriptions)} topic(s)")
            for topic, callback in self._subscriptions.items():
                self._client.message_callback_add(topic, callback)
                result, mid = self._client.subscribe(topic)
                logger.debug(f"Re-subscribed to {topic} (result={result})")

    async def _connection_watchdog(self):
        """Monitor connection and exit if disconnected for too long."""
        while True:
            await asyncio.sleep(RECONNECT_CHECK_INTERVAL)

            if not self._connected and self._initial_connect_complete:
                disconnected_duration = time.time() - self._last_connected_time

                if disconnected_duration > RECONNECT_TIMEOUT_SECONDS:
                    logger.error(
                        f"MQTT connection lost for {disconnected_duration:.0f}s, "
                        f"exceeding timeout of {RECONNECT_TIMEOUT_SECONDS}s. Exiting."
                    )
                    # Exit the application - container/supervisor will restart us
                    sys.exit(1)
                elif disconnected_duration > 30:
                    # Warn every 30 seconds
                    logger.warning(
                        f"MQTT disconnected for {disconnected_duration:.0f}s, "
                        f"retrying..."
                    )

    def subscribe(self, topic: str, callback: Callable) -> Tuple[int, int]:
        """Subscribe to an MQTT topic with a callback.

        The subscription is persisted and automatically re-subscribed on reconnect.

        Args:
            topic: The MQTT topic to subscribe to
            callback: The callback function for messages on this topic

        Returns:
            Tuple of (result_code, message_id)
        """
        if not self._client:
            raise RuntimeError("MQTT client not connected")

        # Store the subscription for reconnection
        self._subscriptions[topic] = callback

        # Add callback and subscribe
        self._client.message_callback_add(topic, callback)
        result, mid = self._client.subscribe(topic)
        logger.debug(f"Subscribed to {topic} (result={result}, mid={mid})")
        return result, mid

    def unsubscribe(self, topic: str) -> Tuple[int, int]:
        """Unsubscribe from an MQTT topic.

        Args:
            topic: The MQTT topic to unsubscribe from

        Returns:
            Tuple of (result_code, message_id)
        """
        if not self._client:
            raise RuntimeError("MQTT client not connected")

        # Remove from stored subscriptions
        self._subscriptions.pop(topic, None)

        result, mid = self._client.unsubscribe(topic)
        logger.debug(f"Unsubscribed from {topic} (result={result}, mid={mid})")
        return result, mid

    async def disconnect(self):
        """Disconnect from MQTT broker."""
        self._shutdown = True

        # Stop the watchdog
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None

        if self._client:
            logger.info("Disconnecting from MQTT broker")
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False
            self._subscriptions.clear()

    @property
    def client(self) -> Optional[Client]:
        """Get the raw MQTT client for manual publishing."""
        return self._client

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to MQTT broker."""
        return self._connected
