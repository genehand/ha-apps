"""Minimal websocket_api stub for HACS compatibility."""
from __future__ import annotations

def async_register_command(*args, **kwargs):
    """Stub for async_register_command."""
    pass

def websocket_command(*args, **kwargs):
    """Stub decorator for websocket_command."""
    def decorator(func):
        return func
    return decorator
