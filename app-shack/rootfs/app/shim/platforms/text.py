"""Text platform shim for Home Assistant.

Provides base classes for text entities.
"""

from enum import StrEnum
from typing import Any, Dict, Optional
from ..entity import (
    Entity,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "text"


class TextMode(StrEnum):
    """Modes for the text entity."""

    TEXT = "text"
    PASSWORD = "password"


class RestoreEntity:
    """Mixin class for restoring entity state after restart.

    This is a stub implementation - state restoration is not implemented
    in the shim but the class is provided for compatibility.
    """

    async def async_get_last_state(self):
        """Return last state from restore."""
        return None


class TextEntity(Entity):
    """Base class for text entities."""

    _attr_native_value: str | None = None
    _attr_pattern: str | None = None
    _attr_mode: str = "text"
    _attr_min: int = 0
    _attr_max: int = 255
    entity_description: Optional["TextEntityDescription"] = None

    @property
    def native_value(self) -> str | None:
        """Return the value of the text."""
        return self._attr_native_value

    @property
    def state(self):
        """Return the state of the text."""
        return self.native_value

    @property
    def pattern(self) -> str | None:
        """Return the regex pattern for the text."""
        # Check entity_description first
        if self.entity_description is not None:
            return self.entity_description.pattern
        return self._attr_pattern

    @property
    def mode(self) -> str:
        """Return the mode of the text."""
        # Check entity_description first
        if self.entity_description is not None:
            return self.entity_description.mode
        return self._attr_mode

    @property
    def min(self) -> int:
        """Return the minimum length of the text."""
        # Check entity_description first
        if self.entity_description is not None:
            return self.entity_description.min
        return self._attr_min

    @property
    def max(self) -> int:
        """Return the maximum length of the text."""
        # Check entity_description first
        if self.entity_description is not None:
            return self.entity_description.max
        return self._attr_max

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
    def entity_category(self) -> Optional[str]:
        """Return the category of this entity."""
        # Check entity_description first
        if self.entity_description is not None:
            return self.entity_description.entity_category
        return self._attr_entity_category

    async def async_set_value(self, value: str) -> None:
        """Change the value of the text."""
        raise NotImplementedError()

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT."""
        _LOGGER.debug(f"TextEntity _mqtt_publish called for {self.entity_id}")

        if not hasattr(self.hass, "_mqtt_client"):
            _LOGGER.debug(f"  No _mqtt_client attribute for {self.entity_id}")
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            _LOGGER.debug(f"  MQTT not connected for {self.entity_id}")
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            _LOGGER.debug(f"  No base_topic for {self.entity_id}")
            return

        # Publish state
        state_topic = f"{base_topic}/state"
        state = self.state
        _LOGGER.debug(f"  Publishing state '{state}' to {state_topic}")
        if state is not None:
            mqtt.publish(state_topic, str(state), qos=0, retain=True)
        else:
            mqtt.publish(state_topic, "", qos=0, retain=True)

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

        # Check if any discovery properties changed
        self._check_and_publish_discovery_update(
            ["device_class", "icon", "pattern", "mode", "min", "max"]
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
        entity_name = get_entity_name_for_discovery(self.name, self.device_info)
        config: Dict[str, Any] = {
            "name": entity_name,
            "unique_id": self.unique_id,
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        # Add text-specific properties
        if self.pattern:
            config["pattern"] = self.pattern

        if self.mode:
            config["mode"] = self.mode

        if self.min is not None:
            config["min"] = self.min

        if self.max is not None:
            config["max"] = self.max

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)


from ..frozen_dataclass_compat import FrozenOrThawed


class TextEntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
    """A class that describes text entities."""

    key: str
    name: str | None = None
    icon: str | None = None
    entity_registry_enabled_default: bool = True
    entity_category: str | None = None
    pattern: str | None = None
    mode: str = "text"
    min: int = 0
    max: int = 255


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up text platform."""
    _LOGGER.warning("text platform setup not implemented")
    return True


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up text platform."""
    _LOGGER.warning("text platform setup not implemented")
    return True
