"""Home Assistant Shim - Core Infrastructure.

Mocks Home Assistant's core classes for running HACS integrations outside HA.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar, Generic
from datetime import datetime
import json
from pathlib import Path
from enum import Enum

from .storage import Storage
from .logging import get_logger

_LOGGER = get_logger(__name__)

T = TypeVar("T")


def _slugify_name(name: str) -> str:
    """Create a safe entity_id slug from a name.

    Handles unicode characters by normalizing and mapping to ASCII.
    Converts to lowercase and replaces spaces/dashes with underscores.
    Removes other punctuation.
    """
    import re
    import unicodedata

    # Map common unicode punctuation to ASCII equivalents
    unicode_map = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": ",",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2026": "...",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }

    text = str(name)

    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)

    # Map unicode chars to ASCII
    for unicode_char, ascii_char in unicode_map.items():
        text = text.replace(unicode_char, ascii_char)

    # Encode to ASCII, dropping remaining non-ASCII
    text = text.encode("ascii", "ignore").decode("ascii")

    # Convert to lowercase and replace spaces/dashes with underscores
    text = re.sub(r"[-\s]+", "_", text.strip().lower())

    # Remove any remaining non-word characters (except underscores)
    text = re.sub(r"[^\w]", "", text)

    return text


class SupportsResponse(Enum):
    """Enum for service call response support."""

    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


def callback(func: Callable) -> Callable:
    """Decorator to mark a function as safe to be called from within the event loop.

    This is used to mark functions that should be run in the event loop but are not async.
    """
    setattr(func, "_hass_callback", True)
    return func


@dataclass
class ConfigEntry:
    """Configuration entry for an integration."""

    entry_id: str
    version: int
    domain: str
    title: str
    data: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    pref_disable_new_entities: bool = False
    source: str = "user"
    runtime_data: Any = None
    # Additional fields for compatibility with newer HA integrations
    minor_version: int = 1
    discovery_keys: Any = field(default_factory=dict)
    subentries_data: tuple = field(default_factory=tuple)
    _unique_id: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize callbacks list after creation."""
        self._on_unload_callbacks: List[Callable] = []

    @property
    def unique_id(self) -> Optional[str]:
        """Return unique ID from field or data."""
        if self._unique_id is not None:
            return self._unique_id
        return self.data.get("unique_id")

    @unique_id.setter
    def unique_id(self, value: Optional[str]) -> None:
        """Set unique ID."""
        self._unique_id = value

    def async_on_unload(self, callback: Callable) -> Callable:
        """Register a callback to be called when the entry is unloaded.

        Returns the callback for use as a decorator.
        """
        self._on_unload_callbacks.append(callback)
        return callback

    def add_update_listener(self, listener: Callable) -> Callable:
        """Add a listener for options updates.

        Returns a function that can be called to remove the listener.
        """
        if not hasattr(self, "_update_listeners"):
            self._update_listeners: List[Callable] = []
        self._update_listeners.append(listener)

        def remove_listener():
            if listener in self._update_listeners:
                self._update_listeners.remove(listener)

        return remove_listener

    async def _run_unload_callbacks(self) -> None:
        """Run all registered unload callbacks."""
        for callback in self._on_unload_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                _LOGGER.error(f"Error in unload callback: {e}")


@dataclass
class State:
    """Represents an entity state."""

    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    last_changed: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    context: Optional[dict] = field(default_factory=dict)

    def __post_init__(self):
        if not self.last_changed:
            self.last_changed = datetime.now()
        if not self.last_updated:
            self.last_updated = datetime.now()


@dataclass
class Context:
    """Context of a service call."""

    id: str = field(default_factory=lambda: str(id(object())))
    user_id: Optional[str] = None
    parent_id: Optional[str] = None


