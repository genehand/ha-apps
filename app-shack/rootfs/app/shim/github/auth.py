"""GitHub OAuth device-flow authentication for the Shack shim.

Mirrors the device-flow used by HACS (`custom_components/hacs/config_flow.py`):
the user opens https://github.com/login/device and enters a one-time code,
while the shim polls GitHub's access-token endpoint until the user confirms.

The resulting `access_token` is persisted to disk so it survives restarts,
and is surfaced to the IntegrationManager via `get_token()` so it can be
attached to `api.github.com` requests as `Authorization: Bearer <token>`.

We reuse HACS's OAuth App client_id (no scope) — that is enough for
read-only access to public repository metadata (releases, tags, trees),
which is all the shim needs.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from ..logging import get_logger

_LOGGER = get_logger(__name__)

# HACS's registered GitHub OAuth App client_id. Reusing it means we don't
# require the user to register their own OAuth App; tokens issued via this
# client_id are standard GitHub OAuth tokens scoped to public data.
HACS_CLIENT_ID = "395a8e669c5de9f7c6e8"

# Path of the persisted token file relative to shim_dir.
_TOKEN_FILENAME = "github_token.json"

# The GitHub rate_limit endpoint (extracted as a constant so tests can
# patch it to point at a local mock server).
GITHUB_RATE_LIMIT_URL = "https://api.github.com/rate_limit"


class GitHubAuth:
    """Manage a single user's GitHub OAuth device-flow token.

    The token is loaded from disk at construction time and cached in
    memory. Callers fetch it via `get_token()`. The IntegrationManager
    reads the token via `set_github_token()` on the manager.
    """

    def __init__(
        self,
        shim_dir: Path,
        client_id: str = HACS_CLIENT_ID,
    ) -> None:
        self._shim_dir = Path(shim_dir)
        self._shim_dir.mkdir(parents=True, exist_ok=True)
        self._token_file = self._shim_dir / _TOKEN_FILENAME
        self._client_id = client_id

        # Cached token (None == not authenticated)
        self._token: Optional[str] = None

        # Active device-flow state
        self._device_api: Optional[Any] = None  # aiogithubapi.GitHubDeviceAPI
        self._registration: Optional[Dict[str, Any]] = None
        self._activation_task: Optional[asyncio.Task] = None
        self._activation_result: Optional[Dict[str, Any]] = None

        self._load_token()

    # ------------------------------------------------------------------ #
    #  Token persistence
    # ------------------------------------------------------------------ #

    def _load_token(self) -> None:
        """Load the cached token from disk, if present."""
        try:
            with open(self._token_file, "r") as f:
                data = json.load(f)
            token = data.get("token") if isinstance(data, dict) else None
            if token:
                self._token = token
                _LOGGER.debug("Loaded cached GitHub token")
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as e:
            _LOGGER.warning(f"Corrupt GitHub token file ({e}); ignoring")
        except Exception as e:
            _LOGGER.warning(f"Failed to load GitHub token: {e}")

    def _save_token(self, token: str) -> None:
        """Persist the token to disk atomically (tmp + rename).

        Also updates the in-memory cache so callers see the new token
        immediately after a successful save.
        """
        tmp = self._token_file.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump({"token": token, "saved_at": datetime.now().isoformat()}, f)
            tmp.rename(self._token_file)
        except Exception as e:
            _LOGGER.error(f"Failed to save GitHub token: {e}")
            # Best-effort cleanup of tmp file
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        self._token = token

    def clear_token(self) -> None:
        """Delete the persisted token and cancel any pending flow.

        Synchronous — use from ``__init__`` or tests. For async callers
        (route handlers), use ``async_clear_token`` instead to avoid
        blocking the event loop.
        """
        self._delete_token_file()
        self._token = None
        self.cancel_activation()

    async def async_clear_token(self) -> None:
        """Async-safe version of ``clear_token`` for route handlers."""
        await asyncio.to_thread(self._delete_token_file)
        self._token = None
        self.cancel_activation()

    def _delete_token_file(self) -> None:
        """Delete the token file from disk (blocking I/O)."""
        try:
            self._token_file.unlink(missing_ok=True)
            _LOGGER.info("Cleared GitHub token")
        except Exception as e:
            _LOGGER.warning(f"Failed to delete GitHub token file: {e}")

    def get_token(self) -> Optional[str]:
        """Return the cached token, or None if not authenticated."""
        return self._token

    def is_authenticated(self) -> bool:
        """True if a token is available."""
        return self._token is not None

    # ------------------------------------------------------------------ #
    #  Device flow
    # ------------------------------------------------------------------ #

    async def register_device(self) -> Dict[str, Any]:
        """Start a new device-flow registration.

        Returns a dict with: device_code, user_code, verification_uri,
        expires_in, interval. A background task is started that polls
        GitHub for activation; check `get_activation_status()`.
        """
        # Cancel any in-flight flow first
        self.cancel_activation()
        if self._device_api is not None:
            try:
                await self._device_api.close_session()
            except Exception as e:
                _LOGGER.debug(f"Error closing previous device session: {e}")
            self._device_api = None

        # Lazy import so the shim can run without aiogithubapi at import time
        from aiogithubapi import GitHubDeviceAPI

        self._device_api = GitHubDeviceAPI(client_id=self._client_id)
        response = await self._device_api.register()
        data = response.data
        self._registration = {
            "device_code": data.device_code,
            "user_code": data.user_code,
            "verification_uri": data.verification_uri,
            "expires_in": data.expires_in,
            "interval": data.interval,
        }
        self._activation_result = None
        self._activation_task = asyncio.create_task(
            self._wait_for_activation(data.device_code)
        )
        _LOGGER.info(
            "Started GitHub device flow (user_code=%s)", data.user_code
        )
        return self._registration

    async def _wait_for_activation(self, device_code: str) -> None:
        """Poll GitHub until the user confirms the device code."""
        try:
            response = await self._device_api.activation(device_code=device_code)
            token = response.data.access_token
            # Wrap sync file I/O in to_thread to avoid blocking the event loop
            await asyncio.to_thread(self._save_token, token)
            self._activation_result = {"status": "success"}
            _LOGGER.info("GitHub device activation succeeded")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._activation_result = {"status": "error", "error": str(e)}
            _LOGGER.warning(f"GitHub device activation failed: {e}")
        finally:
            # Close the device session; the token is all we need.
            if self._device_api is not None:
                try:
                    await self._device_api.close_session()
                except Exception as e:
                    _LOGGER.debug(f"Error closing device session: {e}")
            self._device_api = None

    def get_activation_status(self) -> Dict[str, Any]:
        """Return the status of the current (or last) device flow.

        Returns one of:
        - {"status": "idle"}                            - no flow started
        - {"status": "pending", "user_code": ...}       - waiting for user
        - {"status": "success"}                         - token acquired
        - {"status": "error", "error": "..."}           - flow failed
        """
        if self._activation_result is not None:
            return self._activation_result
        if self._activation_task is not None and not self._activation_task.done():
            status: Dict[str, Any] = {"status": "pending"}
            if self._registration:
                status["user_code"] = self._registration.get("user_code")
                status["verification_uri"] = self._registration.get(
                    "verification_uri"
                )
            return status
        return {"status": "idle"}

    def cancel_activation(self) -> None:
        """Cancel any pending device-flow task."""
        if (
            self._activation_task is not None
            and not self._activation_task.done()
        ):
            self._activation_task.cancel()
        self._activation_task = None
        self._registration = None
        self._activation_result = None

    # ------------------------------------------------------------------ #
    #  Lightweight rate-limit probe (used by the web UI's status page)
    # ------------------------------------------------------------------ #

    async def check_rate_limit(self) -> Optional[Dict[str, Any]]:
        """Fetch the authenticated rate-limit status from GitHub.

        Returns the `resources.core` dict (limit, remaining, reset) on
        success, or None if not authenticated / on error.
        """
        token = self._token
        if not token:
            return None
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "ha-apps/shack",
            "Accept": "application/vnd.github+json",
        }
        url = GITHUB_RATE_LIMIT_URL
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("resources", {}).get("core")
                    if response.status == 401:
                        _LOGGER.warning(
                            "GitHub token appears to be invalid (401)"
                        )
                        return {"invalid": True}
                    _LOGGER.debug(
                        f"Rate-limit probe returned HTTP {response.status}"
                    )
        except Exception as e:
            _LOGGER.debug(f"Rate-limit probe failed: {e}")
        return None
