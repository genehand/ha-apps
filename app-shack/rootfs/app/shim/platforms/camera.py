"""Camera platform shim.

Provides Camera entity base class for compatibility.
"""

from typing import Any, Optional

from ..entity import Entity, EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed

DOMAIN = "camera"


class CameraEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes camera entities."""


class Camera(Entity):
    """Base class for camera entities."""

    _attr_is_streaming: bool = False
    _attr_is_recording: bool = False

    @property
    def is_streaming(self) -> bool:
        """Return True if entity is streaming."""
        return self._attr_is_streaming

    @property
    def is_recording(self) -> bool:
        """Return True if entity is recording."""
        return self._attr_is_recording

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> bytes:
        """Return bytes of camera image."""
        raise NotImplementedError()

    async def async_camera_stream(self) -> bytes:
        """Return stream of camera."""
        raise NotImplementedError()

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery config for camera.

        Only publishes for non-streaming cameras (static images like thumbnails).
        Streaming cameras (MjpegCamera) are skipped as they don't work well over MQTT.
        """
        # Skip streaming cameras - only publish thumbnail/static cameras
        if self.is_streaming or isinstance(self, MjpegCamera):
            from ..logging import get_logger

            _LOGGER = get_logger(__name__)
            _LOGGER.debug(
                f"Skipping MQTT discovery for streaming camera {self.entity_id}"
            )
            return

        # For static cameras, publish camera-specific discovery with image_topic
        if not self.entity_id:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        from ..entity import (
            get_entity_name_for_discovery,
            get_mqtt_safe_unique_id,
            build_mqtt_device_config,
        )
        import json

        discovery_topic = f"{base_topic}/config"
        image_topic = f"{base_topic}/image"

        config = {
            "name": get_entity_name_for_discovery(
                self.name, self.device_info, self.has_entity_name
            ),
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "topic": image_topic,  # Required for MQTT camera
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Check if entity is disabled by default
        if not self.entity_registry_enabled_default:
            config["enabled_by_default"] = False

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)

        # Publish empty image initially (entity will update with actual image)
        mqtt.publish(image_topic, "", qos=0, retain=True)

    def _mqtt_publish(self) -> None:
        """Publish camera image to MQTT.

        Fetches the current camera image via async_camera_image() and publishes
        it to the image_topic as raw bytes, as required by the MQTT camera integration.
        """
        if not self.entity_id:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        # Skip streaming cameras - they don't work well over MQTT
        if self.is_streaming or isinstance(self, MjpegCamera):
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        image_topic = f"{base_topic}/image"

        # Schedule async image fetch and publish
        async def _publish_image():
            try:
                image_bytes = await self.async_camera_image()
                if image_bytes:
                    mqtt.publish(image_topic, image_bytes, qos=0, retain=True)
            except Exception:
                # Silently ignore errors - camera may not have image available yet
                pass

        # Schedule the async task
        if hasattr(self.hass, "async_add_job"):
            self.hass.async_add_job(_publish_image)


class MjpegCamera(Camera):
    """MJPEG streaming camera entity."""

    _attr_is_streaming: bool = True

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the camera."""
        super().__init__()
        self._attr_name = kwargs.get("name")
        self._attr_unique_id = kwargs.get("unique_id")
        self._mjpeg_url = kwargs.get("mjpeg_url")
        self._still_image_url = kwargs.get("still_image_url")
