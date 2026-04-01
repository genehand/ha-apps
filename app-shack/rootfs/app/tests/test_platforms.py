"""Tests for shim platform additions."""

import pytest
from dataclasses import FrozenInstanceError


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

    def test_vacuum_entity_description_immutable(self):
        """Test that VacuumEntityDescription is immutable (frozen dataclass)."""
        from shim.platforms.vacuum import VacuumEntityDescription

        desc = VacuumEntityDescription(key="immutable_test")

        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"

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

    def test_humidifier_entity_description_immutable(self):
        """Test that HumidifierEntityDescription is immutable."""
        from shim.platforms.humidifier import HumidifierEntityDescription

        desc = HumidifierEntityDescription(key="immutable_test")

        with pytest.raises(FrozenInstanceError):
            desc.key = "modified"

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
