"""Regression tests for GitHub's oversized response headers.

GitHub sends a ~9.2 KiB ``Content-Security-Policy`` header on ``github.com``
responses. aiohttp's default parser limit (8190 bytes per header field) rejects
the response *before* the redirect to codeload.github.com can be followed:

    ClientResponseError: 400, message='Got more than 8190 bytes when reading:
    b"default-src 'none'; ..."', url='https://github.com/.../archive/...zip'

That broke integration installs (archive download) and GitHub device-flow login
(``/login/device/code``). See hacs/integration#5644 for the upstream fix.

These tests assert the shim passes bounded, raised limits everywhere it can
reach ``github.com``.
"""

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.github.auth import GitHubAuth
from shim.http import GITHUB_MAX_FIELD_SIZE, GITHUB_MAX_LINE_SIZE, github_session
from shim.integrations.manager import IntegrationManager


# --------------------------------------------------------------------------- #
#  shim.http helper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_github_session_raises_bounded_limits():
    """The default limits exceed GitHub's header but stay bounded."""
    assert GITHUB_MAX_FIELD_SIZE > 9217  # observed CSP header size
    assert GITHUB_MAX_FIELD_SIZE < 1024 * 1024
    assert GITHUB_MAX_LINE_SIZE >= 4 * 1024

    session = github_session()
    try:
        assert session._max_field_size == GITHUB_MAX_FIELD_SIZE
        assert session._max_line_size == GITHUB_MAX_LINE_SIZE
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_github_session_allows_override():
    """Explicit kwargs win, so callers can tighten or widen the limits."""
    session = github_session(max_field_size=32 * 1024, connector=None)
    try:
        assert session._max_field_size == 32 * 1024
        assert session._max_line_size == GITHUB_MAX_LINE_SIZE
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
#  Integration download path
# --------------------------------------------------------------------------- #


def _build_integration_zip(domain: str = "demo_integration") -> bytes:
    """Build a minimal GitHub-style archive with custom_components/<domain>."""
    buffer = io.BytesIO()
    manifest = {
        "domain": domain,
        "name": "Demo",
        "version": "1.0.0",
        "documentation": "https://example.com",
    }
    with zipfile.ZipFile(buffer, "w") as zf:
        prefix = f"repo-main/custom_components/{domain}"
        zf.writestr(f"{prefix}/manifest.json", json.dumps(manifest))
        zf.writestr(f"{prefix}/__init__.py", "")
    return buffer.getvalue()


class _RecorderSession:
    """Async-context-manager session that records the constructor kwargs."""

    def __init__(self, payload: bytes, status: int = 200):
        self.payload = payload
        self.status = status
        self.get_calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))

        payload, status = self.payload, self.status

        class _Response:
            def __init__(self):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def read(self):
                return payload

        return _Response()


@pytest.fixture
def manager(tmp_path):
    storage = MagicMock()
    storage.load_integrations.return_value = {}
    storage.load_custom_repos.return_value = {}
    storage.load_unsupported_repos.return_value = {}
    return IntegrationManager(storage, tmp_path / "shim")


@pytest.mark.asyncio
async def test_download_integration_uses_bounded_http_limits(manager):
    """The archive-download session tolerates GitHub's ~9.2 KiB CSP header."""
    payload = _build_integration_zip()
    session = _RecorderSession(payload)
    captured = {}

    def _make_session(**kwargs):
        captured.update(kwargs)
        return session

    with patch("aiohttp.ClientSession", side_effect=_make_session):
        actual_domain = await manager._download_integration(
            "https://github.com/owner/repo", "demo_integration", version="1.0.0"
        )

    assert actual_domain == "demo_integration"
    assert captured.get("max_field_size") == GITHUB_MAX_FIELD_SIZE
    assert captured.get("max_line_size") == GITHUB_MAX_LINE_SIZE
    # Sanity: the download really went to the github.com archive endpoint that
    # emits the oversized header (not directly to codeload).
    assert session.get_calls
    assert session.get_calls[0][0].startswith("https://github.com/owner/repo/archive/")


@pytest.mark.asyncio
async def test_download_integration_retries_through_archive_urls(manager):
    """A failed first URL still falls through to the next one."""
    payload = _build_integration_zip()
    session = _RecorderSession(payload, status=404)

    with patch("aiohttp.ClientSession", return_value=session):
        with pytest.raises(Exception, match="Failed to download"):
            await manager._download_integration(
                "https://github.com/owner/repo", "demo_integration", version="1.0.0"
            )

    # refs/tags/<version>, refs/tags/v<version>, refs/heads/<version>
    assert len(session.get_calls) == 3


