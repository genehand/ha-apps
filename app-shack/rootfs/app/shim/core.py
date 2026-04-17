"""Home Assistant Shim - Core Infrastructure.

Mocks Home Assistant's core classes for running HACS integrations outside HA.

This module re-exports from the split modules for backward compatibility.
New code should import directly from the specific modules.
"""

# Re-export from models.py
from .models import (
    CALLBACK_TYPE,
    ConfigEntry,
    Context,
    Event,
    ServiceCall,
    ServiceResponse,
    State,
    SupportsResponse,
    callback,
)

# Re-export from registries.py
from .registries import (
    ConfigEntries,
    FlowManager,
    ServiceRegistry,
    StateMachine,
)

# Re-export from hass.py
from .hass import HomeAssistant

# Re-export from mocks.py
from .mocks import (
    MockConfig,
    MockEventBus,
    MockUnitSystem,
)

__all__ = [
    "CALLBACK_TYPE",
    "callback",
    "ConfigEntry",
    "Context",
    "Event",
    "ServiceCall",
    "ServiceResponse",
    "State",
    "StateMachine",
    "SupportsResponse",
    "ServiceRegistry",
    "ConfigEntries",
    "FlowManager",
    "HomeAssistant",
    "MockConfig",
    "MockUnitSystem",
    "MockEventBus",
]
