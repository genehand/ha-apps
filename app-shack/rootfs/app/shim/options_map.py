"""Options map registry for select entities.

This module provides a registry for mapping raw API values to human-readable
display values for select entities. It loads translations from integration
translation files and uses them to build options_map for MQTT discovery.
"""

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any, Optional

_LOGGER = logging.getLogger(__name__)

# Cache: domain -> translation_data
_translations_cache: Dict[str, Dict[str, Any]] = {}


def load_integration_translations(
    domain: str, integration_path: Path
) -> Dict[str, Any]:
    """Load translations for an integration.

    Args:
        domain: The integration domain (e.g., "leviton_decora_smart_wifi")
        integration_path: Path to the integration directory

    Returns:
        The translations dictionary, or empty dict if not found
    """
    if domain in _translations_cache:
        return _translations_cache[domain]

    # Look for translations/en.json (or strings.json as fallback)
    translations_file = integration_path / "translations" / "en.json"
    if not translations_file.exists():
        translations_file = integration_path / "strings.json"

    if not translations_file.exists():
        _LOGGER.debug(f"No translations file found for {domain}")
        return {}

    try:
        with open(translations_file, "r", encoding="utf-8") as f:
            translations = json.load(f)
        _translations_cache[domain] = translations
        _LOGGER.debug(f"Loaded translations for {domain}")
        return translations
    except (json.JSONDecodeError, IOError) as e:
        _LOGGER.warning(f"Failed to load translations for {domain}: {e}")
        return {}


def get_select_state_translations(translations: Dict[str, Any]) -> Dict[str, str]:
    """Extract select state translations from translation data.

    Args:
        translations: The loaded translations dictionary

    Returns:
        Dictionary mapping state keys to display values
    """
    # Look for entity.select.<translation_key>.state
    # Common patterns: "entity.select.all.state", "entity.select.<key>.state"
    entity_section = translations.get("entity", {})
    select_section = entity_section.get("select", {})

    # Collect all state translations from all select entities
    state_map = {}
    for translation_key, key_data in select_section.items():
        if isinstance(key_data, dict) and "state" in key_data:
            state_map.update(key_data["state"])

    return state_map


def get_options_map_for_key(domain: str, entity_key: str) -> Dict[str, str]:
    """Get options map for an entity key from translations.

    This looks up the translations for the given domain and extracts
    the state translations that apply to the entity key.

    Args:
        domain: The integration domain
        entity_key: The entity description key (e.g., "led_bar_behavior")

    Returns:
        Dictionary mapping internal values to display values
    """
    # Get cached translations
    translations = _translations_cache.get(domain, {})
    if not translations:
        return {}

    # Get all select state translations
    state_map = get_select_state_translations(translations)

    # For now, we use all select state translations for all select entities
    # In the future, we could filter by translation_key if needed
    return state_map


def patch_select_descriptions(domain: str, module: Any, integration_path: Path) -> None:
    """Patch select entity descriptions in a module to add options_map.

    This function loads translations for the integration and injects
    options_map into select entity descriptions using the translation data.

    Args:
        domain: The integration domain
        module: The integration module that was just imported
        integration_path: Path to the integration directory
    """
    _LOGGER.debug(f"patch_select_descriptions called for {domain}")

    # Load translations first
    translations = load_integration_translations(domain, integration_path)
    if not translations:
        _LOGGER.debug(f"No translations loaded for {domain}")
        return

    # Get select state translations
    state_map = get_select_state_translations(translations)
    if not state_map:
        _LOGGER.debug(f"No select state translations found for {domain}")
        return

    _LOGGER.debug(f"Found {len(state_map)} state translations for {domain}")

    # Look for common select description variable names
    description_vars = [
        "SELECT_DESCRIPTIONS",
        "SELECT_ENTITY_DESCRIPTIONS",
        "DESCRIPTIONS",
    ]

    patched_count = 0
    modules_to_check = [module]

    # Also check submodules that might contain select descriptions (e.g., select.py, sensor.py)
    # Try to import them explicitly since they may not be loaded yet
    platform_modules = [
        "select",
        "sensor",
        "switch",
        "light",
        "fan",
        "climate",
        "number",
        "binary_sensor",
    ]
    for platform in platform_modules:
        submodule_name = f"{module.__name__}.{platform}"
        try:
            # Try to import the submodule if not already loaded
            if submodule_name not in sys.modules:
                _LOGGER.debug(f"Importing submodule {submodule_name}")
                importlib.import_module(submodule_name)

            if submodule_name in sys.modules:
                modules_to_check.append(sys.modules[submodule_name])
                _LOGGER.debug(f"Will check submodule {submodule_name}")
        except ImportError as e:
            _LOGGER.debug(f"Could not import submodule {submodule_name}: {e}")

    for check_module in modules_to_check:
        _LOGGER.debug(
            f"Checking module {check_module.__name__} for select descriptions"
        )

        for var_name in description_vars:
            if not hasattr(check_module, var_name):
                continue

            descriptions = getattr(check_module, var_name)
            if not isinstance(descriptions, (list, tuple)):
                continue

            _LOGGER.debug(
                f"Found {var_name} in {check_module.__name__} with {len(descriptions)} descriptions"
            )

            # Patch descriptions that don't already have options_map
            for desc in descriptions:
                if not hasattr(desc, "key"):
                    continue

                # Check if description already has options_map
                has_map = hasattr(desc, "options_map")
                map_value = getattr(desc, "options_map", None)

                if has_map and map_value is not None:
                    continue

                _LOGGER.debug(f"Patching {desc.key} with options_map")

                # Filter state_map to only include values that are in the entity's options
                filtered_map = None
                if hasattr(desc, "options_key"):
                    # For integrations like Leviton that use options_key
                    # We include all state translations since we don't know the exact options
                    # The SelectEntity._get_options_map will filter at runtime
                    filtered_map = state_map
                elif hasattr(desc, "options") and desc.options:
                    # Filter to only include translations for this entity's options
                    entity_options = desc.options
                    if entity_options:
                        filtered_map = {
                            k: v for k, v in state_map.items() if k in entity_options
                        }

                # If no specific options, add all state translations
                if filtered_map is None:
                    filtered_map = state_map

                # Try to patch
                try:
                    object.__setattr__(desc, "options_map", filtered_map)
                    patched_count += 1
                    _LOGGER.debug(
                        f"Patched {desc.key} with {len(filtered_map)} entries"
                    )
                except (AttributeError, TypeError) as e:
                    _LOGGER.warning(f"Could not patch options_map for {desc.key}: {e}")

            if patched_count > 0:
                _LOGGER.info(
                    f"Patched {patched_count} select descriptions for {domain}"
                )
            break  # Only patch the first matching description list


def clear_translations_cache() -> None:
    """Clear the translations cache. Used mainly for testing."""
    _translations_cache.clear()
