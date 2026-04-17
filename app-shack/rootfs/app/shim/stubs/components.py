"""Home Assistant components stub modules.

Provides alarm_control_panel, cover, persistent_notification, diagnostics,
zeroconf, cloud, webhook, mqtt, image, number, scene, and various const modules.
"""

import sys
import types
from enum import Enum

import voluptuous as vol

from ..logging import get_logger

_LOGGER = get_logger(__name__)


class AlarmControlPanelState:
    """Alarm control panel state constants."""

    DISARMED = "disarmed"
    ARMED_HOME = "armed_home"
    ARMED_AWAY = "armed_away"
    ARMED_NIGHT = "armed_night"
    ARMED_VACATION = "armed_vacation"
    ARMED_CUSTOM_BYPASS = "armed_custom_bypass"
    PENDING = "pending"
    ARMING = "arming"
    DISARMING = "disarming"
    TRIGGERED = "triggered"


class CodeFormat:
    """Code format constants."""

    TEXT = "text"
    NUMBER = "number"


class CoverDeviceClass:
    """Cover device class constants."""

    AWNING = "awning"
    BLIND = "blind"
    CURTAIN = "curtain"
    DAMPER = "damper"
    DOOR = "door"
    GARAGE = "garage"
    GATE = "gate"
    SHADE = "shade"
    SHUTTER = "shutter"
    WINDOW = "window"


class NumberMode(Enum):
    """Number entity display modes."""

    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


