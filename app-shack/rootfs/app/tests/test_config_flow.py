"""Tests for config flow and options flow methods in IntegrationLoader.

These tests verify that start_config_flow, continue_config_flow,
start_options_flow, and continue_options_flow work correctly.
"""

import pytest
import asyncio
import sys
from pathlib import Path
import tempfile
import uuid
import importlib

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching
from shim.integrations.loader import IntegrationLoader
from shim.integrations.manager import IntegrationManager
from shim.config_entries import ConfigEntry, ConfigFlow
from shim.storage import Storage


def generate_unique_domain(prefix="test"):
    """Generate a unique domain name to avoid module collisions."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def setup_import_path(shim_dir):
    """Add shim_dir to sys.path and invalidate import caches."""
    parent_path = str(shim_dir)
    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)
    importlib.invalidate_caches()


def cleanup_test_modules(domain):
    """Remove test modules from sys.modules to allow re-importing."""
    modules_to_remove = [
        f"custom_components.{domain}",
        f"custom_components.{domain}.config_flow",
        "custom_components",
    ]
    for mod in modules_to_remove:
        if mod in sys.modules:
            del sys.modules[mod]


def create_mock_integration_with_config_flow(shim_dir, domain):
    """Create a mock integration with config flow in the custom_components directory.

    Returns the path to the integration directory.
    """
    custom_components_dir = shim_dir / "custom_components"
    custom_components_dir.mkdir(parents=True, exist_ok=True)

    # Create __init__.py for custom_components package
    (custom_components_dir / "__init__.py").write_text("# custom_components package\n")

    # Create integration directory
    integration_dir = custom_components_dir / domain
    integration_dir.mkdir(exist_ok=True)

    # Create __init__.py with async_setup and async_setup_entry
    init_py = integration_dir / "__init__.py"
    init_py.write_text(f'''
async def async_setup(hass, config):
    """Set up the integration."""
    hass.data["{domain}"] = {{}}
    return True

async def async_setup_entry(hass, entry):
    """Set up a config entry."""
    hass.data["{domain}"][entry.entry_id] = {{"data": "test"}}
    return True
''')

    # Create manifest.json
    manifest = integration_dir / "manifest.json"
    manifest.write_text(f'''
{{
    "domain": "{domain}",
    "name": "Test Integration",
    "version": "1.0.0",
    "config_flow": true
}}
''')

    # Create config_flow.py
    config_flow_py = integration_dir / "config_flow.py"
    config_flow_py.write_text(f'''
from homeassistant.config_entries import ConfigFlow, OptionsFlow

class TestConfigFlow(ConfigFlow, domain="{domain}"):
    """Config flow for {domain}."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle initial step."""
        if user_input is not None:
            return self.async_create_entry(title="Test", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema={{
                "host": "str"
            }}
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get options flow."""
        return TestOptionsFlow(config_entry)


class TestOptionsFlow(OptionsFlow):
    """Options flow for {domain}."""

    async def async_step_init(self, user_input=None):
        """Handle options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema={{
                "option": "str"
            }}
        )
