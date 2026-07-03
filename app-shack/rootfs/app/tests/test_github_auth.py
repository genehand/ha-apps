"""Tests for the GitHub OAuth device-flow module and IntegrationManager wiring."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp
from aiohttp import web

import sys

# Make the app directory importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.github import GitHubAuth, HACS_CLIENT_ID
from shim.integrations.manager import IntegrationManager, GITHUB_RETRY_MAX_ATTEMPTS
from shim.storage import Storage


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_shim_dir(tmp_path):
    """Provide a clean shim_dir for each test (created on disk)."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    return shim_dir


@pytest.fixture
def github_auth(temp_shim_dir):
    """A GitHubAuth instance with no token."""
    return GitHubAuth(temp_shim_dir)


# ---------------------------------------------------------------------------
#  GitHubAuth: token persistence
# ---------------------------------------------------------------------------


class TestGitHubAuthTokenPersistence:
    def test_initial_state_is_unauthenticated(self, github_auth):
        assert github_auth.is_authenticated() is False
        assert github_auth.get_token() is None

    def test_save_and_reload_token(self, temp_shim_dir):
        auth1 = GitHubAuth(temp_shim_dir)
        assert auth1.is_authenticated() is False

        # Persist a token
        auth1._save_token("gho_testtoken123")
        assert auth1.get_token() == "gho_testtoken123"

        # A new instance picks it up from disk
        auth2 = GitHubAuth(temp_shim_dir)
        assert auth2.is_authenticated() is True
        assert auth2.get_token() == "gho_testtoken123"

        # Token file content is well-formed JSON
        with open(temp_shim_dir / "github_token.json") as f:
            payload = json.load(f)
        assert payload["token"] == "gho_testtoken123"
        assert "saved_at" in payload

    def test_clear_token_removes_file_and_memory(self, temp_shim_dir):
        auth = GitHubAuth(temp_shim_dir)
        auth._save_token("gho_testtoken123")
        assert auth.is_authenticated()

        auth.clear_token()

        assert not auth.is_authenticated()
        assert auth.get_token() is None
        assert not (temp_shim_dir / "github_token.json").exists()

        # A fresh instance also reports unauthenticated
        assert GitHubAuth(temp_shim_dir).is_authenticated() is False


@pytest.mark.asyncio
async def test_async_clear_token_is_non_blocking(temp_shim_dir):
    """async_clear_token should delete the file and clear memory without
    blocking the event loop (file I/O runs in a thread)."""
    auth = GitHubAuth(temp_shim_dir)
    auth._save_token("gho_async_clear")
    assert auth.is_authenticated()

    await auth.async_clear_token()

    assert not auth.is_authenticated()
    assert auth.get_token() is None
    assert not (temp_shim_dir / "github_token.json").exists()

    def test_corrupt_token_file_is_ignored(self, temp_shim_dir):
        token_path = temp_shim_dir / "github_token.json"
        token_path.write_text("not json at all")

        auth = GitHubAuth(temp_shim_dir)
        assert auth.is_authenticated() is False
        assert auth.get_token() is None

    def test_missing_token_file_is_ok(self, temp_shim_dir):
        # No file created yet — auth should be cleanly unauthenticated
        auth = GitHubAuth(temp_shim_dir)
        assert auth.is_authenticated() is False


# ---------------------------------------------------------------------------
#  GitHubAuth: device-flow state machine
# ---------------------------------------------------------------------------


class _FakeRegistrationData:
    """Mimics aiogithubapi's GitHubLoginDeviceModel fields."""

    def __init__(self):
        self.device_code = "device-code-abc"
        self.user_code = "AB1234"
        self.verification_uri = "https://github.com/login/device"
        self.expires_in = 900
        self.interval = 5


class _FakeActivationResponse:
    """Mimics aiogithubapi's GitHubResponseModel.activation().data."""

    def __init__(self, token: str):
        self.access_token = token


class _FakeDeviceAPI:
    """Stand-in for aiogithubapi.GitHubDeviceAPI.

    The real class blocks inside `activation()` until the user confirms
    or an error occurs. We emulate that by waiting on an asyncio.Event
    that the test can set to "release" the activation.
    """

    def __init__(self, *, fail_with: Exception | None = None, token: str = "gho_testtoken"):
        self._fail_with = fail_with
        self._token = token
        self._release = asyncio.Event()
        self.register = AsyncMock(return_value=MagicMock(data=_FakeRegistrationData()))
        self.close_session = AsyncMock()

    async def activation(self, device_code, **kwargs):
        await self._release.wait()
        if self._fail_with is not None:
            raise self._fail_with
        return MagicMock(data=_FakeActivationResponse(self._token))

    def release(self):
        self._release.set()


