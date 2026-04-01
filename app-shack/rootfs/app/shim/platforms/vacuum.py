"""Vacuum component shim.

Provides compatibility for homeassistant.components.vacuum imports.
"""

from enum import Enum
from typing import Any, List, Optional

from ..entity import (
    Entity,
    EntityDescription,
)
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "vacuum"


class VacuumActivity(Enum):
    """Vacuum activity states."""

    CLEANING = "cleaning"
    DOCKED = "docked"
    PAUSED = "paused"
    IDLE = "idle"
    RETURNING = "returning"
    ERROR = "error"


class VacuumEntityFeature:
    """Supported features of the vacuum entity."""

    TURN_ON = 1
    TURN_OFF = 2
    PAUSE = 4
    STOP = 8
    RETURN_HOME = 16
    FAN_SPEED = 32
    BATTERY = 64
    STATUS = 128
    SEND_COMMAND = 256
    LOCATE = 512
    CLEAN_SPOT = 1024
    MAP = 2048
    STATE = 4096
    START = 8192


class StateVacuumEntity(Entity):
    """Base class for state-based vacuum entities."""

    _attr_activity: Optional[VacuumActivity] = None
    _attr_battery_level: Optional[int] = None
    _attr_fan_speed: Optional[str] = None
    _attr_fan_speed_list: List[str] = []
    _attr_state: Optional[str] = None
    _attr_supported_features: int = 0

    @property
    def activity(self) -> Optional[VacuumActivity]:
        """Return the current vacuum activity."""
        return self._attr_activity

    @property
    def battery_level(self) -> Optional[int]:
        """Return the battery level."""
        return self._attr_battery_level

    @property
    def fan_speed(self) -> Optional[str]:
        """Return the fan speed."""
        return self._attr_fan_speed

    @property
    def fan_speed_list(self) -> List[str]:
        """Return the list of available fan speeds."""
        return self._attr_fan_speed_list

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        return self._attr_supported_features

    async def async_start(self) -> None:
        """Start the vacuum."""
        raise NotImplementedError()

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum."""
        raise NotImplementedError()

    async def async_pause(self) -> None:
        """Pause the vacuum."""
        raise NotImplementedError()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to its dock."""
        raise NotImplementedError()

    async def async_clean_spot(self, **kwargs: Any) -> None:
        """Perform a spot clean."""
        raise NotImplementedError()

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate the vacuum."""
        raise NotImplementedError()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the fan speed."""
        raise NotImplementedError()

    async def async_send_command(
        self, command: str, params: Optional[dict] = None, **kwargs: Any
    ) -> None:
        """Send a command to the vacuum."""
        raise NotImplementedError()


class VacuumEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a vacuum entity."""

    fan_speed_list: List[str] = []
    supported_features: int = 0


# Legacy vacuum entity for backwards compatibility
class VacuumEntity(Entity):
    """Base class for legacy vacuum entities."""

    _attr_is_on: Optional[bool] = None
    _attr_status: Optional[str] = None
    _attr_battery_level: Optional[int] = None
    _attr_fan_speed: Optional[str] = None
    _attr_fan_speed_list: List[str] = []
    _attr_supported_features: int = 0

    @property
    def status(self) -> Optional[str]:
        """Return the vacuum status."""
        return self._attr_status

    @property
    def battery_level(self) -> Optional[int]:
        """Return the battery level."""
        return self._attr_battery_level

    @property
    def fan_speed(self) -> Optional[str]:
        """Return the fan speed."""
        return self._attr_fan_speed

    @property
    def fan_speed_list(self) -> List[str]:
        """Return the list of available fan speeds."""
        return self._attr_fan_speed_list

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        return self._attr_supported_features

    async def async_start(self) -> None:
        """Start the vacuum."""
        raise NotImplementedError()

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum."""
        raise NotImplementedError()

    async def async_pause(self) -> None:
        """Pause the vacuum."""
        raise NotImplementedError()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to its dock."""
        raise NotImplementedError()

    async def async_clean_spot(self, **kwargs: Any) -> None:
        """Perform a spot clean."""
        raise NotImplementedError()

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate the vacuum."""
        raise NotImplementedError()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the fan speed."""
        raise NotImplementedError()

    async def async_send_command(
        self, command: str, params: Optional[dict] = None, **kwargs: Any
    ) -> None:
        """Send a command to the vacuum."""
        raise NotImplementedError()


# Constants for legacy support
STATE_CLEANING = "cleaning"
STATE_DOCKED = "docked"
STATE_PAUSED = "paused"
STATE_IDLE = "idle"
STATE_RETURNING = "returning"
STATE_ERROR = "error"

# Service constants
SERVICE_START = "start"
SERVICE_STOP = "stop"
SERVICE_PAUSE = "pause"
SERVICE_RETURN_TO_BASE = "return_to_base"
SERVICE_CLEAN_SPOT = "clean_spot"
SERVICE_LOCATE = "locate"
SERVICE_SET_FAN_SPEED = "set_fan_speed"
SERVICE_SEND_COMMAND = "send_command"