def create_components_stubs(hass, homeassistant, platforms):
    """Create all homeassistant.components.* stub modules."""

    # Attach existing platform modules
    homeassistant.components.fan = platforms.fan
    homeassistant.components.sensor = platforms.sensor
    homeassistant.components.switch = platforms.switch
    homeassistant.components.light = platforms.light
    homeassistant.components.climate = platforms.climate
    homeassistant.components.binary_sensor = platforms.binary_sensor
    homeassistant.components.update = platforms.update
    homeassistant.components.select = platforms.select
    homeassistant.components.button = platforms.button
    homeassistant.components.device_tracker = platforms.device_tracker
    homeassistant.components.text = platforms.text
    homeassistant.components.vacuum = platforms.vacuum
    homeassistant.components.humidifier = platforms.humidifier
    homeassistant.components.number = platforms.number
    homeassistant.components.lock = platforms.lock
    homeassistant.components.water_heater = platforms.water_heater
    homeassistant.components.camera = platforms.camera
    homeassistant.components.siren = platforms.siren
    homeassistant.components.remote = platforms.remote

    # alarm_control_panel
    alarm_control_panel = types.ModuleType("homeassistant.components.alarm_control_panel")
    alarm_control_panel.AlarmControlPanelState = AlarmControlPanelState
    alarm_control_panel.AlarmControlPanelEntity = type("AlarmControlPanelEntity", (), {})
    alarm_control_panel.AlarmControlPanelEntityDescription = type("AlarmControlPanelEntityDescription", (), {})
    alarm_control_panel.AlarmControlPanelEntityFeature = type("AlarmControlPanelEntityFeature", (), {})
    alarm_control_panel.AlarmControlPanelEntityFeature.ARM_HOME = 1
    alarm_control_panel.AlarmControlPanelEntityFeature.ARM_AWAY = 2
    alarm_control_panel.AlarmControlPanelEntityFeature.ARM_NIGHT = 4
    alarm_control_panel.AlarmControlPanelEntityFeature.TRIGGER = 8
    alarm_control_panel.CodeFormat = CodeFormat
    alarm_control_panel.CodeFormat.TEXT = "text"
    alarm_control_panel.CodeFormat.NUMBER = "number"
    alarm_control_panel.DOMAIN = "alarm_control_panel"
    homeassistant.components.alarm_control_panel = alarm_control_panel
    sys.modules["homeassistant.components.alarm_control_panel"] = alarm_control_panel

    # cover
    cover = types.ModuleType("homeassistant.components.cover")
    cover.DOMAIN = "cover"
    cover.CoverDeviceClass = CoverDeviceClass
    cover.DEVICE_CLASSES_SCHEMA = vol.In([
        "awning", "blind", "curtain", "damper", "door", "garage",
        "gate", "shade", "shutter", "window",
    ])
    cover.CoverEntity = type("CoverEntity", (), {})
    cover.CoverEntityDescription = type("CoverEntityDescription", (), {})
    cover.CoverEntityFeature = type("CoverEntityFeature", (), {})
    cover.CoverEntityFeature.OPEN = 1
    cover.CoverEntityFeature.CLOSE = 2
    cover.CoverEntityFeature.SET_POSITION = 4
    cover.CoverEntityFeature.STOP = 8
    cover.CoverEntityFeature.OPEN_TILT = 16
    cover.CoverEntityFeature.CLOSE_TILT = 32
    cover.CoverEntityFeature.STOP_TILT = 64
    cover.CoverEntityFeature.SET_TILT_POSITION = 128
    cover.CoverState = type("CoverState", (), {})
    cover.CoverState.OPEN = "open"
    cover.CoverState.CLOSED = "closed"
    cover.CoverState.OPENING = "opening"
    cover.CoverState.CLOSING = "closing"
    cover.CoverEntity.CoverState = cover.CoverState
    cover.ATTR_CURRENT_POSITION = "current_position"
    cover.ATTR_CURRENT_TILT_POSITION = "current_tilt_position"
    cover.ATTR_POSITION = "position"
    cover.ATTR_TILT_POSITION = "tilt_position"
    homeassistant.components.cover = cover
    sys.modules["homeassistant.components.cover"] = cover

    # mjpeg.camera
    mjpeg_camera = types.ModuleType("homeassistant.components.mjpeg.camera")
    # MjpegCamera is defined in platforms.camera and handles device_info kwarg
    mjpeg_camera.MjpegCamera = platforms.camera.MjpegCamera
    homeassistant.components.mjpeg = types.ModuleType("homeassistant.components.mjpeg")
    homeassistant.components.mjpeg.camera = mjpeg_camera
    sys.modules["homeassistant.components.mjpeg"] = homeassistant.components.mjpeg
    sys.modules["homeassistant.components.mjpeg.camera"] = mjpeg_camera

    # sensor.const
    sensor_const = types.ModuleType("homeassistant.components.sensor.const")
    sensor_const.CONF_STATE_CLASS = "state_class"
    sensor_const.ATTR_LAST_RESET = "last_reset"
    sensor_const.ATTR_STATE_CLASS = "state_class"
    sensor_const.SensorDeviceClass = platforms.sensor.SensorDeviceClass
    sensor_const.SensorStateClass = platforms.sensor.SensorStateClass
    homeassistant.components.sensor.const = sensor_const
    sys.modules["homeassistant.components.sensor.const"] = sensor_const

    # climate.const
    climate_const = types.ModuleType("homeassistant.components.climate.const")
    climate_const.DOMAIN = platforms.climate.DOMAIN
    climate_const.DEFAULT_MAX_TEMP = platforms.climate.DEFAULT_MAX_TEMP
    climate_const.DEFAULT_MIN_TEMP = platforms.climate.DEFAULT_MIN_TEMP
    climate_const.HVACMode = platforms.climate.HVACMode
    climate_const.HVACAction = platforms.climate.HVACAction
    climate_const.ClimateEntityFeature = platforms.climate.ClimateEntityFeature
    climate_const.PRESET_AWAY = "away"
    climate_const.PRESET_ECO = "eco"
    climate_const.PRESET_HOME = "home"
    climate_const.PRESET_NONE = "none"
    climate_const.PRESET_BOOST = "boost"
    climate_const.PRESET_COMFORT = "comfort"
    climate_const.PRESET_SLEEP = "sleep"
    climate_const.PRESET_ACTIVITY = "activity"
    homeassistant.components.climate.const = climate_const
    sys.modules["homeassistant.components.climate.const"] = climate_const

    # water_heater.const
    water_heater_const = types.ModuleType("homeassistant.components.water_heater.const")
    water_heater_const.DOMAIN = platforms.water_heater.DOMAIN
    water_heater_const.DEFAULT_MAX_TEMP = platforms.water_heater.DEFAULT_MAX_TEMP
    water_heater_const.DEFAULT_MIN_TEMP = platforms.water_heater.DEFAULT_MIN_TEMP
    water_heater_const.STATE_ECO = platforms.water_heater.STATE_ECO
    water_heater_const.STATE_ELECTRIC = platforms.water_heater.STATE_ELECTRIC
    water_heater_const.STATE_PERFORMANCE = platforms.water_heater.STATE_PERFORMANCE
    water_heater_const.STATE_HIGH_DEMAND = platforms.water_heater.STATE_HIGH_DEMAND
    water_heater_const.STATE_HEAT_PUMP = platforms.water_heater.STATE_HEAT_PUMP
    water_heater_const.STATE_GAS = platforms.water_heater.STATE_GAS
    water_heater_const.STATE_OFF = platforms.water_heater.STATE_OFF
    water_heater_const.STATE_ON = platforms.water_heater.STATE_ON
    homeassistant.components.water_heater.const = water_heater_const
    sys.modules["homeassistant.components.water_heater.const"] = water_heater_const

    # humidifier.const
    humidifier_const = types.ModuleType("homeassistant.components.humidifier.const")
    humidifier_const.DOMAIN = platforms.humidifier.DOMAIN
    humidifier_const.DEFAULT_MAX_HUMIDITY = platforms.humidifier.DEFAULT_MAX_HUMIDITY
    humidifier_const.DEFAULT_MIN_HUMIDITY = platforms.humidifier.DEFAULT_MIN_HUMIDITY
    humidifier_const.ATTR_MAX_HUMIDITY = platforms.humidifier.ATTR_MAX_HUMIDITY
    humidifier_const.ATTR_MIN_HUMIDITY = platforms.humidifier.ATTR_MIN_HUMIDITY
    humidifier_const.HumidifierDeviceClass = platforms.humidifier.HumidifierDeviceClass
    humidifier_const.DEVICE_CLASSES_SCHEMA = platforms.humidifier.DEVICE_CLASSES_SCHEMA
    homeassistant.components.humidifier.const = humidifier_const
    sys.modules["homeassistant.components.humidifier.const"] = humidifier_const

    # light.const
    light_const = types.ModuleType("homeassistant.components.light.const")
    light_const.DOMAIN = platforms.light.DOMAIN
    light_const.ColorMode = platforms.light.ColorMode
    light_const.ATTR_BRIGHTNESS = platforms.light.ATTR_BRIGHTNESS
    light_const.ATTR_COLOR_TEMP = platforms.light.ATTR_COLOR_TEMP
    light_const.ATTR_EFFECT = platforms.light.ATTR_EFFECT
    light_const.ATTR_HS_COLOR = platforms.light.ATTR_HS_COLOR
    light_const.ATTR_RGB_COLOR = platforms.light.ATTR_RGB_COLOR
    light_const.ATTR_RGBW_COLOR = platforms.light.ATTR_RGBW_COLOR
    light_const.ATTR_RGBWW_COLOR = platforms.light.ATTR_RGBWW_COLOR
    light_const.ATTR_XY_COLOR = platforms.light.ATTR_XY_COLOR
    light_const.ATTR_WHITE_VALUE = platforms.light.ATTR_WHITE_VALUE
    light_const.ATTR_TRANSITION = platforms.light.ATTR_TRANSITION
    light_const.ATTR_FLASH = platforms.light.ATTR_FLASH
    light_const.SUPPORT_BRIGHTNESS = platforms.light.SUPPORT_BRIGHTNESS
    light_const.SUPPORT_COLOR = platforms.light.SUPPORT_COLOR
    light_const.SUPPORT_COLOR_TEMP = platforms.light.SUPPORT_COLOR_TEMP
    light_const.SUPPORT_EFFECT = platforms.light.SUPPORT_EFFECT
    light_const.SUPPORT_FLASH = platforms.light.SUPPORT_FLASH
    light_const.SUPPORT_TRANSITION = platforms.light.SUPPORT_TRANSITION
    light_const.SUPPORT_WHITE_VALUE = platforms.light.SUPPORT_WHITE_VALUE
    homeassistant.components.light.const = light_const
    sys.modules["homeassistant.components.light.const"] = light_const

    # persistent_notification
    persistent_notification = types.ModuleType("homeassistant.components.persistent_notification")
    persistent_notification.DOMAIN = "persistent_notification"
    persistent_notification.async_create = lambda hass, message, title=None, notification_id=None: None
    persistent_notification.async_dismiss = lambda hass, notification_id: None
    homeassistant.components.persistent_notification = persistent_notification
    sys.modules["homeassistant.components.persistent_notification"] = persistent_notification

    # diagnostics
    diagnostics = types.ModuleType("homeassistant.components.diagnostics")
    diagnostics.DOMAIN = "diagnostics"
    diagnostics.async_get_config_entry_diagnostics = lambda hass, config_entry: {}
    diagnostics.async_get_device_diagnostics = lambda hass, config_entry, device: {}
    diagnostics.REDACTED = "**REDACTED**"
    homeassistant.components.diagnostics = diagnostics
    sys.modules["homeassistant.components.diagnostics"] = diagnostics

    # Register all platform modules in sys.modules
    sys.modules["homeassistant.components.fan"] = platforms.fan
    sys.modules["homeassistant.components.sensor"] = platforms.sensor
    sys.modules["homeassistant.components.switch"] = platforms.switch
    sys.modules["homeassistant.components.light"] = platforms.light
    sys.modules["homeassistant.components.climate"] = platforms.climate
    sys.modules["homeassistant.components.binary_sensor"] = platforms.binary_sensor
    sys.modules["homeassistant.components.update"] = platforms.update
    sys.modules["homeassistant.components.select"] = platforms.select
    sys.modules["homeassistant.components.button"] = platforms.button
    sys.modules["homeassistant.components.device_tracker"] = platforms.device_tracker
    sys.modules["homeassistant.components.device_tracker.config_entry"] = platforms.device_tracker.config_entry
    sys.modules["homeassistant.components.device_tracker.const"] = platforms.device_tracker.const
    sys.modules["homeassistant.components.text"] = platforms.text
    sys.modules["homeassistant.components.vacuum"] = platforms.vacuum
    sys.modules["homeassistant.components.humidifier"] = platforms.humidifier
    sys.modules["homeassistant.components.number"] = platforms.number
    sys.modules["homeassistant.components.lock"] = platforms.lock
    sys.modules["homeassistant.components.water_heater"] = platforms.water_heater
    sys.modules["homeassistant.components.camera"] = platforms.camera
    sys.modules["homeassistant.components.siren"] = platforms.siren
    sys.modules["homeassistant.components.remote"] = platforms.remote

    _LOGGER.debug("Platform modules patched")

    return homeassistant


