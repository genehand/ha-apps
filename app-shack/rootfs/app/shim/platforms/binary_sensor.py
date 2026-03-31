"""Binary sensor platform shim.

Bridges BinarySensorEntity to MQTT binary sensor discovery.
"""

from typing import Optional

from ..entity import (
    ToggleEntity,
    format_device_identifiers,
    get_device_info_attr,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
)
from ..logging import get_logger

_LOGGER = get_logger(__name__)


class BinarySensorEntity(ToggleEntity):
    """Base class for binary sensor entities."""

    _attr_is_on: Optional[bool] = None

    @property
    def is_on(self) -> Optional[bool]:
        """Return True if entity is on."""
        return self._attr_is_on

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
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.device_class:
            config["device_class"] = self.device_class

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
