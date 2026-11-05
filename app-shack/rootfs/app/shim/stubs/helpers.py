"""Home Assistant helpers stub modules.

Provides device_registry, config_validation, entity_platform, aiohttp_client,
entity_registry, entity_component, config_entry_oauth2_flow, event,
restore_state, service_info and selector.

Everything else in ``homeassistant.helpers`` comes from the wheel -- see
``UPSTREAM_HELPER_MODULES`` in import_patch.py (storage, dispatcher, start,
instance_id, json, network, area/floor/label/category/issue registries,
debounce, ratelimit, location, sun, trace). A name must never be in both places:
``sys.modules`` wins, so the stub would silently shadow the real module.
"""

import asyncio
import inspect
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar, Literal, NotRequired, Optional, TypedDict

import aiohttp
import voluptuous as vol

from ..http import github_session
from ..logging import get_logger

_LOGGER = get_logger(__name__)

# Default for "argument not passed" where ``None`` is a meaningful value
# (``disabled_by=None`` enables a device, ``name=None`` clears a name).
_NOT_SET = object()

# Global device registry for lookups without hass
_global_device_registry = None


class _EventEntityRegistryUpdatedData_CreateRemove(TypedDict):
    """Entity registry update event payload for create/remove (HA shape)."""

    action: Literal["create", "remove"]
    entity_id: str


class _EventEntityRegistryUpdatedData_Update(TypedDict):
    """Entity registry update event payload for update (HA shape)."""

    action: Literal["update"]
    entity_id: str
    changes: dict[str, Any]
    old_entity_id: NotRequired[str]


_EventEntityRegistryUpdatedData = (
    _EventEntityRegistryUpdatedData_CreateRemove
    | _EventEntityRegistryUpdatedData_Update
)


def _undefined():
    """Return Home Assistant's ``UNDEFINED`` sentinel.

    It has to be the *upstream* object: integrations compare
    ``device_info.name is UNDEFINED`` against the sentinel they imported from
    ``homeassistant.helpers.typing``. Resolved lazily because this module is
    imported while the import patch is being built, before the adopted upstream
    helper is attached to ``sys.modules``.
    """
    from homeassistant.helpers.typing import UNDEFINED  # noqa: PLC0415

    return UNDEFINED


def _is_undefined(value: Any) -> bool:
    """Return whether ``value`` is Home Assistant's ``UNDEFINED`` sentinel."""
    from homeassistant.helpers.typing import UNDEFINED  # noqa: PLC0415

    return value is UNDEFINED


