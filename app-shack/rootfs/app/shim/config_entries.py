"""Config entries shim for Home Assistant.

Provides classes for config flow handling.
"""

from enum import Enum
from typing import Any, Callable, Dict, Optional, TypedDict, Union
from dataclasses import dataclass
from contextvars import ContextVar

import probatio

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.field_visibility import VISIBLE_SCHEMA, get_hidden_fields
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from .logging import get_logger
from .models import ConfigEntry, callback

HOMEASSISTANT_DOMAIN = "homeassistant"

_LOGGER = get_logger(__name__)


class FlowRequired(probatio.Required):
    """Required marker that can be shown conditionally (HA 2026.11).

    Exposed as ``homeassistant.data_entry_flow.Required``. Unless the ``visible``
    condition matches the other fields, the field is not rendered, is treated as
    optional, and its value is omitted from the submitted data. A list of
    conditions is combined with AND.
    """

    def __init__(
        self,
        *args: Any,
        visible: Any = None,
        **kwargs: Any,
    ) -> None:
        """Initialize a required marker."""
        super().__init__(*args, **kwargs)
        self.visible = None if visible is None else VISIBLE_SCHEMA(visible)


class FlowOptional(probatio.Optional):
    """Optional marker that can be shown conditionally (HA 2026.11)."""

    def __init__(
        self,
        *args: Any,
        visible: Any = None,
        **kwargs: Any,
    ) -> None:
        """Initialize an optional marker."""
        super().__init__(*args, **kwargs)
        self.visible = None if visible is None else VISIBLE_SCHEMA(visible)


class SectionConfig(TypedDict, total=False):
    """Section config."""

    collapsed: bool


class section:
    """Data entry flow section.

    Exposed as ``homeassistant.data_entry_flow.section`` so integrations that
    group form fields can import it. The shim's form renderer flattens it.
    """

    CONFIG_SCHEMA = probatio.Schema(
        {
            probatio.Optional("collapsed", default=False): bool,
        },
    )

    def __init__(
        self, schema: probatio.Schema, options: Optional[SectionConfig] = None
    ) -> None:
        """Initialize."""
        self.schema = schema
        self.options: SectionConfig = self.CONFIG_SCHEMA(options or {})

    def __call__(self, value: Any) -> Any:
        """Validate input."""
        return self.schema(value)


def _schema_has_visible(data_schema: Any) -> bool:
    """Return True if the schema (or a nested section) has a visible marker."""
    if not isinstance(getattr(data_schema, "schema", None), dict):
        return False
    for key, val in data_schema.schema.items():
        if getattr(key, "visible", None) is not None:
            return True
        if isinstance(val, section) and _schema_has_visible(val.schema):
            return True
    return False


def strip_hidden_fields(data_schema: Any, user_input: dict) -> tuple[Any, dict]:
    """Remove fields hidden by a ``visible`` condition (HA 2026.11).

    The shim does not validate form input against the schema, so this is the part
    that matters: a hidden field is dropped from the submitted data instead of
    reaching the step as a stale value. Returns the input unchanged when the
    schema has no visibility conditions.
    """
    if data_schema is None or not _schema_has_visible(data_schema):
        return data_schema, user_input

    eval_data = dict(user_input)
    conditions: dict[str, Any] = {}
    for key in data_schema.schema:
        name = key.schema if isinstance(key, probatio.Marker) else key
        if (
            isinstance(key, (probatio.Optional, probatio.Required))
            and name not in eval_data
            and not isinstance(key.default, probatio.Undefined)
        ):
            eval_data[name] = key.default()
        if (visible_condition := getattr(key, "visible", None)) is not None:
            conditions[name] = visible_condition

    hidden = get_hidden_fields(conditions, eval_data)
    if not hidden:
        return data_schema, user_input
    return data_schema, {
        name: value for name, value in user_input.items() if name not in hidden
    }

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
SOURCE_HARDWARE = "hardware"
SOURCE_HOMEKIT = "homekit"
SOURCE_USB = "usb"

