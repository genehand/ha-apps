"""Home Assistant helpers stub modules.

Provides device_registry, config_validation, entity_platform, aiohttp_client,
issue_registry, entity_registry, entity_component, config_entry_oauth2_flow,
event, restore_state, dispatcher, service_info, network, storage, and instance_id.
"""

import asyncio
import sys
import types
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import aiohttp

from ..logging import get_logger

_LOGGER = get_logger(__name__)

# Global device registry for lookups without hass
_global_device_registry = None


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
    name_by_user: Optional[str] = None
    sw_version: Optional[str] = None
    hw_version: Optional[str] = None
    entry_type: Optional[str] = None
    via_device: Optional[tuple] = None
    configuration_url: Optional[str] = None
    suggested_area: Optional[str] = None


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

    def async_get_or_create_for_config_entry(self, config_entry_id: str):
        """Get all devices associated with a config entry."""
        return [
            device
            for device in self._devices.values()
            if config_entry_id in device.config_entries
        ]

    def async_update_device(
        self,
        device_id: str,
        *,
        area_id: str = None,
        name: str = None,
        name_by_user: str = None,
        new_identifiers: set = None,
        merge_identifiers: set = None,
        suggested_area: str = None,
        sw_version: str = None,
        hw_version: str = None,
        via_device_id: str = None,
        remove_config_entry_id: str = None,
    ):
        """Update device properties."""
        device = self._devices.get(device_id)
        if not device:
            return None

        name_updated = False
        if name is not None and device.name != name:
            device.name = name
            name_updated = True
        if name_by_user is not None:
            device.name_by_user = name_by_user
        if sw_version is not None:
            device.sw_version = sw_version
        if hw_version is not None:
            device.hw_version = hw_version
        if suggested_area is not None:
            device.suggested_area = suggested_area
        if via_device_id is not None:
            device.via_device_id = via_device_id
        if remove_config_entry_id is not None:
            device.config_entries.discard(remove_config_entry_id)

        if name_updated and self.hass:
            try:
                asyncio.create_task(self._republish_device_discovery(device))
            except Exception:
                pass

        return device

    async def _republish_device_discovery(self, device):
        """Republish MQTT discovery for all entities of a device."""
        try:
            loader = self.hass.data.get("integration_loader")
            if not loader:
                return

            all_entities = loader.get_entities()
            for entity in all_entities:
                device_info = getattr(entity, "device_info", None)
                if device_info and hasattr(device_info, "get"):
                    identifiers = device_info.get("identifiers", set())
                    if device.identifiers == identifiers:
                        if hasattr(entity, "_publish_mqtt_discovery"):
                            await entity._publish_mqtt_discovery()
        except Exception:
            pass


def _get_device_registry(hass):
    """Get the device registry for the Home Assistant instance."""
    global _global_device_registry
    if hass is not None:
        if not hasattr(hass, "data"):
            hass.data = {}
        if "device_registry" not in hass.data:
            hass.data["device_registry"] = DeviceRegistry(hass)
            _global_device_registry = hass.data["device_registry"]
        return hass.data["device_registry"]
    if _global_device_registry is None:
        _global_device_registry = DeviceRegistry(None)
    return _global_device_registry


class DeviceEntryType(Enum):
    """Device entry type."""

    SERVICE = "service"


class IssueSeverity(Enum):
    """Issue severity levels."""

    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"


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


class RestoreEntity:
    """Stub for RestoreEntity that supports async_get_last_state."""

    async def async_get_last_state(self):
        """Return the last state for this entity."""
        return None


# Store for signal callbacks: {signal: [callbacks]}
_dispatcher_signals = {}


def _async_dispatcher_connect(hass, signal, target):
    """Connect a receiver to a signal."""
    _LOGGER.debug(f"Dispatcher connect: {signal}")
    if signal not in _dispatcher_signals:
        _dispatcher_signals[signal] = []
    _dispatcher_signals[signal].append(target)

    def remove():
        if signal in _dispatcher_signals and target in _dispatcher_signals[signal]:
            _dispatcher_signals[signal].remove(target)

    return remove


