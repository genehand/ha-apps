"""Home Assistant update_coordinator stub module.

Provides DataUpdateCoordinator, CoordinatorEntity, and UpdateFailed.
"""

import sys
import types
from typing import Generic, TypeVar, Optional
import asyncio

from ..logging import get_logger

_LOGGER = get_logger(__name__)

# Global registry to track all coordinators by domain
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
                coordinator.hass.async_create_task(coordinator.async_shutdown())
            _unregister_coordinator(coordinator)
        if count > 0:
            _LOGGER.info(
                f"Shutdown {count} coordinator(s) for domain '{domain}'"
            )
        del _coordinator_registry[domain]


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
        self.data = {}
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
        _LOGGER.debug(
            f"DataUpdateCoordinator '{self.name}' updating {len(self._listeners)} listeners"
        )
        for update_callback, _ in list(self._listeners.values()):
            try:
                update_callback()
            except Exception as e:
                import traceback
                callback_name = getattr(update_callback, '__name__', repr(update_callback))
                _LOGGER.error(
                    f"Error in listener callback {callback_name} for coordinator '{self.name}': {e}"
                )
                _LOGGER.debug(f"Listener callback traceback: {traceback.format_exc()}")

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
            _LOGGER.debug(
                f"Coordinator '{self.name}' refresh skipped: shutdown requested"
            )
            return

        try:
            _LOGGER.debug(f"Coordinator '{self.name}' fetching data...")
            self.data = await self._async_update_data()
            self._last_update_success = True
            _LOGGER.debug(
                f"Coordinator '{self.name}' data fetched successfully: {self.data}"
            )
            self.async_update_listeners()
        except (UnboundLocalError, NameError) as e:
            # Handle buggy integrations that use 'raise X from error' where
            # 'error' variable doesn't exist
            error_str = str(e)
            if "error" in error_str.lower() and (
                "not associated" in error_str or "not defined" in error_str
            ):
                msg = f"Integration bug: tried to chain from undefined error variable in {self.name}"
                self.logger.error(f"Error fetching {self.name} data: {msg}")
                self._last_update_success = False
                raise UpdateFailed(msg) from e
            # Re-raise if it's not the specific pattern we're handling
            self.logger.error(f"Error fetching {self.name} data: {e}")
            self._last_update_success = False
            raise
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

    def async_set_updated_data(self, data):
        """Manually update data, notify listeners and reset update failure."""
        self.data = data
        self._last_update_success = True
        self.async_update_listeners()

    @property
    def last_update_success(self):
        """Return True if last update was successful."""
        return self._last_update_success


class UpdateFailed(Exception):
    """Exception to indicate an update failure.

    Handles malformed messages that integrations might pass,
    such as printf-style format strings without proper formatting.
    """

    def __init__(self, message, *args, **kwargs):
        # Handle the case where message is a printf-style format string
        # but args weren't properly passed (common integration bug)
        if isinstance(message, str) and "%" in message and not args:
            # Convert to a safe string without formatting
            message = (
                message.replace("%s", "?").replace("%d", "?").replace("%r", "?")
            )
        # Handle the normal case with proper formatting
        elif args:
            try:
                message = message % args
            except (TypeError, ValueError):
                # If formatting fails, use the raw message
                pass
        super().__init__(message)


_T = TypeVar("_T")


class CoordinatorEntity(Generic[_T]):
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
        _LOGGER.debug(
            f"CoordinatorEntity {getattr(self, 'entity_id', 'unknown')} handling coordinator update"
        )
        self.async_write_ha_state()

    @property
    def available(self):
        return self.coordinator.last_update_success


def create_coordinator_stubs(hass, homeassistant, entity_module):
    """Create update_coordinator stub module and register in sys.modules."""
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    # Expose the shutdown function
    update_coordinator._shutdown_coordinators_for_domain = shutdown_coordinators_for_domain

    # Create a proper CoordinatorEntity class with Entity as base
    _T = TypeVar("_T")

    class _CoordinatorEntityImpl(Generic[_T], entity_module.Entity):
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
            _LOGGER.debug(
                f"CoordinatorEntity {getattr(self, 'entity_id', 'unknown')} handling coordinator update"
            )
            self.async_write_ha_state()

        @property
        def available(self):
            return self.coordinator.last_update_success

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.CoordinatorEntity = _CoordinatorEntityImpl

    homeassistant.helpers.update_coordinator = update_coordinator
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator

    return homeassistant
