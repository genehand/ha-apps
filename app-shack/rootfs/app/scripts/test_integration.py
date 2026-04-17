#!/usr/bin/env python3
"""Test loading HACS integrations.

This script attempts to load a specified integration to verify
all required HA module stubs are present.

Usage:
    python3 test_integration.py dreo
    python3 test_integration.py flightradar24
    python3 test_integration.py --all
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add the app directory to the path
# Script is in app-shack/rootfs/app/scripts, app code is in app-shack/rootfs/app
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.manager import IntegrationManager
from shim.integrations.loader import IntegrationLoader
from shim.storage import Storage


def get_available_integrations(data_dir: Path) -> list:
    """Get list of available integration domains."""
    custom_components = data_dir / "custom_components"
    if not custom_components.exists():
        return []

    integrations = []
    for item in custom_components.iterdir():
        if item.is_dir() and not item.name.startswith("__"):
            # Check for manifest.json
            manifest = item / "manifest.json"
            if manifest.exists():
                integrations.append(item.name)

    return sorted(integrations)


async def test_integration(domain: str, data_dir: Path) -> bool:
    """Test loading a single integration.

    Args:
        domain: The integration domain to test
        data_dir: Path to the shim data directory

    Returns:
        True if integration loaded successfully, False otherwise
    """
    tmp_path = data_dir.parent

    hass = HomeAssistant(config_dir=tmp_path)

    # Setup import patching
    patcher = setup_import_patching(hass)
    patcher.patch()

    # Create storage and integration manager
    storage = Storage(data_dir)
    integration_manager = IntegrationManager(storage, data_dir)

    # Create loader
    loader = IntegrationLoader(hass, integration_manager)

    # Try to load the integration
    try:
        result = await loader.load_integration(domain)
        return result
    except Exception as e:
        print(f"Error loading {domain}: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Test loading HACS integrations")
    parser.add_argument(
        "integration",
        nargs="?",
        help="Integration domain to test (e.g., 'dreo', 'flightradar24')",
    )
    parser.add_argument(
        "--all", action="store_true", help="Test all available integrations"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/shim"),
        help="Path to shim data directory (default: data/shim)",
    )

    args = parser.parse_args()

    if not args.integration and not args.all:
        parser.print_help()
        print("\nAvailable integrations:")
        for domain in get_available_integrations(args.data_dir):
            print(f"  - {domain}")
        sys.exit(1)

    if args.all:
        integrations = get_available_integrations(args.data_dir)
        if not integrations:
            print("No integrations found!")
            sys.exit(1)

        print(f"Testing {len(integrations)} integrations...\n")
        results = {}

        for domain in integrations:
            print(f"Testing {domain}... ", end="", flush=True)
            success = await test_integration(domain, args.data_dir)
            results[domain] = success
            print("✓" if success else "✗")

        print("\n" + "=" * 50)
        print("SUMMARY:")
        passed = sum(1 for v in results.values() if v)
        failed = sum(1 for v in results.values() if not v)

        for domain, success in results.items():
            status = "✓ PASS" if success else "✗ FAIL"
            print(f"  {domain}: {status}")

        print(f"\nTotal: {passed} passed, {failed} failed")
        sys.exit(0 if failed == 0 else 1)

    else:
        print(f"Testing integration: {args.integration}")
        success = await test_integration(args.integration, args.data_dir)

        if success:
            print(f"\n✓ Integration '{args.integration}' loaded successfully!")
            sys.exit(0)
        else:
            print(f"\n✗ Failed to load integration '{args.integration}'")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