# Sources that mean "we found this device ourselves". Mirrors HA's
# ``DISCOVERY_SOURCES``: an entry sitting in ``setup_retry`` is reloaded when one
# of these rediscovers it, because that proves the device is back online.
DISCOVERY_SOURCES = frozenset(
    {
        SOURCE_BLUETOOTH,
        SOURCE_DHCP,
        SOURCE_DISCOVERY,
        SOURCE_HARDWARE,
        SOURCE_HASSIO,
        SOURCE_HOMEKIT,
        SOURCE_IMPORT,
        SOURCE_INTEGRATION_DISCOVERY,
        SOURCE_MQTT,
        SOURCE_SSDP,
        SOURCE_SYSTEM,
        SOURCE_USB,
        SOURCE_ZEROCONF,
    }
)

# Connection class constants (deprecated but still used by integrations)
CONN_CLASS_CLOUD_POLL = "cloud_poll"
CONN_CLASS_CLOUD_PUSH = "cloud_push"
CONN_CLASS_LOCAL_POLL = "local_poll"
CONN_CLASS_LOCAL_PUSH = "local_push"
CONN_CLASS_ASSUMED = "assumed"


class AbortFlow(Exception):
    """Raise inside a flow step to abort the flow.

    Mirrors ``homeassistant.data_entry_flow.AbortFlow``: integrations raise it
    (directly or through helpers such as ``_abort_if_unique_id_mismatch``) when
    the step cannot continue, and the flow manager turns it into an abort
    result instead of letting the exception escape.
    """

    def __init__(
        self,
        reason: str,
        description_placeholders: Optional[Dict[str, str]] = None,
        *,
        translation_domain: Optional[str] = None,
    ) -> None:
        """Initialize the abort flow exception."""
        super().__init__(f"Flow aborted: {reason}")
        self.reason = reason
        self.description_placeholders = description_placeholders
        self.translation_domain = translation_domain


class ConfigError(HomeAssistantError):
    """Base class for config entry errors."""


class UnknownEntry(ConfigError):
    """Unknown config entry id specified."""


def abort_result_from_flow_error(flow_id: str, exc: AbortFlow) -> Dict[str, Any]:
    """Build the abort result for a raised ``AbortFlow``.

    ``flow_id`` is included because the web UI continues the flow by id, and an
    abort result that loses it leaves the wizard page without a next request.
    """
    result: Dict[str, Any] = {
        "type": "abort",
        "reason": exc.reason,
        "flow_id": flow_id,
    }
    if exc.description_placeholders:
        result["description_placeholders"] = dict(exc.description_placeholders)
    if exc.translation_domain:
        result["translation_domain"] = exc.translation_domain
    return result

# Result types where Home Assistant's flow manager fills in a missing ``step_id``
# with the step it called. Mirrors ``data_entry_flow.STEP_ID_OPTIONAL_STEPS``.
STEP_ID_OPTIONAL_RESULT_TYPES = frozenset({"form", "menu", "external", "progress"})


