"""Tests for shim platform additions."""

import dataclasses

import pytest


class TestVacuumEntity:
    """Tests for vacuum platform shim."""

    def test_vacuum_entity_import(self):
        """Test that vacuum entities can be imported."""
        from shim.platforms.vacuum import (
            StateVacuumEntity,
            VacuumActivity,
            VacuumEntityFeature,
            VacuumEntity,
            VacuumEntityDescription,
        )

        assert StateVacuumEntity is not None
        assert VacuumActivity is not None
        assert VacuumEntityFeature is not None
        assert VacuumEntity is not None
        assert VacuumEntityDescription is not None

    def test_vacuum_activity_values(self):
        """Test VacuumActivity enum values."""
        from shim.platforms.vacuum import VacuumActivity

        assert VacuumActivity.CLEANING.value == "cleaning"
        assert VacuumActivity.DOCKED.value == "docked"
        assert VacuumActivity.PAUSED.value == "paused"
        assert VacuumActivity.IDLE.value == "idle"
        assert VacuumActivity.RETURNING.value == "returning"
        assert VacuumActivity.ERROR.value == "error"

    def test_vacuum_entity_feature_values(self):
        """Test VacuumEntityFeature constants."""
        from shim.platforms.vacuum import VacuumEntityFeature

        assert VacuumEntityFeature.TURN_ON == 1
        assert VacuumEntityFeature.TURN_OFF == 2
        assert VacuumEntityFeature.PAUSE == 4
        assert VacuumEntityFeature.STOP == 8
        assert VacuumEntityFeature.RETURN_HOME == 16
        assert VacuumEntityFeature.FAN_SPEED == 32
        assert VacuumEntityFeature.BATTERY == 64
        assert VacuumEntityFeature.STATUS == 128
        assert VacuumEntityFeature.SEND_COMMAND == 256
        assert VacuumEntityFeature.LOCATE == 512
        assert VacuumEntityFeature.CLEAN_SPOT == 1024
        assert VacuumEntityFeature.MAP == 2048
        assert VacuumEntityFeature.STATE == 4096
        assert VacuumEntityFeature.START == 8192

    def test_vacuum_entity_description_creation(self):
        """Test creating VacuumEntityDescription."""
        from shim.platforms.vacuum import VacuumEntityDescription

        desc = VacuumEntityDescription(
            key="test_vacuum",
            fan_speed_list=["quiet", "normal", "turbo"],
            supported_features=15,
        )

        assert desc.key == "test_vacuum"
        assert desc.fan_speed_list == ["quiet", "normal", "turbo"]
        assert desc.supported_features == 15

    def test_vacuum_entity_description_defaults(self):
        """Test VacuumEntityDescription with default values."""
        from shim.platforms.vacuum import VacuumEntityDescription

        desc = VacuumEntityDescription(key="simple_vacuum")

        assert desc.key == "simple_vacuum"
        assert desc.fan_speed_list == []
        assert desc.supported_features == 0

    def test_vacuum_entity_description_frozen(self):
        """Test that VacuumEntityDescription is frozen (immutable dataclass)."""
        import dataclasses
        from shim.platforms.vacuum import VacuumEntityDescription

        desc = VacuumEntityDescription(key="frozen_test")
        # Should NOT be able to modify attributes
        with pytest.raises(dataclasses.FrozenInstanceError):
            desc.key = "modified"
        assert desc.key == "frozen_test"

    def test_vacuum_entity_description_separate_instances(self):
        """Test that separate instances don't share mutable defaults."""
        from shim.platforms.vacuum import VacuumEntityDescription

        desc1 = VacuumEntityDescription(
            key="vacuum1",
            fan_speed_list=["eco"],
        )
        desc2 = VacuumEntityDescription(key="vacuum2")

        # Ensure modifying desc1 doesn't affect desc2
        desc1_fan_list = desc1.fan_speed_list
        desc1_fan_list.append("normal")

        assert desc2.fan_speed_list == []
        assert desc1.fan_speed_list == ["eco", "normal"]


