"""Tests for ConfigEntry.async_start_reauth support.

Verifies that ConfigEntry.async_start_reauth exists and works correctly,
preventing the AttributeError seen with nest_protect at shutdown.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import ConfigEntry, HomeAssistant


class TestConfigEntryReauth:
    """Test cases for ConfigEntry.async_start_reauth."""

    def _make_hass_mock(self, active_flows=None):
        """Create a mock hass with config_entries.flow.async_progress."""
        hass = MagicMock()
        # Set up config_entries.flow.async_progress
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress.return_value = active_flows or []
        # Schedule the coroutine via side effect so it doesn't leak as unawaited
        hass.async_create_task = MagicMock(
            side_effect=lambda coro, name=None: asyncio.ensure_future(coro)
        )
        return hass

    def test_has_async_start_reauth_method(self):
        """Test that ConfigEntry has the async_start_reauth method."""
        entry = ConfigEntry(
            entry_id="test_reauth_1",
            version=1,
            domain="nest_protect",
            title="Nest Protect",
        )
        assert hasattr(entry, "async_start_reauth")
        assert callable(entry.async_start_reauth)

    def test_has_async_get_active_flows_method(self):
        """Test that ConfigEntry has the async_get_active_flows method."""
        entry = ConfigEntry(
            entry_id="test_reauth_2",
            version=1,
            domain="nest_protect",
            title="Nest Protect",
        )
        assert hasattr(entry, "async_get_active_flows")
        assert callable(entry.async_get_active_flows)

    def test_async_start_reauth_without_hass_does_not_crash(self):
        """Test that calling async_start_reauth without hass logs a warning but doesn't crash.

        This covers the case where an integration calls it during shutdown
        and the hass reference is already gone.
        """
        entry = ConfigEntry(
            entry_id="test_reauth_3",
            version=1,
            domain="nest_protect",
            title="Nest Protect",
        )

        # Should not raise - hass is None
        entry.async_start_reauth(hass=None)

    @pytest.mark.asyncio
    async def test_async_start_reauth_creates_background_task(self):
        """Test that async_start_reauth creates a background task via hass."""
        entry = ConfigEntry(
            entry_id="test_reauth_4",
            version=1,
            domain="nest_protect",
            title="Nest Protect",
            data={"username": "test@example.com", "cookies": {"session": "abc"}},
        )

        hass = self._make_hass_mock(active_flows=[])

        # Call async_start_reauth (sync method, no await needed)
        entry.async_start_reauth(hass=hass)

        # Should have created a task via async_create_task
        hass.async_create_task.assert_called_once()

    def test_async_start_reauth_skips_when_flow_already_active(self):
        """Test that async_start_reauth skips if a reauth flow is already active."""
        entry = ConfigEntry(
            entry_id="test_reauth_5",
            version=1,
            domain="nest_protect",
            title="Nest Protect",
        )

        # Return an active reauth flow for this entry
        hass = self._make_hass_mock(active_flows=[
            {
                "flow_id": "existing_reauth_flow",
                "handler": "nest_protect",
                "context": {
                    "source": "reauth",
                    "entry_id": "test_reauth_5",
                },
            }
        ])

        # Call async_start_reauth
        entry.async_start_reauth(hass=hass)

        # Should NOT have created a task since a reauth flow already exists
        hass.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_get_active_flows_returns_matching_flows(self):
        """Test async_get_active_flows returns flows matching source and entry_id."""
        entry = ConfigEntry(
            entry_id="entry_001",
            version=1,
            domain="test_domain",
            title="Test",
        )

        hass = self._make_hass_mock(active_flows=[
            {
                "flow_id": "reauth_for_entry_001",
                "handler": "test_domain",
                "context": {
                    "source": "reauth",
                    "entry_id": "entry_001",
                },
            },
            {
                "flow_id": "reauth_for_other_entry",
                "handler": "test_domain",
                "context": {
                    "source": "reauth",
                    "entry_id": "entry_999",
                },
            },
            {
                "flow_id": "user_flow_for_entry_001",
                "handler": "test_domain",
                "context": {
                    "source": "user",
                    "entry_id": "entry_001",
                },
            },
        ])

        # Get active reauth flows for entry_001
        active = entry.async_get_active_flows(hass, {"reauth"})
        assert len(active) == 1
        assert active[0]["flow_id"] == "reauth_for_entry_001"

        # Get active flows for both reauth and user for entry_001
        active = entry.async_get_active_flows(hass, {"reauth", "user"})
        assert len(active) == 2

        # Get active flows for non-existent source
        active = entry.async_get_active_flows(hass, {"discovery"})
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_async_get_active_flows_with_empty_flows(self):
        """Test async_get_active_flows returns empty list when no flows exist."""
        entry = ConfigEntry(
            entry_id="entry_empty",
            version=1,
            domain="test_domain",
            title="Test",
        )

        hass = self._make_hass_mock(active_flows=[])

        active = entry.async_get_active_flows(hass, {"reauth"})
        assert active == []

    @pytest.mark.asyncio
    async def test_reauth_flow_init_called_via_background_task(self, tmp_path):
        """Integration test: async_start_reauth eventually calls flow.async_init."""
        hass = HomeAssistant(config_dir=tmp_path)

        entry = ConfigEntry(
            entry_id="int_test_reauth",
            version=1,
            domain="test_domain",
            title="Integration Test",
            data={"key": "value"},
        )

        # Mock the flow's async_init to track calls
        original_async_init = hass.config_entries.flow.async_init
        flow_init_called = False

        async def mock_async_init(domain, context=None, data=None):
            nonlocal flow_init_called
            flow_init_called = True
            assert domain == "test_domain"
            assert context["source"] == "reauth"
            assert context["entry_id"] == "int_test_reauth"
            assert context["unique_id"] is None
            assert data == {"key": "value"}
            return {"type": "abort", "reason": "mock"}

        hass.config_entries.flow.async_init = mock_async_init

        # Patch async_create_task to execute the coroutine immediately
        # This simulates what the event loop would do
        def mock_create_task(coro, name=None):
            # Schedule the coroutine to run
            task = asyncio.ensure_future(coro)
            return task

        hass.async_create_task = mock_create_task

        # Call async_start_reauth
        entry.async_start_reauth(hass=hass)

        # Give the background task a chance to run
        await asyncio.sleep(0)

        # Verify flow.async_init was called with correct parameters
        assert flow_init_called, "flow.async_init was not called"

        # Restore original
        hass.config_entries.flow.async_init = original_async_init

    @pytest.mark.asyncio
    async def test_reauth_flow_with_unique_id(self, tmp_path):
        """Test that unique_id is passed through to the reauth flow context."""
        hass = HomeAssistant(config_dir=tmp_path)

        entry = ConfigEntry(
            entry_id="int_test_unique",
            version=1,
            domain="test_domain",
            title="Test With UID",
            data={"unique_id": "device_abc_123"},
        )

        flow_init_called = False

        async def mock_async_init(domain, context=None, data=None):
            nonlocal flow_init_called
            flow_init_called = True
            assert context["unique_id"] == "device_abc_123"
            assert context["entry_id"] == "int_test_unique"
            return {"type": "abort", "reason": "mock"}

        hass.config_entries.flow.async_init = mock_async_init

        def mock_create_task(coro, name=None):
            task = asyncio.ensure_future(coro)
            return task

        hass.async_create_task = mock_create_task

        entry.async_start_reauth(hass=hass)
        await asyncio.sleep(0)
        assert flow_init_called

    @pytest.mark.asyncio
    async def test_reauth_flow_reuses_data_from_context(self, tmp_path):
        """Test that extra context passed to async_start_reauth is merged."""
        hass = HomeAssistant(config_dir=tmp_path)

        entry = ConfigEntry(
            entry_id="int_test_extra_ctx",
            version=1,
            domain="test_domain",
            title="Extra Context Test",
            data={"key": "original_value"},
        )

        flow_init_called = False

        async def mock_async_init(domain, context=None, data=None):
            nonlocal flow_init_called
            flow_init_called = True
            # Extra context should be merged in
            assert context.get("extra_context") == "some_extra_context"
            # Source should still be reauth
            assert context["source"] == "reauth"
            return {"type": "abort", "reason": "mock"}

        hass.config_entries.flow.async_init = mock_async_init

        def mock_create_task(coro, name=None):
            task = asyncio.ensure_future(coro)
            return task

        hass.async_create_task = mock_create_task

        # Call with extra context (like xiaomi_ble does: entry.async_start_reauth(hass, data={"device": data}))
        entry.async_start_reauth(
            hass=hass,
            context={"extra_context": "some_extra_context"},
            data={"device": {"mac": "AA:BB:CC:DD:EE:FF"}},
        )
        await asyncio.sleep(0)
        assert flow_init_called
