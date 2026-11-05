"""Selectors added in HA 2026.11 (``UnitOfMeasurementSelector``)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _selector(config=None):
    from homeassistant.helpers.selector import UnitOfMeasurementSelector

    return UnitOfMeasurementSelector(config)


def test_selector_is_importable_from_the_helpers_path():
    """Integrations import it from ``homeassistant.helpers.selector``."""
    from homeassistant.helpers.selector import (
        UnitOfMeasurementSelector,
        UnitOfMeasurementSelectorConfig,
        UnitOfMeasurementSelectorContext,
    )

    assert UnitOfMeasurementSelector.selector_type == "unit_of_measurement"
    assert UnitOfMeasurementSelectorConfig is not None
    assert UnitOfMeasurementSelectorContext is not None


def test_unconstrained_accepts_a_custom_unit():
    """Without a device/state class any string is accepted (custom units)."""
    selector = _selector()
    assert selector.allowed_units() is None
    assert selector("widgets_per_fortnight") == "widgets_per_fortnight"
    assert selector(None) is None


def test_device_class_limits_the_units_to_the_sensor_table():
    """``device_classes`` narrows the units, taken from the real HA tables."""
    selector = _selector({"device_classes": ["energy"]})
    allowed = selector.allowed_units()
    assert "kWh" in allowed
    assert len(allowed) == 15
    assert selector("kWh") == "kWh"
    with pytest.raises(ValueError):
        selector("m")


def test_state_class_limit_intersects_with_device_class():
    """State class units narrow the device class units further."""
    selector = _selector(
        {"device_classes": ["power"], "state_classes": ["measurement"]}
    )
    allowed = selector.allowed_units()
    assert "W" in allowed
    assert selector("W") == "W"
    with pytest.raises(ValueError):
        selector("kWh")


def test_context_cannot_be_combined_with_a_fixed_limit():
    """A context key and its matching fixed limit are mutually exclusive."""
    with pytest.raises(ValueError):
        _selector(
            {
                "device_classes": ["energy"],
                "context": {"filter_device_class": "device_class"},
            }
        )
    # The other context key is fine.
    selector = _selector(
        {"device_classes": ["energy"], "context": {"filter_state_class": "state"}}
    )
    assert selector("kWh") == "kWh"
