"""Minimal deprecation stub for HA const.py compatibility.

Mirrors the runtime behaviour of homeassistant.helpers.deprecation for the
subset used by ha_fetched/const.py's module-level __getattr__/__dir__/__all__
machinery (PEP 562). Deprecation notices are logged at debug level rather than
printed, but attribute lookup, dir() and __all__ behave like upstream so
introspection of homeassistant.const works.
"""
from __future__ import annotations

import logging

_PREFIX_DEPRECATED = "_DEPRECATED_"


class DeprecatedConstant:
    """Stub for deprecated constant wrapper (matches upstream attributes)."""

    def __init__(self, value, replacement=None, breaks_in_ha_version=None, *args, **kwargs):
        self.value = value
        self.replacement = replacement
        self.breaks_in_ha_version = breaks_in_ha_version

    def __get__(self, obj, objtype=None):
        return self.value

    def __set_name__(self, owner, name):
        pass


class DeprecatedConstantEnum:
    """Stub for deprecated enum wrapper."""

    def __init__(self, enum, breaks_in_ha_version=None, *args, **kwargs):
        self.enum = enum
        self.breaks_in_ha_version = breaks_in_ha_version

    def __get__(self, obj, objtype=None):
        return self.enum

    def __set_name__(self, owner, name):
        pass


class EnumWithDeprecatedMembers(type):
    """Stub metaclass for enums with deprecated members."""

    pass


def check_if_deprecated_constant(name, module_globals):
    """Check if the not found name is a deprecated constant.

    If it is, return the constant's value (logging a debug deprecation notice).
    Otherwise raise AttributeError per PEP 562.
    """
    module_name = module_globals.get("__name__")
    deprecated_const = module_globals.get(_PREFIX_DEPRECATED + name)
    if deprecated_const is None:
        raise AttributeError(f"Module {module_name!r} has no attribute {name!r}")

    value = None
    replacement = None
    breaks_in_ha_version = None
    if isinstance(deprecated_const, DeprecatedConstant):
        value = deprecated_const.value
        replacement = deprecated_const.replacement
        breaks_in_ha_version = deprecated_const.breaks_in_ha_version
    elif isinstance(deprecated_const, DeprecatedConstantEnum):
        value = deprecated_const.enum
        replacement = (
            f"{deprecated_const.enum.__class__.__name__}.{deprecated_const.enum.name}"
        )
        breaks_in_ha_version = deprecated_const.breaks_in_ha_version

    if value is None or replacement is None:
        raise AttributeError(
            f"Value of {_PREFIX_DEPRECATED}{name} is an instance of "
            f"{type(deprecated_const)} but an instance of DeprecatedConstant or "
            "DeprecatedConstantEnum is required"
        )

    logging.getLogger(module_name or __name__).debug(
        "Accessed deprecated constant %r (use %r instead, breaks in %s)",
        name,
        replacement,
        breaks_in_ha_version,
    )
    return value


def dir_with_deprecated_constants(module_globals_keys):
    """Return dir() with deprecated constants exposed by their public names."""
    return module_globals_keys + [
        name.removeprefix(_PREFIX_DEPRECATED)
        for name in module_globals_keys
        if name.startswith(_PREFIX_DEPRECATED)
    ]


def all_with_deprecated_constants(module_globals):
    """Generate an __all__ list including deprecated constants."""
    module_globals_keys = list(module_globals)
    return [itm for itm in module_globals_keys if not itm.startswith("_")] + [
        name.removeprefix(_PREFIX_DEPRECATED)
        for name in module_globals_keys
        if name.startswith(_PREFIX_DEPRECATED)
    ]
