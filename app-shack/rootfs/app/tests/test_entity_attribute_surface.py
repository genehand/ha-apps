"""The ``Entity`` attribute surface must stay assignable (HA parity).

Regression tests for the meross_lan 6.0.0 update in the shack::

    ERROR: shim.integrations.loader - Error during async_setup_entry for
           meross_lan: property 'device_info' of 'ProtocolSensor' object has no
           setter

Home Assistant declares ``device_info``, ``available``, ``native_value`` and
friends as ``cached_property`` (propcache), which is a *non-data* descriptor: an
integration that assigns the legacy way -- ``self.device_info = ...``, or the
``setattr`` loop over ``HA_ENTITY_ATTRIBUTES`` in meross_lan's own
``Entity.__init__`` -- stores an instance attribute, and ``__dict__`` wins over
the class attribute. A plain ``@property`` is a *data* descriptor instead, so
the very same assignment raises.

The shim served those getters as ``@property``, so an integration's first entity
could not be constructed. ``EntityAttribute`` restores the non-data behaviour
(without caching: the shim reads these on every state publish and integrations
flip ``_attr_*`` at runtime).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.entity import Entity, EntityAttribute, ToggleEntity
from shim.platforms.light import LightEntity
from shim.platforms.number import NumberEntity
from shim.platforms.sensor import SensorEntity
from shim.platforms.text import TextEntity

# The names meross_lan's Entity.__init__ assigns directly or through its
# HA_ENTITY_ATTRIBUTES kwargs loop.
MERGED_ATTRIBUTES = (
    "device_class",
    "entity_category",
    "entity_registry_enabled_default",
    "name",
    "icon",
    "unique_id",
    "device_info",
    "available",
    "force_update",
    "has_entity_name",
    "should_poll",
    "extra_state_attributes",
)


def test_entity_attribute_is_a_non_data_descriptor():
    """Assignment must not go through a setter (that would reorder precedence)."""

    class Holder:
        @EntityAttribute
        def value(self):
            return "computed"

    holder = Holder()
    assert holder.value == "computed"
    holder.value = "assigned"
    assert holder.value == "assigned"
    assert hasattr(EntityAttribute(lambda self: None), "__set__") is False


@pytest.mark.parametrize("attribute", MERGED_ATTRIBUTES)
def test_entity_accepts_the_legacy_assignment(attribute):
    """``self.<attribute> = ...`` is legal on a plain entity (HA parity)."""

    entity = Entity()
    setattr(entity, attribute, "assigned")
    # ``__dict__`` wins over the class attribute, exactly as with HA's
    # cached_property.
    assert getattr(entity, attribute) == "assigned"


def test_assignment_wins_over_a_derived_getter():
    """Sensor availability is derived, but an explicit value must override it."""
    sensor = SensorEntity()
    Entity.__init__(sensor)

    # No native value yet: the platform getter derives "unavailable".
    assert sensor.native_value is None
    assert sensor.available is False

    sensor.native_value = 0  # 0 is a valid state, not "missing"
    assert sensor.native_value == 0
    assert sensor.available is True

    # meross_lan's set_unavailable() does exactly this.
    sensor.available = False
    assert sensor.available is False


def test_platform_value_getters_stay_assignable():
    """The platform base classes assign the same way (meross_lan does)."""
    sensor = SensorEntity()
    Entity.__init__(sensor)
    sensor.native_value = 21.5
    assert sensor.native_value == 21.5
    sensor.unit_of_measurement = "°C"
    assert sensor.unit_of_measurement == "°C"
    sensor.icon = "mdi:thermometer"
    assert sensor.icon == "mdi:thermometer"
    sensor.extra_state_attributes = {"battery": 90}
    assert sensor.extra_state_attributes == {"battery": 90}

    number = NumberEntity()
    Entity.__init__(number)
    number.native_value = 42
    assert number.native_value == 42

    text = TextEntity()
    Entity.__init__(text)
    text.native_value = "abc"
    assert text.native_value == "abc"

    light = LightEntity()
    Entity.__init__(light)
    light.supported_features = 3
    assert light.supported_features == 3


def test_toggle_state_still_derives_from_is_on():
    """A toggle entity with no explicit value keeps computing its state."""
    toggle = ToggleEntity()
    Entity.__init__(toggle)
    assert toggle.is_on is None
    assert toggle.state is None

    toggle.is_on = True
    assert toggle.state == "on"
    toggle.is_on = False
    assert toggle.state == "off"

    # ... and an integration may still assign the state outright.
    toggle.state = "whatever"
    assert toggle.state == "whatever"