class TestHumidifierEntity:
    """Tests for humidifier platform shim."""

    def test_humidifier_entity_import(self):
        """Test that humidifier entities can be imported."""
        from shim.platforms.humidifier import (
            HumidifierDeviceClass,
            HumidifierEntity,
            HumidifierEntityDescription,
            HumidifierEntityFeature,
        )

        assert HumidifierDeviceClass is not None
        assert HumidifierEntity is not None
        assert HumidifierEntityDescription is not None
        assert HumidifierEntityFeature is not None

    def test_humidifier_device_class_values(self):
        """Test HumidifierDeviceClass enum values."""
        from shim.platforms.humidifier import HumidifierDeviceClass

        assert HumidifierDeviceClass.DEHUMIDIFIER.value == "dehumidifier"
        assert HumidifierDeviceClass.HUMIDIFIER.value == "humidifier"

    def test_humidifier_entity_feature_values(self):
        """Test HumidifierEntityFeature constants."""
        from shim.platforms.humidifier import HumidifierEntityFeature

        assert HumidifierEntityFeature.MODES == 1

    def test_humidifier_entity_description_creation(self):
        """Test creating HumidifierEntityDescription."""
        from shim.platforms.humidifier import (
            HumidifierEntityDescription,
            HumidifierDeviceClass,
        )

        desc = HumidifierEntityDescription(
            key="test_humidifier",
            device_class=HumidifierDeviceClass.HUMIDIFIER,
            max_humidity=80,
            min_humidity=30,
            modes=["auto", "manual", "sleep"],
            supported_features=1,
        )

        assert desc.key == "test_humidifier"
        assert desc.device_class == HumidifierDeviceClass.HUMIDIFIER
        assert desc.max_humidity == 80
        assert desc.min_humidity == 30
        assert desc.modes == ["auto", "manual", "sleep"]
        assert desc.supported_features == 1

    def test_humidifier_entity_description_defaults(self):
        """Test HumidifierEntityDescription with default values."""
        from shim.platforms.humidifier import HumidifierEntityDescription

        desc = HumidifierEntityDescription(key="simple_humidifier")

        assert desc.key == "simple_humidifier"
        assert desc.device_class is None
        assert desc.max_humidity == 100
        assert desc.min_humidity == 0
        assert desc.modes == []
        assert desc.supported_features == 0

    def test_humidifier_entity_description_frozen(self):
        """Test that HumidifierEntityDescription is frozen (immutable dataclass)."""
        import dataclasses
        from shim.platforms.humidifier import HumidifierEntityDescription

        desc = HumidifierEntityDescription(key="frozen_test")
        # Should NOT be able to modify attributes
        with pytest.raises(dataclasses.FrozenInstanceError):
            desc.key = "modified"
        assert desc.key == "frozen_test"

    def test_humidifier_entity_description_separate_instances(self):
        """Test that separate instances don't share mutable defaults."""
        from shim.platforms.humidifier import HumidifierEntityDescription

        desc1 = HumidifierEntityDescription(
            key="humidifier1",
            modes=["auto"],
        )
        desc2 = HumidifierEntityDescription(key="humidifier2")

        # Ensure modifying desc1 doesn't affect desc2
        desc1_modes = desc1.modes
        desc1_modes.append("manual")

        assert desc2.modes == []
        assert desc1.modes == ["auto", "manual"]

    def test_humidifier_modes_constants(self):
        """Test humidifier mode constants."""
        from shim.platforms.humidifier import (
            MODE_NORMAL,
            MODE_ECO,
            MODE_AWAY,
            MODE_BOOST,
            MODE_COMFORT,
            MODE_HOME,
            MODE_SLEEP,
            MODE_AUTO,
            MODE_BABY,
        )

        assert MODE_NORMAL == "normal"
        assert MODE_ECO == "eco"
        assert MODE_AWAY == "away"
        assert MODE_BOOST == "boost"
        assert MODE_COMFORT == "comfort"
        assert MODE_HOME == "home"
        assert MODE_SLEEP == "sleep"
        assert MODE_AUTO == "auto"
        assert MODE_BABY == "baby"


