"""Minimal deprecation stub for HA const.py compatibility."""
from __future__ import annotations

class DeprecatedConstant:
    """Stub for deprecated constant wrapper."""
    def __init__(self, value, *args, **kwargs):
        self._value = value
    def __get__(self, obj, objtype=None):
        return self._value
    def __set_name__(self, owner, name):
        pass

class DeprecatedConstantEnum:
    """Stub for deprecated enum wrapper."""
    def __init__(self, enum, *args, **kwargs):
        self._enum = enum
    def __get__(self, obj, objtype=None):
        return self._enum

class EnumWithDeprecatedMembers(type):
    """Stub metaclass for enums with deprecated members."""
    pass

def all_with_deprecated_constants(*args, **kwargs):
    """Stub for all() with deprecated constants."""
    return []

def dir_with_deprecated_constants(*args, **kwargs):
    """Stub for dir() with deprecated constants."""
    return []

def check_if_deprecated_constant(*args, **kwargs):
    """Stub for checking deprecated constants."""
    pass