def apply_step_id_fallback(flow, called_step_id: str, result):
    """Backfill the ``step_id`` Home Assistant's flow manager would supply.

    Omitting ``step_id`` from ``async_show_form``, ``async_show_menu``,
    ``async_show_progress`` and ``async_external_step`` is deprecated in Home
    Assistant and becomes an error in 2027.5.0. Until then HA's flow manager
    fills in the step it called rather than the step that built the result, so
    we mirror that here (and warn) instead of failing the flow outright.

    Must be called by whoever invoked the ``async_step_*`` method; the flow
    class alone cannot know which step the manager called.
    """
    if not isinstance(result, dict):
        return result
    if result.get("type") not in STEP_ID_OPTIONAL_RESULT_TYPES:
        return result
    if result.get("step_id"):
        return result

    result["step_id"] = called_step_id
    _LOGGER.warning(
        "Config flow step 'async_step_%s' of %s returned a '%s' result without a "
        "step_id; assuming the step that was called. Omitting step_id is "
        "deprecated in Home Assistant and stops working in 2027.5.0 - pass "
        "step_id='%s' explicitly",
        called_step_id,
        f"{type(flow).__module__}.{type(flow).__qualname__}",
        result["type"],
        called_step_id,
    )
    return result


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

    @property
    def source(self) -> Optional[str]:
        """Return the source of the flow from context."""
        return self.context.get("source")

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

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler.

        Integrations that support an options flow (reconfiguration) must
        override this and return an OptionsFlow instance. The base
        implementation raises NotImplementedError, matching Home Assistant.
        """
        raise NotImplementedError()

    @classmethod
    def async_supports_options_flow(cls, config_entry) -> bool:
        """Return True if this config entry supports an options flow.

        Mirrors Home Assistant: support is detected by checking whether the
        flow handler overrides ``async_get_options_flow`` rather than
        inheriting the base implementation that raises NotImplementedError.
        """
        return cls.async_get_options_flow is not ConfigFlow.async_get_options_flow

    def async_show_form(
        self,
        step_id: Optional[str] = None,
        data_schema=None,
        errors: Optional[Dict[str, str]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
        last_step: bool = False,
    ):
        """Return the form definition.

        ``step_id`` is deprecated as optional upstream and required from Home
        Assistant 2027.5.0; the flow manager backfills it via
        ``apply_step_id_fallback``.
        """
        # HA appends the entry name to the reauth description so the user can
        # tell which account they are re-authenticating (nest_protect's reauth
        # forms rely on it). Guarded on ``entry_id`` because integrations may
        # start a flow with source=reauth themselves, without an entry.
        if self.source == SOURCE_REAUTH and "entry_id" in self.context:
            description_placeholders = dict(description_placeholders or {})
            if description_placeholders.get("name") is None:
                description_placeholders["name"] = self._get_reauth_entry().title

        result: Dict[str, Any] = {
            "type": "form",
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
            "last_step": last_step,
        }
        if step_id is not None:
            # Track current step for multi-step flows
            self.cur_step_id = step_id
            result["step_id"] = step_id
        return result

    def async_show_menu(
        self,
        step_id: Optional[str] = None,
        menu_options: Optional[list] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
    ):
        """Return a menu selection step definition."""
        result: Dict[str, Any] = {
            "type": "menu",
            "menu_options": menu_options or [],
            "description_placeholders": description_placeholders or {},
        }
        if step_id is not None:
            self.cur_step_id = step_id
            result["step_id"] = step_id
        return result

    def async_create_entry(
        self,
        title: str,
        data: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
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
        if description_placeholders:
            result["description_placeholders"] = description_placeholders
        return result

    def async_abort(
        self, reason: str, *, translation_domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Abort config flow."""
        result: Dict[str, Any] = {
            "type": "abort",
            "reason": reason,
        }
        if translation_domain is not None:
            result["translation_domain"] = translation_domain
        return result

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

    def async_external_step(self, step_id: Optional[str] = None, url: str = ""):
        """Return external step definition."""
        result: Dict[str, Any] = {"type": "external", "url": url}
        if step_id is not None:
            self.cur_step_id = step_id
            result["step_id"] = step_id
        return result

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
        reload_on_update: bool = True,
        *,
        error: str = "already_configured",
        description_placeholders: Optional[Dict[str, str]] = None,
    ) -> None:
        """Abort if the unique ID is already configured.

        Mirrors HA's ``ConfigFlow._abort_if_unique_id_configured``. A discovery
        flow that rediscovers an already-configured device must not create a
        second entry (HA raises ``AbortFlow`` with ``already_configured``), but
        it may also refresh the existing entry's data first -- a device that
        moved to another IP reports its new address in the discovery payload --
        and have it reloaded.

        meross_lan 6.0.0 calls this from ``async_try_discovery`` as::

            self._abort_if_unique_id_configured(
                updates=device_config, reload_on_update=False
            )

        The shim used to accept only ``updates`` and do nothing, so the call
        raised ``TypeError: ... unexpected keyword argument 'reload_on_update'``
        and discovery of the device failed.
        """
        if self.unique_id is None or self.hass is None:
            return

        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, self.unique_id
        )
        if entry is None:
            return

        should_reload = False
        if updates is not None and self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, **updates}
        ):
            # Existing entry present and the data just changed.
            if reload_on_update and entry.state in ("loaded", "setup_retry"):
                should_reload = True
        elif self.source in DISCOVERY_SOURCES and entry.state == "setup_retry":
            # Retrying setup and we just discovered the unique id, so the device
            # is online again: retry now instead of waiting out the backoff.
            should_reload = True

        # Allow ignored entries to be configured again on the manual user step.
        if entry.source == SOURCE_IGNORE and self.source == SOURCE_USER:
            return

        if should_reload:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

        raise AbortFlow(error, description_placeholders)

    def _async_current_entries(self):
        """Return current entries."""
        if self.hass:
            return self.hass.config_entries.async_entries(self.handler)
        return []

    def _async_current_ids(self):
        """Return current entry IDs."""
        entries = self._async_current_entries()
        return [entry.unique_id for entry in entries if entry.unique_id]

    # -- reauth / reconfigure helpers ----------------------------------
    # HA hands reauth and reconfigure flows the entry they act on through the
    # flow context (``entry_id``); these accessors are how every migrated
    # integration gets at it and how it updates the entry afterwards.

    @property
    def _reauth_entry_id(self) -> str:
        """Return the reauth entry id."""
        if self.source != SOURCE_REAUTH:
            raise ValueError(f"Source is {self.source}, expected {SOURCE_REAUTH}")
        return self.context["entry_id"]

    def _get_reauth_entry(self) -> ConfigEntry:
        """Return the reauth config entry linked to the current context."""
        return self.hass.config_entries.async_get_known_entry(self._reauth_entry_id)

    @property
    def _reconfigure_entry_id(self) -> str:
        """Return the reconfigure entry id."""
        if self.source != SOURCE_RECONFIGURE:
            raise ValueError(f"Source is {self.source}, expected {SOURCE_RECONFIGURE}")
        return self.context["entry_id"]

    def _get_reconfigure_entry(self) -> ConfigEntry:
        """Return the reconfigure config entry linked to the current context."""
        return self.hass.config_entries.async_get_known_entry(
            self._reconfigure_entry_id
        )

    def _abort_if_unique_id_mismatch(
        self,
        *,
        reason: str = "unique_id_mismatch",
        description_placeholders: Optional[Dict[str, str]] = None,
    ) -> None:
        """Abort if the unique ID does not match the reauth/reconfigure entry.

        Raises ``AbortFlow`` (not an abort result) because it is called from
        inside a step, exactly like upstream.
        """
        if (
            self.source == SOURCE_REAUTH
            and self._get_reauth_entry().unique_id != self.unique_id
        ) or (
            self.source == SOURCE_RECONFIGURE
            and self._get_reconfigure_entry().unique_id != self.unique_id
        ):
            raise AbortFlow(reason, description_placeholders)

    # -- entry updates from a flow -------------------------------------

    def __async_update(
        self,
        entry: ConfigEntry,
        *,
        unique_id: Union[str, None, UndefinedType],
        title: Union[str, UndefinedType],
        data: Union[Dict[str, Any], UndefinedType],
        data_updates: Union[Dict[str, Any], UndefinedType],
        options: Union[Dict[str, Any], UndefinedType],
    ) -> bool:
        """Update a config entry from a flow; returns True if anything changed."""
        if data_updates is not UNDEFINED:
            if data is not UNDEFINED:
                raise ValueError("Cannot set both data and data_updates")
            data = {**entry.data, **data_updates}

        updates: Dict[str, Any] = {}
        for key, value in (
            ("unique_id", unique_id),
            ("title", title),
            ("data", data),
            ("options", options),
        ):
            if value is not UNDEFINED:
                updates[key] = value
        return self.hass.config_entries.async_update_entry(entry, **updates)

    def _async_abort_after_update(
        self, reason: Union[str, UndefinedType]
    ) -> Dict[str, Any]:
        """Build the abort result for a finished reauth/reconfigure flow."""
        translation_domain: Optional[str] = None
        if reason is UNDEFINED:
            if self.source == SOURCE_RECONFIGURE:
                reason = "reconfigure_successful"
            else:
                reason = "reauth_successful"
            translation_domain = HOMEASSISTANT_DOMAIN
        return self.async_abort(reason=reason, translation_domain=translation_domain)

    def async_update_and_abort(
        self,
        entry: ConfigEntry,
        *,
        unique_id: Union[str, None, UndefinedType] = UNDEFINED,
        title: Union[str, UndefinedType] = UNDEFINED,
        data: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        data_updates: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        options: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        reason: Union[str, UndefinedType] = UNDEFINED,
    ) -> Dict[str, Any]:
        """Update a config entry and finish the flow without reloading it."""
        self.__async_update(
            entry=entry,
            unique_id=unique_id,
            title=title,
            data=data,
            data_updates=data_updates,
            options=options,
        )
        return self._async_abort_after_update(reason)

    def async_update_reload_and_abort(
        self,
        entry: ConfigEntry,
        *,
        unique_id: Union[str, None, UndefinedType] = UNDEFINED,
        title: Union[str, UndefinedType] = UNDEFINED,
        data: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        data_updates: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        options: Union[Dict[str, Any], UndefinedType] = UNDEFINED,
        reason: Union[str, UndefinedType] = UNDEFINED,
        reload_even_if_entry_is_unchanged: bool = True,
    ) -> Dict[str, Any]:
        """Update a config entry, schedule a reload and finish the flow."""
        result = self.__async_update(
            entry=entry,
            unique_id=unique_id,
            title=title,
            data=data,
            data_updates=data_updates,
            options=options,
        )
        if reload_even_if_entry_is_unchanged or result:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self._async_abort_after_update(reason)


