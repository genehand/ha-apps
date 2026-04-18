"""Tests for RestoreEntity state restoration functionality."""

import pytest
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.core import HomeAssistant
from shim.import_patch import setup_import_patching
from shim.platforms.text import TextEntity, RestoreEntity
from shim.storage import Storage


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
async def hass(temp_data_dir):
    """Create a HomeAssistant instance with patched imports."""
    hass = HomeAssistant(temp_data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()
    return hass


class TestRestoreEntity:
    """Test RestoreEntity state save and restore functionality."""

    @pytest.mark.asyncio
    async def test_restore_entity_saves_and_restores_state(self, hass):
        """Test that RestoreEntity saves state and can restore it later."""
        
        class TestTextRestoreEntity(TextEntity, RestoreEntity):
            """Test entity that supports state restoration."""
            
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_entity_001"
                self.entity_id = "text.test_restore"
                self._attr_native_value = ""
                
            async def async_set_value(self, value: str) -> None:
                self._attr_native_value = value
                self.async_write_ha_state()
        
        entity = TestTextRestoreEntity()
        entity.hass = hass
        entity._added = True
        
        await entity.async_set_value("LAX")
        
        state = hass.states.get("text.test_restore")
        assert state is not None
        assert state.state == "LAX"
        
        storage = Storage(hass.shim_dir)
        saved = storage.load_entity_state("text.test_restore")
        assert saved is not None
        assert saved["state"] == "LAX"
    
    @pytest.mark.asyncio
    async def test_async_get_last_state_returns_none_when_no_saved_state(self, hass):
        """Test that async_get_last_state returns None when no state was saved."""
        
        class TestTextRestoreEntity(TextEntity, RestoreEntity):
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_entity_002"
                self.entity_id = "text.test_no_state"
                self._attr_native_value = ""
                
            async def async_set_value(self, value: str) -> None:
                self._attr_native_value = value
                self.async_write_ha_state()
        
        entity = TestTextRestoreEntity()
        entity.hass = hass
        
        last_state = await entity.async_get_last_state()
        assert last_state is None
    
    @pytest.mark.asyncio
    async def test_async_get_last_state_returns_state_object(self, hass):
        """Test that async_get_last_state returns a State-like object."""
        
        class TestTextRestoreEntity(TextEntity, RestoreEntity):
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_entity_003"
                self.entity_id = "text.test_with_state"
                self._attr_native_value = ""
                
            async def async_set_value(self, value: str) -> None:
                self._attr_native_value = value
                self.async_write_ha_state()
        
        entity = TestTextRestoreEntity()
        entity.hass = hass
        entity._added = True
        
        await entity.async_set_value("JFK")
        
        last_state = await entity.async_get_last_state()
        assert last_state is not None
        assert hasattr(last_state, 'state')
        assert last_state.state == "JFK"


class TestRestoreEntityWithStorage:
    """Test RestoreEntity integration with Storage layer."""

    @pytest.mark.asyncio
    async def test_storage_save_and_load_entity_state(self, temp_data_dir):
        """Test that Storage properly saves and loads entity states."""
        
        storage = Storage(temp_data_dir / "shim")
        
        storage.save_entity_state("text.test_entity", "SFO", {"extra": "data"})
        
        saved = storage.load_entity_state("text.test_entity")
        assert saved is not None
        assert saved["state"] == "SFO"
        assert saved["attributes"]["extra"] == "data"
        assert "last_updated" in saved
    
    @pytest.mark.asyncio
    async def test_storage_remove_entity_state(self, temp_data_dir):
        """Test that Storage properly removes entity states."""
        
        storage = Storage(temp_data_dir / "shim")
        
        storage.save_entity_state("text.test_remove", "ORD")
        saved = storage.load_entity_state("text.test_remove")
        assert saved is not None
        
        storage.remove_entity_state("text.test_remove")
        saved = storage.load_entity_state("text.test_remove")
        assert saved is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestRestoreSensor:
    """Test RestoreSensor state save and restore functionality."""

    @pytest.mark.asyncio
    async def test_restore_sensor_saves_and_restores_state(self, hass):
        """Test that RestoreSensor saves state and can restore it later."""
        from shim.platforms.sensor import SensorEntity, RestoreSensor
        
        class TestRestoreSensorEntity(SensorEntity, RestoreSensor):
            """Test sensor entity that supports state restoration."""
            
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_sensor_001"
                self.entity_id = "sensor.test_restore_sensor"
                self._attr_native_value = 0
                
        entity = TestRestoreSensorEntity()
        entity.hass = hass
        entity._added = True
        
        # Update value and write state
        entity._attr_native_value = 42
        entity.async_write_ha_state()
        
        # Verify state was written
        state = hass.states.get("sensor.test_restore_sensor")
        assert state is not None
        assert state.state == "42"
        
        # Verify state was saved to storage
        storage = Storage(hass.shim_dir)
        saved = storage.load_entity_state("sensor.test_restore_sensor")
        assert saved is not None
        assert saved["state"] == "42"
    
    @pytest.mark.asyncio
    async def test_restore_sensor_async_get_last_sensor_data(self, hass):
        """Test that async_get_last_sensor_data returns proper sensor data."""
        from shim.platforms.sensor import SensorEntity, RestoreSensor
        
        class TestRestoreSensorEntity(SensorEntity, RestoreSensor):
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_sensor_002"
                self.entity_id = "sensor.test_sensor_data"
                self._attr_native_value = 100
                self._attr_native_unit_of_measurement = "km"
                
        entity = TestRestoreSensorEntity()
        entity.hass = hass
        entity._added = True
        
        # Save state
        entity.async_write_ha_state()
        
        # Get sensor data
        sensor_data = await entity.async_get_last_sensor_data()
        assert sensor_data is not None
        assert sensor_data.native_value == "100"
        assert sensor_data.native_unit_of_measurement == "km"
    
    @pytest.mark.asyncio
    async def test_restore_sensor_returns_none_when_no_saved_state(self, hass):
        """Test that RestoreSensor returns None when no state was saved."""
        from shim.platforms.sensor import SensorEntity, RestoreSensor
        
        class TestRestoreSensorEntity(SensorEntity, RestoreSensor):
            def __init__(self):
                super().__init__()
                self._attr_unique_id = "test_restore_sensor_003"
                self.entity_id = "sensor.test_no_saved"
                self._attr_native_value = 0
                
        entity = TestRestoreSensorEntity()
        entity.hass = hass
        
        last_state = await entity.async_get_last_state()
        assert last_state is None
        
        sensor_data = await entity.async_get_last_sensor_data()
        assert sensor_data is None
