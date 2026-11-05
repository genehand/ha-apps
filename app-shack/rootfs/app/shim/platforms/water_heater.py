"""Water heater platform shim.

Provides compatibility for homeassistant.components.water_heater imports.
"""

from dataclasses import field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..entity import (
    Entity,
    EntityDescription,
    EntityAttribute,
    get_entity_name_for_discovery,
    get_mqtt_safe_unique_id,
)
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "water_heater"

# Default temperature constants
DEFAULT_MAX_TEMP = 60
DEFAULT_MIN_TEMP = 30

# State constants for operation modes
STATE_ECO = "eco"
STATE_ELECTRIC = "electric"
STATE_PERFORMANCE = "performance"
STATE_HIGH_DEMAND = "high_demand"
STATE_HEAT_PUMP = "heat_pump"
STATE_GAS = "gas"
STATE_OFF = "off"
STATE_ON = "on"
STATE_IDLE = "idle"

# Attribute constants
ATTR_TEMPERATURE = "temperature"
ATTR_TARGET_TEMP_LOW = "target_temp_low"
ATTR_TARGET_TEMP_HIGH = "target_temp_high"
ATTR_OPERATION_MODE = "operation_mode"
ATTR_AWAY_MODE = "away_mode"


class WaterHeaterEntityFeature:
    """Supported features of the water heater entity."""

    TARGET_TEMPERATURE = 1
    OPERATION_MODE = 2
    AWAY_MODE = 4


