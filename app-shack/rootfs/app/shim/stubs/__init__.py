"""Home Assistant stub modules for import patching.

This package contains stub implementations of Home Assistant modules
for running integrations outside of the HA core environment.
"""

from .base import make_module, make_submodule, simple_method, simple_class_factory
from .coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    CoordinatorEntity,
    create_coordinator_stubs,
)
from .util import create_util_stubs
from .helpers import create_helpers_stubs
from .components import create_components_stubs, create_additional_stubs
from .network import create_network_stubs

__all__ = [
    "make_module",
    "make_submodule",
    "simple_method",
    "simple_class_factory",
    "DataUpdateCoordinator",
    "UpdateFailed",
    "CoordinatorEntity",
    "create_coordinator_stubs",
    "create_util_stubs",
    "create_helpers_stubs",
    "create_components_stubs",
    "create_additional_stubs",
    "create_network_stubs",
]