''')

    return integration_dir


def setup_test_env(temp_data_dir):
    """Set up test environment with hass, loader, and integration manager.

    Returns (hass, loader, shim_dir, integration_manager)
    """
    hass = HomeAssistant(temp_data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()

    shim_dir = temp_data_dir / "shim"
    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    return hass, loader, shim_dir, integration_manager


@pytest.mark.asyncio
async def test_start_config_flow_success():
    """Test that start_config_flow successfully starts a config flow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain to avoid collisions
        domain = generate_unique_domain("cf")

        # Create mock integration with config flow
        integration_dir = create_mock_integration_with_config_flow(shim_dir, domain)

        # Register the integration in manager
        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test CF",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Start the config flow
        result = await loader.start_config_flow(domain)

        assert result is not None
        assert "flow_id" in result
        assert result.get("type") == "form"
        assert result.get("step_id") == "user"

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_start_config_flow_no_config_flow_support():
    """Test start_config_flow returns None for integration without config flow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("nocf")

        # Create integration WITHOUT config_flow in manifest
        custom_components_dir = shim_dir / "custom_components"
        custom_components_dir.mkdir(parents=True, exist_ok=True)
        (custom_components_dir / "__init__.py").write_text("")

        integration_dir = custom_components_dir / domain
        integration_dir.mkdir(exist_ok=True)

        (integration_dir / "__init__.py").write_text("""
async def async_setup(hass, config):
    return True
""")

        (integration_dir / "manifest.json").write_text("""
{
    "domain": "''' + domain + '''",
    "name": "No Config Flow",
    "version": "1.0.0",
    "config_flow": false
}
""")

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="No CF",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=False,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Try to start config flow - should fail
        result = await loader.start_config_flow(domain)

        assert result is None

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_continue_config_flow_with_user_input():
    """Test continue_config_flow processes user input correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("continue")

        # Create mock integration with config flow
        create_mock_integration_with_config_flow(shim_dir, domain)

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test Continue",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Start the config flow
        result = await loader.start_config_flow(domain)
        assert result is not None
        flow_id = result["flow_id"]

        # Continue with user input
        user_input = {"host": "192.168.1.1"}
        result = await loader.continue_config_flow(domain, flow_id, user_input)

        assert result is not None
        assert result.get("type") == "create_entry"
        assert result.get("title") == "Test"
        assert result.get("data") == user_input

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_start_options_flow_success():
    """Test that start_options_flow successfully starts an options flow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("options")

        # Create mock integration with config flow
        create_mock_integration_with_config_flow(shim_dir, domain)

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test Options",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Create a config entry (already set up)
        entry = ConfigEntry(
            entry_id=f"{domain}_entry_123",
            version=1,
            domain=domain,
            title="Test Entry",
            data={"host": "192.168.1.1"},
            options={},
        )
        await hass.config_entries.async_add(entry)

        # First set up the integration so entry.state is "loaded"
        await loader.setup_integration(entry)
        assert entry.state == "loaded"

        # Start the options flow
        result = await loader.start_options_flow(entry)

        assert result is not None
        assert "flow_id" in result
        assert result.get("type") == "form"
        assert result.get("step_id") == "init"

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_start_options_flow_not_loaded_entry():
    """Test start_options_flow sets up entry if not already loaded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("notloaded")

        # Create mock integration
        create_mock_integration_with_config_flow(shim_dir, domain)

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test Not Loaded",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Create a config entry but DON'T set it up (state will be "not_loaded")
        entry = ConfigEntry(
            entry_id=f"{domain}_entry_456",
            version=1,
            domain=domain,
            title="Test Entry",
            data={"host": "192.168.1.1"},
            options={},
        )
        await hass.config_entries.async_add(entry)

        assert entry.state == "not_loaded"

        # Verify hass.data doesn't have entry data yet
        assert domain not in hass.data or entry.entry_id not in hass.data.get(
            domain, {}
        )

        # Start the options flow - should set up the entry first
        result = await loader.start_options_flow(entry)

        assert result is not None
        # Entry should now be loaded
        assert entry.state == "loaded"
        # Data should now exist
        assert hass.data.get(domain, {}).get(entry.entry_id) is not None

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_continue_options_flow_with_user_input():
    """Test continue_options_flow processes user input correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("contopts")

        # Create mock integration with config flow
        create_mock_integration_with_config_flow(shim_dir, domain)

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test Cont Opts",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Create and set up entry
        entry = ConfigEntry(
            entry_id=f"{domain}_entry_789",
            version=1,
            domain=domain,
            title="Test Entry",
            data={"host": "192.168.1.1"},
            options={},
        )
        await hass.config_entries.async_add(entry)
        await loader.setup_integration(entry)

        # Start options flow
        result = await loader.start_options_flow(entry)
        assert result is not None
        flow_id = result["flow_id"]

        # Continue with user input
        user_input = {"option": "value1"}
        result = await loader.continue_options_flow(entry, flow_id, user_input)

        assert result is not None
        assert result.get("type") == "create_entry"

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_continue_options_flow_missing_data_reloads():
    """Test continue_options_flow re-sets up entry if data is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, shim_dir, integration_manager = setup_test_env(temp_data_dir)

        # Generate unique domain
        domain = generate_unique_domain("missingdata")

        # Create mock integration
        create_mock_integration_with_config_flow(shim_dir, domain)

        from shim.integrations.manager import IntegrationInfo

        info = IntegrationInfo(
            domain=domain,
            name="Test Missing Data",
            version="1.0.0",
            description="Test",
            source="local",
            repository_url="https://example.com/test",
            enabled=True,
            config_flow=True,
        )
        integration_manager._integrations[domain] = info

        setup_import_path(shim_dir)

        # Create entry and set it up
        entry = ConfigEntry(
            entry_id=f"{domain}_entry_abc",
            version=1,
            domain=domain,
            title="Test Entry",
            data={"host": "192.168.1.1"},
            options={},
        )
        await hass.config_entries.async_add(entry)
        await loader.setup_integration(entry)

        # Verify data exists
        assert hass.data.get(domain, {}).get(entry.entry_id) is not None

        # Start options flow
        result = await loader.start_options_flow(entry)
        flow_id = result["flow_id"]

        # Now simulate restart by clearing hass.data (but entry.state is still "loaded")
        hass.data[domain] = {}

        # Verify data is now "missing"
        assert entry.entry_id not in hass.data.get(domain, {})

        # Continue options flow - should detect missing data and re-setup
        user_input = {"option": "value1"}
        result = await loader.continue_options_flow(entry, flow_id, user_input)

        assert result is not None
        # Data should have been restored by re-setup
        assert hass.data.get(domain, {}).get(entry.entry_id) is not None

        cleanup_test_modules(domain)


@pytest.mark.asyncio
async def test_continue_options_flow_no_flow_found():
    """Test continue_options_flow returns None when flow not found."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, _, _ = setup_test_env(temp_data_dir)

        entry = ConfigEntry(
            entry_id="test_no_flow_entry",
            version=1,
            domain="test_no_flow",
            title="Test Entry",
            data={},
            options={},
        )
        await hass.config_entries.async_add(entry)

        # Try to continue with non-existent flow_id
        result = await loader.continue_options_flow(entry, "nonexistent_flow_id", {})

        assert result is None


@pytest.mark.asyncio
async def test_start_config_flow_integration_not_found():
    """Test start_config_flow returns None for non-existent integration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_data_dir = Path(tmpdir)
        hass, loader, _, _ = setup_test_env(temp_data_dir)

        # Try to start config flow for non-existent integration
        result = await loader.start_config_flow("nonexistent_domain_xyz_12345")

        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