def create_additional_stubs(hass, homeassistant):
    """Create additional stub modules for HA dependencies."""

    # zeroconf
    zeroconf_stub = types.ModuleType("homeassistant.components.zeroconf")
    zeroconf_stub.DOMAIN = "zeroconf"
    zeroconf_stub.Zeroconf = lambda *args, **kwargs: None
    zeroconf_stub.async_get_instance = lambda *args, **kwargs: None
    zeroconf_stub.HaZeroconf = lambda *args, **kwargs: None
    homeassistant.components.zeroconf = zeroconf_stub
    sys.modules["homeassistant.components.zeroconf"] = zeroconf_stub

    # cloud
    cloud_stub = types.ModuleType("homeassistant.components.cloud")
    cloud_stub.DOMAIN = "cloud"
    cloud_stub.async_active_subscription = lambda hass: False
    cloud_stub.async_migrate_paypal_agreement = lambda hass: None
    cloud_stub.CloudNotAvailable = type("CloudNotAvailable", (Exception,), {})
    homeassistant.components.cloud = cloud_stub
    sys.modules["homeassistant.components.cloud"] = cloud_stub

    # webhook
    webhook_stub = types.ModuleType("homeassistant.components.webhook")
    webhook_stub.DOMAIN = "webhook"
    webhook_stub.async_register = lambda hass, domain, webhook_id, handler: None
    webhook_stub.async_unregister = lambda hass, webhook_id: None
    webhook_stub.async_generate_id = lambda: "stub_webhook_id"
    webhook_stub.async_generate_path = lambda webhook_id: f"/api/webhook/{webhook_id}"
    homeassistant.components.webhook = webhook_stub
    sys.modules["homeassistant.components.webhook"] = webhook_stub

    # mqtt
    mqtt_stub = types.ModuleType("homeassistant.components.mqtt")
    mqtt_stub.DOMAIN = "mqtt"
    mqtt_stub.PublishPayloadType = type("PublishPayloadType", (), {})
    mqtt_stub.CONF_STATE_TOPIC = "state_topic"
    mqtt_stub.CONF_COMMAND_TOPIC = "command_topic"
    mqtt_stub.CONF_AVAILABILITY_TOPIC = "availability_topic"
    mqtt_stub.CONF_QOS = "qos"
    mqtt_stub.CONF_RETAIN = "retain"
    mqtt_stub.DEFAULT_QOS = 0
    mqtt_stub.DEFAULT_RETAIN = False
    mqtt_stub.MQTT_ERR_SUCCESS = 0

    # ReceiveMessage class
    class ReceiveMessage:
        """MQTT message representation."""

        def __init__(self, topic, payload, qos, retain, timestamp=None):
            self.topic = topic
            self.payload = payload
            self.qos = qos
            self.retain = retain
            self.timestamp = timestamp

    mqtt_stub.ReceiveMessage = ReceiveMessage

    async def async_publish(hass, topic, payload, qos=0, retain=False, encoding="utf-8"):
        """Publish a message to an MQTT topic."""
        return None

    async def async_subscribe(hass, topic, msg_callback, qos=0, encoding="utf-8"):
        """Subscribe to an MQTT topic."""
        def unsub():
            pass
        return unsub

    mqtt_stub.async_publish = async_publish
    mqtt_stub.async_subscribe = async_subscribe
    homeassistant.components.mqtt = mqtt_stub
    sys.modules["homeassistant.components.mqtt"] = mqtt_stub

    # image
    from ..frozen_dataclass_compat import FrozenOrThawed
    from typing import Optional

    image_stub = types.ModuleType("homeassistant.components.image")
    image_stub.DOMAIN = "image"

    class ImageEntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
        """Image entity description."""
        key: str
        name: Optional[str] = None
        icon: Optional[str] = None
        device_class: Optional[str] = None
        entity_category: Optional[str] = None
        entity_registry_enabled_default: bool = True

    image_stub.ImageEntityDescription = ImageEntityDescription
    image_stub.ImageEntity = type("ImageEntity", (), {})
    homeassistant.components.image = image_stub
    sys.modules["homeassistant.components.image"] = image_stub

    # number - extend the existing platforms.number module
    from ..frozen_dataclass_compat import FrozenOrThawed
    from typing import Optional

    number_stub = homeassistant.components.number

    # Create an extended NumberEntityDescription with legacy field names
    class NumberEntityDescription(metaclass=FrozenOrThawed, frozen_or_thawed=True):
        """Number entity description with legacy field support."""
        key: str
        name: Optional[str] = None
        translation_key: Optional[str] = None
        icon: Optional[str] = None
        device_class: Optional[str] = None
        entity_category: Optional[str] = None
        entity_registry_enabled_default: bool = True
        native_unit_of_measurement: Optional[str] = None
        native_max_value: Optional[float] = None
        native_min_value: Optional[float] = None
        native_step: Optional[float] = None
        # Legacy field names for compatibility
        min_value: Optional[float] = None
        max_value: Optional[float] = None
        step: Optional[float] = None

    number_stub.NumberEntityDescription = NumberEntityDescription

    # Add NumberDeviceClass enum-like class with all device class constants
    number_stub.NumberDeviceClass = type("NumberDeviceClass", (), {})
    number_stub.NumberDeviceClass.TEMPERATURE = "temperature"
    number_stub.NumberDeviceClass.HUMIDITY = "humidity"
    number_stub.NumberDeviceClass.POWER = "power"
    number_stub.NumberDeviceClass.CURRENT = "current"
    number_stub.NumberDeviceClass.VOLTAGE = "voltage"
    number_stub.NumberDeviceClass.ENERGY = "energy"
    number_stub.NumberDeviceClass.DURATION = "duration"
    number_stub.NumberDeviceClass.ILLUMINANCE = "illuminance"
    number_stub.NumberDeviceClass.IRRADIANCE = "irradiance"
    number_stub.NumberDeviceClass.FREQUENCY = "frequency"
    number_stub.NumberDeviceClass.PRESSURE = "pressure"
    number_stub.NumberDeviceClass.DISTANCE = "distance"
    number_stub.NumberDeviceClass.SPEED = "speed"
    number_stub.NumberDeviceClass.VOLUME = "volume"
    number_stub.NumberDeviceClass.WATER = "water"
    number_stub.NumberDeviceClass.WEIGHT = "weight"
    number_stub.NumberDeviceClass.WIND_SPEED = "wind_speed"
    number_stub.NumberDeviceClass.PRECIPITATION = "precipitation"
    number_stub.NumberDeviceClass.PRECIPITATION_INTENSITY = "precipitation_intensity"
    number_stub.NumberDeviceClass.AQI = "aqi"
    number_stub.NumberDeviceClass.CO = "carbon_monoxide"
    number_stub.NumberDeviceClass.CO2 = "carbon_dioxide"
    number_stub.NumberDeviceClass.PM1 = "pm1"
    number_stub.NumberDeviceClass.PM25 = "pm25"
    number_stub.NumberDeviceClass.PM10 = "pm10"
    number_stub.NumberDeviceClass.VOLATILE_ORGANIC_COMPOUNDS = "volatile_organic_compounds"
    number_stub.NumberDeviceClass.NITROGEN_DIOXIDE = "nitrogen_dioxide"
    number_stub.NumberDeviceClass.NITROGEN_MONOXIDE = "nitrogen_monoxide"
    number_stub.NumberDeviceClass.OZONE = "ozone"
    number_stub.NumberDeviceClass.SULPHUR_DIOXIDE = "sulphur_dioxide"
    number_stub.NumberDeviceClass.BATTERY = "battery"
    number_stub.NumberDeviceClass.APPARENT_POWER = "apparent_power"
    number_stub.NumberDeviceClass.REACTIVE_POWER = "reactive_power"
    number_stub.NumberDeviceClass.POWER_FACTOR = "power_factor"

    # Add DEVICE_CLASSES_SCHEMA for config validation
    import voluptuous as vol
    number_stub.DEVICE_CLASSES_SCHEMA = vol.In([
        "temperature", "humidity", "power", "current", "voltage", "energy",
        "duration", "illuminance", "irradiance", "frequency", "pressure",
        "distance", "speed", "volume", "water", "weight", "wind_speed",
        "precipitation", "precipitation_intensity", "aqi", "carbon_monoxide",
        "carbon_dioxide", "pm1", "pm25", "pm10", "volatile_organic_compounds",
        "nitrogen_dioxide", "nitrogen_monoxide", "ozone", "sulphur_dioxide",
        "battery", "apparent_power", "reactive_power", "power_factor",
    ])

    # Add NumberMode if not already present
    if not hasattr(number_stub, 'NumberMode'):
        number_stub.NumberMode = NumberMode

    # Add constants if not already present
    if not hasattr(number_stub, 'DEFAULT_MIN_VALUE'):
        number_stub.DEFAULT_MIN_VALUE = 0.0
    if not hasattr(number_stub, 'DEFAULT_MAX_VALUE'):
        number_stub.DEFAULT_MAX_VALUE = 100.0
    if not hasattr(number_stub, 'DEFAULT_STEP'):
        number_stub.DEFAULT_STEP = 1.0
    if not hasattr(number_stub, 'ATTR_MIN'):
        number_stub.ATTR_MIN = "min"
    if not hasattr(number_stub, 'ATTR_MAX'):
        number_stub.ATTR_MAX = "max"
    if not hasattr(number_stub, 'ATTR_STEP'):
        number_stub.ATTR_STEP = "step"
    if not hasattr(number_stub, 'ATTR_MODE'):
        number_stub.ATTR_MODE = "mode"
    if not hasattr(number_stub, 'DOMAIN'):
        number_stub.DOMAIN = "number"

    # scene
    scene_stub = types.ModuleType("homeassistant.components.scene")
    scene_stub.Scene = type("Scene", (), {})
    scene_stub.SceneEntity = type("SceneEntity", (), {})
    scene_stub.SceneEntity.activate = lambda self: None
    scene_stub.SceneEntity.async_activate = lambda self: None
    scene_stub.DOMAIN = "scene"
    homeassistant.components.scene = scene_stub
    sys.modules["homeassistant.components.scene"] = scene_stub

    # helpers.typing
    typing_stub = types.ModuleType("homeassistant.helpers.typing")
    typing_stub.ConfigType = dict
    typing_stub.DiscoveryInfoType = dict
    # StateType is used by integrations for sensor state values
    typing_stub.StateType = str | int | float | None

    class EventType:
        def __init__(self, event_type, event_data_type):
            self.event_type = event_type
            self.event_data_type = event_data_type

        def __call__(self, func):
            return func

    typing_stub.EventType = EventType
    homeassistant.helpers.typing = typing_stub
    sys.modules["homeassistant.helpers.typing"] = typing_stub

    return homeassistant
