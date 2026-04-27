"""Integration translation loading utilities.

Loads and applies translation strings from integration's translation files
(en.json / strings.json) for use in config flow forms.
"""

import json
import logging
from typing import Any, Dict, List

_LOGGER = logging.getLogger(__name__)


def load_integration_translations(integration_manager, domain: str) -> dict:
    """Load translations for an integration.

    Args:
        integration_manager: The integration manager instance
        domain: The integration domain

    Returns:
        The translations dictionary, or empty dict if not found
    """
    try:
        # Use the integration manager to find the integration path
        integration_path = integration_manager.get_integration_path(domain)

        if not integration_path:
            return {}

        # Look for translations/en.json (or strings.json as fallback)
        translations_file = integration_path / "translations" / "en.json"
        if not translations_file.exists():
            translations_file = integration_path / "strings.json"

        if not translations_file.exists():
            return {}

        with open(translations_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        _LOGGER.debug("Failed to load translations for %s: %s", domain, e)
        return {}


def apply_field_translations(
    fields: List[Dict[str, Any]], translations: dict, step_id: str
) -> None:
    """Apply field labels and descriptions from translations.

    Args:
        fields: List of field dictionaries to modify (in-place)
        translations: The loaded translations dictionary
        step_id: The current config flow step ID (e.g., "user", "reconfigure")
    """
    # Get the config section for this step
    config_section = translations.get("config", {})
    steps = config_section.get("step", {})
    step_data = steps.get(step_id, {})

    # Get field labels from data.{field_name}
    data_labels = step_data.get("data", {})
    # Get field descriptions from data_description.{field_name}
    data_descriptions = step_data.get("data_description", {})

    # Get selector translations for options
    selector_translations = translations.get("selector", {})

    for field in fields:
        field_name = field.get("name", "")
        if not field_name:
            continue

        # Apply label if available in translations
        if field_name in data_labels:
            field["label"] = data_labels[field_name]

        # Apply description if available in translations
        if field_name in data_descriptions:
            field["description"] = data_descriptions[field_name]

        # Apply selector option translations for select fields
        if field.get("type") == "select" and field_name in selector_translations:
            selector_config = selector_translations[field_name]
            option_labels = selector_config.get("options", {})
            if option_labels and field.get("options"):
                # Map the option values to their translated labels
                for option in field["options"]:
                    option_value = option.get("value", "")
                    if option_value in option_labels:
                        option["label"] = option_labels[option_value]
