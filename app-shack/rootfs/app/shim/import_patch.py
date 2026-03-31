"""Import patching for Home Assistant compatibility.

Patches sys.modules to redirect homeassistant imports to the shim.
"""

import sys
import types
from pathlib import Path
from typing import Optional

from .logging import get_logger

_LOGGER = get_logger(__name__)


class ImportPatcher:
    """Manages import patching for HA compatibility."""

    def __init__(self, hass):
        self._hass = hass
        self._original_modules = {}
        self._patched = False

    def patch(self) -> None:
        """Patch sys.modules to redirect HA imports."""
        if self._patched:
            return

        _LOGGER.info("Patching imports for Home Assistant compatibility")

        # Import shim modules
        from . import core
        from . import const
        from . import entity
        from . import exceptions
        from . import config_entries
        from . import selectors

        # Import types here since it's used below
        import types

        # Create homeassistant package
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.core = core
        homeassistant.const = const
        homeassistant.exceptions = exceptions
        homeassistant.config_entries = config_entries

        # Create data_entry_flow stub module
        data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
        data_entry_flow.FlowHandler = config_entries.ConfigFlow
        data_entry_flow.FlowResult = config_entries.FlowResult
        data_entry_flow.FlowResultType = config_entries.FlowResultType
        data_entry_flow.AbortFlow = type("AbortFlow", (Exception,), {})
        homeassistant.data_entry_flow = data_entry_flow
        sys.modules["homeassistant.data_entry_flow"] = data_entry_flow

        homeassistant.helpers = types.ModuleType("homeassistant.helpers")
        homeassistant.helpers.selector = selectors
        homeassistant.util = types.ModuleType("homeassistant.util")
        homeassistant.components = types.ModuleType("homeassistant.components")

        # Create device_registry stub
        device_registry = types.ModuleType("homeassistant.helpers.device_registry")
        device_registry.async_get = lambda *args, **kwargs: None
        device_registry.DeviceEntry = type("DeviceEntry", (), {})
        device_registry.DeviceRegistry = type("DeviceRegistry", (), {})

        # DeviceInfo dataclass for integrations
        from dataclasses import dataclass, field
        from typing import Optional, List

        @dataclass
        class DeviceInfo:
            """Device info for entities."""

            identifiers: set = field(default_factory=set)
            connections: set = field(default_factory=set)
            manufacturer: Optional[str] = None
            model: Optional[str] = None
            name: Optional[str] = None
            sw_version: Optional[str] = None
            hw_version: Optional[str] = None
            via_device: Optional[tuple] = None
            entry_type: Optional[str] = None
            configuration_url: Optional[str] = None
            suggested_area: Optional[str] = None

        device_registry.DeviceInfo = DeviceInfo
        device_registry.format_mac = lambda x: x
        device_registry.async_entries_for_config_entry = lambda *args, **kwargs: []
        device_registry.async_get_device = lambda *args, **kwargs: None

        homeassistant.helpers.device_registry = device_registry
        sys.modules["homeassistant.helpers.device_registry"] = device_registry

        # Import entity helper from shim.entity
        from . import entity

        homeassistant.helpers.entity = entity
        sys.modules["homeassistant.helpers.entity"] = entity

        # Create config_validation stub module
        config_validation = types.ModuleType("homeassistant.helpers.config_validation")
        config_validation.string = lambda x: x
        config_validation.boolean = lambda x: x
        config_validation.integer = lambda x: x
        config_validation.float = lambda x: x
        config_validation.positive_int = lambda x: x
        config_validation.positive_float = lambda x: x
        config_validation.time_period = lambda x: x
        config_validation.datetime = lambda x: x
        config_validation.date = lambda x: x
        config_validation.time = lambda x: x

        # Latitude and longitude validators
        def latitude_validator(value):
            """Validate latitude is between -90 and 90."""
            try:
                lat = float(value)
                if -90 <= lat <= 90:
                    return lat
                raise ValueError(f"Latitude must be between -90 and 90, got {value}")
            except (TypeError, ValueError):
                raise ValueError(f"Invalid latitude: {value}")

        def longitude_validator(value):
            """Validate longitude is between -180 and 180."""
            try:
                lon = float(value)
                if -180 <= lon <= 180:
                    return lon
                raise ValueError(f"Longitude must be between -180 and 180, got {value}")
            except (TypeError, ValueError):
                raise ValueError(f"Invalid longitude: {value}")

        config_validation.latitude = latitude_validator
        config_validation.longitude = longitude_validator

        homeassistant.helpers.config_validation = config_validation
        sys.modules["homeassistant.helpers.config_validation"] = config_validation

        # Create entity_platform stub module
        entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
        entity_platform.async_get_platforms = lambda *args, **kwargs: []
        entity_platform.AddEntitiesCallback = lambda *args, **kwargs: None

        # current_platform should be an object with a .get() method
        # that returns a platform object with async_register_entity_service
        class _MockPlatform:
            def async_register_entity_service(self, *args, **kwargs):
                pass

        class _CurrentPlatform:
            def get(self):
                return _MockPlatform()

        entity_platform.current_platform = _CurrentPlatform()

        homeassistant.helpers.entity_platform = entity_platform
        sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

        # Create update_coordinator stub module
        from .entity import Entity
        from typing import Generic, TypeVar

        update_coordinator = types.ModuleType(
            "homeassistant.helpers.update_coordinator"
        )

        # Global registry to track all coordinators by domain
        # This allows force-shutdown even if integration doesn't clean up properly
        _coordinator_registry = {}

        def _register_coordinator(coordinator):
            """Register a coordinator in the global registry."""
            domain = None
            if coordinator.config_entry:
                domain = coordinator.config_entry.domain
            if domain:
                if domain not in _coordinator_registry:
                    _coordinator_registry[domain] = []
                _coordinator_registry[domain].append(coordinator)
                _LOGGER.debug(
                    f"Registered coordinator '{coordinator.name}' for domain '{domain}'"
                )

        def _unregister_coordinator(coordinator):
            """Unregister a coordinator from the global registry."""
            domain = None
            if coordinator.config_entry:
                domain = coordinator.config_entry.domain
            if domain and domain in _coordinator_registry:
                if coordinator in _coordinator_registry[domain]:
                    _coordinator_registry[domain].remove(coordinator)
                    _LOGGER.debug(
                        f"Unregistered coordinator '{coordinator.name}' for domain '{domain}'"
                    )

        def shutdown_coordinators_for_domain(domain):
            """Force shutdown all coordinators for a domain."""
            if domain in _coordinator_registry:
                coordinators = _coordinator_registry[domain].copy()
                count = len(coordinators)
                for coordinator in coordinators:
                    if not coordinator._shutdown_requested:
                        _LOGGER.info(
                            f"Force shutting down coordinator '{coordinator.name}' for disabled integration '{domain}'"
                        )
                        # Use async_create_task to call async_shutdown
                        coordinator.hass.async_create_task(coordinator.async_shutdown())
                    _unregister_coordinator(coordinator)
                if count > 0:
                    _LOGGER.info(
                        f"Shutdown {count} coordinator(s) for domain '{domain}'"
                    )
                del _coordinator_registry[domain]

        # Expose the shutdown function
        update_coordinator._shutdown_coordinators_for_domain = (
            shutdown_coordinators_for_domain
        )

        # Create a generic DataUpdateCoordinator that supports subscripting
        T = TypeVar("T")

        class DataUpdateCoordinator(Generic[T]):
            """Stub DataUpdateCoordinator that supports generic subscripting."""

            def __init__(
                self,
                hass,
                logger,
                *,
                name,
                update_method=None,
                update_interval=None,
                config_entry=None,
            ):
                self.hass = hass
                self.logger = logger
                self.name = name
                self.update_method = update_method
                self.update_interval = update_interval
                # If config_entry not provided, get from context variable (like real HA)
                if config_entry is None:
                    from homeassistant.config_entries import current_entry

                    self.config_entry = current_entry.get()
                else:
                    self.config_entry = config_entry
                self.data = None
                self._shutdown_requested = False
                self._last_update_success = True
                self._listeners = {}
                self._last_listener_id = 0
                self._unsub_refresh = None

                # Register this coordinator
                _register_coordinator(self)

                # Start periodic updates if interval is set
                if update_interval:
                    self._schedule_refresh()

            def _schedule_refresh(self):
                """Schedule the next refresh."""
                import asyncio

                if self._shutdown_requested or not self.update_interval:
                    return

                if self._unsub_refresh:
                    self._unsub_refresh.cancel()

                async def _refresh():
                    await asyncio.sleep(self.update_interval.total_seconds())
                    if not self._shutdown_requested:
                        await self.async_refresh()
                        self._schedule_refresh()

                self._unsub_refresh = self.hass.async_create_task(_refresh())

            def async_add_listener(self, update_callback, context=None):
                """Add a listener for data updates."""
                self._last_listener_id += 1
                listener_id = self._last_listener_id
                self._listeners[listener_id] = (update_callback, context)

                def remove_listener():
                    self._listeners.pop(listener_id, None)
                    if not self._listeners:
                        self._unschedule_refresh()

                return remove_listener

            def _unschedule_refresh(self):
                """Unschedule any pending refresh."""
                if self._unsub_refresh:
                    self._unsub_refresh.cancel()
                    self._unsub_refresh = None

            def async_update_listeners(self):
                """Update all registered listeners."""
                for update_callback, _ in list(self._listeners.values()):
                    update_callback()

            async def async_shutdown(self):
                """Cancel any scheduled refresh and ignore new runs."""
                self._shutdown_requested = True
                self._unschedule_refresh()
                _unregister_coordinator(self)

            async def _async_update_data(self):
                """Fetch the latest data from the source.

                Can be overridden by subclasses instead of using update_method.
                """
                if self.update_method is None:
                    raise NotImplementedError("Update method not implemented")
                return await self.update_method()

            async def async_refresh(self):
                """Refresh data and log errors."""
                if self._shutdown_requested:
                    return

                try:
                    self.data = await self._async_update_data()
                    self._last_update_success = True
                    self.async_update_listeners()
                except Exception as e:
                    self.logger.error(f"Error fetching {self.name} data: {e}")
                    self._last_update_success = False
                    raise

            async def async_request_refresh(self):
                """Request a refresh."""
                await self.async_refresh()

            async def async_config_entry_first_refresh(self):
                """Perform first refresh on config entry setup."""
                await self.async_refresh()

            @property
            def last_update_success(self):
                """Return True if last update was successful."""
                return self._last_update_success

        update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
        update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})

        # CoordinatorEntity base class - make it generic like DataUpdateCoordinator
        from typing import TypeVar, Generic

        _T = TypeVar("_T")

        class CoordinatorEntity(Generic[_T], Entity):
            """A base class for entities using DataUpdateCoordinator."""

            def __init__(self, coordinator):
                self.coordinator = coordinator
                self.coordinator_context = None

            async def async_added_to_hass(self):
                """When entity is added to hass."""
                await super().async_added_to_hass()
                self.async_on_remove(
                    self.coordinator.async_add_listener(
                        self._handle_coordinator_update, self.coordinator_context
                    )
                )

            def _handle_coordinator_update(self):
                """Handle updated data from the coordinator."""
                self.async_write_ha_state()

            @property
            def available(self):
                return self.coordinator.last_update_success

        update_coordinator.CoordinatorEntity = CoordinatorEntity
        homeassistant.helpers.update_coordinator = update_coordinator
        sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator

        # Add core classes
        homeassistant.HomeAssistant = core.HomeAssistant
        homeassistant.ConfigEntry = core.ConfigEntry
        homeassistant.ServiceRegistry = core.ServiceRegistry
        homeassistant.State = core.State
        homeassistant.StateMachine = core.StateMachine
        homeassistant.ConfigEntries = core.ConfigEntries

        # Store reference to our hass instance
        homeassistant.core._shim_instance = self._hass

        # Save original modules
        self._original_modules["homeassistant"] = sys.modules.get("homeassistant")

        # Install patched modules
        sys.modules["homeassistant"] = homeassistant
        sys.modules["homeassistant.core"] = core
        sys.modules["homeassistant.const"] = const
        sys.modules["homeassistant.exceptions"] = exceptions
        sys.modules["homeassistant.config_entries"] = config_entries
        sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
        sys.modules["homeassistant.helpers"] = homeassistant.helpers
        sys.modules["homeassistant.helpers.selector"] = selectors
        sys.modules["homeassistant.helpers.entity"] = homeassistant.helpers.entity
        sys.modules["homeassistant.helpers.device_registry"] = device_registry
        sys.modules["homeassistant.util"] = homeassistant.util
        sys.modules["homeassistant.components"] = homeassistant.components

        # Patch platforms
        self._patch_platforms(homeassistant)

        # Create stub modules
        self._create_stubs(homeassistant)

        self._patched = True
        _LOGGER.info("Import patching complete")

    def _patch_platforms(self, homeassistant) -> None:
        """Patch entity platform modules."""
        from . import platforms

        # Create platform modules
        homeassistant.components.fan = platforms.fan
        homeassistant.components.sensor = platforms.sensor
        homeassistant.components.switch = platforms.switch
        homeassistant.components.light = platforms.light
        homeassistant.components.climate = platforms.climate
        homeassistant.components.binary_sensor = platforms.binary_sensor
        homeassistant.components.update = platforms.update
        homeassistant.components.select = platforms.select
        homeassistant.components.button = platforms.button
        homeassistant.components.device_tracker = platforms.device_tracker
        homeassistant.components.text = platforms.text

        # Add to sys.modules
        sys.modules["homeassistant.components.fan"] = platforms.fan
        sys.modules["homeassistant.components.sensor"] = platforms.sensor
        sys.modules["homeassistant.components.switch"] = platforms.switch
        sys.modules["homeassistant.components.light"] = platforms.light
        sys.modules["homeassistant.components.climate"] = platforms.climate
        sys.modules["homeassistant.components.binary_sensor"] = platforms.binary_sensor
        sys.modules["homeassistant.components.update"] = platforms.update
        sys.modules["homeassistant.components.select"] = platforms.select
        sys.modules["homeassistant.components.button"] = platforms.button
        sys.modules["homeassistant.components.device_tracker"] = (
            platforms.device_tracker
        )
        sys.modules["homeassistant.components.device_tracker.config_entry"] = (
            platforms.device_tracker.config_entry
        )
        sys.modules["homeassistant.components.device_tracker.const"] = (
            platforms.device_tracker.const
        )
        sys.modules["homeassistant.components.text"] = platforms.text

        _LOGGER.debug("Platform modules patched")

    def _create_stubs(self, homeassistant) -> None:
        """Create stub modules for HA dependencies."""
        # Create zeroconf stub
        zeroconf_stub = types.ModuleType("homeassistant.components.zeroconf")
        zeroconf_stub.Zeroconf = lambda *args, **kwargs: None
        zeroconf_stub.async_get_instance = lambda *args, **kwargs: None
        zeroconf_stub.HaZeroconf = lambda *args, **kwargs: None

        homeassistant.components.zeroconf = zeroconf_stub
        sys.modules["homeassistant.components.zeroconf"] = zeroconf_stub

        # Create homeassistant.util.percentage stub
        percentage_stub = types.ModuleType("homeassistant.util.percentage")
        percentage_stub.int_states_in_range = lambda *args, **kwargs: 100
        percentage_stub.percentage_to_ranged_value = lambda *args, **kwargs: 0
        percentage_stub.ranged_value_to_percentage = lambda *args, **kwargs: 0

        homeassistant.util.percentage = percentage_stub
        sys.modules["homeassistant.util.percentage"] = percentage_stub

        _LOGGER.debug("Stub modules created")

    def unpatch(self) -> None:
        """Restore original modules."""
        if not self._patched:
            return

        _LOGGER.info("Restoring original imports")

        for name, module in self._original_modules.items():
            if module is None:
                if name in sys.modules:
                    del sys.modules[name]
            else:
                sys.modules[name] = module

        self._patched = False


def setup_import_patching(hass) -> ImportPatcher:
    """Setup and return import patcher.

    Usage:
        patcher = setup_import_patching(hass)
        patcher.patch()

        # Now you can import HA integrations
        from custom_components.dyson_local import fan

        # Later, if needed:
        # patcher.unpatch()
    """
    return ImportPatcher(hass)