class WaterHeaterEntity(Entity):
    """Base class for water heater entities."""

    _attr_available_modes: List[str] = []
    _attr_current_operation: Optional[str] = None
    _attr_current_temperature: Optional[float] = None
    _attr_device_class: Optional[str] = None
    _attr_is_away_mode_on: Optional[bool] = None
    _attr_is_on: Optional[bool] = None
    _attr_max_temp: float = 100.0
    _attr_min_temp: float = 0.0
    _attr_mode: Optional[str] = None
    _attr_name: Optional[str] = None
    _attr_operation_list: List[str] = []
    _attr_supported_features: int = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.AWAY_MODE
    )  # Default: 1 | 2 | 4 = 7
    _attr_target_temperature: Optional[float] = None
    _attr_target_temperature_high: Optional[float] = None
    _attr_target_temperature_low: Optional[float] = None
    _attr_target_temperature_step: float = 1.0
    _attr_temperature_unit: str = "°F"  # Default to Fahrenheit for water heaters
    # HA 2026.11: native_* names for temperatures in the device's unit.
    _attr_native_current_temperature: Optional[float] = None
    _attr_native_target_temperature: Optional[float] = None
    _attr_native_target_temperature_high: Optional[float] = None
    _attr_native_target_temperature_low: Optional[float] = None
    _attr_native_temperature_unit: Optional[str] = None

    @property
    def native_temperature_unit(self) -> Optional[str]:
        """Return the unit the temperatures are specified in (HA 2026.11)."""
        return self._attr_native_temperature_unit or self._attr_temperature_unit

    @property
    def temperature_unit(self) -> str:
        """Return the unit of the temperatures in the state (HA 2026.11).

        Set by the base class from the user's unit system upstream; the shim has
        no conversion, so it reports the native unit and falls back to the
        configured unit system.
        """
        native = self.native_temperature_unit
        if native:
            return native
        try:
            return self.hass.config.units.temperature_unit
        except Exception:  # noqa: BLE001 - no hass/config in unit tests
            return "°F"

    @property
    def current_operation(self) -> Optional[str]:
        """Return current operation mode."""
        return self._attr_current_operation

    @EntityAttribute
    def state(self) -> Optional[str]:
        """Return the state of the entity - the current operation mode."""
        return self.current_operation

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return self._attr_current_temperature

    @property
    def native_current_temperature(self) -> Optional[float]:
        """Return the current temperature in the device's unit (HA 2026.11)."""
        value = self._attr_native_current_temperature
        if value is not None:
            return value
        return self.current_temperature

    @property
    def is_away_mode_on(self) -> Optional[bool]:
        """Return True if away mode is on."""
        return self._attr_is_away_mode_on

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._attr_max_temp

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._attr_min_temp

    @property
    def operation_list(self) -> List[str]:
        """Return the list of available operation modes."""
        return self._attr_operation_list

    @EntityAttribute
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return self._attr_supported_features

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        return self._attr_target_temperature

    @property
    def native_target_temperature(self) -> Optional[float]:
        """Return the target temperature in the device's unit (HA 2026.11)."""
        value = self._attr_native_target_temperature
        if value is not None:
            return value
        return self.target_temperature

    @property
    def target_temperature_high(self) -> Optional[float]:
        """Return the high target temperature."""
        return self._attr_target_temperature_high

    @property
    def native_target_temperature_high(self) -> Optional[float]:
        """Return the high target temperature in the device's unit (2026.11)."""
        value = self._attr_native_target_temperature_high
        if value is not None:
            return value
        return self.target_temperature_high

    @property
    def target_temperature_low(self) -> Optional[float]:
        """Return the low target temperature."""
        return self._attr_target_temperature_low

    @property
    def native_target_temperature_low(self) -> Optional[float]:
        """Return the low target temperature in the device's unit (2026.11)."""
        value = self._attr_native_target_temperature_low
        if value is not None:
            return value
        return self.target_temperature_low

    @property
    def state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes, including ``temperature_unit``."""
        attributes: Dict[str, Any] = dict(self.extra_state_attributes or {})
        if self.temperature_unit is not None:
            attributes["temperature_unit"] = self.temperature_unit
        return attributes

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step."""
        return self._attr_target_temperature_step

    async def _publish_mqtt_discovery(self) -> None:
        """Publish water heater state to MQTT."""
        from ..entity import (
            get_entity_name_for_discovery,
            get_mqtt_safe_unique_id,
        )

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

        discovery_topic = f"{base_topic}/config"

        # Build discovery config - use default state topic for mode_state_topic
        config = {
            "name": get_entity_name_for_discovery(
                self.name, self.device_info, self.has_entity_name
            ),
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "mode_state_topic": f"{base_topic}/state",
        }

        if self.device_info:
            self._add_mqtt_device_config(config)

        if self.min_temp:
            config["min_temp"] = self.min_temp

        if self.max_temp:
            config["max_temp"] = self.max_temp

        # Use 'modes' for MQTT water heater - use seen states if available,
        # otherwise start with operation_list. Additional states are dynamically
        # added as they're reported via _mqtt_publish.
        if self.operation_list:
            if hasattr(self, "_mqtt_seen_states"):
                config["modes"] = list(self._mqtt_seen_states)
            else:
                config["modes"] = list(self.operation_list)

        # Add command topics for HA to enable features (required for supported_features)
        config["mode_command_topic"] = f"{base_topic}/mode_set"
        config["temperature_state_topic"] = f"{base_topic}/target_temperature"
        config["temperature_command_topic"] = f"{base_topic}/temperature_set"
        config["current_temperature_topic"] = f"{base_topic}/current_temperature"

        if self.icon:
            config["icon"] = self.icon

        # Add temperature unit (C or F) - default to Fahrenheit
        temp_unit = self.temperature_unit
        config["temperature_unit"] = "F" if "F" in temp_unit.upper() else "C"

        # Add enabled_by_default if entity is disabled by default
        if not self.entity_registry_enabled_default:
            config["enabled_by_default"] = False

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)

        # Base Entity class will publish state to state_topic (which is mode_state_topic)
        # We just need to publish the other temperature topics

        # Publish current temperature
        if self.native_current_temperature is not None:
            mqtt.publish(
                f"{base_topic}/current_temperature",
                str(self.native_current_temperature),
                qos=0,
                retain=True,
            )

        # Publish target temperature
        if self.native_target_temperature is not None:
            mqtt.publish(
                f"{base_topic}/target_temperature",
                str(self.native_target_temperature),
                qos=0,
                retain=True,
            )

        # Build water heater attributes
        # Note: inlet/outlet temperatures are handled by separate sensors, not included here
        attributes = {}

        if self.native_current_temperature is not None:
            attributes["current_temperature"] = self.native_current_temperature
        if self.native_target_temperature is not None:
            attributes["temperature"] = self.native_target_temperature
        if self.native_target_temperature_high is not None:
            attributes["target_temp_high"] = self.native_target_temperature_high
        if self.native_target_temperature_low is not None:
            attributes["target_temp_low"] = self.native_target_temperature_low
        if hasattr(self, "target_temperature_step") and self.target_temperature_step:
            attributes["target_temp_step"] = self.target_temperature_step
        if self.is_away_mode_on is not None:
            attributes["away_mode"] = "on" if self.is_away_mode_on else "off"
        if self.min_temp:
            attributes["min_temp"] = self.min_temp
        if self.max_temp:
            attributes["max_temp"] = self.max_temp

        # Publish attributes if present
        if attributes:
            attr_topic = f"{base_topic}/attributes"
            mqtt.publish(attr_topic, json.dumps(attributes), qos=0, retain=True)

        # Track new states and republish discovery if needed
        # This handles entities that report states not in their operation_list
        current_state = self._safe_state()
        if current_state is not None:
            # Track seen states
            if not hasattr(self, "_mqtt_seen_states"):
                self._mqtt_seen_states = (
                    set(self.operation_list) if self.operation_list else set()
                )

            if current_state not in self._mqtt_seen_states:
                # New state discovered - add it and republish discovery
                self._mqtt_seen_states.add(current_state)
                _LOGGER.debug(
                    f"New water heater state discovered: {current_state}, republishing discovery"
                )
                self.hass.async_add_job(self._publish_mqtt_discovery)

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT.

        Overrides base class to publish water heater-specific topics:
        - state (operation mode)
        - current_temperature
        - target_temperature
        """
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Publish state (operation mode)
        state_topic = f"{base_topic}/state"
        state = self._safe_state()
        if state is not None:
            mqtt.publish(state_topic, str(state), qos=0, retain=True)

        # Publish current temperature
        if self.native_current_temperature is not None:
            temp_topic = f"{base_topic}/current_temperature"
            mqtt.publish(temp_topic, str(self.native_current_temperature), qos=0, retain=True)

        # Publish target temperature
        if self.native_target_temperature is not None:
            target_topic = f"{base_topic}/target_temperature"
            mqtt.publish(target_topic, str(self.native_target_temperature), qos=0, retain=True)

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

    async def _cleanup_mqtt(self) -> None:
        """Clean up MQTT topics when entity is removed.

        Extends base class cleanup to also clear water heater-specific topics.
        """
        # Call parent cleanup for base topics (config, state, attributes)
        await super()._cleanup_mqtt()

        if not self.hass or not self.entity_id:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Clean up water heater-specific topics
        topics_to_clear = [
            f"{base_topic}/current_temperature",
            f"{base_topic}/target_temperature",
            f"{base_topic}/mode/set",
            f"{base_topic}/target_temperature/set",
        ]

        for topic in topics_to_clear:
            mqtt.publish(topic, "", qos=0, retain=True)

    async def async_turn_away_mode_on(self) -> None:
        """Turn away mode on."""
        raise NotImplementedError()

    async def async_turn_away_mode_off(self) -> None:
        """Turn away mode off."""
        raise NotImplementedError()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperatures."""
        raise NotImplementedError()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new target operation mode."""
        raise NotImplementedError()


class WaterHeaterEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a water heater entity."""

    max_temp: float = 100.0
    min_temp: float = 0.0
    operation_list: List[str] = field(default_factory=list)


# Default operation list includes all common states
DEFAULT_OPERATION_LIST = [
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_PERFORMANCE,
    STATE_HIGH_DEMAND,
    STATE_HEAT_PUMP,
    STATE_GAS,
    STATE_OFF,
]
