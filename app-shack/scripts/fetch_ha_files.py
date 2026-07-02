#!/usr/bin/env python3
"""Fetch Home Assistant source files from GitHub releases.

This script downloads key HA files (const.py, exceptions.py, utils) from the
latest GitHub release to keep the shim in sync without manual copying.
"""

import argparse
import re
import sys
import urllib.request
from pathlib import Path

# Files to fetch from HA core repo
# Format: (github_path, local_filename, needs_cleaning)
HA_FILES = [
    # Core constants - replaces shim/const.py
    ("homeassistant/const.py", "const.py", True),
    # Generated platform enum (const.py depends on this)
    (
        "homeassistant/generated/entity_platforms.py",
        "generated/entity_platforms.py",
        True,
    ),
    # Exceptions - replaces shim/exceptions.py
    ("homeassistant/exceptions.py", "exceptions.py", True),
    # Utility modules
    ("homeassistant/util/event_type.py", "util/event_type.py", True),
    ("homeassistant/util/dt.py", "util/dt.py", False),
    ("homeassistant/util/color.py", "util/color.py", False),
]

# Minimal stubs for HA-internal dependencies that const.py imports
# These are injected before loading the real const.py
STUBS = {
    "homeassistant/helpers/deprecation.py": '''"""Minimal deprecation stub for HA const.py compatibility.

Mirrors the runtime behaviour of homeassistant.helpers.deprecation for the
subset used by ha_fetched/const.py's module-level __getattr__/__dir__/__all__
machinery (PEP 562). Deprecation notices are logged at debug level rather than
printed, but attribute lookup, dir() and __all__ behave like upstream so
introspection of homeassistant.const works.
"""
from __future__ import annotations

import logging

_PREFIX_DEPRECATED = "_DEPRECATED_"


class DeprecatedConstant:
    """Stub for deprecated constant wrapper (matches upstream attributes)."""

    def __init__(self, value, replacement=None, breaks_in_ha_version=None, *args, **kwargs):
        self.value = value
        self.replacement = replacement
        self.breaks_in_ha_version = breaks_in_ha_version

    def __get__(self, obj, objtype=None):
        return self.value

    def __set_name__(self, owner, name):
        pass


class DeprecatedConstantEnum:
    """Stub for deprecated enum wrapper."""

    def __init__(self, enum, breaks_in_ha_version=None, *args, **kwargs):
        self.enum = enum
        self.breaks_in_ha_version = breaks_in_ha_version

    def __get__(self, obj, objtype=None):
        return self.enum

    def __set_name__(self, owner, name):
        pass


class EnumWithDeprecatedMembers(type):
    """Stub metaclass for enums with deprecated members."""

    pass


def check_if_deprecated_constant(name, module_globals):
    """Check if the not found name is a deprecated constant.

    If it is, return the constant's value (logging a debug deprecation notice).
    Otherwise raise AttributeError per PEP 562.
    """
    module_name = module_globals.get("__name__")
    deprecated_const = module_globals.get(_PREFIX_DEPRECATED + name)
    if deprecated_const is None:
        raise AttributeError(f"Module {module_name!r} has no attribute {name!r}")

    value = None
    replacement = None
    breaks_in_ha_version = None
    if isinstance(deprecated_const, DeprecatedConstant):
        value = deprecated_const.value
        replacement = deprecated_const.replacement
        breaks_in_ha_version = deprecated_const.breaks_in_ha_version
    elif isinstance(deprecated_const, DeprecatedConstantEnum):
        value = deprecated_const.enum
        replacement = (
            f"{deprecated_const.enum.__class__.__name__}.{deprecated_const.enum.name}"
        )
        breaks_in_ha_version = deprecated_const.breaks_in_ha_version

    if value is None or replacement is None:
        raise AttributeError(
            f"Value of {_PREFIX_DEPRECATED}{name} is an instance of "
            f"{type(deprecated_const)} but an instance of DeprecatedConstant or "
            "DeprecatedConstantEnum is required"
        )

    logging.getLogger(module_name or __name__).debug(
        "Accessed deprecated constant %r (use %r instead, breaks in %s)",
        name,
        replacement,
        breaks_in_ha_version,
    )
    return value


def dir_with_deprecated_constants(module_globals_keys):
    """Return dir() with deprecated constants exposed by their public names."""
    return module_globals_keys + [
        name.removeprefix(_PREFIX_DEPRECATED)
        for name in module_globals_keys
        if name.startswith(_PREFIX_DEPRECATED)
    ]


def all_with_deprecated_constants(module_globals):
    """Generate an __all__ list including deprecated constants."""
    module_globals_keys = list(module_globals)
    return [itm for itm in module_globals_keys if not itm.startswith("_")] + [
        name.removeprefix(_PREFIX_DEPRECATED)
        for name in module_globals_keys
        if name.startswith(_PREFIX_DEPRECATED)
    ]
''',
    "homeassistant/util/hass_dict.py": '''"""Minimal HassKey stub for HA const.py compatibility."""
from __future__ import annotations

class HassKey(str):
    """Stub for HassKey - just a typed string."""
    pass
''',
    "homeassistant/util/signal_type.py": '''"""Minimal SignalType stub for HA const.py compatibility."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

class SignalType[_DataT: Mapping[str, Any] = Mapping[str, Any]](str):
    """Stub for SignalType - generic str subclass."""
    pass
''',
}

