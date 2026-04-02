"""Update platform shim.

Bridges UpdateEntity to MQTT update discovery.
Shows available updates for integrations.
"""

from enum import StrEnum
from typing import Any, Dict, Optional
from datetime import datetime

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
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "update"


class UpdateDeviceClass(StrEnum):
    """Device class for updates."""

    FIRMWARE = "firmware"


class UpdateEntityDescription(
    EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
):
    """Describe a update entity."""

    device_class: Optional[str] = None


class UpdateEntity(Entity):
    """Base class for update entities."""

    _attr_installed_version: Optional[str] = None
    _attr_latest_version: Optional[str] = None
    _attr_release_summary: Optional[str] = None
    _attr_release_url: Optional[str] = None
    _attr_title: Optional[str] = None
    _attr_in_progress: bool = False
    _attr_update_percentage: Optional[int] = None
    _attr_auto_update: bool = False
    _attr_entity_picture: Optional[str] = None
    _attr_supported_features: int = 0

    @property
    def installed_version(self) -> Optional[str]:
        """Version installed and in use."""
        return self._attr_installed_version

    @property
    def latest_version(self) -> Optional[str]:
        """Latest version available for install."""
        return self._attr_latest_version

    @property
    def release_summary(self) -> Optional[str]:
        """Summary of the release notes or changelog."""
        return self._attr_release_summary

    @property
    def release_url(self) -> Optional[str]:
        """URL to the full release notes."""
        return self._attr_release_url

    @property
    def title(self) -> Optional[str]:
        """Title of the software."""
        return self._attr_title

    @property
    def in_progress(self) -> bool:
        """Update installation in progress."""
        return self._attr_in_progress

    @property
    def update_percentage(self) -> Optional[int]:
        """Update installation progress percentage."""
        return self._attr_update_percentage

    @property
    def auto_update(self) -> bool:
        """Indicate if the device or service has auto update enabled."""
        return self._attr_auto_update

    @property
    def entity_picture(self) -> Optional[str]:
        """Return the entity picture."""
        return self._attr_entity_picture

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return self._attr_supported_features

    @property
    def state(self) -> Optional[str]:
        """Return the state of the entity."""
        if self.latest_version is None or self.installed_version is None:
            return None
        if self.latest_version == self.installed_version:
            return "off"  # No update available
        return "on"  # Update available

    def install(self, version: Optional[str] = None, backup: bool = False) -> None:
        """Install an update."""
        raise NotImplementedError()

    async def async_install(
        self, version: Optional[str] = None, backup: bool = False
    ) -> None:
        """Install an update."""
        await self.hass.async_add_executor_job(self.install, version, backup)

    def release_notes(self) -> Optional[str]:
        """Return full release notes."""
        return None

    async def async_release_notes(self) -> Optional[str]:
        """Return full release notes."""
        return await self.hass.async_add_executor_job(self.release_notes)

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
        state = self.state
        if state is not None:
            mqtt.publish(state_topic, state, qos=0, retain=True)

        # Publish installed version
        if self.installed_version is not None:
            installed_topic = f"{base_topic}/installed_version"
            mqtt.publish(installed_topic, self.installed_version, qos=0, retain=True)

        # Publish latest version
        if self.latest_version is not None:
            latest_topic = f"{base_topic}/latest_version"
            mqtt.publish(latest_topic, self.latest_version, qos=0, retain=True)

        # Publish title
        if self.title is not None:
            title_topic = f"{base_topic}/title"
            mqtt.publish(title_topic, self.title, qos=0, retain=True)

        # Publish in_progress
        progress_topic = f"{base_topic}/in_progress"
        mqtt.publish(
            progress_topic, "true" if self.in_progress else "false", qos=0, retain=True
        )

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

        # Clean up installed version topic
        installed_topic = f"{base_topic}/installed_version"
        mqtt.publish(installed_topic, "", qos=0, retain=True)

        # Clean up latest version topic
        latest_topic = f"{base_topic}/latest_version"
        mqtt.publish(latest_topic, "", qos=0, retain=True)

        # Clean up title topic
        title_topic = f"{base_topic}/title"
        mqtt.publish(title_topic, "", qos=0, retain=True)

        # Clean up in_progress topic
        progress_topic = f"{base_topic}/in_progress"
        mqtt.publish(progress_topic, "", qos=0, retain=True)

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
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)


class UpdateEntityFeature:
    """Supported features of the update entity."""

    INSTALL = 1
    SPECIFIC_VERSION = 2
    PROGRESS = 4
    BACKUP = 8
    RELEASE_NOTES = 16
