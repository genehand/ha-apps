"""Application Credentials stub for Home Assistant Shim.

Provides homeassistant.components.application_credentials with
AuthorizationServer, ClientCredential, and credential storage.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger

_LOGGER = get_logger(__name__)

DOMAIN = "application_credentials"
STORAGE_KEY = "application_credentials"
STORAGE_VERSION = 1
DATA_COMPONENT = DOMAIN
CONF_AUTH_DOMAIN = "auth_domain"
DEFAULT_IMPORT_NAME = "Import from configuration.yaml"


@dataclass
class ClientCredential:
    """Represent an OAuth client credential."""

    client_id: str
    client_secret: str
    name: str | None = None


@dataclass
class AuthorizationServer:
    """Represent an OAuth2 Authorization Server."""

    authorize_url: str
    token_url: str


class ApplicationCredentialsStorage:
    """Simple JSON file storage for application credentials."""

    def __init__(self, storage_dir: Path):
        self._storage_file = storage_dir / f"{STORAGE_KEY}.json"
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load credentials from storage."""
        if self._storage_file.exists():
            try:
                with open(self._storage_file, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                _LOGGER.error("Failed to load application credentials: %s", e)
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """Save credentials to storage."""
        try:
            temp_path = self._storage_file.with_suffix(".json.tmp")
            with open(temp_path, "w") as f:
                json.dump(self._data, f, indent=2)
            temp_path.rename(self._storage_file)
        except OSError as e:
            _LOGGER.error("Failed to save application credentials: %s", e)

    def async_items(self) -> list[dict]:
        """Return all stored credential items."""
        return list(self._data.values())

    def async_client_credentials(self, domain: str) -> dict[str, ClientCredential]:
        """Return ClientCredentials in storage for the specified domain."""
        credentials = {}
        for item_id, item in self._data.items():
            if item.get("domain") != domain:
                continue
            auth_domain = item.get(CONF_AUTH_DOMAIN, item_id)
            credentials[auth_domain] = ClientCredential(
                client_id=item["client_id"],
                client_secret=item["client_secret"],
                name=item.get("name"),
            )
        return credentials

    def async_create_item(self, info: dict) -> str:
        """Create a new credential item."""
        domain = info["domain"]
        client_id = info["client_id"]
        item_id = f"{domain}.{client_id}"

        self._data[item_id] = {
            "id": item_id,
            "domain": domain,
            "client_id": client_id,
            "client_secret": info["client_secret"],
            "auth_domain": info.get(CONF_AUTH_DOMAIN, item_id),
            "name": info.get("name", DEFAULT_IMPORT_NAME),
        }
        self._save()
        _LOGGER.debug("Created application credential %s for %s", item_id, domain)
        return item_id

    def async_delete_item(self, item_id: str) -> bool:
        """Delete a credential item."""
        if item_id not in self._data:
            return False
        del self._data[item_id]
        self._save()
        _LOGGER.debug("Deleted application credential %s", item_id)
        return True


class AuthImplementation:
    """Application Credentials local oauth2 implementation."""

    def __init__(
        self,
        hass,
        auth_domain: str,
        credential: ClientCredential,
        authorization_server: AuthorizationServer,
    ) -> None:
        """Initialize AuthImplementation."""
        self.hass = hass
        self.auth_domain = auth_domain
        self._credential = credential
        self._authorization_server = authorization_server
        self._name = credential.name

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return self._name or self._credential.client_id

    @property
    def domain(self) -> str:
        """Domain providing the implementation."""
        return self.auth_domain

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate a url for the user to authorize."""
        from .oauth2 import (
            LocalOAuth2Implementation,
            async_get_redirect_uri,
        )

        redirect_uri = async_get_redirect_uri(self.hass)
        impl = LocalOAuth2Implementation(
            self.hass,
            self.auth_domain,
            self._credential.client_id,
            self._credential.client_secret,
            self._authorization_server.authorize_url,
            self._authorization_server.token_url,
        )
        return await impl.async_generate_authorize_url(flow_id)

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Resolve external data to tokens."""
        from .oauth2 import LocalOAuth2Implementation

        impl = LocalOAuth2Implementation(
            self.hass,
            self.auth_domain,
            self._credential.client_id,
            self._credential.client_secret,
            self._authorization_server.authorize_url,
            self._authorization_server.token_url,
        )
        return await impl.async_resolve_external_data(external_data)

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token."""
        from .oauth2 import LocalOAuth2Implementation

        impl = LocalOAuth2Implementation(
            self.hass,
            self.auth_domain,
            self._credential.client_id,
            self._credential.client_secret,
            self._authorization_server.authorize_url,
            self._authorization_server.token_url,
        )
        return await impl._async_refresh_token(token)

    async def async_refresh_token(self, token: dict) -> dict:
        """Refresh a token and update expires info."""
        import time

        new_token = await self._async_refresh_token(token)
        new_token["expires_in"] = int(new_token["expires_in"])
        new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token


def setup_application_credentials(hass, storage_dir: Path) -> ApplicationCredentialsStorage:
    """Set up Application Credentials storage."""
    storage = ApplicationCredentialsStorage(storage_dir)
    hass.data[DATA_COMPONENT] = storage
    return storage


async def _async_provide_implementation(
    hass, domain: str
) -> list:
    """Return registered OAuth implementations from stored credentials."""
    storage: ApplicationCredentialsStorage = hass.data.get(DATA_COMPONENT)
    if storage is None:
        return []

    # Try to load the integration's application_credentials platform
    try:
        platform = await _get_platform(hass, domain)
    except (ImportError, ValueError):
        return []

    if not platform:
        return []

    credentials = storage.async_client_credentials(domain)
    if hasattr(platform, "async_get_auth_implementation"):
        return [
            await platform.async_get_auth_implementation(hass, auth_domain, credential)
            for auth_domain, credential in credentials.items()
        ]

    authorization_server = await platform.async_get_authorization_server(hass)
    return [
        AuthImplementation(hass, auth_domain, credential, authorization_server)
        for auth_domain, credential in credentials.items()
    ]


async def _get_platform(hass, integration_domain: str):
    """Load the application_credentials platform for an integration."""
    try:
        platform_module = __import__(
            f"custom_components.{integration_domain}.application_credentials",
            fromlist=["application_credentials"],
        )
    except ImportError as err:
        _LOGGER.debug(
            "Integration '%s' does not provide application_credentials: %s",
            integration_domain,
            err,
        )
        return None

    if not hasattr(platform_module, "async_get_authorization_server") and not hasattr(
        platform_module, "async_get_auth_implementation"
    ):
        raise ValueError(
            f"Integration '{integration_domain}' platform application_credentials "
            "did not implement 'async_get_authorization_server' or "
            "'async_get_auth_implementation'"
        )
    return platform_module


async def async_import_client_credential(
    hass,
    domain: str,
    credential: ClientCredential,
    auth_domain: str | None = None,
) -> None:
    """Import an existing credential from configuration.yaml."""
    storage: ApplicationCredentialsStorage = hass.data.get(DATA_COMPONENT)
    if storage is None:
        raise ValueError("Integration 'application_credentials' not setup")
    item = {
        "domain": domain,
        "client_id": credential.client_id,
        "client_secret": credential.client_secret,
        "auth_domain": auth_domain or domain,
        "name": credential.name or DEFAULT_IMPORT_NAME,
    }
    # Check if already exists
    for existing in storage.async_items():
        if (
            existing["domain"] == domain
            and existing["client_id"] == credential.client_id
        ):
            return
    storage.async_create_item(item)