# --------------------------------------------------------------------------- #
#  GitHub device-flow login path
# --------------------------------------------------------------------------- #


class _FakeRegistrationData:
    device_code = "device-code-abc"
    user_code = "AB1234"
    verification_uri = "https://github.com/login/device"
    expires_in = 900
    interval = 5


class _FakeDeviceAPI:
    """Minimal GitHubDeviceAPI stand-in that mirrors its session ownership.

    Like the real class, it exposes ``_session`` and only closes sessions it
    created itself (``_close_session = False`` when a session is injected).
    """

    _close_session = False

    def __init__(self, session=None):
        self._session = session
        self.version = None
        self.register = AsyncMock(return_value=MagicMock(data=_FakeRegistrationData()))
        self._release = asyncio.Event()
        self.close_session = AsyncMock()

    async def activation(self, device_code, **kwargs):
        await self._release.wait()
        return MagicMock(data=MagicMock(access_token="gho_token"))

    def release(self):
        self._release.set()


@pytest.fixture
def auth(tmp_path):
    return GitHubAuth(tmp_path / "shim")


def _device_api_factory(store: dict):
    """Patch target that mirrors aiogithubapi's ``session`` kwarg handling."""

    def _factory(**kwargs):
        fake = _FakeDeviceAPI(session=kwargs.get("session"))
        store["api"] = fake
        store["session"] = kwargs.get("session")
        return fake

    return _factory


@pytest.mark.asyncio
async def test_register_device_passes_limited_session(auth):
    """The device-flow request to github.com/login/device/code uses our session."""
    store = {}
    with patch("aiogithubapi.GitHubDeviceAPI", side_effect=_device_api_factory(store)):
        await auth.register_device()

    session = store["session"]
    assert session is not None, "aiogithubapi would create a bare ClientSession"
    assert session._max_field_size == GITHUB_MAX_FIELD_SIZE
    assert session._max_line_size == GITHUB_MAX_LINE_SIZE

    # Cleanup: run the flow to completion so the session is closed.
    store["api"].release()
    await asyncio.wait_for(auth._activation_task, timeout=5)
    assert session.closed


@pytest.mark.asyncio
async def test_register_device_failure_closes_session(auth):
    """A failed registration must not leak the injected session."""
    store = {}
    factory = _device_api_factory(store)

    def _factory(**kwargs):
        fake = factory(**kwargs)
        fake.register = AsyncMock(side_effect=RuntimeError("Got more than 8190 bytes"))
        return fake

    with patch("aiogithubapi.GitHubDeviceAPI", side_effect=_factory):
        with pytest.raises(RuntimeError):
            await auth.register_device()

    assert store["session"].closed


@pytest.mark.asyncio
async def test_register_device_twice_does_not_close_new_session(auth):
    """Re-registering closes the previous session, not the freshly-created one."""
    store1 = {}
    with patch("aiogithubapi.GitHubDeviceAPI", side_effect=_device_api_factory(store1)):
        await auth.register_device()
    store2 = {}
    with patch("aiogithubapi.GitHubDeviceAPI", side_effect=_device_api_factory(store2)):
        await auth.register_device()

    first_session, second_session = store1["session"], store2["session"]
    # The superseded flow's session is closed straight away...
    assert first_session.closed
    # ...while the active one stays open until activation finishes.
    assert not second_session.closed

    store2["api"].release()
    await asyncio.wait_for(auth._activation_task, timeout=5)
    assert second_session.closed


# --------------------------------------------------------------------------- #
#  HA session stubs used by integrations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_clientsession_stubs_use_bounded_limits(tmp_path):
    """Integrations reaching github.com via the HA session helpers are covered."""
    from shim.core import HomeAssistant
    from shim.import_patch import setup_import_patching
    from homeassistant.helpers.aiohttp_client import (
        async_create_clientsession,
        async_get_clientsession,
        _async_close_clientsessions,
    )

    hass = HomeAssistant(tmp_path)
    setup_import_patching(hass).patch()

    cached = async_get_clientsession(hass)
    assert cached._max_field_size == GITHUB_MAX_FIELD_SIZE
    assert cached._max_line_size == GITHUB_MAX_LINE_SIZE

    # Explicit kwargs (e.g. HACS's own fix) are honoured and not cached into
    # the default-keyed session.
    custom = async_get_clientsession(hass, max_field_size=16 * 1024)
    assert custom is not cached
    assert custom._max_field_size == 16 * 1024

    created = async_create_clientsession(hass)
    assert created._max_field_size == GITHUB_MAX_FIELD_SIZE

    await _async_close_clientsessions()
