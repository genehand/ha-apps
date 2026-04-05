"""Siren platform shim.

Provides compatibility for homeassistant.components.siren imports.
"""

from enum import StrEnum
from typing import Any, Optional

from ..entity import Entity, EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed

DOMAIN = "siren"


class SirenEntityFeature:
    """Supported features of the siren entity."""

    TURN_ON = 1
    TURN_OFF = 2
    TONES = 4
    VOLUME_SET = 8
    DURATION = 16


class SirenEntity(Entity):
    """Base class for siren entities."""

    _attr_is_on: bool = False
    _attr_supported_features: int = 0

    @property
    def is_on(self) -> bool:
        """Return True if the siren is on."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the siren on."""
        raise NotImplementedError

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the siren off."""
        raise NotImplementedError


class SirenEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes siren entities."""

    available_tones: Optional[list] = None
    default_duration: Optional[int] = None
    default_tone: Optional[str] = None
