"""Import-only placeholder for ``dbus_fast`` on non-Linux hosts.

``dbus_fast`` is the D-Bus client behind bleak's BlueZ backend
(``bleak.backends.bluezdbus``), and it is Linux-only: its wheels are
manylinux/musllinux, and a Linux-only marker installs it through ``bleak``. An
integration that imports that backend unconditionally -- meross_lan 6.x does it
at module import time in ``merossclient/client/bluetooth.py`` -- therefore cannot
be imported at all on a macOS dev box, which kills the *whole* integration even
for its Wi-Fi/MQTT devices.

Nothing here can make BLE work off Linux (there is no BlueZ to talk to), so this
module installs the smallest thing that lets the import succeed: a placeholder
``dbus_fast`` package whose every attribute is an empty, subclassable class. That
is enough for module-level use -- base classes, decorators, enum-ish members,
type annotations -- and constant-only attributes cost nothing. Anything that
actually calls into D-Bus is unreachable on this host anyway.

Only installed when the platform is not Linux *and* the real package is missing.
On Linux a missing ``dbus_fast`` means the venv lost a locked dependency (it is
in ``uv.lock`` through ``bleak``), and a placeholder would silently disable BLE
where it could have worked.
"""

import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from typing import Any, Optional

from .logging import get_logger

_LOGGER = get_logger(__name__)

PACKAGE = 'dbus_fast'


class _PlaceholderMeta(type):
    """Metaclass so attribute access yields more placeholders.

    ``dbus_fast.constants.MessageType.METHOD`` and friends are read at import
    time by bleak; each read materialises a placeholder class and caches it.
    """

    def __getattr__(cls, name: str):
        if name.startswith('__'):
            raise AttributeError(name)
        value = _make_placeholder(name)
        setattr(cls, name, value)
        return value


class _Placeholder(metaclass=_PlaceholderMeta):
    """Base for placeholders: accepts any constructor arguments."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        """Support decorator use (``@dbus_property(access=...)``)."""
        if len(args) == 1 and not kwargs and callable(args[0]):
            return args[0]
        return self


def _make_placeholder(name: str) -> type:
    """Create (and let the caller cache) a placeholder class named *name*."""
    return _PlaceholderMeta(name, (_Placeholder,), {})


class _PlaceholderModule(types.ModuleType):
    """A module whose attributes are placeholder classes, cached on first use."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith('__'):
            raise AttributeError(name)
        value = _make_placeholder(f'{self.__name__}.{name}')
        setattr(self, name, value)
        return value


class _PlaceholderFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``dbus_fast`` and every ``dbus_fast.*`` submodule to a placeholder."""

    def find_spec(
        self, fullname: str, path: Optional[list[str]] = None, target: Any = None
    ) -> Optional[importlib.machinery.ModuleSpec]:
        if fullname == PACKAGE or fullname.startswith(PACKAGE + '.'):
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        module = _PlaceholderModule(spec.name)
        # Every placeholder is a package, so arbitrary submodules resolve here
        # instead of raising ModuleNotFoundError.
        module.__path__ = []  # type: ignore[attr-defined]
        module.__doc__ = (
            f'Import-only placeholder for {spec.name} (see '
            'shim/dbus_fast_placeholder.py)'
        )
        return module

    def exec_module(self, module: types.ModuleType) -> None:
        pass


def install() -> bool:
    """Install the placeholder finder; return True when it was installed.

    Idempotent, and a no-op on Linux or wherever the real package exists.
    """
    if sys.platform == 'linux':
        return False
    if any(isinstance(finder, _PlaceholderFinder) for finder in sys.meta_path):
        return False
    if importlib.util.find_spec(PACKAGE) is not None:
        return False

    sys.meta_path.append(_PlaceholderFinder())
    _LOGGER.warning(
        "dbus_fast isn't available on %s (Linux-only). "
        "BLE devices are unavailable in this environment.",
        sys.platform,
    )
    return True
