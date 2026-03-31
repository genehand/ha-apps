"""Switch platform shim.

Bridges SwitchEntity to MQTT switch discovery.
"""

from typing import Any, Optional
from dataclasses import dataclass, field

from ..entity import (
    ToggleEntity,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)


@dataclass
class SwitchEntityDescription:
    """A class that describes switch entities."""

    key: str
    name: Optional[str] = None
    icon: Optional[str] = None
    entity_category: Optional[str] = None
    device_class: Optional[str] = None
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True


class SwitchEntity(ToggleEntity):
    """Base class for switch entities."""

    _attr_is_on: bool = False

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._attr_is_on

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        raise NotImplementedError()

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        raise NotImplementedError()

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
        entity_name = get_entity_name_for_discovery(self.name, self.device_info)
        config = {
            "name": entity_name,
            "unique_id": self.unique_id,
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