def _async_dispatcher_send(hass, signal, *args):
    """Send a signal to all receivers (async version)."""
    _LOGGER.debug(f"Dispatcher send: {signal} with args: {args}")
    if signal in _dispatcher_signals:
        for callback in _dispatcher_signals[signal]:
            try:
                result = callback(*args)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                _LOGGER.error(f"Error in dispatcher callback for {signal}: {e}")


def _dispatcher_send(hass, signal, *args):
    """Send a signal to all receivers (sync version)."""
    _LOGGER.debug(f"Dispatcher send: {signal}")
    _async_dispatcher_send(hass, signal, *args)


# Store state in mutable containers so functions see updates
_instance_state = {"path": None, "cached_uuid": None}


def _get_data_dir():
    data_path = Path("/data")
    if data_path.exists() and data_path.is_dir():
        return data_path
    return Path("./data")


def _load_or_create_uuid():
    if _instance_state["cached_uuid"] is not None:
        return _instance_state["cached_uuid"]

    uuid_path = _instance_state["path"]

    try:
        if uuid_path.exists():
            uuid_str = uuid_path.read_text().strip()
            if uuid_str:
                _instance_state["cached_uuid"] = uuid_str
                return uuid_str
    except Exception:
        pass

    new_uuid = uuid.uuid4().hex
    try:
        uuid_path.parent.mkdir(parents=True, exist_ok=True)
        uuid_path.write_text(new_uuid)
    except Exception:
        pass

    _instance_state["cached_uuid"] = new_uuid
    return new_uuid


def _save_uuid(uuid_str):
    uuid_path = _instance_state["path"]
    try:
        uuid_path.parent.mkdir(parents=True, exist_ok=True)
        uuid_path.write_text(uuid_str)
        _instance_state["cached_uuid"] = uuid_str
    except Exception:
        _instance_state["cached_uuid"] = uuid_str


async def _async_get_instance_id(hass) -> str:
    """Get unique ID for the hass instance."""
    return _load_or_create_uuid()


async def _async_recreate_instance_id(hass) -> str:
    """Recreate a new unique ID for the hass instance."""
    new_uuid = uuid.uuid4().hex
    _save_uuid(new_uuid)
    return new_uuid


_client_session_cache = {}
_created_sessions = []


def _async_get_clientsession(hass, verify_ssl=True):
    """Get aiohttp ClientSession with caching."""
    key = (id(hass), verify_ssl)
    if key not in _client_session_cache:
        connector = aiohttp.TCPConnector(verify_ssl=verify_ssl)
        session = aiohttp.ClientSession(connector=connector)
        _client_session_cache[key] = session
    return _client_session_cache[key]


def _async_create_clientsession(hass, verify_ssl=True):
    """Create a new aiohttp ClientSession."""
    connector = aiohttp.TCPConnector(verify_ssl=verify_ssl)
    session = aiohttp.ClientSession(connector=connector)
    _created_sessions.append(session)
    return session


async def _async_close_clientsessions():
    """Close all aiohttp ClientSessions."""
    for key, session in list(_client_session_cache.items()):
        if not session.closed:
            await session.close()
    _client_session_cache.clear()

    for session in list(_created_sessions):
        if not session.closed:
            await session.close()
    _created_sessions.clear()


