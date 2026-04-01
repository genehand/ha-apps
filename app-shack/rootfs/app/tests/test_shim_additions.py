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

    def test_external_integration_with_frozenorthawed_metaclass(self):
        """Test that external integrations can use FrozenOrThawed metaclass."""
        from dataclasses import FrozenInstanceError
        from shim.entity import EntityDescription
        from shim.frozen_dataclass_compat import FrozenOrThawed

        class ExternalEntityDescription(
            EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
        ):
            """External integration description using FrozenOrThawed."""

            custom_field: str = "default"

        desc = ExternalEntityDescription(
            key="external_key",
            custom_field="custom_value",
        )
        assert desc.key == "external_key"
        assert desc.custom_field == "custom_value"
        # Verify frozen behavior
        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"

    def test_external_integration_with_frozenorthawed_metaclass(self):
        """Test that external integrations can use FrozenOrThawed metaclass."""
        from dataclasses import FrozenInstanceError
        from shim.entity import EntityDescription
        from shim.frozen_dataclass_compat import FrozenOrThawed

        class ExternalEntityDescription(
            EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True
        ):
            """External integration description using FrozenOrThawed."""

            custom_field: str = "default"

        desc = ExternalEntityDescription(
            key="external_key",
            custom_field="custom_value",
        )
        assert desc.key == "external_key"
        assert desc.custom_field == "custom_value"
        # Verify frozen behavior
        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"

    def test_external_integration_with_dataclass_decorator(self):
        """Test that external integrations can use @dataclass(frozen=True).

        This tests the flightradar24, Leviton, and Dreo integration patterns.
        """
        from dataclasses import dataclass, FrozenInstanceError
        from shim.entity import EntityDescription

        @dataclass(frozen=True)
        class ExternalEntityDescription(EntityDescription):
            """External integration description using @dataclass decorator."""

            custom_field: str = "default"
            another_field: int = 42

        desc = ExternalEntityDescription(
            key="external_key",
            name="Test Entity",
            custom_field="custom_value",
            another_field=100,
        )
        assert desc.key == "external_key"
        assert desc.name == "Test Entity"
        assert desc.custom_field == "custom_value"
        assert desc.another_field == 100
        # Verify frozen behavior
        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"

    def test_external_integration_inherits_parent_fields(self):
        """Test that external integrations inherit all parent EntityDescription fields."""
        from dataclasses import dataclass
        from shim.entity import EntityDescription

        @dataclass(frozen=True)
        class CustomDescription(EntityDescription):
            """Custom description with additional fields."""

            extra: str = "extra_default"

        desc = CustomDescription(
            key="test_key",
            device_class="temperature",
            entity_category="diagnostic",
            icon="mdi:test",
            extra="extra_value",
        )

        # Verify all parent fields are accessible
        assert desc.key == "test_key"
        assert desc.device_class == "temperature"
        assert desc.entity_category == "diagnostic"
        assert desc.icon == "mdi:test"
        assert desc.extra == "extra_value"
        # Verify default values from parent
        assert desc.has_entity_name is False
        assert desc.entity_registry_enabled_default is True

    def test_platform_entity_description_frozen_behavior(self):
        """Test that platform EntityDescriptions are frozen and immutable."""
        from dataclasses import FrozenInstanceError
        from shim.platforms.sensor import SensorEntityDescription

        desc = SensorEntityDescription(
            key="test_sensor",
            state_class="measurement",
            native_unit_of_measurement="°C",
        )

        assert desc.key == "test_sensor"
        assert desc.state_class == "measurement"

        # Verify frozen behavior
        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"
        with pytest.raises(FrozenInstanceError):
            desc.state_class = "total"