@dataclass
class ServiceCall:
    """Represents a service call.

    This is passed to service handlers and contains all the information
    about the service being called.
    """

    domain: str
    service: str
    data: Dict[str, Any] = field(default_factory=dict)
    target: Optional[Dict[str, Any]] = None
    context: Optional[Context] = None

    def __getitem__(self, key: str) -> Any:
        """Allow accessing data dict directly."""
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Get value from data dict with default."""
        return self.data.get(key, default)


# ServiceResponse type - typically a dict response from services
ServiceResponse = Dict[str, Any]


class StateMachine:
    """Manages entity states."""

    def __init__(self, hass: HomeAssistant):
        self._hass = hass
        self._states: Dict[str, State] = {}
        self._listeners: List[Callable[[str, State, State], None]] = []
        self._entity_ids: Dict[str, str] = {}  # unique_id -> entity_id mapping

    def async_entity_ids(self, domain: Optional[str] = None) -> List[str]:
        """Return all entity IDs, optionally filtered by domain."""
        if domain:
            return [eid for eid in self._states.keys() if eid.startswith(f"{domain}.")]
        return list(self._states.keys())

    def get(self, entity_id: str) -> Optional[State]:
        """Get state for entity."""
        return self._states.get(entity_id)

    def async_set(
        self,
        entity_id: str,
        new_state: str,
        attributes: Optional[dict] = None,
        force_update: bool = False,
        context: Optional[dict] = None,
    ) -> None:
        """Set entity state."""
        old_state = self._states.get(entity_id)

        if old_state and old_state.state == new_state and not force_update:
            # No change, just update attributes if provided
            if attributes:
                old_state.attributes.update(attributes)
                old_state.last_updated = datetime.now()
            return

        new_state_obj = State(
            entity_id=entity_id,
            state=new_state,
            attributes=attributes or (old_state.attributes if old_state else {}),
            context=context,
        )

        self._states[entity_id] = new_state_obj

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(entity_id, old_state, new_state_obj)
            except Exception as e:
                _LOGGER.error(f"Error in state listener: {e}")

    def async_remove(self, entity_id: str) -> None:
        """Remove entity state."""
        if entity_id in self._states:
            del self._states[entity_id]

    def async_register_entity_id(self, unique_id: str, entity_id: str) -> None:
        """Register unique_id to entity_id mapping."""
        self._entity_ids[unique_id] = entity_id

    def async_get_entity_id(self, unique_id: str) -> Optional[str]:
        """Get entity_id from unique_id."""
        return self._entity_ids.get(unique_id)

    def async_add_listener(
        self, listener: Callable[[str, State, State], None]
    ) -> Callable:
        """Add state change listener. Returns remove function."""
        self._listeners.append(listener)

        def remove_listener():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener


class ServiceRegistry:
    """Manages services."""

    def __init__(self, hass: HomeAssistant):
        self._hass = hass
        self._services: Dict[
            str, Dict[str, Callable]
        ] = {}  # domain -> {service: handler}

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: Optional[dict] = None,
        blocking: bool = False,
        context: Optional[dict] = None,
    ) -> None:
        """Call a service."""
        if domain not in self._services or service not in self._services[domain]:
            _LOGGER.warning(f"Service {domain}.{service} not found")
            return

        handler = self._services[domain][service]
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(service_data or {})
            else:
                handler(service_data or {})
        except Exception as e:
            _LOGGER.error(f"Error calling service {domain}.{service}: {e}")

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Callable,
        supports_response: Optional[Any] = None,
    ) -> None:
        """Register a service."""
        if domain not in self._services:
            self._services[domain] = {}

        self._services[domain][service] = service_func
        _LOGGER.debug(f"Registered service {domain}.{service}")


class ConfigEntries:
    """Manages config entries."""

    def __init__(self, hass: HomeAssistant, storage: Storage):
        self._hass = hass
        self._storage = storage
        self._entries: Dict[str, List[ConfigEntry]] = {}  # domain -> [entries]
        self._flow_progress: Dict[str, dict] = {}  # flow_id -> flow_data
        self._flow_id_counter = 0
        self._flow_manager = FlowManager(hass, self)

        self._load_entries()

    def _load_entries(self) -> None:
        """Load entries from storage."""
        data = self._storage.load_entries()
        for domain, entries_data in data.items():
            self._entries[domain] = [
                ConfigEntry(**entry_data) for entry_data in entries_data
            ]

    def _save_entries(self) -> None:
        """Save entries to storage."""
        data = {}
        for domain, entries in self._entries.items():
            data[domain] = [
                {
                    "entry_id": e.entry_id,
                    "version": e.version,
                    "domain": e.domain,
                    "title": e.title,
                    "data": e.data,
                    "options": e.options,
                    "pref_disable_new_entities": e.pref_disable_new_entities,
                    "source": e.source,
                }
                for e in entries
            ]
        self._storage.save_entries(data)

    def async_entries(self, domain: Optional[str] = None) -> List[ConfigEntry]:
        """Return all entries, optionally filtered by domain."""
        if domain:
            return self._entries.get(domain, [])

        all_entries = []
        for entries in self._entries.values():
            all_entries.extend(entries)
        return all_entries

    def async_get_entry(self, entry_id: str) -> Optional[ConfigEntry]:
        """Get entry by ID."""
        for entries in self._entries.values():
            for entry in entries:
                if entry.entry_id == entry_id:
                    return entry
        return None

    async def async_add(self, entry: ConfigEntry) -> None:
        """Add a new entry."""
        if entry.domain not in self._entries:
            self._entries[entry.domain] = []

        self._entries[entry.domain].append(entry)
        self._save_entries()
        _LOGGER.info(f"Added config entry {entry.entry_id} for {entry.domain}")

    async def async_remove(self, entry_id: str) -> None:
        """Remove an entry."""
        for domain, entries in self._entries.items():
            for i, entry in enumerate(entries):
                if entry.entry_id == entry_id:
                    entries.pop(i)
                    self._save_entries()
                    _LOGGER.info(f"Removed config entry {entry_id}")
                    return

    async def async_update_entry(self, entry: ConfigEntry, **kwargs) -> None:
        """Update an entry."""
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)

        self._save_entries()
        _LOGGER.debug(f"Updated config entry {entry.entry_id}")

    async def async_reload(self, entry_id: str) -> None:
        """Reload an entry."""
        # TODO: Implement reload logic
        _LOGGER.info(f"Reloading config entry {entry_id}")

    def async_progress(self) -> List[dict]:
        """Return in-progress config flows."""
        return list(self._flow_progress.values())

    def async_create_flow(
        self,
        domain: str,
        context: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> str:
        """Create a new config flow."""
        self._flow_id_counter += 1
        flow_id = f"flow_{self._flow_id_counter}"

        self._flow_progress[flow_id] = {
            "flow_id": flow_id,
            "handler": domain,
            "context": context or {},
            "data": data or {},
        }

        return flow_id

    async def async_configure(
        self, flow_id: str, user_input: Optional[dict] = None
    ) -> dict:
        """Configure a flow step."""
        # This would be called by the integration's config flow
        # For now, return a placeholder
        if flow_id not in self._flow_progress:
            raise ValueError(f"Flow {flow_id} not found")

        return self._flow_progress[flow_id]

    async def async_forward_entry_setups(
        self, entry: ConfigEntry, platforms: List[str]
    ) -> None:
        """Forward entry setup to platforms."""
        _LOGGER.debug(f"Setting up platforms {platforms} for {entry.domain}")

        # Ensure custom_components parent is in path for platform imports
        custom_components_parent = str(self._hass.shim_dir)
        if custom_components_parent not in sys.path:
            sys.path.insert(0, custom_components_parent)
            _LOGGER.debug(
                f"Added {custom_components_parent} to sys.path for platform imports"
            )

        for platform in platforms:
            # Handle Platform enum values (e.g., Platform.SENSOR) by extracting the string value
            platform_name = (
                platform.value if hasattr(platform, "value") else str(platform)
            )
            _LOGGER.debug(f"Setting up platform {platform_name} for {entry.domain}")
            # Check if the platform file exists before trying to import
            # The custom_components should be in data/shim/custom_components
            platform_file = (
                self._hass.shim_dir
                / "custom_components"
                / entry.domain
                / f"{platform_name}.py"
            )
            _LOGGER.debug(f"Looking for platform file at: {platform_file}")
            _LOGGER.debug(f"File exists: {platform_file.exists()}")

            # Import and setup the platform module
            try:
                platform_module = importlib.import_module(
                    f"custom_components.{entry.domain}.{platform_name}"
                )
                if hasattr(platform_module, "async_setup_entry"):
                    # Track entities that need to be added
                    _entities_to_add = []

                    # Create async_add_entities callback
                    # This needs to work both when awaited AND when called synchronously
                    # because some platform code has bugs where they don't await it
                    def async_add_entities(entities, update_before_add=False):
                        """Add entities to the platform."""
                        _entities_to_add.extend(entities)

                        # Schedule async work in background
                        async def _do_add():
                            for entity in entities:
                                # Generate entity_id if not set
                                _LOGGER.debug(
                                    f"Entity {type(entity).__name__}: entity_id={getattr(entity, 'entity_id', None)}, unique_id={getattr(entity, 'unique_id', None)}, name={getattr(entity, 'name', None)}"
                                )
                                if not entity.entity_id:
                                    # Generate from platform and unique_id or name
                                    unique_id = getattr(entity, "unique_id", None)
                                    name = getattr(entity, "name", None) or getattr(
                                        entity, "_attr_name", None
                                    )
                                    _LOGGER.debug(
                                        f"Generating entity_id for {type(entity).__name__}: unique_id={unique_id}, name={name}"
                                    )
                                    if unique_id:
                                        entity.entity_id = (
                                            f"{platform_name}.{unique_id}"
                                        )
                                        _LOGGER.debug(
                                            f"Generated entity_id from unique_id: {entity.entity_id}"
                                        )
                                    elif name:
                                        # Clean up name for entity_id
                                        clean_name = _slugify_name(name)
                                        entity.entity_id = f"{platform}.{clean_name}"
                                        _LOGGER.debug(
                                            f"Generated entity_id from name: {entity.entity_id}"
                                        )
                                    else:
                                        # Fallback
                                        entity.entity_id = (
                                            f"{platform}.unknown_{id(entity)}"
                                        )

                                _LOGGER.debug(
                                    f"Adding entity {entity.entity_id} to {platform} platform"
                                )

                                # Check if entity is disabled by default
                                entity_enabled = getattr(
                                    entity, "entity_registry_enabled_default", True
                                )
                                if (
                                    hasattr(entity, "entity_description")
                                    and entity.entity_description is not None
                                ):
                                    desc_enabled = getattr(
                                        entity.entity_description,
                                        "entity_registry_enabled_default",
                                        None,
                                    )
                                    if desc_enabled is not None:
                                        entity_enabled = desc_enabled

                                if not entity_enabled:
                                    _LOGGER.debug(
                                        f"Skipping disabled entity {entity.entity_id}"
                                    )
                                    continue

                                # Set integration domain for tracking
                                entity._attr_integration_domain = entry.domain
                                # Register entity with loader under platform domain
                                if (
                                    hasattr(self._hass, "data")
                                    and "integration_loader" in self._hass.data
                                ):
                                    loader = self._hass.data["integration_loader"]
                                    entity_domain = (
                                        entity.entity_id.split(".")[0]
                                        if hasattr(entity, "entity_id")
                                        else entry.domain
                                    )
                                    loader.register_entity(entity_domain, entity)
                                # Add to entity registry
                                from .entity import EntityRegistry

                                registry = EntityRegistry()
                                registry.register(entity)
                                # Set up entity in hass
                                entity.hass = self._hass
                                _LOGGER.debug(
                                    f"Calling async_added_to_hass for {entity.entity_id} (type: {type(entity).__name__})"
                                )
                                await entity.async_added_to_hass()
                                _LOGGER.debug(
                                    f"async_added_to_hass completed for {entity.entity_id}"
                                )
                                # Trigger state write to publish initial state
                                # This is needed for coordinator-based entities where data
                                # was fetched before the entity was added
                                entity.async_write_ha_state()
                                _LOGGER.debug(
                                    f"Initial state written for {entity.entity_id}"
                                )
                                # Publish MQTT discovery if entity supports it
                                from .entity import Entity

                                if hasattr(entity, "_publish_mqtt_discovery"):
                                    # Check if this is a platform-specific implementation
                                    is_platform_specific = (
                                        type(entity)._publish_mqtt_discovery
                                        is not Entity._publish_mqtt_discovery
                                    )
                                    if is_platform_specific:
                                        _LOGGER.debug(
                                            f"Publishing MQTT discovery for {entity.entity_id} (platform-specific)"
                                        )
                                        await entity._publish_mqtt_discovery()
                                    else:
                                        # Use generic discovery for base Entity classes
                                        _LOGGER.debug(
                                            f"Publishing generic MQTT discovery for {entity.entity_id}"
                                        )
                                        await entity._publish_generic_mqtt_discovery()
                                    _LOGGER.debug(
                                        f"MQTT discovery published for {entity.entity_id}"
                                    )

                        # Create task to run async work
                        task = asyncio.create_task(_do_add())
                        return task

                    await platform_module.async_setup_entry(
                        self._hass, entry, async_add_entities
                    )

                    # Wait for any entities that were queued (in case they didn't await)
                    if _entities_to_add:
                        await asyncio.sleep(0.1)  # Give tasks time to start

                    _LOGGER.debug(
                        f"Platform {platform} setup complete for {entry.domain}"
                    )
            except ImportError as e:
                import traceback

                _LOGGER.warning(
                    f"Platform {platform} import failed for {entry.domain}: {e}"
                )
                _LOGGER.debug(f"Import traceback:\n{traceback.format_exc()}")
                _LOGGER.debug(f"sys.path at import time: {sys.path}")
            except Exception as e:
                _LOGGER.error(
                    f"Failed to setup platform {platform} for {entry.domain}: {e}"
                )

    async def async_unload_platforms(
        self, entry: ConfigEntry, platforms: List[str]
    ) -> bool:
        """Unload platforms for an entry."""
        _LOGGER.debug(f"Unloading platforms {platforms} for {entry.domain}")
        return True

    @property
    def flow(self):
        """Return the flow manager."""
        return self._flow_manager


class FlowManager:
    """Manages config entry flows."""

    def __init__(self, hass: HomeAssistant, config_entries: ConfigEntries):
        self._hass = hass
        self._config_entries = config_entries

    async def async_init(
        self,
        domain: str,
        context: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> dict:
        """Initialize a new config flow."""
        from .integrations.loader import IntegrationLoader

        # Get the integration loader from hass data
        loader = self._hass.data.get("integration_loader")
        if not loader:
            _LOGGER.error("Integration loader not available")
            return {"type": "abort", "reason": "loader_not_available"}

        # Start the config flow
        result = await loader.start_config_flow(domain)
        if not result:
            return {"type": "abort", "reason": "failed_to_start"}

        # If there's data (discovery), continue with that step
        if data is not None and result.get("type") == "form":
            flow_id = result.get("flow_id")
            # Find the appropriate step method based on context source
            source = (context or {}).get("source", "user")
            step_method = f"async_step_{source}"

            flow = self._config_entries._flow_progress.get(flow_id)
            if flow and hasattr(flow, step_method):
                step_func = getattr(flow, step_method)
                result = await step_func(data)
                result["flow_id"] = flow_id

        return result


class HomeAssistant:
    """Mock Home Assistant core object."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)

        # Create config directory if it doesn't exist
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config = MockConfig(self.config_dir)

        # Create shim directory (create parents too if needed)
        self.shim_dir = self.config_dir / "shim"
        self.shim_dir.mkdir(parents=True, exist_ok=True)

        # Initialize storage
        self._storage = Storage(self.shim_dir)

        # Core systems
        self.states = StateMachine(self)
        self.services = ServiceRegistry(self)
        self.config_entries = ConfigEntries(self, self._storage)

        # Data storage for integrations
        self.data: Dict[str, Any] = {}

        # Event bus
        self._event_listeners: Dict[str, List[Callable]] = {}

        # Bus
        self.bus = MockEventBus(self)

        # Store the event loop at initialization time
        # This is needed for integrations that use run_coroutine_threadsafe from other threads
        self._loop = asyncio.get_event_loop()

        _LOGGER.debug("HomeAssistant shim initialized")

    @property
    def loop(self):
        """Return the event loop.

        This is used by some integrations that use asyncio.run_coroutine_threadsafe().
        Returns the loop that was active when HomeAssistant was created.
        """
        return self._loop

    async def async_add_executor_job(self, target: Callable, *args) -> Any:
        """Run function in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, target, *args)

    def async_run_job(self, target: Callable[..., Any], *args: Any) -> Any:
        """Run a job (coroutine or function) in the event loop.

        This is thread-safe - it can be called from any thread.
        If called from the event loop thread, it schedules immediately.
        If called from another thread, it uses run_coroutine_threadsafe.
        """
        # Check if we're in the event loop thread safely (without get_event_loop())
        try:
            current_loop = asyncio.get_running_loop()
            in_event_loop = current_loop == self._loop
        except RuntimeError:
            # No running loop in this thread
            in_event_loop = False

        if asyncio.iscoroutine(target) or asyncio.iscoroutinefunction(target):
            coro = target(*args) if asyncio.iscoroutinefunction(target) else target
            if in_event_loop:
                # We're in the event loop thread
                return asyncio.ensure_future(coro)
            else:
                # We're in a different thread (e.g., paho-mqtt callback)
                return asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            # Synchronous function - run in executor
            return self._loop.run_in_executor(None, functools.partial(target, *args))

    def async_create_task(self, target: asyncio.coroutine) -> asyncio.Task:
        """Create a task."""
        return asyncio.create_task(target)

    def async_add_job(self, target: Callable, *args) -> asyncio.Future:
        """Add a job."""
        return self.async_run_job(target, *args)

    def async_fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event."""
        listeners = self._event_listeners.get(event_type, [])
        for listener in listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    asyncio.create_task(listener(event_data))
                else:
                    listener(event_data)
            except Exception as e:
                _LOGGER.error(f"Error in event listener: {e}")

    def async_track_state_change(
        self,
        entity_ids: List[str],
        action: Callable,
    ) -> Callable:
        """Track state changes for entities."""

        def state_listener(entity_id: str, old_state: State, new_state: State):
            if entity_id in entity_ids:
                action(entity_id, old_state, new_state)

        return self.states.async_add_listener(state_listener)


