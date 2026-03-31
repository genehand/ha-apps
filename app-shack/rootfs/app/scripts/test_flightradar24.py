#!/usr/bin/env python3
"""Test script for flightradar24 integration setup."""

import asyncio
import sys
import tempfile
from pathlib import Path

# Add the app directory to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_flightradar24():
    """Test flightradar24 integration setup."""
    from shim.core import HomeAssistant
    from shim.import_patch import setup_import_patching
    from shim.integrations.loader import IntegrationLoader
    from shim.integrations.manager import IntegrationManager
    from shim.config_entries import ConfigEntry
    from shim.logging import get_logger
    from shim.storage import Storage

    _LOGGER = get_logger("test")

    # Use the existing data directory with flightradar24 installed
    data_dir = Path(__file__).parent / "data"
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

    # Create a config entry for flightradar24
    entry = ConfigEntry(
        entry_id="test_flightradar24_12345",
        version=1,
        domain="flightradar24",
        title="FlightRadar24",
        data={
            "radius": 1800.0,
            "latitude": 32.719682,
            "longitude": -117.129535,
            "scan_interval": 10,
        },
    )

    # Add the entry to hass
    await hass.config_entries.async_add(entry)
    _LOGGER.info(f"Added config entry: {entry.entry_id}")

    try:
        # Setup the integration
        _LOGGER.info("Setting up flightradar24 integration...")
        result = await loader.setup_integration(entry)

        if result:
            _LOGGER.info("SUCCESS: Integration setup completed!")

            # Check if entities were created
            entities = loader.get_entities(integration_domain="flightradar24")
            _LOGGER.info(f"Created {len(entities)} entities:")
            for entity in entities:
                _LOGGER.info(f"  - {entity.entity_id}")

            return True
        else:
            _LOGGER.error("FAILED: Integration setup returned False")
            return False

    except Exception as e:
        _LOGGER.error(f"FAILED: {e}")
        import traceback

        _LOGGER.error(traceback.format_exc())
        return False

    finally:
        # Cleanup with MQTT cleanup enabled
        try:
            await loader.unload_integration(entry, cleanup_mqtt=True)
            _LOGGER.info("Integration unloaded with MQTT cleanup")
        except Exception as e:
            _LOGGER.warning(f"Error during unload: {e}")


if __name__ == "__main__":
    # Setup logging
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = asyncio.run(test_flightradar24())
    sys.exit(0 if result else 1)
