"""OAuth2 authentication callback route.

Handles the external OAuth2 callback from providers after the user
completes authorization in a popup window. Resumes the config flow
with the authorization code.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register OAuth2 callback routes."""

    @app.get("/auth/external/callback")
    async def oauth_callback(request: Request):
        """Handle OAuth2 external callback.

        Because the app runs behind Home Assistant ingress, this endpoint
        is reached via a localhost redirect that will normally fail. The
        user copies the full failed URL and pastes it back into the config
        form to complete the flow.  If the request *does* reach us directly
        (e.g. local testing), we show a simple instruction page.
        """
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        callback_url = str(request.url)

        if error:
            return HTMLResponse(
                content=f"""
                <h1>Authorization Error</h1>
                <p>{error}</p>
                <p>Copy the URL below and paste it into the config form:</p>
                <code style="word-break: break-all;">{callback_url}</code>
                """,
                status_code=400,
            )

        if not code or not state:
            return HTMLResponse(
                content="""
                <h1>OAuth Callback</h1>
                <p>This page is not reachable from Home Assistant ingress.</p>
                <p>Copy the full URL from your browser's address bar and
                paste it into the config form in the Shack UI.</p>
                """,
                status_code=400,
            )

        return HTMLResponse(
            content=f"""
            <h1>Authorization Successful</h1>
            <p>Copy the URL below and paste it into the config form to
            complete the setup:</p>
            <code style="word-break: break-all;">{callback_url}</code>
            """
        )
