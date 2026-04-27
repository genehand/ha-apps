"""Tests for OAuth2 shim implementation."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from shim.stubs.oauth2 import (
    AbstractOAuth2Implementation,
    LocalOAuth2Implementation,
    LocalOAuth2ImplementationWithPkce,
    OAuth2Session,
    _encode_jwt,
    _decode_jwt,
    async_register_implementation,
    async_get_implementations,
    async_get_config_entry_implementation,
    async_get_redirect_uri,
    ImplementationUnavailableError,
)
from shim.stubs.application_credentials import (
    ApplicationCredentialsStorage,
    ClientCredential,
    AuthorizationServer,
    AuthImplementation,
    setup_application_credentials,
)
from shim.ha_fetched.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)


class TestJWTState:
    """Test JWT state encode/decode for OAuth callbacks."""

    def test_encode_decode_roundtrip(self):
        """Test JWT encoding and decoding roundtrip."""
        hass = MagicMock()
        hass.data = {}

        data = {"flow_id": "test_flow_123", "redirect_uri": "http://test/callback"}
        encoded = _encode_jwt(hass, data)

        assert isinstance(encoded, str)

        decoded = _decode_jwt(hass, encoded)
        assert decoded == data

    def test_decode_invalid_token(self):
        """Test decoding an invalid JWT returns None."""
        hass = MagicMock()
        hass.data = {}

        assert _decode_jwt(hass, "invalid_token") is None

    def test_decode_with_different_secret(self):
        """Test decoding with wrong secret returns None."""
        hass1 = MagicMock()
        hass1.data = {}
        hass2 = MagicMock()
        hass2.data = {}

        data = {"flow_id": "test"}
        encoded = _encode_jwt(hass1, data)

        # Different hass instance has different secret
        assert _decode_jwt(hass2, encoded) is None


class TestAsyncGetRedirectUri:
    """Test redirect URI computation."""

    def test_from_hass_data(self):
        """Test redirect URI from hass.data."""
        hass = MagicMock()
        hass.data = {"_oauth2_redirect_uri": "http://ingress/callback"}
        hass.config.external_url = None

        assert async_get_redirect_uri(hass) == "http://ingress/callback"

    def test_from_external_url(self):
        """Test redirect URI from hass.config.external_url."""
        hass = MagicMock()
        hass.data = {}
        hass.config.external_url = "https://ha.example.com"

        assert async_get_redirect_uri(hass) == "https://ha.example.com/auth/external/callback"

    def test_external_url_trailing_slash(self):
        """Test external_url with trailing slash is handled."""
        hass = MagicMock()
        hass.data = {}
        hass.config.external_url = "https://ha.example.com/"

        assert async_get_redirect_uri(hass) == "https://ha.example.com/auth/external/callback"

    def test_missing_url_raises(self):
        """Test RuntimeError when no redirect URI is available."""
        hass = MagicMock()
        hass.data = {}
        hass.config.external_url = None

        with pytest.raises(RuntimeError):
            async_get_redirect_uri(hass)


class TestLocalOAuth2Implementation:
    """Test LocalOAuth2Implementation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock hass with redirect URI configured."""
        hass = MagicMock()
        hass.data = {"_oauth2_redirect_uri": "http://test/callback"}
        hass.config.external_url = None
        return hass

    def test_properties(self, mock_hass):
        """Test basic properties."""
        impl = LocalOAuth2Implementation(
            mock_hass, "smartcar", "client_id", "secret",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        assert impl.name == "Local application credentials"
        assert impl.domain == "smartcar"
        assert impl.redirect_uri == "http://test/callback"

    @pytest.mark.asyncio
    async def test_generate_authorize_url(self, mock_hass):
        """Test authorize URL generation includes required params."""
        impl = LocalOAuth2Implementation(
            mock_hass, "smartcar", "client_id", "secret",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        url = await impl.async_generate_authorize_url("flow_123")

        assert "https://auth.example.com/authorize" in url
        assert "response_type=code" in url
        assert "client_id=client_id" in url
        assert "redirect_uri=" in url
        assert "state=" in url

    @pytest.mark.asyncio
    async def test_resolve_external_data(self, mock_hass):
        """Test resolving authorization code to tokens."""
        impl = LocalOAuth2Implementation(
            mock_hass, "smartcar", "client_id", "secret",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "test_token",
            "refresh_token": "refresh_token",
            "expires_in": 7200,
        })
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_response)

        # Patch the module-level import target
        with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=mock_session):
            token = await impl.async_resolve_external_data({
                "code": "auth_code_123",
                "state": {"redirect_uri": "http://test/callback"},
            })

        assert token["access_token"] == "test_token"
        assert token["refresh_token"] == "refresh_token"

    @pytest.mark.asyncio
    async def test_refresh_token(self, mock_hass):
        """Test token refresh."""
        impl = LocalOAuth2Implementation(
            mock_hass, "smartcar", "client_id", "secret",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "new_token",
            "expires_in": 7200,
        })
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_response)

        with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=mock_session):
            new_token = await impl.async_refresh_token({
                "access_token": "old_token",
                "refresh_token": "refresh_123",
                "expires_at": time.time() - 100,
            })

        assert new_token["access_token"] == "new_token"
        assert "expires_at" in new_token
        assert new_token["expires_at"] > time.time()

    @pytest.mark.asyncio
    async def test_token_request_error_400(self, mock_hass):
        """Test 400 error raises OAuth2TokenRequestReauthError."""
        impl = LocalOAuth2Implementation(
            mock_hass, "smartcar", "client_id", "secret",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value='{"error": "invalid_grant"}')

        # Build a proper ClientResponseError
        req_info = MagicMock()
        err = aiohttp.ClientResponseError(
            request_info=req_info,
            history=(),
            status=400,
            message="Bad Request",
        )
        mock_response.raise_for_status = MagicMock(side_effect=err)

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_response)

        with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=mock_session):
            with pytest.raises(OAuth2TokenRequestReauthError):
                await impl._token_request({"grant_type": "refresh_token"})


class TestLocalOAuth2ImplementationWithPkce:
    """Test PKCE variant."""

    def test_code_verifier_generation(self):
        """Test code verifier generation."""
        verifier = LocalOAuth2ImplementationWithPkce.generate_code_verifier(128)
        assert len(verifier) == 128
        assert 43 <= len(verifier) <= 128

    def test_code_challenge_computation(self):
        """Test code challenge computation."""
        verifier = LocalOAuth2ImplementationWithPkce.generate_code_verifier(128)
        challenge = LocalOAuth2ImplementationWithPkce.compute_code_challenge(verifier)

        assert len(challenge) > 0
        assert "=" not in challenge  # base64urlsafe without padding

    @pytest.mark.asyncio
    async def test_extra_authorize_data(self):
        """Test PKCE params in authorize URL."""
        hass = MagicMock()
        hass.data = {"_oauth2_redirect_uri": "http://test/callback"}

        impl = LocalOAuth2ImplementationWithPkce(
            hass, "smartcar", "client_id",
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
        )

        url = await impl.async_generate_authorize_url("flow_123")

        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url


class TestOAuth2Session:
    """Test OAuth2Session token management."""

    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.data = {}
        return hass

    def test_valid_token(self, mock_hass):
        """Test token validity check."""
        config_entry = MagicMock()
        config_entry.data = {
            "token": {
                "access_token": "test",
                "expires_at": time.time() + 3600,
            }
        }

        impl = MagicMock()
        session = OAuth2Session(mock_hass, config_entry, impl)

        assert session.valid_token is True

    def test_expired_token(self, mock_hass):
        """Test expired token detection."""
        config_entry = MagicMock()
        config_entry.data = {
            "token": {
                "access_token": "test",
                "expires_at": time.time() - 100,
            }
        }

        impl = MagicMock()
        session = OAuth2Session(mock_hass, config_entry, impl)

        assert session.valid_token is False

    @pytest.mark.asyncio
    async def test_ensure_token_valid_refresh(self, mock_hass):
        """Test auto-refresh on expired token."""
        config_entry = MagicMock()
        config_entry.data = {
            "token": {
                "access_token": "old",
                "refresh_token": "refresh",
                "expires_at": time.time() - 100,
            }
        }

        impl = MagicMock()
        impl.async_refresh_token = AsyncMock(return_value={
            "access_token": "new",
            "refresh_token": "refresh",
            "expires_at": time.time() + 3600,
        })

        session = OAuth2Session(mock_hass, config_entry, impl)
        await session.async_ensure_token_valid()

        impl.async_refresh_token.assert_called_once()
        mock_hass.config_entries.async_update_entry.assert_called_once()


class TestAsyncGetImplementations:
    """Test implementation registry."""

    @pytest.mark.asyncio
    async def test_register_and_get(self):
        """Test registering and retrieving implementations."""
        hass = MagicMock()
        hass.data = {}

        impl = MagicMock()
        impl.domain = "test_impl"

        await async_register_implementation(hass, "smartcar", impl)
        implementations = await async_get_implementations(hass, "smartcar")

        assert "test_impl" in implementations
        assert implementations["test_impl"] == impl

    @pytest.mark.asyncio
    async def test_empty_raises_when_providers_fail(self):
        """Test empty implementations with failing providers."""
        hass = MagicMock()
        hass.data = {
            "oauth2_providers": {
                "test": AsyncMock(side_effect=ImplementationUnavailableError("fail"))
            }
        }

        with pytest.raises(ImplementationUnavailableError):
            await async_get_implementations(hass, "smartcar")


class TestApplicationCredentialsStorage:
    """Test ApplicationCredentialsStorage."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create a temporary storage instance."""
        return ApplicationCredentialsStorage(tmp_path)

    def test_create_and_list(self, storage):
        """Test creating and listing credentials."""
        storage.async_create_item({
            "domain": "smartcar",
            "client_id": "my_client",
            "client_secret": "my_secret",
            "name": "Test App",
        })

        items = storage.async_items()
        assert len(items) == 1
        assert items[0]["domain"] == "smartcar"
        assert items[0]["client_id"] == "my_client"

    def test_client_credentials_for_domain(self, storage):
        """Test retrieving credentials for a specific domain."""
        storage.async_create_item({
            "domain": "smartcar",
            "client_id": "client1",
            "client_secret": "secret1",
        })
        storage.async_create_item({
            "domain": "other",
            "client_id": "client2",
            "client_secret": "secret2",
        })

        creds = storage.async_client_credentials("smartcar")
        assert len(creds) == 1
        assert "client1" in [c.client_id for c in creds.values()]

    def test_delete_item(self, storage):
        """Test deleting a credential."""
        storage.async_create_item({
            "domain": "smartcar",
            "client_id": "client1",
            "client_secret": "secret1",
        })

        items = storage.async_items()
        item_id = items[0]["id"]

        assert storage.async_delete_item(item_id) is True
        assert len(storage.async_items()) == 0
        assert storage.async_delete_item(item_id) is False

    def test_persistence(self, tmp_path):
        """Test credentials persist to disk."""
        storage1 = ApplicationCredentialsStorage(tmp_path)
        storage1.async_create_item({
            "domain": "smartcar",
            "client_id": "persist_client",
            "client_secret": "persist_secret",
        })

        # Create new instance reading same file
        storage2 = ApplicationCredentialsStorage(tmp_path)
        items = storage2.async_items()
        assert len(items) == 1
        assert items[0]["client_id"] == "persist_client"


class TestAuthImplementation:
    """Test AuthImplementation bridging credentials to OAuth2."""

    @pytest.mark.asyncio
    async def test_name_from_credential(self):
        """Test name uses credential name if available."""
        hass = MagicMock()
        hass.data = {"_oauth2_redirect_uri": "http://test/callback"}
        hass.config.external_url = None

        cred = ClientCredential(client_id="id", client_secret="secret", name="My App")
        server = AuthorizationServer(
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )

        impl = AuthImplementation(hass, "smartcar", cred, server)
        assert impl.name == "My App"

    @pytest.mark.asyncio
    async def test_name_fallback_to_client_id(self):
        """Test name falls back to client_id when no name."""
        hass = MagicMock()
        hass.data = {"_oauth2_redirect_uri": "http://test/callback"}
        hass.config.external_url = None

        cred = ClientCredential(client_id="my_id", client_secret="secret")
        server = AuthorizationServer(
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )

        impl = AuthImplementation(hass, "smartcar", cred, server)
        assert impl.name == "my_id"