def create_helpers_stubs(hass, homeassistant, config_entries_module, entity_module, selectors_module):
    """Create all homeassistant.helpers.* stub modules."""

    homeassistant.helpers = types.ModuleType("homeassistant.helpers")

    # device_registry
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceEntry = DeviceEntry
    device_registry.DeviceRegistry = DeviceRegistry
    device_registry.DeviceInfo = DeviceInfo
    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.CONNECTION_NETWORK_MAC = "mac"
    device_registry.CONNECTION_UPNP = "upnp"
    device_registry.CONNECTION_ASSUMED = "assumed"
    device_registry.EVENT_DEVICE_REGISTRY_UPDATED = "device_registry_updated"
    device_registry.async_get = _get_device_registry
    device_registry.format_mac = lambda x: x
    device_registry.async_entries_for_config_entry = lambda *args, **kwargs: []
    device_registry.async_get_device = lambda *args, **kwargs: None
    homeassistant.helpers.device_registry = device_registry
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    # entity - already created by import, just attach DeviceInfo
    entity_module.DeviceInfo = DeviceInfo
    homeassistant.helpers.entity = entity_module
    sys.modules["homeassistant.helpers.entity"] = entity_module

    # config_validation
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

    def ensure_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def multi_select(options):
        def validator(value):
            if value is None:
                return []
            if not isinstance(value, list):
                value = [value]
            return [v for v in value if v in options]
        return validator

    def latitude_validator(value):
        try:
            lat = float(value)
            if -90 <= lat <= 90:
                return lat
            raise ValueError(f"Latitude must be between -90 and 90, got {value}")
        except (TypeError, ValueError):
            raise ValueError(f"Invalid latitude: {value}")

    def longitude_validator(value):
        try:
            lon = float(value)
            if -180 <= lon <= 180:
                return lon
            raise ValueError(f"Longitude must be between -180 and 180, got {value}")
        except (TypeError, ValueError):
            raise ValueError(f"Invalid longitude: {value}")

    config_validation.ensure_list = ensure_list
    config_validation.multi_select = multi_select
    config_validation.latitude = latitude_validator
    config_validation.longitude = longitude_validator
    homeassistant.helpers.config_validation = config_validation
    sys.modules["homeassistant.helpers.config_validation"] = config_validation

    # entity_platform
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.async_get_platforms = lambda *args, **kwargs: []
    entity_platform.AddEntitiesCallback = lambda *args, **kwargs: None

    class _MockPlatform:
        def async_register_entity_service(self, *args, **kwargs):
            pass

    class _CurrentPlatform:
        def get(self):
            return _MockPlatform()

    entity_platform.async_get_current_platform = lambda: _MockPlatform()
    entity_platform.current_platform = _CurrentPlatform()
    homeassistant.helpers.entity_platform = entity_platform
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    # aiohttp_client
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = _async_get_clientsession
    aiohttp_client.async_create_clientsession = _async_create_clientsession
    aiohttp_client._async_close_clientsessions = _async_close_clientsessions
    homeassistant.helpers.aiohttp_client = aiohttp_client
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    # issue_registry
    issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")
    issue_registry.async_create_issue = lambda *args, **kwargs: None
    issue_registry.async_delete_issue = lambda *args, **kwargs: None
    issue_registry.IssueSeverity = IssueSeverity
    homeassistant.helpers.issue_registry = issue_registry
    sys.modules["homeassistant.helpers.issue_registry"] = issue_registry

    # entity_registry
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    from ..entity import EntityRegistry

    def async_get(hass):
        registry = EntityRegistry()
        if hass:
            registry.setup(hass)
        return registry

    entity_registry.async_get = async_get
    entity_registry.RegistryEntry = type("RegistryEntry", (), {"entity_id": None})
    entity_registry.async_entries_for_config_entry = lambda hass, config_entry_id: []
    homeassistant.helpers.entity_registry = entity_registry
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

    # entity_component
    entity_component = types.ModuleType("homeassistant.helpers.entity_component")
    entity_component.DATA_INSTANCES = {}
    homeassistant.helpers.entity_component = entity_component
    sys.modules["homeassistant.helpers.entity_component"] = entity_component

    # config_entry_oauth2_flow
    oauth2_flow = types.ModuleType("homeassistant.helpers.config_entry_oauth2_flow")
    oauth2_flow.OAuth2Session = OAuth2Session
    oauth2_flow.AbstractOAuth2Implementation = AbstractOAuth2Implementation
    oauth2_flow.LocalOAuth2Implementation = LocalOAuth2Implementation

    # OAuth2FlowHandler base class
    from ..config_entries import ConfigFlow

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
                return await self.async_oauth_create_entry(user_input)

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
            return await self.async_step_user(user_input)

        async def async_oauth_create_entry(self, data):
            return self.async_create_entry(title="OAuth2 Manual Entry", data=data)

        async def async_step_pick_implementation(self, user_input=None):
            return await self.async_step_user(user_input)

    oauth2_flow.OAuth2FlowHandler = OAuth2FlowHandler
    oauth2_flow.AbstractOAuth2FlowHandler = OAuth2FlowHandler
    oauth2_flow.async_get_implementations = lambda hass, domain: []
    oauth2_flow.async_register_implementation = lambda hass, domain, implementation: None
    oauth2_flow.async_get_config_entry_implementation = lambda hass, config_entry: None

    homeassistant.helpers.config_entry_oauth2_flow = oauth2_flow
    sys.modules["homeassistant.helpers.config_entry_oauth2_flow"] = oauth2_flow

    # event
    event = types.ModuleType("homeassistant.helpers.event")

    def async_track_point_in_time(hass, action, point_in_time):
        return lambda: None

    def async_track_state_change_event(hass, entity_ids, action):
        return lambda: None

    def async_track_time_interval(hass, action, interval):
        async def _interval_loop():
            while True:
                await asyncio.sleep(interval.total_seconds())
                try:
                    result = action()
                    if asyncio.iscoroutine(result) or asyncio.iscoroutinefunction(action):
                        await result
                except Exception:
                    pass

        task = hass.async_create_task(_interval_loop())

        def cancel():
            task.cancel()

        return cancel

    def async_call_later(hass, delay, action):
        from datetime import timedelta

        if isinstance(delay, timedelta):
            delay = delay.total_seconds()

        async def _delayed_call():
            await asyncio.sleep(delay)
            try:
                result = action()
                if asyncio.iscoroutine(result) or asyncio.iscoroutinefunction(action):
                    await result
            except Exception:
                pass

        task = hass.async_create_task(_delayed_call())

        def cancel():
            task.cancel()

        return cancel

    event.async_track_point_in_time = async_track_point_in_time
    event.async_track_state_change_event = async_track_state_change_event
    event.async_track_time_interval = async_track_time_interval
    event.async_call_later = async_call_later
    event.EventStateChangedData = type("EventStateChangedData", (), {})
    homeassistant.helpers.event = event
    sys.modules["homeassistant.helpers.event"] = event

    # restore_state
    restore_state = types.ModuleType("homeassistant.helpers.restore_state")
    restore_state.RestoreEntity = RestoreEntity
    restore_state.ExtraStoredData = type("ExtraStoredData", (), {})
    restore_state.RestoredExtraData = type("RestoredExtraData", (), {})
    restore_state.async_get = lambda hass: None
    homeassistant.helpers.restore_state = restore_state
    sys.modules["homeassistant.helpers.restore_state"] = restore_state

    # dispatcher
    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_connect = _async_dispatcher_connect
    dispatcher.async_dispatcher_send = _async_dispatcher_send
    dispatcher.dispatcher_send = _dispatcher_send
    homeassistant.helpers.dispatcher = dispatcher
    sys.modules["homeassistant.helpers.dispatcher"] = dispatcher

    # service_info
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

    # network
    network = types.ModuleType("homeassistant.helpers.network")
    network.get_url = lambda hass, *, prefer_external=False, allow_cloud=True: "http://localhost:8123"
    homeassistant.helpers.network = network
    sys.modules["homeassistant.helpers.network"] = network

    # storage
    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:
        def __init__(self, hass, version, key):
            self._data = {}
            self._delay_handle = None

        async def async_load(self):
            return self._data

        async def async_save(self, data):
            self._data = data

        def async_delay_save(self, data_func, delay):
            async def _delayed_save():
                await asyncio.sleep(delay)
                data = data_func()
                await self.async_save(data)
                self._delay_handle = None

            if self._delay_handle:
                self._delay_handle.cancel()
            self._delay_handle = asyncio.create_task(_delayed_save())

        def __class_getitem__(cls, item):
            return cls

    storage.Store = Store
    homeassistant.helpers.storage = storage
    sys.modules["homeassistant.helpers.storage"] = storage

    # instance_id
    _instance_state["path"] = _get_data_dir() / ".instance_uuid"
    instance_id = types.ModuleType("homeassistant.helpers.instance_id")
    instance_id.async_get = _async_get_instance_id
    instance_id.async_recreate = _async_recreate_instance_id
    instance_id._instance_state = _instance_state
    homeassistant.helpers.instance_id = instance_id
    sys.modules["homeassistant.helpers.instance_id"] = instance_id

    # selector
    homeassistant.helpers.selector = selectors_module
    sys.modules["homeassistant.helpers.selector"] = selectors_module

    sys.modules["homeassistant.helpers"] = homeassistant.helpers

    return homeassistant