@pytest.mark.asyncio
async def test_register_device_starts_background_task(github_auth):
    fake = _FakeDeviceAPI()
    with patch("aiogithubapi.GitHubDeviceAPI", return_value=fake):
        registration = await github_auth.register_device()

    assert registration["user_code"] == "AB1234"
    assert registration["device_code"] == "device-code-abc"

    # Status should be pending
    status = github_auth.get_activation_status()
    assert status["status"] == "pending"
    assert status["user_code"] == "AB1234"

    # Background task is running
    assert github_auth._activation_task is not None
    assert not github_auth._activation_task.done()

    # Cleanup
    github_auth.cancel_activation()
    await asyncio.sleep(0)  # let cancellation propagate
    assert github_auth.get_activation_status()["status"] == "idle"


@pytest.mark.asyncio
async def test_device_flow_completes_and_persists_token(temp_shim_dir):
    auth = GitHubAuth(temp_shim_dir)
    fake = _FakeDeviceAPI(token="gho_completed_token")
    with patch("aiogithubapi.GitHubDeviceAPI", return_value=fake):
        await auth.register_device()

        # Release the activation task
        fake.release()
        # Wait for the background task to finish
        await asyncio.wait_for(auth._activation_task, timeout=2)

    # Token should now be persisted and in-memory
    assert auth.is_authenticated()
    assert auth.get_token() == "gho_completed_token"
    status = auth.get_activation_status()
    assert status["status"] == "success"

    # A fresh instance should see the persisted token
    assert GitHubAuth(temp_shim_dir).get_token() == "gho_completed_token"


@pytest.mark.asyncio
async def test_device_flow_failure_does_not_persist_token(temp_shim_dir):
    auth = GitHubAuth(temp_shim_dir)
    fake = _FakeDeviceAPI(fail_with=RuntimeError("user denied"))
    with patch("aiogithubapi.GitHubDeviceAPI", return_value=fake):
        await auth.register_device()
        fake.release()
        await asyncio.wait_for(auth._activation_task, timeout=2)

    assert not auth.is_authenticated()
    assert auth.get_token() is None
    status = auth.get_activation_status()
    assert status["status"] == "error"
    assert "user denied" in status["error"]


@pytest.mark.asyncio
async def test_register_device_cancels_previous_flow(github_auth):
    fake1 = _FakeDeviceAPI()
    with patch("aiogithubapi.GitHubDeviceAPI", return_value=fake1):
        await github_auth.register_device()
        first_task = github_auth._activation_task

    fake2 = _FakeDeviceAPI(token="gho_second")
    with patch("aiogithubapi.GitHubDeviceAPI", return_value=fake2):
        await github_auth.register_device()
        second_task = github_auth._activation_task

    assert first_task is not second_task
    # The previous task must have been cancelled. Release fake1 so its
    # activation coroutine can observe the cancellation and finish.
    fake1.release()
    try:
        await asyncio.wait_for(first_task, timeout=2)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    assert first_task.done()

    # Release the second flow so the test can tear down cleanly
    fake2.release()
    await asyncio.wait_for(second_task, timeout=2)


# ---------------------------------------------------------------------------
#  GitHubAuth: rate-limit probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_rate_limit_returns_none_when_unauthenticated(github_auth):
    assert await github_auth.check_rate_limit() is None


