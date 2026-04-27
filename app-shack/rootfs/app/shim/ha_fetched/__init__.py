"""Auto-fetched Home Assistant compatibility files.

This package contains files fetched from Home Assistant 2026.4.4.

Files fetched:
- const.py: Core constants
- exceptions.py: Exception classes
- util/: Utility modules

Run `python3 fetch_ha_files.py` to update to latest HA release.
"""


# Re-export main modules for convenience
# These are loaded lazily to avoid circular imports during import_patch.py setup
def __getattr__(name):
    """Lazy module loading to avoid circular import issues."""
    if name == "const":
        from . import const
        return const
    elif name == "exceptions":
        from . import exceptions
        return exceptions
    elif name == "util":
        try:
            from . import util
            return util
        except ImportError:
            pass
    elif name == "generated":
        from . import generated
        return generated
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__version__ = "2026.4.4"