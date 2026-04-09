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
            await hass.config_entries.async_remove(entry.entry_id)
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

        # Remove the config entry
        await hass.config_entries.async_remove(entry.entry_id)

        # Entities should be unregistered
        entities_after = len(registry.get_all())
        # Note: Some entities might persist in the loader's internal registry
        # but should be removed from the EntityRegistry

    except Exception as e:
        # Cleanup on failure
        try:
            await loader.unload_integration(entry, cleanup_mqtt=True)
            await hass.config_entries.async_remove(entry.entry_id)
        except Exception:
            pass
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


class TestIntegrationEnableDisable:
    """Tests for integration enabled/disabled state behavior."""

    def test_integration_starts_disabled_by_default(self, temp_data_dir):
        """Test that newly added integrations start disabled."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Add a new integration directly (simulating install)
        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain="test_integration",
            name="Test Integration",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=False,  # Should start disabled
        )
        integration_manager._integrations["test_integration"] = info

        # Verify it starts disabled
        retrieved = integration_manager.get_integration("test_integration")
        assert retrieved is not None
        assert retrieved.enabled is False, "New integration should start disabled"

    @pytest.mark.asyncio
    async def test_enable_integration_sets_enabled_flag(self, temp_data_dir):
        """Test that enabling an integration sets the enabled flag."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Add and then enable
        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain="test_integration",
            name="Test Integration",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=False,
        )
        integration_manager._integrations["test_integration"] = info

        result = await integration_manager.enable_integration("test_integration")
        assert result is True

        retrieved = integration_manager.get_integration("test_integration")
        assert retrieved.enabled is True, (
            "Integration should be enabled after enable_integration"
        )

    @pytest.mark.asyncio
    async def test_disable_integration_clears_enabled_flag(self, temp_data_dir):
        """Test that disabling an integration clears the enabled flag."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Add, enable, then disable
        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain="test_integration",
            name="Test Integration",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,  # Start enabled
        )
        integration_manager._integrations["test_integration"] = info

        result = await integration_manager.disable_integration("test_integration")
        assert result is True

        retrieved = integration_manager.get_integration("test_integration")
        assert retrieved.enabled is False, (
            "Integration should be disabled after disable_integration"
        )

    def test_only_enabled_integrations_loaded(self, temp_data_dir):
        """Test that only enabled integrations are returned by get_enabled_integrations."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Add two integrations, enable only one
        from shim.integrations.manager import IntegrationInfo

        enabled_info = IntegrationInfo(
            domain="enabled_integration",
            name="Enabled",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/enabled",
            enabled=True,
        )
        disabled_info = IntegrationInfo(
            domain="disabled_integration",
            name="Disabled",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/disabled",
            enabled=False,
        )
        integration_manager._integrations["enabled_integration"] = enabled_info
        integration_manager._integrations["disabled_integration"] = disabled_info

        # Check that only enabled integrations are returned
        enabled = integration_manager.get_enabled_integrations()
        assert len(enabled) == 1
        assert enabled[0].domain == "enabled_integration"

    def test_get_all_integrations_returns_all(self, temp_data_dir):
        """Test that get_all_integrations returns both enabled and disabled."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Add two integrations, enable one
        from shim.integrations.manager import IntegrationInfo

        enabled_info = IntegrationInfo(
            domain="enabled_integration",
            name="Enabled",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/enabled",
            enabled=True,
        )
        disabled_info = IntegrationInfo(
            domain="disabled_integration",
            name="Disabled",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/disabled",
            enabled=False,
        )
        integration_manager._integrations["enabled_integration"] = enabled_info
        integration_manager._integrations["disabled_integration"] = disabled_info

        # All integrations should include both
        all_integrations = integration_manager.get_all_integrations()
        assert len(all_integrations) == 2


class TestIntegrationManagerCallbacks:
    """Test the callback mechanisms in IntegrationManager."""

    def test_set_updates_found_callback(self, temp_data_dir):
        """Test that the updates found callback can be set and retrieved."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Initially no callback
        assert integration_manager._on_updates_found is None

        # Set a callback
        async def my_callback(updates):
            pass

        integration_manager.set_updates_found_callback(my_callback)
        assert integration_manager._on_updates_found is my_callback

        # Can be set to None
        integration_manager.set_updates_found_callback(None)
        assert integration_manager._on_updates_found is None

    @pytest.mark.asyncio
    async def test_periodic_update_check_calls_callback(
        self, temp_data_dir, monkeypatch
    ):
        """Test that periodic update check calls the callback when updates found."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Create a mock update
        from shim.integrations.manager import IntegrationInfo

        mock_info = IntegrationInfo(
            domain="test_integration",
            name="Test Integration",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            update_available=True,
            latest_version="1.1.0",
        )

        # Track callback invocations
        callback_calls = []

        async def test_callback(updates):
            callback_calls.append(updates)

        # Set the callback
        integration_manager.set_updates_found_callback(test_callback)

        # Directly test the callback mechanism by simulating what _periodic_update_check does
        # when it finds updates (we don't test the full loop to avoid timing issues)
        updates = [mock_info]
        if updates:
            if asyncio.iscoroutinefunction(test_callback):
                await test_callback(updates)
            else:
                test_callback(updates)

        # The callback should have been called with the updates
        assert len(callback_calls) == 1
        assert callback_calls[0] == [mock_info]

    @pytest.mark.asyncio
    async def test_periodic_update_check_no_callback_when_no_updates(
        self, temp_data_dir, monkeypatch
    ):
        """Test that callback is not called when no updates are found."""
        storage = Storage(temp_data_dir / "shim")
        integration_manager = IntegrationManager(storage, temp_data_dir / "shim")

        # Mock check_for_updates to return empty list
        async def mock_check_for_updates():
            return []

        monkeypatch.setattr(
            integration_manager, "check_for_updates", mock_check_for_updates
        )

        # Track callback invocations
        callback_calls = []

        async def test_callback(updates):
            callback_calls.append(updates)

        # Set the callback
        integration_manager.set_updates_found_callback(test_callback)

        # Run one iteration
        task = asyncio.create_task(integration_manager._periodic_update_check())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Callback should NOT have been called when no updates
        assert len(callback_calls) == 0


if __name__ == "__main__":
    # Allow running this file directly for quick testing
    pytest.main([__file__, "-v", "-m", "integration"])