RAW_GITHUB_URL = "https://raw.githubusercontent.com/home-assistant/core"
GITHUB_API_LATEST = "https://api.github.com/repos/home-assistant/core/releases/latest"


def get_latest_version() -> str:
    """Fetch the latest HA release tag from GitHub API."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST, headers={"Accept": "application/vnd.github.v3+json"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            import json

            data = json.loads(response.read().decode("utf-8"))
            return data["tag_name"]
    except Exception as e:
        print(f"Error fetching latest version: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_file(version: str, path: str) -> str:
    """Fetch a single file from GitHub raw content."""
    url = f"{RAW_GITHUB_URL}/{version}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def clean_const_py(content: str) -> str:
    """Clean const.py to work with our shim structure."""
    # Replace complex TYPE_CHECKING block
    content = re.sub(
        r"if TYPE_CHECKING:.*?(?=\n\n|\Z)",
        "if TYPE_CHECKING:\n    from typing import Any, Mapping\n    NoEventData = Mapping[str, Any]",
        content,
        flags=re.DOTALL,
    )

    # Convert ALL relative imports to absolute imports using the homeassistant
    # namespace. import_patch.py injects these as stub modules before const.py
    # is loaded, so the absolute imports resolve at runtime without a fallback.
    content = re.sub(
        r"^from \.generated\.entity_platforms import",
        "from homeassistant.generated.entity_platforms import",
        content,
        flags=re.MULTILINE,
    )

    # from .helpers.deprecation import (...)
    content = re.sub(
        r"^from \.helpers\.deprecation import",
        "from homeassistant.helpers.deprecation import",
        content,
        flags=re.MULTILINE,
    )

    # from .util.event_type import EventType
    content = re.sub(
        r"^from \.util\.event_type import EventType",
        "from homeassistant.util.event_type import EventType",
        content,
        flags=re.MULTILINE,
    )

    # from .util.hass_dict import HassKey
    content = re.sub(
        r"^from \.util\.hass_dict import HassKey",
        "from homeassistant.util.hass_dict import HassKey as _HassKey\nHassKey = _HassKey",
        content,
        flags=re.MULTILINE,
    )

    # from .util.signal_type import SignalType
    content = re.sub(
        r"^from \.util\.signal_type import SignalType",
        "from homeassistant.util.signal_type import SignalType as _SignalType\nSignalType = _SignalType",
        content,
        flags=re.MULTILINE,
    )

    return content


def clean_exceptions_py(content: str) -> str:
    """Clean exceptions.py to remove HA-internal dependencies."""
    # Replace TYPE_CHECKING imports (module level only - starts at beginning of line)
    content = re.sub(
        r"^if TYPE_CHECKING:.*?(?=\n\n|^\S|\Z)",
        "if TYPE_CHECKING:\n    pass",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )

    # Remove the complex translation import function - we don't need translations
    content = re.sub(
        r"_function_cache: dict\[str.*?= \{\}\n\n"
        r"def import_async_get_exception_message\(\).*?return async_get_exception_message_import\n",
        "# Translation support disabled for shim\n",
        content,
        flags=re.DOTALL,
    )

    # Replace EventType import
    content = re.sub(
        r"from \.util\.event_type import EventType",
        "# from .util.event_type import EventType  # Provided by stub",
        content,
    )

    return content


def clean_entity_platforms_py(content: str) -> str:
    """Clean entity_platforms.py - it's already simple."""
    # Add a header noting it's auto-generated
    header = '"""Platform enum - auto-generated by HA.\n\nFetched from Home Assistant core.\n"""\n\n'
    # Remove the docstring and add our header
    content = re.sub(r'""".*?"""\n\n', header, content, count=1, flags=re.DOTALL)
    return content


