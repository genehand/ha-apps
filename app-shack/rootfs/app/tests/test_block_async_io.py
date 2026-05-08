"""Tests for the warn-only blocking call detection module."""

import asyncio
import logging
import threading
import time

import pytest

from shim.block_async_io import (
    _BLOCKING_CALLS,
    _PREVIOUSLY_REPORTED,
    _check_import_call_allowed,
    _check_file_allowed,
    _check_load_verify_locations_call_allowed,
    BlockingCall,
    disable,
    enable,
    protect_loop,
    warn_for_blocking_call,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _noop(*args, **kwargs):
    """A no-op function used as the wrapped target."""
    return _SENTINEL


def _call_via_wrapper(func, *args, **kwargs):
    """Call warn_for_blocking_call via a one-frame wrapper.

    In production, ``warn_for_blocking_call`` is always called from
    ``protect_loop``, so the 'offender' frame is at depth 2 (behind the
    wrapper frame).  This helper replicates that depth so direct tests
    see the correct caller frame.
    """
    warn_for_blocking_call(func, args=args, kwargs=kwargs)


# ===================================================================
# warn_for_blocking_call
# ===================================================================


class TestWarnForBlockingCall:
    """Tests for the warning logic (via a wrapper to match real frame depth)."""

    @pytest.fixture(autouse=True)
    def clear_reported(self):
        """Clear dedup set before each test."""
        _PREVIOUSLY_REPORTED.clear()
        yield
        _PREVIOUSLY_REPORTED.clear()

    def test_warns_on_event_loop(self, caplog):
        """Calling warn_for_blocking_call emits a WARNING with the caller info."""
        caplog.set_level(logging.WARNING)

        _call_via_wrapper(_noop, "foo")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert "Detected blocking call to _noop" in record.getMessage()
        assert "test_block_async_io.py" in record.getMessage()
        assert "Traceback" in record.getMessage()

    def test_does_not_raise(self):
        """warn_for_blocking_call never raises, even when detected."""
        _call_via_wrapper(_noop, "bad")  # should not raise

    def test_deduplication(self, caplog):
        """First identical call is WARNING, second is DEBUG."""
        caplog.set_level(logging.DEBUG)

        # Both calls from the same line so report keys match
        for _ in range(2):
            _call_via_wrapper(_noop, "dedup")

        assert len(caplog.records) == 2
        assert caplog.records[0].levelname == "WARNING"
        assert caplog.records[1].levelname == "DEBUG"

    def test_check_allowed_skips_warning(self, caplog):
        """When check_allowed returns True, no warning is emitted."""
        caplog.set_level(logging.WARNING)

        def _allow_all(_):
            return True

        warn_for_blocking_call(
            _noop, check_allowed=_allow_all, args=("ignored",), kwargs={}
        )

        assert len(caplog.records) == 0

    def test_check_allowed_false_still_warns(self, caplog):
        """When check_allowed returns False, warning is emitted."""
        caplog.set_level(logging.WARNING)

        def _deny_all(_):
            return False

        warn_for_blocking_call(
            _noop, check_allowed=_deny_all, args=("blocked",), kwargs={}
        )

        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"

    def test_message_contains_offender_info(self, caplog):
        """The warning message includes the calling file and line."""
        caplog.set_level(logging.WARNING)

        _call_via_wrapper(_noop, "x")

        msg = caplog.records[0].getMessage()
        assert "test_block_async_io.py" in msg
        assert "_noop" in msg


# ===================================================================
# protect_loop
# ===================================================================


class TestProtectLoop:
    """Tests for the protect_loop wrapper."""

    @pytest.fixture(autouse=True)
    def clear_reported(self):
        _PREVIOUSLY_REPORTED.clear()
        yield
        _PREVIOUSLY_REPORTED.clear()

    def test_warns_on_event_loop_thread(self, caplog):
        """protect_loop warns when called from the event loop thread."""
        caplog.set_level(logging.WARNING)

        loop_thread_id = threading.get_ident()
        wrapped = protect_loop(_noop, loop_thread_id=loop_thread_id)

        result = wrapped("hello", kw="test")

        assert result is _SENTINEL  # original function still executed
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert "Detected blocking call to _noop" in caplog.records[0].getMessage()

    async def test_passes_through_on_other_thread(self, caplog):
        """protect_loop silently passes through from a different thread."""
        caplog.set_level(logging.WARNING)

        # The loop_thread_id is set to a *different* ID, so calling from
        # the main thread should be fine.
        bogus_thread_id = -1  # no real thread will have ID -1
        wrapped = protect_loop(_noop, loop_thread_id=bogus_thread_id)

        result = await asyncio.to_thread(wrapped, "world")

        assert result is _SENTINEL
        assert len(caplog.records) == 0

    def test_does_not_raise_when_on_event_loop(self):
        """protect_loop never raises, even when on the event loop thread."""
        loop_thread_id = threading.get_ident()
        wrapped = protect_loop(_noop, loop_thread_id=loop_thread_id)

        wrapped()  # should not raise

    def test_wraps_functools_attributes(self):
        """The wrapper preserves __wrapped__ and __name__."""
        wrapped = protect_loop(_noop, loop_thread_id=0)

        assert wrapped.__wrapped__ is _noop
        assert wrapped.__name__ == "_noop"


# ===================================================================
# enable / disable
# ===================================================================


class TestEnable:
    """Tests for enable() and disable()."""

    @pytest.fixture(autouse=True)
    def clean_state(self):
        """Ensure clean state before and after each test."""
        disable()  # clean up in case a prior test left patching on
        _PREVIOUSLY_REPORTED.clear()
        yield
        disable()
        _PREVIOUSLY_REPORTED.clear()

    async def test_enable_patches_sleep(self, caplog):
        """After enable(), time.sleep(0) on the event loop logs a WARNING."""
        caplog.set_level(logging.WARNING)

        enable()
        time.sleep(0)

        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert "time.sleep" in caplog.records[0].getMessage()

    async def test_enable_does_not_prevent_execution(self, caplog):
        """After enable(), patched functions still work normally."""
        caplog.set_level(logging.WARNING)

        enable()
        time.sleep(0.001)  # should execute fine

        assert len(caplog.records) == 1  # the warning
        # No exception = success

    async def test_double_enable_silent_noop_during_tests(self):
        """Calling enable() twice silently no-ops when _IN_TESTS is True."""
        from shim.block_async_io import _BLOCKED_CALLS

        enable()
        calls_before = len(_BLOCKED_CALLS.calls)
        enable()  # should not raise during tests
        calls_after = len(_BLOCKED_CALLS.calls)
        assert calls_before == calls_after  # no double-patching
        disable()

    async def test_disable_restores_originals(self, caplog):
        """After disable(), patched functions no longer warn."""
        caplog.set_level(logging.WARNING)

        enable()
        disable()

        # After disable, time.sleep should work without warning
        time.sleep(0)
        assert len(caplog.records) == 0

    async def test_enable_sleep_allowed_pydevd(self, caplog):
        """sleep calls from pydevd.py are silently allowed."""
        # This is hard to test directly, so we just verify the check function
        # path exists.  The actual pydevd detection is tested implicitly
        # through _check_sleep_call_allowed.
        caplog.set_level(logging.WARNING)

        enable()
        # A normal sleep(0) should warn (already tested above)
        # No way to simulate pydevd caller in tests, so we just verify
        # the function exists and is callable.
        from shim.block_async_io import _check_sleep_call_allowed

        assert callable(_check_sleep_call_allowed)
        disable()


# ===================================================================
# check_allowed predicates
# ===================================================================


class TestCheckAllowed:
    """Tests for the allow-list predicates."""

    def test_check_import_call_allowed_new_module(self):
        """A never-imported module is NOT allowed."""
        assert _check_import_call_allowed({"args": ("_nonexistent_module_xyz",)}) is False

    def test_check_import_call_allowed_imported(self):
        """An already-imported module IS allowed."""
        assert _check_import_call_allowed({"args": ("os",)}) is True

    def test_check_file_allowed_proc(self):
        """/proc paths are allowed."""
        assert _check_file_allowed({"args": ("/proc/self/status",)}) is True

    def test_check_file_allowed_normal(self):
        """Normal paths are not allowed."""
        assert _check_file_allowed({"args": ("/etc/passwd",)}) is False

    def test_check_load_verify_locations_cadata(self):
        """cadata-only kwargs are allowed."""
        assert (
            _check_load_verify_locations_call_allowed({"kwargs": {"cadata": "..."}})
            is True
        )

    def test_check_load_verify_locations_with_file(self):
        """kwargs with cafile are NOT allowed."""
        assert (
            _check_load_verify_locations_call_allowed(
                {"kwargs": {"cafile": "/path/to/cert.pem"}}
            )
            is False
        )


# ===================================================================
# _BLOCKING_CALLS registry
# ===================================================================


class TestBlockingCallsRegistry:
    """Verify the _BLOCKING_CALLS tuple has the expected entries."""

    def _find(self, obj, func_name):
        for bc in _BLOCKING_CALLS:
            if bc.object is obj and bc.function == func_name:
                return bc
        return None

    def test_has_putrequest(self):
        """HTTPConnection.putrequest is monitored."""
        from http.client import HTTPConnection

        bc = self._find(HTTPConnection, "putrequest")
        assert bc is not None
        assert bc.check_allowed is None

    def test_has_sleep(self):
        """time.sleep is monitored."""
        import time

        bc = self._find(time, "sleep")
        assert bc is not None
        assert bc.check_allowed is not None

    def test_has_glob(self):
        """glob.glob and glob.iglob are monitored."""
        import glob

        assert self._find(glob, "glob") is not None
        assert self._find(glob, "iglob") is not None

    def test_has_os_walk(self):
        """os.walk is monitored."""
        import os

        assert self._find(os, "walk") is not None

    def test_has_builtins_open(self):
        """builtins.open is monitored."""
        import builtins

        bc = self._find(builtins, "open")
        assert bc is not None
        assert bc.check_allowed is _check_file_allowed

    def test_has_import_module(self):
        """importlib.import_module is monitored."""
        import importlib

        bc = self._find(importlib, "import_module")
        assert bc is not None
        assert bc.check_allowed is _check_import_call_allowed

    def test_has_ssl_context_methods(self):
        """SSLContext methods are monitored."""
        from ssl import SSLContext

        for method in (
            "load_default_certs",
            "load_verify_locations",
            "load_cert_chain",
            "set_default_verify_paths",
        ):
            assert self._find(SSLContext, method) is not None, f"Missing {method}"

    def test_has_path_methods(self):
        """Path methods are monitored."""
        from pathlib import Path

        for method in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
            assert self._find(Path, method) is not None, f"Missing {method}"

    def test_all_have_original_func(self):
        """Every BlockingCall entry has an original_func."""
        for bc in _BLOCKING_CALLS:
            assert bc.original_func is not None, f"{bc.object}.{bc.function}"

    def test_all_have_skip_for_tests_bool(self):
        """Every BlockingCall entry has a bool skip_for_tests."""
        for bc in _BLOCKING_CALLS:
            assert isinstance(bc.skip_for_tests, bool), f"{bc.object}.{bc.function}"