class MockConfig:
    """Mock config object."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.path = lambda *parts: str(config_dir.joinpath(*parts))
        # Default location settings (can be overridden)
        self.latitude = 0.0
        self.longitude = 0.0
        self.elevation = 0
        self.unit_system = "metric"
        self.time_zone = "UTC"
        self.external_url = None
        self.internal_url = None


class MockEventBus:
    """Mock event bus."""

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    def async_listen(self, event_type: str, listener: Callable) -> Callable:
        """Listen for events."""
        if event_type not in self._hass._event_listeners:
            self._hass._event_listeners[event_type] = []

        self._hass._event_listeners[event_type].append(listener)

        def remove():
            if listener in self._hass._event_listeners.get(event_type, []):
                self._hass._event_listeners[event_type].remove(listener)

        return remove

    def async_fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event."""
        self._hass.async_fire(event_type, event_data)

    def fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event (synchronous version)."""
        self._hass.async_fire(event_type, event_data)

    def async_listen_once(self, event_type: str, listener: Callable) -> Callable:
        """Listen for an event once."""

        def wrapped_listener(event_data):
            remove_listener()
            return listener(event_data)

        if event_type not in self._hass._event_listeners:
            self._hass._event_listeners[event_type] = []

        self._hass._event_listeners[event_type].append(wrapped_listener)

        def remove_listener():
            if wrapped_listener in self._hass._event_listeners.get(event_type, []):
                self._hass._event_listeners[event_type].remove(wrapped_listener)

        return remove_listener
