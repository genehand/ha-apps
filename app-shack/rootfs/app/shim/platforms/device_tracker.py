"""Device tracker platform shim for Home Assistant.

Provides base classes for device tracker entities.
"""

from typing import Optional

from ..entity import Entity, EntityAttribute
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "device_tracker"


# Create config_entry submodule
class _ConfigEntryModule:
    """Stub module for homeassistant.components.device_tracker.config_entry"""

    pass


from ..entity import EntityDescription


class TrackerEntityDescription(EntityDescription):
    """Description for tracker entities."""

    pass


config_entry = _ConfigEntryModule()
config_entry.TrackerEntity = None  # Will be set below
config_entry.TrackerEntityDescription = TrackerEntityDescription


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

    @EntityAttribute
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

    @EntityAttribute
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


class ScannerEntityDescription(EntityDescription):
    """Description for scanner entities."""

    pass


class ScannerEntity(DeviceTrackerEntity):
    """Base class for a tracked device on a scanned network (HA 2026.11).

    A scanner tracker is a client seen by a router or another network scanner.
    Since HA 2026.11 it handles devices like any other entity:

    * ``device_info`` is no longer final, so an integration decides which device
      the tracker belongs to (the MAC of the tracked client as a connection is
      the recommended shape).
    * It is **disabled by default**. Override
      ``_attr_entity_registry_enabled_default`` when tracking is the purpose of
      the device.
    * A tracker without device info still gets the deprecated default device
      (named after the hostname/MAC, matched by connection) until HA 2027.11; an
      empty ``DeviceInfo()`` opts out.
    """

    _attr_hostname: Optional[str] = None
    _attr_ip_address: Optional[str] = None
    _attr_mac_address: Optional[str] = None
    _attr_is_connected: Optional[bool] = None
    # Most scanned network clients are not worth tracking.
    _attr_entity_registry_enabled_default = False
    _attr_source_type: str = "router"

    @EntityAttribute
    def hostname(self) -> Optional[str]:
        """Return the hostname of the device."""
        return self._attr_hostname

    @EntityAttribute
    def ip_address(self) -> Optional[str]:
        """Return the primary IP address of the device."""
        return self._attr_ip_address

    @EntityAttribute
    def mac_address(self) -> Optional[str]:
        """Return the MAC address of the device."""
        return self._attr_mac_address

    @EntityAttribute
    def is_connected(self) -> Optional[bool]:
        """Return whether the device is connected to the network."""
        return self._attr_is_connected

    @property
    def unique_id(self) -> Optional[str]:
        """Return the MAC address as the unique id."""
        return self.mac_address or super().unique_id

    @EntityAttribute
    def source_type(self) -> str:
        """Return the source type of the device."""
        return self._attr_source_type or "router"

    @EntityAttribute
    def state(self):
        """Return ``home``/``not_home`` from connectivity."""
        if self.is_connected is None:
            return None
        return "home" if self.is_connected else "not_home"

    @EntityAttribute
    def device_info(self):
        """Return the device info, with the deprecated MAC default.

        Only applied when the integration set no ``device_info`` at all; an empty
        ``DeviceInfo()`` means "no device" and is returned as-is.
        """
        if self._attr_device_info is not None:
            return self._attr_device_info
        mac = self.mac_address
        if not mac:
            return None
        connections = {("mac", mac.lower())}
        name = self.hostname
        try:
            from homeassistant.helpers import device_registry as dr

            existing = dr.async_get(getattr(self, "hass", None)).async_get_device(
                connections=connections
            )
        except Exception:  # noqa: BLE001 - no registry means "new device"
            existing = None
        if existing is not None:
            # Do not rename a device another entity already named.
            name = None
            identifiers = getattr(existing, "identifiers", None)
            if identifiers:
                return {"identifiers": set(identifiers), "connections": connections}
        return {"connections": connections, "name": name or mac}

    @EntityAttribute
    def state_attributes(self) -> dict:
        """Return the device state attributes (ip/mac/hostname)."""
        return {
            "source_type": self.source_type,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "hostname": self.hostname,
        }


config_entry.ScannerEntity = ScannerEntity
config_entry.ScannerEntityDescription = ScannerEntityDescription

__all__ = [
    "DeviceTrackerEntity",
    "ScannerEntity",
    "ScannerEntityDescription",
    "TrackerEntity",
    "TrackerEntityDescription",
    "config_entry",
    "const",
]
