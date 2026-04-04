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


class MjpegCamera(Camera):
    """MJPEG camera entity."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the camera."""
        super().__init__()
        self._attr_name = kwargs.get("name")
        self._attr_unique_id = kwargs.get("unique_id")
        self._mjpeg_url = kwargs.get("mjpeg_url")
        self._still_image_url = kwargs.get("still_image_url")
