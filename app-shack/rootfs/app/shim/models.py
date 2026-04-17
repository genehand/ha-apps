"""Data models for Home Assistant Shim.

Provides dataclasses and enums used throughout the shim.
"""

from __future__ import annotations

import fnmatch
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar, Generic, Union
from datetime import datetime
from enum import Enum

from .logging import get_logger

_LOGGER = get_logger(__name__)

T = TypeVar("T")

# Type alias for callback functions (used by integrations)
CALLBACK_TYPE = Callable[[], None]


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
class ConfigEntry(Generic[T]):
    """Configuration entry for an integration."""

    entry_id: str
    domain: str
    title: str
    version: int = 1
    data: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    pref_disable_new_entities: bool = False
    source: str = "user"
    runtime_data: Any = None
    state: str = field(default="not_loaded", repr=False)
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

    def async_create_task(self, hass, target, name=None):
        """Create a task to track for this config entry.

        The task is stored on the entry and will be cancelled when the entry is unloaded.
        """
        task = hass.async_create_background_task(target, name)
        self._on_unload_callbacks.append(
            lambda: task.cancel() if not task.done() else None
        )
        return task

    @property
    def entity_filters(self) -> list[str]:
        """Return list of entity glob patterns to filter by entity_id.

        Patterns are stored in options['entity_filters'] and used to exclude
        matching entities from MQTT discovery.
        """
        filters = self.options.get("entity_filters", [])
        if isinstance(filters, str):
            # Handle legacy single string format
            return [filters] if filters else []
        return filters if filters else []

    @property
    def entity_name_filters(self) -> list[str]:
        """Return list of entity glob patterns to filter by name.

        Patterns are stored in options['entity_name_filters'] and used to exclude
        matching entities from MQTT discovery based on their display name.
        """
        filters = self.options.get("entity_name_filters", [])
        if isinstance(filters, str):
            # Handle legacy single string format
            return [filters] if filters else []
        return filters if filters else []

    def entity_matches_filter(
        self, entity_id: str, entity_name: Optional[str] = None
    ) -> bool:
        """Check if an entity matches any of the configured filters.

        Entity matches if either the entity_id matches entity_filters patterns
        OR the entity_name matches entity_name_filters patterns (OR logic).

        Args:
            entity_id: The full entity ID (e.g., 'sensor.living_room_temp').
            entity_name: The display name of the entity (e.g., 'Living Room Temp').

        Returns:
            True if the entity matches any filter pattern, False otherwise.
        """
        # Check entity_id patterns
        for pattern in self.entity_filters:
            if fnmatch.fnmatch(entity_id, pattern):
                return True

        # Check name patterns if name is provided
        if entity_name:
            for pattern in self.entity_name_filters:
                if fnmatch.fnmatch(entity_name, pattern):
                    return True

        return False

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
class Event:
    """Represents an event within Home Assistant."""

    event_type: str
    data: dict = field(default_factory=dict)
    origin: str = "LOCAL"  # EventOrigin: LOCAL or REMOTE
    time_fired: Optional[datetime] = None
    context: Optional[Context] = None

    def __post_init__(self):
        if not self.time_fired:
            self.time_fired = datetime.now()
        if not self.context:
            self.context = Context()


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
