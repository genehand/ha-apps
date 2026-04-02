"""Lock platform shim.

Bridges LockEntity to MQTT lock discovery.
"""

from enum import StrEnum
from typing import Any, Optional

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

DOMAIN = "lock"


class LockEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes lock entities."""


class LockEntity(Entity):
    """Base class for lock entities."""

    _attr_is_locked: bool = False
    _attr_is_locking: bool = False
    _attr_is_unlocking: bool = False

    @property
    def is_locked(self) -> bool:
        """Return True if lock is locked."""
        return self._attr_is_locked

    @property
    def is_locking(self) -> bool:
        """Return True if lock is locking."""
        return self._attr_is_locking

    @property
    def is_unlocking(self) -> bool:
        """Return True if lock is unlocking."""
        return self._attr_is_unlocking

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        raise NotImplementedError()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
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
        state = "LOCKED" if self.is_locked else "UNLOCKED"
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
