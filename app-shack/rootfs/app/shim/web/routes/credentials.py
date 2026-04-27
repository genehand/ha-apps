"""Application credentials management routes.

Handles listing, creating, and deleting OAuth2 application credentials
for integrations.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..renderers import render_template

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register credential management routes."""

    @app.get("/credentials", response_class=HTMLResponse)
    async def credentials_list(request: Request):
        """List all integrations with application credentials."""
        hass = shim_manager.get_hass()
        from ...stubs.application_credentials import DATA_COMPONENT

        storage = hass.data.get(DATA_COMPONENT)
        domains = []

        # Find all domains that have credentials
        if storage:
            seen = set()
            for item in storage.async_items():
                domain = item.get("domain")
                if domain and domain not in seen:
                    seen.add(domain)
                    info = shim_manager.get_integration_manager().get_integration(domain)
                    domains.append({
                        "domain": domain,
                        "name": info.name if info else domain,
                    })

        html = render_template(
            template_dir,
            "credentials.html",
            request=request,
            domains=domains,
            current_domain=None,
        )
        return HTMLResponse(content=html)

    @app.get("/credentials/{domain}", response_class=HTMLResponse)
    async def credentials_domain(request: Request, domain: str):
        """Show credentials for a specific integration."""
        hass = shim_manager.get_hass()
        from ...stubs.application_credentials import DATA_COMPONENT

        storage = hass.data.get(DATA_COMPONENT)
        credentials_list = []
        all_domains = []

        if storage:
            seen = set()
            for item in storage.async_items():
                item_domain = item.get("domain")
                if item_domain and item_domain not in seen:
                    seen.add(item_domain)
                    info = shim_manager.get_integration_manager().get_integration(item_domain)
                    all_domains.append({
                        "domain": item_domain,
                        "name": info.name if info else item_domain,
                    })
                if item_domain == domain:
                    credentials_list.append({
                        "id": item.get("id"),
                        "client_id": item.get("client_id"),
                        "name": item.get("name"),
                    })

        info = shim_manager.get_integration_manager().get_integration(domain)
        html = render_template(
            template_dir,
            "credentials.html",
            request=request,
            domains=all_domains,
            current_domain=domain,
            current_name=info.name if info else domain,
            credentials=credentials_list,
        )
        return HTMLResponse(content=html)

    @app.post("/credentials/{domain}")
    async def credentials_create(request: Request, domain: str):
        """Create a new application credential."""
        form_data = await request.form()
        hass = shim_manager.get_hass()
        from ...stubs.application_credentials import (
            DATA_COMPONENT,
        )

        storage = hass.data.get(DATA_COMPONENT)
        if storage is None:
            raise HTTPException(
                status_code=500, detail="Application credentials not initialized"
            )

        storage.async_create_item({
            "domain": domain,
            "client_id": form_data.get("client_id", "").strip(),
            "client_secret": form_data.get("client_secret", "").strip(),
            "name": form_data.get("name", "").strip() or None,
        })

        response = HTMLResponse(
            '<div class="alert alert-success">Credential saved successfully.</div>'
        )
        response.headers["HX-Redirect"] = f"./{domain}"
        return response

    @app.delete("/credentials/{domain}/{item_id}")
    async def credentials_delete(request: Request, domain: str, item_id: str):
        """Delete an application credential."""
        hass = shim_manager.get_hass()
        from ...stubs.application_credentials import DATA_COMPONENT

        storage = hass.data.get(DATA_COMPONENT)
        if storage is None:
            raise HTTPException(
                status_code=500, detail="Application credentials not initialized"
            )

        if storage.async_delete_item(item_id):
            return HTMLResponse(
                '<tr><td colspan="3" class="alert alert-success">Credential deleted.</td></tr>'
            )
        else:
            raise HTTPException(status_code=404, detail="Credential not found")
