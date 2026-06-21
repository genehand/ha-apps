"""Event platform shim.

Bridges EventEntity to MQTT event discovery.
"""

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from ..entity import (
    Entity,
    EntityDescription,
    build_mqtt_device_config,
    get_entity_name_for_discovery,
    get_mqtt_safe_unique_id,
)
from ..frozen_dataclass_compat import FrozenOrThawed

DOMAIN = "event"

ATTR_EVENT_TYPE = "event_type"
ATTR_EVENT_TYPES = "event_types"


class EventDeviceClass(StrEnum):
    """Device class for events."""

    DOORBELL = "doorbell"
    BUTTON = "button"
    MOTION = "motion"


class DoorbellEventType(StrEnum):
    """Standard event types for doorbell device class."""

    RING = "ring"


class EventEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """A class that describes event entities."""

    device_class: Optional[EventDeviceClass] = None
    event_types: Optional[list[str]] = None


class EventEntity(Entity):
    """Base class for event entities.

    Event entities are momentary — they fire events rather than holding
    a persistent state. The state is the timestamp of the last triggered
    event, and attributes carry the event type and payload.
    """

    _attr_device_class: Optional[EventDeviceClass] = None
    _attr_event_types: Optional[list[str]] = None
    _attr_state: Optional[str] = None

    @property
    def device_class(self) -> Optional[EventDeviceClass]:
        """Return the class of this entity."""
        if hasattr(self, "_attr_device_class") and self._attr_device_class is not None:
            return self._attr_device_class
        if hasattr(self, "entity_description") and self.entity_description is not None:
            return getattr(self.entity_description, "device_class", None)
        return None

    @property
    def event_types(self) -> list[str]:
        """Return list of possible event types."""
        if hasattr(self, "_attr_event_types") and self._attr_event_types is not None:
            return self._attr_event_types
        if (
            hasattr(self, "entity_description")
            and self.entity_description is not None
            and getattr(self.entity_description, "event_types", None) is not None
        ):
            return self.entity_description.event_types
        return []

    @property
    def state(self) -> Optional[str]:
        """Return the entity state (timestamp of last event)."""
        last_event = getattr(self, "_last_event_triggered", None)
        if last_event is None:
            return None
        return last_event.isoformat(timespec="milliseconds")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attributes: dict[str, Any] = {}
        last_event_type = getattr(self, "_last_event_type", None)
        last_event_attributes = getattr(self, "_last_event_attributes", None)
        if last_event_type is not None:
            attributes[ATTR_EVENT_TYPE] = last_event_type
        if last_event_attributes:
            attributes.update(last_event_attributes)
        attributes[ATTR_EVENT_TYPES] = self.event_types
        return attributes

    def _trigger_event(
        self, event_type: str, event_attributes: dict[str, Any] | None = None
    ) -> None:
        """Process a new event."""
        if event_type not in self.event_types:
            raise ValueError(
                f"Invalid event type {event_type} for {self.entity_id}"
            )
        self._last_event_triggered = datetime.now()
        self._last_event_type = event_type
        self._last_event_attributes = event_attributes

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

        # Publish event as JSON payload on the state topic
        state_topic = f"{base_topic}/state"
        last_event_type = getattr(self, "_last_event_type", None)
        last_event_attributes = getattr(self, "_last_event_attributes", None)
        if last_event_type is not None:
            payload = {ATTR_EVENT_TYPE: last_event_type}
            if last_event_attributes:
                payload.update(last_event_attributes)
            mqtt.publish(state_topic, json.dumps(payload), qos=0, retain=False)
        else:
            # Empty payload before first event
            mqtt.publish(state_topic, "", qos=0, retain=False)

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
            "event_types": self.event_types,
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.device_class:
            config["device_class"] = (
                self.device_class.value
                if hasattr(self.device_class, "value")
                else str(self.device_class)
            )

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
        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
