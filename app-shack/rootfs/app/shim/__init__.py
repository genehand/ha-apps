"""Home Assistant Shim Package.

Provides compatibility layer for running Home Assistant integrations
outside of Home Assistant via MQTT.
"""

import os
from pathlib import Path

import yaml

from .hass import HomeAssistant
from .models import State, ConfigEntry
from .registries import StateMachine, ConfigEntries
from .storage import Storage
from .logging import get_logger, set_current_integration
from .import_patch import setup_import_patching
from .manager import ShimManager
from .web import WebUI

# Load version from BUILD_VERSION env var (set at build time), fallback to config.yaml for dev
__version__ = os.environ.get("BUILD_VERSION")
if not __version__:
    # Fallback to config.yaml for local development
    _CONFIG_YAML_PATH = Path(__file__).parent.parent.parent.parent / "config.yaml"
    try:
        with open(_CONFIG_YAML_PATH, "r") as _f:
            _config_yaml = yaml.safe_load(_f)
            __version__ = _config_yaml.get("version", "0.1.0")
    except (FileNotFoundError, yaml.YAMLError):
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
