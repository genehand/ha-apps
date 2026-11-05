"""Marketplace code adopted from Home Assistant core.

HACS lives on inside HA as ``homeassistant.components.marketplace`` (2026.11).
The package itself cannot be imported here -- its ``__init__`` pulls
``homeassistant.auth``, ``components.frontend``, ``components.http``,
``config_entries`` and ``loader`` -- but the leaf ``utils`` modules are
model-layer-free. They are loaded from the installed wheel by path, so upstream
stays the source of truth and nothing is copied into the shim.

Adopt a module here only when it is a leaf: no relative imports that reach the
package ``__init__``, no ``homeassistant.core`` model layer. Add the file name to
``_MARKETPLACE_LEAVES``.
"""

from functools import lru_cache
from typing import Optional

from .logging import get_logger

_LOGGER = get_logger(__name__)

# Marketplace ``utils`` modules that are safe to load on their own.
_MARKETPLACE_LEAVES = ("version",)


@lru_cache(maxsize=None)
def _load_leaf(name: str):
    """Load ``components/marketplace/utils/<name>.py`` from the wheel.

    Returns ``None`` when the wheel layout changes; every caller has a local
    fallback, so a missing leaf only loses the shared implementation.
    """
    try:
        import importlib.util
        from pathlib import Path

        import homeassistant.const as ha_const

        path = (
            Path(ha_const.__file__).parent
            / "components"
            / "marketplace"
            / "utils"
            / f"{name}.py"
        )
        spec = importlib.util.spec_from_file_location(
            f"_shack_marketplace_{name}", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOGGER.debug("Loaded Marketplace util %s from %s", name, path)
        return module
    except Exception as err:  # noqa: BLE001 - the fallback is the point
        _LOGGER.debug("Could not load Marketplace util %s: %s", name, err)
        return None


_marketplace_version = _load_leaf("version")


def _fallback_is_newer_version(left: str, right: str) -> Optional[bool]:
    """Return if ``left`` is newer than ``right`` (Marketplace semantics)."""
    from awesomeversion import (
        AwesomeVersion,
        AwesomeVersionException,
        AwesomeVersionStrategy,
    )

    try:
        left_version = AwesomeVersion(left)
        right_version = AwesomeVersion(right)
        if AwesomeVersionStrategy.UNKNOWN not in (
            left_version.strategy,
            right_version.strategy,
        ):
            return left_version > right_version
    except (AwesomeVersionException, AttributeError, KeyError):
        pass
    return None


def _fallback_is_same_or_newer_version(left: str, right: str) -> bool:
    """Return if ``left`` is the same as or newer than ``right``."""
    if left == right:
        return True
    return _fallback_is_newer_version(left, right) or False


if _marketplace_version is not None:
    is_newer_version = _marketplace_version.is_newer_version
    is_same_or_newer_version = _marketplace_version.is_same_or_newer_version
else:  # pragma: no cover - exercised only when the wheel layout changes
    is_newer_version = _fallback_is_newer_version
    is_same_or_newer_version = _fallback_is_same_or_newer_version
