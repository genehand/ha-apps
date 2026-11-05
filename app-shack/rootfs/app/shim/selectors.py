"""Home Assistant selector helpers.

Provides selector classes for config flows.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union, TypedDict


class SelectOptionDict(TypedDict):
    """Dictionary representing a select option."""

    value: str
    label: str


class SelectSelectorMode(str, Enum):
    """Enum for select selector modes."""

    LIST = "list"
    DROPDOWN = "dropdown"


class TextSelectorType(str, Enum):
    """Enum for text selector types."""

    COLOR = "color"
    DATE = "date"
    DATETIME_LOCAL = "datetime-local"
    EMAIL = "email"
    MONTH = "month"
    NUMBER = "number"
    PASSWORD = "password"
    SEARCH = "search"
    TEL = "tel"
    TEXT = "text"
    TIME = "time"
    URL = "url"
    WEEK = "week"


class NumberSelectorMode(str, Enum):
    """Enum for number selector modes."""

    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


class Selector:
    """Base selector class."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize selector."""
        self.config = config or {}

    def __call__(self, data: Any) -> Any:
        """Validate and convert data."""
        return data


class EntitySelector(Selector):
    """Selector for entities."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        domain: Optional[Union[str, List[str]]] = None,
        device_class: Optional[str] = None,
        multiple: bool = False,
    ):
        """Initialize entity selector."""
        super().__init__(config)
        if domain:
            self.config["domain"] = domain
        if device_class:
            self.config["device_class"] = device_class
        self.config["multiple"] = multiple


class DeviceSelector(Selector):
    """Selector for devices."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        integration: Optional[str] = None,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        multiple: bool = False,
    ):
        """Initialize device selector."""
        super().__init__(config)
        if integration:
            self.config["integration"] = integration
        if manufacturer:
            self.config["manufacturer"] = manufacturer
        if model:
            self.config["model"] = model
        self.config["multiple"] = multiple


class AreaSelector(Selector):
    """Selector for areas."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        multiple: bool = False,
    ):
        """Initialize area selector."""
        super().__init__(config)
        self.config["multiple"] = multiple


class NumberSelector(Selector):
    """Selector for numbers."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        min: Optional[float] = None,
        max: Optional[float] = None,
        step: Optional[float] = None,
        unit_of_measurement: Optional[str] = None,
        mode: str = "auto",  # "box" or "slider"
    ):
        """Initialize number selector."""
        super().__init__(config)
        if min is not None:
            self.config["min"] = min
        if max is not None:
            self.config["max"] = max
        if step is not None:
            self.config["step"] = step
        if unit_of_measurement:
            self.config["unit_of_measurement"] = unit_of_measurement
        self.config["mode"] = mode


class BooleanSelector(Selector):
    """Selector for boolean values."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize boolean selector."""
        super().__init__(config)


class TextSelector(Selector):
    """Selector for text input."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        type: str = "text",
        autocomplete: Optional[str] = None,
        multiple: bool = False,
    ):
        """Initialize text selector."""
        super().__init__(config)
        # Only set defaults if not already provided in config dict
        if "type" not in self.config:
            self.config["type"] = type
        if autocomplete and "autocomplete" not in self.config:
            self.config["autocomplete"] = autocomplete
        if "multiple" not in self.config:
            self.config["multiple"] = multiple


class SelectSelector(Selector):
    """Selector for selecting from options."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        options: Optional[List[Union[str, Dict[str, str]]]] = None,
        multiple: bool = False,
        mode: str = "list",  # "list" or "dropdown"
        translation_key: Optional[str] = None,
        custom_value: bool = False,
    ):
        """Initialize select selector."""
        super().__init__(config)
        # Use provided options, or fall back to config's options, or empty list
        self.config["options"] = (
            options if options is not None else self.config.get("options", [])
        )
        if "multiple" not in self.config:
            self.config["multiple"] = multiple
        if "mode" not in self.config:
            self.config["mode"] = mode
        if "custom_value" not in self.config:
            self.config["custom_value"] = custom_value
        if translation_key and "translation_key" not in self.config:
            self.config["translation_key"] = translation_key


class TimeSelector(Selector):
    """Selector for time input."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize time selector."""
        super().__init__(config)


class DateSelector(Selector):
    """Selector for date input."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize date selector."""
        super().__init__(config)


class DateTimeSelector(Selector):
    """Selector for datetime input."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize datetime selector."""
        super().__init__(config)


class ColorRGBSelector(Selector):
    """Selector for RGB color."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize RGB color selector."""
        super().__init__(config)


