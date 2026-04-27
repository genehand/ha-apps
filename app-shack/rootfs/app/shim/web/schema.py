"""Voluptuous schema parsing and form value conversion utilities.

Parses HA config flow schemas into form field definitions and converts
raw form string values into their appropriate Python types.
"""

import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


def is_undefined(value: Any) -> bool:
    """Check if a value is UNDEFINED (either HA's or voluptuous's).

    Args:
        value: The value to check

    Returns:
        True if the value is UNDEFINED, False otherwise
    """
    import voluptuous as vol

    if value is None:
        return False

    # Check for HA's UNDEFINED class (defined in import_patch.py)
    if hasattr(value, "__class__") and value.__class__.__name__ == "UNDEFINED":
        return True

    # Check for voluptuous's UNDEFINED (which is ... Ellipsis)
    if value is vol.UNDEFINED:
        return True

    return False


def parse_schema(schema) -> List[Dict[str, Any]]:
    """Parse a voluptuous schema into form field definitions."""
    fields = []

    if schema is None:
        return fields

    try:
        # Handle voluptuous Schema
        if hasattr(schema, "schema"):
            schema_dict = schema.schema
            if isinstance(schema_dict, dict):
                for key, validator in schema_dict.items():
                    field = parse_field(key, validator)
                    if field:
                        fields.append(field)
    except Exception as e:
        _LOGGER.warning("Failed to parse schema: %s", e)

    return fields


