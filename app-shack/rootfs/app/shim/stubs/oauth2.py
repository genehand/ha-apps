"""OAuth2 implementation for Home Assistant Shim.

Adapted from homeassistant.helpers.config_entry_oauth2_flow.
Provides real OAuth2 authorization-code flow with PKCE support,
token refresh, and callback handling for integrations running
outside of Home Assistant core.
"""

from __future__ import annotations

import asyncio
from asyncio import Lock
import base64
from collections.abc import Awaitable, Callable
import hashlib
from http import HTTPStatus
import json
import logging
import secrets
import time
from typing import Any, cast

from aiohttp import ClientError, ClientResponseError
import jwt
from yarl import URL

from ..config_entries import ConfigFlow
from ..ha_fetched.exceptions import (
    HomeAssistantError,
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)
from ..logging import get_logger

_LOGGER = get_logger(__name__)

DATA_JWT_SECRET = "oauth2_jwt_secret"
DATA_IMPLEMENTATIONS = "oauth2_impl"
DATA_PROVIDERS = "oauth2_providers"
AUTH_CALLBACK_PATH = "/auth/external/callback"
MY_AUTH_CALLBACK_PATH = "https://my.home-assistant.io/redirect/oauth"

CLOCK_OUT_OF_SYNC_MAX_SEC = 20
OAUTH_AUTHORIZE_URL_TIMEOUT_SEC = 30
OAUTH_TOKEN_TIMEOUT_SEC = 30


class ImplementationUnavailableError(HomeAssistantError):
    """Raised when an underlying implementation is unavailable."""


LOCALHOST_REDIRECT_URI = "http://localhost:8080/auth/external/callback"


def async_get_redirect_uri(hass) -> str:
    """Return the redirect uri.

    Always returns the localhost redirect URI. The OAuth provider will
    redirect here, which will fail as expected. The user copies the
    full failed redirect URL and pastes it back into the config form.
    """
    return LOCALHOST_REDIRECT_URI


class AbstractOAuth2Implementation:
    """Base class to abstract OAuth2 authentication."""

    @property
    def name(self) -> str:
        """Name of the implementation."""
        raise NotImplementedError

    @property
    def domain(self) -> str:
        """Domain that is providing the implementation."""
        raise NotImplementedError

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate a url for the user to authorize."""
        raise NotImplementedError

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Resolve external data to tokens."""
        raise NotImplementedError

    async def async_refresh_token(self, token: dict) -> dict:
        """Refresh a token and update expires info."""
        new_token = await self._async_refresh_token(token)
        # Force int for non-compliant oauth2 providers
        new_token["expires_in"] = int(new_token["expires_in"])
        new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token.

        Should raise OAuth2TokenRequestError on token refresh failure.
        """
        raise NotImplementedError


class LocalOAuth2Implementation(AbstractOAuth2Implementation):
    """Local OAuth2 implementation using authorization-code grant."""

    def __init__(
        self,
        hass,
        domain: str,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
    ) -> None:
        """Initialize local auth implementation."""
        self.hass = hass
        self._domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorize_url = authorize_url
        self.token_url = token_url

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return "Local application credentials"

    @property
    def domain(self) -> str:
        """Domain providing the implementation."""
        return self._domain

    @property
    def redirect_uri(self) -> str:
        """Return the redirect uri."""
        return async_get_redirect_uri(self.hass)

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data that needs to be appended to the authorize url."""
        return {}

    @property
    def extra_token_resolve_data(self) -> dict:
        """Extra data for the token resolve request."""
        return {}

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate a url for the user to authorize."""
        redirect_uri = self.redirect_uri
        return str(
            URL(self.authorize_url)
            .with_query(
                {
                    "response_type": "code",
                    "client_id": self.client_id,
                    "redirect_uri": redirect_uri,
                    "state": _encode_jwt(
                        self.hass, {"flow_id": flow_id, "redirect_uri": redirect_uri}
                    ),
                }
            )
            .update_query(self.extra_authorize_data)
        )

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Resolve the authorization code to tokens."""
        request_data: dict = {
            "grant_type": "authorization_code",
            "code": external_data["code"],
            "redirect_uri": external_data["state"]["redirect_uri"],
        }
        request_data.update(self.extra_token_resolve_data)
        return await self._token_request(request_data)

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token."""
        new_token = await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": token["refresh_token"],
            }
        )
        return {**token, **new_token}

    async def _token_request(self, data: dict) -> dict:
        """Make a token request.

        Raises OAuth2TokenRequestError on token request failure.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)

        data["client_id"] = self.client_id
        if self.client_secret:
            data["client_secret"] = self.client_secret

        _LOGGER.debug("Sending token request to %s", self.token_url)

        try:
            resp = await session.post(self.token_url, data=data)
            if resp.status >= 400:
                error_body = ""
                try:
                    error_body = await resp.text()
                    error_data = json.loads(error_body)
                    error_code = error_data.get("error", "unknown error")
                    error_description = error_data.get("error_description")
                    detail = (
                        f"{error_code}: {error_description}"
                        if error_description
                        else error_code
                    )
                except (ClientError, ValueError, AttributeError):
                    detail = error_body[:200] if error_body else "unknown error"
                _LOGGER.debug(
                    "Token request for %s failed (%s): %s",
                    self.domain,
                    resp.status,
                    detail,
                )
            resp.raise_for_status()
        except ClientResponseError as err:
            if err.status == HTTPStatus.TOO_MANY_REQUESTS or 500 <= err.status <= 599:
                raise OAuth2TokenRequestTransientError(
                    request_info=err.request_info,
                    history=err.history,
                    status=err.status,
                    message=err.message,
                    headers=err.headers,
                    domain=self._domain,
                ) from err
            if 400 <= err.status <= 499:
                raise OAuth2TokenRequestReauthError(
                    request_info=err.request_info,
                    history=err.history,
                    status=err.status,
                    message=err.message,
                    headers=err.headers,
                    domain=self._domain,
                ) from err

            raise OAuth2TokenRequestError(
                request_info=err.request_info,
                history=err.history,
                status=err.status,
                message=err.message,
                headers=err.headers,
                domain=self._domain,
            ) from err

        return cast(dict, await resp.json())


