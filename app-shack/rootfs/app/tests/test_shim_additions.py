"""Tests for shim modules added in this session."""

import pytest
from dataclasses import dataclass, FrozenInstanceError


class TestEntityDescription:
    """Tests for EntityDescription dataclass."""

    def test_entity_description_creation(self):
        """Test creating EntityDescription with all fields."""
        from shim.entity import EntityDescription

        desc = EntityDescription(
            key="test_key",
            device_class="temperature",
            entity_category="diagnostic",
            entity_registry_enabled_default=True,
            entity_registry_visible_default=True,
            has_entity_name=True,
            icon="mdi:thermometer",
            name="Test Entity",
            translation_key="test_translation",
            unit_of_measurement="°C",
        )

        assert desc.key == "test_key"
        assert desc.device_class == "temperature"
        assert desc.entity_category == "diagnostic"
        assert desc.entity_registry_enabled_default is True
        assert desc.entity_registry_visible_default is True
        assert desc.has_entity_name is True
        assert desc.icon == "mdi:thermometer"
        assert desc.name == "Test Entity"
        assert desc.translation_key == "test_translation"
        assert desc.unit_of_measurement == "°C"

    def test_entity_description_defaults(self):
        """Test EntityDescription with default values."""
        from shim.entity import EntityDescription

        desc = EntityDescription(key="simple_key")

        assert desc.key == "simple_key"
        assert desc.device_class is None
        assert desc.entity_category is None
        assert desc.entity_registry_enabled_default is True
        assert desc.entity_registry_visible_default is True
        assert desc.has_entity_name is False
        assert desc.icon is None
        assert desc.name is None
        assert desc.translation_key is None
        assert desc.unit_of_measurement is None


class TestSelectorConfigTypes:
    """Tests for selector config type aliases."""

    def test_selector_configs_are_dicts(self):
        """Test that selector config types are dict aliases."""
        from shim.selectors import (
            EntitySelectorConfig,
            DeviceSelectorConfig,
            NumberSelectorConfig,
            SelectSelectorConfig,
            TextSelectorConfig,
            BooleanSelectorConfig,
        )

        assert EntitySelectorConfig is dict
        assert DeviceSelectorConfig is dict
        assert NumberSelectorConfig is dict
        assert SelectSelectorConfig is dict
        assert TextSelectorConfig is dict
        assert BooleanSelectorConfig is dict

    def test_all_selector_configs_exist(self):
        """Test that all selector config types are importable."""
        import shim.selectors as selectors

        config_types = [
            "EntitySelectorConfig",
            "DeviceSelectorConfig",
            "AreaSelectorConfig",
            "NumberSelectorConfig",
            "BooleanSelectorConfig",
            "TextSelectorConfig",
            "SelectSelectorConfig",
            "TimeSelectorConfig",
            "DateSelectorConfig",
            "DateTimeSelectorConfig",
            "ColorRGBSelectorConfig",
            "IconSelectorConfig",
            "ThemeSelectorConfig",
            "LocationSelectorConfig",
            "MediaSelectorConfig",
            "DurationSelectorConfig",
            "ObjectSelectorConfig",
            "AttributeSelectorConfig",
            "ActionSelectorConfig",
            "AddonSelectorConfig",
            "AreaFilterSelectorConfig",
            "AssistPipelineSelectorConfig",
            "BackupLocationSelectorConfig",
            "BarcodeSelectorConfig",
            "ColorTempSelectorConfig",
            "ConfigEntrySelectorConfig",
            "ConstantSelectorConfig",
            "ConversationAgentSelectorConfig",
            "CountrySelectorConfig",
            "DateTimeRangeSelectorConfig",
            "EntityFilterSelectorConfig",
            "FileSelectorConfig",
            "FloorSelectorConfig",
            "LabelSelectorConfig",
            "LanguageSelectorConfig",
            "NavigationLocationSelectorConfig",
            "QRCodeSelectorConfig",
            "ResourceSelectorConfig",
            "SelectorSelectorConfig",
            "StateSelectorConfig",
            "StatisticsPeriodSelectorConfig",
            "TargetSelectorConfig",
            "TemplateSelectorConfig",
            "TimeZoneSelectorConfig",
            "TriggerSelectorConfig",
            "UserSelectorConfig",
        ]

        for config_type in config_types:
            assert hasattr(selectors, config_type), f"Missing {config_type}"
            assert getattr(selectors, config_type) is dict