def clean_event_type_py(content: str) -> str:
    """Clean event_type.py - it's already simple but add a note."""
    content = re.sub(
        r"from __future__ import annotations",
        "from __future__ import annotations\n\n# Stubbed for HA compatibility",
        content,
    )
    return content


def write_file(dest_dir: Path, filename: str, content: str):
    """Write content to a file in the destination directory."""
    filepath = dest_dir / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"  ✓ {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch HA source files from GitHub releases"
    )
    parser.add_argument(
        "--version",
        help="Specific HA version tag (e.g., 2025.3.4). Defaults to latest release.",
    )
    parser.add_argument(
        "--dest",
        default="../rootfs/app/shim/ha_fetched",
        help="Destination directory relative to script location (default: ../rootfs/app/shim/ha_fetched)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without writing files",
    )

    args = parser.parse_args()

    # Determine version
    if args.version:
        version = args.version
        print(f"Using specified version: {version}")
    else:
        print("Fetching latest HA release version...")
        version = get_latest_version()
        print(f"Latest version: {version}")

    # Setup destination directory
    script_dir = Path(__file__).parent.absolute()
    dest_dir = (script_dir / args.dest).resolve()

    if args.dry_run:
        print(f"\n[Dry Run] Would download to: {dest_dir}")
    else:
        print(f"\nDownloading to: {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)

    # Fetch and write files
    print("\nFetching HA files...")
    for github_path, local_name, needs_cleaning in HA_FILES:
        print(f"\n  Fetching {github_path}...")
        content = fetch_file(version, github_path)

        if content is None:
            print(f"  ✗ Failed to fetch {github_path}")
            continue

        if needs_cleaning:
            if "const.py" in local_name:
                content = clean_const_py(content)
            elif "exceptions.py" in local_name:
                content = clean_exceptions_py(content)
            elif "entity_platforms.py" in local_name:
                content = clean_entity_platforms_py(content)
            elif "event_type.py" in local_name:
                content = clean_event_type_py(content)

        if args.dry_run:
            print(f"  → Would write {len(content)} bytes to {local_name}")
        else:
            write_file(dest_dir, local_name, content)

    # Write __init__.py for generated package
    generated_init = (
        '"""Generated HA files.\n\nAuto-fetched from Home Assistant.\n"""\n'
    )
    if args.dry_run:
        print(f"  → Would write generated/__init__.py")
    else:
        write_file(dest_dir, "generated/__init__.py", generated_init)

    # Write stubs
    print("\nWriting dependency stubs...")
    for stub_path, stub_content in STUBS.items():
        local_name = stub_path.replace("homeassistant/", "").replace("/", "_")
        if args.dry_run:
            print(f"  → Would write stub: {local_name}")
        else:
            write_file(dest_dir, f"_stub_{local_name}", stub_content)

    # Write __init__.py to make it a proper package with lazy loading
    init_content = f'''"""Auto-fetched Home Assistant compatibility files.

This package contains files fetched from Home Assistant {version}.

Files fetched:
- const.py: Core constants
- exceptions.py: Exception classes
- util/: Utility modules

Run `python3 fetch_ha_files.py` to update to latest HA release.
"""


# Re-export main modules for convenience
# These are loaded lazily to avoid circular imports during import_patch.py setup
def __getattr__(name):
    """Lazy module loading to avoid circular import issues."""
    if name == "const":
        from . import const
        return const
    elif name == "exceptions":
        from . import exceptions
        return exceptions
    elif name == "util":
        try:
            from . import util
            return util
        except ImportError:
            pass
    elif name == "generated":
        from . import generated
        return generated
    raise AttributeError(f"module '{{__name__}}' has no attribute '{{name}}'")


__version__ = "{version}"'''

    if args.dry_run:
        print(f"  → Would write __init__.py")
    else:
        write_file(dest_dir, "__init__.py", init_content)

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print("Dry run complete. No files were written.")
        print(f"To apply changes, run without --dry-run")
    else:
        print(f"✓ Successfully fetched HA {version} files!")
        print(f"\nNext steps:")
        print(f"  1. Update import_patch.py to use these files:")
        print(f"     - Replace shim.const with shim.ha_fetched.const")
        print(f"     - Replace shim.exceptions with shim.ha_fetched.exceptions")
        print(f"  2. Delete old manual files:")
        print(f"     - shim/const.py (908 lines)")
        print(f"     - shim/exceptions.py (92 lines)")
        print(f"  3. Test that integrations still load properly")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