class LocalOAuth2ImplementationWithPkce(LocalOAuth2Implementation):
    """Local OAuth2 implementation with PKCE."""

    def __init__(
        self,
        hass,
        domain: str,
        client_id: str,
        authorize_url: str,
        token_url: str,
        client_secret: str = "",
        code_verifier_length: int = 128,
    ) -> None:
        """Initialize local auth implementation with PKCE."""
        super().__init__(
            hass,
            domain,
            client_id,
            client_secret,
            authorize_url,
            token_url,
        )
        self.code_verifier = self.generate_code_verifier(code_verifier_length)

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data appended to the authorize url (PKCE challenge)."""
        return {
            "code_challenge": self.compute_code_challenge(self.code_verifier),
            "code_challenge_method": "S256",
        }

    @property
    def extra_token_resolve_data(self) -> dict:
        """Extra data for the token resolve request (PKCE verifier)."""
        return {"code_verifier": self.code_verifier}

    @staticmethod
    def generate_code_verifier(code_verifier_length: int = 128) -> str:
        """Generate a code verifier."""
        if not 43 <= code_verifier_length <= 128:
            raise ValueError(
                "Parameter `code_verifier_length` must validate "
                "`43 <= code_verifier_length <= 128`."
            )
        return secrets.token_urlsafe(96)[:code_verifier_length]

    @staticmethod
    def compute_code_challenge(code_verifier: str) -> str:
        """Compute the code challenge."""
        if not 43 <= len(code_verifier) <= 128:
            raise ValueError(
                "Parameter `code_verifier` must validate "
                "`43 <= len(code_verifier) <= 128`."
            )
        hashed = hashlib.sha256(code_verifier.encode("ascii")).digest()
        encoded = base64.urlsafe_b64encode(hashed)
        return encoded.decode("ascii").replace("=", "")


class AbstractOAuth2FlowHandler(ConfigFlow):
    """Handle an OAuth2 config flow."""

    DOMAIN = ""
    VERSION = 1

    def __init__(self) -> None:
        """Instantiate config flow."""
        super().__init__()
        self.external_data: Any = None
        self.flow_impl: AbstractOAuth2Implementation = None  # type: ignore[assignment]

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        raise NotImplementedError

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data that needs to be appended to the authorize url."""
        return {}

    async def async_generate_authorize_url(self) -> str:
        """Generate a url for the user to authorize."""
        url = await self.flow_impl.async_generate_authorize_url(self.flow_id)
        return str(URL(url).update_query(self.extra_authorize_data))

    async def async_step_pick_implementation(
        self, user_input: dict | None = None
    ) -> dict:
        """Handle a flow start."""
        try:
            implementations = await async_get_implementations(self.hass, self.DOMAIN)
        except ImplementationUnavailableError as err:
            self.logger.error(
                "No OAuth2 implementations available: %s",
                ", ".join(str(e) for e in err.args),
            )
            return self.async_abort(reason="oauth_implementation_unavailable")

        if user_input is not None:
            impl_key = user_input.get("implementation")
            if impl_key is None:
                return self.async_show_form(
                    step_id="pick_implementation",
                    errors={"base": "no_implementation_selected"},
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                "implementation", default=list(implementations)[0]
                            ): vol.In({key: impl.name for key, impl in implementations.items()})
                        }
                    ),
                )
            self.flow_impl = implementations[impl_key]
            return await self.async_step_auth()

        if not implementations:
            return self.async_abort(reason="missing_credentials")

        # Pick first implementation if we have only one
        if len(implementations) == 1:
            self.flow_impl = list(implementations.values())[0]
            return await self.async_step_auth()

        return self.async_show_form(
            step_id="pick_implementation",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "implementation", default=list(implementations)[0]
                    ): vol.In({key: impl.name for key, impl in implementations.items()})
                }
            ),
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Create an entry for auth."""
        # Flow has been triggered by external data (callback)
        if user_input is not None:
            self.external_data = user_input
            next_step = "authorize_rejected" if "error" in user_input else "creation"
            return self.async_external_step_done(next_step_id=next_step)

        try:
            async with asyncio.timeout(OAUTH_AUTHORIZE_URL_TIMEOUT_SEC):
                url = await self.async_generate_authorize_url()
        except TimeoutError as err:
            _LOGGER.error("Timeout generating authorize url: %s", err)
            return self.async_abort(reason="authorize_url_timeout")
        except RuntimeError:
            return self.async_abort(
                reason="no_url_available",
                description_placeholders={
                    "docs_url": (
                        "https://www.home-assistant.io/more-info/no-url-available"
                    )
                },
            )

        return self.async_external_step(step_id="auth", url=url)

    async def async_step_creation(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Create config entry from external data."""
        _LOGGER.debug("Creating config entry from external data")

        try:
            async with asyncio.timeout(OAUTH_TOKEN_TIMEOUT_SEC):
                token = await self.flow_impl.async_resolve_external_data(
                    self.external_data
                )
        except TimeoutError as err:
            _LOGGER.error("Timeout resolving OAuth token: %s", err)
            return self.async_abort(reason="oauth_timeout")
        except (OAuth2TokenRequestError, ClientError) as err:
            _LOGGER.error("Error resolving OAuth token: %s", err)
            if isinstance(err, OAuth2TokenRequestReauthError):
                return self.async_abort(reason="oauth_unauthorized")
            return self.async_abort(reason="oauth_failed")

        if "expires_in" not in token:
            _LOGGER.warning("Invalid token: %s", token)
            return self.async_abort(reason="oauth_error")

        try:
            token["expires_in"] = int(token["expires_in"])
        except ValueError as err:
            _LOGGER.warning("Error converting expires_in to int: %s", err)
            return self.async_abort(reason="oauth_error")
        token["expires_at"] = time.time() + token["expires_in"]

        self.logger.info("Successfully authenticated")

        return await self.async_oauth_create_entry(
            {"auth_implementation": self.flow_impl.domain, "token": token}
        )

    async def async_step_authorize_rejected(
        self, data: None = None
    ) -> dict:
        """Step to handle flow rejection."""
        return self.async_abort(
            reason="user_rejected_authorize",
            description_placeholders={"error": self.external_data["error"]},
        )

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Create an entry for the flow."""
        return self.async_create_entry(title=self.flow_impl.name, data=data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle a flow start."""
        return await self.async_step_pick_implementation(user_input)

    @classmethod
    def async_register_implementation(
        cls, hass, local_impl: LocalOAuth2Implementation
    ) -> None:
        """Register a local implementation."""
        async_register_implementation(hass, cls.DOMAIN, local_impl)


