"""Sensor platform shim.

Bridges SensorEntity to MQTT sensor discovery.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, Final, Optional

from shim.entity import (
    Entity,
    EntityDescription,
    build_mqtt_device_config,
    format_device_identifiers,
    get_device_info_attr,
    get_entity_name_for_discovery,
    get_mqtt_safe_unique_id,
)
from shim.frozen_dataclass_compat import FrozenOrThawed
from shim.logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN: Final = "sensor"

CONF_STATE_CLASS: Final = "state_class"

ATTR_LAST_RESET: Final = "last_reset"
ATTR_STATE_CLASS: Final = "state_class"
ATTR_OPTIONS: Final = "options"


class SensorDeviceClass(StrEnum):
    """Device class for sensors."""

    # Non-numerical device classes
    DATE = "date"
    ENUM = "enum"
    TIMESTAMP = "timestamp"

    # Numerical device classes
    ABSOLUTE_HUMIDITY = "absolute_humidity"
    APPARENT_POWER = "apparent_power"
    AQI = "aqi"
    AREA = "area"
    ATMOSPHERIC_PRESSURE = "atmospheric_pressure"
    BATTERY = "battery"
    BLOOD_GLUCOSE_CONCENTRATION = "blood_glucose_concentration"
    CO = "carbon_monoxide"
    CO2 = "carbon_dioxide"
    CONDUCTIVITY = "conductivity"
    CURRENT = "current"
    DATA_RATE = "data_rate"
    DATA_SIZE = "data_size"
    DISTANCE = "distance"
    DURATION = "duration"
    ENERGY = "energy"
    ENERGY_DISTANCE = "energy_distance"
    ENERGY_STORAGE = "energy_storage"
    FREQUENCY = "frequency"
    GAS = "gas"
    HUMIDITY = "humidity"
    ILLUMINANCE = "illuminance"
    IRRADIANCE = "irradiance"
    MOISTURE = "moisture"
    MONETARY = "monetary"
    NITROGEN_DIOXIDE = "nitrogen_dioxide"
    NITROGEN_MONOXIDE = "nitrogen_monoxide"
    NITROUS_OXIDE = "nitrous_oxide"
    NO2 = "nitrogen_dioxide"
    OZONE = "ozone"
    PH = "ph"
    PM = "pm"
    PM1 = "pm1"
    PM10 = "pm10"
    PM25 = "pm25"
    PM4 = "pm4"
    POWER_FACTOR = "power_factor"
    POWER = "power"
    PRECIPITATION = "precipitation"
    PRECIPITATION_INTENSITY = "precipitation_intensity"
    PRESSURE = "pressure"
    REACTIVE_ENERGY = "reactive_energy"
    REACTIVE_POWER = "reactive_power"
    SIGNAL_STRENGTH = "signal_strength"
    SOUND_PRESSURE = "sound_pressure"
    SPEED = "speed"
    SULPHUR_DIOXIDE = "sulphur_dioxide"
    TEMPERATURE = "temperature"
    TEMPERATURE_DELTA = "temperature_delta"
    VOLATILE_ORGANIC_COMPOUNDS = "volatile_organic_compounds"
    VOLATILE_ORGANIC_COMPOUNDS_PARTS = "volatile_organic_compounds_parts"
    VOLTAGE = "voltage"
    VOLUME = "volume"
    VOLUME_STORAGE = "volume_storage"
    VOLUME_FLOW_RATE = "volume_flow_rate"
    WATER = "water"
    WEIGHT = "weight"
    WIND_DIRECTION = "wind_direction"
    WIND_SPEED = "wind_speed"


class SensorStateClass(StrEnum):
    """State class for sensors."""

    MEASUREMENT = "measurement"
    MEASUREMENT_ANGLE = "measurement_angle"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


# Backwards compatibility
DEVICE_CLASSES: Final[list[str]] = [cls.value for cls in SensorDeviceClass]
STATE_CLASSES: Final[list[str]] = [cls.value for cls in SensorStateClass]
NON_NUMERIC_DEVICE_CLASSES: Final[set[str]] = {
    SensorDeviceClass.DATE,
    SensorDeviceClass.ENUM,
    SensorDeviceClass.TIMESTAMP,
}

# Legacy state class constants for backwards compatibility
STATE_CLASS_MEASUREMENT = "measurement"
STATE_CLASS_TOTAL = "total"
STATE_CLASS_TOTAL_INCREASING = "total_increasing"


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
        # Check for directly assigned state_class attribute first
        if "state_class" in self.__dict__:
            return self.__dict__["state_class"]
        # Check entity_description for state_class
        if self.entity_description is not None:
            return self.entity_description.state_class
        return self._attr_state_class

    @property
    def icon(self) -> Optional[str]:
        """Return the icon to use in the frontend."""
        # Check for directly assigned icon attribute first
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
        _LOGGER.debug(f"_mqtt_publish called for {self.entity_id}")

        if not hasattr(self.hass, "_mqtt_client"):
            _LOGGER.debug(f"  Skipping: hass has no _mqtt_client attribute")
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            _LOGGER.debug(f"  Skipping: MQTT not connected")
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            _LOGGER.debug(f"  Skipping: no base topic for {self.entity_id}")
            return

        # Publish state
        state_topic = f"{base_topic}/state"
        state = self.state
        native_val = self.native_value
        _LOGGER.debug(
            f"  Publishing state for {self.entity_id}: "
            f"state={state!r}, native_value={native_val!r}, "
            f"state_class={self.state_class}"
        )

        if state is not None and state != "None":
            _LOGGER.debug(f"  Publishing to {state_topic}: {state}")
            mqtt.publish(state_topic, state, qos=0, retain=True)
        elif self.state_class:
            # For sensors with state_class (numeric), publish empty string instead of 'unavailable'
            # to avoid HA warnings about non-numeric values
            _LOGGER.debug(
                f"  Publishing empty string to {state_topic} (has state_class)"
            )
            mqtt.publish(state_topic, "", qos=0, retain=True)
        else:
            _LOGGER.debug(f"  Publishing 'unavailable' to {state_topic}")
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
        entity_name = get_entity_name_for_discovery(
            self.name, self.device_info, self.has_entity_name
        )
        config = {
            "name": entity_name,
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
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
