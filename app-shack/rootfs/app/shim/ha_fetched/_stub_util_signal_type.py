"""Minimal SignalType stub for HA const.py compatibility."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

class SignalType[_DataT: Mapping[str, Any] = Mapping[str, Any]](str):
    """Stub for SignalType - generic str subclass."""
    pass
