"""Remote platform shim.

Provides compatibility for homeassistant.components.remote imports.
"""

from typing import Any, Optional, List

from ..entity import Entity, EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed

DOMAIN = "remote"

# Attribute constants
ATTR_ACTIVITY = "activity"
ATTR_ACTIVITY_LIST = "activity_list"
ATTR_COMMAND = "command"
ATTR_COMMAND_TYPE = "command_type"
ATTR_DEVICE = "device"
ATTR_DELAY_SECS = "delay_secs"
ATTR_NUM_REPEATS = "num_repeats"
ATTR_HOLD_SECS = "hold_secs"
ATTR_TIMEOUT = "timeout"


class RemoteEntityFeature:
    """Supported features of the remote entity."""

    LEARN_COMMAND = 1
    DELETE_COMMAND = 2
    ACTIVITY = 4


class RemoteEntity(Entity):
    """Base class for remote entities."""

    _attr_is_on: bool = False
    _attr_supported_features: int = 0
    _attr_activity_list: Optional[List[str]] = None
    _attr_current_activity: Optional[str] = None

    @property
    def is_on(self) -> bool:
        """Return True if the remote is on."""
        return self._attr_is_on

    @property
    def activity_list(self) -> Optional[List[str]]:
        """Return the list of activities."""
        return self._attr_activity_list

    @property
    def current_activity(self) -> Optional[str]:
        """Return the current activity."""
        return self._attr_current_activity

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the remote on."""
        raise NotImplementedError

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the remote off."""
        raise NotImplementedError

    async def async_send_command(self, command: List[str], **kwargs: Any) -> None:
        """Send a command to the remote."""
        raise NotImplementedError

    async def async_learn_command(self, **kwargs: Any) -> None:
        """Learn a command to the remote."""
        raise NotImplementedError

    async def async_delete_command(self, **kwargs: Any) -> None:
        """Delete a command from the remote."""
        raise NotImplementedError


class RemoteEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes remote entities."""

    activity_list: Optional[List[str]] = None