class OptionsFlow:
    """Base class for options flows."""

    def __init__(self, config_entry=None):
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
        step_id: Optional[str] = None,
        data_schema=None,
        errors: Optional[Dict[str, str]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
        last_step: bool = False,
    ):
        """Return the form definition."""
        result: Dict[str, Any] = {
            "type": "form",
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
            "last_step": last_step,
        }
        if step_id is not None:
            result["step_id"] = step_id
        return result

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

    def async_show_menu(
        self,
        step_id: Optional[str] = None,
        menu_options: Optional[list] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
    ):
        """Return a menu selection step definition."""
        result: Dict[str, Any] = {
            "type": "menu",
            "menu_options": menu_options or [],
            "description_placeholders": description_placeholders or {},
        }
        if step_id is not None:
            result["step_id"] = step_id
        return result


class OptionsFlowWithConfigEntry(OptionsFlow):
    """Options flow with config entry access.

    This is the newer options flow style that receives the config entry
    during initialization rather than accessing it as an instance variable.
    """

    def __init__(self, config_entry=None):
        """Initialize options flow with config entry."""
        super().__init__(config_entry)
        self._config_entry = config_entry

    @property
    def config_entry(self):
        """Return the config entry."""
        return self._config_entry


class OptionsFlowWithReload(OptionsFlow):
    """Options flow that reloads the entry when its options change.

    Mirrors HA's marker class: integrations subclass it instead of
    ``OptionsFlow`` and stop wiring a config entry update listener (HA raises if
    both are used). ``automatic_reload = False`` opts back out.
    """

    automatic_reload: bool = True


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
