"""Tests for the integration detail page and its async version/release-note fragments.

The detail page renders immediately and pulls the GitHub-dependent sections
(Available Versions, Release Notes) in via HTMX after paint. These tests
verify the templates render with the right context and that no
``releases``/``release_notes`` context is required for the detail page.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shim.web.renderers import render_template

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "shim" / "web" / "templates"


def _base_integration() -> dict:
    """Minimal integration dict compatible with integration_detail.html."""
    return {
        "domain": "dreo",
        "name": "Dreo",
        "version": "1.0.0",
        "description": "Dreo integration",
        "source": "hacs_default",
        "repository_url": "https://github.com/owner/dreo",
        "enabled": True,
        "update_available": False,
        "latest_version": None,
    }


class TestDetailPageRendersWithoutReleasesContext:
    """The detail page must render without awaiting any GitHub calls."""

    def test_renders_without_releases_or_release_notes(self):
        """No releases/release_notes keys needed — page should render fine."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_detail.html",
            request=MagicMock(),
            integration=_base_integration(),
            entries=[],
            entities=[],
            devices=[],
        )
        # Header / core info always present
        assert "Integration Details" in html
        assert "dreo" in html
        # No longer references template vars that the route no longer passes
        assert "Available Versions" not in html  # gated inside async fragment now

    def test_versions_placeholder_present_when_repository_url(self):
        """With a repository_url, an HTMX loading placeholder is emitted."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_detail.html",
            request=MagicMock(),
            integration=_base_integration(),
            entries=[],
            entities=[],
            devices=[],
        )
        assert 'id="versions-section"' in html
        assert 'hx-get="../integrations/dreo/versions"' in html
        assert "Loading versions from GitHub" in html

    def test_versions_placeholder_absent_without_repository_url(self):
        """Without a repository_url, no versions placeholder is emitted."""
        info = _base_integration()
        info["repository_url"] = ""
        html = render_template(
            TEMPLATE_DIR,
            "integration_detail.html",
            request=MagicMock(),
            integration=info,
            entries=[],
            entities=[],
            devices=[],
        )
        assert 'id="versions-section"' not in html

    def test_release_notes_placeholder_present_when_update_available(self):
        """With an update available, a release-notes loading placeholder appears."""
        info = _base_integration()
        info["update_available"] = True
        info["latest_version"] = "1.1.0"
        html = render_template(
            TEMPLATE_DIR,
            "integration_detail.html",
            request=MagicMock(),
            integration=info,
            entries=[],
            entities=[],
            devices=[],
        )
        assert 'id="release-notes-content"' in html
        assert 'hx-get="../integrations/dreo/release-notes"' in html
        assert "Loading release notes from GitHub" in html

    def test_release_notes_placeholder_absent_when_no_update(self):
        """No update means no release-notes section at all."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_detail.html",
            request=MagicMock(),
            integration=_base_integration(),  # update_available False
            entries=[],
            entities=[],
            devices=[],
        )
        assert 'id="release-notes-content"' not in html


class TestVersionsFragment:
    """integration_versions.html fragment template behavior."""

    def test_renders_versions_dropdown(self):
        releases = [
            {"version": "1.1.0", "body_html": "<p>notes</p>", "url": "u",
             "published_at": "2024-01-01", "prerelease": False,
             "tag_only": False, "branch": False},
            {"version": "main", "body_html": "", "url": "", "published_at": "",
             "prerelease": False, "tag_only": False, "branch": True},
        ]
        html = render_template(
            TEMPLATE_DIR,
            "integration_versions.html",
            request=MagicMock(),
            integration=_base_integration(),
            releases=releases,
            error=None,
        )
        assert '<h3 style="margin-bottom: 1rem;">Available Versions</h3>' in html
        assert "1.1.0" in html
        assert "[branch]" in html
        assert "Install Selected Version" in html
        assert "Install Ref" in html

    def test_empty_releases_emits_nothing(self):
        """No releases and no error -> empty fragment (block hides itself)."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_versions.html",
            request=MagicMock(),
            integration=_base_integration(),
            releases=[],
            error=None,
        )
        assert "Available Versions" not in html
        assert html.strip() == ""

    def test_error_renders_alert(self):
        """On GitHub failure, an error alert is shown instead of a dropdown."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_versions.html",
            request=MagicMock(),
            integration=_base_integration(),
            releases=[],
            error="boom",
        )
        assert "Failed to load available versions from GitHub." in html


