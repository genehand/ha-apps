"""Mock classes for Home Assistant compatibility.

Provides mock implementations of HA's config, unit system, and event bus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hass import HomeAssistant


class MockConfig:
    """Mock config object."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.path = lambda *parts: str(config_dir.joinpath(*parts))
        # Default location settings (can be overridden)
        self.latitude = 0.0
        self.longitude = 0.0
        self.elevation = 0
        self.unit_system = "metric"
        self.time_zone = "UTC"
        self.external_url = None
        self.internal_url = None
        # Mock units object for temperature and other unit conversions
        self.units = MockUnitSystem()


class MockUnitSystem:
    """Mock unit system for unit conversions."""

    def __init__(self):
        self.temperature_unit = "°C"  # Celsius by default
        self.length_unit = "km"
        self.mass_unit = "kg"
        self.pressure_unit = "Pa"
        self.volume_unit = "L"
        self.wind_speed_unit = "m/s"
        self.accumulated_precipitation_unit = "mm"


class MockEventBus:
    """Mock event bus."""

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    def async_listen(
        self,
        event_type: str,
        listener: Callable,
        event_filter: Optional[Callable] = None,
    ) -> Callable:
        """Listen for events.

        Args:
            event_type: The type of event to listen for
            listener: The callback function to call when the event is fired
            event_filter: Optional filter function that returns True if the event should be handled
        """
        if event_type not in self._hass._event_listeners:
            self._hass._event_listeners[event_type] = []

        # Wrap listener with filter if provided
        if event_filter is not None:
            original_listener = listener

            def filtered_listener(event_data):
                if event_filter(event_data):
                    return original_listener(event_data)
                return None

            listener_to_add = filtered_listener
        else:
            listener_to_add = listener

        self._hass._event_listeners[event_type].append(listener_to_add)

        def remove():
            if listener_to_add in self._hass._event_listeners.get(event_type, []):
                self._hass._event_listeners[event_type].remove(listener_to_add)

        return remove

    def async_fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event."""
        self._hass.async_fire(event_type, event_data)

    def fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event (synchronous version)."""
        self._hass.async_fire(event_type, event_data)

    def async_listeners(self) -> dict:
        """Return all registered event listeners.

        Returns a dict mapping event types to lists of listener functions.
        """
        return self._hass._event_listeners.copy()

    def async_listen_once(self, event_type: str, listener: Callable) -> Callable:
        """Listen for an event once."""

        def wrapped_listener(event_data):
            remove_listener()
            return listener(event_data)

        if event_type not in self._hass._event_listeners:
            self._hass._event_listeners[event_type] = []

        self._hass._event_listeners[event_type].append(wrapped_listener)

        def remove_listener():
            if wrapped_listener in self._hass._event_listeners.get(event_type, []):
                self._hass._event_listeners[event_type].remove(wrapped_listener)

        return remove_listener
