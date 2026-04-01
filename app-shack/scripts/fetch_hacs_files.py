#!/usr/bin/env python3
"""Fetch HACS integration source files from GitHub.

This script downloads key HACS utility files from the HACS integration repository
to leverage their battle-tested code for version comparison, validation,
and download management instead of recreating it.
"""

import argparse
import sys
import urllib.request
from pathlib import Path

# Files to fetch from HACS integration repo
# Format: (github_path, local_filename, needs_cleaning)
HACS_FILES = [
    # Core constants and enums
    ("custom_components/hacs/const.py", "const.py", True),
    ("custom_components/hacs/enums.py", "enums.py", True),
    ("custom_components/hacs/exceptions.py", "exceptions.py", True),
    ("custom_components/hacs/types.py", "types.py", True),
    # Utility modules - these are the gold we want!
    ("custom_components/hacs/utils/version.py", "utils/version.py", True),
    ("custom_components/hacs/utils/validate.py", "utils/validate.py", True),
    ("custom_components/hacs/utils/url.py", "utils/url.py", True),
    ("custom_components/hacs/utils/path.py", "utils/path.py", True),
    ("custom_components/hacs/utils/queue_manager.py", "utils/queue_manager.py", True),
    ("custom_components/hacs/utils/data.py", "utils/data.py", True),
    ("custom_components/hacs/utils/backup.py", "utils/backup.py", True),
    ("custom_components/hacs/utils/file_system.py", "utils/file_system.py", True),
    ("custom_components/hacs/utils/decorator.py", "utils/decorator.py", True),
    ("custom_components/hacs/utils/filters.py", "utils/filters.py", True),
]

# Minimal stubs for HA-internal dependencies
STUBS = {
    "homeassistant/loader.py": '''"""Minimal loader stub for HACS compatibility."""
from __future__ import annotations

def async_mount_config_dir(*args, **kwargs):
    """Stub for async_mount_config_dir."""
    pass
''',
    "homeassistant/helpers/device_registry.py": '''"""Minimal device registry stub for HACS compatibility."""
from __future__ import annotations

class DeviceEntry:
    """Stub for DeviceEntry."""
    pass

class DeviceRegistry:
    """Stub for DeviceRegistry."""
    pass

def async_get(*args, **kwargs):
    """Stub for async_get."""
    return DeviceRegistry()
''',
    "homeassistant/helpers/entity_registry.py": '''"""Minimal entity registry stub for HACS compatibility."""
from __future__ import annotations

class RegistryEntry:
    """Stub for RegistryEntry."""
    pass

class EntityRegistry:
    """Stub for EntityRegistry."""
    pass

def async_get(*args, **kwargs):
    """Stub for async_get."""
    return EntityRegistry()
''',
    "homeassistant/components/lovelace/__init__.py": '''"""Minimal lovelace stub for HACS compatibility."""
from __future__ import annotations

class LovelaceData:
    """Stub for LovelaceData."""
    pass
''',
    "homeassistant/components/websocket_api/__init__.py": '''"""Minimal websocket_api stub for HACS compatibility."""
from __future__ import annotations

def async_register_command(*args, **kwargs):
    """Stub for async_register_command."""
    pass

def websocket_command(*args, **kwargs):
    """Stub decorator for websocket_command."""
    def decorator(func):
        return func
    return decorator
''',
    "homeassistant/components/frontend/__init__.py": '''"""Minimal frontend stub for HACS compatibility."""
from __future__ import annotations

def async_register_built_in_panel(*args, **kwargs):
    """Stub for async_register_built_in_panel."""
    pass
''',
    "homeassistant/components/recorder/__init__.py": '''"""Minimal recorder stub for HACS compatibility."""
from __future__ import annotations

def async_initialize_recorder(*args, **kwargs):
    """Stub for async_initialize_recorder."""
    pass
''',
    "homeassistant/components/repairs/__init__.py": '''"""Minimal repairs stub for HACS compatibility."""
from __future__ import annotations

class IssueSeverity:
    """Stub for IssueSeverity."""
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"

class RepairsFlowManager:
    """Stub for RepairsFlowManager."""
    pass

def async_create_issue(*args, **kwargs):
    """Stub for async_create_issue."""
    pass

def async_delete_issue(*args, **kwargs):
    """Stub for async_delete_issue."""
    pass
''',
    "homeassistant/components/sensor/__init__.py": '''"""Minimal sensor stub for HACS compatibility."""
from __future__ import annotations

class SensorEntity:
    """Stub for SensorEntity."""
    pass

class SensorDeviceClass:
    """Stub for SensorDeviceClass."""
    pass
''',
    "homeassistant/components/update/__init__.py": '''"""Minimal update stub for HACS compatibility."""
from __future__ import annotations

class UpdateEntity:
    """Stub for UpdateEntity."""
    pass

class UpdateDeviceClass:
    """Stub for UpdateDeviceClass."""
    pass
''',
    "homeassistant/components/switch/__init__.py": '''"""Minimal switch stub for HACS compatibility."""
from __future__ import annotations

class SwitchEntity:
    """Stub for SwitchEntity."""
    pass
''',
    "homeassistant/components/diagnostics/__init__.py": '''"""Minimal diagnostics stub for HACS compatibility."""
from __future__ import annotations

async def async_get_config_diagnostics(*args, **kwargs):
    """Stub for async_get_config_diagnostics."""
    return {}
''',
    "homeassistant/components/system_health/__init__.py": '''"""Minimal system_health stub for HACS compatibility."""
from __future__ import annotations

async def async_info(*args, **kwargs):
    """Stub for async_info."""
    return {}
''',
    "homeassistant/components/config/__init__.py": '''"""Minimal config stub for HACS compatibility."""
from __future__ import annotations

class ConfigEntries:
    """Stub for ConfigEntries."""
    pass
''',
    "homeassistant/components/persistent_notification/__init__.py": '''"""Minimal persistent_notification stub for HACS compatibility."""
from __future__ import annotations

def async_create(*args, **kwargs):
    """Stub for async_create."""
    pass
''',
}