class IconSelector(Selector):
    """Selector for icons."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        placeholder: Optional[str] = None,
    ):
        """Initialize icon selector."""
        super().__init__(config)
        if placeholder:
            self.config["placeholder"] = placeholder


class ThemeSelector(Selector):
    """Selector for themes."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize theme selector."""
        super().__init__(config)


class LocationSelector(Selector):
    """Selector for location."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        radius: bool = False,
        icon: str = "mdi:map-marker-radius",
    ):
        """Initialize location selector."""
        super().__init__(config)
        self.config["radius"] = radius
        self.config["icon"] = icon


class MediaSelector(Selector):
    """Selector for media."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize media selector."""
        super().__init__(config)


class DurationSelector(Selector):
    """Selector for duration."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        enable_day: bool = False,
    ):
        """Initialize duration selector."""
        super().__init__(config)
        self.config["enable_day"] = enable_day


class StateSelector(Selector):
    """Selector for entity states."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        entity_id: Optional[str] = None,
    ):
        """Initialize state selector."""
        super().__init__(config)
        if entity_id:
            self.config["entity_id"] = entity_id


class TemplateSelector(Selector):
    """Selector for templates (used heavily in MQTT)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        native_value: bool = True,
    ):
        """Initialize template selector."""
        super().__init__(config)
        self.config["native_value"] = native_value


class QRCodeSelector(Selector):
    """Selector for QR code scanning (device pairing)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        data: Optional[str] = None,
    ):
        """Initialize QR code selector."""
        super().__init__(config)
        if data:
            self.config["data"] = data


class ObjectSelector(Selector):
    """Selector for objects (JSON/YAML data)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize object selector."""
        super().__init__(config)


class FloorSelector(Selector):
    """Selector for floors (new HA organizational feature)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        multiple: bool = False,
    ):
        """Initialize floor selector."""
        super().__init__(config)
        self.config["multiple"] = multiple


class LabelSelector(Selector):
    """Selector for labels (new HA organizational feature)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        multiple: bool = False,
    ):
        """Initialize label selector."""
        super().__init__(config)
        self.config["multiple"] = multiple


class ConfigEntrySelector(Selector):
    """Selector for config entries."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        integration: Optional[str] = None,
    ):
        """Initialize config entry selector."""
        super().__init__(config)
        if integration:
            self.config["integration"] = integration


# Selector config type aliases (used by config flows)
EntitySelectorConfig = dict
DeviceSelectorConfig = dict
AreaSelectorConfig = dict
NumberSelectorConfig = dict
BooleanSelectorConfig = dict
TextSelectorConfig = dict
SelectSelectorConfig = dict
TimeSelectorConfig = dict
DateSelectorConfig = dict
DateTimeSelectorConfig = dict
ColorRGBSelectorConfig = dict
IconSelectorConfig = dict
ThemeSelectorConfig = dict
LocationSelectorConfig = dict
MediaSelectorConfig = dict
DurationSelectorConfig = dict
ObjectSelectorConfig = dict
AttributeSelectorConfig = dict
ActionSelectorConfig = dict
AddonSelectorConfig = dict
AreaFilterSelectorConfig = dict
AssistPipelineSelectorConfig = dict
BackupLocationSelectorConfig = dict
BarcodeSelectorConfig = dict
ColorTempSelectorConfig = dict
ConfigEntrySelectorConfig = dict
ConstantSelectorConfig = dict
ConversationAgentSelectorConfig = dict
CountrySelectorConfig = dict
DateTimeRangeSelectorConfig = dict
EntityFilterSelectorConfig = dict
FileSelectorConfig = dict
FloorSelectorConfig = dict
LabelSelectorConfig = dict
LanguageSelectorConfig = dict
NavigationLocationSelectorConfig = dict
QRCodeSelectorConfig = dict
ResourceSelectorConfig = dict
SelectorSelectorConfig = dict
StateSelectorConfig = dict
StatisticsPeriodSelectorConfig = dict
TargetSelectorConfig = dict
TemplateSelectorConfig = dict
TimeZoneSelectorConfig = dict
TriggerSelectorConfig = dict
UserSelectorConfig = dict

StateSelectorConfig = dict
TemplateSelectorConfig = dict
QRCodeSelectorConfig = dict
FloorSelectorConfig = dict
LabelSelectorConfig = dict


def selector(
    selector_type: Union[str, Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Create a selector configuration.

    This is a helper function used in config flows to create selector configs.

    Args:
        selector_type: The type of selector (e.g., 'text', 'number', 'select', etc.)
                     or a dict with the selector config as the first/only key
        config: Optional configuration dict
        **kwargs: Additional configuration options

    Returns:
        A dict with the selector configuration
    """
    # Handle case where selector_type is actually a dict config
    # e.g., selector({"select": {"options": [...]}})
    if isinstance(selector_type, dict):
        return selector_type

    # Handle normal case: selector("text") or selector("select", {...})
    selector_config = config or {}
    selector_config.update(kwargs)
    return {selector_type: selector_config}