def parse_field(key, validator) -> Optional[Dict[str, Any]]:
    """Parse a single schema field into form field definition."""
    import voluptuous as vol

    field = {
        "name": None,
        "label": None,
        "type": "text",
        "required": False,
        "default": None,
        "options": None,
        "placeholder": None,
    }

    # Extract field name from key (Required/Optional marker)
    if hasattr(key, "schema"):
        # This is a Required/Optional marker
        field["name"] = key.schema
        # Check if it's Required or Optional by checking the type
        field["required"] = isinstance(key, vol.Required)
        # Get default if Optional with default value
        if hasattr(key, "default"):
            # In voluptuous, Required raises Undefined if no default
            # Optional has UNDEFINED as default if no default specified
            if key.default is not vol.UNDEFINED:
                # Handle callable defaults (e.g., lambda functions)
                if callable(key.default):
                    try:
                        field["default"] = key.default()
                    except Exception:
                        # If the callable fails, don't set a default
                        pass
                else:
                    field["default"] = key.default
        # Check for suggested_value in description (used by some integrations like cryptoinfo)
        if field["default"] is None and hasattr(key, "description"):
            description = key.description
            if isinstance(description, dict):
                if "suggested_value" in description:
                    suggested = description["suggested_value"]
                    # Skip UNDEFINED suggested values
                    if not is_undefined(suggested):
                        field["default"] = suggested
                # Extract help text description if present
                if "description" in description:
                    field["description"] = description["description"]
    else:
        field["name"] = key

    # Handle list-type defaults - convert to first element for text fields
    # or keep as-is for multi-select fields
    if field["default"] is not None and isinstance(field["default"], (list, tuple)):
        _LOGGER.debug(
            "Field %s: converting list default %s to first element",
            field["name"],
            field["default"],
        )
        if len(field["default"]) > 0:
            field["default"] = field["default"][0]
        else:
            field["default"] = None
        _LOGGER.debug("Field %s: new default is %s", field["name"], field["default"])

    # Make label from field name (capitalize and replace underscores)
    field["label"] = field["name"].replace("_", " ").title()

    # Parse validator type
    # Handle plain Python types (e.g., str, bool, int) used directly
    if validator is bool:
        field["type"] = "checkbox"
    elif validator is str:
        field["type"] = "text"
    elif validator is int:
        field["type"] = "number"
    elif validator is float:
        field["type"] = "number"
        field["step"] = "any"
    elif isinstance(validator, type):
        # Other Python types default to text
        field["type"] = "text"
    elif hasattr(validator, "__class__"):
        validator_class = validator.__class__.__name__

        if validator_class == "In":
            # Select field with options
            field["type"] = "select"
            if hasattr(validator, "container"):
                container = validator.container
                if isinstance(container, dict):
                    # Dict mapping values to labels
                    field["options"] = [
                        {
                            "value": k,
                            "label": v,
                            "selected": k == field.get("default"),
                        }
                        for k, v in container.items()
                    ]
                elif isinstance(container, (list, tuple)):
                    # List of values
                    field["options"] = [
                        {
                            "value": v,
                            "label": str(v),
                            "selected": v == field.get("default"),
                        }
                        for v in container
                    ]
        elif validator_class == "Email":
            field["type"] = "email"
        elif validator_class == "Url":
            field["type"] = "url"
        elif validator_class == "Number":
            field["type"] = "number"
        elif validator_class == "Boolean":
            field["type"] = "checkbox"
        elif validator_class == "Password":
            field["type"] = "password"
        elif validator_class == "SelectSelector":
            # Handle SelectSelector from homeassistant.helpers.selector
            field["type"] = "select"
            _LOGGER.debug("SelectSelector found for field %s", field["name"])
            if hasattr(validator, "config"):
                config = validator.config
                _LOGGER.debug("SelectSelector config: %s", config)
                options = config.get("options", [])
                multiple = config.get("multiple", False)
                _LOGGER.debug(
                    "SelectSelector options: %s, multiple: %s", options, multiple
                )
                # Handle options as list of dicts (SelectOptionDict) or simple values
                parsed_options = []
                for opt in options:
                    if isinstance(opt, dict) and "value" in opt and "label" in opt:
                        # SelectOptionDict format: {"value": "...", "label": "..."}
                        parsed_options.append(
                            {
                                "value": opt["value"],
                                "label": opt["label"],
                                "selected": opt["value"]
                                in (field.get("default") or [])
                                if multiple
                                else opt["value"] == field.get("default"),
                            }
                        )
                    else:
                        # Simple value format
                        parsed_options.append(
                            {
                                "value": opt,
                                "label": str(opt),
                                "selected": opt in (field.get("default") or [])
                                if multiple
                                else opt == field.get("default"),
                            }
                        )
                field["options"] = parsed_options
                field["multiple"] = multiple
                _LOGGER.debug(
                    "Parsed %d options for field %s",
                    len(field["options"]),
                    field["name"],
                )
            else:
                _LOGGER.debug("SelectSelector has no config attribute")
        elif validator_class == "TextSelector":
            # Handle TextSelector from homeassistant.helpers.selector
            if hasattr(validator, "config"):
                config = validator.config
                selector_type = config.get("type", "text")
                if selector_type == "password":
                    field["type"] = "password"
                elif selector_type == "email":
                    field["type"] = "email"
                elif selector_type == "url":
                    field["type"] = "url"
                elif selector_type == "tel":
                    field["type"] = "tel"
                else:
                    field["type"] = "text"
            else:
                field["type"] = "text"
        elif validator_class == "NumberSelector":
            # Handle NumberSelector from homeassistant.helpers.selector
            field["type"] = "number"
            if hasattr(validator, "config"):
                config = validator.config
                if "min" in config:
                    field["min"] = config["min"]
                if "max" in config:
                    field["max"] = config["max"]
                if "step" in config:
                    field["step"] = config["step"]
        elif validator_class == "BooleanSelector":
            # Handle BooleanSelector from homeassistant.helpers.selector
            field["type"] = "checkbox"

    # Check for Coerce (type conversion)
    validator_class = validator.__class__.__name__ if hasattr(validator, "__class__") else ""
    if hasattr(validator, "type") and validator_class == "Coerce":
        if validator.type == int:
            field["type"] = "number"
        elif validator.type == float:
            field["type"] = "number"
            field["step"] = "any"

    # Handle dict-based selectors (e.g., selector({"select": {...}}) or {"select": {...}})
    if isinstance(validator, dict):
        # Check if this is a selector dict (has single key like "select", "text", "number", etc.)
        if len(validator) == 1:
            selector_type = list(validator.keys())[0]
            selector_config = validator[selector_type]

            if selector_type == "select":
                field["type"] = "select"
                options = selector_config.get("options", [])
                mode = selector_config.get("mode", "list")  # "list" or "dropdown"

                # Parse options
                parsed_options = []
                for opt in options:
                    if isinstance(opt, dict) and "value" in opt:
                        # Dict format: {"value": "...", "label": "..."}
                        parsed_options.append(
                            {
                                "value": opt["value"],
                                "label": opt.get("label", str(opt["value"])),
                                "selected": opt["value"] == field.get("default"),
                            }
                        )
                    else:
                        # Simple value format
                        parsed_options.append(
                            {
                                "value": opt,
                                "label": str(opt),
                                "selected": opt == field.get("default"),
                            }
                        )
                field["options"] = parsed_options
                field["mode"] = mode
                _LOGGER.debug(
                    "Dict-based select selector: %d options, mode=%s",
                    len(parsed_options),
                    mode,
                )
            elif selector_type == "boolean":
                field["type"] = "checkbox"
            elif selector_type == "text":
                text_type = selector_config.get("type", "text")
                if text_type == "password":
                    field["type"] = "password"
                elif text_type == "email":
                    field["type"] = "email"
                else:
                    field["type"] = "text"
            elif selector_type == "number":
                field["type"] = "number"
                if "min" in selector_config:
                    field["min"] = selector_config["min"]
                if "max" in selector_config:
                    field["max"] = selector_config["max"]
                if "step" in selector_config:
                    field["step"] = selector_config["step"]

    # Detect password fields by name (in addition to Password validator)
    # But don't override checkbox (boolean) types
    if field.get("type") != "checkbox":
        password_keywords = [
            "password",
            "secret",
            "token",
            "api_key",
            "credential",
            "otp",
            "key",
        ]
        field_name_lower = field["name"].lower()
        if any(keyword in field_name_lower for keyword in password_keywords):
            field["type"] = "password"

    # Clean up None values
    field = {k: v for k, v in field.items() if v is not None}

    _LOGGER.debug(
        "Parsed field %s: type=%s, default=%r",
        field["name"],
        field.get("type"),
        field.get("default"),
    )

    return field


