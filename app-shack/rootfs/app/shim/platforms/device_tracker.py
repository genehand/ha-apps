"""Device tracker platform shim for Home Assistant.

Provides base classes for device tracker entities.
"""

from ..entity import Entity
from ..logging import get_logger

_LOGGER = get_logger(__name__)


# Create config_entry submodule
class _ConfigEntryModule:
    """Stub module for homeassistant.components.device_tracker.config_entry"""

    pass


config_entry = _ConfigEntryModule()
config_entry.TrackerEntity = None  # Will be set below


# Create const submodule
class _ConstModule:
    """Stub module for homeassistant.components.device_tracker.const"""

    pass


const = _ConstModule()
const.SourceType = type(
    "SourceType",
    (),
    {
        "GPS": "gps",
        "ROUTER": "router",
        "BLUETOOTH": "bluetooth",
        "BLUETOOTH_LE": "bluetooth_le",
    },
)()


class DeviceTrackerEntity(Entity):
    """Base class for device tracker entities."""

    _attr_latitude: float | None = None
    _attr_longitude: float | None = None
    _attr_location_name: str | None = None
    _attr_location_accuracy: int = 0
    _attr_battery_level: int | None = None
    _attr_source_type: str = "gps"

    @property
    def state(self):
        """Return the state of the device."""
        if self.location_name is not None:
            return self.location_name
        if self.latitude is not None and self.longitude is not None:
            return "home"
        return None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._attr_latitude

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._attr_longitude

    @property
    def location_name(self) -> str | None:
        """Return a location name for the current location of the device."""
        return self._attr_location_name

    @property
    def location_accuracy(self) -> int:
        """Return the gps accuracy of the device."""
        return self._attr_location_accuracy

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device."""
        return self._attr_battery_level

    @property
    def source_type(self) -> str:
        """Return the source type, eg gps or router, of the device."""
        return self._attr_source_type

    @property
    def state_attributes(self):
        """Return the device state attributes."""
        attr = {}
        if self.latitude is not None:
            attr["latitude"] = self.latitude
        if self.longitude is not None:
            attr["longitude"] = self.longitude
        if self.location_name is not None:
            attr["location_name"] = self.location_name
        if self.location_accuracy is not None:
            attr["gps_accuracy"] = self.location_accuracy
        if self.battery_level is not None:
            attr["battery"] = self.battery_level
        return attr


class TrackerEntity(Entity):
    """Base class for a tracker entity."""

    _attr_latitude: float | None = None
    _attr_longitude: float | None = None
    _attr_location_accuracy: int = 0
    _attr_location_name: str | None = None

    @property
    def state(self):
        """Return the state of the device."""
        if self.location_name is not None:
            return self.location_name
        if self.latitude is not None and self.longitude is not None:
            return "home"
        return None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._attr_latitude

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._attr_longitude

    @property
    def location_name(self) -> str | None:
        """Return a location name for the current location of the device."""
        return self._attr_location_name

    @property
    def location_accuracy(self) -> int:
        """Return the gps accuracy of the device."""
        return self._attr_location_accuracy

    @property
    def state_attributes(self):
        """Return the device state attributes."""
        attr = {}
        if self.latitude is not None:
            attr["latitude"] = self.latitude
        if self.longitude is not None:
            attr["longitude"] = self.longitude
        if self.location_accuracy is not None:
            attr["gps_accuracy"] = self.location_accuracy
        return attr


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up device tracker platform."""
    _LOGGER.warning("device_tracker platform setup not implemented")
    return True


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up device tracker platform."""
    _LOGGER.warning("device_tracker platform setup not implemented")
    return True


# Set up config_entry exports
config_entry.TrackerEntity = TrackerEntity
