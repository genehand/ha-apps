"""Humidifier platform shim.

Provides compatibility for homeassistant.components.humidifier imports.
"""

from dataclasses import field
from enum import Enum
from typing import Any, List, Optional

from ..entity import (
    Entity,
    EntityDescription,
)
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "humidifier"


class HumidifierDeviceClass(Enum):
    """Device class for humidifiers."""

    DEHUMIDIFIER = "dehumidifier"
    HUMIDIFIER = "humidifier"


class HumidifierEntityFeature:
    """Supported features of the humidifier entity."""

    MODES = 1


class HumidifierEntity(Entity):
    """Base class for humidifier entities."""

    _attr_available_modes: List[str] = []
    _attr_current_humidity: Optional[int] = None
    _attr_device_class: Optional[HumidifierDeviceClass] = None
    _attr_is_on: Optional[bool] = None
    _attr_max_humidity: int = 100
    _attr_min_humidity: int = 0
    _attr_mode: Optional[str] = None
    _attr_supported_features: int = 0
    _attr_target_humidity: Optional[int] = None

    @property
    def available_modes(self) -> List[str]:
        """Return the list of available modes."""
        return self._attr_available_modes

    @property
    def current_humidity(self) -> Optional[int]:
        """Return the current humidity."""
        return self._attr_current_humidity

    @property
    def device_class(self) -> Optional[HumidifierDeviceClass]:
        """Return the device class."""
        return self._attr_device_class

    @property
    def is_on(self) -> Optional[bool]:
        """Return True if the humidifier is on."""
        return self._attr_is_on

    @property
    def max_humidity(self) -> int:
        """Return the maximum humidity."""
        return self._attr_max_humidity

    @property
    def min_humidity(self) -> int:
        """Return the minimum humidity."""
        return self._attr_min_humidity

    @property
    def mode(self) -> Optional[str]:
        """Return the current mode."""
        return self._attr_mode

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        return self._attr_supported_features

    @property
    def target_humidity(self) -> Optional[int]:
        """Return the target humidity."""
        return self._attr_target_humidity

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the humidifier on."""
        raise NotImplementedError()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the humidifier off."""
        raise NotImplementedError()

    async def async_set_humidity(self, humidity: int) -> None:
        """Set the target humidity."""
        raise NotImplementedError()

    async def async_set_mode(self, mode: str) -> None:
        """Set the mode."""
        raise NotImplementedError()


class HumidifierEntityDescription(EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True):
    """Describe a humidifier entity."""

    device_class: Optional[HumidifierDeviceClass] = None
    max_humidity: int = 100
    min_humidity: int = 0
    modes: List[str] = field(default_factory=list)
    supported_features: int = 0


# Constants
MODE_NORMAL = "normal"
MODE_ECO = "eco"
MODE_AWAY = "away"
MODE_BOOST = "boost"
MODE_COMFORT = "comfort"
MODE_HOME = "home"
MODE_SLEEP = "sleep"
MODE_AUTO = "auto"
MODE_BABY = "baby"
