"""Number platform shim.

Bridges NumberEntity to MQTT number discovery.
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
from ..logging import get_logger
from ..frozen_dataclass_compat import FrozenOrThawed

_LOGGER = get_logger(__name__)

DOMAIN = "number"


class NumberMode(StrEnum):
    """Modes for number entities."""

    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


class NumberEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a number entity."""

    native_min_value: Optional[float] = None
    native_max_value: Optional[float] = None
    native_step: Optional[float] = None
    mode: Optional[NumberMode] = None


class NumberEntity(Entity):
    """Base class for number entities."""

    _attr_native_value: Optional[float] = None
    _attr_native_min_value: float = 0.0
    _attr_native_max_value: float = 100.0
    _attr_native_step: float = 1.0
    _attr_mode: NumberMode = NumberMode.AUTO

    @property
    def native_value(self) -> Optional[float]:
        """Return the value of the entity."""
        return self._attr_native_value

    @property
    def state(self) -> Optional[str]:
        """Return the state of the entity."""
        if self.native_value is None:
            return None
        return str(self.native_value)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.native_value is not None

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return self._attr_native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return self._attr_native_max_value

    @property
    def native_step(self) -> float:
        """Return the step size."""
        return self._attr_native_step

    @property
    def mode(self) -> NumberMode:
        """Return the display mode of the number entity."""
        return self._attr_mode

    def set_native_value(self, value: float) -> None:
        """Set the value of the entity."""
        raise NotImplementedError()

    async def async_set_native_value(self, value: float) -> None:
        """Set the value of the entity."""
        await self.hass.async_add_executor_job(self.set_native_value, value)

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT."""
        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = f"homeassistant/{DOMAIN}/{self.mqtt_object_id}"
        if not base_topic:
            return

        # Publish state
        state_topic = f"{base_topic}/state"
        value = self.native_value
        if value is not None:
            # Clamp value to min/max range to avoid MQTT rejection
            min_val = self.native_min_value
            max_val = self.native_max_value
            clamped_value = max(min_val, min(max_val, value))
            if clamped_value != value:
                _LOGGER.debug(
                    f"Clamping {self.entity_id} value from {value} to {clamped_value} "
                    f"(range {min_val} - {max_val})"
                )
            mqtt.publish(state_topic, str(clamped_value), qos=0, retain=True)
        else:
            mqtt.publish(state_topic, "", qos=0, retain=True)

        # Publish attributes using base class helper
        self._publish_mqtt_attributes()

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery for this number entity."""
        _LOGGER.debug(
            f"NumberEntity _publish_mqtt_discovery called for {self.entity_id}"
        )

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

        base_topic = f"homeassistant/{DOMAIN}/{self.mqtt_object_id}"
        discovery_topic = f"{base_topic}/config"
        _LOGGER.debug(f"Publishing number discovery to {discovery_topic}")

        # Build discovery config
        entity_name = get_entity_name_for_discovery(
            self.name, self.device_info, self.has_entity_name
        )
        import json

        config = {
            "name": entity_name,
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
            "min": self.native_min_value,
            "max": self.native_max_value,
            "step": self.native_step,
            "mode": self.mode.value,
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add enabled_by_default if entity is disabled by default
        if not self.entity_registry_enabled_default:
            config["enabled_by_default"] = False

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
        _LOGGER.debug(f"Published number discovery config for {self.entity_id}")
