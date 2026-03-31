#!/usr/bin/env python3
"""Test that the import patching works correctly."""

import sys
import types
import importlib.util
from pathlib import Path


def test_import_patching():
    """Test that import patching sets up HA modules correctly."""
    print("Testing import patching...")

    # STEP 1: Inject stubs
    stubs = {
        "homeassistant.helpers.deprecation": "_stub_helpers_deprecation.py",
        "homeassistant.util.hass_dict": "_stub_util_hass_dict.py",
        "homeassistant.util.signal_type": "_stub_util_signal_type.py",
    }

    base_path = Path(__file__).parent / "shim" / "ha_fetched"

    for module_name, stub_filename in stubs.items():
        stub_module = types.ModuleType(module_name)
        stub_code = (base_path / stub_filename).read_text()
        exec(stub_code, stub_module.__dict__)
        sys.modules[module_name] = stub_module

    print("  ✓ Stubs injected")

    # STEP 2: Load ha_fetched modules directly
    # Load generated.entity_platforms first
    spec_ep = importlib.util.spec_from_file_location(
        "ha_fetched.generated.entity_platforms",
        base_path / "generated" / "entity_platforms.py",
    )
    entity_platforms = importlib.util.module_from_spec(spec_ep)
    sys.modules["ha_fetched.generated.entity_platforms"] = entity_platforms
    spec_ep.loader.exec_module(entity_platforms)

    # Load generated package
    spec_gen = importlib.util.spec_from_file_location(
        "ha_fetched.generated", base_path / "generated" / "__init__.py"
    )
    generated = importlib.util.module_from_spec(spec_gen)
    generated.entity_platforms = entity_platforms
    sys.modules["ha_fetched.generated"] = generated
    spec_gen.loader.exec_module(generated)

    # Load util.event_type
    spec_et = importlib.util.spec_from_file_location(
        "ha_fetched.util.event_type", base_path / "util" / "event_type.py"
    )
    event_type = importlib.util.module_from_spec(spec_et)
    sys.modules["ha_fetched.util.event_type"] = event_type
    spec_et.loader.exec_module(event_type)

    print("  ✓ HA fetched modules loaded")

    # STEP 3: Set up homeassistant namespace
    homeassistant_generated = types.ModuleType("homeassistant.generated")
    homeassistant_generated.entity_platforms = entity_platforms
    sys.modules["homeassistant.generated"] = homeassistant_generated
    sys.modules["homeassistant.generated.entity_platforms"] = entity_platforms

    homeassistant_util = types.ModuleType("homeassistant.util")
    homeassistant_util.event_type = event_type
    homeassistant_util.hass_dict = sys.modules["homeassistant.util.hass_dict"]
    homeassistant_util.signal_type = sys.modules["homeassistant.util.signal_type"]
    sys.modules["homeassistant.util"] = homeassistant_util
    sys.modules["homeassistant.util.event_type"] = event_type

    print("  ✓ homeassistant namespace set up")

    # STEP 4: Load const and exceptions
    spec_const = importlib.util.spec_from_file_location(
        "ha_fetched.const", base_path / "const.py"
    )
    const = importlib.util.module_from_spec(spec_const)
    sys.modules["ha_fetched.const"] = const
    spec_const.loader.exec_module(const)

    spec_exc = importlib.util.spec_from_file_location(
        "ha_fetched.exceptions", base_path / "exceptions.py"
    )
    exceptions = importlib.util.module_from_spec(spec_exc)
    sys.modules["ha_fetched.exceptions"] = exceptions
    spec_exc.loader.exec_module(exceptions)

    print("  ✓ const and exceptions loaded")

    # STEP 5: Create homeassistant package
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.const = const
    homeassistant.exceptions = exceptions

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.exceptions"] = exceptions

    print("  ✓ homeassistant package created")

    # STEP 6: Test imports
    from homeassistant.const import Platform, CONF_HOST, STATE_ON

    assert Platform.SENSOR == "sensor", f"Expected 'sensor', got {Platform.SENSOR}"
    assert CONF_HOST == "host", f"Expected 'host', got {CONF_HOST}"
    assert STATE_ON == "on", f"Expected 'on', got {STATE_ON}"

    from homeassistant.exceptions import HomeAssistantError

    assert HomeAssistantError is not None

    print("  ✓ Imports verified")

    print()
    print("=" * 60)
    print("SUCCESS! Import patching is working correctly.")
    print("=" * 60)
    print()
    print("Files fetched from HA 2026.3.4:")
    print("  - const.py (all HA constants)")
    print("  - exceptions.py (all HA exceptions)")
    print("  - generated/entity_platforms.py")
    print("  - util/event_type.py")
    print("  - util/dt.py")
    print("  - util/color.py")
    print()
    print("To update to a newer HA version:")
    print("  cd scripts && python3 fetch_ha_files.py")
    return True


if __name__ == "__main__":
    try:
        test_import_patching()
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
