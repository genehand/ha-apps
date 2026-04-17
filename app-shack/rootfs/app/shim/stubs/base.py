"""Base utilities for creating Home Assistant stub modules."""

import sys
import types
from typing import Any, Dict, List, Optional, Set, Tuple, Union


def make_module(name: str) -> types.ModuleType:
    """Create a new module and register it in sys.modules."""
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def make_submodule(parent: types.ModuleType, name: str, full_name: str) -> types.ModuleType:
    """Create a submodule and attach it to parent."""
    module = types.ModuleType(full_name)
    setattr(parent, name, module)
    sys.modules[full_name] = module
    return module


def simple_method(*args, **kwargs) -> Any:
    """A simple method that accepts anything and returns None."""
    return None


def simple_class_factory(name: str, bases: Tuple = (), attrs: Optional[Dict] = None) -> type:
    """Create a simple class with the given name and attributes."""
    if attrs is None:
        attrs = {}
    return type(name, bases, attrs)
