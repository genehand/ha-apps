"""GitHub authentication routes.

Implements a web UI for the OAuth device flow exposed by
`shim.github.GitHubAuth`. The flow is HTMX-driven:

  1. User clicks "Sign in with GitHub" on `/github/auth` (GET).
  2. POST `/github/auth/start` registers a device code with GitHub and
     returns a fragment showing the user_code + a link to
     https://github.com/login/device.
  3. The fragment polls `/github/auth/poll` every 2 seconds via
     `hx-trigger="every 2s"`. When the user completes the flow, the
     background activation task stores the token and the poll endpoint
     returns a success fragment.
  4. `/github/auth/logout` (POST) clears the token.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from ..renderers import render_template

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register GitHub auth routes."""

    @app.get("/github/auth", response_class=HTMLResponse)
    async def github_auth_page(request: Request):
        """Settings page for GitHub authentication."""
        github_auth = shim_manager.get_github_auth()
        rate_limit = None
        if github_auth.is_authenticated():
            # Best-effort probe so the user can see remaining quota.
            rate_limit = await github_auth.check_rate_limit()
        html = render_template(
            template_dir,
            "github_auth.html",
            request=request,
            authenticated=github_auth.is_authenticated(),
            activation_status=github_auth.get_activation_status(),
            rate_limit=rate_limit,
        )
        return HTMLResponse(content=html)

    @app.post("/github/auth/start", response_class=HTMLResponse)
    async def github_auth_start():
        """Start a device-flow registration; returns the polling fragment."""
        github_auth = shim_manager.get_github_auth()
        try:
            registration = await github_auth.register_device()
        except Exception as e:
            _LOGGER.exception("Failed to start GitHub device flow")
            return HTMLResponse(
                content=(
                    '<div class="alert alert-error" role="alert">'
                    f"Could not start GitHub sign-in: {e}. "
                    '<a href="./auth">Try again</a>.'
                    "</div>"
                ),
                status_code=500,
            )

        user_code = registration.get("user_code", "")
        verification_uri = registration.get(
            "verification_uri", "https://github.com/login/device"
        )
        expires_in = registration.get("expires_in", 900)

        return HTMLResponse(
            content=_render_polling_fragment(user_code, verification_uri, expires_in)
        )

    @app.get("/github/auth/poll", response_class=HTMLResponse)
    async def github_auth_poll():
        """Poll the activation status; HTMX polls this every 2s.

        On `success`: refresh the page so the user sees the authenticated
        state. On `error`: show the error and a retry link. On `pending`:
        return the polling fragment again (HTMX keeps polling).
        """
        github_auth = shim_manager.get_github_auth()
        status = github_auth.get_activation_status()
        state = status.get("status")

        if state == "success":
            # Hand the token to the IntegrationManager immediately so
            # authenticated API requests start working.
            shim_manager.get_integration_manager().set_github_token(
                github_auth.get_token()
            )
            # Reload the whole auth page so the rate-limit info shows up.
            response = HTMLResponse(content='<div hx-get="./auth" hx-trigger="load" hx-target="main" hx-swap="innerHTML"></div>')
            response.headers["HX-Redirect"] = "./auth"
            return response

        if state == "error":
            err = status.get("error", "Unknown error")
            return HTMLResponse(
                content=(
                    '<div class="alert alert-error" role="alert">'
                    f"GitHub sign-in failed: {err}. "
                    '<a href="./auth">Try again</a>.'
                    "</div>"
                )
            )

        # Pending — keep polling. Re-render the fragment with the latest
        # user_code (in case the flow was restarted) so the user always
        # sees a valid code.
        registration = (
            shim_manager.get_github_auth()._registration  # noqa: SLF001
        )
        user_code = (registration or {}).get("user_code", "")
        verification_uri = (registration or {}).get(
            "verification_uri", "https://github.com/login/device"
        )
        expires_in = (registration or {}).get("expires_in", 900)
        return HTMLResponse(
            content=_render_polling_fragment(
                user_code, verification_uri, expires_in
            )
        )

    @app.post("/github/auth/logout", response_class=HTMLResponse)
    async def github_auth_logout():
        """Clear the stored GitHub token."""
        github_auth = shim_manager.get_github_auth()
        github_auth.clear_token()
        # Tell the IntegrationManager to drop the token too.
        shim_manager.get_integration_manager().set_github_token(None)
        response = HTMLResponse(
            content=(
                '<div class="alert alert-success" role="alert">'
                "Signed out of GitHub. API requests are now anonymous "
                "(rate-limited to 60/hour)."
                "</div>"
            )
        )
        response.headers["HX-Redirect"] = "./auth"
        return response


def _render_polling_fragment(
    user_code: str, verification_uri: str, expires_in: int
) -> str:
    """Render the HTMX fragment shown while waiting for the user."""
    return f"""
    <div class="alert alert-info" role="alert" style="text-align: center;">
        <h3 style="margin: 0 0 0.5rem 0;">Enter this code at GitHub:</h3>
        <div style="font-size: 2rem; font-family: monospace; letter-spacing: 0.25em;
                    padding: 0.5rem 1rem; background: var(--pico-card-background-color);
                    border: 1px solid var(--pico-muted-border-color);
                    border-radius: var(--pico-border-radius);
                    display: inline-block; margin: 0.5rem 0;">
            {user_code}
        </div>
        <p style="margin: 1rem 0;">
            <a href="{verification_uri}" target="_blank" rel="noopener noreferrer"
               class="btn btn-primary" role="button">
                Open {verification_uri}
            </a>
        </p>
        <p class="pico-color-muted" style="font-size: 0.85rem; margin: 0;">
            Code expires in {expires_in} seconds. Waiting for you to confirm...
            <span class="spinner" style="width: 14px; height: 14px; margin-left: 6px; vertical-align: middle;"></span>
        </p>
    </div>
    <div hx-get="./auth/poll" hx-trigger="every 2s" hx-target="main" hx-swap="innerHTML"></div>
    """
