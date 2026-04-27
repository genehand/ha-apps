"""Registry classes for Home Assistant Shim.

Manages states, services, config entries, and config flows.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union
from datetime import datetime

from .models import State, ConfigEntry, ServiceCall, Context, _slugify_name
from .storage import Storage
from .logging import get_logger

if TYPE_CHECKING:
    from .hass import HomeAssistant

_LOGGER = get_logger(__name__)


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
        schema: Optional[Any] = None,
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

    def async_update_entry(self, entry: ConfigEntry, **kwargs) -> bool:
        """Update an entry.

        Returns True if changes were made, False otherwise.
        """
        changed = False
        for key, value in kwargs.items():
            if hasattr(entry, key):
                current_value = getattr(entry, key)
                if current_value != value:
                    setattr(entry, key, value)
                    changed = True

        if changed:
            self._save_entries()
            _LOGGER.debug(f"Updated config entry {entry.entry_id}")
        return changed

    async def async_reload(self, entry_id: str) -> None:
        """Reload an entry."""
        # TODO: Implement reload logic
        _LOGGER.info(f"Reloading config entry {entry_id}")

    async def async_forward_entry_unload(
        self, entry: ConfigEntry, platform: str
    ) -> bool:
        """Unload a specific platform for a config entry.

        This is called by integrations during async_unload_entry to unload
        individual platforms (e.g., sensor, switch, light).

        Args:
            entry: The config entry to unload the platform for.
            platform: The platform domain to unload (e.g., "sensor", "light").

        Returns:
            True if successful, False otherwise.
        """
        try:
            # Check if shutdown is in progress (set by unload_integration during server shutdown)
            # If so, don't clean up MQTT topics to preserve them for reconnection
            shutdown_in_progress = getattr(
                self._hass, "_shim_shutdown_in_progress", False
            )
            cleanup_mqtt = not shutdown_in_progress
            _LOGGER.debug(
                f"async_forward_entry_unload: {platform} for {entry.entry_id}, "
                f"shutdown_in_progress={shutdown_in_progress}, cleanup_mqtt={cleanup_mqtt}"
            )

            # Get the loader from hass.data (set by ShimManager)
            loader = self._hass.data.get("integration_loader")
            if loader:
                # Remove entities for this specific platform/domain combination
                await loader._remove_platform_entities(
                    entry.domain, platform, cleanup_mqtt=cleanup_mqtt
                )
                _LOGGER.debug(f"Unloaded {platform} platform for {entry.entry_id}")
            return True
        except Exception as e:
            _LOGGER.warning(f"Failed to unload {platform} for {entry.entry_id}: {e}")
            return False

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
                    # Track tasks so we can await them before returning
                    _platform_tasks = []

                    # Create async_add_entities callback
                    # This needs to work both when awaited AND when called synchronously
                    # because some platform code has bugs where they don't await it
                    def async_add_entities(entities, update_before_add=False):
                        """Add entities to the platform."""
                        _entities_to_add.extend(entities)

                        # Log sub-entity creation for debugging
                        if len(entities) > 0:
                            first_entity = entities[0]
                            entity_class_name = type(first_entity).__name__
                            _LOGGER.debug(
                                f"async_add_entities called for {len(entities)} entities "
                                f"(first: {entity_class_name}) on {platform_name} platform"
                            )

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

                                # Note: We still publish disabled entities to MQTT
                                # so users can enable them in HA if needed.
                                # The platform's _publish_mqtt_discovery will set
                                # enabled_by_default: false for disabled entities.
                                if not entity_enabled:
                                    _LOGGER.debug(
                                        f"Entity {entity.entity_id} is disabled by default, "
                                        f"but still publishing to MQTT for optional enable"
                                    )

                                # Set integration domain and config entry for tracking
                                entity._attr_integration_domain = entry.domain
                                entity._attr_config_entry_id = entry.entry_id
                                # Register entity with loader under platform domain
                                entity_registered = True
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
                                    entity_registered = loader.register_entity(
                                        entity_domain, entity
                                    )

                                # Skip rest of setup if entity was filtered out
                                if not entity_registered:
                                    _LOGGER.debug(
                                        f"Entity {entity.entity_id} was filtered out, skipping MQTT discovery"
                                    )
                                    return

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

                        # Create task to run async work and store it so it doesn't get garbage collected
                        task = asyncio.create_task(_do_add())
                        _platform_tasks.append(task)
                        # Also store reference globally to ensure task completes even if caller doesn't await
                        _pending_tasks = getattr(
                            self._hass, "_pending_entity_tasks", []
                        )
                        _pending_tasks.append(task)
                        self._hass._pending_entity_tasks = _pending_tasks
                        # Clean up task when done
                        task.add_done_callback(
                            lambda t: self._hass._pending_entity_tasks.remove(t)
                            if t in getattr(self._hass, "_pending_entity_tasks", [])
                            else None
                        )
                        return task

                    await platform_module.async_setup_entry(
                        self._hass, entry, async_add_entities
                    )

                    # Wait for all entity registration tasks to complete before returning
                    if _platform_tasks:
                        await asyncio.gather(*_platform_tasks, return_exceptions=True)

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

        # Update flow context with any provided context (e.g. source from discovery)
        if context:
            flow_id = result.get("flow_id")
            flow = self._config_entries._flow_progress.get(flow_id)
            if flow:
                flow.context.update(context)

        # If there's data (discovery), continue with that step
        if data is not None:
            flow_id = result.get("flow_id")
            # Find the appropriate step method based on context source
            source = (context or {}).get("source", "user")
            step_method = f"async_step_{source}"

            flow = self._config_entries._flow_progress.get(flow_id)
            if flow and hasattr(flow, step_method):
                step_func = getattr(flow, step_method)
                result = await step_func(data)
                result["flow_id"] = flow_id

                # Auto-complete if the step returned a form/menu with no required fields
                # This handles discovery flows that just need confirmation (like meross_lan finalize step)
                if result.get("type") in ("form", "menu"):
                    data_schema = result.get("data_schema")
                    if data_schema is not None or result.get("type") == "menu":
                        try:
                            # Check if schema has any required fields
                            has_required = False
                            if data_schema is not None and hasattr(
                                data_schema, "schema"
                            ):
                                schema_dict = data_schema.schema
                                if isinstance(schema_dict, dict):
                                    for key in schema_dict.keys():
                                        # Check if key is a Required marker
                                        if hasattr(
                                            key, "__class__"
                                        ) and key.__class__.__name__ in ("Required",):
                                            has_required = True
                                            break

                            # If no required fields, auto-submit the form
                            if not has_required:
                                _LOGGER.debug(
                                    f"Auto-submitting {result.get('type')} for flow {flow_id}, step {result.get('step_id')}"
                                )
                                step_id = result.get("step_id", "user")
                                step_method = f"async_step_{step_id}"
                                if hasattr(flow, step_method):
                                    step_func = getattr(flow, step_method)
                                    result = await step_func({})  # Empty user input
                                    result["flow_id"] = flow_id
                        except Exception as e:
                            _LOGGER.warning(f"Error auto-submitting form: {e}")

        # If result is create_entry, actually create the config entry
        if result.get("type") == "create_entry":
            try:
                flow = self._config_entries._flow_progress.get(flow_id)
                if flow:
                    from .models import ConfigEntry

                    entry = ConfigEntry(
                        entry_id=flow_id,
                        version=getattr(flow, "VERSION", 1),
                        domain=flow.handler,
                        title=result.get("title", "Unknown"),
                        data=result.get("data", {}),
                    )
                    # Set unique_id if available
                    if flow.unique_id:
                        entry._unique_id = flow.unique_id
                    await self._config_entries.async_add(entry)
                    _LOGGER.debug(
                        f"Created config entry {entry.entry_id} for {flow.handler}"
                    )
                    # Clean up the flow
                    self._config_entries._flow_progress.pop(flow_id, None)

                    # Set up the integration to create entities
                    loader = self._hass.data.get("integration_loader")
                    if loader:
                        try:
                            await loader.setup_integration(entry)
                            _LOGGER.debug(
                                f"Set up integration {flow.handler} for entry {entry.entry_id}"
                            )
                        except Exception as e:
                            _LOGGER.error(
                                f"Error setting up integration {flow.handler}: {e}"
                            )
            except Exception as e:
                _LOGGER.error(f"Error creating config entry from flow: {e}")

        return result

    def _flow_to_progress(self, flow_or_dict: Any) -> dict:
        """Convert a flow object or dict to a progress dict.

        This handles both:
        - Dict entries created by async_create_flow()
        - ConfigFlow objects stored by start_config_flow()
        """
        if isinstance(flow_or_dict, dict):
            return flow_or_dict

        # It's a ConfigFlow object - extract the relevant attributes
        flow = flow_or_dict
        return {
            "flow_id": getattr(flow, "flow_id", None),
            "handler": getattr(flow, "handler", None),
            "context": getattr(flow, "context", {}),
            "data": getattr(flow, "data", {}),
        }

    def async_progress(self) -> List[dict]:
        """Return all in-progress config flows."""
        return [
            self._flow_to_progress(flow_data)
            for flow_data in self._config_entries._flow_progress.values()
        ]

    def async_progress_by_handler(
        self,
        handler: str,
        *,
        match_context: Optional[dict] = None,
        include_uninitialized: bool = False,
    ) -> List[dict]:
        """Return in-progress config flows for a specific handler (domain).

        Args:
            handler: The domain/handler to filter by
            match_context: Optional context keys to match
            include_uninitialized: Whether to include uninitialized flows

        Returns:
            List of flow progress dicts matching the criteria
        """
        result = []
        for flow_id, flow_data in self._config_entries._flow_progress.items():
            # Convert to progress dict if needed
            progress = self._flow_to_progress(flow_data)

            # Check if flow matches the handler
            if progress.get("handler") != handler:
                continue

            flow_context = progress.get("context", {})

            # If match_context is provided, check that all keys match
            if match_context:
                if not all(
                    flow_context.get(key) == value
                    for key, value in match_context.items()
                ):
                    continue

            result.append(progress)

        return result

    def async_abort(self, flow_id: str) -> dict:
        """Abort a config flow.

        Args:
            flow_id: The flow ID to abort

        Returns:
            Abort result dict
        """
        if flow_id in self._config_entries._flow_progress:
            del self._config_entries._flow_progress[flow_id]
            _LOGGER.debug(f"Aborted config flow {flow_id}")

        return {"type": "abort", "flow_id": flow_id}

    async def async_configure(
        self, flow_id: str, user_input: Optional[dict] = None
    ) -> dict:
        """Continue a config flow with user input.

        This is used by the OAuth2 callback to resume a flow with
        authorization code or error data.
        """
        from .integrations.loader import IntegrationLoader

        loader = self._hass.data.get("integration_loader")
        if not loader:
            _LOGGER.error("Integration loader not available")
            return {"type": "abort", "reason": "loader_not_available"}

        # Find the flow to get its handler (domain)
        flow = self._config_entries._flow_progress.get(flow_id)
        if not flow:
            _LOGGER.error(f"Flow {flow_id} not found")
            return {"type": "abort", "reason": "flow_not_found"}

        domain = getattr(flow, "handler", None)
        if not domain:
            _LOGGER.error(f"Flow {flow_id} has no handler")
            return {"type": "abort", "reason": "no_handler"}

        # Continue the config flow
        result = await loader.continue_config_flow(domain, flow_id, user_input or {})
        if not result:
            return {"type": "abort", "reason": "failed_to_continue"}

        return result
