#!/usr/bin/env python3
"""Test script for meross_lan integration - test config flow like accessing /config/meross_lan."""

import asyncio
import sys
from pathlib import Path

# Add the app directory to path (scripts is a subdirectory of app)
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_meross_lan_config_flow():
    """Test meross_lan config flow like accessing /config/meross_lan endpoint."""
    from shim.core import HomeAssistant
    from shim.import_patch import setup_import_patching
    from shim.integrations.loader import IntegrationLoader
    from shim.integrations.manager import IntegrationManager
    from shim.logging import get_logger
    from shim.storage import Storage

    _LOGGER = get_logger("test")

    # Use the existing data directory with meross_lan installed
    app_dir = Path(__file__).parent.parent
    data_dir = app_dir / "data"
    shim_dir = data_dir / "shim"

    _LOGGER.info(f"Creating HomeAssistant in {data_dir}")
    hass = HomeAssistant(data_dir)

    # Setup import patching
    patcher = setup_import_patching(hass)
    patcher.patch()

    # Create integration manager and loader
    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    try:
        # Try to start config flow (this is what /config/meross_lan does)
        _LOGGER.info("Starting config flow for meross_lan...")

        flow_result = await loader.start_config_flow(domain="meross_lan")

        if flow_result is None:
            _LOGGER.error("FAILED: Config flow returned None")
            return False

        _LOGGER.info(f"SUCCESS: Config flow started!")
        _LOGGER.info(f"  Flow result: {flow_result}")

        return True

    except Exception as e:
        _LOGGER.error(f"FAILED: {e}")
        import traceback

        _LOGGER.error(traceback.format_exc())
        return False


if __name__ == "__main__":
    # Setup logging
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = asyncio.run(test_meross_lan_config_flow())
    sys.exit(0 if result else 1)
