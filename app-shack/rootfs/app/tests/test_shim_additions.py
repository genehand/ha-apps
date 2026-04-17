"""Tests for shim modules added in this session."""

import pytest
from dataclasses import dataclass, FrozenInstanceError
from pathlib import Path


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
        from shim.hass import HomeAssistant
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
        from shim.hass import HomeAssistant
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
        from shim.hass import HomeAssistant
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

    @pytest.mark.asyncio
    async def test_async_get_devices_for_config_entry(self):
        """Test async_get_or_create_for_config_entry returns devices for a config entry."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                registry = dr.async_get(hass)

                # Create devices for different config entries
                device1 = registry.async_get_or_create(
                    config_entry_id="entry_1",
                    identifiers={("test_domain", "device_1")},
                    name="Device 1",
                )
                device2 = registry.async_get_or_create(
                    config_entry_id="entry_1",
                    identifiers={("test_domain", "device_2")},
                    name="Device 2",
                )
                device3 = registry.async_get_or_create(
                    config_entry_id="entry_2",
                    identifiers={("test_domain", "device_3")},
                    name="Device 3",
                )

                # Get devices for entry_1
                entry1_devices = registry.async_get_or_create_for_config_entry(
                    "entry_1"
                )

                assert len(entry1_devices) == 2
                assert device1 in entry1_devices
                assert device2 in entry1_devices
                assert device3 not in entry1_devices

                # Get devices for entry_2
                entry2_devices = registry.async_get_or_create_for_config_entry(
                    "entry_2"
                )
                assert len(entry2_devices) == 1
                assert device3 in entry2_devices

            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_async_get_devices_for_config_entry_empty(self):
        """Test async_get_or_create_for_config_entry returns empty list for unknown entry."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                registry = dr.async_get(hass)

                # Get devices for non-existent entry
                devices = registry.async_get_or_create_for_config_entry("unknown_entry")

                assert devices == []

            finally:
                patcher.unpatch()


class TestMqttStub:
    """Tests for the homeassistant.components.mqtt stub."""

    @pytest.mark.asyncio
    async def test_mqtt_module_imports(self):
        """Test that homeassistant.components.mqtt can be imported."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.components import mqtt

                assert mqtt is not None
                assert hasattr(mqtt, "async_publish")
                assert hasattr(mqtt, "async_subscribe")
                assert hasattr(mqtt, "ReceiveMessage")
                assert hasattr(mqtt, "MQTT_ERR_SUCCESS")
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_mqtt_async_publish_returns_none(self):
        """Test that mqtt.async_publish returns None (stub behavior)."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.components import mqtt

                result = await mqtt.async_publish(hass, "test/topic", "payload")
                assert result is None
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_mqtt_async_subscribe_returns_unsubscribe(self):
        """Test that mqtt.async_subscribe returns an unsubscribe function."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.components import mqtt

                def dummy_callback(msg):
                    pass

                unsub = await mqtt.async_subscribe(hass, "test/topic", dummy_callback)
                assert unsub is not None
                assert callable(unsub)
                # Call unsub to verify it works
                unsub()
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_mqtt_receive_message_class(self):
        """Test that mqtt.ReceiveMessage can be instantiated."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.components import mqtt

                msg = mqtt.ReceiveMessage(
                    topic="test/topic",
                    payload="test payload",
                    qos=1,
                    retain=True,
                    timestamp=12345,
                )
                assert msg.topic == "test/topic"
                assert msg.payload == "test payload"
                assert msg.qos == 1
                assert msg.retain is True
                assert msg.timestamp == 12345
            finally:
                patcher.unpatch()


