"""Climate platform shim.

Bridges ClimateEntity to MQTT climate discovery.
"""

from typing import Any, List, Optional

from ..entity import (
    Entity,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "climate"

# HVAC modes
HVAC_MODE_OFF = "off"
HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"
HVAC_MODE_HEAT_COOL = "heat_cool"
HVAC_MODE_AUTO = "auto"
HVAC_MODE_DRY = "dry"
HVAC_MODE_FAN_ONLY = "fan_only"

# HVAC actions
CURRENT_HVAC_OFF = "off"
CURRENT_HVAC_HEAT = "heating"
CURRENT_HVAC_COOL = "cooling"
CURRENT_HVAC_DRY = "drying"
CURRENT_HVAC_IDLE = "idle"
CURRENT_HVAC_FAN = "fan"

# Preset modes
PRESET_NONE = "none"
PRESET_ECO = "eco"
PRESET_AWAY = "away"
PRESET_BOOST = "boost"
PRESET_COMFORT = "comfort"
PRESET_HOME = "home"
PRESET_SLEEP = "sleep"
PRESET_ACTIVITY = "activity"

# Features
SUPPORT_TARGET_TEMPERATURE = 1
SUPPORT_TARGET_TEMPERATURE_RANGE = 2
SUPPORT_TARGET_HUMIDITY = 4
SUPPORT_FAN_MODE = 8
SUPPORT_PRESET_MODE = 16
SUPPORT_SWING_MODE = 32
SUPPORT_AUX_HEAT = 64


class ClimateEntity(Entity):
    """Base class for climate entities."""

    _attr_current_temperature: Optional[float] = None
    _attr_current_humidity: Optional[int] = None
    _attr_hvac_mode: Optional[str] = None
    _attr_hvac_modes: List[str] = []
    _attr_hvac_action: Optional[str] = None
    _attr_preset_mode: Optional[str] = None
    _attr_preset_modes: List[str] = []
    _attr_fan_mode: Optional[str] = None
    _attr_fan_modes: List[str] = []
    _attr_swing_mode: Optional[str] = None
    _attr_swing_modes: List[str] = []
    _attr_target_temperature: Optional[float] = None
    _attr_target_temperature_high: Optional[float] = None
    _attr_target_temperature_low: Optional[float] = None
    _attr_target_humidity: Optional[int] = None
    _attr_supported_features: int = 0
    _attr_min_temp: float = 7
    _attr_max_temp: float = 35
    _attr_min_humidity: int = 30
    _attr_max_humidity: int = 99

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return self._attr_current_temperature

    @property
    def current_humidity(self) -> Optional[int]:
        """Return the current humidity."""
        return self._attr_current_humidity

    @property
    def hvac_mode(self) -> Optional[str]:
        """Return hvac operation ie. heat, cool mode."""
        return self._attr_hvac_mode

    @property
    def hvac_modes(self) -> List[str]:
        """Return the list of available hvac operation modes."""
        return self._attr_hvac_modes

    @property
    def hvac_action(self) -> Optional[str]:
        """Return the current running hvac operation."""
        return self._attr_hvac_action

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode."""
        return self._attr_preset_mode

    @property
    def preset_modes(self) -> List[str]:
        """Return a list of available preset modes."""
        return self._attr_preset_modes

    @property
    def fan_mode(self) -> Optional[str]:
        """Return the fan setting."""
        return self._attr_fan_mode

    @property
    def fan_modes(self) -> List[str]:
        """Return the list of available fan modes."""
        return self._attr_fan_modes

    @property
    def swing_mode(self) -> Optional[str]:
        """Return the swing setting."""
        return self._attr_swing_mode

    @property
    def swing_modes(self) -> List[str]:
        """Return the list of available swing modes."""
        return self._attr_swing_modes

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        return self._attr_target_temperature

    @property
    def target_temperature_high(self) -> Optional[float]:
        """Return the highbound target temperature we try to reach."""
        return self._attr_target_temperature_high

    @property
    def target_temperature_low(self) -> Optional[float]:
        """Return the lowbound target temperature we try to reach."""
        return self._attr_target_temperature_low

    @property
    def target_humidity(self) -> Optional[int]:
        """Return the humidity we try to reach."""
        return self._attr_target_humidity

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return self._attr_supported_features

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._attr_min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._attr_max_temp

    @property
    def min_humidity(self) -> int:
        """Return the minimum humidity."""
        return self._attr_min_humidity

    @property
    def max_humidity(self) -> int:
        """Return the maximum humidity."""
        return self._attr_max_humidity

    @property
    def state(self) -> Optional[str]:
        """Return the current state."""
        return self.hvac_mode

    def set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        raise NotImplementedError()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        await self.hass.async_add_executor_job(self.set_temperature, **kwargs)

    def set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        raise NotImplementedError()

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        await self.hass.async_add_executor_job(self.set_humidity, humidity)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target hvac mode."""
        raise NotImplementedError()

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target hvac mode."""
        await self.hass.async_add_executor_job(self.set_hvac_mode, hvac_mode)

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        raise NotImplementedError()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        await self.hass.async_add_executor_job(self.set_preset_mode, preset_mode)

    def set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        raise NotImplementedError()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        await self.hass.async_add_executor_job(self.set_fan_mode, fan_mode)

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        raise NotImplementedError()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        await self.hass.async_add_executor_job(self.set_swing_mode, swing_mode)

    def turn_on(self) -> None:
        """Turn the entity on."""
        raise NotImplementedError()

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self.hass.async_add_executor_job(self.turn_on)

    def turn_off(self) -> None:
        """Turn the entity off."""
        raise NotImplementedError()

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.hass.async_add_executor_job(self.turn_off)

    def turn_aux_heat_on(self) -> None:
        """Turn auxiliary heater on."""
        raise NotImplementedError()

    async def async_turn_aux_heat_on(self) -> None:
        """Turn auxiliary heater on."""
        await self.hass.async_add_executor_job(self.turn_aux_heat_on)

    def turn_aux_heat_off(self) -> None:
        """Turn auxiliary heater off."""
        raise NotImplementedError()

    async def async_turn_aux_heat_off(self) -> None:
        """Turn auxiliary heater off."""
        await self.hass.async_add_executor_job(self.turn_aux_heat_off)

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

        # Publish state (HVAC mode)
        state_topic = f"{base_topic}/state"
        state = self.hvac_mode or "off"
        mqtt.publish(state_topic, state, qos=0, retain=True)

        # Publish current temperature
        if self.current_temperature is not None:
            temp_topic = f"{base_topic}/current_temperature"
            mqtt.publish(temp_topic, str(self.current_temperature), qos=0, retain=True)

        # Publish target temperature
        if self.target_temperature is not None:
            target_topic = f"{base_topic}/target_temperature"
            mqtt.publish(target_topic, str(self.target_temperature), qos=0, retain=True)

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

        # Clean up current temperature topic
        temp_topic = f"{base_topic}/current_temperature"
        mqtt.publish(temp_topic, "", qos=0, retain=True)

        # Clean up target temperature topic
        target_topic = f"{base_topic}/target_temperature"
        mqtt.publish(target_topic, "", qos=0, retain=True)

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery when added."""
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
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
            "mode_state_topic": f"{base_topic}/state",
            "mode_command_topic": f"{base_topic}/mode_set",
            "current_temperature_topic": f"{base_topic}/current_temperature",
            "temperature_state_topic": f"{base_topic}/target_temperature",
            "temperature_command_topic": f"{base_topic}/temperature_set",
            "min_temp": self.min_temp,
            "max_temp": self.max_temp,
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.hvac_modes:
            config["modes"] = self.hvac_modes

        if self.supported_features & SUPPORT_TARGET_TEMPERATURE_RANGE:
            config["temp_step"] = 0.5

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