class UnitOfMeasurementSelectorContext(TypedDict, total=False):
    """Context for the unit of measurement selector (HA 2026.11)."""

    filter_device_class: str
    filter_state_class: str


class UnitOfMeasurementSelectorConfig(TypedDict, total=False):
    """Unit of measurement selector config (HA 2026.11).

    ``device_classes``/``state_classes`` limit the allowed units to the sensor
    tables. ``context`` names sibling fields the frontend filters on; it is
    frontend-only and can't be combined with the matching fixed limit.
    """

    device_classes: Union[str, List[str], None]
    state_classes: Union[str, List[str], None]
    context: UnitOfMeasurementSelectorContext


def _sensor_units_tables():
    """Return the real sensor unit tables, or ``None`` when unavailable.

    ``homeassistant.components.sensor`` is synthetic in the shim, but its
    ``const`` module is model-layer-free. Load the wheel's file directly so the
    selector validates against HA's own tables rather than a copy that drifts.
    """
    try:
        import importlib.util
        from pathlib import Path

        import homeassistant.const as ha_const

        path = (
            Path(ha_const.__file__).parent / "components" / "sensor" / "const.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_shack_sensor_const", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 - a missing wheel file only relaxes validation
        return None


def _units_set(dict_name: str, keys: List[str]):
    """Return the units allowed for any of ``keys`` (mirrors HA's ``_units_set``).

    ``None`` means one of the keys does not limit the units, so any (custom)
    unit has to be accepted.
    """
    module = _sensor_units_tables()
    if module is None:
        return None
    units_dict = getattr(module, dict_name, None)
    if units_dict is None:
        return None
    non_numeric = getattr(module, "NON_NUMERIC_DEVICE_CLASSES", set())

    units = set()
    for key in keys:
        if key in units_dict:
            units |= units_dict[key]
        elif key in non_numeric:
            units.add(None)
        else:
            return None
    return units


class UnitOfMeasurementSelector(Selector):
    """Selector for a unit of measurement (HA 2026.11)."""

    selector_type = "unit_of_measurement"
    allowed_context_keys = {
        "filter_device_class": frozenset({"device_class"}),
        "filter_state_class": frozenset({"state_class"}),
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the selector."""
        super().__init__(config)
        self._validate_context()

    def _validate_context(self) -> None:
        """Reject a context key combined with the matching fixed limit."""
        context = self.config.get("context") or {}
        for option, context_key in (
            ("device_classes", "filter_device_class"),
            ("state_classes", "filter_state_class"),
        ):
            if self.config.get(option) and context_key in context:
                raise ValueError(
                    f"Context key {context_key} can not be used with {option}"
                )

    def _units(self):
        """Return the raw allowed-unit set, or ``None`` when unconstrained."""
        device_classes = self._as_list(self.config.get("device_classes"))
        state_classes = self._as_list(self.config.get("state_classes"))

        valid = None
        if device_classes:
            valid = _units_set("DEVICE_CLASS_UNITS", device_classes)
        if state_classes:
            state_units = _units_set("STATE_CLASS_UNITS", state_classes)
            if state_units is not None:
                valid = state_units if valid is None else valid & state_units
        return valid

    def allowed_units(self) -> Optional[List[str]]:
        """Return the allowed units for the UI, or ``None`` for "any"."""
        valid = self._units()
        if valid is None:
            return None
        return sorted(
            str(unit.value if isinstance(unit, Enum) else unit)
            for unit in valid
            if unit is not None
        )

    def __call__(self, data: Any) -> Optional[str]:
        """Validate the selected unit (``None`` clears the unit)."""
        valid = self._units()
        if valid is None:
            if data is not None and not isinstance(data, str):
                raise ValueError("Value should be a string or None")
            return data
        if data not in valid:
            raise ValueError(
                f"Value {data} is not a valid unit of measurement for this selector"
            )
        return data

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        """Normalize a single value or list config entry to a list."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)
