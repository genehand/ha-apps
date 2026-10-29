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

# Names taken from the installed Home Assistant wheel.
#
# Upstream helpers (``helpers.storage``, ``.dispatcher``, ``.start``, the
# registries) are imported verbatim, so their ``from homeassistant.core import
# X`` binds *this* module and these names must exist here. They are the
# callback machinery and the CoreState enum: the parts of ``homeassistant.core``
# that carry no model layer, which is why the module is importable at all (it
# pulls ~23 modules, no config_entries/loader -- see docs/upstream-tier.md).
# Everything above this block is the shim's own model layer and must not be
# shadowed, so this stays last.
from homeassistant.core import (  # noqa: E402
    DOMAIN,
    CoreState,
    EventStateChangedData,
    EventStateEventData,
    EventStateReportedData,
    HassJob,
    HassJobType,
    ReleaseChannel,
    async_get_hass,
    async_get_hass_or_none,
    get_hassjob_callable_job_type,
    get_release_channel,
    is_callback,
    split_entity_id,
    valid_entity_id,
)

__all__ = [
    "CoreState",
    "DOMAIN",
    "EventStateChangedData",
    "EventStateEventData",
    "EventStateReportedData",
    "HassJob",
    "HassJobType",
    "ReleaseChannel",
    "async_get_hass",
    "async_get_hass_or_none",
    "get_hassjob_callable_job_type",
    "get_release_channel",
    "is_callback",
    "split_entity_id",
    "valid_entity_id",
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