@dataclass
class DeviceInfo:
    """Device info for entities (HA 2026.11: a dataclass, not a TypedDict).

    Fields left unset are ``UNDEFINED``, not ``None`` - ``None`` is a value like
    any other (``via_device_id=None`` removes the link). ``as_dict()`` returns
    only the fields that were set; the JSON helpers serialize the whole
    dataclass, unset fields included, which is why it exists upstream.

    Attribute access is the primary surface. The pre-2026.11 dict protocol
    (``info["name"]``, ``.get``, ``.update``, ``**info``, ``"name" in info``)
    still works: it is exactly the compatibility layer upstream keeps until it is
    deprecated, and integrations written against a TypedDict need it. Keys that
    are not modelled fields are stored in ``_extra`` instead of raising, the
    documented deviation from upstream's ``TypeError``.
    """

    configuration_url: Any = field(default_factory=_undefined)
    connections: Any = field(default_factory=_undefined)
    entry_type: Any = field(default_factory=_undefined)
    identifiers: Any = field(default_factory=_undefined)
    manufacturer: Any = field(default_factory=_undefined)
    model: Any = field(default_factory=_undefined)
    model_id: Any = field(default_factory=_undefined)
    name: Any = field(default_factory=_undefined)
    serial_number: Any = field(default_factory=_undefined)
    suggested_area: Any = field(default_factory=_undefined)
    sw_version: Any = field(default_factory=_undefined)
    hw_version: Any = field(default_factory=_undefined)
    translation_key: Any = field(default_factory=_undefined)
    translation_placeholders: Any = field(default_factory=_undefined)
    via_device_id: Any = field(default_factory=_undefined)
    # Deprecated fields: accepted through HA 2027.8/2027.9, resolved by the
    # registry (`via_device` -> `via_device_id`, `default_*` -> gaps only).
    created_at: Any = field(default_factory=_undefined)
    default_manufacturer: Any = field(default_factory=_undefined)
    default_model: Any = field(default_factory=_undefined)
    default_name: Any = field(default_factory=_undefined)
    modified_at: Any = field(default_factory=_undefined)
    via_device: Any = field(default_factory=_undefined)

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
        """Return the value for ``key``; a field that is unset raises KeyError."""
        if key in self._field_names():
            value = getattr(self, key)
            if not _is_undefined(value):
                return value
            raise KeyError(key)
        return self._extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """Set ``key`` to ``value`` like the deprecated dict-based DeviceInfo."""
        if key in self._field_names():
            setattr(self, key, value)
        else:
            self._extra[key] = value

    def __delitem__(self, key: str) -> None:
        """Unset ``key`` (a field back to UNDEFINED, an extra key removed)."""
        if key in self._field_names():
            setattr(self, key, _undefined())
        else:
            del self._extra[key]

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` is a field that is set, or a stored extra key."""
        if key in self._field_names():
            return not _is_undefined(getattr(self, key))
        return key in self._extra

    def __iter__(self):
        """Iterate over the keys like a dict."""
        return iter(self.keys())

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` or ``default`` when unset/absent."""
        if key in self:
            return self[key]
        return default

    def keys(self) -> list:
        """Return the set field names and extra keys, in declaration order."""
        return [name for name in self._field_names() if name in self] + list(
            self._extra
        )

    def values(self) -> list:
        """Return the values for :meth:`keys`."""
        return [self[key] for key in self.keys()]

    def items(self) -> list:
        """Return ``(key, value)`` pairs for :meth:`keys`."""
        return [(key, self[key]) for key in self.keys()]

    def as_dict(self) -> dict:
        """Return only the fields that were set (HA 2026.11 serialization)."""
        return {key: self[key] for key in self.keys()}

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
class ChildDeviceInfo:
    """Device info for a child device (HA 2026.11 child devices).

    A child device is a logical part of one physical product (an outlet of a
    power strip, a gang of a switch). It carries only what makes sense for a
    part: identifiers, a name, its parent's device id, and name translation.
    ``identifiers`` and ``parent_device_id`` are required.
    """

    identifiers: set
    parent_device_id: str
    name: Any = field(default_factory=_undefined)
    suggested_area: Any = field(default_factory=_undefined)
    translation_key: Any = field(default_factory=_undefined)
    translation_placeholders: Any = field(default_factory=_undefined)

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

    __getitem__ = DeviceInfo.__getitem__
    __setitem__ = DeviceInfo.__setitem__
    __delitem__ = DeviceInfo.__delitem__
    __contains__ = DeviceInfo.__contains__
    __iter__ = DeviceInfo.__iter__
    get = DeviceInfo.get
    keys = DeviceInfo.keys
    values = DeviceInfo.values
    items = DeviceInfo.items
    as_dict = DeviceInfo.as_dict
    update = DeviceInfo.update


class DeviceInfoError(Exception):
    """Invalid device info (HA 2026.11).

    Upstream subclasses ``HomeAssistantError``; the shim keeps the same shape and
    attributes but a plain base, because this module is built before the upstream
    ``exceptions`` module is registered and the shim never raises it (it stays
    lenient where HA raises - see ``docs/device-registry.md``).
    """

    def __init__(self, domain, device_info, message):
        """Initialize the error."""
        super().__init__(
            f"Invalid device info {device_info} for '{domain}' config entry: "
            f"{message}"
        )
        self.device_info = device_info
        self.domain = domain


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
    # Set on a child device (HA 2026.11): the ``DeviceEntry.id`` of its parent.
    parent_device_id: Optional[str] = None
    configuration_url: Optional[str] = None
    suggested_area: Optional[str] = None
    # The assigned area (upstream ``DeviceEntry.area_id``), as set through
    # ``async_update_device(area_id=...)``, by ``area_registry`` cascades, or -
    # on creation only - from the ``suggested_area`` name hint above.
    area_id: Optional[str] = None
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


@dataclass
class ChildDeviceEntry(DeviceEntry):
    """A child device row: a logical part of a parent device (HA 2026.11).

    Distinct type so ``isinstance(entry, ChildDeviceEntry)`` works the way it
    does upstream. It carries none of the connectivity or hardware identity of
    its parent (connections, manufacturer, model, firmware, serial), and follows
    the parent's lifecycle: removing or disabling the parent cascades down.
    """


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
    * ``config_entry_id``/``parent_device_id`` are not validated against real
      config entries or registration order (HA raises ``DeviceInfoError`` for an
      unknown parent); a child with a missing parent is kept as a row and the
      link simply does not resolve.
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

    def async_get(self, device_id, include_child_devices=True):
        """Get a device or child device by id.

        ``include_child_devices`` mirrors upstream's keyword; the shim keeps one
        row collection, so it only gates child ids.
        """
        device = self._devices.get(device_id)
        if not include_child_devices and isinstance(device, ChildDeviceEntry):
            return None
        return device

    def async_entries_for_parent_device(self, parent_device_id):
        """Return the child devices of a parent device (HA 2026.11)."""
        return [
            device
            for device in self._devices.values()
            if device.parent_device_id == parent_device_id
        ]

    def async_child_entries_for_config_entry(self, config_entry_id):
        """Return the child devices owned by a config entry (HA 2026.11)."""
        return [
            device
            for device in self._devices.values()
            if isinstance(device, ChildDeviceEntry)
            and config_entry_id in device.config_entries
        ]

    @property
    def devices(self):
        """All device rows, as a public collection (upstream: ``devices``).

        Upstream exposes ``Collection[DeviceEntry]`` and *raises* if an embedder
        treats it as a mapping, so callers must iterate it. Provided here so code
        can be written once against the same API for the wrapper and a real
        registry (see ``docs/tier-c-spike.md`` §6.1).
        """
        return self._devices.values()

    def _get_entry(
        self,
        identifiers=None,
        connections=None,
        *,
        config_entry_id=None,
        include_child_devices=True,
    ):
        """Return the first device matching an identifier or connection.

        Mirrors HA's ``DeviceRegistryItems.get_entry``: a match on *any*
        identifier (not the whole set) identifies the device, scoped to one
        config entry when ``config_entry_id`` is given. Main-device registration
        decodes ``include_child_devices=False`` (HA keeps the collections apart).
        """
        want_identifiers = _normalize_identifiers(identifiers)
        want_connections = _normalize_connections(connections)
        for device in self._devices.values():
            if not include_child_devices and isinstance(
                device, ChildDeviceEntry
            ):
                continue
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
        """Return the main devices owned by a config entry.

        Children are excluded, like upstream: ``async_child_entries_for_config_entry``
        returns those.
        """
        return [
            device
            for device in self._devices.values()
            if config_entry_id in device.config_entries
            and not isinstance(device, ChildDeviceEntry)
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
            identifiers,
            connections,
            config_entry_id=config_entry_id,
            include_child_devices=False,
        )
        if device is None:
            # A live child already owns these identifiers. Upstream raises; the
            # shim returns the child so a platform setup is not aborted by an
            # integration that registers a main device before/while migrating.
            existing_child = self._get_entry(
                identifiers, connections, config_entry_id=config_entry_id
            )
            if isinstance(existing_child, ChildDeviceEntry):
                _LOGGER.warning(
                    "device_registry.async_get_or_create() called for %s, which is "
                    "a child device (parent %s); returning the child",
                    sorted(identifiers or connections)[:1],
                    existing_child.parent_device_id,
                )
                return existing_child
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

        if created and suggested_area:
            # HA applies the hint once, where the row is created: the name is
            # looked up in the area registry and the area is created when it is
            # new. An existing device keeps the area it has (so an integration
            # that keeps sending the hint cannot undo a user's move), and the
            # hint itself stays on the entry - upstream deprecated it, which is
            # why nothing else reads it.
            device.area_id = self._area_id_for_name(suggested_area)

        if config_entry_id:
            self._live_device_ids.setdefault(config_entry_id, set()).add(device.id)
        return device

    # ------------------------------------------------------------------
    # Device info / child devices (HA 2026.11)
    # ------------------------------------------------------------------

    def async_get_or_create_from_info(
        self, *, config_entry_id, device_info, config_subentry_id=None
    ):
        """Get or create a device from a ``DeviceInfo`` (HA 2026.11).

        The replacement for ``async_get_or_create(config_entry_id=..., **device_info)``:
        only the fields the ``DeviceInfo`` set are passed, so unset fields leave
        the existing row untouched.
        """
        fields = self._device_info_fields(device_info)
        return self.async_get_or_create(
            config_entry_id=config_entry_id,
            config_subentry_id=config_subentry_id,
            **fields,
        )

    def async_get_or_create_child(
        self,
        *,
        config_entry_id,
        parent_device_id,
        identifiers,
        config_subentry_id=None,
        disabled_by=_NOT_SET,
        name=_NOT_SET,
        suggested_area=_NOT_SET,
        translation_key=None,
        translation_placeholders=None,
    ):
        """Get or create a child device (HA 2026.11).

        If ``identifiers`` match an existing device of the same config entry,
        that device is converted to a child **in place**, keeping its id (the
        migration path from ``via_device``-linked devices).
        """
        return self._async_get_or_create_child_from_info(
            config_entry_id=config_entry_id,
            config_subentry_id=config_subentry_id,
            disabled_by=disabled_by,
            device_info=ChildDeviceInfo(
                identifiers=identifiers,
                parent_device_id=parent_device_id,
                name=_undefined() if name is _NOT_SET else name,
                suggested_area=(
                    _undefined() if suggested_area is _NOT_SET else suggested_area
                ),
                translation_key=translation_key,
                translation_placeholders=translation_placeholders,
            ),
        )

    def async_get_or_create_child_from_info(
        self, *, config_entry_id, device_info, config_subentry_id=None
    ):
        """Get or create a child device from a ``ChildDeviceInfo``."""
        return self._async_get_or_create_child_from_info(
            config_entry_id=config_entry_id,
            config_subentry_id=config_subentry_id,
            device_info=device_info,
        )

    def _async_get_or_create_child_from_info(
        self, *, config_entry_id, config_subentry_id, device_info, disabled_by=_NOT_SET
    ):
        """Shared implementation for the child-device entry points."""
        fields = self._device_info_fields(device_info)
        identifiers = _normalize_identifiers(fields.get("identifiers"))
        parent_device_id = fields.get("parent_device_id")
        if _is_undefined(parent_device_id) or not parent_device_id:
            raise ValueError("a child device requires parent_device_id")
        if not identifiers:
            raise ValueError("a child device requires at least one identifier")

        parent = self._devices.get(parent_device_id)
        if parent is None:
            _LOGGER.warning(
                "Child device %s: parent %s is not registered (HA raises here); "
                "keeping the link unresolved",
                sorted(identifiers)[:1],
                parent_device_id,
            )
        elif isinstance(parent, ChildDeviceEntry):
            raise ValueError(
                f"parent_device_id {parent_device_id} is a child device; a child "
                "device can't be the parent of another child device"
            )
        elif config_entry_id not in parent.config_entries:
            raise ValueError(
                f"child device must belong to the same config entry as its parent "
                f"{parent.id}"
            )

        existing = self._get_entry(identifiers, config_entry_id=config_entry_id)

        if isinstance(existing, ChildDeviceEntry):
            if existing.parent_device_id != parent_device_id:
                raise ValueError(
                    "the child device is already registered with a different "
                    f"parent device {existing.parent_device_id}; reparenting is "
                    "not supported, remove the child device first"
                )
            self._apply_child_fields(
                existing, fields, config_entry_id, config_subentry_id
            )
            return existing

        if existing is not None:
            # The integration split an existing main device into parts. Convert
            # in place so device ids (and device-targeted automations) survive.
            child = ChildDeviceEntry(
                id=existing.id,
                config_entries=set(existing.config_entries),
                connections=set(),
                identifiers=set(existing.identifiers) | identifiers,
                parent_device_id=parent_device_id,
                area_id=existing.area_id,
                config_subentry_id=existing.config_subentry_id,
                disabled_by=existing.disabled_by
                or ("device" if parent is not None and parent.disabled else None),
                name=existing.name,
                name_by_user=existing.name_by_user,
            )
            self._devices[child.id] = child
            _LOGGER.debug(
                "Converted device %s to a child of %s (id preserved)",
                child.id,
                parent_device_id,
            )
            self._apply_child_fields(
                child, fields, config_entry_id, config_subentry_id
            )
            return child

        child = ChildDeviceEntry(
            id=self._derive_device_id(
                identifiers,
                None,
                config_entry_id,
                None if _is_undefined(fields.get("name")) else fields.get("name"),
            ),
            config_entries={config_entry_id} if config_entry_id else set(),
            connections=set(),
            identifiers=identifiers,
            parent_device_id=parent_device_id,
            config_subentry_id=config_subentry_id,
            disabled_by=None if disabled_by is _NOT_SET else disabled_by,
        )
        self._devices[child.id] = child
        self._apply_child_fields(child, fields, config_entry_id, config_subentry_id)

        suggested_area = fields.get("suggested_area")
        if not _is_undefined(suggested_area) and suggested_area:
            child.area_id = self._area_id_for_name(suggested_area)

        if config_entry_id:
            self._live_device_ids.setdefault(config_entry_id, set()).add(child.id)
        return child

    @staticmethod
    def _device_info_fields(device_info) -> dict:
        """Return the fields a ``DeviceInfo``/``ChildDeviceInfo`` set."""
        if hasattr(device_info, "as_dict"):
            return dict(device_info.as_dict())
        return dict(device_info)

    def _apply_child_fields(
        self, child, fields, config_entry_id, config_subentry_id
    ) -> None:
        """Apply the mutable child-device fields (only those that were set)."""
        if isinstance(fields.get("identifiers"), (set, list, tuple)):
            child.identifiers |= _normalize_identifiers(fields["identifiers"])
        for attr in ("name", "translation_key", "translation_placeholders"):
            value = fields.get(attr, _NOT_SET)
            if value is _NOT_SET or _is_undefined(value):
                continue
            setattr(child, attr, value)
        suggested_area = fields.get("suggested_area", _NOT_SET)
        if suggested_area is not _NOT_SET and not _is_undefined(suggested_area):
            child.suggested_area = suggested_area
        if config_subentry_id is not None:
            child.config_subentry_id = config_subentry_id
        if config_entry_id:
            child.config_entries.add(config_entry_id)

    def _area_id_for_name(self, suggested_area: str) -> Optional[str]:
        """Resolve a ``suggested_area`` name to an area id, creating the area.

        HA parses the hint in ``DeviceRegistry.async_get_or_create`` (device
        creation only) through ``area_registry.async_get(hass)
        .async_get_or_create(name)``, so an integration that only knows a room
        name still ends up with ``device_entry.area_id`` set - which is what
        integrations read back.

        A failure here must never cost the device row: integrations resolve
        ``via_device_id`` through this registry, so losing the row breaks their
        platform setup. Log it and drop the hint instead.
        """
        if self.hass is None:
            # The process-global registry (no hass at all) has no area registry
            # to resolve against; there is nothing to warn about.
            return None
        try:
            # Local import: the area registry imports this module back (HA does
            # the same at the one call site, for the same reason).
            from homeassistant.helpers import area_registry as ar

            return ar.async_get(self.hass).async_get_or_create(suggested_area).id
        except Exception as err:
            _LOGGER.warning(
                "Could not map suggested_area %r to an area: %s",
                suggested_area,
                err,
            )
            return None

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
        disabled_by=_NOT_SET,
        remove_config_entry_id: str = None,
    ):
        """Update device properties.

        ``disabled_by`` uses a sentinel default because ``None`` is meaningful
        (it enables the device); disabling a parent enables/disables its child
        devices, like HA 2026.11.
        """
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
        if area_id is not None:
            device.area_id = area_id
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

        if disabled_by is not _NOT_SET:
            device.disabled_by = disabled_by
            self._cascade_disabled_by(device)

        if name_updated and self.hass:
            try:
                asyncio.create_task(self._republish_device_discovery(device))
            except Exception:
                pass

        return device

    def _cascade_disabled_by(self, parent) -> None:
        """Apply a parent's disabled state to its child devices (HA 2026.11)."""
        if isinstance(parent, ChildDeviceEntry):
            return
        children = self.async_entries_for_parent_device(parent.id)
        if not children:
            return
        if parent.disabled_by is None:
            for child in children:
                if child.disabled_by == "device":
                    child.disabled_by = None
        elif parent.disabled_by != "config_entry":
            for child in children:
                if child.disabled_by is None:
                    child.disabled_by = "device"

    def async_remove_device(self, device_id: str) -> None:
        """Remove a device or child device (HA 2026.11 cascade).

        Removing a parent removes its child devices: a child has no meaning
        without the physical device it was split from. Inbound ``via_device_id``
        links to the removed device are detached.
        """
        device = self._devices.get(device_id)
        if device is None:
            return

        if not isinstance(device, ChildDeviceEntry):
            for child in self.async_entries_for_parent_device(device_id):
                del self._devices[child.id]

        del self._devices[device_id]

        for other in self._devices.values():
            if other.via_device_id == device_id:
                other.via_device_id = None
        _LOGGER.debug("Removed device %s", device_id)

    def async_clear_area_id(self, area_id: str) -> None:
        """Clear an area from every device that has it.

        Called by ``helpers.area_registry`` when an area is deleted, so the two
        registries cannot drift apart.
        """
        for device in self._devices.values():
            if device.area_id == area_id:
                device.area_id = None

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
    RestoreStateData,
    StoredState,
)