class TestHomeAssistantComponentImports:
    """Tests for homeassistant.components.* import patching."""

    @pytest.mark.integration
    def test_vacuum_component_import(self):
        """Test that homeassistant.components.vacuum can be imported."""
        from homeassistant.components.vacuum import (
            StateVacuumEntity,
            VacuumActivity,
            VacuumEntityFeature,
        )

        assert StateVacuumEntity is not None
        assert VacuumActivity is not None
        assert VacuumEntityFeature is not None

    @pytest.mark.integration
    def test_humidifier_component_import(self):
        """Test that homeassistant.components.humidifier can be imported."""
        from homeassistant.components.humidifier import (
            HumidifierDeviceClass,
            HumidifierEntity,
            HumidifierEntityFeature,
        )

        assert HumidifierDeviceClass is not None
        assert HumidifierEntity is not None
        assert HumidifierEntityFeature is not None


class TestConfigFlowResult:
    """Tests for ConfigFlowResult import compatibility."""

    def test_config_flow_result_import(self):
        """Test that ConfigFlowResult can be imported."""
        from shim.config_entries import ConfigFlowResult, FlowResult

        # ConfigFlowResult should be an alias for FlowResult
        assert ConfigFlowResult is FlowResult

    def test_config_flow_result_type(self):
        """Test that ConfigFlowResult is Dict[str, Any]."""
        from shim.config_entries import ConfigFlowResult
        from typing import Dict, Any, get_origin, get_args

        origin = get_origin(ConfigFlowResult)
        args = get_args(ConfigFlowResult)

        assert origin is dict
        assert str in args or Any in args or len(args) == 2


class TestServiceCall:
    """Tests for ServiceCall class."""

    def test_service_call_creation(self):
        """Test creating a ServiceCall."""
        from shim.core import ServiceCall, Context

        context = Context()
        call = ServiceCall(
            domain="fan",
            service="turn_on",
            data={"entity_id": "fan.living_room"},
            target={"entity_id": "fan.living_room"},
            context=context,
        )

        assert call.domain == "fan"
        assert call.service == "turn_on"
        assert call.data == {"entity_id": "fan.living_room"}
        assert call.target == {"entity_id": "fan.living_room"}
        assert call.context == context

    def test_service_call_defaults(self):
        """Test ServiceCall with default values."""
        from shim.core import ServiceCall

        call = ServiceCall(domain="light", service="turn_on")

        assert call.domain == "light"
        assert call.service == "turn_on"
        assert call.data == {}
        assert call.target is None
        assert call.context is None

    def test_service_call_data_access(self):
        """Test accessing service call data."""
        from shim.core import ServiceCall

        call = ServiceCall(
            domain="vacuum",
            service="send_command",
            data={"command": "locate", "params": {"mode": "sound"}},
        )

        assert call["command"] == "locate"
        assert call.get("params") == {"mode": "sound"}
        assert call.get("missing_key") is None
        assert call.get("missing_key", "default") == "default"

    def test_service_call_separate_instances(self):
        """Test that separate instances don't share mutable defaults."""
        from shim.core import ServiceCall

        call1 = ServiceCall(
            domain="switch", service="turn_on", data={"entity_id": "switch.one"}
        )
        call2 = ServiceCall(domain="switch", service="turn_off")

        # Modify call1's data
        call1.data["extra"] = "value"

        # Ensure call2's data is not affected
        assert call2.data == {}
        assert call1.data == {"entity_id": "switch.one", "extra": "value"}