RAW_GITHUB_URL = "https://raw.githubusercontent.com/hacs/integration"
GITHUB_API_LATEST = "https://api.github.com/repos/hacs/integration/releases/latest"


def get_latest_version() -> str:
    """Fetch the latest HACS release tag from GitHub API."""
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


def clean_hacs_file(content: str, filename: str) -> str:
    """Clean HACS files to work without full HA."""
    # Replace HA imports with relative imports from shim
    # from homeassistant.core import HomeAssistant -> from ..core import HomeAssistant
    content = content.replace("from homeassistant.core import", "from ..core import")
    content = content.replace(
        "from homeassistant.config_entries import", "from ..config_entries import"
    )
    content = content.replace(
        "from homeassistant.exceptions import", "from ..exceptions import"
    )
    content = content.replace(
        "from homeassistant.helpers.event import", "from ..helpers.event import"
    )
    content = content.replace(
        "from homeassistant.helpers.storage import", "from ..helpers.storage import"
    )
    content = content.replace(
        "from homeassistant.helpers.dispatcher import",
        "from ..helpers.dispatcher import",
    )
    content = content.replace(
        "from homeassistant.helpers.aiohttp_client import",
        "from ..helpers.aiohttp_client import",
    )
    content = content.replace(
        "from homeassistant.helpers import device_registry as dr",
        "from ..helpers import device_registry as dr",
    )
    content = content.replace(
        "from homeassistant.helpers import entity_registry as er",
        "from ..helpers import entity_registry as er",
    )
    content = content.replace("from homeassistant.components", "from ..components")
    content = content.replace(
        "from homeassistant.loader import", "from ..loader import"
    )
    content = content.replace(
        "from homeassistant.requirements import", "from ..requirements import"
    )
    content = content.replace("from homeassistant.util", "from homeassistant.util")

    # Replace TYPE_CHECKING blocks to avoid circular imports
    content = content.replace(
        "from homeassistant.core import HomeAssistant",
        "if TYPE_CHECKING:\n    from ..core import HomeAssistant",
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
        description="Fetch HACS integration source files from GitHub"
    )
    parser.add_argument(
        "--version",
        help="Specific HACS version tag (e.g., 1.34.0). Defaults to latest release.",
    )
    parser.add_argument(
        "--dest",
        default="../rootfs/app/shim/hacs_fetched",
        help="Destination directory relative to script location (default: ../rootfs/app/shim/hacs_fetched)",
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
        print("Fetching latest HACS release version...")
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
    print("\nFetching HACS files...")
    for github_path, local_name, needs_cleaning in HACS_FILES:
        print(f"\n  Fetching {github_path}...")
        content = fetch_file(version, github_path)

        if content is None:
            print(f"  ✗ Failed to fetch {github_path}")
            continue

        if needs_cleaning:
            content = clean_hacs_file(content, local_name)

        if args.dry_run:
            print(f"  → Would write {len(content)} bytes to {local_name}")
        else:
            write_file(dest_dir, local_name, content)

    # Write __init__.py for utils package
    utils_init = '"""HACS utilities fetched from HACS integration."""\n'
    if args.dry_run:
        print(f"  → Would write utils/__init__.py")
    else:
        write_file(dest_dir, "utils/__init__.py", utils_init)

    # Write stubs
    print("\nWriting dependency stubs...")
    for stub_path, stub_content in STUBS.items():
        local_name = stub_path.replace("homeassistant/", "").replace("/", "_")
        if args.dry_run:
            print(f"  → Would write stub: {local_name}")
        else:
            write_file(dest_dir, f"_stub_{local_name}", stub_content)

    # Write __init__.py to make it a proper package
    init_content = f'''"""Auto-fetched HACS compatibility files.

This package contains utility files fetched from HACS integration {version}.

Key modules:
- utils/version.py: Version comparison using AwesomeVersion
- utils/validate.py: Validation schemas for manifests and hacs.json
- utils/url.py: URL builders for GitHub releases
- utils/path.py: Path safety validation
- utils/queue_manager.py: Async queue management for downloads
- utils/data.py: Data storage utilities
- utils/backup.py: Backup utilities during installation

Run `python3 fetch_hacs_files.py` to update to latest HACS release.
"""

__version__ = "{version}"
'''

    if args.dry_run:
        print(f"  → Would write __init__.py")
    else:
        write_file(dest_dir, "__init__.py", init_content)

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print("Dry run complete. No files were written.")
        print(f"To apply changes, run without --dry-run")
    else:
        print(f"✓ Successfully fetched HACS {version} files!")
        print(f"\nNext steps:")
        print(f"  1. Update requirements.txt:")
        print(f"     aiogithubapi>=22.10.1")
        print(f"     awesomeversion>=22.9.0")
        print(f"  2. Import HACS utilities in your code:")
        print(f"     from shim.hacs_fetched.utils.version import compare_versions")
        print(f"     from shim.hacs_fetched.utils.queue_manager import QueueManager")
        print(f"  3. Test version comparison and validation")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
