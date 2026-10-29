"""Import patching for Home Assistant compatibility.

Patches sys.modules so integrations get the shim's model layer (core,
config_entries, entity, registries) while everything HA keeps free of that model
layer is imported verbatim from the pinned ``homeassistant`` package.
"""

import importlib
import sys
import types

from . import block_async_io
from . import config_entries
from . import core
from . import entity
from . import selectors
from .logging import get_logger
from .stubs import (
    create_coordinator_stubs,
    create_helpers_stubs,
    create_components_stubs,
    create_additional_stubs,
)

_LOGGER = get_logger(__name__)

# Upstream modules taken verbatim, by name. The admission ticket is that a
# module does not import homeassistant.core, homeassistant.config_entries,
# homeassistant.loader, homeassistant.setup, homeassistant.data_entry_flow or
# any entity/device registry: those are the shim's job, and a module that needs
# them cannot run against the synthetic hass. Verify per module in a *fresh
# process* (existing imports hide the dependency) before adding a name here.
#
# Deliberately not here, and not free to add:
#   helpers.config_validation  +84 modules, pulls entity_registry and loader
#   helpers.selector           +86 modules, same
#   helpers.event              pulls helpers.template plus the real
#                              device/entity registries and loader (model layer)
#   helpers.translation, .frame, .script_variables   need homeassistant.loader
#   helpers.update_coordinator, .restore_state   full model layer
#   helpers.service_info.*     pull data_entry_flow
#   components.<anything>      pull the full model layer (and entity_platform)
UPSTREAM_UTIL_MODULES = (
    "async_",
    "color",
    "dt",
    "event_type",
    "hass_dict",
    "json",
    "network",
    "percentage",
    "scaling",
    "signal_type",
    "unit_conversion",
    "unit_system",
    "uuid",
    "yaml",
)
# Upstream helpers: pure modules that import homeassistant.core for types and the
# callback machinery only. They are imported *after* the synthetic package is
# installed (see ImportPatcher.patch), so `from homeassistant.core import X`
# binds shim.core. Sibling imports (`from . import json`) therefore resolve to
# whatever the shim already registered -- which is why the stubs must be
# created first and why this list must stay a flat, explicit set of names.
UPSTREAM_HELPER_MODULES = (
    "deprecation",
    "typing",
    "json",
    "storage",
    "dispatcher",
    "start",
    "instance_id",
    "issue_registry",
    "floor_registry",
    "label_registry",
    "category_registry",
    # area_registry against the shim's own device_registry stub: the module-level
    # API it needs (async_get + async_clear_area_id on delete) is small, and the
    # alternative is a 400-line reimplementation of area storage.
    "area_registry",
    # helpers.network: real internal/external URL resolution. Its `http` and
    # `hassio` siblings come along as transitive imports (see the closure test).
    "network",
    # Pure leaves that never needed the model layer -- they were never stubbed,
    # only accidentally importable through helpers.__path__.
    "debounce",
    "ratelimit",
    "location",
    "sun",
    "trace",
)


def _import_upstream_modules() -> dict[str, types.ModuleType]:
    """Import the upstream modules the shim reuses, keyed by module name.

    Must run before the synthetic ``homeassistant`` package is registered:
    afterwards these names have no ``__path__`` to load from.
    """
    modules = {
        "homeassistant.const": importlib.import_module("homeassistant.const"),
        "homeassistant.exceptions": importlib.import_module("homeassistant.exceptions"),
        "homeassistant.generated": importlib.import_module("homeassistant.generated"),
        "homeassistant.generated.entity_platforms": importlib.import_module(
            "homeassistant.generated.entity_platforms"
        ),
        "homeassistant.util": importlib.import_module("homeassistant.util"),
    }
    for name in UPSTREAM_UTIL_MODULES:
        modules[f"homeassistant.util.{name}"] = importlib.import_module(
            f"homeassistant.util.{name}"
        )
    return modules


def _upstream_helpers_path() -> list[str]:
    """Return the wheel's ``helpers/`` directory, for the synthetic package.

    Called before the synthetic package is installed (and, on a repeat patch,
    after it already has the path) so either answer is the right one.
    """
    return list(importlib.import_module("homeassistant.helpers").__path__)


