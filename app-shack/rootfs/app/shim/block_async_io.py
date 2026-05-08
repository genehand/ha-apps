"""Detect blocking calls in the asyncio event loop and warn.

Monkey-patches known blocking functions (time.sleep, HTTPConnection.putrequest,
glob, os.*, builtins.open, importlib, SSLContext, Path methods) to check
whether they are being called from the event loop thread. If so, a warning
with a full traceback is logged so developers can identify the culprit.

This is inspired by Home Assistant's block_async_io module
(homeassistant/block_async_io.py) but is warn-only: it never raises
RuntimeError. The blocking function always executes normally after the
warning is emitted.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
import functools
import glob
from http.client import HTTPConnection
import importlib
import linecache
import logging
import os
from pathlib import Path
from ssl import SSLContext
import sys
import threading
import time
import traceback
from typing import Any

from .logging import get_logger

_LOGGER = get_logger(__name__)

_IN_TESTS = "unittest" in sys.modules

ALLOWED_FILE_PREFIXES = ("/proc",)

# Set of previously reported blocking calls (filename, lineno)
_PREVIOUSLY_REPORTED: set[tuple[str, int]] = set()


def _get_line_from_cache(filename: str, lineno: int) -> str:
    """Get source line from cache or read from file."""
    return (linecache.getline(filename, lineno) or "?").strip()


# ---------------------------------------------------------------------------
# Allow-listed checks (same predicates as Home Assistant)
# ---------------------------------------------------------------------------


def _check_import_call_allowed(mapped_args: dict[str, Any]) -> bool:
    """Skip the check if the module is already imported.

    Also allows first-time imports from uvicorn's own importer so that
    the startup ``importlib.import_module("uvicorn.protocols...")``
    calls that happen on the event loop don't produce noisy warnings.
    """
    if (args := mapped_args.get("args")) and args[0] in sys.modules:
        return True
    with suppress(ValueError):
        # Frame 0: this function
        # Frame 1: warn_for_blocking_call
        # Frame 2: protected_loop_func (the wrapper)
        # Frame 3: the original caller (e.g. uvicorn/importer.py)
        caller = sys._getframe(3).f_code.co_filename  # noqa
        if caller.endswith("uvicorn/importer.py"):
            return True
    return False


def _check_file_allowed(mapped_args: dict[str, Any]) -> bool:
    """Skip the check if the path starts with an allowed prefix (e.g. /proc).

    Also allows file reads from Jinja2's template loader so that template
    rendering on the event loop doesn't produce noisy warnings.
    """
    args = mapped_args["args"]
    path = args[0] if type(args[0]) is str else str(args[0])
    if path.startswith(ALLOWED_FILE_PREFIXES):
        return True
    with suppress(ValueError):
        # Frame 0: this function
        # Frame 1: warn_for_blocking_call
        # Frame 2: protected_loop_func (the wrapper)
        # Frame 3: the original caller (e.g. jinja2/loaders.py)
        caller = sys._getframe(3).f_code.co_filename  # noqa
        if "jinja2" in caller:
            return True
    return False


def _check_sleep_call_allowed(mapped_args: dict[str, Any]) -> bool:
    """Skip the check if the caller is pydevd (debugger)."""
    with suppress(ValueError):
        # Guard against frame depth issues
        caller = sys._getframe(4).f_code.co_filename  # noqa
        return caller.endswith("pydevd.py")
    return False


def _check_load_verify_locations_call_allowed(mapped_args: dict[str, Any]) -> bool:
    """Skip the check if only cadata is passed (no I/O)."""
    kwargs = mapped_args.get("kwargs")
    return bool(kwargs and len(kwargs) == 1 and "cadata" in kwargs)


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class BlockingCall:
    """Metadata about a function that should not be called on the event loop."""

    original_func: Callable
    object: object
    function: str
    check_allowed: Callable[[dict[str, Any]], bool] | None
    skip_for_tests: bool


# ---------------------------------------------------------------------------
# Detection and warning logic
# ---------------------------------------------------------------------------


def _dev_help_message(what: str) -> str:
    """Generate a help message directing developers to best practices."""
    return (
        "For developers, please see "
        "https://developers.home-assistant.io/docs/asyncio_blocking_operations/"
        f"#{what.replace('.', '')}"
    )


def warn_for_blocking_call(
    func: Callable[..., Any],
    check_allowed: Callable[[dict[str, Any]], bool] | None = None,
    **mapped_args: Any,
) -> None:
    """Warn if *func* is called inside the event loop (never raises).

    Walks the call stack to find the caller, logs a ``WARNING`` (first
    occurrence) or ``DEBUG`` (subsequent identical calls), and returns
    normally.  The blocking call is **not** prevented.
    """
    if check_allowed is not None and check_allowed(mapped_args):
        return

    # Frame 0: this function
    # Frame 1: protect_loop wrapper
    # Frame 2: the user/integration code that triggered the call (offender)
    try:
        offender_frame = sys._getframe(2)
    except ValueError:
        return  # stack is too shallow, nothing we can do

    offender_filename = offender_frame.f_code.co_filename
    offender_lineno = offender_frame.f_lineno
    offender_line = _get_line_from_cache(offender_filename, offender_lineno)

    report_key = (offender_filename, offender_lineno)
    was_reported = report_key in _PREVIOUSLY_REPORTED
    _PREVIOUSLY_REPORTED.add(report_key)

    if was_reported:
        _LOGGER.debug(
            "Detected blocking call to %s with args %s "
            "inside the event loop at %s, line %s: %s. "
            "This is causing stability issues.\n%s",
            func.__name__,
            mapped_args.get("args"),
            offender_filename,
            offender_lineno,
            offender_line,
            _dev_help_message(func.__name__),
        )
    else:
        _LOGGER.warning(
            "Detected blocking call to %s with args %s "
            "inside the event loop at %s, line %s: %s. "
            "This is causing stability issues.\n%s\n"
            "Traceback (most recent call last):\n%s",
            func.__name__,
            mapped_args.get("args"),
            offender_filename,
            offender_lineno,
            offender_line,
            _dev_help_message(func.__name__),
            "".join(traceback.format_stack(f=offender_frame)),
        )


def protect_loop[**_P, _R](
    func: Callable[_P, _R],
    loop_thread_id: int,
    check_allowed: Callable[[dict[str, Any]], bool] | None = None,
) -> Callable[_P, _R]:
    """Wrap *func* so that calls from the event loop thread produce a warning.

    The wrapped function always passes through to the original *func* after
    (optionally) logging a warning.
    """

    @functools.wraps(func)
    def protected_loop_func(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if threading.get_ident() == loop_thread_id:
            warn_for_blocking_call(
                func,
                check_allowed=check_allowed,
                args=args,
                kwargs=kwargs,
            )
        return func(*args, **kwargs)

    return protected_loop_func


# ---------------------------------------------------------------------------
# Functions to monitor
# ---------------------------------------------------------------------------

_BLOCKING_CALLS: tuple[BlockingCall, ...] = (
    BlockingCall(
        original_func=HTTPConnection.putrequest,
        object=HTTPConnection,
        function="putrequest",
        check_allowed=None,
        skip_for_tests=False,
    ),
    BlockingCall(
        original_func=time.sleep,
        object=time,
        function="sleep",
        check_allowed=_check_sleep_call_allowed,
        skip_for_tests=False,
    ),
    BlockingCall(
        original_func=glob.glob,
        object=glob,
        function="glob",
        check_allowed=None,
        skip_for_tests=False,
    ),
    BlockingCall(
        original_func=glob.iglob,
        object=glob,
        function="iglob",
        check_allowed=None,
        skip_for_tests=False,
    ),
    BlockingCall(
        original_func=os.walk,
        object=os,
        function="walk",
        check_allowed=None,
        skip_for_tests=False,
    ),
    BlockingCall(
        original_func=os.listdir,
        object=os,
        function="listdir",
        check_allowed=None,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=os.scandir,
        object=os,
        function="scandir",
        check_allowed=None,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=builtins.open,
        object=builtins,
        function="open",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=importlib.import_module,
        object=importlib,
        function="import_module",
        check_allowed=_check_import_call_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=SSLContext.load_default_certs,
        object=SSLContext,
        function="load_default_certs",
        check_allowed=None,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=SSLContext.load_verify_locations,
        object=SSLContext,
        function="load_verify_locations",
        check_allowed=_check_load_verify_locations_call_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=SSLContext.load_cert_chain,
        object=SSLContext,
        function="load_cert_chain",
        check_allowed=None,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=SSLContext.set_default_verify_paths,
        object=SSLContext,
        function="set_default_verify_paths",
        check_allowed=None,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=Path.open,
        object=Path,
        function="open",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=Path.read_text,
        object=Path,
        function="read_text",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=Path.read_bytes,
        object=Path,
        function="read_bytes",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=Path.write_text,
        object=Path,
        function="write_text",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
    BlockingCall(
        original_func=Path.write_bytes,
        object=Path,
        function="write_bytes",
        check_allowed=_check_file_allowed,
        skip_for_tests=True,
    ),
)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _BlockedCalls:
    """Tracks which calls have been patched (to prevent double-enable)."""

    calls: set[BlockingCall]


_BLOCKED_CALLS = _BlockedCalls(set())

_original_functions: dict[BlockingCall, Callable] = {}


def enable() -> None:
    """Monkey-patch known blocking functions with warning wrappers.

    Must be called once from the event loop thread during startup.
    Raises ``RuntimeError`` if already enabled (unless running tests, in
    which case it silently no-ops so that multiple ``ImportPatcher``
    instances created during a test session don't crash).
    """
    calls = _BLOCKED_CALLS.calls
    if calls:
        if _IN_TESTS:
            return
        raise RuntimeError("Blocking call detection is already enabled")

    loop_thread_id = threading.get_ident()
    for blocking_call in _BLOCKING_CALLS:
        if _IN_TESTS and blocking_call.skip_for_tests:
            continue

        protected_function = protect_loop(
            blocking_call.original_func,
            check_allowed=blocking_call.check_allowed,
            loop_thread_id=loop_thread_id,
        )
        setattr(blocking_call.object, blocking_call.function, protected_function)
        calls.add(blocking_call)

    _LOGGER.debug("Blocking call detection enabled (%d functions patched)", len(calls))


def disable() -> None:
    """Restore all original functions (mainly for testing)."""
    calls = _BLOCKED_CALLS.calls
    if not calls:
        return

    for blocking_call in list(calls):
        original = getattr(blocking_call.object, blocking_call.function, None)
        # Only restore if it's still our wrapper (check via __wrapped__)
        if original is not None and hasattr(original, "__wrapped__"):
            setattr(blocking_call.object, blocking_call.function, blocking_call.original_func)
        calls.discard(blocking_call)

    _PREVIOUSLY_REPORTED.clear()
    _LOGGER.debug("Blocking call detection disabled")