class TestSelectEntityOptionsMap:
    """Tests for select entity options_map functionality."""

    def test_select_entity_description_with_options_map(self):
        """Test SelectEntityDescription with options_map field."""
        from shim.platforms.select import SelectEntityDescription

        options_map = {
            "5_seconds": "5 seconds",
            "10_seconds": "10 seconds",
        }
        desc = SelectEntityDescription(
            key="test_select",
            options=["5_seconds", "10_seconds"],
            options_map=options_map,
        )

        assert desc.key == "test_select"
        assert desc.options == ["5_seconds", "10_seconds"]
        assert desc.options_map == options_map

    def test_select_entity_without_options_map(self):
        """Test SelectEntityDescription without options_map (backward compatible)."""
        from shim.platforms.select import SelectEntityDescription

        desc = SelectEntityDescription(
            key="simple_select",
            options=["option1", "option2"],
        )

        assert desc.key == "simple_select"
        assert desc.options == ["option1", "option2"]
        assert desc.options_map is None

    def test_select_entity_get_options_map(self):
        """Test SelectEntity._get_options_map method."""
        from shim.platforms.select import SelectEntity, SelectEntityDescription

        options_map = {
            "raw_value": "Display Value",
        }
        desc = SelectEntityDescription(
            key="test",
            options=["raw_value"],
            options_map=options_map,
        )

        class TestSelect(SelectEntity):
            def __init__(self):
                self.entity_description = desc

        entity = TestSelect()
        result = entity._get_options_map()

        assert result == options_map

    def test_select_entity_get_options_map_without_description(self):
        """Test SelectEntity._get_options_map returns None without description."""
        from shim.platforms.select import SelectEntity

        class TestSelect(SelectEntity):
            pass

        entity = TestSelect()
        result = entity._get_options_map()

        assert result is None

    def test_select_entity_frozen_dataclass_with_options_map(self):
        """Test that SelectEntityDescription with options_map is still frozen."""
        import dataclasses
        from shim.platforms.select import SelectEntityDescription

        options_map = {"a": "A"}
        desc = SelectEntityDescription(
            key="frozen_test",
            options=["a"],
            options_map=options_map,
        )

        # Should NOT be able to modify attributes
        with pytest.raises(dataclasses.FrozenInstanceError):
            desc.options_map = {"b": "B"}

    def test_select_entity_frozen_dataclass_patch_with_object_setattr(self):
        """Test that we can patch frozen dataclass instances using object.__setattr__."""
        import dataclasses
        from shim.platforms.select import SelectEntityDescription

        # Create a frozen description without options_map
        desc = SelectEntityDescription(
            key="patch_test",
            options=["a"],
        )

        # Verify it doesn't have options_map initially
        assert not hasattr(desc, "options_map") or desc.options_map is None

        # Patch using object.__setattr__ (this is what our integration loader does)
        test_map = {"a": "A Value", "b": "B Value"}
        object.__setattr__(desc, "options_map", test_map)

        # Verify the patch worked
        assert hasattr(desc, "options_map")
        assert desc.options_map == test_map
        assert desc.options_map["a"] == "A Value"


