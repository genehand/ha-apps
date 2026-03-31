"""Integration management for Home Assistant Shim.

Provides functionality for downloading, installing, and managing
HACS integrations.
"""

from .manager import IntegrationManager, IntegrationInfo, InstallTask
from .loader import IntegrationLoader

__all__ = [
    "IntegrationManager",
    "IntegrationInfo",
    "IntegrationLoader",
    "InstallTask",
]
