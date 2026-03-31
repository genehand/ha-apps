"""Fan platform shim.

Bridges FanEntity to MQTT fan discovery.
"""

import math
from typing import Any, List, Optional

from ..entity import (
    Entity,
    EntityDescription,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "fan"


class FanEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a fan entity."""

    preset_modes: Optional[List[str]] = None
    supported_features: int = 0


# Fan features
SUPPORT_SET_SPEED = 1
SUPPORT_OSCILLATE = 2
SUPPORT_DIRECTION = 4
SUPPORT_PRESET_MODE = 8

# Fan directions
DIRECTION_FORWARD = "forward"
DIRECTION_REVERSE = "reverse"

# Preset modes
PRESET_MODE_AUTO = "auto"
PRESET_MODE_SMART = "smart"
PRESET_MODE_NORMAL = "normal"


class NotValidPresetModeError(ValueError):
    """Error raised when setting invalid preset mode."""

    pass


class FanEntity(Entity):
    """Base class for fan entities."""

    _attr_is_on: Optional[bool] = None
    _attr_speed: Optional[str] = None
    _attr_speed_list: List[str] = []
    _attr_oscillating: Optional[bool] = None
    _attr_direction: Optional[str] = None
    _attr_preset_mode: Optional[str] = None
    _attr_preset_modes: List[str] = []
    _attr_percentage: Optional[int] = None
    _attr_speed_count: int = 0
    _attr_supported_features: int = 0

    # Enable turn on/off backwards compatibility
    _enable_turn_on_off_backwards_compatibility = True

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the entity is on."""
        return self._attr_is_on

    @property
    def speed(self) -> Optional[str]:
        """Return the current speed."""
        return self._attr_speed

    @property
    def speed_list(self) -> List[str]:
        """Return the list of available speeds."""
        return self._attr_speed_list

    @property
    def oscillating(self) -> Optional[bool]:
        """Return whether or not the fan is currently oscillating."""
        return self._attr_oscillating

    @property
    def current_direction(self) -> Optional[str]:
        """Return the current direction of the fan."""
        return self._attr_direction

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode."""
        return self._attr_preset_mode

    @property
    def preset_modes(self) -> List[str]:
        """Return a list of available preset modes."""
        return self._attr_preset_modes

    @property
    def percentage(self) -> Optional[int]:
        """Return the current speed percentage."""
        return self._attr_percentage

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        return self._attr_speed_count

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return self._attr_supported_features

    @property
    def state(self) -> Optional[str]:
        """Return the state."""
        if self.is_on is None:
            return None
        return "on" if self.is_on else "off"

    def set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        raise NotImplementedError()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        await self.hass.async_add_executor_job(self.set_percentage, percentage)

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        raise NotImplementedError()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Invalid preset mode: {preset_mode}")
        await self.hass.async_add_executor_job(self.set_preset_mode, preset_mode)

    def oscillate(self, oscillating: bool) -> None:
        """Set oscillation."""
        raise NotImplementedError()

    async def async_oscillate(self, oscillating: bool) -> None:
        """Set oscillation."""
        await self.hass.async_add_executor_job(self.oscillate, oscillating)

    def set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        raise NotImplementedError()

    async def async_set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        await self.hass.async_add_executor_job(self.set_direction, direction)

    def turn_on(
        self,
        percentage: Optional[int] = None,
        preset_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        raise NotImplementedError()

    async def async_turn_on(
        self,
        percentage: Optional[int] = None,
        preset_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if percentage is not None:
            await self.async_set_percentage(percentage)
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        await self.hass.async_add_executor_job(
            self.turn_on, percentage, preset_mode, **kwargs
        )

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        raise NotImplementedError()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.hass.async_add_executor_job(self.turn_off, **kwargs)

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
        state = "ON" if self.is_on else "OFF"
        mqtt.publish(state_topic, state, qos=0, retain=True)

        # Publish percentage if supported
        if self.supported_features & SUPPORT_SET_SPEED and self.percentage is not None:
            pct_topic = f"{base_topic}/percentage_state"
            mqtt.publish(pct_topic, str(self.percentage), qos=0, retain=True)

        # Publish preset mode if supported
        if self.supported_features & SUPPORT_PRESET_MODE and self.preset_mode:
            preset_topic = f"{base_topic}/preset_mode_state"
            mqtt.publish(preset_topic, self.preset_mode, qos=0, retain=True)

        # Publish oscillation if supported
        if self.supported_features & SUPPORT_OSCILLATE and self.oscillating is not None:
            osc_topic = f"{base_topic}/oscillation_state"
            osc_state = "ON" if self.oscillating else "OFF"
            mqtt.publish(osc_topic, osc_state, qos=0, retain=True)

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

    async def _cleanup_mqtt(self) -> None:
        """Clean up MQTT topics."""
        await super()._cleanup_mqtt()

        if not self.hass or not self.entity_id:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Clean up percentage state topic
        pct_topic = f"{base_topic}/percentage_state"
        mqtt.publish(pct_topic, "", qos=0, retain=True)

        # Clean up preset mode state topic
        preset_topic = f"{base_topic}/preset_mode_state"
        mqtt.publish(preset_topic, "", qos=0, retain=True)

        # Clean up oscillation state topic
        osc_topic = f"{base_topic}/oscillation_state"
        mqtt.publish(osc_topic, "", qos=0, retain=True)

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery when added."""
        _LOGGER.debug(f"FanEntity _publish_mqtt_discovery called for {self.entity_id}")

        if not hasattr(self.hass, "_mqtt_client"):
            _LOGGER.warning(
                f"Cannot publish discovery for {self.entity_id}: no _mqtt_client"
            )
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            _LOGGER.warning(
                f"Cannot publish discovery for {self.entity_id}: MQTT not connected"
            )
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        discovery_topic = f"{base_topic}/config"
        _LOGGER.debug(f"Publishing fan discovery to {discovery_topic}")

        # Build discovery config
        # Strip device name prefix from entity name if present
        entity_name = get_entity_name_for_discovery(self.name, self.device_info)
        config = {
            "name": entity_name,
            "unique_id": self.unique_id,
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        # Add features
        if self.supported_features & SUPPORT_SET_SPEED:
            config["percentage_command_topic"] = f"{base_topic}/percentage_set"
            config["percentage_state_topic"] = f"{base_topic}/percentage_state"
            if self.speed_count:
                config["speed_range_min"] = 1
                config["speed_range_max"] = self.speed_count

        if self.supported_features & SUPPORT_PRESET_MODE and self.preset_modes:
            config["preset_mode_command_topic"] = f"{base_topic}/preset_mode_set"
            config["preset_mode_state_topic"] = f"{base_topic}/preset_mode_state"
            config["preset_modes"] = self.preset_modes

        if self.supported_features & SUPPORT_OSCILLATE:
            config["oscillation_command_topic"] = f"{base_topic}/oscillation_set"
            config["oscillation_state_topic"] = f"{base_topic}/oscillation_state"

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
        _LOGGER.debug(f"Published fan discovery config for {self.entity_id}")


class FanEntityFeature:
    """Supported features of the fan entity."""

    SET_SPEED = 1
    OSCILLATE = 2
    DIRECTION = 4
    PRESET_MODE = 8
    TURN_ON = 16
    TURN_OFF = 32


def percentage_to_ranged_value(low_high_range, percentage: int):
    """Map a percentage to a value within a range."""
    low, high = low_high_range
    return low + (high - low) * percentage / 100


def ranged_value_to_percentage(low_high_range, value):
    """Map a value within a range to a percentage."""
    low, high = low_high_range
    if value is None:
        return None
    return round((value - low) / (high - low) * 100)


def int_states_in_range(low_high_range):
    """Return the number of integer states in a range."""
    low, high = low_high_range
    return high - low + 1