async def async_register_implementation(
    hass, domain: str, implementation: AbstractOAuth2Implementation
) -> None:
    """Register an OAuth2 flow implementation for an integration."""
    implementations = hass.data.setdefault(DATA_IMPLEMENTATIONS, {})
    implementations.setdefault(domain, {})[implementation.domain] = implementation


async def async_get_implementations(
    hass, domain: str
) -> dict[str, AbstractOAuth2Implementation]:
    """Return OAuth2 implementations for specified domain."""
    registered = hass.data.setdefault(DATA_IMPLEMENTATIONS, {}).get(domain, {})

    if DATA_PROVIDERS not in hass.data:
        return registered

    registered = dict(registered)
    exceptions = []
    for get_impl in list(hass.data[DATA_PROVIDERS].values()):
        try:
            for impl in await get_impl(hass, domain):
                registered[impl.domain] = impl
        except ImplementationUnavailableError as err:
            exceptions.append(err)

    if not registered and exceptions:
        raise ImplementationUnavailableError(*exceptions)

    return registered


async def async_get_config_entry_implementation(
    hass, config_entry
) -> AbstractOAuth2Implementation:
    """Return the implementation for this config entry."""
    implementations = await async_get_implementations(hass, config_entry.domain)
    implementation = implementations.get(config_entry.data["auth_implementation"])

    if implementation is None:
        raise ValueError("Implementation not available")

    return implementation