class TestReleaseNotesFragment:
    """integration_release_notes.html fragment template behavior."""

    def test_renders_release_cards(self):
        notes = [
            {"version": "1.1.0", "body_html": "<p>changelog</p>",
             "url": "https://github.com/owner/dreo/releases/1.1.0",
             "published_at": "2024-01-01"},
        ]
        html = render_template(
            TEMPLATE_DIR,
            "integration_release_notes.html",
            request=MagicMock(),
            release_notes=notes,
            error=None,
        )
        assert "1.1.0" in html
        assert "<p>changelog</p>" in html
        assert "View on GitHub" in html

    def test_empty_shows_placeholder_message(self):
        """No notes (and no error) -> a friendly 'no release notes' message."""
        html = render_template(
            TEMPLATE_DIR,
            "integration_release_notes.html",
            request=MagicMock(),
            release_notes=[],
            error=None,
        )
        assert "No release notes available." in html

    def test_error_renders_alert(self):
        html = render_template(
            TEMPLATE_DIR,
            "integration_release_notes.html",
            request=MagicMock(),
            release_notes=[],
            error="boom",
        )
        assert "Failed to load release notes from GitHub." in html


class TestFragmentRouteHandlers:
    """Smoke-test the new fragment route handlers with a mocked shim_manager."""

    def _build_app(self, info, versions=None, notes=None, versions_error=None):
        """Register the integrations routes against a fake app/shim_manager."""
        from fastapi import FastAPI
        from shim.web.routes.integrations import register_routes

        app = FastAPI()
        shim_manager = MagicMock()

        integration_manager = MagicMock()
        shim_manager.get_integration_manager.return_value = integration_manager
        integration_manager.get_integration.return_value = info

        integration_manager.get_available_versions = AsyncMock(
            side_effect=Exception(versions_error) if versions_error
            else AsyncMock(return_value=versions or [])
        )
        integration_manager.get_release_notes = AsyncMock(return_value=notes or [])
        register_routes(app, shim_manager, TEMPLATE_DIR)
        return app

    @pytest.mark.asyncio
    async def test_versions_fragment_calls_get_available_versions(self):
        from starlette.testclient import TestClient

        info = MagicMock()
        info.repository_url = "https://github.com/owner/dreo"
        info.to_dict.return_value = _base_integration()
        app = self._build_app(
            info,
            versions=[{"version": "1.1.0", "body": "*x*", "published_at": "",
                        "url": "", "prerelease": False, "tag_only": False,
                        "branch": False}],
        )
        client = TestClient(app)
        r = client.get("/integrations/dreo/versions")
        assert r.status_code == 200
        assert "1.1.0" in r.text
        assert "Available Versions" in r.text

    @pytest.mark.asyncio
    async def test_versions_fragment_no_repo_returns_empty(self):
        from starlette.testclient import TestClient

        info = MagicMock()
        info.repository_url = ""  # no repo -> short circuit
        app = self._build_app(info)
        client = TestClient(app)
        r = client.get("/integrations/dreo/versions")
        assert r.status_code == 200
        assert r.text == ""

    @pytest.mark.asyncio
    async def test_release_notes_fragment_skips_when_no_update(self):
        from starlette.testclient import TestClient

        info = MagicMock()
        info.repository_url = "https://github.com/owner/dreo"
        info.update_available = False  # no update -> short circuit
        app = self._build_app(info)
        client = TestClient(app)
        r = client.get("/integrations/dreo/release-notes")
        assert r.status_code == 200
        assert r.text == ""

    @pytest.mark.asyncio
    async def test_release_notes_fragment_renders_notes(self):
        from starlette.testclient import TestClient

        info = MagicMock()
        info.repository_url = "https://github.com/owner/dreo"
        info.update_available = True
        app = self._build_app(
            info,
            notes=[{"version": "1.1.0", "body": "**big**", "published_at": "",
                    "url": "u"}],
        )
        client = TestClient(app)
        r = client.get("/integrations/dreo/release-notes")
        assert r.status_code == 200
        assert "1.1.0" in r.text
        assert "View on GitHub" in r.text