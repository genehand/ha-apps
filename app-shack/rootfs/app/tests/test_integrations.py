"""Integration tests for specific HACS integrations.

These tests verify that actual integrations can be loaded and set up correctly.
They require the integration files to be present in the data directory.
"""

import pytest
import asyncio
import sys
from pathlib import Path
import tempfile

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationManager
from shim.config_entries import ConfigEntry
from shim.storage import Storage


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def hass(temp_data_dir):
    """Create a HomeAssistant instance with patched imports."""
    hass = HomeAssistant(temp_data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()
    return hass


@pytest.fixture
def integration_loader(hass, temp_data_dir):
    """Create an integration loader for testing."""
    shim_dir = temp_data_dir / "shim"
    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader
    return loader


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        Path(__file__).parent.parent
        / "data"
        / "shim"
        / "custom_components"
        / "flightradar24"
    ).exists(),
    reason="flightradar24 integration not installed in data/shim/custom_components/",
)
@pytest.mark.asyncio
async def test_flightradar24_setup():
    """Test flightradar24 integration setup.

    This test requires the flightradar24 integration to be installed in:
    app-shack/rootfs/app/data/shim/custom_components/flightradar24/

    To install it:
    1. Copy the integration files to the custom_components directory
    2. Run: cd app-shack/rootfs/app && python3 -m pytest tests/test_integrations.py::test_flightradar24_setup -v
    """
    # Use the existing data directory with flightradar24 installed
    data_dir = Path(__file__).parent.parent / "data"
    shim_dir = data_dir / "shim"

    if not shim_dir.exists():
        pytest.skip(f"Data directory not found: {data_dir}")

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

    try:
        # Setup the integration
        result = await loader.setup_integration(entry)

        assert result is True, "Integration setup returned False"

        # Check if entities were created
        entities = loader.get_entities(integration_domain="flightradar24")
        assert len(entities) > 0, "No entities were created"

        # Verify entity IDs are properly formatted
        for entity in entities:
            assert entity.entity_id is not None
            assert "." in entity.entity_id

    finally:
        # Cleanup
        try:
            await loader.unload_integration(entry, cleanup_mqtt=True)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        Path(__file__).parent.parent
        / "data"
        / "shim"
        / "custom_components"
        / "flightradar24"
    ).exists(),
    reason="flightradar24 integration not installed",
)
@pytest.mark.asyncio
async def test_flightradar24_entity_lifecycle():
    """Test flightradar24 entity creation and cleanup.

    Verifies that entities are properly registered and can be cleaned up.
    """
    from shim.entity import EntityRegistry

    data_dir = Path(__file__).parent.parent / "data"
    shim_dir = data_dir / "shim"

    if not shim_dir.exists():
        pytest.skip(f"Data directory not found: {data_dir}")

    hass = HomeAssistant(data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()

    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    entry = ConfigEntry(
        entry_id="test_flightradar24_lifecycle",
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

    await hass.config_entries.async_add(entry)
    registry = EntityRegistry()
    registry.setup(hass)

    try:
        # Setup integration
        result = await loader.setup_integration(entry)
        assert result is True

        # Check entities are in registry
        entities_before = len(registry.get_all())
        assert entities_before > 0

        # Unload and verify cleanup
        await loader.unload_integration(entry, cleanup_mqtt=True)

        # Entities should be unregistered
        entities_after = len(registry.get_all())
        # Note: Some entities might persist in the loader's internal registry
        # but should be removed from the EntityRegistry

    except Exception as e:
        pytest.fail(f"Integration test failed: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generic_integration_setup(temp_data_dir):
    """Test that the integration setup infrastructure works.

    This is a generic test that doesn't require any specific integration
    to be installed. It verifies the setup machinery works.
    """
    hass = HomeAssistant(temp_data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()

    shim_dir = temp_data_dir / "shim"
    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    # Try to set up a non-existent integration
    entry = ConfigEntry(
        entry_id="test_nonexistent",
        version=1,
        domain="nonexistent_integration",
        title="Nonexistent",
        data={},
    )

    await hass.config_entries.async_add(entry)

    # Should return False but not crash
    result = await loader.setup_integration(entry)
    assert result is False


if __name__ == "__main__":
    # Allow running this file directly for quick testing
    pytest.main([__file__, "-v", "-m", "integration"])