class TestWebUISchemaParsing:
    """Tests for web UI schema parsing, including suggested_value support."""

    def test_parse_field_with_suggested_value(self):
        """Test that suggested_value from description is used as default."""
        from shim.web.app import WebUI
        import voluptuous as vol

        # Create a mock shim manager (we won't use it, just need the instance)
        web_ui = WebUI.__new__(WebUI)

        # Test vol.Optional with suggested_value (like cryptoinfo config_flow)
        key = vol.Optional("unit_of_measurement", description={"suggested_value": "$"})
        field = web_ui._parse_field(key, str)

        assert field["name"] == "unit_of_measurement"
        assert field["required"] is False
        assert field["default"] == "$"
        assert field["type"] == "text"

    def test_parse_field_with_regular_default(self):
        """Test that regular default values still work."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        # Test vol.Required with default
        key = vol.Required("currency_name", default="usd")
        field = web_ui._parse_field(key, str)

        assert field["name"] == "currency_name"
        assert field["required"] is True
        assert field["default"] == "usd"

    def test_parse_field_no_default_or_suggested_value(self):
        """Test that fields without defaults have no default key (None values are cleaned)."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        # Test vol.Optional without any default
        key = vol.Optional("precision")
        field = web_ui._parse_field(key, str)

        assert field["name"] == "precision"
        assert field["required"] is False
        # None values are cleaned up, so 'default' key should not exist
        assert "default" not in field

    def test_parse_field_default_overrides_suggested_value(self):
        """Test that explicit default takes precedence over suggested_value."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        # If both default and suggested_value exist, default wins
        key = vol.Optional(
            "test_field",
            default="explicit_default",
            description={"suggested_value": "suggested_value"},
        )
        field = web_ui._parse_field(key, str)

        assert field["default"] == "explicit_default"

    def test_parse_field_basic_label_generation(self):
        """Test basic label generation from field name."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        key = vol.Optional("test_field_name")
        field = web_ui._parse_field(key, str)

        assert field["name"] == "test_field_name"
        assert field["label"] == "Test Field Name"

    def test_parse_field_id_shows_title_case(self):
        """Test that 'id' field shows 'Id' by default (translations will override)."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        key = vol.Optional("id")
        field = web_ui._parse_field(key, str)

        assert field["name"] == "id"
        # Without translations, it just title-cases the name
        assert field["label"] == "Id"

    def test_parse_field_description_help_text(self):
        """Test that description/help text is extracted from field."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        key = vol.Optional(
            "id",
            description={
                "suggested_value": "my_id",
                "description": "Unique name for this sensor.",
            },
        )
        field = web_ui._parse_field(key, str)

        assert field["default"] == "my_id"
        assert field["description"] == "Unique name for this sensor."

    def test_parse_field_no_description(self):
        """Test that fields without description don't have description key."""
        from shim.web.app import WebUI
        import voluptuous as vol

        web_ui = WebUI.__new__(WebUI)

        key = vol.Optional("test_field")
        field = web_ui._parse_field(key, str)

        assert "description" not in field


