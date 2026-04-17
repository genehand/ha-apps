"""Camera platform shim.

Provides Camera entity base class for compatibility.
"""

from typing import Any, Optional

from ..entity import Entity, EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

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

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass.

        Auto-detect and register with coordinator for entities that have a
        coordinator attribute but don't inherit from CoordinatorEntity.
        This fixes integrations like moonraker where PreviewCamera only
        inherits from Camera but needs coordinator updates.
        """
        await super().async_added_to_hass()

        # Check if this entity has a coordinator but isn't CoordinatorEntity
        coordinator = getattr(self, "coordinator", None)
        if coordinator is None:
            return

        # Check if this entity is already a CoordinatorEntity (skip if so)
        # We detect this by checking if _handle_coordinator_update is overridden
        # in a parent class that inherits from CoordinatorEntity
        if hasattr(self, "_handle_coordinator_update"):
            # Check if it's the CoordinatorEntity's method
            import inspect

            mro = type(self).__mro__
            for cls in mro:
                if cls.__name__ == "CoordinatorEntity":
                    # Already a CoordinatorEntity, skip
                    _LOGGER.debug(
                        f"Camera {self.entity_id} is already CoordinatorEntity, "
                        "skipping auto-registration"
                    )
                    return

        # Check if coordinator has async_add_listener
        if not hasattr(coordinator, "async_add_listener"):
            _LOGGER.debug(
                f"Camera {self.entity_id} coordinator has no async_add_listener"
            )
            return

        # Register for coordinator updates
        _LOGGER.debug(
            f"Camera {self.entity_id} auto-registering with coordinator for updates"
        )

        def _on_coordinator_update():
            """Handle coordinator update."""
            _LOGGER.debug(f"Camera {self.entity_id} handling coordinator update")
            self.async_write_ha_state()

        remove_listener = coordinator.async_add_listener(_on_coordinator_update)
        self.async_on_remove(remove_listener)

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery config for camera.

        Only publishes for non-streaming cameras (static images like thumbnails).
        Streaming cameras (MjpegCamera) are skipped as they don't work well over MQTT.
        """
        # Skip streaming cameras - only publish thumbnail/static cameras
        if self.is_streaming or isinstance(self, MjpegCamera):
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

    async def _cleanup_mqtt(self) -> None:
        """Clean up MQTT topics for camera entities.

        Cameras don't use state_topic - they use image_topic instead.
        Override base class to skip state_topic cleanup.
        """
        _LOGGER.debug(f"_cleanup_mqtt called for camera {self.entity_id}")

        if not self.hass or not self.entity_id:
            _LOGGER.debug(
                f"  Skipping cleanup: hass={self.hass is not None}, entity_id={self.entity_id}"
            )
            return

        if not hasattr(self.hass, "_mqtt_client"):
            _LOGGER.debug(f"  Skipping cleanup: no _mqtt_client attribute")
            return

        mqtt = self.hass._mqtt_client

        from ..entity import get_mqtt_entity_id

        entity_id_clean = get_mqtt_entity_id(self.entity_id)
        platform = "camera"

        # Delete discovery config topic (empty payload with retain=True removes it)
        discovery_topic = f"homeassistant/{platform}/{entity_id_clean}/config"
        mqtt.publish(discovery_topic, "", qos=0, retain=True)
        _LOGGER.debug(f"  Published empty payload to {discovery_topic}")

        # Clear image topic (cameras use image_topic, not state_topic)
        image_topic = f"homeassistant/{platform}/{entity_id_clean}/image"
        mqtt.publish(image_topic, "", qos=0, retain=True)
        _LOGGER.debug(f"  Published empty payload to {image_topic}")

        _LOGGER.debug(f"Cleaned up MQTT topics for camera {self.entity_id}")


class MjpegCamera(Camera):
    """MJPEG streaming camera entity."""

    _attr_is_streaming: bool = True

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the camera."""
        # Handle device_info separately before calling super().__init__()
        # since Entity.__init__() doesn't accept kwargs
        if "device_info" in kwargs:
            self._attr_device_info = kwargs.pop("device_info")
        super().__init__()
        self._attr_name = kwargs.get("name")
        self._attr_unique_id = kwargs.get("unique_id")
        self._mjpeg_url = kwargs.get("mjpeg_url")
        self._still_image_url = kwargs.get("still_image_url")
