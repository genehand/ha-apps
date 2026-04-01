"""MQTT bridge for Home Assistant integration."""

import logging
from typing import Optional

from paho.mqtt.client import Client

logger = logging.getLogger("shack.mqtt")


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

    async def connect(self):
        """Connect to MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {self.host}:{self.port}")

        # Create MQTT client
        self._client = Client()

        # Track connection state
        self._connected = False
        self._connection_error = None

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                logger.debug("Connected to MQTT broker")
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
            if rc != 0:
                logger.warning(f"Unexpected MQTT disconnection (rc={rc})")
            self._connected = False

        self._client.on_connect = on_connect
        self._client.on_disconnect = on_disconnect

        if self.username and self.password:
            self._client.username_pw_set(self.username, self.password)

        try:
            self._client.connect(self.host, self.port)
            self._client.loop_start()

            # Wait for connection with timeout
            import asyncio

            for _ in range(50):  # 5 seconds timeout
                if self._connected:
                    break
                if self._connection_error:
                    raise Exception(self._connection_error)
                await asyncio.sleep(0.1)

            if not self._connected:
                raise Exception("Connection timeout - no response from broker")

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            if self._client:
                self._client.loop_stop()
                self._client = None
            raise

    async def disconnect(self):
        """Disconnect from MQTT broker."""
        if self._client:
            logger.info("Disconnecting from MQTT broker")
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False

    @property
    def client(self) -> Optional[Client]:
        """Get the raw MQTT client for manual publishing."""
        return self._client
