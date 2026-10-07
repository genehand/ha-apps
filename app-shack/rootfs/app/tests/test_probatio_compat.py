"""Regression tests for ``probatio`` compatibility.

Home Assistant 2026.9+ integrations import ``probatio`` (HA's maintained,
drop-in reimplementation of voluptuous) directly, e.g. in their config flows.
The shim must therefore:

1. Provide the ``probatio`` module (it is a dependency of the shim, not listed
   in the integration manifest, because HA core provides it).
2. Register it under the ``voluptuous`` name before any shim module imports
   voluptuous, so shim internals and integrations share *one* schema
   implementation. ``probatio.Required`` / ``probatio.Optional`` and
   ``probatio.UNDEFINED`` are distinct classes/objects from voluptuous's, and
   the shim's schema handling relies on ``isinstance`` / ``is`` identity.

Regression: loading ``leviton_decora_smart_wifi`` failed with
``Failed to load integration leviton_decora_smart_wifi: No module named
'probatio'`` after that integration moved to ``import probatio``.
"""

import importlib.metadata
import sys
import types
from pathlib import Path

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationManager
from shim.storage import Storage


class TestProbatioIsAvailable:
    """The shim must provide probatio and unify it with voluptuous."""

    def test_probatio_is_importable(self):
        """Integrations do ``import probatio``; the module must exist."""
        import probatio

        assert hasattr(probatio, "Schema")
        assert hasattr(probatio, "Required")
        assert hasattr(probatio, "Optional")

    def test_voluptuous_resolves_to_probatio(self):
        """``import voluptuous`` and ``import probatio`` share classes.

        Without this, ``isinstance(key, vol.Required)`` is False for a
        ``probatio.Required`` marker and every field renders as optional.
        """
        import probatio
        import voluptuous as vol

        assert vol.Schema is probatio.Schema
        assert vol.Required is probatio.Required
        assert vol.Optional is probatio.Optional
        assert vol.UNDEFINED is probatio.UNDEFINED


class TestVoluptuousIsNotInstalled:
    """Upstream voluptuous must not be shipped; probatio replaces it.

    Regression: ``voluptuous>=0.14.0`` was a direct dependency until it was
    dropped. It is dead weight at best (``install_as_voluptuous()`` shadows it
    in ``sys.modules``) and a mixed-namespace bug at worst, if anything manages
    to import it before ``shim/__init__.py`` aliases probatio over it.
    """

    def test_voluptuous_distribution_is_not_installed(self):
        """No distribution should provide the real ``voluptuous`` package."""
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version("voluptuous")

    def test_voluptuous_module_comes_from_probatio(self):
        """The ``voluptuous`` module on disk is probatio's shim, not a wheel."""
        import probatio
        import shim  # noqa: F401  # installs the alias
        import voluptuous

        probatio_root = Path(probatio.__file__).parent
        assert Path(voluptuous.__file__).is_relative_to(probatio_root)


class TestProbatioSchemaParsing:
    """The web form parser must understand probatio schemas."""

    def test_required_markers_are_recognized(self):
        import probatio

        from shim.web.schema import parse_schema

        schema = probatio.Schema(
            {
                probatio.Required("email"): str,
                probatio.Optional("timeout", default=30): int,
            }
        )

        fields = {field["name"]: field for field in parse_schema(schema)}

        assert fields["email"]["required"] is True
        assert fields["timeout"]["required"] is False
        assert fields["timeout"]["default"] == 30

    def test_probatio_undefined_is_detected(self):
        import probatio

        from shim.web.schema import is_undefined

        assert is_undefined(probatio.UNDEFINED) is True
        assert is_undefined("value") is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_leviton_config_flow_loads_with_probatio():
    """Load the real leviton integration, whose config flow imports probatio.

    This is the end-to-end regression: before the fix, ``load_integration``
    returned False with ``No module named 'probatio'``.
    """
    data_dir = Path(__file__).parent.parent / "data"
    shim_dir = data_dir / "shim"
    custom_components_dir = shim_dir / "custom_components"
    integration_dir = custom_components_dir / "leviton_decora_smart_wifi"

    if not (integration_dir / "config_flow.py").exists():
        pytest.skip("leviton_decora_smart_wifi integration not installed")

    # Other tests build ``custom_components`` in temp dirs and leave it cached
    # in sys.modules; point it at the bundled integrations for this test.
    saved_cc = sys.modules.get("custom_components")
    saved_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name.startswith("custom_components.leviton_decora_smart_wifi")
    }
    for name in saved_modules:
        del sys.modules[name]

    cc_module = types.ModuleType("custom_components")
    cc_module.__path__ = [str(custom_components_dir)]
    sys.modules["custom_components"] = cc_module

    try:
        hass = HomeAssistant(data_dir)
        setup_import_patching(hass).patch()

        storage = Storage(shim_dir)
        integration_manager = IntegrationManager(storage, shim_dir)
        loader = IntegrationLoader(hass, integration_manager)
        hass.data["integration_loader"] = loader

        assert await loader.load_integration("leviton_decora_smart_wifi") is True

        result = await loader.start_config_flow("leviton_decora_smart_wifi")
        assert result is not None
        assert result["type"] == "form"

        from shim.web.schema import parse_schema

        fields = {
            field["name"]: field for field in parse_schema(result["data_schema"])
        }
        # The user step is email + password, both Required markers.
        assert fields["email"]["required"] is True
        assert fields["password"]["required"] is True
    finally:
        for name in [
            name
            for name in sys.modules
            if name.startswith("custom_components.leviton_decora_smart_wifi")
        ]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        if saved_cc is not None:
            sys.modules["custom_components"] = saved_cc
        else:
            sys.modules.pop("custom_components", None)