class TestWebUITranslations:
    """Tests for web UI translation loading and application."""

    def test_load_integration_translations(self):
        """Test loading translations for an integration."""
        from shim.web.app import WebUI
        from pathlib import Path

        web_ui = WebUI.__new__(WebUI)

        # Create a mock integration manager that returns the actual path
        class MockIntegrationManager:
            def get_integration_path(self, domain):
                # Return the path to bundled integrations in rootfs
                return (
                    Path(__file__).parent.parent
                    / "data"
                    / "shim"
                    / "custom_components"
                    / domain
                )

        web_ui._integration_manager = MockIntegrationManager()

        # Test with cryptoinfo integration (which has translations)
        translations = web_ui._load_integration_translations("cryptoinfo")

        assert "config" in translations
        assert "step" in translations["config"]
        assert "user" in translations["config"]["step"]
        assert "data" in translations["config"]["step"]["user"]
        assert "id" in translations["config"]["step"]["user"]["data"]

    def test_load_integration_translations_missing_integration(self):
        """Test loading translations for non-existent integration."""
        from shim.web.app import WebUI
        from pathlib import Path

        web_ui = WebUI.__new__(WebUI)

        # Create a mock integration manager that returns None for missing integrations
        class MockIntegrationManager:
            def get_integration_path(self, domain):
                return None

        web_ui._integration_manager = MockIntegrationManager()

        translations = web_ui._load_integration_translations("nonexistent")
        assert translations == {}

    def test_apply_field_translations_labels(self):
        """Test applying field labels from translations."""
        from shim.web.app import WebUI

        web_ui = WebUI.__new__(WebUI)

        fields = [
            {"name": "id", "label": "Id"},
            {"name": "cryptocurrency_ids", "label": "Cryptocurrency Ids"},
        ]

        translations = {
            "config": {
                "step": {
                    "user": {
                        "data": {
                            "id": "Identifier",
                            "cryptocurrency_ids": "Cryptocurrency id's",
                        }
                    }
                }
            }
        }

        web_ui._apply_field_translations(fields, translations, "user")

        assert fields[0]["label"] == "Identifier"
        assert fields[1]["label"] == "Cryptocurrency id's"

    def test_apply_field_translations_descriptions(self):
        """Test applying field descriptions from translations."""
        from shim.web.app import WebUI

        web_ui = WebUI.__new__(WebUI)

        fields = [
            {"name": "id", "label": "Id"},
            {"name": "unit_of_measurement", "label": "Unit Of Measurement"},
        ]

        translations = {
            "config": {
                "step": {
                    "user": {
                        "data": {},
                        "data_description": {
                            "id": "Unique name for the sensor.",
                            "unit_of_measurement": "Currency symbol to use.",
                        },
                    }
                }
            }
        }

        web_ui._apply_field_translations(fields, translations, "user")

        assert fields[0]["description"] == "Unique name for the sensor."
        assert fields[1]["description"] == "Currency symbol to use."

    def test_apply_field_translations_missing_step(self):
        """Test that missing step in translations doesn't crash."""
        from shim.web.app import WebUI

        web_ui = WebUI.__new__(WebUI)

        fields = [{"name": "id", "label": "Id"}]
        translations = {"config": {"step": {}}}

        # Should not raise
        web_ui._apply_field_translations(fields, translations, "nonexistent_step")

        # Labels unchanged
        assert fields[0]["label"] == "Id"

    def test_apply_field_translations_reconfigure_step(self):
        """Test applying translations for reconfigure step."""
        from shim.web.app import WebUI

        web_ui = WebUI.__new__(WebUI)

        fields = [{"name": "id", "label": "Id"}]

        translations = {
            "config": {
                "step": {
                    "reconfigure": {
                        "data": {"id": "Identifier"},
                        "data_description": {"id": "Update the unique name."},
                    }
                }
            }
        }

        web_ui._apply_field_translations(fields, translations, "reconfigure")

        assert fields[0]["label"] == "Identifier"
        assert fields[0]["description"] == "Update the unique name."


class TestAiohttpClientCleanup:
    """Tests for aiohttp client session cleanup."""

    @pytest.mark.asyncio
    async def test_aiohttp_session_cleanup(self):
        """Test that aiohttp sessions are properly cleaned up."""
        from pathlib import Path
        from shim.import_patch import setup_import_patching
        from shim.core import HomeAssistant

        # Create a mock hass instance and set up import patching
        hass = HomeAssistant(Path("./data"))
        patcher = setup_import_patching(hass)
        patcher.patch()

        # Now we can import from homeassistant
        from homeassistant.helpers.aiohttp_client import (
            async_get_clientsession,
            _async_close_clientsessions,
        )

        # Get a client session (this should create and cache it)
        session = async_get_clientsession(hass)
        assert session is not None
        assert not session.closed

        # Close all sessions
        await _async_close_clientsessions()

        # Verify the session is now closed
        assert session.closed

    @pytest.mark.asyncio
    async def test_aiohttp_multiple_sessions_cleanup(self):
        """Test cleanup of multiple cached aiohttp sessions."""
        from pathlib import Path
        from shim.import_patch import setup_import_patching
        from shim.core import HomeAssistant

        # Create mock hass instances and set up import patching
        hass1 = HomeAssistant(Path("./data"))
        patcher = setup_import_patching(hass1)
        patcher.patch()

        # Now we can import from homeassistant
        from homeassistant.helpers.aiohttp_client import (
            async_get_clientsession,
            _async_close_clientsessions,
        )

        hass2 = HomeAssistant(Path("./data"))

        # Get client sessions with different verify_ssl settings
        session1 = async_get_clientsession(hass1, verify_ssl=True)
        session2 = async_get_clientsession(hass2, verify_ssl=False)

        assert session1 is not None
        assert session2 is not None
        assert session1 is not session2

        # Close all sessions
        await _async_close_clientsessions()

        # Verify all sessions are closed
        assert session1.closed
        assert session2.closed