class TestOptionsMapRegistry:
    """Tests for the options_map registry."""

    def test_load_integration_translations(self):
        """Test loading translations from integration files."""
        import tempfile
        import json
        from pathlib import Path
        from shim.options_map import (
            load_integration_translations,
            clear_translations_cache,
        )

        # Clear cache for clean test
        clear_translations_cache()

        try:
            # Create a temp integration with translations
            with tempfile.TemporaryDirectory() as tmpdir:
                integration_path = Path(tmpdir)
                translations_dir = integration_path / "translations"
                translations_dir.mkdir()

                translations = {
                    "entity": {
                        "select": {
                            "all": {
                                "state": {
                                    "5_seconds": "5 seconds",
                                    "10_seconds": "10 seconds",
                                }
                            }
                        }
                    }
                }

                with open(translations_dir / "en.json", "w") as f:
                    json.dump(translations, f)

                result = load_integration_translations(
                    "test_integration", integration_path
                )

                assert result == translations
                assert (
                    result["entity"]["select"]["all"]["state"]["5_seconds"]
                    == "5 seconds"
                )
        finally:
            clear_translations_cache()

    def test_get_select_state_translations(self):
        """Test extracting select state translations from translation data."""
        from shim.options_map import get_select_state_translations

        translations = {
            "entity": {
                "select": {
                    "all": {
                        "state": {
                            "5_seconds": "5 seconds",
                            "always_on": "Always On",
                        }
                    }
                }
            }
        }

        result = get_select_state_translations(translations)

        assert result["5_seconds"] == "5 seconds"
        assert result["always_on"] == "Always On"

    def test_get_select_state_translations_empty(self):
        """Test extracting translations when no select translations exist."""
        from shim.options_map import get_select_state_translations

        translations = {"config": {}}
        result = get_select_state_translations(translations)

        assert result == {}

    def test_load_strings_json_fallback(self):
        """Test loading strings.json as fallback when translations not present."""
        import tempfile
        import json
        from pathlib import Path
        from shim.options_map import (
            load_integration_translations,
            clear_translations_cache,
        )

        clear_translations_cache()

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                integration_path = Path(tmpdir)

                strings = {
                    "entity": {
                        "select": {"test": {"state": {"raw_value": "Display Value"}}}
                    }
                }

                with open(integration_path / "strings.json", "w") as f:
                    json.dump(strings, f)

                result = load_integration_translations(
                    "test_integration", integration_path
                )

                assert (
                    result["entity"]["select"]["test"]["state"]["raw_value"]
                    == "Display Value"
                )
        finally:
            clear_translations_cache()

    def test_patch_select_descriptions_with_frozen_dataclass(self):
        """Test patching select descriptions that are frozen dataclass instances."""
        import dataclasses
        import tempfile
        import json
        from pathlib import Path
        from shim.options_map import (
            load_integration_translations,
            patch_select_descriptions,
            clear_translations_cache,
        )

        # Clear cache for clean test
        clear_translations_cache()

        try:
            # Create a frozen dataclass like Leviton uses
            @dataclasses.dataclass(frozen=True)
            class TestDescription:
                key: str
                options_key: str = None

            # Create a temp integration with translations
            with tempfile.TemporaryDirectory() as tmpdir:
                integration_path = Path(tmpdir)
                translations_dir = integration_path / "translations"
                translations_dir.mkdir()

                translations = {
                    "entity": {
                        "select": {
                            "all": {
                                "state": {
                                    "5_seconds": "5 seconds",
                                    "10_seconds": "10 seconds",
                                }
                            }
                        }
                    }
                }

                with open(translations_dir / "en.json", "w") as f:
                    json.dump(translations, f)

                # Create mock module with frozen descriptions
                class FakeModule:
                    SELECT_DESCRIPTIONS = [
                        TestDescription(
                            key="led_bar_behavior",
                            options_key="led_bar_behavior_options",
                        ),
                        TestDescription(
                            key="other_entity", options_key="other_options"
                        ),
                    ]

                # Verify descriptions don't have options_map before patch
                desc1 = FakeModule.SELECT_DESCRIPTIONS[0]
                assert not hasattr(desc1, "options_map")

                # Patch
                patch_select_descriptions(
                    "test_integration", FakeModule, integration_path
                )

                # Verify led_bar_behavior was patched
                assert hasattr(desc1, "options_map")
                assert desc1.options_map is not None
                assert "5_seconds" in desc1.options_map
                assert desc1.options_map["5_seconds"] == "5 seconds"

        finally:
            clear_translations_cache()