class RestoreEntity(_SharedRestoreEntity):
    """Stub for RestoreEntity that supports async_get_last_state.

    Saves entity state to persistent storage and restores it when
    the entity is re-added to Home Assistant after a restart.
    """

    pass





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

    # homeassistant.helpers.typing comes from upstream (import_patch attaches it
    # after this function runs, since we just replaced the helpers package).

    # device_registry
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceEntry = DeviceEntry
    device_registry.ChildDeviceEntry = ChildDeviceEntry
    device_registry.DeviceRegistry = DeviceRegistry
    device_registry.DeviceInfo = DeviceInfo
    device_registry.ChildDeviceInfo = ChildDeviceInfo
    device_registry.DeviceInfoError = DeviceInfoError
    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.CONNECTION_NETWORK_MAC = "mac"
    device_registry.CONNECTION_UPNP = "upnp"
    device_registry.CONNECTION_ASSUMED = "assumed"
    device_registry.EVENT_DEVICE_REGISTRY_UPDATED = "device_registry_updated"
    device_registry.async_get = _get_device_registry
    device_registry.format_mac = lambda x: x

    def async_entries_for_config_entry(registry, config_entry_id):
        """Return the main devices owned by a config entry.

        Mirrors HA's module-level
        ``device_registry.async_entries_for_config_entry(registry, entry_id)``,
        which takes the registry, not ``hass``. Child devices are returned by
        ``async_child_entries_for_config_entry``, like upstream.
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

    def async_get_effective_area_id(hass, device):
        """Return the effective area, inheriting it for a child device (2026.11)."""
        if device is None:
            return None
        if device.area_id is not None:
            return device.area_id
        if isinstance(device, ChildDeviceEntry):
            parent = _get_device_registry(hass).async_get(
                device.parent_device_id, include_child_devices=False
            )
            if parent is not None:
                return parent.area_id
        return None

    def async_entries_for_parent_device(registry, parent_device_id):
        """Return the child devices of a parent device."""
        if registry is None:
            registry = _get_device_registry(None)
        return registry.async_entries_for_parent_device(parent_device_id)

    def async_child_entries_for_config_entry(registry, config_entry_id):
        """Return the child devices owned by a config entry."""
        if registry is None:
            registry = _get_device_registry(None)
        return registry.async_child_entries_for_config_entry(config_entry_id)

    device_registry.async_get_effective_area_id = async_get_effective_area_id
    device_registry.async_entries_for_parent_device = async_entries_for_parent_device
    device_registry.async_child_entries_for_config_entry = (
        async_child_entries_for_config_entry
    )
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

    def async_entries_for_device(registry, device_id, include_disabled_entities=False):
        """Return the registry entries for a device (HA's registry signature)."""
        if not isinstance(registry, EntityRegistry):
            registry = EntityRegistry()
        return registry.async_entries_for_device(
            device_id, include_disabled_entities=include_disabled_entities
        )

    entity_registry.async_entries_for_device = async_entries_for_device

    # Event surface the adopted upstream registries listen to. ``area_registry``
    # subscribes to entity renames in 2026.11 so its temperature/humidity entity
    # references follow the entity. The event type is an ``EventType`` (a ``str``
    # subclass) upstream, so a plain string hashes identically on the shim bus.
    entity_registry.EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"
    entity_registry.EventEntityRegistryUpdatedData = _EventEntityRegistryUpdatedData
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
    restore_state.async_get = RestoreStateData.async_get
    restore_state.RestoreStateData = RestoreStateData
    restore_state.StoredState = StoredState
    homeassistant.helpers.restore_state = restore_state
    sys.modules["homeassistant.helpers.restore_state"] = restore_state


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

    # selector
    homeassistant.helpers.selector = selectors_module
    sys.modules["homeassistant.helpers.selector"] = selectors_module

    sys.modules["homeassistant.helpers"] = homeassistant.helpers

    return homeassistant
