"""Config entries shim for Home Assistant.

Provides classes for config flow handling.
"""

from enum import Enum
from typing import Any, Optional, Dict, Callable
from dataclasses import dataclass
from contextvars import ContextVar

from shim.ha_fetched.exceptions import HomeAssistantError
from .core import ConfigEntry
from .core import callback

# Context variable to track the current config entry being set up
current_entry: ContextVar[Optional[ConfigEntry]] = ContextVar(
    "current_entry", default=None
)


# Config entry source constants
SOURCE_USER = "user"
SOURCE_ZEROCONF = "zeroconf"
SOURCE_SSDP = "ssdp"
SOURCE_DISCOVERY = "discovery"
SOURCE_DHCP = "dhcp"
SOURCE_BLUETOOTH = "bluetooth"
SOURCE_MQTT = "mqtt"
SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"
SOURCE_HASSIO = "hassio"
SOURCE_SYSTEM = "system"
SOURCE_REAUTH = "reauth"
SOURCE_RECONFIGURE = "reconfigure"
SOURCE_IMPORT = "import"
SOURCE_IGNORE = "ignore"
SOURCE_STORAGE = "storage"
SOURCE_ONBOARDING = "onboarding"

# Connection class constants (deprecated but still used by integrations)
CONN_CLASS_CLOUD_POLL = "cloud_poll"
CONN_CLASS_CLOUD_PUSH = "cloud_push"
CONN_CLASS_LOCAL_POLL = "local_poll"
CONN_CLASS_LOCAL_PUSH = "local_push"
CONN_CLASS_ASSUMED = "assumed"


class ConfigEntryState(Enum):
    """Config entry state."""

    SETUP_IN_PROGRESS = "setup_in_progress"
    SETUP_RETRY = "setup_retry"
    SETUP_ERROR = "setup_error"
    LOADED = "loaded"
    FAILED_UNLOAD = "failed_unload"
    NOT_LOADED = "not_loaded"
    MIGRATION_ERROR = "migration_error"


class ConfigFlowMeta(type):
    """Metaclass for ConfigFlow to handle domain keyword argument."""

    def __new__(mcs, name, bases, namespace, domain=None, **kwargs):
        """Create new class, handling domain keyword argument."""
        # Handle any keyword arguments that Home Assistant passes
        # domain is used to register the config flow handler
        cls = super().__new__(mcs, name, bases, namespace)
        if domain is not None:
            cls.handler = domain
        return cls

    def __init__(cls, name, bases, namespace, domain=None, **kwargs):
        """Initialize the class."""
        super().__init__(name, bases, namespace)


