"""Switch platform shim.

Bridges SwitchEntity to MQTT switch discovery.
"""

from enum import StrEnum
from typing import Any, Optional

import voluptuous as vol

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

DOMAIN = "switch"


class SwitchDeviceClass(StrEnum):
    """Device class for switches."""

    OUTLET = "outlet"
    SWITCH = "switch"


# Schema for device classes (used by integrations for validation)
DEVICE_CLASSES_SCHEMA = vol.In([cls.value for cls in SwitchDeviceClass])


class SwitchEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes switch entities."""


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

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on via sync method in executor."""
        await self.hass.async_add_executor_job(self.turn_on, **kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off via sync method in executor."""
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

        # Generate enhanced unique_id for localtuya integration
        # Change "local_" prefix to "localtuya_" and include device name
        original_unique_id = self.unique_id
        if original_unique_id.startswith("local_") and self.device_info:
            device_name = getattr(self.device_info, "name", "")
            if device_name:
                # Replace "local_" with "localtuya_" and prepend device name
                clean_name = device_name.replace(" ", "_").replace("-", "_")
                enhanced_unique_id = f"localtuya_{clean_name}_{original_unique_id[6:]}"
            else:
                enhanced_unique_id = f"localtuya_{original_unique_id[6:]}"
        else:
            enhanced_unique_id = original_unique_id

        config = {
            "name": entity_name,
            "unique_id": get_mqtt_safe_unique_id(enhanced_unique_id),
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

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
