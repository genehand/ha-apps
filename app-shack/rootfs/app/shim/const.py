"""Home Assistant constants shim.

Provides constants commonly imported by HA integrations.
"""

from enum import Enum


class EntityCategory(str, Enum):
    """Entity categories."""

    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class Platform(str, Enum):
    """Available platforms."""

    AIR_QUALITY = "air_quality"
    ALARM_CONTROL_PANEL = "alarm_control_panel"
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    CALENDAR = "calendar"
    CAMERA = "camera"
    CLIMATE = "climate"
    COVER = "cover"
    DATE = "date"
    DATETIME = "datetime"
    DEVICE_TRACKER = "device_tracker"
    FAN = "fan"
    HUMIDIFIER = "humidifier"
    IMAGE = "image"
    LAWN_MOWER = "lawn_mower"
    LIGHT = "light"
    LOCK = "lock"
    MEDIA_PLAYER = "media_player"
    NOTIFY = "notify"
    NUMBER = "number"
    REMOTE = "remote"
    SCENE = "scene"
    SELECT = "select"
    SENSOR = "sensor"
    SIREN = "siren"
    STT = "stt"
    SWITCH = "switch"
    TEXT = "text"
    TIME = "time"
    TODO = "todo"
    TTS = "tts"
    UPDATE = "update"
    VACUUM = "vacuum"
    VALVE = "valve"
    WATER_HEATER = "water_heater"
    WEATHER = "weather"


# Entity platforms (legacy constants for backwards compatibility)
PLATFORM_FAN = "fan"
PLATFORM_SENSOR = "sensor"
PLATFORM_SWITCH = "switch"
PLATFORM_LIGHT = "light"
PLATFORM_CLIMATE = "climate"
PLATFORM_BINARY_SENSOR = "binary_sensor"
PLATFORM_UPDATE = "update"

# Configuration keys
CONF_NAME = "name"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_TOKEN = "token"
CONF_API_KEY = "api_key"
CONF_DEVICE_ID = "device_id"
CONF_UNIQUE_ID = "unique_id"
CONF_ENTITY_ID = "entity_id"
CONF_DEVICE = "device"
CONF_DEVICES = "devices"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_URL = "url"
CONF_EMAIL = "email"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_CODE = "code"
CONF_VERIFY_SSL = "verify_ssl"
CONF_TIMEOUT = "timeout"
CONF_ZONE = "zone"
CONF_ENTITIES = "entities"
CONF_INCLUDE = "include"
CONF_EXCLUDE = "exclude"
CONF_SENSORS = "sensors"
CONF_SWITCHES = "switches"
CONF_LIGHTS = "lights"
CONF_FANS = "fans"
CONF_CLIMATES = "climates"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS = "radius"
CONF_LOCATION = "location"
CONF_UNIT_OF_MEASUREMENT = "unit_of_measurement"
CONF_ICON = "icon"
CONF_MODE = "mode"
CONF_TYPE = "type"

# State values
STATE_ON = "on"
STATE_OFF = "off"
STATE_HOME = "home"
STATE_NOT_HOME = "not_home"
STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_OK = "ok"
STATE_PROBLEM = "problem"
STATE_IDLE = "idle"
STATE_PLAYING = "playing"
STATE_PAUSED = "paused"
STATE_BUFFERING = "buffering"
STATE_STREAMING = "streaming"

# Attribute names
ATTR_ENTITY_ID = "entity_id"
ATTR_FRIENDLY_NAME = "friendly_name"
ATTR_UNIT_OF_MEASUREMENT = "unit_of_measurement"
ATTR_DEVICE_CLASS = "device_class"
ATTR_STATE_CLASS = "state_class"
ATTR_ICON = "icon"
ATTR_ASSUMED_STATE = "assumed_state"
ATTR_ATTRIBUTION = "attribution"
ATTR_BATTERY_LEVEL = "battery_level"
ATTR_DEVICE_ID = "device_id"
ATTR_DOMAIN = "domain"
ATTR_SERVICE = "service"
ATTR_SERVICE_DATA = "service_data"

# Device classes
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_HUMIDITY = "humidity"
DEVICE_CLASS_PRESSURE = "pressure"
DEVICE_CLASS_ILLUMINANCE = "illuminance"
DEVICE_CLASS_ENERGY = "energy"
DEVICE_CLASS_POWER = "power"
DEVICE_CLASS_CURRENT = "current"
DEVICE_CLASS_VOLTAGE = "voltage"
DEVICE_CLASS_BATTERY = "battery"
DEVICE_CLASS_SIGNAL_STRENGTH = "signal_strength"
DEVICE_CLASS_TIMESTAMP = "timestamp"
DEVICE_CLASS_MONETARY = "monetary"
DEVICE_CLASS_DISTANCE = "distance"
DEVICE_CLASS_VOLUME = "volume"
DEVICE_CLASS_GAS = "gas"
DEVICE_CLASS_WATER = "water"
DEVICE_CLASS_AQI = "aqi"
DEVICE_CLASS_CO = "carbon_monoxide"
DEVICE_CLASS_CO2 = "carbon_dioxide"
DEVICE_CLASS_PM25 = "pm25"
DEVICE_CLASS_PM10 = "pm10"

# State classes
STATE_CLASS_MEASUREMENT = "measurement"
STATE_CLASS_TOTAL = "total"
STATE_CLASS_TOTAL_INCREASING = "total_increasing"

# Units
UNIT_PERCENTAGE = "%"
PERCENTAGE = "%"