class TestSelectorModeEnums:
    """Tests for selector mode enums."""

    def test_select_selector_mode_values(self):
        """Test SelectSelectorMode enum values."""
        from shim.selectors import SelectSelectorMode

        assert SelectSelectorMode.LIST == "list"
        assert SelectSelectorMode.DROPDOWN == "dropdown"

    def test_text_selector_type_values(self):
        """Test TextSelectorType enum values."""
        from shim.selectors import TextSelectorType

        assert TextSelectorType.TEXT == "text"
        assert TextSelectorType.PASSWORD == "password"
        assert TextSelectorType.EMAIL == "email"
        assert TextSelectorType.URL == "url"
        assert TextSelectorType.TEL == "tel"
        assert TextSelectorType.NUMBER == "number"

    def test_number_selector_mode_values(self):
        """Test NumberSelectorMode enum values."""
        from shim.selectors import NumberSelectorMode

        assert NumberSelectorMode.BOX == "box"
        assert NumberSelectorMode.SLIDER == "slider"


class TestSelectSelectorOptions:
    """Tests for SelectSelector options handling."""

    def test_select_selector_with_options(self):
        """Test SelectSelector with explicit options."""
        from shim.selectors import SelectSelector

        selector = SelectSelector(options=["option1", "option2"])
        assert selector.config["options"] == ["option1", "option2"]

    def test_select_selector_with_config(self):
        """Test SelectSelector with config dict containing options."""
        from shim.selectors import SelectSelector

        selector = SelectSelector(
            config={"options": ["a", "b", "c"]}, multiple=True, mode="dropdown"
        )
        assert selector.config["options"] == ["a", "b", "c"]
        assert selector.config["multiple"] is True
        assert selector.config["mode"] == "dropdown"

    def test_select_selector_default_empty_list(self):
        """Test SelectSelector defaults to empty list when no options provided."""
        from shim.selectors import SelectSelector

        selector = SelectSelector()
        assert selector.config["options"] == []


class TestConfigFlowAdvancedOptions:
    """Tests for ConfigFlow show_advanced_options."""

    def test_show_advanced_options_default(self):
        """Test show_advanced_options defaults to False."""
        from shim.config_entries import ConfigFlow

        flow = ConfigFlow()
        assert flow.show_advanced_options is False

    def test_show_advanced_options_setter(self):
        """Test setting show_advanced_options."""
        from shim.config_entries import ConfigFlow

        flow = ConfigFlow()
        flow.show_advanced_options = True
        assert flow.show_advanced_options is True

    def test_show_advanced_options_from_context(self):
        """Test show_advanced_options reads from context."""
        from shim.config_entries import ConfigFlow

        flow = ConfigFlow()
        flow.context = {"show_advanced_options": True}
        assert flow.show_advanced_options is True

    def test_show_advanced_options_subclass_without_super_init(self):
        """Test show_advanced_options works when subclass doesn't call super().__init__()."""
        from shim.config_entries import ConfigFlow

        class SubConfigFlow(ConfigFlow):
            def __init__(self):
                # Don't call super().__init__()
                self.custom_attr = "test"

        flow = SubConfigFlow()
        # Should not raise AttributeError
        assert flow.show_advanced_options is False
        assert flow.context == {}


class TestOptionsFlowAdvancedOptions:
    """Tests for OptionsFlow show_advanced_options."""

    def test_options_flow_show_advanced_options_default(self):
        """Test show_advanced_options defaults to False for OptionsFlow."""
        from shim.config_entries import OptionsFlow

        class MockConfigEntry:
            pass

        flow = OptionsFlow(MockConfigEntry())
        assert flow.show_advanced_options is False

    def test_options_flow_show_advanced_options_setter(self):
        """Test setting show_advanced_options on OptionsFlow."""
        from shim.config_entries import OptionsFlow

        class MockConfigEntry:
            pass

        flow = OptionsFlow(MockConfigEntry())
        flow.show_advanced_options = True
        assert flow.show_advanced_options is True