def _import_upstream_helpers() -> dict[str, types.ModuleType]:
    """Import the upstream helper modules the shim adopts verbatim.

    Must run *after* ``sys.modules`` points ``homeassistant.core`` at the shim:
    these modules do ``from homeassistant.core import HassJob, callback, ...``
    and would otherwise bind a second, unshared copy of those classes.
    """
    return {
        f"homeassistant.helpers.{name}": importlib.import_module(
            f"homeassistant.helpers.{name}"
        )
        for name in UPSTREAM_HELPER_MODULES
    }


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

        # STEP 1: Import the upstream modules we reuse, before the synthetic
        # package exists below. In this order Python loads the real files from
        # the installed wheel; afterwards these names have no __path__.
        upstream = _import_upstream_modules()
        helpers_path = _upstream_helpers_path()

        # STEP 2: Build the synthetic homeassistant package around them.
        # homeassistant.util *is* the real package object, so a util submodule
        # nobody thought about still resolves upstream (util is pure by design).
        # components stays synthetic: an unstubbed name there must fail loudly
        # rather than half-load the real model layer. helpers is synthetic too,
        # but gets the wheel's directory as a __path__ (below) so adopted helper
        # files can load behind the modules the shim still owns.
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.core = core
        homeassistant.const = upstream["homeassistant.const"]
        homeassistant.exceptions = upstream["homeassistant.exceptions"]
        homeassistant.config_entries = config_entries
        homeassistant.generated = upstream["homeassistant.generated"]
        homeassistant.util = upstream["homeassistant.util"]

        homeassistant.helpers = types.ModuleType("homeassistant.helpers")
        homeassistant.components = types.ModuleType("homeassistant.components")

        # Create data_entry_flow stub
        data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
        data_entry_flow.FlowHandler = config_entries.ConfigFlow
        data_entry_flow.FlowResult = config_entries.FlowResult
        data_entry_flow.FlowResultType = config_entries.FlowResultType
        data_entry_flow.AbortFlow = type("AbortFlow", (Exception,), {})
        homeassistant.data_entry_flow = data_entry_flow

        # STEP 3: Create the remaining stub modules
        create_helpers_stubs(self._hass, homeassistant, config_entries, entity, selectors)
        create_coordinator_stubs(self._hass, homeassistant, entity)
        create_components_stubs(self._hass, homeassistant, self._get_platforms())
        create_additional_stubs(self._hass, homeassistant)

        # create_helpers_stubs() rebuilds homeassistant.helpers from scratch, so
        # the upstream helpers path is attached after it runs. With a __path__
        # the real files load behind the modules the shim still owns; every
        # helper the shim does not stub therefore half-loads (and fails inside
        # upstream) instead of raising ModuleNotFoundError. That is the same
        # trade made for `components` on purpose -- a truer error.
        homeassistant.helpers.__path__ = helpers_path

        # Store reference to our hass instance
        homeassistant.core._shim_instance = self._hass

        # Save original modules
        self._original_modules["homeassistant"] = sys.modules.get("homeassistant")

        # STEP 4: Install the patched modules. sys.modules wins over the parent
        # package, so every name must be listed: anything the shim does not
        # provide still fails loudly, exactly as before.
        sys.modules.update(
            {
                "homeassistant": homeassistant,
                "homeassistant.core": core,
                "homeassistant.config_entries": config_entries,
                "homeassistant.data_entry_flow": data_entry_flow,
                "homeassistant.helpers": homeassistant.helpers,
                "homeassistant.helpers.selector": selectors,
                "homeassistant.helpers.entity": entity,
                "homeassistant.components": homeassistant.components,
                **upstream,
            }
        )

        # STEP 5: Now that homeassistant.core is the shim's module, load the
        # upstream helper modules that were waiting for it. Doing this earlier
        # would bind a second copy of HassJob/callback/CoreState, and the two
        # copies do not interoperate (``_async_add_hass_job`` compares
        # ``job_type`` by identity).
        upstream_helpers = _import_upstream_helpers()
        for full_name, module in upstream_helpers.items():
            setattr(homeassistant.helpers, full_name.rpartition(".")[2], module)
        sys.modules.update(upstream_helpers)

        # Patch aiohttp response json for lenient parsing of error responses
        self._patch_aiohttp_response_json()

        # Patch asyncio socket options for macOS compatibility
        self._patch_asyncio_socket_options()

        # Patch curl_cffi to strip Content-Encoding after auto-decompression
        self._patch_curl_cffi_content_encoding()

        # Enable blocking call detection (warn-only)
        block_async_io.enable()

        self._patched = True
        _LOGGER.debug("Import patching complete")

    def _get_platforms(self):
        """Import and return platform modules."""
        from . import platforms
        return platforms

    def _patch_aiohttp_response_json(self) -> None:
        """Patch aiohttp.ClientResponse.json for lenient parsing of error responses.

        Some integrations receive HTTP error responses with text/plain content-type
        and empty body. aiohttp's json() raises ContentTypeError, which integrations
        often catch and re-raise as their own exception with a poor message. This
        patch attempts to parse the body as JSON for error responses, and for empty
        text/plain bodies it raises EmptyResponseException if the integration defines
        it, allowing clean handling.
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

    def _patch_curl_cffi_content_encoding(self) -> None:
        """Patch curl_cffi to strip Content-Encoding after auto-decompression.

        libcurl (and thus curl_cffi) automatically decompresses gzip, deflate,
        and brotli content. However, unlike the ``requests`` library, curl_cffi
        does **not** strip the ``Content-Encoding`` response header after
        decompression. This causes any downstream code that reads that header
        and attempts its own decompression (e.g., FlightRadarAPI, or any other
        library using curl_cffi) to fail with a noisy warning.

        This patch wraps the session's ``_parse_response`` to remove the
        ``Content-Encoding`` header from the response, since libcurl has
        already transparently handled the decompression.
        """
        try:
            from curl_cffi.requests.session import Session
        except ImportError:
            return  # curl_cffi not installed

        original_parse = Session._parse_response

        def _patched_parse(self, curl, buffer, header_buffer, default_encoding, discard_cookies):
            rsp = original_parse(self, curl, buffer, header_buffer, default_encoding, discard_cookies)
            # libcurl already decompressed the body — this header is misleading
            rsp.headers.pop('Content-Encoding', None)
            return rsp

        Session._parse_response = _patched_parse
        _LOGGER.debug('Patched curl_cffi to strip Content-Encoding after auto-decompression')

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
