"""Home Assistant Shim Package.

Provides compatibility layer for running Home Assistant integrations
outside of Home Assistant via MQTT.
"""

from .core import HomeAssistant, State, StateMachine, ConfigEntry, ConfigEntries
from .storage import Storage
from .logging import get_logger, set_current_integration
from .import_patch import setup_import_patching
from .manager import ShimManager
from .web import WebUI

__version__ = "0.1.0"

__all__ = [
    "HomeAssistant",
    "State",
    "StateMachine",
    "ConfigEntry",
    "ConfigEntries",
    "Storage",
    "get_logger",
    "set_current_integration",
    "setup_import_patching",
    "ShimManager",
    "WebUI",
]