class TestNewConstants:
    """Tests for new constants added to shim.ha_fetched.const."""

    def test_conf_id_exists(self):
        """Test CONF_ID constant exists."""
        from shim.ha_fetched.const import CONF_ID

        assert CONF_ID == "id"

    def test_platform_enum_values(self):
        """Test Platform enum has expected values."""
        from shim.ha_fetched.const import Platform

        assert Platform.SENSOR == "sensor"
        assert Platform.SWITCH == "switch"
        assert Platform.LIGHT == "light"
        assert Platform.FAN == "fan"
        assert Platform.CLIMATE == "climate"
        assert Platform.BINARY_SENSOR == "binary_sensor"

    def test_unit_of_temperature_enum(self):
        """Test UnitOfTemperature enum values."""
        from shim.ha_fetched.const import UnitOfTemperature

        assert UnitOfTemperature.CELSIUS == "°C"
        assert UnitOfTemperature.FAHRENHEIT == "°F"
        assert UnitOfTemperature.KELVIN == "K"

    def test_entity_category_enum(self):
        """Test EntityCategory enum from shim.ha_fetched.const."""
        from shim.ha_fetched.const import EntityCategory

        assert EntityCategory.CONFIG == "config"
        assert EntityCategory.DIAGNOSTIC == "diagnostic"


class TestDeviceRegistry:
    """Tests for device registry functionality."""

    @pytest.mark.asyncio
    async def test_async_get_returns_registry(self):
        """Test that async_get returns a DeviceRegistry instance."""
        from shim.import_patch import ImportPatcher
        from shim.core import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                registry = dr.async_get(hass)
                assert registry is not None
                assert hasattr(registry, "async_get_or_create")
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_device_entry_type_enum(self):
        """Test DeviceEntryType enum exists."""
        from shim.import_patch import ImportPatcher
        from shim.core import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                assert hasattr(dr, "DeviceEntryType")
                assert dr.DeviceEntryType.SERVICE.value == "service"
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_async_get_or_create_creates_device(self):
        """Test async_get_or_create creates a device entry."""
        from shim.import_patch import ImportPatcher
        from shim.core import HomeAssistant
        from pathlib import Path
        import tempfile
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                registry = dr.async_get(hass)
                device = registry.async_get_or_create(
                    config_entry_id="test_entry",
                    identifiers={("test_domain", "test_id")},
                    manufacturer="Test Manufacturer",
                    model="Test Model",
                    name="Test Device",
                )

                assert device is not None
                assert device.name == "Test Device"
                assert device.manufacturer == "Test Manufacturer"
                assert device.model == "Test Model"
            finally:
                patcher.unpatch()


class TestConfigEntryUniqueId:
    """Tests for ConfigEntry.unique_id property."""

    def test_unique_id_from_explicit_field(self):
        """Test unique_id returns explicit field value."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_123",
            version=1,
            domain="sensor",
            title="Test Sensor",
        )
        entry.unique_id = "explicit_id"

        assert entry.unique_id == "explicit_id"

    def test_unique_id_from_data(self):
        """Test unique_id falls back to data dict."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_456",
            version=1,
            domain="sensor",
            title="Test Sensor",
            data={"unique_id": "from_data"},
        )

        assert entry.unique_id == "from_data"

    def test_unique_id_explicit_overrides_data(self):
        """Test explicit unique_id overrides data dict."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_789",
            version=1,
            domain="sensor",
            title="Test Sensor",
            data={"unique_id": "from_data"},
        )
        entry.unique_id = "explicit_id"

        assert entry.unique_id == "explicit_id"

    def test_unique_id_none_when_not_set(self):
        """Test unique_id is None when not set anywhere."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_000",
            version=1,
            domain="light",
            title="Test Light",
        )

        assert entry.unique_id is None

    def test_unique_id_setter(self):
        """Test setting unique_id via setter."""
        from shim.core import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_setter",
            version=1,
            domain="sensor",
            title="Test",
        )

        entry.unique_id = "new_id"
        assert entry.unique_id == "new_id"


class TestEntityDescriptionWorksWithIntegrations:
    """Tests that EntityDescription works with real integration patterns."""

    def test_entity_description_basic_creation(self):
        """Test basic EntityDescription creation."""
        from shim.entity import EntityDescription

        desc = EntityDescription(
            key="test_key",
            name="Test Name",
            device_class="temperature",
        )
        assert desc.key == "test_key"
        assert desc.name == "Test Name"
        assert desc.device_class == "temperature"

    def test_sensor_entity_description_creation(self):
        """Test SensorEntityDescription creation."""
        from shim.platforms.sensor import SensorEntityDescription

        desc = SensorEntityDescription(
            key="test_key",
            name="Test Sensor",
            state_class="measurement",
            native_unit_of_measurement="°C",
            options=["option1", "option2"],
        )
        assert desc.key == "test_key"
        assert desc.state_class == "measurement"
        assert desc.native_unit_of_measurement == "°C"
        assert desc.options == ["option1", "option2"]
