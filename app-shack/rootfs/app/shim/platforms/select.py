"""Select platform shim.

Bridges SelectEntity to MQTT select discovery.
"""

from typing import Any, List, Optional

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

DOMAIN = "select"


class SelectEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a select entity."""

    options: List[str] = None
    options_map: dict = (
        None  # Maps internal values to display values for MQTT discovery
    )


class SelectEntity(Entity):
    """Base class for select entities."""

    _attr_current_option: Optional[str] = None
    _attr_options: List[str] = []

    @property
    def current_option(self) -> Optional[str]:
        """Return the selected entity option to represent the entity state."""
        return self._attr_current_option

    @property
    def options(self) -> List[str]:
        """Return a set of selectable options."""
        return self._attr_options

    def select_option(self, option: str) -> None:
        """Change the selected option."""
        raise NotImplementedError()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.hass.async_add_executor_job(self.select_option, option)

    def _get_options_map(self) -> Optional[dict]:
        """Get the options map if available."""
        if hasattr(self, "entity_description") and self.entity_description:
            return getattr(self.entity_description, "options_map", None)
        return None

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT."""
        import logging

        _LOGGER = logging.getLogger(__name__)

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        # Publish state (map internal value to display value if options_map exists)
        state_topic = f"{base_topic}/state"
        state = self.current_option
        if state is not None:
            options_map = self._get_options_map()
            _LOGGER.debug(
                f"_mqtt_publish: state={state}, options_map={options_map is not None}"
            )
            if options_map:
                _LOGGER.debug(
                    f"_mqtt_publish: options_map keys={list(options_map.keys())[:5]}"
                )
                if state in options_map:
                    state = options_map[state]
                    _LOGGER.debug(f"_mqtt_publish: translated state={state}")
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

        # Map options to display values if options_map is available
        options = self.options
        options_map = self._get_options_map()
        if options_map:
            options = [options_map.get(opt, opt) for opt in options]

        config = {
            "name": entity_name,
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "state_topic": f"{base_topic}/state",
            "command_topic": f"{base_topic}/set",
            "options": options,
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.icon:
            config["icon"] = self.icon

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add translation_key if available
        if hasattr(self, "entity_description") and self.entity_description:
            if getattr(self.entity_description, "translation_key", None):
                config["translation_key"] = self.entity_description.translation_key

        # Add attributes topic using base class helper
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)
