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

        Receives authorization code from OAuth provider and resumes
        the config flow.
        """
        state_param = request.query_params.get("state")
        code = request.query_params.get("code")
        error = request.query_params.get("error")

        if not state_param:
            return HTMLResponse(
                content="<h1>Missing state parameter</h1>", status_code=400
            )

        hass = shim_manager.get_hass()
        from ...stubs.oauth2 import _decode_jwt

        state = _decode_jwt(hass, state_param)
        if state is None:
            return HTMLResponse(
                content="<h1>Invalid state parameter</h1><p>The authorization link may have expired.</p>",
                status_code=400,
            )

        flow_id = state.get("flow_id")
        if not flow_id:
            return HTMLResponse(
                content="<h1>Missing flow ID in state</h1>", status_code=400
            )

        user_input = {"state": state}
        if code:
            user_input["code"] = code
        elif error:
            user_input["error"] = error
        else:
            return HTMLResponse(
                content="<h1>Missing code or error parameter</h1>", status_code=400
            )

        # Resume the config flow
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input
        )

        # Close the popup window and show a message
        if result.get("type") == "abort":
            return HTMLResponse(
                content=f"""
                <h1>Authorization Failed</h1>
                <p>{result.get('reason', 'Unknown error')}</p>
                <script>window.close()</script>
                """,
                status_code=400,
            )

        return HTMLResponse(
            content="""
            <h1>Authorization Successful</h1>
            <p>You can close this window and return to the config flow.</p>
            <script>window.close()</script>
            """
        )
