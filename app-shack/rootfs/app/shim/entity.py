"""Base entity classes for Home Assistant Shim.

Provides Entity base class and entity registry functionality.
"""

from enum import StrEnum
from typing import Any, Dict, List, Optional, Callable, Set, Union
import asyncio
from datetime import datetime

from .logging import get_logger
from .frozen_dataclass_compat import FrozenOrThawed

# STATE_UNAVAILABLE is a constant from homeassistant.const
# Define it locally to avoid circular import during module load
STATE_UNAVAILABLE = "unavailable"

_LOGGER = get_logger(__name__)


class EntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
    """A class that describes Home Assistant entities."""

    key: str
    device_class: Optional[str] = None
    entity_category: Optional[str] = None
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    has_entity_name: bool = False
    icon: Optional[str] = None
    name: Optional[str] = None
    translation_key: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    disabled_by_default: bool = False


def format_device_identifiers(identifiers: Set[Union[tuple, list, str]]) -> List[str]:
    """Convert device identifiers from set of tuples to list of strings.

    Args:
        identifiers: A set containing tuples, lists, or strings representing
                     device identifiers.

    Returns:
        A list of strings with identifiers joined by dashes, with colons
        converted to dashes for MQTT compatibility.
    """
    id_list = []
    for item in identifiers:
        if isinstance(item, (list, tuple)):
            identifier = "-".join(str(x) for x in item)
        else:
            identifier = str(item)
        # Convert colons to dashes for MQTT topic compatibility
        id_list.append(identifier.replace(":", "-"))
    return id_list


def get_mqtt_safe_unique_id(unique_id: str) -> str:
    """Convert a unique_id to an MQTT-safe format.

    Replaces colons and spaces with dashes for valid MQTT topic names and identifiers.

    Args:
        unique_id: The original unique_id (may contain colons/spaces from device serials).

    Returns:
        The unique_id with colons and spaces replaced by dashes.
    """
    return unique_id.replace(":", "-").replace(" ", "-")


def get_mqtt_entity_id(entity_id: str) -> str:
    """Extract the MQTT-safe entity ID without domain prefix.

    Converts 'fan.living_room' to 'living-room' for MQTT topics.
    Replaces dots, underscores, colons, and spaces with dashes for valid MQTT topic names.
    Removes duplicate domain segments to avoid redundancy (e.g., integration
    unique_ids that include the domain twice).

    Args:
        entity_id: The full entity ID (e.g., 'fan.living_room').

    Returns:
        The entity ID without domain prefix, with dots/underscores/colons/spaces replaced by dashes,
        and duplicate domain segments removed. All lowercase for MQTT topic compatibility.
    """
    if "." in entity_id:
        entity_id = entity_id.split(".", 1)[1]

    # Replace dots, underscores, colons, and spaces with dashes for MQTT topic compatibility
    # Lowercase to ensure consistency with Home Assistant's MQTT handling
    dashed = (
        entity_id.replace(".", "-")
        .replace("_", "-")
        .replace(":", "-")
        .replace(" ", "-")
        .lower()
    )

    # Remove duplicate segments that appear later in the string
    # This handles cases like "flightradar24-40897-75808525-flightradar24-in-area"
    # which should become "flightradar24-40897-75808525-in-area"
    parts = dashed.split("-")
    seen = set()
    deduplicated = []
    for part in parts:
        if part not in seen:
            deduplicated.append(part)
            seen.add(part)

    return "-".join(deduplicated)


def get_mqtt_object_id(entity_id: str) -> str:
    """Get the MQTT-safe object_id from an entity_id.

    This is used for looking up entities from MQTT topics. The object_id
    is the entity_id without the domain prefix, with dots, underscores, and colons
    converted to dashes, and duplicate segments removed.

    Args:
        entity_id: The full entity ID (e.g., 'switch.flightradar24_41831.scanning').

    Returns:
        The MQTT-safe object_id (e.g., 'flightradar24-41831-scanning').
    """
    return get_mqtt_entity_id(entity_id)