class TestDeviceRegistryConstants:
    """Tests for device registry connection constants."""

    @pytest.mark.asyncio
    async def test_connection_constants_exist(self):
        """Test that device registry connection constants are available."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                assert hasattr(dr, "CONNECTION_NETWORK_MAC")
                assert dr.CONNECTION_NETWORK_MAC == "mac"
                assert hasattr(dr, "CONNECTION_UPNP")
                assert dr.CONNECTION_UPNP == "upnp"
                assert hasattr(dr, "CONNECTION_ASSUMED")
                assert dr.CONNECTION_ASSUMED == "assumed"
            finally:
                patcher.unpatch()

    @pytest.mark.asyncio
    async def test_async_update_device_exists(self):
        """Test that DeviceRegistry.async_update_device method exists."""
        from shim.import_patch import ImportPatcher
        from shim.hass import HomeAssistant
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            hass = HomeAssistant(Path(tmpdir))
            patcher = ImportPatcher(hass)
            patcher.patch()

            try:
                from homeassistant.helpers import device_registry as dr

                registry = dr.async_get(hass)
                assert hasattr(registry, "async_update_device")
                assert callable(registry.async_update_device)
            finally:
                patcher.unpatch()


class TestEntityClassAttributes:
    """Tests for Entity class-level attributes."""

    def test_entity_class_attributes_exist(self):
        """Test that Entity class has hass and platform as class attributes."""
        from shim.entity import Entity

        # These should be accessible as class attributes (for meross_lan compatibility)
        assert hasattr(Entity, "hass")
        assert hasattr(Entity, "platform")
        assert Entity.hass is None
        assert Entity.platform is None

        # Instance should inherit these
        entity = Entity()
        assert entity.hass is None
        assert entity.platform is None


class TestEntityCategoryProperty:
    """Tests for Entity.entity_category property."""

    def test_entity_category_from_description(self):
        """Test entity_category returns value from entity_description when set."""
        from shim.entity import Entity, EntityDescription

        class TestEntity(Entity):
            """Test entity with description."""

            def __init__(self):
                self.entity_description = EntityDescription(
                    key="test_key",
                    entity_category="diagnostic",
                )

        entity = TestEntity()
        assert entity.entity_category == "diagnostic"

    def test_entity_category_from_attr_when_description_is_none(self):
        """Test entity_category falls back to _attr_entity_category."""
        from shim.entity import Entity

        class TestEntity(Entity):
            """Test entity with _attr_entity_category set."""

            _attr_entity_category = "diagnostic"

        entity = TestEntity()
        assert entity.entity_category == "diagnostic"

    def test_entity_category_attr_takes_precedence_when_description_is_none(self):
        """Test _attr_entity_category is used when entity_description.entity_category is None."""
        from shim.entity import Entity, EntityDescription

        class TestEntity(Entity):
            """Test entity with both description (None category) and attr set."""

            _attr_entity_category = "config"

            def __init__(self):
                # Description has entity_category=None (default)
                self.entity_description = EntityDescription(key="test_key")

        entity = TestEntity()
        # Description has None, so should fall back to _attr_entity_category
        assert entity.entity_category == "config"

    def test_entity_category_description_takes_precedence_over_attr(self):
        """Test entity_description.entity_category takes precedence over _attr_entity_category."""
        from shim.entity import Entity, EntityDescription

        class TestEntity(Entity):
            """Test entity with both description category and attr set."""

            _attr_entity_category = "config"

            def __init__(self):
                # Description has explicit entity_category
                self.entity_description = EntityDescription(
                    key="test_key",
                    entity_category="diagnostic",
                )

        entity = TestEntity()
        # Description has explicit value, takes precedence
        assert entity.entity_category == "diagnostic"

    def test_entity_category_none_when_both_none(self):
        """Test entity_category is None when both description and attr are None."""
        from shim.entity import Entity, EntityDescription

        class TestEntity(Entity):
            """Test entity with no category set anywhere."""

            def __init__(self):
                self.entity_description = EntityDescription(key="test_key")

        entity = TestEntity()
        assert entity.entity_category is None


class TestConfigEntryUniqueId:
    """Tests for ConfigEntry.unique_id property."""

    def test_unique_id_from_explicit_field(self):
        """Test unique_id returns explicit field value."""
        from shim.models import ConfigEntry

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
        from shim.models import ConfigEntry

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
        from shim.models import ConfigEntry

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
        from shim.models import ConfigEntry

        entry = ConfigEntry(
            entry_id="test_000",
            version=1,
            domain="light",
            title="Test Light",
        )

        assert entry.unique_id is None

    def test_unique_id_setter(self):
        """Test setting unique_id via setter."""
        from shim.models import ConfigEntry

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

    @pytest.mark.skipif(
        not (
            Path(__file__).parent.parent
            / "data"
            / "shim"
            / "custom_components"
            / "cryptoinfo"
            / "translations"
            / "en.json"
        ).exists(),
        reason="cryptoinfo integration not installed in data/shim/custom_components/",
    )
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
        from shim.hass import HomeAssistant

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
        from shim.hass import HomeAssistant

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

    @pytest.mark.asyncio
    async def test_async_create_clientsession(self):
        """Test that async_create_clientsession creates new sessions."""
        from pathlib import Path
        from shim.import_patch import setup_import_patching
        from shim.hass import HomeAssistant

        # Create a mock hass instance and set up import patching
        hass = HomeAssistant(Path("./data"))
        patcher = setup_import_patching(hass)
        patcher.patch()

        # Now we can import from homeassistant
        from homeassistant.helpers.aiohttp_client import (
            async_create_clientsession,
            async_get_clientsession,
        )

        # async_create_clientsession should create a new session (not cached)
        # NOTE: async_create_clientsession is NOT an async function, just returns a session
        session1 = async_create_clientsession(hass)
        session2 = async_create_clientsession(hass)
        cached_session = async_get_clientsession(hass)

        assert session1 is not None
        assert session2 is not None
        assert session1 is not session2  # Each call creates a new session
        assert session1 is not cached_session  # Not from cache
        assert not session1.closed
        assert not session2.closed

        # Clean up
        await session1.close()
        await session2.close()

        assert session1.closed
        assert session2.closed


class TestSelectOptionDict:
    """Tests for SelectOptionDict TypedDict."""

    def test_select_option_dict_creation(self):
        """Test creating SelectOptionDict with value and label."""
        from shim.selectors import SelectOptionDict

        option: SelectOptionDict = {"value": "cloud", "label": "Cloud Mode"}
        assert option["value"] == "cloud"
        assert option["label"] == "Cloud Mode"

    def test_select_option_dict_in_list(self):
        """Test SelectOptionDict used in a list of options."""
        from shim.selectors import SelectOptionDict

        options: list[SelectOptionDict] = [
            {"value": "cloud", "label": "Cloud (Rinnai account)"},
            {"value": "local", "label": "Local (direct connection)"},
            {"value": "hybrid", "label": "Hybrid (local + cloud fallback)"},
        ]

        assert len(options) == 3
        assert options[0]["value"] == "cloud"
        assert options[0]["label"] == "Cloud (Rinnai account)"
        assert options[1]["value"] == "local"
        assert options[2]["value"] == "hybrid"


class TestSelectSelectorConfigParsing:
    """Tests for parsing SelectSelector config with different option formats."""

    def test_parse_select_option_dict_format(self):
        """Test parsing SelectSelector with SelectOptionDict format."""
        from shim.web.app import WebUI
        from shim.selectors import (
            SelectSelector,
            SelectSelectorConfig,
            SelectOptionDict,
        )

        # Create options in SelectOptionDict format (as used by rinnai integration)
        options: list[SelectOptionDict] = [
            {"value": "cloud", "label": "Cloud (Rinnai account)"},
            {"value": "local", "label": "Local (direct connection)"},
        ]

        selector = SelectSelector(SelectSelectorConfig(options=options))

        # Simulate the parsing logic from _parse_field
        config = selector.config
        parsed_options = []
        for opt in config.get("options", []):
            if isinstance(opt, dict) and "value" in opt and "label" in opt:
                parsed_options.append(
                    {
                        "value": opt["value"],
                        "label": opt["label"],
                        "selected": False,
                    }
                )
            else:
                parsed_options.append(
                    {
                        "value": opt,
                        "label": str(opt),
                        "selected": False,
                    }
                )

        # Verify proper parsing
        assert len(parsed_options) == 2
        assert parsed_options[0]["value"] == "cloud"
        assert parsed_options[0]["label"] == "Cloud (Rinnai account)"
        assert parsed_options[1]["value"] == "local"
        assert parsed_options[1]["label"] == "Local (direct connection)"

    def test_parse_simple_value_format(self):
        """Test parsing SelectSelector with simple value format."""
        from shim.selectors import SelectSelector, SelectSelectorConfig

        # Create options with simple string values
        selector = SelectSelector(SelectSelectorConfig(options=["option1", "option2"]))

        config = selector.config
        parsed_options = []
        for opt in config.get("options", []):
            if isinstance(opt, dict) and "value" in opt and "label" in opt:
                parsed_options.append(
                    {
                        "value": opt["value"],
                        "label": opt["label"],
                        "selected": False,
                    }
                )
            else:
                parsed_options.append(
                    {
                        "value": opt,
                        "label": str(opt),
                        "selected": False,
                    }
                )

        # Verify fallback to simple stringification
        assert len(parsed_options) == 2
        assert parsed_options[0]["value"] == "option1"
        assert parsed_options[0]["label"] == "option1"
        assert parsed_options[1]["value"] == "option2"
        assert parsed_options[1]["label"] == "option2"


class TestConfigFlowAsyncCreateEntry:
    """Tests for ConfigFlow.async_create_entry with options parameter."""

    def test_async_create_entry_without_options(self):
        """Test async_create_entry without options parameter."""
        from shim.config_entries import ConfigFlow

        flow = ConfigFlow()
        result = flow.async_create_entry(title="Test Entry", data={"key": "value"})

        assert result["type"] == "create_entry"
        assert result["title"] == "Test Entry"
        assert result["data"] == {"key": "value"}
        assert "options" not in result

    def test_async_create_entry_with_options(self):
        """Test async_create_entry with options parameter (as used by rinnai)."""
        from shim.config_entries import ConfigFlow

        flow = ConfigFlow()
        result = flow.async_create_entry(
            title="Rinnai Water Heater",
            data={"email": "test@example.com"},
            options={"maintenance_interval_enabled": True},
        )

        assert result["type"] == "create_entry"
        assert result["title"] == "Rinnai Water Heater"
        assert result["data"] == {"email": "test@example.com"}
        assert result["options"] == {"maintenance_interval_enabled": True}


class TestEntityNameFromTranslationKey:
    """Tests for deriving entity name from translation_key or key."""

    def test_name_from_translation_key(self):
        """Test that entity name is derived from translation_key when name is not set."""
        from shim.entity import Entity
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription:
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = None  # Explicitly no name

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Name should be derived from translation_key
        assert entity.name == "Outlet Temperature"

    def test_name_from_key_when_no_translation_key(self):
        """Test that entity name is derived from key when translation_key is not set."""
        from shim.entity import Entity
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription:
            key: str = "water_flow_rate"
            translation_key: str = None
            name: str = None

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Name should be derived from key
        assert entity.name == "Water Flow Rate"

    def test_explicit_name_takes_precedence(self):
        """Test that explicit name takes precedence over translation_key."""
        from shim.entity import Entity
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription:
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = "Custom Sensor Name"

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Explicit name should be used
        assert entity.name == "Custom Sensor Name"

    def test_attr_name_takes_precedence(self):
        """Test that _attr_name takes precedence over entity_description."""
        from shim.entity import Entity
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription:
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = "Entity Description Name"

        entity = Entity()
        entity._attr_name = "Attribute Name"
        entity.entity_description = MockEntityDescription()

        # _attr_name should take precedence
        assert entity.name == "Attribute Name"


class TestSensorEntityName:
    """Tests for SensorEntity name property with translation_key fallback."""

    def test_sensor_name_from_translation_key(self):
        """Test that SensorEntity name is derived from translation_key when name is not set."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = None  # Explicitly no name

        entity = SensorEntity()
        entity.entity_description = MockSensorEntityDescription()

        # Name should be derived from translation_key
        assert entity.name == "Outlet Temperature"

    def test_sensor_name_from_key_when_no_translation_key(self):
        """Test that SensorEntity name is derived from key when translation_key is not set."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "water_flow_rate"
            translation_key: str = None
            name: str = None

        entity = SensorEntity()
        entity.entity_description = MockSensorEntityDescription()

        # Name should be derived from key
        assert entity.name == "Water Flow Rate"

    def test_sensor_explicit_name_takes_precedence(self):
        """Test that explicit name takes precedence over translation_key in SensorEntity."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = "Custom Sensor Name"

        entity = SensorEntity()
        entity.entity_description = MockSensorEntityDescription()

        # Explicit name should be used
        assert entity.name == "Custom Sensor Name"

    def test_sensor_attr_name_takes_precedence(self):
        """Test that _attr_name takes precedence over entity_description in SensorEntity."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            translation_key: str = "outlet_temperature"
            name: str = "Entity Description Name"

        entity = SensorEntity()
        entity._attr_name = "Attribute Name"
        entity.entity_description = MockSensorEntityDescription()

        # _attr_name should take precedence
        assert entity.name == "Attribute Name"


class TestSensorNativeUnitOfMeasurement:
    """Tests for SensorEntity.native_unit_of_measurement property."""

    def test_native_unit_from_entity_description(self):
        """Test that native_unit_of_measurement comes from entity_description."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            native_unit_of_measurement: str = "°F"

        entity = SensorEntity()
        entity._attr_native_unit_of_measurement = None
        entity.entity_description = MockSensorEntityDescription()

        # Should get unit from entity_description
        assert entity.native_unit_of_measurement == "°F"

    def test_native_unit_attr_takes_precedence(self):
        """Test that _attr_native_unit_of_measurement takes precedence."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            native_unit_of_measurement: str = "°F"

        entity = SensorEntity()
        entity._attr_native_unit_of_measurement = "°C"
        entity.entity_description = MockSensorEntityDescription()

        # _attr should take precedence over entity_description
        assert entity.native_unit_of_measurement == "°C"

    def test_native_unit_in_mqtt_discovery(self):
        """Test that native_unit_of_measurement is included in MQTT discovery config."""
        from unittest.mock import MagicMock
        import json
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockSensorEntityDescription(SensorEntityDescription):
            key: str = "test_key"
            native_unit_of_measurement: str = "°F"
            state_class: str = "measurement"

        entity = SensorEntity()
        entity.entity_id = "sensor.test_outlet_temp"
        entity._attr_unique_id = "test_outlet_temp"
        entity.entity_description = MockSensorEntityDescription()

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        import asyncio

        asyncio.run(entity._publish_mqtt_discovery())

        # Find discovery config call
        discovery_call = None
        for call in mock_mqtt.publish.call_args_list:
            topic = call[0][0]
            if topic.endswith("/config"):
                discovery_call = call
                break

        assert discovery_call is not None
        payload = json.loads(discovery_call[0][1])

        # Verify unit_of_measurement is in config
        assert "unit_of_measurement" in payload
        assert payload["unit_of_measurement"] == "°F"
        assert payload["state_class"] == "measurement"

    def test_native_unit_fahrenheit_from_rinnai_pattern(self):
        """Test Rinnai-style sensor with Fahrenheit unit."""
        from shim.platforms.sensor import SensorEntity, SensorEntityDescription
        from dataclasses import dataclass

        @dataclass
        class RinnaiOutletTempDescription(SensorEntityDescription):
            key: str = "outlet_temperature"
            name: str = "Outlet Temperature"
            native_unit_of_measurement: str = "°F"
            state_class: str = "measurement"

        entity = SensorEntity()
        entity._attr_native_unit_of_measurement = None
        entity.entity_description = RinnaiOutletTempDescription()

        assert entity.native_unit_of_measurement == "°F"
        assert entity.state_class == "measurement"


class TestDisabledByDefault:
    """Tests for disabled_by_default field and entity_registry_enabled_default property."""

    def test_disabled_by_default_in_entity_description(self):
        """Test that disabled_by_default field exists in EntityDescription."""
        from shim.entity import EntityDescription

        # Default should be False (enabled by default)
        desc = EntityDescription(key="test_key")
        assert desc.disabled_by_default is False

        # Should be settable
        desc2 = EntityDescription(key="test_key", disabled_by_default=True)
        assert desc2.disabled_by_default is True

    def test_entity_registry_enabled_default_with_disabled_by_default(self):
        """Test that entity_registry_enabled_default returns False when disabled_by_default=True."""
        from shim.entity import Entity, EntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription(EntityDescription):
            key: str = "test_key"
            disabled_by_default: bool = True

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Entity should be disabled by default
        assert entity.entity_registry_enabled_default is False

    def test_entity_registry_enabled_default_with_explicit_enabled(self):
        """Test meross_lan pattern: entity_registry_enabled_default=False directly in description."""
        from shim.entity import Entity, EntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription(EntityDescription):
            key: str = "test_key"
            entity_registry_enabled_default: bool = False

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Entity should be disabled by default
        assert entity.entity_registry_enabled_default is False

    def test_disabled_by_default_takes_precedence_over_enabled(self):
        """Test that disabled_by_default=True takes precedence over entity_registry_enabled_default=True."""
        from shim.entity import Entity, EntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription(EntityDescription):
            key: str = "test_key"
            disabled_by_default: bool = True
            entity_registry_enabled_default: bool = True  # This should be ignored

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # disabled_by_default should take precedence
        assert entity.entity_registry_enabled_default is False

    def test_attr_disabled_by_default(self):
        """Test that _attr_disabled_by_default can disable entity at runtime."""
        from shim.entity import Entity, EntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription(EntityDescription):
            key: str = "test_key"
            disabled_by_default: bool = False  # Enabled in description

        entity = Entity()
        entity.entity_description = MockEntityDescription()
        entity._attr_disabled_by_default = True  # But disabled via attribute

        # Instance attribute should take precedence
        assert entity.entity_registry_enabled_default is False

    def test_enabled_by_default_when_no_flags_set(self):
        """Test that entity is enabled by default when no flags are set."""
        from shim.entity import Entity, EntityDescription
        from dataclasses import dataclass

        @dataclass
        class MockEntityDescription(EntityDescription):
            key: str = "test_key"

        entity = Entity()
        entity.entity_description = MockEntityDescription()

        # Entity should be enabled by default
        assert entity.entity_registry_enabled_default is True


class TestWaterHeaterState:
    """Tests for water_heater platform state property."""

    def test_water_heater_state_returns_current_operation(self):
        """Test that WaterHeaterEntity.state returns current_operation."""
        from shim.platforms.water_heater import WaterHeaterEntity, STATE_IDLE, STATE_GAS

        entity = WaterHeaterEntity()
        entity._attr_current_operation = STATE_IDLE

        # State should return the current operation mode
        assert entity.state == STATE_IDLE

        entity._attr_current_operation = STATE_GAS
        assert entity.state == STATE_GAS

    def test_water_heater_state_none_when_no_operation(self):
        """Test that WaterHeaterEntity.state returns None when no operation set."""
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        entity._attr_current_operation = None

        # State should be None when no operation
        assert entity.state is None


class TestWaterHeaterConstants:
    """Tests for water_heater platform constants."""

    def test_state_idle_constant_exists(self):
        """Test that STATE_IDLE constant exists."""
        from shim.platforms.water_heater import STATE_IDLE, STATE_GAS, STATE_OFF

        assert STATE_IDLE == "idle"
        assert STATE_GAS == "gas"
        assert STATE_OFF == "off"

    def test_all_state_constants_defined(self):
        """Test that all operation mode constants are defined."""
        from shim.platforms import water_heater

        # Check all expected constants exist
        assert water_heater.STATE_ECO == "eco"
        assert water_heater.STATE_ELECTRIC == "electric"
        assert water_heater.STATE_PERFORMANCE == "performance"
        assert water_heater.STATE_HIGH_DEMAND == "high_demand"
        assert water_heater.STATE_HEAT_PUMP == "heat_pump"
        assert water_heater.STATE_GAS == "gas"
        assert water_heater.STATE_OFF == "off"
        assert water_heater.STATE_ON == "on"
        assert water_heater.STATE_IDLE == "idle"

    def test_default_supported_features(self):
        """Test that WaterHeaterEntity has correct default supported_features."""
        from shim.platforms.water_heater import (
            WaterHeaterEntity,
            WaterHeaterEntityFeature,
        )

        entity = WaterHeaterEntity()
        # Default should be TARGET_TEMPERATURE | OPERATION_MODE | AWAY_MODE = 1 | 2 | 4 = 7
        expected = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.AWAY_MODE
        )
        assert entity.supported_features == expected
        assert entity.supported_features == 7

    def test_target_temperature_step_property(self):
        """Test that target_temperature_step property works."""
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        # Default step is 1.0
        assert entity.target_temperature_step == 1.0

        # Can be overridden
        entity._attr_target_temperature_step = 5.0
        assert entity.target_temperature_step == 5.0


class TestWaterHeaterAttributeFiltering:
    """Tests for water_heater filtering of inlet/outlet temperatures from attributes."""

    def test_inlet_outlet_temperatures_filtered_from_mqtt_attributes(self):
        """Test that inlet/outlet temperatures are not published to MQTT attributes.

        The Rinnai integration sets outlet_temperature and inlet_temperature in
        extra_state_attributes, but we have separate sensors for these and they
        may be in Celsius (causing confusion with the water heater's Fahrenheit).
        """
        from unittest.mock import MagicMock
        import json
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        entity.entity_id = "water_heater.rinnai_test"
        entity._attr_unique_id = "rinnai_test_wh"

        # Set extra_state_attributes like Rinnai does (with inlet/outlet temps)
        entity._attr_extra_state_attributes = {
            "outlet_temperature": 22.8,
            "inlet_temperature": 23.3,
            "some_other_attr": "value",
        }

        # Create mock hass with MQTT client
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        # Call the overridden _publish_mqtt_attributes
        entity._publish_mqtt_attributes()

        # Verify MQTT publish was called
        assert mock_mqtt.publish.called, "MQTT publish should have been called"

        # Get the published payload
        call_args = mock_mqtt.publish.call_args
        payload = json.loads(call_args[0][1])

        # inlet/outlet should be filtered out
        assert "outlet_temperature" not in payload, (
            "outlet_temperature should be filtered out"
        )
        assert "inlet_temperature" not in payload, (
            "inlet_temperature should be filtered out"
        )

        # Other attributes should still be present
        assert payload.get("some_other_attr") == "value"

    def test_all_temps_filtered_when_only_inlet_outlet_present(self):
        """Test that empty attributes don't cause publish when only inlet/outlet were present."""
        from unittest.mock import MagicMock
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        entity.entity_id = "water_heater.rinnai_test"

        # Only inlet/outlet temps - no other attributes
        entity._attr_extra_state_attributes = {
            "outlet_temperature": 22.8,
            "inlet_temperature": 23.3,
        }

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        entity.hass = mock_hass

        # Call _publish_mqtt_attributes
        entity._publish_mqtt_attributes()

        # Should NOT publish since all attributes were filtered out
        assert not mock_mqtt.publish.called, (
            "MQTT publish should not be called when all attrs filtered"
        )


class TestWaterHeaterModesDiscovery:
    """Tests for water_heater MQTT discovery modes list."""

    def test_modes_start_with_operation_list(self):
        """Test that MQTT discovery modes start with operation_list."""
        from unittest.mock import MagicMock
        import json
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        entity.entity_id = "water_heater.test"
        entity._attr_unique_id = "test_wh"
        entity._attr_operation_list = ["off", "on"]

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        mock_hass.async_add_job = MagicMock()
        entity.hass = mock_hass

        import asyncio

        asyncio.run(entity._publish_mqtt_discovery())

        discovery_call = None
        for call in mock_mqtt.publish.call_args_list:
            topic = call[0][0]
            if topic.endswith("/config"):
                discovery_call = call
                break

        assert discovery_call is not None
        payload = json.loads(discovery_call[0][1])
        modes = payload["modes"]

        # Initial modes should match operation_list
        assert "off" in modes
        assert "on" in modes
        assert len(modes) == 2  # Only operation_list states initially

    def test_modes_dynamically_add_new_states(self):
        """Test that new states are dynamically added to modes list."""
        from unittest.mock import MagicMock
        import json
        from shim.platforms.water_heater import WaterHeaterEntity

        entity = WaterHeaterEntity()
        entity.entity_id = "water_heater.test"
        entity._attr_unique_id = "test_wh"
        entity._attr_operation_list = ["off", "on"]
        entity._attr_current_operation = "idle"  # Not in operation_list

        mock_mqtt = MagicMock()
        mock_mqtt.is_connected.return_value = True

        mock_hass = MagicMock()
        mock_hass._mqtt_client = mock_mqtt
        mock_hass.async_add_job = MagicMock()
        entity.hass = mock_hass

        # First discovery - should have only operation_list
        import asyncio

        asyncio.run(entity._publish_mqtt_discovery())

        discovery_calls = [
            call
            for call in mock_mqtt.publish.call_args_list
            if call[0][0].endswith("/config")
        ]
        initial_modes = json.loads(discovery_calls[0][0][1])["modes"]
        assert "idle" not in initial_modes

        # Now simulate state update with new state
        entity._mqtt_publish()

        # Should trigger republish of discovery with new state
        assert mock_hass.async_add_job.called, (
            "Should republish discovery for new state"
        )

        # Verify new discovery includes the state
        republish_call = mock_hass.async_add_job.call_args[0][0]
        import asyncio

        asyncio.run(republish_call())

        # Check that modes now includes idle
        final_discovery = None
        for call in mock_mqtt.publish.call_args_list:
            topic = call[0][0]
            if topic.endswith("/config"):
                final_discovery = call

        assert final_discovery is not None
        final_payload = json.loads(final_discovery[0][1])
        final_modes = final_payload["modes"]
        assert "idle" in final_modes, "idle should be added to modes after seeing it"
        assert "off" in final_modes
        assert "on" in final_modes


class TestSensorEntityStateWithZero:
    """Tests for sensor entity state handling with zero values."""

    def test_sensor_entity_state_with_string_zero(self):
        """Test that sensor entities with '0' as state show correctly (nws_alerts issue)."""
        from shim.platforms.sensor import SensorEntity

        class TestSensor(SensorEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_unique_id = "test_sensor"
                self._attr_name = "Test Sensor"

        # Test with string "0" - this is the nws_alerts case
        entity_with_string_zero = TestSensor("0")
        assert entity_with_string_zero.native_value == "0"
        assert entity_with_string_zero.state == "0"
        assert entity_with_string_zero.available is True

        # Test with integer 0
        entity_with_int_zero = TestSensor(0)
        assert entity_with_int_zero.native_value == 0
        assert entity_with_int_zero.state == "0"
        assert entity_with_int_zero.available is True

        # Test with float 0.0
        entity_with_float_zero = TestSensor(0.0)
        assert entity_with_float_zero.native_value == 0.0
        assert entity_with_float_zero.state == "0.0"
        assert entity_with_float_zero.available is True

    def test_sensor_entity_available_with_zero_values(self):
        """Test that sensor entities with zero values are available."""
        from shim.platforms.sensor import SensorEntity

        class TestSensor(SensorEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_unique_id = "test_sensor"
                self._attr_name = "Test Sensor"

        # All zero values should be available
        assert TestSensor("0").available is True
        assert TestSensor(0).available is True
        assert TestSensor(0.0).available is True

        # But None should be unavailable
        assert TestSensor(None).available is False
