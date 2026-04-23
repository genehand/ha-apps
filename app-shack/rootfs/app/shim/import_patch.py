"""Import patching for Home Assistant compatibility.

Patches sys.modules to redirect homeassistant imports to the shim.
"""

import asyncio
import sys
import types
from pathlib import Path

from . import config_entries
from . import core
from . import entity
from . import selectors
from .logging import get_logger
from .stubs import (
    create_coordinator_stubs,
    create_util_stubs,
    create_helpers_stubs,
    create_components_stubs,
    create_additional_stubs,
    create_network_stubs,
)

_LOGGER = get_logger(__name__)


def _find_empty_response_exception():
    """Search loaded modules for any EmptyResponseException class."""
    for mod in sys.modules.values():
        if mod is not None and hasattr(mod, 'EmptyResponseException'):
            return mod.EmptyResponseException
    return None


def _create_patched_json(original_json):
    """Create a patched version of aiohttp.ClientResponse.json.

    For HTTP error responses with non-JSON content-type, attempts to parse
    the body as JSON anyway. For empty text/plain bodies, raises the
    integration's EmptyResponseException if available.
    """
    import aiohttp

    _empty_response_exc = _find_empty_response_exception()

    async def _patched_json(self, *args, **kwargs):
        try:
            return await original_json(self, *args, **kwargs)
        except aiohttp.ContentTypeError:
            if self.status >= 400:
                text = await self.text()
                if not text and _empty_response_exc is not None:
                    raise _empty_response_exc(text)
                if text:
                    try:
                        import json
                        return json.loads(text)
                    except Exception:
                        pass
            raise

    return _patched_json