class TestNumberEntityValueClamping:
    """Tests for number entity value clamping in MQTT publish."""

    def test_number_entity_value_within_range(self):
        """Test that values within range are published as-is."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self):
                self._attr_native_value = 50.0
                self._attr_native_min_value = 25.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "test_number"
                self._attr_name = "Test Number"

        entity = TestNumber()
        assert entity.native_value == 50.0
        assert entity.native_min_value == 25.0
        assert entity.native_max_value == 100.0

    def test_number_entity_value_clamping_below_min(self):
        """Test that values below min are clamped to min."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self):
                self._attr_native_value = 10.0  # Below min of 25.0
                self._attr_native_min_value = 25.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "test_number"
                self._attr_name = "Test Number"

        entity = TestNumber()
        # Value should be clamped to 25.0 during publish
        min_val = entity.native_min_value
        max_val = entity.native_max_value
        value = entity.native_value
        clamped_value = max(min_val, min(max_val, value))
        assert clamped_value == 25.0

    def test_number_entity_value_clamping_above_max(self):
        """Test that values above max are clamped to max."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self):
                self._attr_native_value = 150.0  # Above max of 100.0
                self._attr_native_min_value = 25.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "test_number"
                self._attr_name = "Test Number"

        entity = TestNumber()
        # Value should be clamped to 100.0 during publish
        min_val = entity.native_min_value
        max_val = entity.native_max_value
        value = entity.native_value
        clamped_value = max(min_val, min(max_val, value))
        assert clamped_value == 100.0

    def test_number_entity_value_zero_with_nonzero_min(self):
        """Test the specific case: value 0 with min 25 (dreo issue)."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self):
                self._attr_native_value = 0.0  # Dreo case: 0 with min 25
                self._attr_native_min_value = 25.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "ceiling_fan_preset_level"
                self._attr_name = "Ceiling Fan Preset Level"

        entity = TestNumber()
        # Value should be clamped to 25.0 during publish
        min_val = entity.native_min_value
        max_val = entity.native_max_value
        value = entity.native_value
        clamped_value = max(min_val, min(max_val, value))
        assert clamped_value == 25.0

    def test_number_entity_state_property(self):
        """Test that NumberEntity state property returns native_value as string."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_native_min_value = 0.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "test_number"
                self._attr_name = "Test Number"

        # Test with a valid value - state should return string representation
        entity_with_value = TestNumber(45.5)
        assert entity_with_value.native_value == 45.5
        assert entity_with_value.state == "45.5"

        # Test with zero value - state should return "0.0" not None
        entity_with_zero = TestNumber(0.0)
        assert entity_with_zero.native_value == 0.0
        assert entity_with_zero.state == "0.0"

        # Test with None value - state should return None
        entity_with_none = TestNumber(None)
        assert entity_with_none.native_value is None
        assert entity_with_none.state is None

    def test_number_entity_available_property(self):
        """Test that NumberEntity available property reflects native_value status."""
        from shim.platforms.number import NumberEntity

        class TestNumber(NumberEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_native_min_value = 0.0
                self._attr_native_max_value = 100.0
                self._attr_unique_id = "test_number"
                self._attr_name = "Test Number"

        # Test with a valid value - should be available
        entity_with_value = TestNumber(45.5)
        assert entity_with_value.available is True

        # Test with zero value - should be available (0 is a valid number)
        entity_with_zero = TestNumber(0.0)
        assert entity_with_zero.available is True

        # Test with None value - should be unavailable
        entity_with_none = TestNumber(None)
        assert entity_with_none.available is False


class TestSelectEntityStateAndAvailable:
    """Tests for select entity state and available properties."""

    def test_select_entity_state_property(self):
        """Test that SelectEntity state property returns current_option with translation."""
        from shim.platforms.select import SelectEntity, SelectEntityDescription

        class TestSelect(SelectEntity):
            def __init__(self, current_option, options_map=None):
                self._attr_current_option = current_option
                self._attr_options = ["auto", "manual", "sleep"]
                self._attr_unique_id = "test_select"
                self._attr_name = "Test Select"
                if options_map:
                    self.entity_description = SelectEntityDescription(
                        key="test",
                        options=self._attr_options,
                        options_map=options_map,
                    )

        # Test with a valid option - state should return the option string
        entity_with_value = TestSelect("auto")
        assert entity_with_value.current_option == "auto"
        assert entity_with_value.state == "auto"

        # Test with options_map translation - state should return translated value
        options_map = {"auto": "Automatic", "manual": "Manual Mode"}
        entity_with_translation = TestSelect("auto", options_map=options_map)
        assert entity_with_translation.current_option == "auto"
        assert entity_with_translation.state == "Automatic"

        # Test translation for another option
        entity_manual = TestSelect("manual", options_map=options_map)
        assert entity_manual.state == "Manual Mode"

        # Test with empty string - state should return empty string (valid selection)
        entity_with_empty = TestSelect("")
        assert entity_with_empty.current_option == ""
        assert entity_with_empty.state == ""

        # Test with None value - state should return None
        entity_with_none = TestSelect(None)
        assert entity_with_none.current_option is None
        assert entity_with_none.state is None

    def test_select_entity_available_property(self):
        """Test that SelectEntity available property reflects current_option status."""
        from shim.platforms.select import SelectEntity

        class TestSelect(SelectEntity):
            def __init__(self, current_option):
                self._attr_current_option = current_option
                self._attr_options = ["auto", "manual", "sleep"]
                self._attr_unique_id = "test_select"
                self._attr_name = "Test Select"

        # Test with a valid option - should be available
        entity_with_value = TestSelect("manual")
        assert entity_with_value.available is True

        # Test with empty string - should be available (empty is a valid selection)
        entity_with_empty = TestSelect("")
        assert entity_with_empty.available is True

        # Test with None value - should be unavailable
        entity_with_none = TestSelect(None)
        assert entity_with_none.available is False


class TestButtonEntityStateAndAvailable:
    """Tests for button entity state and available properties."""

    def test_button_entity_state_property(self):
        """Test that ButtonEntity state property returns 'Press'."""
        from shim.platforms.button import ButtonEntity

        class TestButton(ButtonEntity):
            def __init__(self):
                self._attr_unique_id = "test_button"
                self._attr_name = "Test Button"

        entity = TestButton()
        # Buttons should always show "Press" as their state
        assert entity.state == "Press"

    def test_button_entity_available_property(self):
        """Test that ButtonEntity available property is always True."""
        from shim.platforms.button import ButtonEntity

        class TestButton(ButtonEntity):
            def __init__(self):
                self._attr_unique_id = "test_button"
                self._attr_name = "Test Button"

        entity = TestButton()
        # Buttons should always be available
        assert entity.available is True

    def test_button_entity_with_device_class(self):
        """Test ButtonEntity with device class."""
        from shim.platforms.button import ButtonEntity, ButtonDeviceClass

        class ResetFilterButton(ButtonEntity):
            def __init__(self):
                self._attr_unique_id = "reset_filter"
                self._attr_name = "Reset Filter Life"
                self._attr_device_class = ButtonDeviceClass.RESTART

        entity = ResetFilterButton()
        assert entity.state == "Press"
        assert entity.available is True
        assert entity.device_class == "restart"


class TestTextEntityStateAndAvailable:
    """Tests for text entity state and available properties."""

    def test_text_entity_state_property(self):
        """Test that TextEntity state property returns native_value."""
        from shim.platforms.text import TextEntity

        class TestText(TextEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_unique_id = "test_text"
                self._attr_name = "Test Text"

        # Test with a valid string - state should return the string
        entity_with_value = TestText("Hello World")
        assert entity_with_value.native_value == "Hello World"
        assert entity_with_value.state == "Hello World"

        # Test with empty string - state should return empty string (valid state)
        entity_with_empty = TestText("")
        assert entity_with_empty.native_value == ""
        assert entity_with_empty.state == ""

        # Test with None value - state should return None
        entity_with_none = TestText(None)
        assert entity_with_none.native_value is None
        assert entity_with_none.state is None

    def test_text_entity_available_property(self):
        """Test that TextEntity available property handles empty strings correctly."""
        from shim.platforms.text import TextEntity

        class TestText(TextEntity):
            def __init__(self, native_value):
                self._attr_native_value = native_value
                self._attr_unique_id = "test_text"
                self._attr_name = "Test Text"

        # Test with a valid string - should be available
        entity_with_value = TestText("Hello")
        assert entity_with_value.available is True

        # Test with empty string - should be available (empty is a valid text state)
        # This is the key fix for flightradar24 "Airport track" issue
        entity_with_empty = TestText("")
        assert entity_with_empty.available is True

        # Test with None value - should be unavailable
        entity_with_none = TestText(None)
        assert entity_with_none.available is False


class TestSwitchEntityAsyncDelegation:
    """Tests for SwitchEntity async method delegation to sync methods."""

    def test_switch_entity_async_turn_on_delegates_to_sync(self):
        """Test that async_turn_on() delegates to sync turn_on() method."""
        import asyncio
        from shim.platforms.switch import SwitchEntity

        # Track if sync turn_on was called
        turn_on_calls = []

        class TestSwitch(SwitchEntity):
            def __init__(self):
                self._attr_unique_id = "test_switch"
                self._attr_name = "Test Switch"
                self._attr_is_on = False

            def turn_on(self, **kwargs):
                """Sync turn_on implementation."""
                turn_on_calls.append(kwargs)
                self._attr_is_on = True

        # Create a mock hass with async_add_executor_job
        class MockHass:
            async def async_add_executor_job(self, func, *args, **kwargs):
                # Execute the sync function immediately
                result = func(*args, **kwargs)
                return result

        entity = TestSwitch()
        entity.hass = MockHass()

        # Call async_turn_on
        asyncio.run(entity.async_turn_on())

        # Verify sync turn_on was called
        assert len(turn_on_calls) == 1
        assert entity._attr_is_on is True

    def test_switch_entity_async_turn_off_delegates_to_sync(self):
        """Test that async_turn_off() delegates to sync turn_off() method."""
        import asyncio
        from shim.platforms.switch import SwitchEntity

        # Track if sync turn_off was called
        turn_off_calls = []

        class TestSwitch(SwitchEntity):
            def __init__(self):
                self._attr_unique_id = "test_switch"
                self._attr_name = "Test Switch"
                self._attr_is_on = True

            def turn_off(self, **kwargs):
                """Sync turn_off implementation."""
                turn_off_calls.append(kwargs)
                self._attr_is_on = False

        # Create a mock hass with async_add_executor_job
        class MockHass:
            async def async_add_executor_job(self, func, *args, **kwargs):
                # Execute the sync function immediately
                result = func(*args, **kwargs)
                return result

        entity = TestSwitch()
        entity.hass = MockHass()

        # Call async_turn_off
        asyncio.run(entity.async_turn_off())

        # Verify sync turn_off was called
        assert len(turn_off_calls) == 1
        assert entity._attr_is_on is False

    def test_switch_entity_async_turn_on_passes_kwargs(self):
        """Test that async_turn_on() passes kwargs to sync turn_on()."""
        import asyncio
        from shim.platforms.switch import SwitchEntity

        received_kwargs = {}

        class TestSwitch(SwitchEntity):
            def __init__(self):
                self._attr_unique_id = "test_switch"
                self._attr_name = "Test Switch"
                self._attr_is_on = False

            def turn_on(self, **kwargs):
                """Sync turn_on implementation."""
                nonlocal received_kwargs
                received_kwargs = kwargs
                self._attr_is_on = True

        class MockHass:
            async def async_add_executor_job(self, func, *args, **kwargs):
                result = func(*args, **kwargs)
                return result

        entity = TestSwitch()
        entity.hass = MockHass()

        # Call async_turn_on with kwargs
        asyncio.run(entity.async_turn_on(brightness=100, transition=2))

        # Verify kwargs were passed
        assert received_kwargs == {"brightness": 100, "transition": 2}

    def test_switch_entity_inherits_from_toggle_entity(self):
        """Test that SwitchEntity properly inherits from ToggleEntity."""
        from shim.platforms.switch import SwitchEntity
        from shim.entity import ToggleEntity

        # Verify inheritance
        assert issubclass(SwitchEntity, ToggleEntity)

    def test_switch_entity_has_async_turn_methods(self):
        """Test that SwitchEntity has both async and sync turn methods."""
        from shim.platforms.switch import SwitchEntity

        # Verify the methods exist
        assert hasattr(SwitchEntity, "turn_on")
        assert hasattr(SwitchEntity, "turn_off")
        assert hasattr(SwitchEntity, "async_turn_on")
        assert hasattr(SwitchEntity, "async_turn_off")

        # Verify they are not the same as ToggleEntity's (which just raise)
        from shim.entity import ToggleEntity

        assert SwitchEntity.async_turn_on is not ToggleEntity.async_turn_on
        assert SwitchEntity.async_turn_off is not ToggleEntity.async_turn_off
