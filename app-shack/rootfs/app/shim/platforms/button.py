"""Button platform shim.

Bridges ButtonEntity to MQTT button discovery.
"""

from enum import StrEnum
from typing import Optional

from ..entity import (
    Entity,
    EntityDescription,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
    get_mqtt_safe_unique_id,
)
from ..frozen_dataclass_compat import FrozenOrThawed

DOMAIN = "button"


class ButtonDeviceClass(StrEnum):
    """Device class for buttons."""

    RESTART = "restart"
    UPDATE = "update"
    IDENTIFY = "identify"


class ButtonEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes button entities."""


class ButtonEntity(Entity):
    """Base class for button entities."""

    _attr_device_class: Optional[str] = None

    @property
    def state(self) -> Optional[str]:
        """Return the state of the entity.

        Buttons don't have a traditional state - they are momentary actions.
        Return a static string to indicate the button is present and pressable.
        """
        return "Press"

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Buttons are always available unless explicitly disabled.
        """
        return True

    def press(self) -> None:
        """Press the button."""
        raise NotImplementedError()

    async def async_press(self) -> None:
        """Press the button."""
        await self.hass.async_add_executor_job(self.press)

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT."""
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        # Buttons don't have state, they just receive commands
        # But we publish availability and attributes if any
        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

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
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.device_class:
            config["device_class"] = self.device_class

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
