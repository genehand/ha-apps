"""Integration management routes.

Handles listing, installing, enabling, disabling, updating, and removing
integrations. Also handles entity filtering and config entry reload.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import markdown

from ..const import PICO_COLORS_URL, PICO_CSS_URL
from ..renderers import (
    check_loading,
    get_detail_redirect,
    render_template,
)

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register integration management routes."""

    # ------------------------------------------------------------------ #
    #  Index: main page with integration list
    # ------------------------------------------------------------------ #

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Main page with integration list."""
        integrations = (
            shim_manager.get_integration_manager().get_all_integrations()
        )
        available = shim_manager.get_integration_manager().get_available_integrations()
        custom_repos = (
            shim_manager.get_integration_manager().get_custom_repositories()
        )

        # Convert to dicts for template compatibility
        integrations_dicts = [i.to_dict() for i in integrations]
        available_dicts = [
            {
                "full_name": a.get("full_name"),
                "domain": a.get("domain"),
                "name": a.get("name"),
                "description": a.get("description", ""),
                "installed": a.get("installed", False),
                "unsupported": a.get("unsupported", False),
                "unsupported_reason": a.get("unsupported_reason"),
                "verified": a.get("verified", False),
                "verified_version": a.get("verified_version"),
                "source": a.get("source", "hacs_default"),
                "stars": a.get("stars", 0),
                "downloads": a.get("downloads", 0),
                "repository_url": a.get("repository_url", ""),
            }
            for a in available
        ]

        html = render_template(
            template_dir,
            "index.html",
            request=request,
            integrations=integrations_dicts,
            available=available_dicts,
            custom_repos=custom_repos,
            is_loading=shim_manager.is_loading,
        )
        return HTMLResponse(content=html)

    # ------------------------------------------------------------------ #
    #  Integration detail page
    # ------------------------------------------------------------------ #

    @app.get("/integrations/{domain}", response_class=HTMLResponse)
    async def integration_detail(request: Request, domain: str):
        """Integration detail page."""
        info = shim_manager.get_integration_manager().get_integration(domain)
        if not info:
            raise HTTPException(status_code=404, detail="Integration not found")

        entries = shim_manager.get_hass().config_entries.async_entries(domain)
        entities = shim_manager.get_integration_loader().get_entities(
            integration_domain=domain
        )

        # Get device registry and fetch devices for this integration's entries
        hass = shim_manager.get_hass()
        device_registry = hass.data.get("device_registry")
        entry_ids = {e.entry_id for e in entries}
        devices = []
        if device_registry:
            for device_entry in device_registry._devices.values():
                # Check if device is associated with any of this integration's config entries
                if device_entry.config_entries & entry_ids:
                    devices.append(
                        {
                            "id": device_entry.id,
                            "name": device_entry.name or device_entry.id,
                            "manufacturer": device_entry.manufacturer,
                            "model": device_entry.model,
                            "sw_version": device_entry.sw_version,
                            "identifiers": list(device_entry.identifiers)
                            if device_entry.identifiers
                            else [],
                        }
                    )

        # Convert to dict for template compatibility
        info_dict = info.to_dict()
        entries_dicts = [
            {
                "entry_id": e.entry_id,
                "title": e.title,
                "data": e.data,
                "state": e.state,
                "options": e.options,
            }
            for e in entries
        ]
        entities_dicts = [
            {
                "entity_id": e.entity_id,
                "name": e.name or e.entity_id,
                "state": e.state,
                "available": e.available,
            }
            for e in entities
        ]

        # Fetch and render release notes if an update is available
        releases = []
        if info.update_available and info.repository_url:
            try:
                raw_releases = (
                    await shim_manager.get_integration_manager().get_release_notes(
                        domain
                    )
                )
                for rel in raw_releases:
                    body = rel.get("body") or ""
                    releases.append(
                        {
                            "version": rel["version"],
                            "body_html": markdown.markdown(body)
                            if body
                            else "<em>No release notes provided.</em>",
                            "url": rel.get("url", ""),
                            "published_at": (
                                rel.get("published_at", "")[:10]
                                if rel.get("published_at")
                                else ""
                            ),
                        }
                    )
            except Exception as e:
                _LOGGER.warning("Failed to fetch release notes for %s: %s", domain, e)

        html = render_template(
            template_dir,
            "integration_detail.html",
            request=request,
            integration=info_dict,
            entries=entries_dicts,
            entities=entities_dicts,
            devices=devices,
            releases=releases,
        )
        return HTMLResponse(content=html)

    # ------------------------------------------------------------------ #
    #  Enable / Disable integration
    # ------------------------------------------------------------------ #

    @app.post("/integrations/{domain}/enable")
    async def enable_integration(request: Request, domain: str):
        """Enable an integration."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        success = (
            await shim_manager.get_integration_manager().enable_integration(
                domain
            )
        )
        if success:
            # Load the integration
            entries = shim_manager.get_hass().config_entries.async_entries(
                domain
            )
            for entry in entries:
                await shim_manager.get_integration_loader().setup_integration(
                    entry
                )
            # Use HTMX redirect to integration detail page
            html = (
                f'<div class="alert alert-success">'
                f"Integration {domain} enabled successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = get_detail_redirect(
                request, domain
            )
            return response
        return HTMLResponse(
            f'<div class="alert alert-error">Failed to enable {domain}</div>',
            status_code=400,
        )

    @app.post("/integrations/{domain}/disable")
    async def disable_integration(request: Request, domain: str):
        """Disable an integration."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        # Unload first
        entries = shim_manager.get_hass().config_entries.async_entries(domain)
        for entry in entries:
            await shim_manager.get_integration_loader().unload_integration(
                entry
            )

        success = (
            await shim_manager.get_integration_manager().disable_integration(
                domain
            )
        )
        if success:
            html = (
                f'<div class="alert alert-success">'
                f"Integration {domain} disabled successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = get_detail_redirect(
                request, domain
            )
            return response
        return HTMLResponse(
            f'<div class="alert alert-error">Failed to disable {domain}</div>',
            status_code=400,
        )

    # ------------------------------------------------------------------ #
    #  Install integration
    # ------------------------------------------------------------------ #

    @app.post("/integrations/{full_name:path}/install")
    async def install_integration(
        request: Request, full_name: str, version: Optional[str] = Form(None)
    ):
        """Install an integration (async - returns immediately)."""
        from ...integrations import InstallTask

        # Check if repository is unsupported before queuing
        unsupported_entry = (
            shim_manager.get_integration_manager().is_unsupported_repo(
                full_name
            )
        )
        if unsupported_entry:
            reason = unsupported_entry.get("reason", "No reason provided")
            return HTMLResponse(
                f'<span class="pico-color-orange-500" style="font-weight: 600;" '
                f'title="{reason}">\u2717 Unsupported</span>',
                status_code=400,
            )

        # Determine if this is a custom repo or HACS default repo
        available = shim_manager.get_integration_manager().get_available_integrations()
        repo_source = "hacs_default"
        for repo in available:
            if repo.get("full_name") == full_name:
                repo_source = repo.get("source", "hacs_default")
                break

        # Queue the install and get the task
        result = await shim_manager.install_integration(
            full_name, version=version, source=repo_source
        )

        # Check if it's a task (async) or boolean (sync/legacy)
        if isinstance(result, InstallTask):
            return HTMLResponse(
                f'<span class="pico-color-jade-500" style="font-weight: 600;" '
                f'hx-get="api/install-status/{full_name}" '
                f'hx-trigger="every 2s" hx-swap="outerHTML">Installing...</span>'
            )
        elif result:
            return HTMLResponse(
                '<span class="pico-color-jade-500" style="font-weight: 600;">\u2713 Installed</span>'
            )
        else:
            return HTMLResponse(
                f'<div class="alert alert-error">Failed to install {full_name}</div>',
                status_code=400,
            )

    @app.get("/api/install-status/{full_name:path}")
    async def get_install_status(request: Request, full_name: str):
        """Get the installation status for a full_name (returns HTML for HTMX)."""
        task = shim_manager.get_integration_manager().get_install_status(
            full_name
        )

        if not task:
            # Check if already installed (by full_name as domain)
            info = shim_manager.get_integration_manager().get_integration(
                full_name
            )
            if info:
                return HTMLResponse(
                    '<span class="pico-color-jade-500" style="font-weight: 600;">\u2713 Installed</span>'
                )
            return HTMLResponse('<span style="color: #999;">Not found</span>')

        polling_attrs = f'hx-get="api/install-status/{full_name}" hx-trigger="every 2s" hx-swap="outerHTML"'

        if task.status == "pending":
            return HTMLResponse(
                f'<span {polling_attrs} class="pico-color-jade-500" style="font-weight: 600;"><span class="spinner"></span> Pending...</span>'
            )
        elif task.status == "downloading":
            return HTMLResponse(
                f'<span {polling_attrs} class="pico-color-jade-500" style="font-weight: 600;"><span class="spinner"></span> Downloading...</span>'
            )
        elif task.status == "installing":
            return HTMLResponse(
                f'<span {polling_attrs} class="pico-color-jade-500" style="font-weight: 600;"><span class="spinner"></span> Installing...</span>'
            )
        elif task.status == "complete":
            response = HTMLResponse(
                '<span class="pico-color-jade-500" style="font-weight: 600;">\u2713 Installed</span>'
            )
            response.headers["HX-Trigger"] = '{"reloadPage": {}}'
            return response
        else:  # error
            return HTMLResponse(
                f'<span style="color: #f44336;">\u2717 Error: {task.error_message or "Unknown error"}</span>'
            )

    # ------------------------------------------------------------------ #
    #  Remove integration
    # ------------------------------------------------------------------ #

    @app.post("/integrations/{domain}/remove")
    async def remove_integration(request: Request, domain: str):
        """Remove an integration."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        # First remove all config entries (this unloads entities and cleans up MQTT)
        entries = shim_manager.get_hass().config_entries.async_entries(domain)
        for entry in entries:
            await shim_manager.get_integration_loader().remove_config_entry(
                entry
            )

        # Then remove the integration files
        success = (
            await shim_manager.get_integration_manager().remove_integration(
                domain
            )
        )
        if success:
            html = (
                f'<div class="alert alert-success">'
                f"Integration {domain} removed successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = ".."
            return response
        return HTMLResponse(
            f'<div class="alert alert-error">Failed to remove {domain}</div>',
            status_code=400,
        )

    # ------------------------------------------------------------------ #
    #  Update integration
    # ------------------------------------------------------------------ #

    @app.post("/integrations/{domain}/update")
    async def update_integration(request: Request, domain: str):
        """Update an integration."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        info = shim_manager.get_integration_manager().get_integration(domain)
        if not info or not info.update_available:
            return HTMLResponse(
                f'<div class="alert alert-error">No update available for {domain}</div>',
                status_code=400,
            )

        await shim_manager._update_integration(domain)
        html = (
            f'<div class="alert alert-success">'
            f"Integration {domain} updated successfully!"
            f"</div>"
        )
        response = HTMLResponse(content=html)
        response.headers["HX-Redirect"] = get_detail_redirect(request, domain)
        return response

    # ------------------------------------------------------------------ #
    #  Config entry enable / disable / remove
    # ------------------------------------------------------------------ #

    @app.post("/config/{entry_id}/remove")
    async def remove_config_entry(request: Request, entry_id: str):
        """Remove a config entry."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        success = (
            await shim_manager.get_integration_loader().remove_config_entry(
                entry
            )
        )

        if success:
            html = (
                f'<div class="alert alert-success">Configuration entry removed '
                f"successfully!</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = get_detail_redirect(
                request, domain
            )
            return response
        else:
            return HTMLResponse(
                '<div class="alert alert-error">Failed to remove configuration entry</div>',
                status_code=400,
            )

    @app.post("/config/{entry_id}/disable")
    async def disable_config_entry(request: Request, entry_id: str):
        """Disable a config entry (unload it)."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        await shim_manager.get_integration_loader().unload_integration(entry)
        entry.state = "not_loaded"

        html = (
            f'<div class="alert alert-success">'
            f"Configuration entry disabled successfully!"
            f"</div>"
        )
        response = HTMLResponse(content=html)
        response.headers["HX-Redirect"] = get_detail_redirect(request, domain)
        return response

    @app.post("/config/{entry_id}/enable")
    async def enable_config_entry(request: Request, entry_id: str):
        """Enable a config entry (reload it)."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        # Auto-enable the integration if it's disabled
        info = shim_manager.get_integration_manager().get_integration(domain)
        if info and not info.enabled:
            await shim_manager.get_integration_manager().enable_integration(
                domain
            )

        await shim_manager.get_integration_loader().setup_integration(entry)

        html = (
            f'<div class="alert alert-success">'
            f"Configuration entry enabled successfully!"
            f"</div>"
        )
        response = HTMLResponse(content=html)
        response.headers["HX-Redirect"] = get_detail_redirect(request, domain)
        return response

    # ------------------------------------------------------------------ #
    #  Entity filters
    # ------------------------------------------------------------------ #

    @app.post("/config/{entry_id}/filters")
    async def update_entity_filters(request: Request, entry_id: str):
        """Update entity filters for a config entry."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        form_data = await request.form()

        # Parse entity ID patterns
        entity_filters_text = form_data.get("entity_filters", "")
        entity_patterns = []
        for line in entity_filters_text.split("\n"):
            pattern = line.strip()
            if pattern:
                entity_patterns.append(pattern)

        # Parse name patterns
        name_filters_text = form_data.get("entity_name_filters", "")
        name_patterns = []
        for line in name_filters_text.split("\n"):
            pattern = line.strip()
            if pattern:
                name_patterns.append(pattern)

        loader = shim_manager.get_integration_loader()

        is_valid, error_msg = loader.validate_entity_filters(entity_patterns)
        if not is_valid:
            return HTMLResponse(
                f'<div class="alert alert-error">Invalid entity ID filter: {error_msg}</div>',
                status_code=400,
            )

        is_valid, error_msg = loader.validate_entity_filters(name_patterns)
        if not is_valid:
            return HTMLResponse(
                f'<div class="alert alert-error">Invalid name filter: {error_msg}</div>',
                status_code=400,
            )

        new_options = dict(entry.options)
        new_options["entity_filters"] = entity_patterns
        new_options["entity_name_filters"] = name_patterns
        shim_manager.get_hass().config_entries.async_update_entry(
            entry, options=new_options
        )

        result = await loader.async_apply_entity_filters(entry)

        total_patterns = len(entity_patterns) + len(name_patterns)
        _LOGGER.info(
            "Updated entity filters for %s entry %s: "
            "%d patterns (%d ID, %d name), removed %d entities",
            domain,
            entry_id,
            total_patterns,
            len(entity_patterns),
            len(name_patterns),
            result["removed"],
        )

        html = (
            f'<div class="alert alert-success">'
            f"Entity filters updated successfully! "
            f"Removed {result['removed']} filtered entities."
            f"</div>"
        )
        response = HTMLResponse(content=html)
        response.headers["HX-Redirect"] = get_detail_redirect(request, domain)
        return response

    # ------------------------------------------------------------------ #
    #  Reload config entry
    # ------------------------------------------------------------------ #

    @app.post("/config/{entry_id}/reload")
    async def reload_config_entry(request: Request, entry_id: str):
        """Reload a config entry to apply configuration changes."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        success = (
            await shim_manager.get_integration_loader().reload_config_entry(
                entry
            )
        )

        if success:
            html = (
                f'<div class="alert alert-success">'
                f"Configuration reloaded successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = get_detail_redirect(
                request, domain
            )
            return response
        else:
            return HTMLResponse(
                '<div class="alert alert-error">Failed to reload configuration</div>',
                status_code=400,
            )
