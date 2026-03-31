"""Home Assistant selector helpers.

Provides selector classes for config flows.
"""

from typing import Any, Dict, List, Optional, Union


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
        type: str = "text",  # "text", "password", "email", "url", "tel"
        autocomplete: Optional[str] = None,
        multiple: bool = False,
    ):
        """Initialize text selector."""
        super().__init__(config)
        self.config["type"] = type
        if autocomplete:
            self.config["autocomplete"] = autocomplete
        self.config["multiple"] = multiple


class SelectSelector(Selector):
    """Selector for selecting from options."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        options: List[Union[str, Dict[str, str]]],
        multiple: bool = False,
        mode: str = "list",  # "list" or "dropdown"
        translation_key: Optional[str] = None,
    ):
        """Initialize select selector."""
        super().__init__(config)
        self.config["options"] = options
        self.config["multiple"] = multiple
        self.config["mode"] = mode
        if translation_key:
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


# Alias for EntitySelectorConfig
EntitySelectorConfig = dict