def async_add_implementation_provider(
    hass,
    provider_domain: str,
    async_provide_implementation: Callable[
        [Any, str], Awaitable[list[AbstractOAuth2Implementation]]
    ],
) -> None:
    """Add an implementation provider."""
    hass.data.setdefault(DATA_PROVIDERS, {})[provider_domain] = (
        async_provide_implementation
    )


class OAuth2Session:
    """Session to make requests authenticated with OAuth2."""

    def __init__(
        self,
        hass,
        config_entry,
        implementation: AbstractOAuth2Implementation,
    ) -> None:
        """Initialize an OAuth2 session."""
        self.hass = hass
        self.config_entry = config_entry
        self.implementation = implementation
        self._token_lock = Lock()

    @property
    def token(self) -> dict:
        """Return the token."""
        return cast(dict, self.config_entry.data["token"])

    @property
    def valid_token(self) -> bool:
        """Return if token is still valid."""
        return (
            cast(float, self.token["expires_at"])
            > time.time() + CLOCK_OUT_OF_SYNC_MAX_SEC
        )

    async def async_ensure_token_valid(self) -> None:
        """Ensure that the current token is valid."""
        async with self._token_lock:
            if self.valid_token:
                return

            new_token = await self.implementation.async_refresh_token(self.token)

            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, "token": new_token}
            )

    async def async_request(
        self, method: str, url: str, **kwargs: Any
    ) -> Any:
        """Make a request."""
        await self.async_ensure_token_valid()
        return await async_oauth2_request(
            self.hass, self.config_entry.data["token"], method, url, **kwargs
        )


async def async_oauth2_request(
    hass, token: dict, method: str, url: str, **kwargs: Any
) -> Any:
    """Make an OAuth2 authenticated request.

    This method will not refresh tokens. Use OAuth2 session for that.
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    headers = kwargs.pop("headers", {})
    return await session.request(
        method,
        url,
        **kwargs,
        headers={
            **headers,
            "authorization": f"Bearer {token['access_token']}",
        },
    )


def _encode_jwt(hass, data: dict) -> str:
    """JWT encode data."""
    if (secret := hass.data.get(DATA_JWT_SECRET)) is None:
        secret = hass.data[DATA_JWT_SECRET] = secrets.token_hex()

    return jwt.encode(data, secret, algorithm="HS256")


def _decode_jwt(hass, encoded: str) -> dict[str, Any] | None:
    """JWT decode data."""
    secret: str | None = hass.data.get(DATA_JWT_SECRET)

    if secret is None:
        return None

    try:
        return jwt.decode(encoded, secret, algorithms=["HS256"])  # type: ignore[no-any-return]
    except jwt.InvalidTokenError:
        return None
