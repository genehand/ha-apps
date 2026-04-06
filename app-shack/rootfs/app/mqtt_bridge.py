"""MQTT bridge for Home Assistant integration."""

import asyncio
import logging
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

from paho.mqtt.client import Client

logger = logging.getLogger("shack.mqtt")

# Reconnection settings
RECONNECT_TIMEOUT_SECONDS = 120  # Exit if disconnected for this long
RECONNECT_CHECK_INTERVAL = 5  # Check connection status every 5 seconds


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

    async def connect(self):
        """Connect to MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {self.host}:{self.port}")

        # Create MQTT client with clean_session=False to persist subscriptions
        # on the broker side (if broker supports it). Requires a client_id.
        self._client = Client(client_id="hacs-shack", clean_session=False)

        # Track connection state
        self._connected = False
        self._connection_error = None

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                self._last_connected_time = time.time()
                self._initial_connect_complete = True
                logger.info("Connected to MQTT broker")
                # Re-subscribe to all topics on reconnect
                self._resubscribe_all()
            else:
                error_codes = {
                    1: "incorrect protocol version",
                    2: "invalid client identifier",
                    3: "server unavailable",
                    4: "bad username or password",
                    5: "not authorised",
                }
                error_msg = error_codes.get(rc, f"unknown error (code {rc})")
                self._connection_error = f"Connection failed: {error_msg}"
                logger.error(f"MQTT connection failed: {error_msg}")

        def on_disconnect(client, userdata, rc):
            self._connected = False
            if rc != 0:
                logger.warning(f"Unexpected MQTT disconnection (rc={rc})")
            else:
                logger.info("MQTT disconnected cleanly")

        self._client.on_connect = on_connect
        self._client.on_disconnect = on_disconnect

        if self.username and self.password:
            self._client.username_pw_set(self.username, self.password)

        try:
            self._client.connect(self.host, self.port)
            self._client.loop_start()

            # Wait for connection with timeout
            for _ in range(50):  # 5 seconds timeout
                if self._connected:
                    break
                if self._connection_error:
                    raise Exception(self._connection_error)
                await asyncio.sleep(0.1)

            if not self._connected:
                raise Exception("Connection timeout - no response from broker")

            # Start the connection watchdog
            self._watchdog_task = asyncio.create_task(self._connection_watchdog())

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            if self._client:
                self._client.loop_stop()
                self._client = None
            raise

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
        # Stop the watchdog first
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
