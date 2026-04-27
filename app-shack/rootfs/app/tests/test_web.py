"""Tests for the web application."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi import Request
from starlette.datastructures import URL

from shim.web.renderers import get_detail_redirect
from shim.web.routes.config_flows import _extract_oauth_params


class TestGetDetailRedirect:
    """Test the get_detail_redirect function."""

    def _create_request(
        self,
        url_path: str = "/integrations/cryptoinfo/enable",
        hx_current_url: str = None,
        x_ingress_path: str = None,
        referer: str = None,
    ) -> MagicMock:
        """Create a mocked request with specified headers."""
        request = MagicMock(spec=Request)
        request.url = MagicMock(spec=URL)
        request.url.path = url_path

        headers = {}
        if hx_current_url:
            headers["hx-current-url"] = hx_current_url
        if x_ingress_path:
            headers["x-ingress-path"] = x_ingress_path
        if referer:
            headers["referer"] = referer

        request.headers.get = lambda key, default="": headers.get(key, default)
        return request

    def test_from_detail_page_direct_access(self):
        """Test redirect when on detail page (direct access, no ingress)."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            hx_current_url="http://localhost:8080/integrations/cryptoinfo",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_detail_page_with_query_string(self):
        """Test redirect when URL has trailing query string."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/disable",
            hx_current_url="https://ha.example.org/integrations/cryptoinfo?",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_detail_page_with_fragment(self):
        """Test redirect when URL has fragment."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/disable",
            hx_current_url="http://localhost:8080/integrations/cryptoinfo#section",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_detail_page_ha_ingress(self):
        """Test redirect when on detail page through HA ingress."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            hx_current_url="https://ha.example.org/api/hassio_ingress/wPCKoeUJliWJ60Qf28SnRzY2LrfIgLH1Jdjk8YA-kPg/integrations/cryptoinfo",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_detail_page_ha_ingress_with_query(self):
        """Test redirect when on detail page through HA ingress with query string."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/disable",
            hx_current_url="https://ha.example.org/api/hassio_ingress/xxx/integrations/cryptoinfo?",
            x_ingress_path="/api/hassio_ingress/xxx",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_detail_page_using_ingress_path(self):
        """Test redirect using X-Ingress-Path when HX-Current-URL is missing."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            x_ingress_path="/api/hassio_ingress/xxx",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_from_index_page_direct_access(self):
        """Test redirect when on index page (direct access)."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            hx_current_url="http://localhost:8080/",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./integrations/cryptoinfo"

    def test_from_index_page_ha_ingress(self):
        """Test redirect when on index page through HA ingress."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            hx_current_url="https://ha.example.org/api/hassio_ingress/xxx/",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./integrations/cryptoinfo"

    def test_fallback_to_referer(self):
        """Test fallback to Referer header when HTMX headers are missing."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
            referer="http://localhost:8080/integrations/cryptoinfo",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        assert result == "./cryptoinfo"

    def test_no_headers_defaults_to_index(self):
        """Test default behavior when no source headers are present."""
        request = self._create_request(
            url_path="/integrations/cryptoinfo/enable",
        )
        result = get_detail_redirect(request, "cryptoinfo")
        # With no source info, defaults to index page behavior
        assert result == "./integrations/cryptoinfo"

    def test_domain_with_hyphens(self):
        """Test redirect with domain containing hyphens."""
        request = self._create_request(
            url_path="/integrations/my-integration/enable",
            hx_current_url="http://localhost:8080/integrations/my-integration",
        )
        result = get_detail_redirect(request, "my-integration")
        assert result == "./my-integration"

    def test_domain_with_underscores(self):
        """Test redirect with domain containing underscores."""
        request = self._create_request(
            url_path="/integrations/my_integration/enable",
            hx_current_url="http://localhost:8080/integrations/my_integration",
        )
        result = get_detail_redirect(request, "my_integration")
        assert result == "./my_integration"

    def test_from_config_flow_page(self):
        """Test redirect when on config flow page (/config/{domain})."""
        request = self._create_request(
            url_path="/config/nest_protect",
            hx_current_url="http://localhost:8080/config/nest_protect",
        )
        result = get_detail_redirect(request, "nest_protect")
        # From config flow page, should go up one level then to integrations
        assert result == "../integrations/nest_protect"

    def test_from_config_flow_reconfigure_page(self):
        """Test redirect when on options flow reconfigure page."""
        request = self._create_request(
            url_path="/config/nest_protect_123/reconfigure",
            hx_current_url="http://localhost:8080/config/nest_protect_123/reconfigure",
        )
        result = get_detail_redirect(request, "nest_protect")
        # From config flow page, should go up one level then to integrations
        assert result == "../integrations/nest_protect"


class TestExtractOAuthParams:
    """Test _extract_oauth_params helper."""

    def test_full_url_with_code_and_state(self):
        """Extract code and state from a full localhost callback URL."""
        url = (
            "http://localhost:8080/auth/external/callback"
            "?code=AQABC123&state=xyz789"
        )
        result = _extract_oauth_params(url)
        assert result["code"] == "AQABC123"
        assert result["state"] == "xyz789"
        assert result["error"] is None

    def test_url_with_error(self):
        """Extract error from a callback URL."""
        url = (
            "http://localhost:8080/auth/external/callback"
            "?error=access_denied&state=abc"
        )
        result = _extract_oauth_params(url)
        assert result["code"] is None
        assert result["state"] == "abc"
        assert result["error"] == "access_denied"

    def test_url_without_query_params(self):
        """Handle a URL with no query parameters."""
        url = "http://localhost:8080/auth/external/callback"
        result = _extract_oauth_params(url)
        assert result["code"] is None
        assert result["state"] is None
        assert result["error"] is None

    def test_code_only(self):
        """Extract just a code value."""
        url = "http://localhost:8080/auth/external/callback?code=SECRET123"
        result = _extract_oauth_params(url)
        assert result["code"] == "SECRET123"
        assert result["state"] is None
        assert result["error"] is None

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace is ignored."""
        url = "  http://localhost:8080/auth/external/callback?code=ABC&state=DEF  "
        result = _extract_oauth_params(url)
        assert result["code"] == "ABC"
        assert result["state"] == "DEF"

    def test_multiple_values(self):
        """When a param appears multiple times, the first value is used."""
        url = (
            "http://localhost:8080/auth/external/callback"
            "?code=first&code=second&state=s1&state=s2"
        )
        result = _extract_oauth_params(url)
        assert result["code"] == "first"
        assert result["state"] == "s1"
