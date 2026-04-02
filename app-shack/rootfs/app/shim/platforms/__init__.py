"""Home Assistant platform shims.

Bridges HA platform entities to MQTT discovery.
"""

from . import fan
from . import sensor
from . import switch
from . import light
from . import climate
from . import binary_sensor
from . import update
from . import select
from . import button
from . import device_tracker
from . import text
from . import vacuum
from . import humidifier
from . import number
from . import lock
from . import water_heater

__all__ = [
    "fan",
    "sensor",
    "switch",
    "light",
    "climate",
    "binary_sensor",
    "update",
    "select",
    "button",
    "device_tracker",
    "text",
    "vacuum",
    "humidifier",
    "number",
    "lock",
    "water_heater",
]