class ImportPatcher:
    """Manages import patching for HA compatibility."""

    def __init__(self, hass):
        self._hass = hass
        self._original_modules = {}
        self._patched = False

    def patch(self) -> None:
        """Patch sys.modules to redirect HA imports."""
        if self._patched:
            return

        _LOGGER.info("Patching imports for Home Assistant compatibility")

        import importlib.util

        # STEP 1: Inject stub modules FIRST
        # This prevents ImportError for HA-internal dependencies
        stubs = {
            "homeassistant.helpers.deprecation": ("_stub_helpers_deprecation", "ha_fetched"),
            "homeassistant.util.hass_dict": ("_stub_util_hass_dict", "ha_fetched"),
            "homeassistant.util.signal_type": ("_stub_util_signal_type", "ha_fetched"),
        }

        for module_name, (stub_filename, stub_dir) in stubs.items():
            stub_module = types.ModuleType(module_name)
            stub_path = Path(__file__).parent / stub_dir / f"{stub_filename}.py"
            if stub_path.exists():
                stub_code = stub_path.read_text()
                exec(stub_code, stub_module.__dict__)
                sys.modules[module_name] = stub_module
                _LOGGER.debug(f"Injected stub: {module_name}")

        # STEP 2: Load ha_fetched modules directly
        ha_fetched_path = Path(__file__).parent / "ha_fetched"

        # Load generated.entity_platforms
        spec_ep = importlib.util.spec_from_file_location(
            "ha_fetched.generated.entity_platforms",
            ha_fetched_path / "generated" / "entity_platforms.py",
        )
        ha_entity_platforms = importlib.util.module_from_spec(spec_ep)
        sys.modules["ha_fetched.generated.entity_platforms"] = ha_entity_platforms
        spec_ep.loader.exec_module(ha_entity_platforms)

        # Load generated package
        spec_gen = importlib.util.spec_from_file_location(
            "ha_fetched.generated", ha_fetched_path / "generated" / "__init__.py"
        )
        ha_generated = importlib.util.module_from_spec(spec_gen)
        ha_generated.entity_platforms = ha_entity_platforms
        sys.modules["ha_fetched.generated"] = ha_generated
        spec_gen.loader.exec_module(ha_generated)

        # Load util.event_type
        spec_et = importlib.util.spec_from_file_location(
            "ha_fetched.util.event_type",
            ha_fetched_path / "util" / "event_type.py",
        )
        ha_event_type = importlib.util.module_from_spec(spec_et)
        sys.modules["ha_fetched.util.event_type"] = ha_event_type
        spec_et.loader.exec_module(ha_event_type)

        # STEP 3: Set up homeassistant namespace
        homeassistant = types.ModuleType("homeassistant")

        homeassistant_generated = types.ModuleType("homeassistant.generated")
        homeassistant_generated.entity_platforms = ha_entity_platforms
        sys.modules["homeassistant.generated"] = homeassistant_generated
        sys.modules["homeassistant.generated.entity_platforms"] = ha_entity_platforms

        homeassistant_util = types.ModuleType("homeassistant.util")
        homeassistant_util.event_type = ha_event_type
        homeassistant_util.hass_dict = sys.modules["homeassistant.util.hass_dict"]
        homeassistant_util.signal_type = sys.modules["homeassistant.util.signal_type"]
        sys.modules["homeassistant.util"] = homeassistant_util
        sys.modules["homeassistant.util.event_type"] = ha_event_type

        homeassistant.generated = homeassistant_generated
        homeassistant.util = homeassistant_util

        # STEP 4: Load const and exceptions
        spec_const = importlib.util.spec_from_file_location(
            "ha_fetched.const", ha_fetched_path / "const.py"
        )
        ha_const = importlib.util.module_from_spec(spec_const)
        sys.modules["ha_fetched.const"] = ha_const
        spec_const.loader.exec_module(ha_const)

        spec_exc = importlib.util.spec_from_file_location(
            "ha_fetched.exceptions", ha_fetched_path / "exceptions.py"
        )
        ha_exceptions = importlib.util.module_from_spec(spec_exc)
        sys.modules["ha_fetched.exceptions"] = ha_exceptions
        spec_exc.loader.exec_module(ha_exceptions)

        # STEP 5: Create full homeassistant package
        homeassistant.core = core
        homeassistant.const = ha_const
        homeassistant.exceptions = ha_exceptions
        homeassistant.config_entries = config_entries
        homeassistant.generated = homeassistant_generated
        homeassistant.util = homeassistant_util

        # Register all submodules
        sys.modules["homeassistant"] = homeassistant
        sys.modules["homeassistant.core"] = core
        sys.modules["homeassistant.const"] = ha_const
        sys.modules["homeassistant.exceptions"] = ha_exceptions
        sys.modules["homeassistant.config_entries"] = config_entries

        # Create data_entry_flow stub
        data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
        data_entry_flow.FlowHandler = config_entries.ConfigFlow
        data_entry_flow.FlowResult = config_entries.FlowResult
        data_entry_flow.FlowResultType = config_entries.FlowResultType
        data_entry_flow.AbortFlow = type("AbortFlow", (Exception,), {})
        homeassistant.data_entry_flow = data_entry_flow
        sys.modules["homeassistant.data_entry_flow"] = data_entry_flow

        homeassistant.helpers = types.ModuleType("homeassistant.helpers")
        homeassistant.components = types.ModuleType("homeassistant.components")
        homeassistant.helpers.selector = selectors

        # Skip loading dt.py and color.py - they require HA-specific dependencies
        _LOGGER.debug("Skipping dt.py and color.py - HA-specific dependencies not needed")

        # STEP 6: Create all stub modules
        create_network_stubs(self._hass, homeassistant)
        create_util_stubs(self._hass, homeassistant)
        create_helpers_stubs(self._hass, homeassistant, config_entries, entity, selectors)
        create_coordinator_stubs(self._hass, homeassistant, entity)
        create_components_stubs(self._hass, homeassistant, self._get_platforms())
        create_additional_stubs(self._hass, homeassistant)

        # Store reference to our hass instance
        homeassistant.core._shim_instance = self._hass

        # Save original modules
        self._original_modules["homeassistant"] = sys.modules.get("homeassistant")

        # Install patched modules
        sys.modules["homeassistant"] = homeassistant
        sys.modules["homeassistant.core"] = core
        sys.modules["homeassistant.const"] = ha_const
        sys.modules["homeassistant.exceptions"] = ha_exceptions
        sys.modules["homeassistant.config_entries"] = config_entries
        sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
        sys.modules["homeassistant.helpers"] = homeassistant.helpers
        sys.modules["homeassistant.helpers.selector"] = selectors
        sys.modules["homeassistant.helpers.entity"] = entity
        sys.modules["homeassistant.util"] = homeassistant.util
        sys.modules["homeassistant.components"] = homeassistant.components

        # Patch aiohttp response json for lenient parsing of error responses
        self._patch_aiohttp_response_json()

        # Patch asyncio socket options for macOS compatibility
        self._patch_asyncio_socket_options()

        self._patched = True
        _LOGGER.debug("Import patching complete")

    def _get_platforms(self):
        """Import and return platform modules."""
        from . import platforms
        return platforms

    def _patch_aiohttp_response_json(self) -> None:
        """Patch aiohttp.ClientResponse.json for lenient parsing of error responses.

        Some integrations (e.g. nest_protect) receive HTTP error responses with
        text/plain content-type and empty body. aiohttp's json() raises
        ContentTypeError, which integrations often catch and re-raise as their own
        exception with a poor message. This patch attempts to parse the body as JSON
        for error responses, and for empty text/plain bodies it raises
        EmptyResponseException if the integration defines it, allowing clean handling.
        """
        import aiohttp

        aiohttp.ClientResponse.json = _create_patched_json(aiohttp.ClientResponse.json)
        _LOGGER.debug(
            'Patched aiohttp.ClientResponse.json for lenient error response parsing'
        )

    def _patch_asyncio_socket_options(self) -> None:
        """Patch asyncio socket options for macOS compatibility."""
        import platform

        if platform.system() == "Darwin":
            import socket

            original_socket = socket.socket

            class PatchedSocket:
                def __init__(self, *args, **kwargs):
                    self._sock = original_socket(*args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self._sock, name)

                def setsockopt(self, level, optname, value):
                    if optname == socket.TCP_NODELAY:
                        try:
                            return self._sock.setsockopt(level, optname, value)
                        except OSError:
                            pass
                    return self._sock.setsockopt(level, optname, value)

            socket.socket = PatchedSocket
            _LOGGER.debug("Patched asyncio _set_nodelay for macOS compatibility")

    def unpatch(self) -> None:
        """Restore original modules."""
        for name, module in self._original_modules.items():
            if module is not None:
                sys.modules[name] = module
        self._patched = False


def setup_import_patching(hass) -> ImportPatcher:
    """Set up import patching for Home Assistant compatibility.

    Returns the ImportPatcher instance so caller can hold a reference.
    """
    patcher = ImportPatcher(hass)
    patcher.patch()
    return patcher