def convert_form_value(value: str, validator, field_name: str = "") -> Any:
    """Convert a form string value to the appropriate type based on validator."""
    import voluptuous as vol

    if not isinstance(value, str):
        return value

    validator_class = validator.__class__.__name__

    # Handle plain Python types (int, float, bool, str)
    if validator is str or (validator_class == "type" and validator == str):
        return value

    # Handle empty strings for non-str types
    if value == "":
        # For numeric types, return 0 instead of None to avoid comparison issues
        if (
            validator is int
            or (validator_class == "Coerce" and hasattr(validator, "type") and validator.type == int)
        ):
            return 0
        if (
            validator is float
            or (validator_class == "Coerce" and hasattr(validator, "type") and validator.type == float)
        ):
            return 0.0
        # For string validators (cv.string), keep empty string
        if validator_class == "function":
            return ""
        return None

    if validator is int or (validator_class == "type" and validator == int):
        try:
            return int(value)
        except (ValueError, TypeError):
            return value

    if validator is float or (validator_class == "type" and validator == float):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    if validator is bool or (validator_class == "type" and validator == bool):
        return value.lower() in ("true", "1", "yes", "on")

    # Handle Coerce validators (most common for type conversion)
    if validator_class == "Coerce" and hasattr(validator, "type"):
        coerce_type = validator.type
        try:
            if coerce_type == int:
                return int(value)
            elif coerce_type == float:
                return float(value)
            elif coerce_type == bool:
                return value.lower() in ("true", "1", "yes", "on")
            else:
                return coerce_type(value)
        except (ValueError, TypeError):
            return value

    # Handle Number validator
    if validator_class == "Number":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    # Handle Boolean validator
    if validator_class == "Boolean":
        return value.lower() in ("true", "1", "yes", "on")

    # Handle Range validator (wrapper around another validator)
    if validator_class == "Range" and hasattr(validator, "schema"):
        return convert_form_value(value, validator.schema, field_name)

    # Handle All validator (composite)
    if validator_class == "All" and hasattr(validator, "validators"):
        for v in validator.validators:
            result = convert_form_value(value, v, field_name)
            if result != value:  # If conversion happened
                return result

    # Handle function validators (like cv.latitude, cv.longitude)
    # These are plain functions, not classes
    if validator_class == "function":
        # Try to infer type from field name
        field_lower = field_name.lower()
        if any(
            x in field_lower for x in ["latitude", "longitude", "lat", "lon", "lng"]
        ):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value
        elif (
            "radius" in field_lower
            or "interval" in field_lower
            or "altitude" in field_lower
        ):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value

    # Default: return as-is
    return value