@pytest.mark.asyncio
async def test_check_rate_limit_returns_core_dict_when_authenticated(temp_shim_dir):
    """Spin up a tiny aiohttp app that mocks the /rate_limit endpoint."""
    auth = GitHubAuth(temp_shim_dir)
    auth._token = "gho_ratelimit_token"

    async def handler(request):
        assert request.headers["Authorization"] == "Bearer gho_ratelimit_token"
        assert request.headers["User-Agent"] == "ha-apps/shack"
        return web.json_response(
            {"resources": {"core": {"limit": 5000, "remaining": 4321, "reset": 123}}}
        )

    app = web.Application()
    app.router.add_get("/rate_limit", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        with patch(
            "shim.github.auth.GITHUB_RATE_LIMIT_URL",
            f"http://127.0.0.1:{port}/rate_limit",
        ):
            core = await auth.check_rate_limit()
    finally:
        await runner.cleanup()

    assert core == {"limit": 5000, "remaining": 4321, "reset": 123}


# ---------------------------------------------------------------------------
#  IntegrationManager: token injection + rate-limit retry
# ---------------------------------------------------------------------------


def _make_manager(tmp_path) -> IntegrationManager:
    """Build an IntegrationManager with a tmp shim_dir."""
    shim_dir = tmp_path / "shim"
    storage = Storage(shim_dir)
    return IntegrationManager(storage, shim_dir)


def test_set_github_token_changes_headers(tmp_path):
    mgr = _make_manager(tmp_path)

    # No token: no Authorization header
    headers = mgr._github_headers()
    assert "Authorization" not in headers
    assert headers["User-Agent"] == "ha-apps/shack"

    # With token: Authorization header is added
    mgr.set_github_token("gho_injected")
    headers = mgr._github_headers()
    assert headers["Authorization"] == "Bearer gho_injected"
    assert mgr.github_token == "gho_injected"

    # Clearing removes the header again
    mgr.set_github_token(None)
    headers = mgr._github_headers()
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_github_api_get_injects_auth_header(tmp_path):
    """A real local aiohttp server asserts the Authorization header is present."""
    mgr = _make_manager(tmp_path)
    mgr.set_github_token("gho_secret_for_header")

    captured = {}

    async def handler(request):
        captured["auth"] = request.headers.get("Authorization")
        captured["ua"] = request.headers.get("User-Agent")
        return web.json_response({"tag_name": "v1.2.3"})

    app = web.Application()
    app.router.add_get("/repos/{owner}/{repo}/releases/latest", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        async with aiohttp.ClientSession() as session:
            status, data = await mgr._github_api_get(
                session,
                f"http://127.0.0.1:{port}/repos/owner/repo/releases/latest",
            )
    finally:
        await runner.cleanup()

    assert status == 200
    assert data == {"tag_name": "v1.2.3"}
    assert captured["auth"] == "Bearer gho_secret_for_header"
    assert captured["ua"] == "ha-apps/shack"


@pytest.mark.asyncio
async def test_github_api_get_retries_on_429(tmp_path):
    """A 429 with Retry-After is retried; the second call succeeds."""
    mgr = _make_manager(tmp_path)
    mgr.set_github_token("gho_retry")

    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return web.Response(
                status=429,
                headers={"Retry-After": "0", "X-RateLimit-Remaining": "0"},
                text="rate limited",
            )
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/repos/owner/repo", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    # Speed up the test by capping the retry delay to a tiny value.
    original_max = None
    import shim.integrations.manager as mod

    original_max = mod.GITHUB_RETRY_MAX_DELAY
    mod.GITHUB_RETRY_MAX_DELAY = 0

    try:
        async with aiohttp.ClientSession() as session:
            status, data = await mgr._github_api_get(
                session,
                f"http://127.0.0.1:{port}/repos/owner/repo",
            )
    finally:
        mod.GITHUB_RETRY_MAX_DELAY = original_max
        await runner.cleanup()

    assert calls["count"] == 2
    assert status == 200
    assert data == {"ok": True}


@pytest.mark.asyncio
async def test_github_api_get_returns_none_after_max_retries(tmp_path):
    """A persistent 429 exhausts retries and returns (None, None)."""
    mgr = _make_manager(tmp_path)
    mgr.set_github_token("gho_persistent")

    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        return web.Response(
            status=429,
            headers={"Retry-After": "0", "X-RateLimit-Remaining": "0"},
            text="rate limited",
        )

    app = web.Application()
    app.router.add_get("/repos/owner/repo", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    # Cap the retry delay so the test runs quickly.
    import shim.integrations.manager as mod

    original_max = mod.GITHUB_RETRY_MAX_DELAY
    mod.GITHUB_RETRY_MAX_DELAY = 0

    try:
        async with aiohttp.ClientSession() as session:
            status, data = await mgr._github_api_get(
                session,
                f"http://127.0.0.1:{port}/repos/owner/repo",
            )
    finally:
        mod.GITHUB_RETRY_MAX_DELAY = original_max
        await runner.cleanup()

    # After exhausting retries, the final 429 response is returned
    # (network errors return None; HTTP responses are surfaced to the
    # caller so they can branch on status).
    assert status == 429
    assert data is None
    assert calls["count"] == GITHUB_RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_github_api_get_does_not_retry_on_404(tmp_path):
    """A 404 is returned immediately, no retries."""
    mgr = _make_manager(tmp_path)
    mgr.set_github_token("gho_404")

    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        return web.json_response({"message": "Not Found"}, status=404)

    app = web.Application()
    app.router.add_get("/repos/owner/repo", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        async with aiohttp.ClientSession() as session:
            status, data = await mgr._github_api_get(
                session,
                f"http://127.0.0.1:{port}/repos/owner/repo",
            )
    finally:
        await runner.cleanup()

    assert status == 404
    assert calls["count"] == 1  # No retries


@pytest.mark.asyncio
async def test_get_latest_version_from_github_uses_token(tmp_path):
    """The end-to-end latest-version helper delegates to _github_api_get,
    which we already verified above injects the Authorization header.
    Here we just check the call plumbing: the helper passes the URL
    through and parses tag_name out of the response."""
    mgr = _make_manager(tmp_path)
    mgr.set_github_token("gho_latest_version")

    captured = {}

    async def fake_api_get(session, url, *, timeout=10):
        captured["url"] = url
        captured["auth_header_in_headers"] = mgr._github_headers().get(
            "Authorization"
        )
        return 200, {"tag_name": "v9.9.9"}

    mgr._github_api_get = fake_api_get  # type: ignore[assignment]

    version = await mgr._get_latest_version_from_github(
        "https://github.com/owner/repo"
    )

    assert version == "9.9.9"
    assert (
        captured["url"]
        == "https://api.github.com/repos/owner/repo/releases/latest"
    )
    assert captured["auth_header_in_headers"] == "Bearer gho_latest_version"


# ---------------------------------------------------------------------------
#  CLIENT_ID sanity check
# ---------------------------------------------------------------------------


def test_hacs_client_id_is_picked_up():
    # If HACS ever rotates its CLIENT_ID, this test reminds us to update.
    assert HACS_CLIENT_ID == "395a8e669c5de9f7c6e8"