class UnitOfTemperature:
    """Temperature units."""

    CELSIUS = "°C"
    FAHRENHEIT = "°F"
    KELVIN = "K"


class UnitOfTime:
    """Time units."""

    SECONDS = "s"
    MINUTES = "min"
    HOURS = "h"
    DAYS = "d"


UNIT_CELSIUS = "°C"
UNIT_FAHRENHEIT = "°F"
UNIT_KELVIN = "K"
UNIT_LUX = "lx"
UNIT_WATT = "W"
UNIT_KILOWATT = "kW"
UNIT_VOLT = "V"
UNIT_AMPERE = "A"
UNIT_WATT_HOUR = "Wh"
UNIT_KILOWATT_HOUR = "kWh"
UNIT_MBPS = "Mbit/s"
UNIT_DB = "dB"
UNIT_DBM = "dBm"
UNIT_PPM = "ppm"
UNIT_MG_M3 = "µg/m³"

# Concentration units (for sensor device classes)
CONCENTRATION_MICROGRAMS_PER_CUBIC_METER = "µg/m³"
CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER = "mg/m³"
CONCENTRATION_PARTS_PER_MILLION = "ppm"
UNIT_MILLISECONDS = "ms"
UNIT_SECONDS = "s"
UNIT_MINUTES = "min"
UNIT_HOURS = "h"
UNIT_DAYS = "d"

# Update domains
UPDATE_DOMAIN = "update"

# Event types
EVENT_HOMEASSISTANT_START = "homeassistant_start"
EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"
EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
EVENT_HOMEASSISTANT_FINAL_WRITE = "homeassistant_final_write"
EVENT_STATE_CHANGED = "state_changed"
EVENT_SERVICE_REGISTERED = "service_registered"
EVENT_SERVICE_REMOVED = "service_removed"
EVENT_CALL_SERVICE = "call_service"
EVENT_COMPONENT_LOADED = "component_loaded"
EVENT_PLATFORM_DISCOVERED = "platform_discovered"

# Source types
SOURCE_TYPE_GPS = "gps"
SOURCE_TYPE_ROUTER = "router"
SOURCE_TYPE_BLUETOOTH = "bluetooth"
SOURCE_TYPE_BLUETOOTH_LE = "bluetooth_le"

# Config entry sources
SOURCE_USER = "user"
SOURCE_IMPORT = "import"
SOURCE_DISCOVERY = "discovery"
SOURCE_ZEROCONF = "zeroconf"
SOURCE_SSDP = "ssdp"
SOURCE_DHCP = "dhcp"
SOURCE_HOMEKIT = "homekit"
SOURCE_MQTT = "mqtt"
SOURCE_REAUTH = "reauth"
SOURCE_RECONFIGURE = "reconfigure"

# Connection classes
CONN_CLASS_LOCAL_POLL = "local_poll"
CONN_CLASS_LOCAL_PUSH = "local_push"
CONN_CLASS_CLOUD_POLL = "cloud_poll"
CONN_CLASS_CLOUD_PUSH = "cloud_push"
CONN_CLASS_ASSUMED = "assumed"

# Data rate units
DATA_RATE_MEGABITS_PER_SECOND = "Mbit/s"
DATA_RATE_GIGABITS_PER_SECOND = "Gbit/s"

# Fan directions
DIRECTION_FORWARD = "forward"
DIRECTION_REVERSE = "reverse"

# Fan speeds
SPEED_OFF = "off"
SPEED_LOW = "low"
SPEED_MEDIUM = "medium"
SPEED_HIGH = "high"

# Cover states
COVER_STATE_OPEN = "open"
COVER_STATE_CLOSED = "closed"
COVER_STATE_OPENING = "opening"
COVER_STATE_CLOSING = "closing"

# Climate modes
HVAC_MODE_OFF = "off"
HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"
HVAC_MODE_HEAT_COOL = "heat_cool"
HVAC_MODE_AUTO = "auto"
HVAC_MODE_DRY = "dry"
HVAC_MODE_FAN_ONLY = "fan_only"

# Climate presets
PRESET_NONE = "none"
PRESET_ECO = "eco"
PRESET_AWAY = "away"
PRESET_BOOST = "boost"
PRESET_COMFORT = "comfort"
PRESET_HOME = "home"
PRESET_SLEEP = "sleep"
PRESET_ACTIVITY = "activity"

# Log levels
LOG_LEVELS = {
    "CRITICAL": 50,
    "FATAL": 50,
    "ERROR": 40,
    "WARN": 30,
    "WARNING": 30,
    "INFO": 20,
    "DEBUG": 10,
    "NOTSET": 0,
}

# Entity feature flags
SUPPORT_TARGET_TEMPERATURE = 1
SUPPORT_TARGET_TEMPERATURE_RANGE = 2
SUPPORT_FAN_MODE = 4
SUPPORT_PRESET_MODE = 8
SUPPORT_SWING_MODE = 16
SUPPORT_AUX_HEAT = 32

# Fan features
SUPPORT_SET_SPEED = 1
SUPPORT_OSCILLATE = 2
SUPPORT_DIRECTION = 4
SUPPORT_PRESET_MODE = 8

# Light features
SUPPORT_BRIGHTNESS = 1
SUPPORT_COLOR_TEMP = 2
SUPPORT_EFFECT = 4
SUPPORT_FLASH = 8
SUPPORT_COLOR = 16
SUPPORT_TRANSITION = 32
SUPPORT_WHITE_VALUE = 128