class ConfigFlow(metaclass=ConfigFlowMeta):
    """Base class for config flows."""

    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_LOCAL_PUSH
    handler = None

    def __init__(self):
        """Initialize config flow."""
        self.hass = None
        self.handler = None
        self.flow_id = None
        self.context = {}
        self._errors = {}
        self.cur_step_id = "user"  # Track current step for multi-step flows
        self._show_advanced_options = False  # Advanced options display setting

    @property
    def unique_id(self) -> Optional[str]:
        """Return the unique ID of the flow from context."""
        return self.context.get("unique_id")

    def __getattr__(self, name: str) -> any:
        """Provide default values for attributes that may not be set.

        This handles subclasses that don't call super().__init__().
        """
        if name == "context":
            # Initialize context on first access if not set
            self.context = {}
            return self.context
        if name == "_show_advanced_options":
            self._show_advanced_options = False
            return self._show_advanced_options
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    @property
    def show_advanced_options(self) -> bool:
        """Return if advanced options should be shown.

        Checks context first, then falls back to internal flag.
        """
        # Check context for advanced mode (set by UI)
        # Handle case where __init__ wasn't called (subclass didn't call super())
        context = getattr(self, "context", {})
        if context.get("show_advanced_options"):
            return True
        return getattr(self, "_show_advanced_options", False)

    @show_advanced_options.setter
    def show_advanced_options(self, value: bool) -> None:
        """Set advanced options display setting."""
        self._show_advanced_options = value

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step."""
        raise NotImplementedError()

    async def async_step_import(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)

    def async_show_form(
        self,
        step_id: str,
        data_schema=None,
        errors: Optional[Dict[str, str]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
    ):
        """Return the form definition."""
        # Track current step for multi-step flows
        self.cur_step_id = step_id
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
        }

    def async_show_menu(
        self,
        step_id: str,
        menu_options: list,
        description_placeholders: Optional[Dict[str, str]] = None,
    ):
        """Return a menu selection step definition."""
        self.cur_step_id = step_id
        return {
            "type": "menu",
            "step_id": step_id,
            "menu_options": menu_options,
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(
        self,
        title: str,
        data: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
    ):
        """Create config entry."""
        # Include unique_id from flow context if set (needed by meross_lan and others)
        unique_id = self.context.get("unique_id")
        if unique_id and "unique_id" not in data:
            data = {**data, "unique_id": unique_id}

        result = {
            "type": "create_entry",
            "title": title,
            "data": data,
        }
        if options:
            result["options"] = options
        return result

    def async_abort(self, reason: str):
        """Abort config flow."""
        return {
            "type": "abort",
            "reason": reason,
        }

    def add_suggested_values_to_schema(self, data_schema, suggested_values):
        """Add suggested values to a data schema.

        This is used to pre-populate form fields with default/suggested values.
        """
        if suggested_values is None:
            return data_schema

        # If it's a voluptuous schema, we can't easily modify it
        # Just return it as-is for now
        # In a full implementation, this would wrap validators with defaults
        return data_schema

    def async_external_step(self, step_id: str, url: str):
        """Return external step definition."""
        return {
            "type": "external",
            "step_id": step_id,
            "url": url,
        }

    def async_external_step_done(self, next_step_id: str):
        """Return external step done."""
        return {
            "type": "external_done",
            "next_step_id": next_step_id,
        }

    async def async_set_unique_id(
        self,
        unique_id: Optional[str] = None,
        *,
        raise_on_progress: bool = True,
    ) -> Optional[Any]:
        """Set unique id and check if already configured.

        Args:
            unique_id: The unique ID to set
            raise_on_progress: If True, abort if another flow is in progress with same ID

        Returns:
            Existing ConfigEntry if one with same unique_id exists, None otherwise
        """
        # Store in context (used by meross_lan and other integrations)
        self.context["unique_id"] = unique_id

        # Check if there's already an entry with this unique_id
        if unique_id is not None and self.hass:
            entries = self.hass.config_entries.async_entries(self.handler)
            for entry in entries:
                if entry.unique_id == unique_id:
                    return entry

        return None

    def _abort_if_unique_id_configured(
        self,
        updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Abort if unique_id is already configured."""
        # This would check config entries
        # For now, do nothing (let it proceed)
        pass

    def _async_current_entries(self):
        """Return current entries."""
        if self.hass:
            return self.hass.config_entries.async_entries(self.handler)
        return []

    def _async_current_ids(self):
        """Return current entry IDs."""
        entries = self._async_current_entries()
        return [entry.unique_id for entry in entries if entry.unique_id]


class OptionsFlow:
    """Base class for options flows."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self.hass = None
        self._show_advanced_options = False  # Advanced options display setting

    @property
    def show_advanced_options(self) -> bool:
        """Return if advanced options should be shown."""
        return self._show_advanced_options

    @show_advanced_options.setter
    def show_advanced_options(self, value: bool) -> None:
        """Set advanced options display setting."""
        self._show_advanced_options = value

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Manage the options."""
        raise NotImplementedError()

    def async_show_form(
        self,
        step_id: str,
        data_schema=None,
        errors: Optional[Dict[str, str]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
    ):
        """Return the form definition."""
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(
        self, title: str, data: Dict[str, Any], options: Optional[Dict[str, Any]] = None
    ):
        """Create config entry."""
        result = {
            "type": "create_entry",
            "title": title,
            "data": data,
        }
        if options:
            result["options"] = options
        return result


class OptionsFlowWithConfigEntry(OptionsFlow):
    """Options flow with config entry access.

    This is the newer options flow style that receives the config entry
    during initialization rather than accessing it as an instance variable.
    """

    def __init__(self, config_entry):
        """Initialize options flow with config entry."""
        super().__init__(config_entry)
        self._config_entry = config_entry

    @property
    def config_entry(self):
        """Return the config entry."""
        return self._config_entry


class HANDLERS:
    """Decorator to register config flow handlers."""

    _handlers = {}

    @classmethod
    def register(cls, domain: str):
        """Register a config flow handler."""

        def decorator(flow_class):
            cls._handlers[domain] = flow_class
            flow_class.handler = domain
            return flow_class

        return decorator

    @classmethod
    def get_handler(cls, domain: str):
        """Get registered handler."""
        return cls._handlers.get(domain)


# Decorator alias
HANDLERS = HANDLERS()


# Flow result types
class FlowResultType(Enum):
    """Flow result types."""

    FORM = "form"
    CREATE_ENTRY = "create_entry"
    ABORT = "abort"
    EXTERNAL_STEP = "external"
    EXTERNAL_STEP_DONE = "external_done"
    SHOW_PROGRESS = "progress"
    SHOW_PROGRESS_DONE = "progress_done"


FlowResult = Dict[str, Any]


# ConfigFlowResult is an alias for FlowResult used by newer integrations
ConfigFlowResult = FlowResult