def get_device_info_attr(device_info: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute from device_info whether it's a dict or dataclass.

    Args:
        device_info: Device info as a dict or DeviceInfo dataclass.
        attr: The attribute name to get.
        default: Default value if attribute is not found.

    Returns:
        The attribute value or default.
    """
    if device_info is None:
        return default

    if isinstance(device_info, dict):
        return device_info.get(attr, default)
    else:
        return getattr(device_info, attr, default)


def build_mqtt_device_config(device_info: Any) -> Dict[str, Any]:
    """Build MQTT device configuration dict from device_info.

    Only includes non-None values for manufacturer, model, and sw_version
    to avoid HA rejecting the discovery message.

    Args:
        device_info: Device info as a dict or DeviceInfo dataclass.

    Returns:
        Device configuration dict for MQTT discovery.
    """
    if device_info is None:
        return {}

    config: Dict[str, Any] = {
        "identifiers": format_device_identifiers(
            get_device_info_attr(device_info, "identifiers", set())
        ),
    }

    # Look up device in registry once if we have identifiers
    registry_device = None
    identifiers = get_device_info_attr(device_info, "identifiers")
    if identifiers:
        try:
            from homeassistant.helpers import device_registry as dr

            # Get the registry - works with or without hass
            registry = dr.async_get(None)
            if registry and hasattr(registry, "_devices"):
                # Look for matching device in registry
                for device_id, device in registry._devices.items():
                    # Check if any identifier matches
                    if hasattr(device, "identifiers"):
                        # Check for identifier overlap
                        if identifiers & device.identifiers:
                            registry_device = device
                            break
        except Exception:
            pass

    # Try to get name from device_info first, then fall back to registry
    name = get_device_info_attr(device_info, "name")
    if not name and registry_device is not None:
        name = registry_device.name
    if name:
        config["name"] = name

    # Try to get manufacturer from device_info first, then fall back to registry
    manufacturer = get_device_info_attr(device_info, "manufacturer")
    if not manufacturer and registry_device is not None:
        manufacturer = registry_device.manufacturer
    if manufacturer:
        config["manufacturer"] = manufacturer

    # Try to get model from device_info first, then fall back to registry
    model = get_device_info_attr(device_info, "model")
    if not model and registry_device is not None:
        model = registry_device.model
    if model:
        config["model"] = model

    # Try to get sw_version from device_info first, then fall back to registry
    sw_version = get_device_info_attr(device_info, "sw_version")
    if not sw_version and registry_device is not None:
        sw_version = registry_device.sw_version
    if sw_version:
        config["sw_version"] = sw_version

    return config


def get_entity_name_for_discovery(
    entity_name: Optional[str],
    device_info: Any,
    has_entity_name: bool = False,
) -> Optional[str]:
    """Get entity name for MQTT discovery, stripping device name prefix if present.

    This helper strips the device name prefix from entity names to avoid redundancy
    in Home Assistant's UI. For example, if a device is named "Living Room" and
    an entity is named "Living Room Temperature", the returned name will be
    "Temperature".

    If the entity name is exactly the same as the device name, returns None
    to indicate the entity should use the device name directly (HA convention).

    When has_entity_name is True (modern naming), the entity name is already a suffix
    and should be returned as-is (or None if it matches the device name).

    Args:
        entity_name: The entity name (full name for legacy, suffix for has_entity_name=True).
        device_info: Device info containing the device name.
        has_entity_name: Whether the entity uses modern naming (name is a suffix).

    Returns:
        The entity name for discovery, or None if entity name matches device name.
    """
    if not entity_name:
        # When has_entity_name is True, None means use device name (HA convention)
        # When has_entity_name is False, we should still return None (no name available)
        return None

    device_name = get_device_info_attr(device_info, "name")
    if not device_name:
        return entity_name

    # For modern naming (has_entity_name=True), the entity name is already a suffix
    if has_entity_name:
        # If entity name equals device name exactly, return None (HA convention)
        if entity_name == device_name:
            return None
        return entity_name

    # Legacy naming: entity name includes device name prefix
    # If entity name equals device name exactly, return None (HA convention)
    if entity_name == device_name:
        return None

    # Strip device name prefix if present (e.g., "Device Name Entity Name" -> "Entity Name")
    if entity_name.startswith(f"{device_name} "):
        return entity_name[len(f"{device_name} ") :]

    return entity_name


class EntityCategory(StrEnum):
    """Category of an entity.

    An entity with a category will:
    - Not be exposed to cloud, Alexa, or Google Assistant components
    - Not be included in indirect service calls to `all` or `none`
    """

    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class RegistryEntry:
    """Entity registry entry compatible with HA's entity registry."""

    def __init__(self, entity_id: str, unique_id: str, config_entry_id: str, disabled: bool = False):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id
        self.disabled = disabled


class EntityRegistry:
    """Registry for tracking entities."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._entities: Dict[str, "Entity"] = {}
            cls._instance._entries_by_config_entry: Dict[str, List[RegistryEntry]] = {}
            cls._instance._hass = None
        return cls._instance

    def setup(self, hass):
        """Setup registry with hass instance."""
        self._hass = hass

    def register(self, entity: "Entity") -> None:
        """Register an entity."""
        self._entities[entity.entity_id] = entity
        # Track by config entry ID if available
        config_entry_id = getattr(entity, "_attr_config_entry_id", None)
        if config_entry_id:
            entry = RegistryEntry(
                entity_id=entity.entity_id,
                unique_id=getattr(entity, "unique_id", entity.entity_id),
                config_entry_id=config_entry_id,
                disabled=not getattr(entity, "entity_registry_enabled_default", True),
            )
            if config_entry_id not in self._entries_by_config_entry:
                self._entries_by_config_entry[config_entry_id] = []
            self._entries_by_config_entry[config_entry_id].append(entry)
        _LOGGER.debug(f"Registered entity {entity.entity_id}")

    def unregister(self, entity_id: str) -> None:
        """Unregister an entity."""
        if entity_id in self._entities:
            entity = self._entities[entity_id]
            config_entry_id = getattr(entity, "_attr_config_entry_id", None)
            if config_entry_id and config_entry_id in self._entries_by_config_entry:
                self._entries_by_config_entry[config_entry_id] = [
                    e for e in self._entries_by_config_entry[config_entry_id]
                    if e.entity_id != entity_id
                ]
            del self._entities[entity_id]
            _LOGGER.debug(f"Unregistered entity {entity_id}")

    def get(self, entity_id: str) -> Optional["Entity"]:
        """Get entity by ID."""
        return self._entities.get(entity_id)

    def get_all(self) -> List["Entity"]:
        """Get all registered entities."""
        return list(self._entities.values())

    def async_entries_for_config_entry(self, config_entry_id: str) -> List[RegistryEntry]:
        """Return registry entries for a config entry."""
        return list(self._entries_by_config_entry.get(config_entry_id, []))

    def async_update_entity(
        self, entity_id: str, *, name: str = None, icon: str = None, **kwargs
    ):
        """Update entity properties."""
        entity = self._entities.get(entity_id)
        if entity:
            if name is not None:
                entity._attr_name = name
            if icon is not None:
                entity._attr_icon = icon
            # Update any other attributes
            for key, value in kwargs.items():
                if hasattr(entity, key):
                    setattr(entity, key, value)
        return entity


class Entity:
    """Base class for all entities."""

    # Class attributes (can be accessed via Entity.hass, Entity.platform)
    hass: Optional[Any] = None
    platform: Optional[Any] = None

    # Entity properties
    entity_id: Optional[str] = None
    _attr_name: Optional[str] = None
    _attr_unique_id: Optional[str] = None
    _attr_device_info: Optional[Dict[str, Any]] = None
    _attr_device_class: Optional[str] = None
    _attr_icon: Optional[str] = None
    _attr_unit_of_measurement: Optional[str] = None
    _attr_extra_state_attributes: Optional[Dict[str, Any]] = None
    _attr_entity_category: Optional[str] = None
    _attr_available: bool = True
    _attr_should_poll: bool = False
    _attr_force_update: bool = False
    _attr_integration_domain: Optional[str] = None
    _unrecorded_attributes: frozenset = frozenset()

    def __init__(self):
        """Initialize the entity."""
        self._added = False
        self._available = True

    @property
    def name(self) -> Optional[str]:
        """Return the name of the entity."""
        if self._attr_name is not None:
            return self._attr_name
        # Check entity_description for name
        if hasattr(self, "entity_description") and self.entity_description is not None:
            # First check for explicit name
            name = getattr(self.entity_description, "name", None)
            if name:
                return name
            # Fall back to translation_key converted to readable name
            translation_key = getattr(self.entity_description, "translation_key", None)
            if translation_key:
                return self._translation_key_to_name(translation_key)
            # Fall back to key converted to readable name
            key = getattr(self.entity_description, "key", None)
            if key:
                return self._translation_key_to_name(key)
        # Fall back to _attr_translation_key if set
        translation_key = getattr(self, "_attr_translation_key", None)
        if translation_key:
            return self._translation_key_to_name(translation_key)
        return None

    def _translation_key_to_name(self, translation_key: str) -> str:
        """Convert a translation key to a human-readable name.

        Example: "outlet_temperature" -> "Outlet Temperature"
        """
        # Replace underscores with spaces and title case
        return translation_key.replace("_", " ").title()

    @property
    def unique_id(self) -> Optional[str]:
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def mqtt_object_id(self) -> Optional[str]:
        """Return the MQTT-safe object_id for this entity.

        This is used for looking up entities from MQTT topics where dots and
        underscores in the entity_id are converted to dashes.
        """
        if self.entity_id is None:
            return None
        return get_mqtt_object_id(self.entity_id)

    @property
    def device_info(self) -> Optional[Dict[str, Any]]:
        """Return device information about this entity."""
        return self._attr_device_info

    @property
    def integration_domain(self) -> Optional[str]:
        """Return the integration domain that created this entity."""
        return self._attr_integration_domain

    @property
    def device_class(self) -> Optional[str]:
        """Return the class of this device."""
        # Check for directly assigned device_class attribute first (integrations may set entity.device_class = "...")
        if "device_class" in self.__dict__:
            return self.__dict__["device_class"]
        # Check entity_description (integrations may set via entity_description.device_class)
        if hasattr(self, "entity_description") and self.entity_description is not None:
            return getattr(self.entity_description, "device_class", None)
        return self._attr_device_class

    @property
    def icon(self) -> Optional[str]:
        """Return the icon to use in the frontend."""
        # Check for directly assigned icon attribute first (integrations may set entity.icon = "...")
        if "icon" in self.__dict__:
            return self.__dict__["icon"]
        # Check entity_description (integrations may set via entity_description.icon)
        if hasattr(self, "entity_description") and self.entity_description is not None:
            return getattr(self.entity_description, "icon", None)
        return self._attr_icon

    @property
    def entity_category(self) -> Optional[str]:
        """Return the category of this entity."""
        # Check entity_description first (integrations may set via entity_description)
        if hasattr(self, "entity_description") and self.entity_description is not None:
            desc_category = getattr(self.entity_description, "entity_category", None)
            if desc_category is not None:
                return desc_category
        return self._attr_entity_category

    @property
    def has_entity_name(self) -> bool:
        """Return if the entity uses the new naming scheme with the device name.

        When True, the entity name is considered a suffix to the device name.
        For example, if the device is "Living Room" and the entity name is
        "Temperature", Home Assistant displays it as "Living Room Temperature".

        When False (legacy), the entity name is the full name.
        """
        # Check entity_description first (integrations may set via entity_description)
        if hasattr(self, "entity_description") and self.entity_description is not None:
            return getattr(self.entity_description, "has_entity_name", False)
        return getattr(self, "_attr_has_entity_name", False)

    @property
    def unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement of this entity."""
        return self._attr_unit_of_measurement

    @property
    def extra_state_attributes(self) -> Optional[Dict[str, Any]]:
        """Return entity specific state attributes."""
        return self._attr_extra_state_attributes

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state."""
        return self._attr_should_poll

    @property
    def force_update(self) -> bool:
        """Return True if state updates should be forced."""
        return self._attr_force_update

    @property
    def state(self) -> Any:
        """Return the state of the entity."""
        return None

    def _get_mqtt_base_topic(self) -> Optional[str]:
        """Get the base MQTT topic for this entity.

        Returns:
            The base topic (e.g., 'homeassistant/sensor/my-entity') or None if
            entity_id is not set.
        """
        if not self.entity_id:
            return None

        entity_id_clean = get_mqtt_entity_id(self.entity_id)
        platform = self.entity_id.split(".")[0] if "." in self.entity_id else "sensor"
        return f"homeassistant/{platform}/{entity_id_clean}"

    def _publish_mqtt_attributes(self) -> None:
        """Publish extra_state_attributes to MQTT if present.

        This helper can be called by platform-specific _mqtt_publish methods
        to handle attribute publishing consistently across all entity types.
        """
        if not self.extra_state_attributes:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        import json

        attr_topic = f"{base_topic}/attributes"
        mqtt.publish(
            attr_topic, json.dumps(self.extra_state_attributes), qos=0, retain=True
        )

        # Republish discovery config to ensure json_attributes_topic is registered
        # This handles the case where discovery was published before attributes existed
        if hasattr(self, "_publish_mqtt_discovery"):
            attr_key = "_mqtt_attrs_registered"
            if not getattr(self, attr_key, False):
                setattr(self, attr_key, True)
                self.hass.async_add_job(self._publish_mqtt_discovery)

    def _add_mqtt_attributes_to_config(self, config: Dict[str, Any]) -> None:
        """Add json_attributes_topic to discovery config if entity has attributes.

        Args:
            config: The MQTT discovery config dict to modify.
        """
        if not self.extra_state_attributes:
            return

        base_topic = self._get_mqtt_base_topic()
        if base_topic:
            config["json_attributes_topic"] = f"{base_topic}/attributes"

    async def _publish_mqtt_discovery(self) -> None:
        """Publish MQTT discovery config.

        This should be overridden by platform-specific entity classes.
        """
        pass

    def _mqtt_publish(self) -> None:
        """Publish state to MQTT.

        This generic implementation works for any entity with a proper entity_id.
        Platform-specific subclasses may override this for specialized behavior.
        """
        if not self.entity_id:
            return

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
            mqtt.publish(state_topic, str(state), qos=0, retain=True)

        # Publish attributes if present
        self._publish_mqtt_attributes()

    async def _publish_generic_mqtt_discovery(self) -> None:
        """Publish generic MQTT discovery config for any entity type.

        This method provides basic MQTT discovery for entities that don't have
        platform-specific discovery (like CoordinatorEntity-based sensors).
        It extracts the platform from the entity_id (e.g., 'sensor.xyz' -> 'sensor').
        """
        if not self.entity_id:
            return

        if not hasattr(self.hass, "_mqtt_client"):
            return

        mqtt = self.hass._mqtt_client
        if not mqtt.is_connected():
            return

        base_topic = self._get_mqtt_base_topic()
        if not base_topic:
            return

        discovery_topic = f"{base_topic}/config"

        # Build basic discovery config
        config = {
            "name": get_entity_name_for_discovery(
                self.name, self.device_info, self.has_entity_name
            ),
            "unique_id": get_mqtt_safe_unique_id(self.unique_id),
            "state_topic": f"{base_topic}/state",
        }

        if self.device_info:
            config["device"] = build_mqtt_device_config(self.device_info)

        if self.device_class:
            config["device_class"] = self.device_class

        if self.icon:
            config["icon"] = self.icon

        if self.unit_of_measurement:
            config["unit_of_measurement"] = self.unit_of_measurement

        if self.entity_category:
            config["entity_category"] = self.entity_category

        # Add attributes topic if present
        self._add_mqtt_attributes_to_config(config)

        # Publish discovery
        import json

        mqtt.publish(discovery_topic, json.dumps(config), qos=0, retain=True)

    def _check_and_publish_discovery_update(self, properties: list[str]) -> bool:
        """Check if any discovery properties changed and republish if needed.

        This helper should be called from _mqtt_publish to detect when new
        properties (like icon, state_class, etc.) become available after
        the initial discovery was published.

        Args:
            properties: List of property names to check (e.g., ["icon", "state_class"])

        Returns:
            True if discovery was republished, False otherwise.
        """
        # Track which properties have been registered
        if not hasattr(self, "_mqtt_discovery_props_registered"):
            self._mqtt_discovery_props_registered = set()

        registered = self._mqtt_discovery_props_registered
        needs_update = False

        for prop in properties:
            if prop not in registered:
                value = getattr(self, prop, None)
                if value is not None:
                    registered.add(prop)
                    needs_update = True

        if needs_update:
            self.hass.async_add_job(self._publish_mqtt_discovery)
            return True

        return False

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        pass

    def async_on_remove(self, func):
        """Add a function to call when entity is removed from hass."""
        if not hasattr(self, "_on_remove_callbacks"):
            self._on_remove_callbacks = []
        self._on_remove_callbacks.append(func)
        return func

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        # Call registered removal callbacks
        if hasattr(self, "_on_remove_callbacks"):
            for callback in self._on_remove_callbacks:
                if callable(callback):
                    callback()
            self._on_remove_callbacks.clear()

    async def async_update(self) -> None:
        """Fetch new state data for the entity."""
        pass

    def async_write_ha_state(self) -> None:
        """Write state to the state machine."""
        _LOGGER.debug(
            f"async_write_ha_state called for {self.entity_id} (type: {type(self).__name__})"
        )
        if not self.hass or not self.entity_id:
            _LOGGER.debug(
                f"  Skipping: hass={self.hass is not None}, entity_id={self.entity_id}"
            )
            return

        state = self.state
        _LOGGER.debug(f"  State value: {state}")
        _LOGGER.debug(f"  Has _mqtt_publish: {hasattr(self, '_mqtt_publish')}")
        if state is None:
            state = STATE_UNAVAILABLE

        # Build attributes
        attributes = {}
        if self.device_class:
            attributes["device_class"] = self.device_class
        if self.icon:
            attributes["icon"] = self.icon
        if self.unit_of_measurement:
            attributes["unit_of_measurement"] = self.unit_of_measurement
        if self.extra_state_attributes:
            attributes.update(self.extra_state_attributes)

        # Set state in state machine
        self.hass.states.async_set(
            self.entity_id,
            str(state) if state is not None else STATE_UNAVAILABLE,
            attributes=attributes,
            force_update=self.force_update,
        )

        # Also publish to MQTT if platform is set up
        if hasattr(self, "_mqtt_publish"):
            _LOGGER.debug(f"  Calling _mqtt_publish for {self.entity_id}")
            try:
                self._mqtt_publish()
            except Exception as e:
                _LOGGER.error(f"  Error in _mqtt_publish for {self.entity_id}: {e}")
        else:
            _LOGGER.debug(f"  No _mqtt_publish method for {self.entity_id}")

    async def async_remove(
        self, *, force_remove: bool = False, cleanup_mqtt: bool = True
    ) -> None:
        """Remove entity from Home Assistant.

        Args:
            force_remove: Force removal even if entity wasn't properly added.
            cleanup_mqtt: Whether to clean up MQTT topics. Set to False during
                         server shutdown to preserve topics for reconnection.
        """
        _LOGGER.debug(
            f"async_remove called for {self.entity_id} (cleanup_mqtt={cleanup_mqtt})"
        )

        # Check if entity was properly added (some integration entities may not have _added)
        is_added = getattr(self, "_added", False)

        if is_added:
            await self.async_will_remove_from_hass()

        # Remove from state machine
        if self.hass and self.entity_id:
            self.hass.states.async_remove(self.entity_id)
            _LOGGER.debug(f"Removed {self.entity_id} from state machine")

        # Clean up MQTT topics only if explicitly requested
        if cleanup_mqtt:
            await self._cleanup_mqtt()
        else:
            _LOGGER.debug(f"Skipping MQTT cleanup for {self.entity_id}")

        # Unregister from registry
        registry = EntityRegistry()
        registry.unregister(self.entity_id)

        self._added = False
        _LOGGER.debug(f"async_remove completed for {self.entity_id}")

    async def _cleanup_mqtt(self) -> None:
        """Clean up MQTT discovery topics when entity is removed."""
        _LOGGER.debug(f"_cleanup_mqtt called for {self.entity_id}")

        if not self.hass or not self.entity_id:
            _LOGGER.debug(
                f"  Skipping cleanup: hass={self.hass is not None}, entity_id={self.entity_id}"
            )
            return

        if not hasattr(self.hass, "_mqtt_client"):
            _LOGGER.debug(f"  Skipping cleanup: no _mqtt_client attribute")
            return

        mqtt = self.hass._mqtt_client
        entity_id_clean = get_mqtt_entity_id(self.entity_id)

        # Determine platform from entity_id (e.g., "sensor.xyz" -> "sensor")
        platform = self.entity_id.split(".")[0] if "." in self.entity_id else "sensor"

        # Delete discovery config topic (empty payload with retain=True removes it)
        discovery_topic = f"homeassistant/{platform}/{entity_id_clean}/config"
        mqtt.publish(discovery_topic, "", qos=0, retain=True)
        _LOGGER.debug(f"  Published empty payload to {discovery_topic}")

        # Clear state topic
        state_topic = f"homeassistant/{platform}/{entity_id_clean}/state"
        mqtt.publish(state_topic, "", qos=0, retain=True)
        _LOGGER.debug(f"  Published empty payload to {state_topic}")

        # Clear attributes topic
        attr_topic = f"homeassistant/{platform}/{entity_id_clean}/attributes"
        mqtt.publish(attr_topic, "", qos=0, retain=True)
        _LOGGER.debug(f"  Published empty payload to {attr_topic}")

        _LOGGER.debug(f"Cleaned up MQTT topics for {self.entity_id}")

    def add_to_platform_start(
        self,
        hass,
        platform,
        parallel_updates,
    ) -> None:
        """Start adding an entity to a platform."""
        self.hass = hass
        self.platform = platform

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled by default."""
        # Check instance attribute for disabled_by_default (runtime override)
        if getattr(self, "_attr_disabled_by_default", False):
            return False
        # Check entity_description for disabled_by_default (rinnai pattern)
        if hasattr(self, "entity_description") and self.entity_description is not None:
            if getattr(self.entity_description, "disabled_by_default", False):
                return False
            # Check entity_registry_enabled_default directly (meross_lan pattern)
            value = getattr(
                self.entity_description, "entity_registry_enabled_default", None
            )
            if value is not None:
                return value
        # Fall back to class attribute for entity_registry_enabled_default
        return getattr(self, "_attr_entity_registry_enabled_default", True)

    async def add_to_platform_finish(self) -> None:
        """Finish adding an entity to a platform."""
        # Check if entity is disabled by default
        if not self.entity_registry_enabled_default:
            _LOGGER.debug(f"Skipping disabled entity {self.entity_id}")
            return

        self._added = True

        # Register with entity registry
        registry = EntityRegistry()
        registry.setup(self.hass)
        registry.register(self)

        # Register entity mapping
        if self.unique_id:
            self.hass.states.async_register_entity_id(self.unique_id, self.entity_id)
            self.hass._storage.register_entity(
                entity_id=self.entity_id,
                unique_id=self.unique_id,
                platform=self.platform.platform_name if self.platform else None,
                device_id=self.device_info.get("identifiers", [[None]])[0][1]
                if self.device_info
                else None,
            )

        await self.async_added_to_hass()

        # Publish MQTT discovery if entity supports it
        if hasattr(self, "_publish_mqtt_discovery"):
            # Check if this is a platform-specific implementation (not the base class no-op)
            # by checking if the method is overridden in a subclass
            is_platform_specific = (
                type(self)._publish_mqtt_discovery is not Entity._publish_mqtt_discovery
            )
            if is_platform_specific:
                _LOGGER.debug(
                    f"Publishing MQTT discovery for {self.entity_id} (platform-specific)"
                )
                await self._publish_mqtt_discovery()
            else:
                # Use generic discovery for base Entity classes (like CoordinatorEntity)
                _LOGGER.debug(f"Publishing generic MQTT discovery for {self.entity_id}")
                await self._publish_generic_mqtt_discovery()

        self.async_write_ha_state()

    def schedule_update_ha_state(self, force_refresh: bool = False) -> None:
        """Schedule an update of the HA state."""
        if self.hass:
            self.hass.async_add_job(self.async_update_ha_state)

    async def async_update_ha_state(self, force_refresh: bool = False) -> None:
        """Update HA state."""
        if force_refresh:
            await self.async_update()
        self.async_write_ha_state()


class ToggleEntity(Entity):
    """Base class for toggle entities (switch, light, etc.)."""

    _attr_is_on: Optional[bool] = None

    @property
    def is_on(self) -> Optional[bool]:
        """Return True if entity is on."""
        return self._attr_is_on

    @property
    def state(self) -> Optional[str]:
        """Return the state."""
        if self.is_on is None:
            return None
        return "on" if self.is_on else "off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        raise NotImplementedError()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        raise NotImplementedError()

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on (sync version)."""
        raise NotImplementedError()

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off (sync version)."""
        raise NotImplementedError()


class DysonEntity(Entity):
    """Base class for Dyson entities (from ha-dyson)."""

    _MESSAGE_TYPE = None

    def __init__(self, device, name: str):
        """Initialize the entity."""
        super().__init__()
        self._device = device
        self._attr_name = name
        self._attr_unique_id = f"{device.serial}-{self.__class__.__name__.lower()}"

        # Set up device info
        self._attr_device_info = {
            "identifiers": {(device.serial,)},
            "name": name,
            "manufacturer": "Dyson",
            "model": device.device_type,
        }

        if hasattr(device, "version"):
            self._attr_device_info["sw_version"] = device.version

    async def async_added_to_hass(self):
        """Call when entity is added."""
        self._device.add_message_listener(self._on_message)

    async def async_will_remove_from_hass(self):
        """Call when entity is removed."""
        self._device.remove_message_listener(self._on_message)

    def _on_message(self, message):
        """Handle new messages."""
        _LOGGER.debug(
            f"DysonEntity _on_message: entity={self.entity_id}, msg_type={self._MESSAGE_TYPE}, msg={message.get('msg')}"
        )
        if self._MESSAGE_TYPE is not None:
            if message.get("msg") == self._MESSAGE_TYPE:
                _LOGGER.debug(
                    f"DysonEntity calling async_write_ha_state for {self.entity_id}"
                )
                self.async_write_ha_state()
        else:
            _LOGGER.debug(
                f"DysonEntity calling async_write_ha_state for {self.entity_id} (no msg type filter)"
            )
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device.connected if hasattr(self._device, "connected") else True
