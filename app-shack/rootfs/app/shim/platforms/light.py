"""Light platform shim.

Bridges LightEntity to MQTT light discovery.
"""

from enum import StrEnum
from typing import Any, Dict, List, Optional, Tuple

from ..entity import (
    ToggleEntity,
    EntityDescription,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
    get_mqtt_safe_unique_id,
)
from ..frozen_dataclass_compat import FrozenOrThawed
from ..frozen_dataclass_compat import FrozenOrThawed
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "light"


class ColorMode(StrEnum):
    """Color modes for light entities."""

    UNKNOWN = "unknown"
    ONOFF = "onoff"
    BRIGHTNESS = "brightness"
    COLOR_TEMP = "color_temp"
    HS = "hs"
    XY = "xy"
    RGB = "rgb"
    RGBW = "rgbw"
    RGBWW = "rgbww"
    WHITE = "white"


class LightEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a light entity."""


# Light features
SUPPORT_BRIGHTNESS = 1
SUPPORT_COLOR_TEMP = 2
SUPPORT_EFFECT = 4
SUPPORT_FLASH = 8
SUPPORT_COLOR = 16
SUPPORT_TRANSITION = 32
SUPPORT_WHITE_VALUE = 128

ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMP = "color_temp"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_EFFECT = "effect"
ATTR_HS_COLOR = "hs_color"
ATTR_RGB_COLOR = "rgb_color"
ATTR_RGBW_COLOR = "rgbw_color"
ATTR_RGBWW_COLOR = "rgbww_color"
ATTR_XY_COLOR = "xy_color"
ATTR_WHITE_VALUE = "white_value"
ATTR_TRANSITION = "transition"
ATTR_FLASH = "flash"


class LightEntity(ToggleEntity):
    """Base class for light entities."""

    _attr_brightness: Optional[int] = None
    _attr_color_mode: Optional[str] = None
    _attr_color_temp: Optional[int] = None  # in mireds
    _attr_effect: Optional[str] = None
    _attr_effect_list: Optional[List[str]] = None
    _attr_hs_color: Optional[Tuple[float, float]] = None
    _attr_is_on: bool = False
    _attr_max_mireds: int = 500  # min K
    _attr_min_mireds: int = 153  # max K
    _attr_rgb_color: Optional[Tuple[int, int, int]] = None
    _attr_rgbw_color: Optional[Tuple[int, int, int, int]] = None
    _attr_rgbww_color: Optional[Tuple[int, int, int, int, int]] = None
    _attr_supported_color_modes: Optional[List[str]] = None
    _attr_supported_features: int = 0
    _attr_xy_color: Optional[Tuple[float, float]] = None
    _attr_white_value: Optional[int] = None

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        return self._attr_brightness

    @property
    def color_mode(self) -> Optional[str]:
        """Return the color mode of the light."""
        return self._attr_color_mode

    @property
    def color_temp(self) -> Optional[int]:
        """Return the CT color value in mireds."""
        return self._attr_color_temp

    @property
    def effect(self) -> Optional[str]:
        """Return the current effect."""
        return self._attr_effect

    @property
    def effect_list(self) -> Optional[List[str]]:
        """Return the list of supported effects."""
        return self._attr_effect_list

    @property
    def hs_color(self) -> Optional[Tuple[float, float]]:
        """Return the hue and saturation color value [float, float]."""
        return self._attr_hs_color

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._attr_is_on

    @property
    def max_mireds(self) -> int:
        """Return the warmest color temp this light supports in mireds."""
        return self._attr_max_mireds

    @property
    def min_mireds(self) -> int:
        """Return the coldest color temp this light supports in mireds."""
        return self._attr_min_mireds

    @property
    def rgb_color(self) -> Optional[Tuple[int, int, int]]:
        """Return the rgb color value [int, int, int]."""
        return self._attr_rgb_color

    @property
    def rgbw_color(self) -> Optional[Tuple[int, int, int, int]]:
        """Return the rgbw color value [int, int, int, int]."""
        return self._attr_rgbw_color

    @property
    def rgbww_color(self) -> Optional[Tuple[int, int, int, int, int]]:
        """Return the rgbww color value [int, int, int, int, int]."""
        return self._attr_rgbww_color

    @property
    def supported_color_modes(self) -> Optional[List[str]]:
        """Flag supported color modes."""
        return self._attr_supported_color_modes

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return self._attr_supported_features

    @property
    def xy_color(self) -> Optional[Tuple[float, float]]:
        """Return the xy color value [float, float]."""
        return self._attr_xy_color

    @property
    def white_value(self) -> Optional[int]:
        """Return the white value of this light between 0..255."""
        return self._attr_white_value

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        raise NotImplementedError()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.hass.async_add_executor_job(self.turn_on, **kwargs)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        raise NotImplementedError()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
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

        # Publish brightness
        if self.brightness is not None:
            bright_topic = f"{base_topic}/brightness"
            mqtt.publish(bright_topic, str(self.brightness), qos=0, retain=True)

        # Publish color temp
        if self.color_temp is not None:
            ct_topic = f"{base_topic}/color_temp"
            mqtt.publish(ct_topic, str(self.color_temp), qos=0, retain=True)

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

        # Clean up brightness topic
        bright_topic = f"{base_topic}/brightness"
        mqtt.publish(bright_topic, "", qos=0, retain=True)

        # Clean up color temp topic
        ct_topic = f"{base_topic}/color_temp"
        mqtt.publish(ct_topic, "", qos=0, retain=True)

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
        entity_name = get_entity_name_for_discovery(
            self.name, self.device_info, self.has_entity_name
        )
        config = {
            "name": entity_name,
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        # Add features
        if self.supported_features & SUPPORT_BRIGHTNESS:
            config["brightness"] = True
            config["brightness_command_topic"] = f"{base_topic}/brightness_set"
            config["brightness_state_topic"] = f"{base_topic}/brightness"

        if self.supported_features & SUPPORT_COLOR_TEMP:
            config["color_temp"] = True
            config["min_mireds"] = self.min_mireds
            config["max_mireds"] = self.max_mireds

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add enabled_by_default if entity is disabled by default
        if not self.entity_registry_enabled_default:
            config["enabled_by_default"] = False

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)


class LightEntityFeature:
    """Supported features of the light entity."""

    EFFECT = 4
    FLASH = 8
    TRANSITION = 32
