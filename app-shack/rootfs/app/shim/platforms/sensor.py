"""Sensor platform shim.

Bridges SensorEntity to MQTT sensor discovery.
"""

from enum import StrEnum
from typing import Any, Dict, Optional
from datetime import datetime

from ..entity import (
    Entity,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "sensor"

# State classes (for backwards compatibility)
STATE_CLASS_MEASUREMENT = "measurement"
STATE_CLASS_TOTAL = "total"
STATE_CLASS_TOTAL_INCREASING = "total_increasing"


class SensorDeviceClass(StrEnum):
    """Device class for sensors."""

    AQI = "aqi"
    BATTERY = "battery"
    CO = "carbon_monoxide"
    CO2 = "carbon_dioxide"
    CURRENT = "current"
    ENERGY = "energy"
    ENUM = "enum"
    HUMIDITY = "humidity"
    ILLUMINANCE = "illuminance"
    PM = "pm"
    PM25 = "pm25"
    PM10 = "pm10"
    POWER = "power"
    SIGNAL_STRENGTH = "signal_strength"
    TEMPERATURE = "temperature"
    NO2 = "nitrogen_dioxide"
    VOLATILE_ORGANIC_COMPOUNDS = "volatile_organic_compounds"
    VOLTAGE = "voltage"


class SensorStateClass(StrEnum):
    """State class for sensors."""

    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class RestoreSensor:
    """Mixin class for restoring sensor state after restart.

    This is a stub implementation - state restoration is not implemented
    in the shim but the class is provided for compatibility.
    """

    @property
    def extra_restore_state_data(self):
        """Return extra state data for restoration."""
        return None

    async def async_get_last_sensor_data(self):
        """Return last sensor data from restore."""
        return None

    async def async_get_last_state(self):
        """Return last state from restore."""
        return None


from ..entity import EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed


class SensorEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes sensor entities."""

    state_class: str | None = None
    native_unit_of_measurement: str | None = None
    options: list[str] | None = None


class SensorEntity(Entity):
    """Base class for sensor entities."""

    _attr_native_value = None
    _attr_native_unit_of_measurement: Optional[str] = None
    _attr_state_class: Optional[str] = None
    _attr_last_reset: Optional[datetime] = None
    entity_description: Optional[SensorEntityDescription] = None

    @property
    def name(self) -> Optional[str]:
        """Return the name of the entity."""
        if hasattr(self, "_attr_name") and self._attr_name is not None:
            return self._attr_name
        if hasattr(self, "entity_description") and self.entity_description is not None:
            return self.entity_description.name
        # Fall back to parent class name property (e.g., DysonEntity)
        return super().name

    @property
    def native_value(self):
        """Return the value reported by the sensor."""
        return self._attr_native_value

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement of the sensor."""
        return self._attr_native_unit_of_measurement

    @property
    def state_class(self) -> Optional[str]:
        """Return the state class of the sensor."""
        # Check for directly assigned state_class attribute first (integrations may set entity.state_class = "...")
        if "state_class" in self.__dict__:
            return self.__dict__["state_class"]
        # Check entity_description for state_class
        if self.entity_description is not None:
            return self.entity_description.state_class
        return self._attr_state_class

    @property
    def icon(self) -> Optional[str]:
        """Return the icon to use in the frontend."""
        # Check for directly assigned icon attribute first (integrations may set entity.icon = "...")
        if "icon" in self.__dict__:
            return self.__dict__["icon"]
        # Check entity_description for icon
        if self.entity_description is not None:
            return self.entity_description.icon
        return self._attr_icon

    @property
    def last_reset(self) -> Optional[datetime]:
        """Return the time when the sensor was last reset."""
        return self._attr_last_reset

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.native_value is None:
            return None
        return str(self.native_value)

    @property
    def unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement of this entity."""
        return self.native_unit_of_measurement

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT."""
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Publish state
        state_topic = f"{base_topic}/state"
        state = self.state
        if state is not None and state != "None":
            mqtt.publish(state_topic, state, qos=0, retain=True)
        elif self.state_class:
            # For sensors with state_class (numeric), publish empty string instead of 'unavailable'
            # to avoid HA warnings about non-numeric values
            mqtt.publish(state_topic, "", qos=0, retain=True)
        else:
            mqtt.publish(state_topic, "unavailable", qos=0, retain=True)

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

        # Check if any discovery properties changed (icon, state_class, etc.)
        # This handles properties that weren't available at initial discovery time
        # We check actual properties (not _attr_*) to catch values from entity_description too
        self._check_and_publish_discovery_update(
            ["device_class", "state_class", "icon", "native_unit_of_measurement"]
        )

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery when added."""
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        discovery_topic = f"{base_topic}/config"

        # Build discovery config
        # Strip device name prefix from entity name if present
        entity_name = get_entity_name_for_discovery(self.name, self.device_info)
        config = {
            "name": entity_name,
            "unique_id": self.unique_id,
            "state_topic": f"{base_topic}/state",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.device_class:
            config["device_class"] = self.device_class

        if self.native_unit_of_measurement:
            config["unit_of_measurement"] = self.native_unit_of_measurement

        if self.state_class:
            config["state_class"] = self.state_class

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
