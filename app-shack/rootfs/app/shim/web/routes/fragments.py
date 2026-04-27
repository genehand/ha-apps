"""HTML fragment and custom repository management routes.

Provides HTMX-friendly HTML fragments for MQTT status, overall status,
and custom repository CRUD operations.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from ..renderers import render_custom_repos_list, render_template

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register fragment and custom repo routes."""

    # ------------------------------------------------------------------ #
    #  HTML fragments (for HTMX polling / partial updates)
    # ------------------------------------------------------------------ #

    @app.get("/mqtt-status-fragment", response_class=HTMLResponse)
    async def mqtt_status_fragment():
        """HTML fragment for MQTT status display (used by HTMX)."""
        mqtt_bridge = shim_manager.get_mqtt_bridge()
        mqtt_status = (
            mqtt_bridge.connection_status
            if mqtt_bridge
            else {"connected": False, "error": "MQTT bridge not available"}
        )

        return render_template(
            template_dir,
            "mqtt_status.html",
            mqtt_status=mqtt_status,
        )

    @app.get("/status-fragment", response_class=HTMLResponse)
    async def status_fragment():
        """HTML fragment for status display (used by HTMX)."""
        loaded_integrations = (
            shim_manager.get_integration_loader().get_loaded_integrations()
        )
        total_entities = len(
            shim_manager.get_integration_loader().get_entities()
        )

        mqtt_bridge = shim_manager.get_mqtt_bridge()
        mqtt_status = (
            mqtt_bridge.connection_status
            if mqtt_bridge
            else {"connected": False, "error": "MQTT bridge not available"}
        )

        return render_template(
            template_dir,
            "status.html",
            loaded_integrations=loaded_integrations,
            total_entities=total_entities,
            mqtt_status=mqtt_status,
        )

    # ------------------------------------------------------------------ #
    #  Custom repositories (CRUD)
    # ------------------------------------------------------------------ #

    @app.post("/custom-repos")
    async def add_custom_repo(request: Request, repo_url: str = Form(...)):
        """Add a custom repository."""
        success, message = (
            await shim_manager.get_integration_manager().add_custom_repository(
                repo_url
            )
        )
        custom_repos = (
            shim_manager.get_integration_manager().get_custom_repositories()
        )
        if success:
            response = render_custom_repos_list(
                custom_repos, success_message=message
            )
            response.headers["HX-Location"] = "#custom"
            return response
        else:
            return render_custom_repos_list(
                custom_repos, error_message=message
            )

    @app.delete("/custom-repos/{domain}")
    async def remove_custom_repo(domain: str):
        """Remove a custom repository."""
        success, message = (
            await shim_manager.get_integration_manager().remove_custom_repository(
                domain
            )
        )
        custom_repos = (
            shim_manager.get_integration_manager().get_custom_repositories()
        )
        if success:
            response = render_custom_repos_list(
                custom_repos, success_message=message
            )
            response.headers["HX-Location"] = "#custom"
            return response
        else:
            return render_custom_repos_list(
                custom_repos, error_message=message
            )
