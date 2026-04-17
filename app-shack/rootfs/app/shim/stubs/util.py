"""Home Assistant util stub modules.

Provides dt, yaml, color, unit_conversion, unit_system, and percentage.
"""

import re
import sys
import types
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml as _yaml

from ..logging import get_logger

_LOGGER = get_logger(__name__)


def create_util_stubs(hass, homeassistant):
    """Create all homeassistant.util.* stub modules."""

    # Create homeassistant.util package if needed
    if not hasattr(homeassistant, "util") or homeassistant.util is None:
        homeassistant.util = types.ModuleType("homeassistant.util")

    # Create util.dt module
    dt_util = types.ModuleType("homeassistant.util.dt")
    dt_util.DEFAULT_TIME_ZONE = timezone.utc
    dt_util.now = lambda: datetime.now(dt_util.DEFAULT_TIME_ZONE)
    dt_util.as_utc = lambda d: d.astimezone(timezone.utc)
    dt_util.start_of_local_day = lambda: datetime.now(
        dt_util.DEFAULT_TIME_ZONE
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    homeassistant.util.dt = dt_util
    sys.modules["homeassistant.util.dt"] = dt_util

    # Create util.yaml module
    yaml_util = types.ModuleType("homeassistant.util.yaml")

    def load_yaml(fname):
        """Load a YAML file."""
        return _yaml.safe_load(Path(fname).read_text())

    def save_yaml(fname, data):
        """Save data to a YAML file."""
        Path(fname).write_text(_yaml.safe_dump(data, default_flow_style=False))

    def dump(data):
        """Dump data to YAML string."""
        return _yaml.safe_dump(data, default_flow_style=False)

    yaml_util.load_yaml = load_yaml
    yaml_util.save_yaml = save_yaml
    yaml_util.dump = dump
    homeassistant.util.yaml = yaml_util
    sys.modules["homeassistant.util.yaml"] = yaml_util

    # Create util.color module
    color_util = types.ModuleType("homeassistant.util.color")

    def brightness_to_value(scale, brightness):
        """Convert brightness from scale to value."""
        return int(brightness * 255 / 255)

    def value_to_brightness(scale, value):
        """Convert value to brightness on scale."""
        return int(value * 255 / 255)

    def color_rgb_to_rgbw(r, g, b):
        """Convert RGB to RGBW."""
        w = min(r, g, b)
        return (r - w, g - w, b - w, w)

    def color_rgbw_to_rgb(r, g, b, w):
        """Convert RGBW to RGB."""
        return (r + w, g + w, b + w)

    color_util.brightness_to_value = brightness_to_value
    color_util.value_to_brightness = value_to_brightness
    color_util.color_rgb_to_rgbw = color_rgb_to_rgbw
    color_util.color_rgbw_to_rgb = color_rgbw_to_rgb
    homeassistant.util.color = color_util
    sys.modules["homeassistant.util.color"] = color_util

    # Create util.unit_conversion module
    unit_conversion = types.ModuleType("homeassistant.util.unit_conversion")

    class TemperatureConverter:
        """Temperature converter."""

        @staticmethod
        def convert(value, from_unit, to_unit):
            """Convert temperature between units."""
            if from_unit == to_unit:
                return value
            if from_unit == "°C":
                if to_unit == "°F":
                    return value * 9 / 5 + 32
                elif to_unit == "K":
                    return value + 273.15
            elif from_unit == "°F":
                if to_unit == "°C":
                    return (value - 32) * 5 / 9
                elif to_unit == "K":
                    return (value - 32) * 5 / 9 + 273.15
            elif from_unit == "K":
                if to_unit == "°C":
                    return value - 273.15
                elif to_unit == "°F":
                    return (value - 273.15) * 9 / 5 + 32
            return value

    unit_conversion.TemperatureConverter = TemperatureConverter
    homeassistant.util.unit_conversion = unit_conversion
    sys.modules["homeassistant.util.unit_conversion"] = unit_conversion

    # Create util.slugify function
    def slugify(text):
        """Create a slug from text.

        Handles unicode characters by normalizing them to ASCII equivalents
        where possible (e.g., curly quotes -> straight quotes).
        """
        text = str(text)

        # Normalize unicode to decompose characters
        text = unicodedata.normalize("NFKD", text)

        # Map common unicode punctuation to ASCII equivalents
        unicode_map = {
            "\u2018": "'",  # LEFT SINGLE QUOTATION MARK -> APOSTROPHE
            "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK -> APOSTROPHE
            "\u201a": ",",  # SINGLE LOW-9 QUOTATION MARK -> COMMA
            "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK -> APOSTROPHE
            "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK -> QUOTATION MARK
            "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK -> QUOTATION MARK
            "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK -> QUOTATION MARK
            "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK -> QUOTATION MARK
            "\u2026": "...",  # HORIZONTAL ELLIPSIS -> ...
            "\u2013": "-",  # EN DASH -> HYPHEN-MINUS
            "\u2014": "-",  # EM DASH -> HYPHEN-MINUS
            "\u2212": "-",  # MINUS SIGN -> HYPHEN-MINUS
        }
        for unicode_char, ascii_char in unicode_map.items():
            text = text.replace(unicode_char, ascii_char)

        # Encode to ASCII, dropping any remaining non-ASCII chars
        text = text.encode("ascii", "ignore").decode("ascii")

        # Remove non-word chars (except spaces and dashes), lowercase, replace spaces/dashes with underscore
        text = re.sub(r"[^\w\s-]", "", text).strip().lower()
        text = re.sub(r"[-\s]+", "_", text)
        return text

    homeassistant.util.slugify = slugify

    # Create util.unit_system module
    unit_system = types.ModuleType("homeassistant.util.unit_system")
    unit_system.US_CUSTOMARY_SYSTEM = type("US_CUSTOMARY_SYSTEM", (), {})()
    unit_system.METRIC_SYSTEM = type("METRIC_SYSTEM", (), {})()
    unit_system.get_unit_system = lambda name: unit_system.METRIC_SYSTEM
    homeassistant.util.unit_system = unit_system
    sys.modules["homeassistant.util.unit_system"] = unit_system

    # Create util.percentage module
    percentage_stub = types.ModuleType("homeassistant.util.percentage")

    def ordered_list_item_to_percentage(ordered_list, item):
        """Determine the percentage of an item in an ordered list.

        When given an ordered list and a value in that list, returns the
        percentage from 0 to 100 that represents the item's position
        in the list.
        """
        if item not in ordered_list:
            raise ValueError(f"Item '{item}' not in list")
        list_len = len(ordered_list)
        if list_len == 1:
            return 100
        return int((ordered_list.index(item) * 100) / (list_len - 1))

    def percentage_to_ordered_list_item(ordered_list, percentage):
        """Return the item at the specified percentage in an ordered list.

        When given an ordered list of items and a percentage from 0 to 100,
        returns the item at that percentage position in the list.
        """
        if not 0 <= percentage <= 100:
            raise ValueError("Percentage must be between 0 and 100")
        list_len = len(ordered_list)
        if list_len == 1:
            return ordered_list[0]
        return ordered_list[min((percentage * (list_len - 1)) // 100, list_len - 1)]

    percentage_stub.ordered_list_item_to_percentage = ordered_list_item_to_percentage
    percentage_stub.percentage_to_ordered_list_item = percentage_to_ordered_list_item

    # Add range functions used by fan platform
    def int_states_in_range(low_high_range):
        """Return the number of integer states in a range."""
        low, high = low_high_range
        return int(high - low + 1)

    def states_in_range(low_high_range):
        """Return the number of states in a range."""
        low, high = low_high_range
        return high - low + 1

    def scale_to_ranged_value(source_low_high_range, target_low_high_range, value):
        """Convert a value from source range to target range."""
        source_offset = source_low_high_range[0] - 1
        target_offset = target_low_high_range[0] - 1
        return (value - source_offset) * (
            states_in_range(target_low_high_range)
        ) / states_in_range(source_low_high_range) + target_offset

    def scale_ranged_value_to_int_range(source_low_high_range, target_low_high_range, value):
        """Convert a value from source range to target int range."""
        source_offset = source_low_high_range[0] - 1
        target_offset = target_low_high_range[0] - 1
        return int(
            (value - source_offset)
            * states_in_range(target_low_high_range)
            // states_in_range(source_low_high_range)
            + target_offset
        )

    def ranged_value_to_percentage(low_high_range, value):
        """Map a value within a range to a percentage."""
        if value is None:
            return None
        return scale_ranged_value_to_int_range(low_high_range, (1, 100), value)

    def percentage_to_ranged_value(low_high_range, percentage):
        """Map a percentage to a value within a range."""
        return scale_to_ranged_value((1, 100), low_high_range, percentage)

    percentage_stub.int_states_in_range = int_states_in_range
    percentage_stub.states_in_range = states_in_range
    percentage_stub.scale_to_ranged_value = scale_to_ranged_value
    percentage_stub.scale_ranged_value_to_int_range = scale_ranged_value_to_int_range
    percentage_stub.ranged_value_to_percentage = ranged_value_to_percentage
    percentage_stub.percentage_to_ranged_value = percentage_to_ranged_value

    homeassistant.util.percentage = percentage_stub
    sys.modules["homeassistant.util.percentage"] = percentage_stub

    sys.modules["homeassistant.util"] = homeassistant.util

    return homeassistant
