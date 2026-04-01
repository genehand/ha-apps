"""Import patching for Home Assistant compatibility.

Patches sys.modules to redirect homeassistant imports to the shim.
"""

import sys
import types
from dataclasses import dataclass, field, make_dataclass
from enum import Enum
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

        # Import types here since it's used below
        import types
        import importlib.util
        from pathlib import Path

        # STEP 1: Inject stub modules FIRST
        # This prevents ImportError for HA-internal dependencies
        stubs = {
            "homeassistant.helpers.deprecation": "_stub_helpers_deprecation",
            "homeassistant.util.hass_dict": "_stub_util_hass_dict",
            "homeassistant.util.signal_type": "_stub_util_signal_type",
        }

        for module_name, stub_filename in stubs.items():
            stub_module = types.ModuleType(module_name)
            stub_path = Path(__file__).parent / "ha_fetched" / f"{stub_filename}.py"
            if stub_path.exists():
                # Execute stub file content in the module
                stub_code = stub_path.read_text()
                exec(stub_code, stub_module.__dict__)
                sys.modules[module_name] = stub_module
                _LOGGER.debug(f"Injected stub: {module_name}")

        # STEP 2: Load ha_fetched modules directly (avoiding __init__.py which imports const)
        # const.py depends on homeassistant namespace being set up first
        ha_fetched_path = Path(__file__).parent / "ha_fetched"

        # Load generated.entity_platforms
        spec_ep = importlib.util.spec_from_file_location(
            "ha_fetched.generated.entity_platforms",
            ha_fetched_path / "generated" / "entity_platforms.py",
        )
        ha_entity_platforms = importlib.util.module_from_spec(spec_ep)
        sys.modules["ha_fetched.generated.entity_platforms"] = ha_entity_platforms
        spec_ep.loader.exec_module(ha_entity_platforms)

        # Load generated package
        spec_gen = importlib.util.spec_from_file_location(
            "ha_fetched.generated", ha_fetched_path / "generated" / "__init__.py"
        )
        ha_generated = importlib.util.module_from_spec(spec_gen)
        ha_generated.entity_platforms = ha_entity_platforms
        sys.modules["ha_fetched.generated"] = ha_generated
        spec_gen.loader.exec_module(ha_generated)

        # Load util.event_type
        spec_et = importlib.util.spec_from_file_location(
            "ha_fetched.util.event_type",
            ha_fetched_path / "util" / "event_type.py",
        )
        ha_event_type = importlib.util.module_from_spec(spec_et)
        sys.modules["ha_fetched.util.event_type"] = ha_event_type
        spec_et.loader.exec_module(ha_event_type)

        # STEP 3: Set up homeassistant namespace BEFORE importing const
        homeassistant_generated = types.ModuleType("homeassistant.generated")
        homeassistant_generated.entity_platforms = ha_entity_platforms
        sys.modules["homeassistant.generated"] = homeassistant_generated
        sys.modules["homeassistant.generated.entity_platforms"] = ha_entity_platforms

        homeassistant_util = types.ModuleType("homeassistant.util")
        homeassistant_util.event_type = ha_event_type
        homeassistant_util.hass_dict = sys.modules["homeassistant.util.hass_dict"]
        homeassistant_util.signal_type = sys.modules["homeassistant.util.signal_type"]
        sys.modules["homeassistant.util"] = homeassistant_util
        sys.modules["homeassistant.util.event_type"] = ha_event_type

        # STEP 4: Now load const and exceptions
        spec_const = importlib.util.spec_from_file_location(
            "ha_fetched.const", ha_fetched_path / "const.py"
        )
        ha_const = importlib.util.module_from_spec(spec_const)
        sys.modules["ha_fetched.const"] = ha_const
        spec_const.loader.exec_module(ha_const)

        spec_exc = importlib.util.spec_from_file_location(
            "ha_fetched.exceptions", ha_fetched_path / "exceptions.py"
        )
        ha_exceptions = importlib.util.module_from_spec(spec_exc)
        sys.modules["ha_fetched.exceptions"] = ha_exceptions
        spec_exc.loader.exec_module(ha_exceptions)

        # STEP 5: Import other shim modules
        from . import core
        from . import entity
        from . import config_entries
        from . import selectors

        # STEP 6: Create full homeassistant package
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.core = core
        homeassistant.const = ha_const
        homeassistant.exceptions = ha_exceptions
        homeassistant.config_entries = config_entries
        homeassistant.generated = homeassistant_generated
        homeassistant.util = homeassistant_util

        # Register all submodules in sys.modules
        sys.modules["homeassistant"] = homeassistant
        sys.modules["homeassistant.core"] = core
        sys.modules["homeassistant.const"] = ha_const
        sys.modules["homeassistant.exceptions"] = ha_exceptions
        sys.modules["homeassistant.config_entries"] = config_entries

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
        homeassistant.components = types.ModuleType("homeassistant.components")

        # Skip loading dt.py and color.py - they require HA-specific dependencies
        # (aiozoneinfo, voluptuous, etc.) that we don't need for basic integration support
        # Most HACS integrations don't actually use these utils
        _LOGGER.debug(
            "Skipping dt.py and color.py - HA-specific dependencies not needed"
        )

        # Create device_registry stub
        device_registry = types.ModuleType("homeassistant.helpers.device_registry")

        # Create DeviceEntry dataclass
        @dataclass
        class DeviceEntry:
            """Device entry stub."""

            id: str
            config_entries: set
            connections: set
            identifiers: set
            manufacturer: Optional[str] = None
            model: Optional[str] = None
            name: Optional[str] = None
            sw_version: Optional[str] = None
            hw_version: Optional[str] = None
            entry_type: Optional[str] = None
            via_device: Optional[tuple] = None
            configuration_url: Optional[str] = None
            suggested_area: Optional[str] = None

        device_registry.DeviceEntry = DeviceEntry

        # Create a DeviceRegistry class with async_get_or_create method
        class DeviceRegistry:
            """Device registry stub."""

            def __init__(self, hass):
                self.hass = hass
                self._devices = {}

            def async_get_or_create(
                self,
                *,
                config_entry_id=None,
                configuration_url=None,
                connections=None,
                default_manufacturer=None,
                default_model=None,
                default_name=None,
                identifiers=None,
                manufacturer=None,
                model=None,
                name=None,
                suggested_area=None,
                sw_version=None,
                hw_version=None,
                entry_type=None,
                via_device=None,
                **kwargs,
            ):
                """Get or create a device entry."""
                # Create a unique device id from identifiers
                device_id = None
                if identifiers:
                    device_id = "_".join(str(i) for i in next(iter(identifiers)))
                if not device_id:
                    device_id = config_entry_id or "mock_device"

                device_entry = DeviceEntry(
                    id=device_id,
                    config_entries={config_entry_id} if config_entry_id else set(),
                    connections=connections or set(),
                    identifiers=identifiers or set(),
                    manufacturer=manufacturer or default_manufacturer,
                    model=model or default_model,
                    name=name or default_name,
                    sw_version=sw_version,
                    hw_version=hw_version,
                    entry_type=entry_type,
                    via_device=via_device,
                    configuration_url=configuration_url,
                    suggested_area=suggested_area,
                )
                self._devices[device_id] = device_entry
                return device_entry

            def async_get(self, device_id):
                """Get a device by id."""
                return self._devices.get(device_id)

        # Replace the simple DeviceRegistry type with our class
        device_registry.DeviceRegistry = DeviceRegistry

        # Create a function to get or create the registry instance
        def async_get(hass):
            """Get the device registry for the Home Assistant instance."""
            # Store registry in hass.data
            if not hasattr(hass, "data"):
                hass.data = {}
            if "device_registry" not in hass.data:
                hass.data["device_registry"] = DeviceRegistry(hass)
            return hass.data["device_registry"]

        device_registry.async_get = async_get

        # DeviceInfo dataclass for integrations
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
            serial_number: Optional[str] = None

        device_registry.DeviceInfo = DeviceInfo
        device_registry.format_mac = lambda x: x
        device_registry.async_entries_for_config_entry = lambda *args, **kwargs: []
        device_registry.async_get_device = lambda *args, **kwargs: None

        # Add DeviceEntryType enum for SERVICE entry type
        class DeviceEntryType(Enum):
            """Device entry type."""

            SERVICE = "service"

        device_registry.DeviceEntryType = DeviceEntryType

        homeassistant.helpers.device_registry = device_registry
        sys.modules["homeassistant.helpers.device_registry"] = device_registry

        # Import entity helper from shim.entity
        from . import entity

        homeassistant.helpers.entity = entity
        sys.modules["homeassistant.helpers.entity"] = entity

        # Also add DeviceInfo to homeassistant.helpers.entity for compatibility
        entity.DeviceInfo = DeviceInfo

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

        # ensure_list validator - wraps single values in a list
        def ensure_list(value):
            """Wrap value in list if it is not one."""
            if value is None:
                return []
            return value if isinstance(value, list) else [value]

        config_validation.ensure_list = ensure_list

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

        # Create aiohttp_client stub module
        aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
        import aiohttp

        _client_session_cache = {}

        def async_get_clientsession(hass, verify_ssl=True):
            """Get aiohttp ClientSession with caching."""
            key = (id(hass), verify_ssl)
            if key not in _client_session_cache:
                connector = aiohttp.TCPConnector(verify_ssl=verify_ssl)
                session = aiohttp.ClientSession(connector=connector)
                _client_session_cache[key] = session
            return _client_session_cache[key]

        aiohttp_client.async_get_clientsession = async_get_clientsession
        homeassistant.helpers.aiohttp_client = aiohttp_client
        sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

        # Create issue_registry stub module
        issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")
        issue_registry.async_create_issue = lambda *args, **kwargs: None
        issue_registry.async_delete_issue = lambda *args, **kwargs: None

        # Add IssueSeverity enum
        class IssueSeverity(Enum):
            """Issue severity levels."""

            CRITICAL = "critical"
            ERROR = "error"
            WARNING = "warning"

        issue_registry.IssueSeverity = IssueSeverity
        homeassistant.helpers.issue_registry = issue_registry
        sys.modules["homeassistant.helpers.issue_registry"] = issue_registry

        # Create entity_registry stub module
        entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
        entity_registry.async_get = lambda *args, **kwargs: None
        entity_registry.RegistryEntry = type("RegistryEntry", (), {"entity_id": None})

        async def async_entries_for_config_entry(hass, config_entry_id):
            """Return all entity registry entries for a config entry."""
            return []

        entity_registry.async_entries_for_config_entry = async_entries_for_config_entry
        homeassistant.helpers.entity_registry = entity_registry
        sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

        # Create entity_component stub module
        entity_component = types.ModuleType("homeassistant.helpers.entity_component")
        entity_component.DATA_INSTANCES = {}
        homeassistant.helpers.entity_component = entity_component
        sys.modules["homeassistant.helpers.entity_component"] = entity_component

        # Create config_entry_oauth2_flow stub - needed for OAuth2 integrations like smartcar
        oauth2_flow = types.ModuleType("homeassistant.helpers.config_entry_oauth2_flow")

        # Create OAuth2Session class
        class OAuth2Session:
            """OAuth2 session stub."""

            def __init__(self, hass, config_entry, implementation):
                self.hass = hass
                self.config_entry = config_entry
                self.implementation = implementation
                self.token = {}

            async def async_ensure_token_valid(self):
                """Ensure token is valid."""
                pass

            @property
            def valid_token(self):
                """Return valid token."""
                return self.token.get("access_token", "")

        oauth2_flow.OAuth2Session = OAuth2Session

        # Create AbstractOAuth2Implementation class
        class AbstractOAuth2Implementation:
            """Abstract OAuth2 implementation stub."""

            @property
            def name(self):
                return "stub"

            @property
            def domain(self):
                return "stub"

            async def async_generate_authorize_url(self, flow_id):
                """Generate authorize URL."""
                return "http://localhost/auth"

            async def async_resolve_external_data(self, external_data):
                """Resolve external data."""
                return {"access_token": "stub_token"}

        oauth2_flow.AbstractOAuth2Implementation = AbstractOAuth2Implementation

        # Create LocalOAuth2Implementation
        class LocalOAuth2Implementation(AbstractOAuth2Implementation):
            """Local OAuth2 implementation stub."""

            def __init__(
                self, hass, domain, client_id, client_secret, authorize_url, token_url
            ):
                self.hass = hass
                self._domain = domain
                self.client_id = client_id
                self.client_secret = client_secret
                self._authorize_url = authorize_url
                self._token_url = token_url

            @property
            def name(self):
                return self._domain

            @property
            def domain(self):
                return self._domain

            async def async_generate_authorize_url(self, flow_id):
                """Generate authorize URL."""
                return f"{self._authorize_url}?client_id={self.client_id}&response_type=code"

            async def async_resolve_external_data(self, external_data):
                """Resolve external data."""
                return {"access_token": "stub_token", "token_type": "bearer"}

        oauth2_flow.LocalOAuth2Implementation = LocalOAuth2Implementation

        # Create OAuth2FlowHandler base class
        from .config_entries import ConfigFlow

        class OAuth2FlowHandler(ConfigFlow):
            """OAuth2 Flow Handler base class with manual token entry support."""

            DOMAIN = ""
            VERSION = 1

            def __init__(self):
                super().__init__()
                self._oauth2_session = None
                self._oauth2_implementation = None

            async def async_step_user(self, user_input=None):
                """Handle user step - show manual token entry form."""
                if user_input is not None:
                    # User provided manual tokens
                    return await self.async_oauth_create_entry(user_input)

                # Show a form for manual token entry
                from homeassistant.helpers.selector import (
                    TextSelector,
                    TextSelectorConfig,
                    TextSelectorType,
                )
                import voluptuous as vol

                data_schema = vol.Schema(
                    {
                        vol.Required("access_token"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                        vol.Optional("refresh_token"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                        vol.Optional("expires_at"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.NUMBER)
                        ),
                    }
                )

                return self.async_show_form(
                    step_id="user",
                    data_schema=data_schema,
                    description_placeholders={
                        "info": "OAuth2 authentication is not supported in this environment. You can manually enter access token for testing purposes."
                    },
                )

            async def async_step_auth(self, user_input=None):
                """Handle auth step - redirect to manual entry."""
                return await self.async_step_user(user_input)

            async def async_oauth_create_entry(self, data):
                """Create entry from OAuth data."""
                return self.async_create_entry(title="OAuth2 Manual Entry", data=data)

            async def async_step_pick_implementation(self, user_input=None):
                """Pick implementation step - redirect to manual entry."""
                return await self.async_step_user(user_input)

        oauth2_flow.OAuth2FlowHandler = OAuth2FlowHandler

        # AbstractOAuth2FlowHandler is an alias for OAuth2FlowHandler
        oauth2_flow.AbstractOAuth2FlowHandler = OAuth2FlowHandler

        # Add helper functions
        oauth2_flow.async_get_implementations = lambda hass, domain: []
        oauth2_flow.async_register_implementation = (
            lambda hass, domain, implementation: None
        )

        async def async_get_config_entry_implementation(hass, config_entry):
            """Get OAuth2 implementation for a config entry."""
            return None

        oauth2_flow.async_get_config_entry_implementation = (
            async_get_config_entry_implementation
        )

        homeassistant.helpers.config_entry_oauth2_flow = oauth2_flow
        sys.modules["homeassistant.helpers.config_entry_oauth2_flow"] = oauth2_flow

        # Create event stub module
        event = types.ModuleType("homeassistant.helpers.event")

        def async_track_point_in_time(hass, action, point_in_time):
            """Track a specific point in time."""
            return lambda: None

        def async_track_state_change_event(hass, entity_ids, action):
            """Track state change events."""
            return lambda: None

        event.async_track_point_in_time = async_track_point_in_time
        event.async_track_state_change_event = async_track_state_change_event
        event.EventStateChangedData = type("EventStateChangedData", (), {})
        homeassistant.helpers.event = event
        sys.modules["homeassistant.helpers.event"] = event

        # Create restore_state stub module
        restore_state = types.ModuleType("homeassistant.helpers.restore_state")
        restore_state.RestoreEntity = type("RestoreEntity", (), {})
        restore_state.ExtraStoredData = type("ExtraStoredData", (), {})
        restore_state.RestoredExtraData = type("RestoredExtraData", (), {})
        restore_state.async_get = lambda hass: None
        homeassistant.helpers.restore_state = restore_state
        sys.modules["homeassistant.helpers.restore_state"] = restore_state

        # Create dispatcher stub module for signal/slot pattern
        dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")

        async def async_dispatcher_connect(hass, signal, target):
            """Connect a receiver to a signal."""
            _LOGGER.debug(f"Dispatcher connect: {signal}")
            return lambda: None

        async def async_dispatcher_send(hass, signal, *args):
            """Send a signal to all receivers."""
            _LOGGER.debug(f"Dispatcher send: {signal}")

        dispatcher.async_dispatcher_connect = async_dispatcher_connect
        dispatcher.async_dispatcher_send = async_dispatcher_send
        homeassistant.helpers.dispatcher = dispatcher
        sys.modules["homeassistant.helpers.dispatcher"] = dispatcher

        # Create service_info stub module with submodules
        service_info = types.ModuleType("homeassistant.helpers.service_info")
        dhcp = types.ModuleType("homeassistant.helpers.service_info.dhcp")
        dhcp.DhcpServiceInfo = type("DhcpServiceInfo", (), {})
        mqtt = types.ModuleType("homeassistant.helpers.service_info.mqtt")
        mqtt.MqttServiceInfo = type("MqttServiceInfo", (), {})
        service_info.dhcp = dhcp
        service_info.mqtt = mqtt
        homeassistant.helpers.service_info = service_info
        sys.modules["homeassistant.helpers.service_info"] = service_info
        sys.modules["homeassistant.helpers.service_info.dhcp"] = dhcp
        sys.modules["homeassistant.helpers.service_info.mqtt"] = mqtt

        # Create network stub module
        network = types.ModuleType("homeassistant.helpers.network")

        def get_url(hass, *, prefer_external=False, allow_cloud=True):
            """Get the URL of the instance."""
            return "http://localhost:8123"

        network.get_url = get_url
        homeassistant.helpers.network = network
        sys.modules["homeassistant.helpers.network"] = network

        # Create storage stub module
        storage = types.ModuleType("homeassistant.helpers.storage")

        class Store:
            """Storage implementation."""

            def __init__(self, hass, version, key):
                self._data = {}

            async def async_load(self):
                return self._data

            async def async_save(self, data):
                self._data = data

            def __class_getitem__(cls, item):
                """Make Store subscriptable for type hints like Store[SomeType]."""
                return cls

        storage.Store = Store
        homeassistant.helpers.storage = storage
        sys.modules["homeassistant.helpers.storage"] = storage

        # Create homeassistant.util package and submodules
        homeassistant.util = types.ModuleType("homeassistant.util")

        # Create util.dt module
        dt_util = types.ModuleType("homeassistant.util.dt")
        from datetime import datetime, timezone

        dt_util.DEFAULT_TIME_ZONE = timezone.utc
        dt_util.now = lambda: datetime.now(dt_util.DEFAULT_TIME_ZONE)
        dt_util.as_utc = lambda d: d.astimezone(timezone.utc)
        dt_util.start_of_local_day = lambda: datetime.now(
            dt_util.DEFAULT_TIME_ZONE
        ).replace(hour=0, minute=0, second=0, microsecond=0)
        homeassistant.util.dt = dt_util
        sys.modules["homeassistant.util.dt"] = dt_util

        # Create util.color module
        color_util = types.ModuleType("homeassistant.util.color")

        def brightness_to_value(scale, brightness):
            """Convert brightness from scale to value."""
            return int(brightness * 255 / 255)

        def value_to_brightness(scale, value):
            """Convert value to brightness on scale."""
            return int(value * 255 / 255)

        def color_rgb_to_rgbw(r, g, b):
            """Convert RGB to RGBW."""
            w = min(r, g, b)
            return (r - w, g - w, b - w, w)

        def color_rgbw_to_rgb(r, g, b, w):
            """Convert RGBW to RGB."""
            return (r + w, g + w, b + w)

        color_util.brightness_to_value = brightness_to_value
        color_util.value_to_brightness = value_to_brightness
        color_util.color_rgb_to_rgbw = color_rgb_to_rgbw
        color_util.color_rgbw_to_rgb = color_rgbw_to_rgb
        homeassistant.util.color = color_util
        sys.modules["homeassistant.util.color"] = color_util

        # Create util.unit_conversion module
        unit_conversion = types.ModuleType("homeassistant.util.unit_conversion")

        class TemperatureConverter:
            """Temperature converter."""

            @staticmethod
            def convert(value, from_unit, to_unit):
                """Convert temperature between units."""
                if from_unit == to_unit:
                    return value
                if from_unit == "°C":
                    if to_unit == "°F":
                        return value * 9 / 5 + 32
                    elif to_unit == "K":
                        return value + 273.15
                elif from_unit == "°F":
                    if to_unit == "°C":
                        return (value - 32) * 5 / 9
                    elif to_unit == "K":
                        return (value - 32) * 5 / 9 + 273.15
                elif from_unit == "K":
                    if to_unit == "°C":
                        return value - 273.15
                    elif to_unit == "°F":
                        return (value - 273.15) * 9 / 5 + 32
                return value

        unit_conversion.TemperatureConverter = TemperatureConverter
        homeassistant.util.unit_conversion = unit_conversion
        sys.modules["homeassistant.util.unit_conversion"] = unit_conversion

        # Create util.slugify function
        def slugify(text):
            """Create a slug from text.

            Handles unicode characters by normalizing them to ASCII equivalents
            where possible (e.g., curly quotes -> straight quotes).
            """
            import re
            import unicodedata

            text = str(text)

            # Normalize unicode to decompose characters
            text = unicodedata.normalize("NFKD", text)

            # Map common unicode punctuation to ASCII equivalents
            # Curly/smart quotes and other typographic characters
            unicode_map = {
                "\u2018": "'",  # LEFT SINGLE QUOTATION MARK -> APOSTROPHE
                "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK -> APOSTROPHE
                "\u201a": ",",  # SINGLE LOW-9 QUOTATION MARK -> COMMA
                "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK -> APOSTROPHE
                "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK -> QUOTATION MARK
                "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK -> QUOTATION MARK
                "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK -> QUOTATION MARK
                "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK -> QUOTATION MARK
                "\u2026": "...",  # HORIZONTAL ELLIPSIS -> ...
                "\u2013": "-",  # EN DASH -> HYPHEN-MINUS
                "\u2014": "-",  # EM DASH -> HYPHEN-MINUS
                "\u2212": "-",  # MINUS SIGN -> HYPHEN-MINUS
            }
            for unicode_char, ascii_char in unicode_map.items():
                text = text.replace(unicode_char, ascii_char)

            # Encode to ASCII, dropping any remaining non-ASCII chars
            text = text.encode("ascii", "ignore").decode("ascii")

            # Remove non-word chars (except spaces and dashes), lowercase, replace spaces/dashes with underscore
            text = re.sub(r"[^\w\s-]", "", text).strip().lower()
            text = re.sub(r"[-\s]+", "_", text)
            return text

        homeassistant.util.slugify = slugify

        sys.modules["homeassistant.util"] = homeassistant.util

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

            def async_set_updated_data(self, data):
                """Manually update data, notify listeners and reset update failure."""
                self.data = data
                self._last_update_success = True
                self.async_update_listeners()

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
        sys.modules["homeassistant.const"] = ha_const
        sys.modules["homeassistant.exceptions"] = ha_exceptions
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
        _LOGGER.debug("Import patching complete")

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
        homeassistant.components.vacuum = platforms.vacuum
        homeassistant.components.humidifier = platforms.humidifier
        homeassistant.components.number = platforms.number
        homeassistant.components.lock = platforms.lock

        # Create persistent_notification stub module
        persistent_notification = types.ModuleType(
            "homeassistant.components.persistent_notification"
        )
        persistent_notification.async_create = (
            lambda hass, message, title=None, notification_id=None: None
        )
        persistent_notification.async_dismiss = lambda hass, notification_id: None
        homeassistant.components.persistent_notification = persistent_notification
        sys.modules["homeassistant.components.persistent_notification"] = (
            persistent_notification
        )

        # Create diagnostics stub module
        diagnostics = types.ModuleType("homeassistant.components.diagnostics")
        diagnostics.async_get_config_entry_diagnostics = lambda hass, config_entry: {}
        diagnostics.async_get_device_diagnostics = lambda hass, config_entry, device: {}
        diagnostics.REDACTED = "**REDACTED**"
        homeassistant.components.diagnostics = diagnostics
        sys.modules["homeassistant.components.diagnostics"] = diagnostics

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
        sys.modules["homeassistant.components.vacuum"] = platforms.vacuum
        sys.modules["homeassistant.components.humidifier"] = platforms.humidifier
        sys.modules["homeassistant.components.number"] = platforms.number
        sys.modules["homeassistant.components.lock"] = platforms.lock

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

        # Create cloud stub - some integrations (like smartcar) depend on this
        cloud_stub = types.ModuleType("homeassistant.components.cloud")
        cloud_stub.async_active_subscription = lambda hass: False
        cloud_stub.async_is_logged_in = lambda hass: False
        cloud_stub.async_create_cloudhook = lambda hass, webhook_id: None
        cloud_stub.async_delete_cloudhook = lambda hass, webhook_id: None
        cloud_stub.async_listen_connection_change = lambda hass, callback: lambda: None
        cloud_stub.CloudNotAvailable = type("CloudNotAvailable", (Exception,), {})
        homeassistant.components.cloud = cloud_stub
        sys.modules["homeassistant.components.cloud"] = cloud_stub

        # Create webhook stub - some integrations (like smartcar) depend on this
        webhook_stub = types.ModuleType("homeassistant.components.webhook")
        webhook_stub.async_register = (
            lambda hass, webhook_id, handler, *args, **kwargs: None
        )
        webhook_stub.async_unregister = lambda hass, webhook_id: None
        webhook_stub.async_generate_id = lambda: "test_webhook_id"
        webhook_stub.async_generate_url = (
            lambda hass, webhook_id: f"http://localhost:8123/api/webhook/{webhook_id}"
        )
        homeassistant.components.webhook = webhook_stub
        sys.modules["homeassistant.components.webhook"] = webhook_stub

        # Create homeassistant.util.percentage stub
        percentage_stub = types.ModuleType("homeassistant.util.percentage")

        def int_states_in_range(low_high_range):
            """Return the number of integer states in a range."""
            low, high = low_high_range
            return high - low + 1

        def percentage_to_ranged_value(low_high_range, percentage):
            """Map a percentage to a value within a range."""
            low, high = low_high_range
            return low + (high - low) * percentage / 100

        def ranged_value_to_percentage(low_high_range, value):
            """Map a value within a range to a percentage."""
            low, high = low_high_range
            if value is None:
                return None
            return round((value - low) / (high - low) * 100)

        percentage_stub.int_states_in_range = int_states_in_range
        percentage_stub.percentage_to_ranged_value = percentage_to_ranged_value
        percentage_stub.ranged_value_to_percentage = ranged_value_to_percentage

        homeassistant.util.percentage = percentage_stub
        sys.modules["homeassistant.util.percentage"] = percentage_stub

        # Create missing component stubs
        from dataclasses import make_dataclass, field

        from shim.frozen_dataclass_compat import FrozenOrThawed

        image_stub = types.ModuleType("homeassistant.components.image")
        image_stub.ImageEntity = type("ImageEntity", (), {})

        # Create ImageEntityDescription using FrozenOrThawed for compatibility
        class ImageEntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
            """Image entity description."""

            key: str
            name: Optional[str] = None
            icon: Optional[str] = None
            device_class: Optional[str] = None
            entity_category: Optional[str] = None
            entity_registry_enabled_default: bool = True

        image_stub.ImageEntityDescription = ImageEntityDescription
        homeassistant.components.image = image_stub
        sys.modules["homeassistant.components.image"] = image_stub

        number_stub = types.ModuleType("homeassistant.components.number")
        # Use the real NumberEntity from shim.platforms.number
        import shim.platforms.number as number_platform

        number_stub.NumberEntity = number_platform.NumberEntity

        # Create NumberEntityDescription using FrozenOrThawed for compatibility
        class NumberEntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
            """Number entity description."""

            key: str
            name: Optional[str] = None
            translation_key: Optional[str] = None
            icon: Optional[str] = None
            device_class: Optional[str] = None
            entity_category: Optional[str] = None
            entity_registry_enabled_default: bool = True
            native_unit_of_measurement: Optional[str] = None
            native_max_value: Optional[float] = None
            native_min_value: Optional[float] = None
            native_step: Optional[float] = None
            # Legacy field names for compatibility
            min_value: Optional[float] = None
            max_value: Optional[float] = None
            step: Optional[float] = None

        number_stub.NumberEntityDescription = NumberEntityDescription
        # Create NumberDeviceClass enum
        number_device_class_enum = Enum(
            "NumberDeviceClass",
            [
                ("APPARENT_POWER", "apparent_power"),
                ("AQUEOUS_NH3_CONCENTRATION", "aqhi"),
                ("AREA", "area"),
                ("ATMOSPHERIC_PRESSURE", "atmospheric_pressure"),
                ("BATTERY", "battery"),
                ("BLOOD_GLUCOSE_CONCENTRATION", "blood_glucose_concentration"),
                ("BREATH_VOC_CONCENTRATION", "breath_voc_concentration"),
                ("CO", "carbon_monoxide"),
                ("CO2", "carbon_dioxide"),
                ("CONDUCTIVITY", "conductivity"),
                ("CURRENT", "current"),
                ("DATA_RATE", "data_rate"),
                ("DATA_SIZE", "data_size"),
                ("DISTANCE", "distance"),
                ("DURATION", "duration"),
                ("ENERGY", "energy"),
                ("ENERGY_STORAGE", "energy_storage"),
                ("FREQUENCY", "frequency"),
                ("GAS", "gas"),
                ("HUMIDITY", "humidity"),
                ("ILLUMINANCE", "illuminance"),
                ("IRRADIANCE", "irradiance"),
                ("MOISTURE", "moisture"),
                ("MONETARY", "monetary"),
                ("NITROGEN_DIOXIDE", "nitrogen_dioxide"),
                ("NITROGEN_MONOXIDE", "nitrogen_monoxide"),
                ("NITROUS_OXIDE", "nitrous_oxide"),
                ("OZONE", "ozone"),
                ("PH", "ph"),
                ("PM1", "pm1"),
                ("PM10", "pm10"),
                ("PM25", "pm25"),
                ("POWER", "power"),
                ("POWER_FACTOR", "power_factor"),
                ("PRECIPITATION", "precipitation"),
                ("PRECIPITATION_INTENSITY", "precipitation_intensity"),
                ("PRESSURE", "pressure"),
                ("REACTIVE_POWER", "reactive_power"),
                ("SIGNAL_STRENGTH", "signal_strength"),
                ("SOUND_PRESSURE", "sound_pressure"),
                ("SPEED", "speed"),
                ("SULPHUR_DIOXIDE", "sulphur_dioxide"),
                ("TEMPERATURE", "temperature"),
                ("VOLATILE_ORGANIC_COMPOUNDS", "volatile_organic_compounds"),
                (
                    "VOLATILE_ORGANIC_COMPOUNDS_PARTS",
                    "volatile_organic_compounds_parts",
                ),
                ("VOLTAGE", "voltage"),
                ("VOLUME", "volume"),
                ("VOLUME_FLOW_RATE", "volume_flow_rate"),
                ("VOLUME_STORAGE", "volume_storage"),
                ("WATER", "water"),
                ("WEIGHT", "weight"),
                ("WIND_SPEED", "wind_speed"),
            ],
        )
        number_stub.NumberDeviceClass = number_device_class_enum
        # Create NumberMode enum for entity display modes
        number_mode_enum = Enum(
            "NumberMode",
            [
                ("AUTO", "auto"),
                ("BOX", "box"),
                ("SLIDER", "slider"),
            ],
        )
        number_stub.NumberMode = number_mode_enum
        homeassistant.components.number = number_stub
        sys.modules["homeassistant.components.number"] = number_stub

        scene_stub = types.ModuleType("homeassistant.components.scene")
        scene_stub.Scene = type("Scene", (), {})
        scene_stub.SceneEntityDescription = type("SceneEntityDescription", (), {})
        homeassistant.components.scene = scene_stub
        sys.modules["homeassistant.components.scene"] = scene_stub

        # Create homeassistant.helpers.typing stub
        typing_stub = types.ModuleType("homeassistant.helpers.typing")
        typing_stub.EventType = type("EventType", (), {})
        typing_stub.StateType = type("StateType", (), {})
        typing_stub.ConfigType = dict  # Config is typically a dict

        # UNDEFINED sentinel for optional values
        class UNDEFINED:
            """Sentinel class for undefined values."""

            pass

        typing_stub.UNDEFINED = UNDEFINED
        typing_stub.UndefinedType = type
        homeassistant.helpers.typing = typing_stub
        sys.modules["homeassistant.helpers.typing"] = typing_stub

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
