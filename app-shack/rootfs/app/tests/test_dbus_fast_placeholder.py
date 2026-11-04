"""Tests for the import-only ``dbus_fast`` placeholder (non-Linux hosts)."""

import sys
import types

import pytest

from shim.dbus_fast_placeholder import _PlaceholderFinder, install

pytestmark = pytest.mark.skipif(
    sys.platform == "linux",
    reason="on linux the real dbus_fast is installed (uv.lock, via bleak)",
)


def _finders():
    return [finder for finder in sys.meta_path if isinstance(finder, _PlaceholderFinder)]


def test_install_registers_finder():
    """install() puts the placeholder finder on sys.meta_path."""
    install()
    assert _finders()


def test_install_is_idempotent():
    """A second install() with the finder already present is a no-op."""
    install()
    before = len(_finders())
    assert install() is False
    assert len(_finders()) == before


def test_package_and_submodules_import():
    """Everything bleak's BlueZ backend imports resolves."""
    install()

    import dbus_fast
    from dbus_fast.aio import MessageBus
    from dbus_fast.aio.message_bus import MessageBus as MessageBusFromModule
    from dbus_fast.auth import AuthExternal
    from dbus_fast.constants import BusType, ErrorType, MessageType
    from dbus_fast.errors import InvalidObjectPathError
    from dbus_fast.message import Message
    from dbus_fast.service import ServiceInterface, dbus_property, method
    from dbus_fast.signature import Variant
    from dbus_fast.validators import assert_object_path_valid

    for name in (
        BusType,
        ErrorType,
        MessageType,
        Message,
        MessageBus,
        MessageBusFromModule,
        AuthExternal,
        InvalidObjectPathError,
        ServiceInterface,
        Variant,
        dbus_property,
        method,
        assert_object_path_valid,
    ):
        assert name is not None
    assert dbus_fast.PropertyAccess is not None


def test_base_classes_and_decorators_work():
    """Module-level use is what has to survive: subclassing and decorators."""
    install()

    import dbus_fast
    from dbus_fast.aio.message_bus import MessageBus
    from dbus_fast.constants import MessageType
    from dbus_fast.service import ServiceInterface, dbus_property

    class Connection(MessageBus):
        """Subclass of a placeholder base class."""

    assert Connection() is not None

    class Interface(ServiceInterface):
        """Class using the placeholder property decorator."""

        @dbus_property(access=dbus_fast.PropertyAccess.READ)
        def name(self) -> str:
            return "shack"

    assert Interface().name() == "shack"

    # Enum-ish members are materialised on demand and cached.
    assert MessageType.METHOD is MessageType.METHOD


def test_attribute_errors_stay_attribute_errors():
    """Dunder lookups raise AttributeError (module protocols rely on that)."""
    install()

    import dbus_fast

    with pytest.raises(AttributeError):
        dbus_fast.__something__


def test_empty_response_scan_skips_non_exceptions():
    """A module answering every attribute must not fool the scan.

    The placeholder modules (and unittest mocks) report any attribute, so
    ``_find_empty_response_exception`` has to check what it got back, or
    aiohttp's json() patch ends up raising ``TypeError: exceptions must derive
    from BaseException``.
    """
    from shim.import_patch import _find_empty_response_exception

    fake = types.ModuleType("fake_empty_response_module")
    fake.EmptyResponseException = "not raisable"
    saved = sys.modules.get("fake_empty_response_module")
    sys.modules["fake_empty_response_module"] = fake
    try:
        found = _find_empty_response_exception()
    finally:
        if saved is None:
            sys.modules.pop("fake_empty_response_module", None)
        else:
            sys.modules["fake_empty_response_module"] = saved

    assert found != "not raisable"
    assert found is None or (isinstance(found, type) and issubclass(found, BaseException))
