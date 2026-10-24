"""Home Assistant helpers stub modules.

Provides device_registry, config_validation, entity_platform, aiohttp_client,
issue_registry, entity_registry, entity_component, config_entry_oauth2_flow,
event, restore_state, dispatcher, service_info, network, storage, and instance_id.
"""

import asyncio
import inspect
import sys
import types
import uuid
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Never, Optional

import aiohttp
import voluptuous as vol

from ..http import github_session
from ..logging import get_logger

_LOGGER = get_logger(__name__)

# Global device registry for lookups without hass
_global_device_registry = None


@dataclass
class DeviceInfo:
    """Device info for entities.

    Mirrors HA's ``DeviceInfo`` (a ``TypedDict``, i.e. a plain dict at
    runtime), so integrations may read and write it like a dict
    (``device_info[ATTR_VIA_DEVICE] = (DOMAIN, id)``) as well as with the
    constructor kwargs / attribute access used across the shim.

    Keys backed by a dataclass field are always present with their default
    values; item assignment maps onto the field. Keys that are not modeled
    fields are stored in ``_extra`` so item access never fails for them.
    """

    identifiers: set = field(default_factory=set)
    connections: set = field(default_factory=set)
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    sw_version: Optional[str] = None
    hw_version: Optional[str] = None
    via_device: Optional[tuple] = None
    via_device_id: Optional[str] = None
    entry_type: Optional[str] = None
    configuration_url: Optional[str] = None
    suggested_area: Optional[str] = None
    serial_number: Optional[str] = None

    # Dict-protocol storage for keys that are not dataclass fields.
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    _field_names_cache: ClassVar[Optional[tuple]] = None

    @classmethod
    def _field_names(cls) -> tuple:
        """Return the keys backed by dataclass fields (declaration order)."""
        if cls._field_names_cache is None:
            cls._field_names_cache = tuple(
                f.name for f in fields(cls) if not f.name.startswith('_')
            )
        return cls._field_names_cache

    def __getitem__(self, key: str) -> Any:
        """Return the value for ``key`` (a field or a stored extra key)."""
        if key in self._field_names():
            return getattr(self, key)
        return self._extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """Set ``key`` to ``value`` like real HA's dict-based DeviceInfo."""
        if key in self._field_names():
            setattr(self, key, value)
        else:
            self._extra[key] = value

    def __delitem__(self, key: str) -> None:
        """Delete ``key``, resetting a field to its default if it is one."""
        if key in self._field_names():
            item = next(f for f in fields(self) if f.name == key)
            if item.default_factory is not MISSING:
                setattr(self, key, item.default_factory())
            else:
                setattr(self, key, item.default)
        else:
            del self._extra[key]

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` is a field or a stored extra key."""
        return key in self._field_names() or key in self._extra

    def __iter__(self):
        """Iterate over the keys like a dict."""
        return iter(self.keys())

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` or ``default`` when absent."""
        if key in self:
            return self[key]
        return default

    def keys(self) -> list:
        """Return field and extra keys in declaration/insertion order."""
        return list(self._field_names()) + list(self._extra)

    def values(self) -> list:
        """Return the values for :meth:`keys`."""
        return [self[key] for key in self.keys()]

    def items(self) -> list:
        """Return ``(key, value)`` pairs for :meth:`keys`."""
        return [(key, self[key]) for key in self.keys()]

    def update(self, other=(), **kwargs: Any) -> None:
        """Update from a mapping/iterable of pairs plus keyword arguments."""
        if isinstance(other, Mapping):
            for key in other:
                self[key] = other[key]
        else:
            for key, value in other:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value


@dataclass
class DeviceEntry:
    """Device entry stub.

    Mirrors HA's ``DeviceEntry``. Since HA 2026.8 a device belongs to exactly
    one config entry, exposed as :attr:`config_entry_id`; the legacy
    ``config_entries`` set stays for older integrations (upstream keeps it as a
    deprecated compatibility property).
    """

    id: str
    config_entries: set
    connections: set
    identifiers: set
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    model_id: Optional[str] = None
    name: Optional[str] = None
    name_by_user: Optional[str] = None
    sw_version: Optional[str] = None
    hw_version: Optional[str] = None
    serial_number: Optional[str] = None
    entry_type: Optional[str] = None
    via_device: Optional[tuple] = None
    via_device_id: Optional[str] = None
    configuration_url: Optional[str] = None
    suggested_area: Optional[str] = None
    config_subentry_id: Optional[str] = None
    disabled_by: Optional[str] = None

    @property
    def config_entry_id(self) -> Optional[str]:
        """Return the single owning config entry (HA >= 2026.8).

        ``None`` when no config entry owns the device (the shim never writes
        such entries itself, but nothing enforces it here).
        """
        return next(iter(self.config_entries), None)

    @property
    def disabled(self) -> bool:
        """Return whether the device is disabled (HA ``BaseDeviceEntry``)."""
        return self.disabled_by is not None


def _normalize_connections(connections) -> set:
    """Normalize connections to ``(type, identifier)`` tuples, like HA.

    Integrations may still pass the legacy ``{"mac:aa:bb:cc:dd:ee:ff"}`` string
    form. HA normalizes it into a tuple; the shim has to do the same or a
    device registered once per form ends up as two rows.
    """
    normalized = set()
    for connection in connections or ():
        if isinstance(connection, str):
            conn_type, _, identifier = connection.partition(":")
            normalized.add((conn_type, identifier))
        else:
            normalized.add(tuple(connection))
    return normalized


def _normalize_identifiers(identifiers) -> set:
    """Normalize identifiers to hashable tuples.

    HA's ``DeviceInfo.identifiers`` is a set of ``(domain, identifier)`` tuples,
    but integrations built against older code sometimes hand over lists, which
    cannot go into a set.
    """
    return {
        tuple(identifier) if isinstance(identifier, list) else identifier
        for identifier in identifiers or ()
    }


class DeviceRegistry:
    """Device registry stub.

    Creates and updates ``DeviceEntry`` rows from ``entity.device_info`` the way
    HA's entity platform does (see ``shim.entity.register_entity_device``).
    Integrations may rely on a device existing as soon as one of its entities is
    added: localtuya >= 2026.10.0 resolves a sub-device's ``via_device_id``
    through ``async_get_device_id_by_identifier``, which raises when the gateway
    device has no row yet.

    Deviations from upstream, all deliberate:

    * ``config_entry_id`` is not validated against a real config entry (the
      registry is usable standalone, e.g. from tests).
    * Unknown keyword arguments are logged at debug level and ignored instead of
      raising ``TypeError`` - integrations ship ``DeviceInfo`` subclasses with
      extra keys, and aborting a whole platform setup over one is worse than
      dropping it.
    * A new device is not named after its config entry title, so MQTT discovery
      payloads do not gain a ``name`` they never had before.
    """

    def __init__(self, hass):
        self.hass = hass
        self._devices: dict = {}
        # Device ids registered during the current setup of each config entry,
        # so an unload can forget them (HA's ``_live_device_ids``).
        self._live_device_ids: dict = {}

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def async_get(self, device_id):
        """Get a device by id."""
        return self._devices.get(device_id)

    def _get_entry(self, identifiers=None, connections=None, *, config_entry_id=None):
        """Return the first device matching an identifier or connection.

        Mirrors HA's ``DeviceRegistryItems.get_entry``: a match on *any*
        identifier (not the whole set) identifies the device, scoped to one
        config entry when ``config_entry_id`` is given.
        """
        want_identifiers = _normalize_identifiers(identifiers)
        want_connections = _normalize_connections(connections)
        for device in self._devices.values():
            if (
                config_entry_id is not None
                and config_entry_id not in device.config_entries
            ):
                continue
            if want_identifiers & set(device.identifiers):
                return device
            if want_connections & set(device.connections):
                return device
        return None

    def async_get_device(self, identifiers=None, connections=None):
        """Get a device by identifiers or connections (any config entry)."""
        return self._get_entry(identifiers, connections)

    def async_get_device_by_identifier(self, identifier, config_entry_id):
        """Get the device with the identifier, owned by the config entry.

        Identifiers are unique within a config entry, so unlike async_get_device
        the lookup cannot be ambiguous. Mirrors HA's
        DeviceRegistry.async_get_device_by_identifier.
        """
        return self._get_entry({identifier}, config_entry_id=config_entry_id)

    def async_entries_for_config_entry(self, config_entry_id):
        """Return the devices owned by a config entry."""
        return [
            device
            for device in self._devices.values()
            if config_entry_id in device.config_entries
        ]

    def async_get_or_create_for_config_entry(self, config_entry_id: str):
        """Get all devices associated with a config entry.

        Shim-only convenience name kept for existing callers; upstream spells it
        ``async_entries_for_config_entry``.
        """
        return self.async_entries_for_config_entry(config_entry_id)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def async_get_or_create(
        self,
        *,
        config_entry_id=None,
        config_subentry_id=None,
        configuration_url=None,
        connections=None,
        disabled_by=None,
        entry_type=None,
        hw_version=None,
        identifiers=None,
        manufacturer=None,
        model=None,
        model_id=None,
        name=None,
        serial_number=None,
        suggested_area=None,
        sw_version=None,
        translation_key=None,
        translation_placeholders=None,
        via_device_id=None,
        **kwargs,
    ) -> DeviceEntry:
        """Get a device, creating or updating it (HA ``async_get_or_create``).

        Matches an existing device by identifier or connection intersection,
        then merges the identifiers/connections and overwrites only the fields
        that were actually supplied - an entity that reports nothing but
        identifiers must not wipe what another entity of the same device filled
        in. ``default_*`` values only fill gaps, and only on creation, like HA.
        """
        # Deprecated upstream, still emitted by integrations: ``via_device`` is
        # a (domain, identifier) tuple resolved to ``via_device_id`` below.
        default_manufacturer = kwargs.pop("default_manufacturer", None)
        default_model = kwargs.pop("default_model", None)
        default_name = kwargs.pop("default_name", None)
        via_device = kwargs.pop("via_device", None)
        kwargs.pop("created_at", None)
        kwargs.pop("modified_at", None)
        if kwargs:
            _LOGGER.debug(
                "device_registry.async_get_or_create() ignoring unknown keys %s "
                "(identifiers=%s)",
                sorted(kwargs),
                identifiers,
            )

        identifiers = _normalize_identifiers(identifiers)
        connections = _normalize_connections(connections)

        device = self._get_entry(
            identifiers, connections, config_entry_id=config_entry_id
        )
        created = device is None
        if created:
            device = DeviceEntry(
                id=self._derive_device_id(
                    identifiers, connections, config_entry_id, name or default_name
                ),
                config_entries=set(),
                connections=set(),
                identifiers=set(),
                disabled_by=disabled_by,
            )
            self._devices[device.id] = device
            if isinstance(via_device, (tuple, list)):
                device.via_device = tuple(via_device)

        if config_entry_id:
            device.config_entries.add(config_entry_id)
        if config_subentry_id is not None:
            device.config_subentry_id = config_subentry_id

        device.identifiers |= identifiers
        device.connections |= connections

        if name is not None:
            device.name = name
        elif default_name is not None and device.name is None:
            device.name = default_name
        if manufacturer is not None:
            device.manufacturer = manufacturer
        elif default_manufacturer is not None and device.manufacturer is None:
            device.manufacturer = default_manufacturer
        if model is not None:
            device.model = model
        elif default_model is not None and device.model is None:
            device.model = default_model

        for attr, value in (
            ("model_id", model_id),
            ("serial_number", serial_number),
            ("sw_version", sw_version),
            ("hw_version", hw_version),
            ("configuration_url", configuration_url),
            ("suggested_area", suggested_area),
            ("entry_type", entry_type),
        ):
            if value is not None:
                setattr(device, attr, value)

        if via_device_id is not None:
            device.via_device_id = via_device_id
        elif isinstance(via_device, (tuple, list)):
            self._resolve_via_device(
                device, tuple(via_device), config_entry_id=config_entry_id
            )

        if config_entry_id:
            self._live_device_ids.setdefault(config_entry_id, set()).add(device.id)
        return device

    def _resolve_via_device(self, device, via_device, *, config_entry_id=None):
        """Resolve a deprecated ``via_device`` identifier tuple to a device id.

        Prefers a parent in the same config entry, then any config entry, like
        HA. An unresolvable (or self-referencing) parent is kept as the raw
        tuple and leaves ``via_device_id`` unset, matching HA's lenient
        behaviour for the deprecated parameter.
        """
        parent = self._get_entry({via_device}, config_entry_id=config_entry_id)
        if parent is None:
            parent = self._get_entry({via_device})
        if parent is None or parent.id == device.id:
            _LOGGER.debug(
                "device %s: via_device %s not resolvable to another device",
                device.id,
                via_device,
            )
            return None
        device.via_device = via_device
        device.via_device_id = parent.id
        return parent.id

    def _derive_device_id(
        self, identifiers, connections, config_entry_id, fallback_name
    ) -> str:
        """Build a stable device id and keep it unique in the registry.

        HA uses an opaque uuid; the shim derives a readable id from the first
        sorted identifier so ids look like ``domain_identifier``. Sorted (not
        set order) so the same device gets the same id on every start.
        """
        keys = sorted(identifiers) if identifiers else sorted(connections)
        if keys:
            # Sorted tuples are comparable as long as each element is a str.
            base = "_".join(str(part) for part in keys[0])
        else:
            base = "_".join(
                str(part)
                for part in (config_entry_id or "shack", fallback_name or "device")
            )
        device_id = base
        suffix = 2
        while device_id in self._devices:
            device_id = f"{base}_{suffix}"
            suffix += 1
        return device_id

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
        if new_identifiers is not None:
            device.identifiers = set(new_identifiers)
        if merge_identifiers is not None:
            device.identifiers |= set(merge_identifiers)

        if name_updated and self.hass:
            try:
                asyncio.create_task(self._republish_device_discovery(device))
            except Exception:
                pass

        return device

    def async_config_entry_unloaded(self, config_entry_id: str) -> None:
        """Forget the live devices of a config entry that unloaded.

        HA parity (``ConfigEntry._async_process_on_unload``): devices survive an
        unload - they are only removed when the entry itself is removed - so this
        only forgets which rows the last setup generation created.
        """
        self._live_device_ids.pop(config_entry_id, None)

    def async_clear_config_entry(self, config_entry_id: str, domain=None) -> None:
        """Remove every device owned by a config entry (HA parity)."""
        self._live_device_ids.pop(config_entry_id, None)
        for device_id, device in list(self._devices.items()):
            if config_entry_id in device.config_entries:
                del self._devices[device_id]
                _LOGGER.debug(
                    "Removed device %s after config entry %s was removed",
                    device_id,
                    config_entry_id,
                )

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


from ..restore import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity as _SharedRestoreEntity,
)


class RestoreEntity(_SharedRestoreEntity):
    """Stub for RestoreEntity that supports async_get_last_state.

    Saves entity state to persistent storage and restores it when
    the entity is re-added to Home Assistant after a restart.
    """

    pass


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
    return await asyncio.to_thread(_load_or_create_uuid)


async def _async_recreate_instance_id(hass) -> str:
    """Recreate a new unique ID for the hass instance."""
    new_uuid = uuid.uuid4().hex
    await asyncio.to_thread(_save_uuid, new_uuid)
    return new_uuid


_client_session_cache = {}
_created_sessions = []


def _async_get_clientsession(hass, verify_ssl=True, **kwargs):
    """Get aiohttp ClientSession with caching.

    Extra ``kwargs`` are forwarded to :class:`aiohttp.ClientSession`, so an
    integration may raise the HTTP parser limits itself (HACS does this for
    GitHub downloads via a per-request ``max_field_size``).
    """
    key = (id(hass), verify_ssl)
    if kwargs:
        # Extra kwargs change the session's behaviour, so don't hand back a
        # cached session built with different settings.
        key += (repr(sorted(kwargs.items(), key=lambda kv: kv[0])),)
    if key not in _client_session_cache:
        connector = aiohttp.TCPConnector(ssl=verify_ssl)
        session = github_session(connector=connector, **kwargs)
        _client_session_cache[key] = session
    return _client_session_cache[key]


def _async_create_clientsession(hass, verify_ssl=True, **kwargs):
    """Create a new aiohttp ClientSession."""
    connector = aiohttp.TCPConnector(ssl=verify_ssl)
    session = github_session(connector=connector, **kwargs)
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

    # typing - mirrors upstream homeassistant/helpers/typing.py
    helpers_typing = types.ModuleType("homeassistant.helpers.typing")

    class UndefinedType(Enum):
        """Singleton type for use with not set sentinel values."""

        _singleton = 0

    helpers_typing.UndefinedType = UndefinedType
    helpers_typing.UNDEFINED = UndefinedType._singleton
    helpers_typing.GPSType = tuple[float, float]
    helpers_typing.ConfigType = dict[str, Any]
    helpers_typing.DiscoveryInfoType = dict[str, Any]
    helpers_typing.ServiceDataType = dict[str, Any]
    helpers_typing.StateType = str | int | float | None
    helpers_typing.TemplateVarsType = Mapping[str, Any] | None
    helpers_typing.NoEventData = Mapping[str, Never]
    helpers_typing.VolSchemaType = vol.Schema | vol.All | vol.Any
    helpers_typing.VolDictType = dict[str | vol.Marker, Any]
    helpers_typing.QueryType = Any
    homeassistant.helpers.typing = helpers_typing
    sys.modules["homeassistant.helpers.typing"] = helpers_typing

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

    def async_entries_for_config_entry(registry, config_entry_id):
        """Return the devices owned by a config entry.

        Mirrors HA's module-level
        ``device_registry.async_entries_for_config_entry(registry, entry_id)``,
        which takes the registry, not ``hass``.
        """
        if registry is None:
            registry = _get_device_registry(None)
        return registry.async_entries_for_config_entry(config_entry_id)

    def async_get_device(hass, identifiers=None, connections=None):
        """Return the device matching any of the identifiers/connections."""
        return _get_device_registry(hass).async_get_device(identifiers, connections)

    device_registry.async_entries_for_config_entry = async_entries_for_config_entry
    device_registry.async_get_device = async_get_device

    def async_get_device_id_by_identifier(hass, identifier, *, config_entry_id):
        """Get the id of the device with the identifier, owned by the config entry.

        Convenience wrapper for linking a device to its via device through
        via_device_id. Mirrors HA's
        device_registry.async_get_device_id_by_identifier.
        """
        device = _get_device_registry(hass).async_get_device_by_identifier(
            identifier, config_entry_id
        )
        if device is None:
            raise ValueError(
                f"There is no device with identifier {identifier} in config entry "
                f"{config_entry_id}"
            )
        return device.id

    device_registry.async_get_device_id_by_identifier = async_get_device_id_by_identifier
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

    def config_entry_only_config_schema(domain: str):
        """Return a config schema for integrations that don't support YAML setup.

        Logs a warning if the domain key appears in a YAML configuration dict.
        """
        def validator(config):
            if isinstance(config, dict) and domain in config:
                _LOGGER.warning(
                    'The %s integration does not support YAML setup, '
                    'please remove it from your configuration file',
                    domain,
                )
            return config
        return validator

    config_validation.ensure_list = ensure_list
    config_validation.multi_select = multi_select
    config_validation.latitude = latitude_validator
    config_validation.longitude = longitude_validator
    config_validation.config_entry_only_config_schema = config_entry_only_config_schema
    homeassistant.helpers.config_validation = config_validation
    sys.modules["homeassistant.helpers.config_validation"] = config_validation

    # entity_platform
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.async_get_platforms = lambda *args, **kwargs: []
    entity_platform.AddEntitiesCallback = lambda *args, **kwargs: None
    entity_platform.AddConfigEntryEntitiesCallback = lambda *args, **kwargs: None

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
    from ..entity import RegistryEntry

    entity_registry.RegistryEntry = RegistryEntry
    entity_registry.async_entries_for_config_entry = lambda hass, config_entry_id: EntityRegistry().async_entries_for_config_entry(config_entry_id)
    homeassistant.helpers.entity_registry = entity_registry
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

    # entity_component
    entity_component = types.ModuleType("homeassistant.helpers.entity_component")
    entity_component.DATA_INSTANCES = {}
    homeassistant.helpers.entity_component = entity_component
    sys.modules["homeassistant.helpers.entity_component"] = entity_component

    # config_entry_oauth2_flow - real OAuth2 implementation
    from .oauth2 import (
        OAuth2Session,
        AbstractOAuth2Implementation,
        LocalOAuth2Implementation,
        LocalOAuth2ImplementationWithPkce,
        AbstractOAuth2FlowHandler,
        async_register_implementation,
        async_get_implementations,
        async_get_config_entry_implementation,
        async_oauth2_request,
        async_add_implementation_provider,
        AUTH_CALLBACK_PATH,
        MY_AUTH_CALLBACK_PATH,
    )

    oauth2_flow = types.ModuleType("homeassistant.helpers.config_entry_oauth2_flow")
    oauth2_flow.OAuth2Session = OAuth2Session
    oauth2_flow.AbstractOAuth2Implementation = AbstractOAuth2Implementation
    oauth2_flow.LocalOAuth2Implementation = LocalOAuth2Implementation
    oauth2_flow.LocalOAuth2ImplementationWithPkce = LocalOAuth2ImplementationWithPkce
    oauth2_flow.AbstractOAuth2FlowHandler = AbstractOAuth2FlowHandler
    oauth2_flow.OAuth2FlowHandler = AbstractOAuth2FlowHandler
    oauth2_flow.async_register_implementation = async_register_implementation
    oauth2_flow.async_get_implementations = async_get_implementations
    oauth2_flow.async_get_config_entry_implementation = async_get_config_entry_implementation
    oauth2_flow.async_oauth2_request = async_oauth2_request
    oauth2_flow.async_add_implementation_provider = async_add_implementation_provider
    oauth2_flow.AUTH_CALLBACK_PATH = AUTH_CALLBACK_PATH
    oauth2_flow.MY_AUTH_CALLBACK_PATH = MY_AUTH_CALLBACK_PATH

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
                    if asyncio.iscoroutine(result) or inspect.iscoroutinefunction(action):
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
                if asyncio.iscoroutine(result) or inspect.iscoroutinefunction(action):
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
    restore_state.ExtraStoredData = ExtraStoredData
    restore_state.RestoredExtraData = RestoredExtraData
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

    import json as _json
    import os as _os

    class Store:
        """Persistent key-value store (stub matching homeassistant.helpers.storage.Store).

        Data is saved to ``{hass.config_dir}/.storage/{key}`` in the same format
        as the real HA implementation:

        .. code-block:: json

            {
              "version": <int>,
              "minor_version": <int>,
              "key": "<key>",
              "data": <user_data>
            }

        Thread-safe I/O is performed via the hass executor to avoid blocking
        the event loop.
        """

        def __init__(
            self,
            hass,
            version,
            key,
            private=False,
            *,
            atomic_writes=False,
            encoder=None,
            max_readable_version=None,
            minor_version=1,
            read_only=False,
            **kwargs,
        ):
            """Initialize the store.

            Args:
                hass: HomeAssistant instance (must have ``hass.config.path``).
                version: Storage version (for future migrations).
                key: Unique key for this store, used as the filename in .storage/.
                private: Reserved for future use; file permissions.
                atomic_writes: If True, use atomic write (temp file + rename).
                encoder: Custom JSON encoder (optional).
                max_readable_version: Maximum readable version (defaults to version).
                minor_version: Minor storage version.
                read_only: If True, skip writes.
            """
            self.hass = hass
            self.version = version
            self.minor_version = minor_version
            self.key = key
            self._private = private
            self._atomic_writes = atomic_writes
            self._encoder = encoder
            self._read_only = read_only
            self._max_readable_version = (
                max_readable_version if max_readable_version is not None else version
            )
            self._data: dict | None = None
            self._delay_handle: asyncio.TimerHandle | None = None
            self._write_lock = asyncio.Lock()

        @property
        def path(self):
            """Return the full filesystem path for this store."""
            return Path(str(self.hass.config.path(".storage", self.key)))

        def make_read_only(self) -> None:
            """Irreversibly make the store read-only."""
            self._read_only = True

        def set_load_empty(self) -> None:
            """Skip loading from disk and become read-only."""
            self._read_only = True
            self._data = {}

        async def _async_load_json(self, filepath):
            """Load JSON from file in executor thread. Returns None on failure."""
            try:
                return await self.hass.async_add_executor_job(
                    self._load_json_sync, filepath
                )
            except Exception as exc:
                _LOGGER.error("Error loading store %s: %s", self.key, exc)
                return None

        def _load_json_sync(self, filepath):
            """Synchronous JSON load."""
            if not filepath.exists():
                return None
            with open(filepath, "r") as f:
                return _json.load(f)

        async def _async_save_json(self, filepath, data):
            """Save JSON to file atomically in executor thread."""
            if self._read_only:
                return
            try:
                await self.hass.async_add_executor_job(
                    self._save_json_sync, filepath, data
                )
            except Exception as exc:
                _LOGGER.error("Error saving store %s: %s", self.key, exc)

        def _save_json_sync(self, filepath, data):
            """Synchronous atomic JSON save."""
            # Ensure directory exists
            _os.makedirs(str(filepath.parent), exist_ok=True)
            if self._atomic_writes:
                # Atomic write: temp file + rename
                tmp_path = filepath.with_suffix(".json.tmp")
                with open(tmp_path, "w") as f:
                    _json.dump(data, f, indent=2, default=str, cls=self._encoder)
                tmp_path.rename(filepath)
            else:
                with open(filepath, "w") as f:
                    _json.dump(data, f, indent=2, default=str, cls=self._encoder)

        async def async_load(self):
            """Load data from disk.

            Returns the stored data dict, or None if the file doesn't exist
            or can't be read.

            This matches the real HA behaviour where a missing .storage file
            returns None so the caller can fall back to defaults.
            """
            raw = await self._async_load_json(self.path)
            if raw is None:
                return None
            stored = raw.get("data")
            return stored

        async def async_save(self, data):
            """Persist data to disk immediately.

            Wraps data in the standard HA storage envelope:
            {version, minor_version, key, data}.
            """
            envelope = {
                "version": self.version,
                "minor_version": self.minor_version,
                "key": self.key,
                "data": data,
            }
            async with self._write_lock:
                self._data = envelope
                await self._async_save_json(self.path, envelope)

        def async_delay_save(self, data_func, delay=0):
            """Persist data to disk after a delay.

            data_func is a callable that returns the data to save.
            If called multiple times before the delay expires, only the
            latest data_func is used.
            """
            if self._read_only:
                return
            self._data = {"data_func": data_func}
            self._cancel_delayed_save()
            loop = asyncio.get_running_loop()
            self._delay_handle = loop.call_later(delay, self._async_delayed_save_cb)

        def _cancel_delayed_save(self):
            """Cancel a pending delayed save."""
            if self._delay_handle is not None:
                self._delay_handle.cancel()
                self._delay_handle = None

        def _async_delayed_save_cb(self):
            """Callback triggered by the delayed save timer."""
            self._delay_handle = None
            # Schedule the actual save as a task so it can be awaited
            asyncio.create_task(self._async_do_delayed_save())

        async def _async_do_delayed_save(self):
            """Execute the delayed save."""
            async with self._write_lock:
                if self._data is None:
                    return
                data_func = self._data.get("data_func")
                if data_func is None:
                    return
                try:
                    data = data_func()
                except Exception as exc:
                    _LOGGER.error(
                        "Error in delayed save data_func for %s: %s", self.key, exc
                    )
                    return
                self._data = None
                envelope = {
                    "version": self.version,
                    "minor_version": self.minor_version,
                    "key": self.key,
                    "data": data,
                }
                await self._async_save_json(self.path, envelope)

        async def async_remove(self):
            """Delete the stored file from disk."""
            self._cancel_delayed_save()
            self._data = None
            filepath = self.path
            try:
                await self.hass.async_add_executor_job(_os.unlink, str(filepath))
            except FileNotFoundError:
                pass
            except Exception as exc:
                _LOGGER.error("Error removing store %s: %s", self.key, exc)

        def __class_getitem__(cls, item):
            """Support generic type hint syntax (Store[MyType])."""
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
