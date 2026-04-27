"""Web UI for Home Assistant Shim.

FastAPI + HTMX interface for managing integrations and config flows.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

# from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

import colorlog
import markdown

from ..logging import get_logger
from ..models import ConfigEntry

_LOGGER = get_logger(__name__)

# CDN URLs for CSS and JS libraries
PICO_CSS_URL = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
PICO_COLORS_URL = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.colors.min.css"

# https://htmx.org/docs/#installing
HTMX_URL = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js"
HTMX_SRI = "sha384-/TgkGk7p307TH7EXJDuUlgG3Ce1UVolAOFopFekQkkXihi5u/6OCvVKyz1W+idaz"


class WebUI:
    """Web UI for the HA shim."""

    def __init__(self, shim_manager, host: str = "0.0.0.0", port: int = 8080):
        self._shim_manager = shim_manager
        self._host = host
        self._port = port
        self._integration_manager = shim_manager._integration_manager

        # Import version locally to avoid circular import
        from .. import __version__

        # Setup FastAPI
        self._app = FastAPI(title="HA Shim", version=__version__)

        # Setup templates - store directory path and load manually
        self._template_dir = Path(__file__).parent / "templates"

        # Register routes
        self._register_routes()

        # Setup static files
        # static_dir = Path(__file__).parent / "static"
        # self._app.mount(
        #     "/static", StaticFiles(directory=str(static_dir)), name="static"
        # )

    def _check_loading(self) -> Optional[HTMLResponse]:
        """Check if integrations are still loading and return a 'please wait' response if so.

        Returns:
            HTMLResponse with 'please wait' message if loading, None otherwise.
        """
        if self._shim_manager.is_loading:
            return HTMLResponse(
                '<div class="alert alert-warning">'
                '<span class="spinner" style="width: 14px; height: 14px; margin-right: 8px;"></span>'
                "Integrations are still loading. Please wait a moment and try again."
                "</div>",
                status_code=503,
            )
        return None

    def _render_template(self, template_name: str, **context) -> str:
        """Render a template using Jinja2 environment with inheritance support."""
        # Create environment with file system loader for template inheritance
        env = Environment(loader=FileSystemLoader(str(self._template_dir)))

        context.setdefault("PICO_CSS_URL", PICO_CSS_URL)
        context.setdefault("PICO_COLORS_URL", PICO_COLORS_URL)
        context.setdefault("HTMX_URL", HTMX_URL)
        context.setdefault("HTMX_SRI", HTMX_SRI)

        template = env.get_template(template_name)
        return template.render(**context)

    def _get_detail_redirect(self, request: Request, domain: str) -> str:
        """Get the correct redirect path to the integration detail page.

        HTMX resolves HX-Redirect relative to the page URL where the request
        originated, not the request URL. We need to detect if the request came
        from the index page or the detail page and return the appropriate path.

        - From index page (e.g., / or /api/hassio_ingress/xxx/): use ./integrations/{domain}
        - From detail page (e.g., /integrations/{domain}): use ./{domain}
        - From config flow page (e.g., /config/{domain}): use ../integrations/{domain}
        """
        # Try multiple sources to determine the current page:
        # 1. HX-Current-URL: HTMX sends the full browser URL
        # 2. X-Ingress-Path + request URL: HA ingress path prefix + request path
        # 3. Referer: Fallback for non-HTMX requests
        current_url = request.headers.get("hx-current-url", "")

        # Build full URL from ingress path if available
        ingress_path = request.headers.get("x-ingress-path", "")
        request_path = request.url.path
        full_url = current_url
        if not full_url and ingress_path:
            # Reconstruct full URL from ingress path + request path
            full_url = f"{ingress_path}{request_path}"

        # Fallback to Referer if neither HX-Current-URL nor X-Ingress-Path available
        source = full_url or request.headers.get("referer", "")

        # Normalize URL: strip query string and fragment for cleaner detection
        normalized = source.split("?")[0].split("#")[0]

        # Check if we're on the detail page already
        # The request path would be /integrations/{domain}/enable (or /disable, etc.)
        # The detail page would be /integrations/{domain}
        if f"/integrations/{domain}/" in normalized or normalized.rstrip("/").endswith(
            f"/integrations/{domain}"
        ):
            # Already on detail page, use just the domain part
            return f"./{domain}"

        # Check if we're on a config flow page (/config/{domain} or /config/{entry_id}/reconfigure)
        if "/config/" in normalized:
            # From config flow page, go up one level then to integrations
            return f"../integrations/{domain}"

        # Coming from index page, navigate to detail page
        return f"./integrations/{domain}"

    def _register_routes(self) -> None:
        """Register all routes."""

        @self._app.get("/", response_class=HTMLResponse)
        async def index(request: Request):
            """Main page with integration list."""
            integrations = (
                self._shim_manager.get_integration_manager().get_all_integrations()
            )
            available = self._shim_manager.get_integration_manager().get_available_integrations()
            custom_repos = (
                self._shim_manager.get_integration_manager().get_custom_repositories()
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

            html = self._render_template(
                "index.html",
                request=request,
                integrations=integrations_dicts,
                available=available_dicts,
                custom_repos=custom_repos,
                is_loading=self._shim_manager.is_loading,
            )
            return HTMLResponse(content=html)

        @self._app.get("/integrations/{domain}", response_class=HTMLResponse)
        async def integration_detail(request: Request, domain: str):
            """Integration detail page."""
            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if not info:
                raise HTTPException(status_code=404, detail="Integration not found")

            entries = self._shim_manager.get_hass().config_entries.async_entries(domain)
            entities = self._shim_manager.get_integration_loader().get_entities(
                integration_domain=domain
            )

            # Get device registry and fetch devices for this integration's entries
            hass = self._shim_manager.get_hass()
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
                        await self._shim_manager.get_integration_manager().get_release_notes(
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
                    _LOGGER.warning(f"Failed to fetch release notes for {domain}: {e}")

            html = self._render_template(
                "integration_detail.html",
                request=request,
                integration=info_dict,
                entries=entries_dicts,
                entities=entities_dicts,
                devices=devices,
                releases=releases,
            )
            return HTMLResponse(content=html)

        @self._app.post("/integrations/{domain}/enable")
        async def enable_integration(request: Request, domain: str):
            """Enable an integration."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            success = (
                await self._shim_manager.get_integration_manager().enable_integration(
                    domain
                )
            )
            if success:
                # Load the integration
                entries = self._shim_manager.get_hass().config_entries.async_entries(
                    domain
                )
                for entry in entries:
                    await self._shim_manager.get_integration_loader().setup_integration(
                        entry
                    )
                # Use HTMX redirect to integration detail page
                html = (
                    f'<div class="alert alert-success">'
                    f"Integration {domain} enabled successfully!"
                    f"</div>"
                )
                response = HTMLResponse(content=html)
                response.headers["HX-Redirect"] = self._get_detail_redirect(
                    request, domain
                )
                return response
            return HTMLResponse(
                f'<div class="alert alert-error">Failed to enable {domain}</div>',
                status_code=400,
            )

        @self._app.post("/integrations/{domain}/disable")
        async def disable_integration(request: Request, domain: str):
            """Disable an integration."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Unload first
            entries = self._shim_manager.get_hass().config_entries.async_entries(domain)
            for entry in entries:
                await self._shim_manager.get_integration_loader().unload_integration(
                    entry
                )

            success = (
                await self._shim_manager.get_integration_manager().disable_integration(
                    domain
                )
            )
            if success:
                # Use HTMX redirect to integration detail page
                html = (
                    f'<div class="alert alert-success">'
                    f"Integration {domain} disabled successfully!"
                    f"</div>"
                )
                response = HTMLResponse(content=html)
                response.headers["HX-Redirect"] = self._get_detail_redirect(
                    request, domain
                )
                return response
            return HTMLResponse(
                f'<div class="alert alert-error">Failed to disable {domain}</div>',
                status_code=400,
            )

        @self._app.post("/integrations/{full_name:path}/install")
        async def install_integration(
            request: Request, full_name: str, version: Optional[str] = Form(None)
        ):
            """Install an integration (async - returns immediately)."""
            from ..integrations import InstallTask

            # Check if repository is unsupported before queuing
            unsupported_entry = (
                self._shim_manager.get_integration_manager().is_unsupported_repo(
                    full_name
                )
            )
            if unsupported_entry:
                reason = unsupported_entry.get("reason", "No reason provided")
                return HTMLResponse(
                    f'<span class="pico-color-orange-500" style="font-weight: 600;" '
                    f'title="{reason}">✗ Unsupported</span>',
                    status_code=400,
                )

            # Determine if this is a custom repo or HACS default repo
            # by looking it up in the available integrations list
            available = self._shim_manager.get_integration_manager().get_available_integrations()
            repo_source = "hacs_default"  # default
            for repo in available:
                if repo.get("full_name") == full_name:
                    repo_source = repo.get("source", "hacs_default")
                    break

            # Queue the install and get the task
            result = await self._shim_manager.install_integration(
                full_name, version=version, source=repo_source
            )

            # Check if it's a task (async) or boolean (sync/legacy)
            if isinstance(result, InstallTask):
                # Async install - return "Installing..." text that polls for completion
                return HTMLResponse(
                    f'<span class="pico-color-jade-500" style="font-weight: 600;" '
                    f'hx-get="api/install-status/{full_name}" '
                    f'hx-trigger="every 2s" hx-swap="outerHTML">Installing...</span>'
                )
            elif result:
                # Legacy blocking install succeeded
                return HTMLResponse(
                    '<span class="pico-color-jade-500" style="font-weight: 600;">✓ Installed</span>'
                )
            else:
                return HTMLResponse(
                    f'<div class="alert alert-error">Failed to install {full_name}</div>',
                    status_code=400,
                )

        @self._app.get("/api/install-status/{full_name:path}")
        async def get_install_status(request: Request, full_name: str):
            """Get the installation status for a full_name (returns HTML for HTMX)."""
            task = self._shim_manager.get_integration_manager().get_install_status(
                full_name
            )

            if not task:
                # Check if already installed (by full_name as domain)
                info = self._shim_manager.get_integration_manager().get_integration(
                    full_name
                )
                if info:
                    return HTMLResponse(
                        '<span class="pico-color-jade-500" style="font-weight: 600;">✓ Installed</span>'
                    )
                return HTMLResponse(f'<span style="color: #999;">Not found</span>')

            # Include polling attributes for incomplete statuses so HTMX continues polling
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
                    '<span class="pico-color-jade-500" style="font-weight: 600;">✓ Installed</span>'
                )
                # Trigger page reload to show in Installed tab
                response.headers["HX-Trigger"] = '{"reloadPage": {}}'
                return response
            else:  # error
                return HTMLResponse(
                    f'<span style="color: #f44336;">✗ Error: {task.error_message or "Unknown error"}</span>'
                )

        @self._app.post("/integrations/{domain}/remove")
        async def remove_integration(request: Request, domain: str):
            """Remove an integration."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # First remove all config entries (this unloads entities and cleans up MQTT)
            entries = self._shim_manager.get_hass().config_entries.async_entries(domain)
            for entry in entries:
                await self._shim_manager.get_integration_loader().remove_config_entry(
                    entry
                )

            # Then remove the integration files
            success = (
                await self._shim_manager.get_integration_manager().remove_integration(
                    domain
                )
            )
            if success:
                # Use HTMX redirect to properly change the URL
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

        @self._app.post("/integrations/{domain}/update")
        async def update_integration(request: Request, domain: str):
            """Update an integration."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if not info or not info.update_available:
                return HTMLResponse(
                    f'<div class="alert alert-error">No update available for {domain}</div>',
                    status_code=400,
                )

            # This triggers the update process
            await self._shim_manager._update_integration(domain)
            # Use HTMX redirect to refresh the integration detail page
            html = (
                f'<div class="alert alert-success">'
                f"Integration {domain} updated successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = self._get_detail_redirect(request, domain)
            return response

        @self._app.post("/config/{entry_id}/remove")
        async def remove_config_entry(request: Request, entry_id: str):
            """Remove a config entry."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Find the entry
            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Remove the entry (this also unloads entities and cleans up MQTT)
            success = (
                await self._shim_manager.get_integration_loader().remove_config_entry(
                    entry
                )
            )

            if success:
                # Use HTMX redirect to refresh the integration detail page
                html = (
                    f'<div class="alert alert-success">Configuration entry removed '
                    f"successfully!</div>"
                )
                response = HTMLResponse(content=html)
                response.headers["HX-Redirect"] = self._get_detail_redirect(
                    request, domain
                )
                return response
            else:
                return HTMLResponse(
                    '<div class="alert alert-error">Failed to remove configuration entry</div>',
                    status_code=400,
                )

        @self._app.post("/config/{entry_id}/disable")
        async def disable_config_entry(request: Request, entry_id: str):
            """Disable a config entry (unload it)."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Unload the entry
            await self._shim_manager.get_integration_loader().unload_integration(entry)
            entry.state = "not_loaded"

            html = (
                f'<div class="alert alert-success">'
                f"Configuration entry disabled successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = self._get_detail_redirect(request, domain)
            return response

        @self._app.post("/config/{entry_id}/enable")
        async def enable_config_entry(request: Request, entry_id: str):
            """Enable a config entry (reload it)."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Auto-enable the integration if it's disabled
            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if info and not info.enabled:
                await self._shim_manager.get_integration_manager().enable_integration(
                    domain
                )

            # Setup the entry
            await self._shim_manager.get_integration_loader().setup_integration(entry)

            html = (
                f'<div class="alert alert-success">'
                f"Configuration entry enabled successfully!"
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = self._get_detail_redirect(request, domain)
            return response

        @self._app.get("/config/{domain}", response_class=HTMLResponse)
        async def config_flow_start(request: Request, domain: str):
            """Start a config flow for an integration."""
            # First ensure integration is loaded
            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if not info:
                raise HTTPException(status_code=404, detail="Integration not found")

            # Compute and store the OAuth redirect URI from the request
            hass = self._shim_manager.get_hass()
            ingress_path = request.headers.get("x-ingress-path", "")
            if ingress_path:
                # Running via HA ingress
                redirect_uri = f"{ingress_path.rstrip('/')}/auth/external/callback"
            else:
                # Direct access - use request base URL
                base_url = str(request.base_url).rstrip("/")
                redirect_uri = f"{base_url}/auth/external/callback"
            hass.data["_oauth2_redirect_uri"] = redirect_uri
            _LOGGER.debug("OAuth2 redirect URI set to: %s", redirect_uri)

            # Start config flow
            result = (
                await self._shim_manager.get_integration_loader().start_config_flow(
                    domain
                )
            )

            if not result:
                raise HTTPException(
                    status_code=400, detail="Failed to start config flow"
                )

            # Handle abort immediately (e.g. missing credentials)
            if result.get("type") == "abort":
                reason = result.get("reason", "unknown")
                if reason == "missing_credentials":
                    return HTMLResponse(
                        content=self._render_template(
                            "config_form.html",
                            request=request,
                            domain=domain,
                            fields=[],
                            errors={"base": "Missing OAuth credentials. "
                                    "Please add application credentials first."},
                            description={
                                "add_creds_url": f"../credentials/{domain}",
                                "info": "This integration requires OAuth2 application credentials. "
                                         f"<a href='../credentials/{domain}'>Add credentials here</a>.",
                            },
                            step_id="user",
                            flow_id=result.get("flow_id"),
                            is_options_flow=False,
                        )
                    )
                return HTMLResponse(
                    content=f'<div class="alert alert-error">Aborted: {reason}</div>'
                )

            return self._render_config_step(request, domain, result)

        @self._app.post("/config/{domain}")
        async def config_flow_submit(request: Request, domain: str):
            """Submit a config flow step."""
            # Parse form data
            form_data = await request.form()
            user_input = dict(form_data)

            # Extract flow_id from form data
            flow_id = user_input.pop("flow_id", None)

            # Get the stored schema from the flow object for type conversion
            hass = self._shim_manager.get_hass()
            flow = hass.config_entries._flow_progress.get(flow_id)
            schema = getattr(flow, "_last_form_schema", None) if flow else None

            # Handle menu selection - if next_step is present, it's a menu selection
            if "next_step" in user_input:
                # This is a menu selection, pass it as the user_input
                user_input = {"next_step": user_input["next_step"]}
                _LOGGER.debug(
                    f"Config flow menu selection for {domain}: flow_id={flow_id}, "
                    f"selected_option={user_input['next_step']}"
                )
            else:
                _LOGGER.debug(
                    f"Config flow submit for {domain}: flow_id={flow_id}, "
                    f"schema={schema}, user_input={user_input}"
                )

            # Convert form values based on schema field types
            if schema and hasattr(schema, "schema"):
                import voluptuous as vol

                # Log all incoming form values for debugging
                for key, value in user_input.items():
                    _LOGGER.debug(
                        f"Form input '{key}': {repr(value)} (type: {type(value).__name__}, "
                        f"is_undefined: {self._is_undefined(value)})"
                    )

                schema_dict = schema.schema
                _LOGGER.debug(f"Schema dict: {schema_dict}")
                if isinstance(schema_dict, dict):
                    converted_input = {}
                    for key, value in user_input.items():
                        # Find the validator and schema key for this field
                        validator = None
                        field_schema_key = None
                        for schema_key, schema_val in schema_dict.items():
                            field_name = (
                                schema_key.schema
                                if hasattr(schema_key, "schema")
                                else schema_key
                            )
                            if field_name == key:
                                validator = schema_val
                                field_schema_key = schema_key
                                break

                        if validator:
                            # Check if the raw value is UNDEFINED (skip it)
                            if self._is_undefined(value):
                                _LOGGER.debug(
                                    f"Field '{key}': value is UNDEFINED, skipping"
                                )
                                continue

                            # Convert based on validator type
                            converted_value = self._convert_form_value(
                                value, validator, key
                            )

                            # Check if converted value is UNDEFINED
                            if self._is_undefined(converted_value):
                                _LOGGER.debug(
                                    f"Field '{key}': converted value is UNDEFINED, skipping"
                                )
                                continue

                            # Handle empty strings on Optional fields
                            is_optional = (
                                field_schema_key
                                and hasattr(field_schema_key, "__class__")
                                and field_schema_key.__class__.__name__ == "Optional"
                            )
                            has_explicit_default = (
                                field_schema_key
                                and hasattr(field_schema_key, "default")
                                and field_schema_key.default is not vol.UNDEFINED
                            )

                            if converted_value == "" and is_optional:
                                if has_explicit_default:
                                    # Use the explicit default value
                                    default_value = field_schema_key.default
                                    if callable(default_value):
                                        try:
                                            default_value = default_value()
                                        except Exception:
                                            default_value = None
                                    _LOGGER.debug(
                                        f"Field '{key}': empty string on Optional field "
                                        f"with default, using default={repr(default_value)}"
                                    )
                                    converted_input[key] = default_value
                                else:
                                    # No explicit default - skip this key entirely
                                    # This makes "if KEY in data" checks in integrations return False
                                    _LOGGER.debug(
                                        f"Field '{key}': empty string on Optional field "
                                        f"without default, skipping key"
                                    )
                                    # Don't add to converted_input - skip it
                            else:
                                _LOGGER.debug(
                                    f"Field '{key}': original='{value}' ({type(value).__name__}), "
                                    f"validator={validator.__class__.__name__}, "
                                    f"converted='{converted_value}' ({type(converted_value).__name__})"
                                )
                                converted_input[key] = converted_value
                        else:
                            _LOGGER.debug(
                                f"Field '{key}': no validator found, keeping as-is"
                            )
                            converted_input[key] = value

                    # Add missing schema fields with their default values
                    # This handles unchecked checkboxes which browsers don't submit
                    for schema_key, schema_val in schema_dict.items():
                        field_name = (
                            schema_key.schema
                            if hasattr(schema_key, "schema")
                            else schema_key
                        )
                        if field_name not in converted_input:
                            # Check if this is an Optional field with a default
                            if hasattr(schema_key, "default"):
                                default_value = schema_key.default
                                # Voluptuous default may be a callable (lambda) that returns the actual value
                                if callable(default_value):
                                    try:
                                        default_value = default_value()
                                    except Exception:
                                        # If calling fails, skip this field
                                        continue
                                _LOGGER.debug(
                                    f"Field '{field_name}': missing from form, using default={repr(default_value)}"
                                )
                                converted_input[field_name] = default_value

                    # Final pass: remove any UNDEFINED values that might have slipped through
                    for key in list(converted_input.keys()):
                        if self._is_undefined(converted_input[key]):
                            _LOGGER.debug(
                                f"Final cleanup: removing UNDEFINED value for '{key}'"
                            )
                            del converted_input[key]

                    user_input = converted_input
                    _LOGGER.debug(f"Final user_input: {user_input}")

            # Continue config flow
            result = (
                await self._shim_manager.get_integration_loader().continue_config_flow(
                    domain, flow_id, user_input
                )
            )

            if not result:
                raise HTTPException(
                    status_code=400, detail="Failed to process config flow"
                )

            # Handle result
            if result.get("type") == "create_entry":
                # Create the config entry
                entry_data = result.get("data", {})
                entry_options = result.get("options")
                entry = await self._shim_manager.create_config_entry(
                    domain, entry_data, entry_options
                )

                if entry:
                    # Note: We don't set up the integration here because
                    # integrations start disabled by default. The user needs
                    # to manually enable the integration via the web UI first.
                    # This prevents MQTT discovery topics from being published
                    # for disabled integrations.

                    # Return success with HTMX redirect to integration page
                    response = HTMLResponse(
                        f'<div class="alert alert-success">Configuration successful! Please enable the integration to use it.</div>'
                    )
                    response.headers["HX-Redirect"] = self._get_detail_redirect(
                        request, domain
                    )
                    return response
                else:
                    return HTMLResponse(
                        '<div class="alert alert-error">Failed to create entry</div>'
                    )

            elif result.get("type") == "form":
                # Show next form step
                return self._render_config_step(request, domain, result)

            elif result.get("type") == "menu":
                # Show menu selection step
                return self._render_menu_step(request, domain, result)

            elif result.get("type") == "external":
                # OAuth2 authorization step - show link to authorize
                return self._render_external_step(request, domain, result)

            elif result.get("type") == "external_done":
                # OAuth flow completed externally, continue to next step
                next_step_id = result.get("next_step_id", "creation")
                result = (
                    await self._shim_manager.get_integration_loader().continue_config_flow(
                        domain, flow_id, {"next_step": next_step_id}
                    )
                )
                if not result:
                    return HTMLResponse(
                        '<div class="alert alert-error">Failed to continue OAuth flow</div>'
                    )
                # Handle the result of the next step
                if result.get("type") == "create_entry":
                    entry_data = result.get("data", {})
                    entry_options = result.get("options")
                    entry = await self._shim_manager.create_config_entry(
                        domain, entry_data, entry_options
                    )
                    if entry:
                        response = HTMLResponse(
                            '<div class="alert alert-success">Configuration successful! Please enable the integration to use it.</div>'
                        )
                        response.headers["HX-Redirect"] = self._get_detail_redirect(
                            request, domain
                        )
                        return response
                    else:
                        return HTMLResponse(
                            '<div class="alert alert-error">Failed to create entry</div>'
                        )
                elif result.get("type") == "form":
                    return self._render_config_step(request, domain, result)
                elif result.get("type") == "abort":
                    return HTMLResponse(
                        f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
                    )
                else:
                    return self._render_config_step(request, domain, result)

            elif result.get("type") == "abort":
                return HTMLResponse(
                    f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
                )

            else:
                return HTMLResponse(
                    f'<div class="alert alert-error">Unknown result: {result.get("type")}</div>'
                )

        @self._app.get("/auth/external/callback")
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

            hass = self._shim_manager.get_hass()
            from ..stubs.oauth2 import _decode_jwt

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

        @self._app.get("/credentials", response_class=HTMLResponse)
        async def credentials_list(request: Request):
            """List all integrations with application credentials."""
            hass = self._shim_manager.get_hass()
            from ..stubs.application_credentials import DATA_COMPONENT

            storage = hass.data.get(DATA_COMPONENT)
            domains = []

            # Find all domains that have credentials
            if storage:
                seen = set()
                for item in storage.async_items():
                    domain = item.get("domain")
                    if domain and domain not in seen:
                        seen.add(domain)
                        # Try to get integration info for display name
                        info = self._shim_manager.get_integration_manager().get_integration(domain)
                        domains.append({
                            "domain": domain,
                            "name": info.name if info else domain,
                        })

            html = self._render_template(
                "credentials.html",
                request=request,
                domains=domains,
                current_domain=None,
            )
            return HTMLResponse(content=html)

        @self._app.get("/credentials/{domain}", response_class=HTMLResponse)
        async def credentials_domain(request: Request, domain: str):
            """Show credentials for a specific integration."""
            hass = self._shim_manager.get_hass()
            from ..stubs.application_credentials import DATA_COMPONENT

            storage = hass.data.get(DATA_COMPONENT)
            credentials = []
            all_domains = []

            if storage:
                seen = set()
                for item in storage.async_items():
                    item_domain = item.get("domain")
                    if item_domain and item_domain not in seen:
                        seen.add(item_domain)
                        info = self._shim_manager.get_integration_manager().get_integration(item_domain)
                        all_domains.append({
                            "domain": item_domain,
                            "name": info.name if info else item_domain,
                        })
                    if item_domain == domain:
                        credentials.append({
                            "id": item.get("id"),
                            "client_id": item.get("client_id"),
                            "name": item.get("name"),
                        })

            info = self._shim_manager.get_integration_manager().get_integration(domain)
            html = self._render_template(
                "credentials.html",
                request=request,
                domains=all_domains,
                current_domain=domain,
                current_name=info.name if info else domain,
                credentials=credentials,
            )
            return HTMLResponse(content=html)

        @self._app.post("/credentials/{domain}")
        async def credentials_create(request: Request, domain: str):
            """Create a new application credential."""
            form_data = await request.form()
            hass = self._shim_manager.get_hass()
            from ..stubs.application_credentials import (
                DATA_COMPONENT,
                ClientCredential,
            )

            storage = hass.data.get(DATA_COMPONENT)
            if storage is None:
                raise HTTPException(status_code=500, detail="Application credentials not initialized")

            storage.async_create_item({
                "domain": domain,
                "client_id": form_data.get("client_id", "").strip(),
                "client_secret": form_data.get("client_secret", "").strip(),
                "name": form_data.get("name", "").strip() or None,
            })

            # Redirect back to the domain page
            response = HTMLResponse(
                '<div class="alert alert-success">Credential saved successfully.</div>'
            )
            response.headers["HX-Redirect"] = f"./{domain}"
            return response

        @self._app.delete("/credentials/{domain}/{item_id}")
        async def credentials_delete(request: Request, domain: str, item_id: str):
            """Delete an application credential."""
            hass = self._shim_manager.get_hass()
            from ..stubs.application_credentials import DATA_COMPONENT

            storage = hass.data.get(DATA_COMPONENT)
            if storage is None:
                raise HTTPException(status_code=500, detail="Application credentials not initialized")

            if storage.async_delete_item(item_id):
                return HTMLResponse(
                    '<tr><td colspan="3" class="alert alert-success">Credential deleted.</td></tr>'
                )
            else:
                raise HTTPException(status_code=404, detail="Credential not found")

        @self._app.get("/config/{entry_id}/reconfigure", response_class=HTMLResponse)
        async def options_flow_start(request: Request, entry_id: str):
            """Start an options flow (reconfiguration) for an existing config entry."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Find the entry
            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Start options flow
            result = (
                await self._shim_manager.get_integration_loader().start_options_flow(
                    entry
                )
            )

            if not result:
                # Fallback message if no options flow available
                return HTMLResponse(
                    '<div class="alert alert-warning">'
                    "This integration does not support reconfiguration."
                    "</div>",
                    status_code=400,
                )

            # Handle different result types (form vs menu)
            if result.get("type") == "menu":
                return self._render_menu_step(
                    request, domain, result, is_options_flow=True, entry_id=entry_id
                )
            else:
                # Default to form rendering for "form" type and any other types
                return self._render_config_step(
                    request, domain, result, is_options_flow=True, entry_id=entry_id
                )

        @self._app.post("/config/{entry_id}/reconfigure")
        async def options_flow_submit(request: Request, entry_id: str):
            """Submit an options flow step."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Find the entry
            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Parse form data
            form_data = await request.form()
            user_input = dict(form_data)
            _LOGGER.debug(f"Options flow raw form data for {domain}: {user_input}")

            # Extract flow_id from form data
            flow_id = user_input.pop("flow_id", None)

            # Get the stored schema from the flow object for type conversion
            hass = self._shim_manager.get_hass()
            flow = hass.config_entries._flow_progress.get(flow_id)
            schema = getattr(flow, "_last_form_schema", None) if flow else None

            # Handle menu selection
            if "next_step" in user_input:
                user_input = {"next_step": user_input["next_step"]}
                _LOGGER.debug(
                    f"Options flow menu selection for {domain}: flow_id={flow_id}, "
                    f"selected_option={user_input['next_step']}"
                )
            else:
                _LOGGER.debug(
                    f"Options flow submit for {domain}: flow_id={flow_id}, "
                    f"schema={schema}, user_input={user_input}"
                )

            # Convert form values based on schema field types (same as config flow)
            if schema and hasattr(schema, "schema"):
                import voluptuous as vol

                # Log all incoming form values for debugging
                for key, value in user_input.items():
                    _LOGGER.debug(
                        f"Options form input '{key}': {repr(value)} (type: {type(value).__name__}, "
                        f"is_undefined: {self._is_undefined(value)})"
                    )

                schema_dict = schema.schema
                _LOGGER.debug(f"Options flow schema dict: {schema_dict}")
                if isinstance(schema_dict, dict):
                    converted_input = {}
                    for key, value in user_input.items():
                        # Find the validator and schema key for this field
                        validator = None
                        field_schema_key = None
                        for schema_key, schema_val in schema_dict.items():
                            field_name = (
                                schema_key.schema
                                if hasattr(schema_key, "schema")
                                else schema_key
                            )
                            if field_name == key:
                                validator = schema_val
                                field_schema_key = schema_key
                                break

                        if validator:
                            # Check if the raw value is UNDEFINED (skip it)
                            if self._is_undefined(value):
                                _LOGGER.debug(
                                    f"Field '{key}': value is UNDEFINED, skipping"
                                )
                                continue

                            converted_value = self._convert_form_value(
                                value, validator, key
                            )

                            # Check if converted value is UNDEFINED
                            if self._is_undefined(converted_value):
                                _LOGGER.debug(
                                    f"Field '{key}': converted value is UNDEFINED, skipping"
                                )
                                continue

                            # Handle empty strings on Optional fields
                            is_optional = (
                                field_schema_key
                                and hasattr(field_schema_key, "__class__")
                                and field_schema_key.__class__.__name__ == "Optional"
                            )
                            has_explicit_default = (
                                field_schema_key
                                and hasattr(field_schema_key, "default")
                                and field_schema_key.default is not vol.UNDEFINED
                            )

                            if converted_value == "" and is_optional:
                                if has_explicit_default:
                                    # Use the explicit default value
                                    default_value = field_schema_key.default
                                    if callable(default_value):
                                        try:
                                            default_value = default_value()
                                        except Exception:
                                            default_value = None
                                    _LOGGER.debug(
                                        f"Field '{key}': empty string on Optional field "
                                        f"with default, using default={repr(default_value)}"
                                    )
                                    converted_input[key] = default_value
                                else:
                                    # No explicit default - skip this key entirely
                                    # This makes "if KEY in data" checks in integrations return False
                                    _LOGGER.debug(
                                        f"Field '{key}': empty string on Optional field "
                                        f"without default, skipping key"
                                    )
                                    # Don't add to converted_input - skip it
                            else:
                                _LOGGER.debug(
                                    f"Field '{key}': original='{value}' ({type(value).__name__}), "
                                    f"validator={validator.__class__.__name__}, "
                                    f"converted='{converted_value}' ({type(converted_value).__name__})"
                                )
                                converted_input[key] = converted_value
                        else:
                            converted_input[key] = value

                    # Add missing schema fields with their default values
                    for schema_key, schema_val in schema_dict.items():
                        field_name = (
                            schema_key.schema
                            if hasattr(schema_key, "schema")
                            else schema_key
                        )
                        if field_name not in converted_input:
                            if hasattr(schema_key, "default"):
                                default_value = schema_key.default
                                if callable(default_value):
                                    try:
                                        default_value = default_value()
                                    except Exception:
                                        continue
                                converted_input[field_name] = default_value

                    # Final pass: remove any UNDEFINED values that might have slipped through
                    for key in list(converted_input.keys()):
                        if self._is_undefined(converted_input[key]):
                            _LOGGER.debug(
                                f"Final cleanup: removing UNDEFINED value for '{key}'"
                            )
                            del converted_input[key]

                    user_input = converted_input
                    _LOGGER.debug(f"Options flow final user_input: {user_input}")

            # Continue options flow
            result = (
                await self._shim_manager.get_integration_loader().continue_options_flow(
                    entry, flow_id, user_input
                )
            )

            if not result:
                raise HTTPException(
                    status_code=400, detail="Failed to process options flow"
                )

            # Handle result
            if result.get("type") == "create_entry":
                # Options flow completed - reload the integration if it was loaded
                if entry.state == "loaded":
                    await (
                        self._shim_manager.get_integration_loader().reload_config_entry(
                            entry
                        )
                    )

                # Return success with HTMX redirect
                response = HTMLResponse(
                    '<div class="alert alert-success">Configuration updated successfully!</div>'
                )
                response.headers["HX-Redirect"] = self._get_detail_redirect(
                    request, domain
                )
                return response

            elif result.get("type") == "form":
                # Show next form step
                return self._render_config_step(
                    request, domain, result, is_options_flow=True, entry_id=entry_id
                )

            elif result.get("type") == "menu":
                # Show menu selection step
                return self._render_menu_step(
                    request, domain, result, is_options_flow=True, entry_id=entry_id
                )

            elif result.get("type") == "abort":
                return HTMLResponse(
                    f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
                )

            else:
                return HTMLResponse(
                    f'<div class="alert alert-error">Unknown result: {result.get("type")}</div>'
                )

        @self._app.post("/config/{entry_id}/reload")
        async def reload_config_entry(request: Request, entry_id: str):
            """Reload a config entry to apply configuration changes."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Find the entry
            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Reload the entry
            success = (
                await self._shim_manager.get_integration_loader().reload_config_entry(
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
                response.headers["HX-Redirect"] = self._get_detail_redirect(
                    request, domain
                )
                return response
            else:
                return HTMLResponse(
                    '<div class="alert alert-error">Failed to reload configuration</div>',
                    status_code=400,
                )

        @self._app.post("/config/{entry_id}/filters")
        async def update_entity_filters(request: Request, entry_id: str):
            """Update entity filters for a config entry."""
            # Check if still loading
            loading_response = self._check_loading()
            if loading_response:
                return loading_response

            # Find the entry
            entry = self._shim_manager.get_hass().config_entries.async_get_entry(
                entry_id
            )
            if not entry:
                raise HTTPException(status_code=404, detail="Config entry not found")

            domain = entry.domain

            # Parse form data
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

            # Validate patterns
            loader = self._shim_manager.get_integration_loader()

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

            # Update entry options - create a new dict to ensure change detection
            new_options = dict(entry.options)
            new_options["entity_filters"] = entity_patterns
            new_options["entity_name_filters"] = name_patterns
            self._shim_manager.get_hass().config_entries.async_update_entry(
                entry, options=new_options
            )

            # Apply filters (remove newly filtered entities)
            result = await loader.async_apply_entity_filters(entry)

            total_patterns = len(entity_patterns) + len(name_patterns)
            _LOGGER.info(
                f"Updated entity filters for {domain} entry {entry_id}: "
                f"{total_patterns} patterns ({len(entity_patterns)} ID, {len(name_patterns)} name), "
                f"removed {result['removed']} entities"
            )

            # Return success with HTMX redirect
            html = (
                f'<div class="alert alert-success">'
                f"Entity filters updated successfully! "
                f"Removed {result['removed']} filtered entities."
                f"</div>"
            )
            response = HTMLResponse(content=html)
            response.headers["HX-Redirect"] = self._get_detail_redirect(request, domain)
            return response

        @self._app.get("/api/integrations", response_class=JSONResponse)
        async def api_integrations():
            """API endpoint for integration list."""
            installed = (
                self._shim_manager.get_integration_manager().get_all_integrations()
            )
            available = self._shim_manager.get_integration_manager().get_available_integrations()

            return {
                "installed": [
                    {
                        "domain": i.domain,
                        "name": i.name,
                        "version": i.version,
                        "enabled": i.enabled,
                        "update_available": i.update_available,
                        "latest_version": i.latest_version,
                    }
                    for i in installed
                ],
                "available": [
                    {
                        "full_name": a.get("full_name"),
                        "domain": a["domain"],
                        "name": a["name"],
                        "description": a.get("description", ""),
                    }
                    for a in available
                ],
            }

        @self._app.get("/api/entities", response_class=JSONResponse)
        async def api_entities():
            """API endpoint for all entities."""
            entities = []
            for (
                domain
            ) in self._shim_manager.get_integration_loader().get_loaded_integrations():
                for entity in self._shim_manager.get_integration_loader().get_entities(
                    integration_domain=domain
                ):
                    entities.append(
                        {
                            "entity_id": entity.entity_id,
                            "name": entity.name,
                            "state": entity.state,
                            "available": entity.available,
                        }
                    )

            return {"entities": entities}

        @self._app.get("/api/status", response_class=JSONResponse)
        async def api_status():
            """API endpoint for shim status."""
            mqtt_bridge = self._shim_manager.get_mqtt_bridge()
            mqtt_status = (
                mqtt_bridge.connection_status
                if mqtt_bridge
                else {"connected": False, "error": "MQTT bridge not available"}
            )

            return {
                "running": True,
                "loaded_integrations": self._shim_manager.get_integration_loader().get_loaded_integrations(),
                "total_entities": len(
                    self._shim_manager.get_integration_loader().get_entities()
                ),
                "mqtt": mqtt_status,
            }

        @self._app.get("/api/mqtt-status", response_class=JSONResponse)
        async def api_mqtt_status():
            """API endpoint for MQTT connection status."""
            mqtt_bridge = self._shim_manager.get_mqtt_bridge()
            if mqtt_bridge:
                return mqtt_bridge.connection_status
            return {"connected": False, "error": "MQTT bridge not available"}

        @self._app.get("/mqtt-status-fragment", response_class=HTMLResponse)
        async def mqtt_status_fragment():
            """HTML fragment for MQTT status display (used by HTMX)."""
            mqtt_bridge = self._shim_manager.get_mqtt_bridge()
            mqtt_status = (
                mqtt_bridge.connection_status
                if mqtt_bridge
                else {"connected": False, "error": "MQTT bridge not available"}
            )

            return self._render_template(
                "mqtt_status.html",
                mqtt_status=mqtt_status,
            )

        @self._app.get("/status-fragment", response_class=HTMLResponse)
        async def status_fragment():
            """HTML fragment for status display (used by HTMX)."""
            loaded_integrations = (
                self._shim_manager.get_integration_loader().get_loaded_integrations()
            )
            total_entities = len(
                self._shim_manager.get_integration_loader().get_entities()
            )

            mqtt_bridge = self._shim_manager.get_mqtt_bridge()
            mqtt_status = (
                mqtt_bridge.connection_status
                if mqtt_bridge
                else {"connected": False, "error": "MQTT bridge not available"}
            )

            return self._render_template(
                "status.html",
                loaded_integrations=loaded_integrations,
                total_entities=total_entities,
                mqtt_status=mqtt_status,
            )

        @self._app.get("/api/custom-repos", response_class=JSONResponse)
        async def api_custom_repos():
            """API endpoint for custom repositories."""
            repos = (
                self._shim_manager.get_integration_manager().get_custom_repositories()
            )
            return {"repositories": repos}

        @self._app.get("/api/unsupported-repos", response_class=JSONResponse)
        async def api_unsupported_repos():
            """API endpoint for listing unsupported repositories.

            This returns the static list of repositories that are known to be
            incompatible with the shim. The list is maintained as a read-only
            static file and cannot be modified via API.
            """
            repos = self._shim_manager.get_integration_manager().get_unsupported_repos()
            return {"repositories": repos}

        @self._app.get("/api/verified-repos", response_class=JSONResponse)
        async def api_verified_repos():
            """API endpoint for listing verified repositories.

            This returns the static list of repositories that are known to be
            compatible and tested with the shim. The list is maintained as a
            read-only static file and cannot be modified via API.
            """
            repos = self._shim_manager.get_integration_manager().get_verified_repos()
            return {"repositories": repos}

        @self._app.post("/custom-repos")
        async def add_custom_repo(request: Request, repo_url: str = Form(...)):
            """Add a custom repository."""
            (
                success,
                message,
            ) = await self._shim_manager.get_integration_manager().add_custom_repository(
                repo_url
            )
            if success:
                # Return updated custom repos list for HTMX
                custom_repos = self._shim_manager.get_integration_manager().get_custom_repositories()
                response = self._render_custom_repos_list(
                    custom_repos, success_message=message
                )
                # Trigger client-side redirect via HTMX
                response.headers["HX-Location"] = "#custom"
                return response
            else:
                # Return error message for HTMX
                custom_repos = self._shim_manager.get_integration_manager().get_custom_repositories()
                return self._render_custom_repos_list(
                    custom_repos, error_message=message
                )

        @self._app.delete("/custom-repos/{domain}")
        async def remove_custom_repo(domain: str):
            """Remove a custom repository."""
            (
                success,
                message,
            ) = await self._shim_manager.get_integration_manager().remove_custom_repository(
                domain
            )
            # Get updated list
            custom_repos = (
                self._shim_manager.get_integration_manager().get_custom_repositories()
            )
            if success:
                response = self._render_custom_repos_list(
                    custom_repos, success_message=message
                )
                # Trigger client-side redirect via HTMX
                response.headers["HX-Location"] = "#custom"
                return response
            else:
                return self._render_custom_repos_list(
                    custom_repos, error_message=message
                )

    def _render_custom_repos_list(
        self, repos: List[dict], success_message: str = None, error_message: str = None
    ) -> HTMLResponse:
        """Render the custom repositories list HTML."""
        html_parts = []

        if success_message:
            html_parts.append(
                '<div class="alert alert-success" style="margin-bottom: 15px;">'
                + success_message
                + "</div>"
            )

        if error_message:
            html_parts.append(
                '<div class="alert alert-error" style="margin-bottom: 15px;">'
                + error_message
                + "</div>"
            )

        if repos:
            html_parts.append('<div class="integration-list">')
            for repo in repos:
                name = repo.get("name", repo.get("domain", "Unknown"))
                description = repo.get("description", "No description")
                repo_url = repo.get("repository_url", "#")
                full_name = repo.get("full_name", repo.get("repository_url", "Unknown"))
                domain = repo.get("domain", "")
                is_installed = repo.get("installed", False)

                installed_badge = (
                    '<span class="pico-color-jade-500" style="font-weight: 600; margin-left: 10px;">✓ Installed</span>'
                    if is_installed
                    else ""
                )

                if is_installed:
                    actions_html = '<span class="pico-color-muted" style="font-size: 12px;">Remove integration first</span>'
                else:
                    actions_html = (
                        '<button type="button" class="secondary" '
                        'hx-delete="custom-repos/' + domain + '" '
                        'hx-target="#custom-repos-list" '
                        'hx-swap="innerHTML" '
                        'hx-confirm="Are you sure you want to remove this repository?" '
                        'hx-indicator="#remove-repo-' + domain + '-spinner" '
                        'hx-disabled-elt="this">'
                        '<span id="remove-repo-'
                        + domain
                        + '-spinner" class="htmx-indicator spinner" style="width: 14px; height: 14px; margin-right: 6px;"></span>'
                        '<span class="button-text">Remove</span>'
                        "</button>"
                    )

                html_parts.append(
                    "<article>"
                    '<div style="display: flex; justify-content: space-between; align-items: center; gap: 1rem;">'
                    "<div>"
                    "<h3>" + name + "</h3>"
                    '<p class="pico-color-muted">' + description + "</p>"
                    '<div style="font-size: 12px; margin-top: 5px;">'
                    '<a href="'
                    + repo_url
                    + '" target="_blank">'
                    + full_name
                    + "</a>"
                    + installed_badge
                    + "</div></div>"
                    '<div style="flex-shrink: 0;">' + actions_html + "</div>"
                    "</div>"
                    "</article>"
                )
            html_parts.append("</div>")
        else:
            html_parts.append(
                '<div class="empty-state">'
                "<p>No custom repositories added.</p>"
                "<p>Add a GitHub repository URL above to get started.</p>"
                "</div>"
            )

        return HTMLResponse(content="".join(html_parts))

    # Error message translations
    ERROR_TRANSLATIONS = {
        "invalid_auth": "Invalid credentials. Please check your username/password.",
        "cannot_connect": "Cannot connect to device. Please check the IP address and ensure the device is online.",
        "cannot_find": "Cannot find device. Please check your network connection.",
        "invalid_host": "Invalid host address. Please check the IP address.",
        "unknown": "An unknown error occurred. Please try again.",
        "cannot_parse_wifi_info": "Failed to parse WiFi information. Please check your SSID and password.",
    }

    def _render_config_step(
        self,
        request: Request,
        domain: str,
        result: dict,
        is_options_flow: bool = False,
        entry_id: str = None,
    ) -> HTMLResponse:
        """Render a config flow form step."""
        schema = result.get("data_schema")
        errors = result.get("errors", {})
        description = result.get("description_placeholders", {})
        flow_id = result.get("flow_id")
        step_id = result.get("step_id", "user")

        # Store schema in flow object for type conversion on submit
        if flow_id and schema:
            hass = self._shim_manager.get_hass()
            flow = hass.config_entries._flow_progress.get(flow_id)
            if flow:
                flow._last_form_schema = schema

        # Load translations for field labels and error messages
        translations = self._load_integration_translations(domain)

        # Translate error messages (check integration translations first, then fall back)
        translated_errors = {}
        config_errors = translations.get("config", {}).get("error", {})
        for key, error_key in errors.items():
            if isinstance(error_key, str):
                # First check integration translations, then static translations
                translated_errors[key] = config_errors.get(
                    error_key, self.ERROR_TRANSLATIONS.get(error_key, error_key)
                )
            else:
                translated_errors[key] = str(error_key)

        # Convert voluptuous schema to form fields
        fields = self._parse_schema(schema)

        # Apply field translations
        if translations:
            self._apply_field_translations(fields, translations, step_id)

        # Check if this is an HTMX request for partial rendering
        is_htmx = request.headers.get("HX-Request") == "true"

        if is_htmx and is_options_flow:
            # Render only the form content without full page wrapper for HTMX
            html = self._render_template(
                "config_form_content.html",
                request=request,
                domain=domain,
                fields=fields,
                errors=translated_errors,
                description=description,
                step_id=step_id,
                flow_id=result.get("flow_id"),
                is_options_flow=is_options_flow,
                entry_id=entry_id,
            )
        else:
            # Render full page with base template
            html = self._render_template(
                "config_form.html",
                request=request,
                domain=domain,
                fields=fields,
                errors=translated_errors,
                description=description,
                step_id=step_id,
                flow_id=result.get("flow_id"),
                is_options_flow=is_options_flow,
                entry_id=entry_id,
            )
        return HTMLResponse(content=html)

    def _render_menu_step(
        self,
        request: Request,
        domain: str,
        result: dict,
        is_options_flow: bool = False,
        entry_id: str = None,
    ) -> HTMLResponse:
        """Render a config flow menu step with options."""
        menu_options = result.get("menu_options", [])
        description = result.get("description_placeholders", {})
        flow_id = result.get("flow_id")
        step_id = result.get("step_id", "user")

        # Determine form action URL
        if is_options_flow and entry_id:
            form_action = "reconfigure"
            title_prefix = "Reconfigure"
        else:
            form_action = domain
            title_prefix = "Configure"

        # Build HTML for menu options
        options_html = ""
        for option in menu_options:
            option_label = option.replace("_", " ").title()
            options_html += f"""
            <button type="submit" name="next_step" value="{option}" class="btn btn-secondary" style="margin: 10px; padding: 15px 30px;">
                {option_label}
            </button>
            """

        # Load translations for menu step
        translations = self._load_integration_translations(domain)
        step_translations = (
            translations.get("config", {}).get("step", {}).get(step_id, {})
        )
        menu_title = step_translations.get("title", f"{title_prefix} {domain.title()}")
        menu_description = step_translations.get("description", "")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{menu_title}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="{PICO_CSS_URL}">
            <link rel="stylesheet" href="{PICO_COLORS_URL}">
            <script src="{HTMX_URL}" integrity="{HTMX_SRI}" crossorigin="anonymous"></script>
            <style>
                :root {{
                    --pico-font-size: 1rem;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>{menu_title}</h2>
                {"<p>" + menu_description + "</p>" if menu_description else ""}
                {"<p>" + str(description) + "</p>" if description else ""}
                <form hx-post="{form_action}" hx-target="#config-result" hx-swap="innerHTML">
                    <input type="hidden" name="flow_id" value="{flow_id}">
                    <input type="hidden" name="step_id" value="{step_id}">
                    <div style="margin: 20px 0;">
                        <label style="display: block; margin-bottom: 15px;">Select an option:</label>
                        <div style="display: flex; flex-wrap: wrap; gap: 10px;">
                            {options_html}
                        </div>
                    </div>
                </form>
                <div id="config-result"></div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    def _render_external_step(
        self,
        request: Request,
        domain: str,
        result: dict,
        is_options_flow: bool = False,
        entry_id: str = None,
    ) -> HTMLResponse:
        """Render an OAuth2 external authorization step."""
        url = result.get("url", "")
        description = result.get("description_placeholders", {})
        flow_id = result.get("flow_id")
        step_id = result.get("step_id", "auth")

        # Compute the callback check URL for polling
        if is_options_flow and entry_id:
            form_action = entry_id
            title_prefix = "Reconfigure"
        else:
            form_action = domain
            title_prefix = "Configure"

        # Load translations
        translations = self._load_integration_translations(domain)
        step_translations = (
            translations.get("config", {}).get("step", {}).get(step_id, {})
        )
        step_title = step_translations.get(
            "title", f"{title_prefix} {domain.title()}"
        )
        step_description = step_translations.get("description", "")

        # Build description placeholders text
        desc_text = ""
        if description:
            for key, value in description.items():
                desc_text += f"<p><strong>{key}:</strong> {value}</p>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{step_title}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="{PICO_CSS_URL}">
            <link rel="stylesheet" href="{PICO_COLORS_URL}">
            <script src="{HTMX_URL}" integrity="{HTMX_SRI}" crossorigin="anonymous"></script>
            <style>
                :root {{
                    --pico-font-size: 1rem;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>{step_title}</h2>
                {"<p>" + step_description + "</p>" if step_description else ""}
                {desc_text}
                <div class="alert alert-info" role="alert">
                    <p><strong>Step 1:</strong> Click the button below to open the authorization page.</p>
                    <p><strong>Step 2:</strong> Complete authorization in the new window.</p>
                    <p><strong>Step 3:</strong> Return here and click <strong>Continue Setup</strong>.</p>
                </div>
                <div style="margin: 20px 0; display: flex; gap: 15px; flex-wrap: wrap;">
                    <a href="{url}" target="_blank" rel="noopener noreferrer"
                       class="btn btn-primary" style="padding: 15px 30px; font-size: 1.1rem;">
                        Authorize with {domain.title()}
                    </a>
                    <form hx-post="{form_action}" hx-target="#config-result" hx-swap="innerHTML" style="margin: 0;">
                        <input type="hidden" name="flow_id" value="{flow_id}">
                        <button type="submit" class="btn btn-secondary" style="padding: 15px 30px; font-size: 1.1rem;">
                            Continue Setup
                        </button>
                    </form>
                </div>
                <div id="config-result"></div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    def _parse_schema(self, schema) -> List[Dict[str, Any]]:
        """Parse a voluptuous schema into form field definitions."""
        fields = []

        if schema is None:
            return fields

        try:
            # Handle voluptuous Schema
            if hasattr(schema, "schema"):
                schema_dict = schema.schema
                if isinstance(schema_dict, dict):
                    for key, validator in schema_dict.items():
                        field = self._parse_field(key, validator)
                        if field:
                            fields.append(field)
        except Exception as e:
            _LOGGER.warning(f"Failed to parse schema: {e}")

        return fields

    def _parse_field(self, key, validator) -> Optional[Dict[str, Any]]:
        """Parse a single schema field into form field definition."""
        import voluptuous as vol

        field = {
            "name": None,
            "label": None,
            "type": "text",
            "required": False,
            "default": None,
            "options": None,
            "placeholder": None,
        }

        # Extract field name from key (Required/Optional marker)
        if hasattr(key, "schema"):
            # This is a Required/Optional marker
            field["name"] = key.schema
            # Check if it's Required or Optional by checking the type
            field["required"] = isinstance(key, vol.Required)
            # Get default if Optional with default value
            if hasattr(key, "default"):
                # In voluptuous, Required raises Undefined if no default
                # Optional has UNDEFINED as default if no default specified
                if key.default is not vol.UNDEFINED:
                    # Handle callable defaults (e.g., lambda functions)
                    if callable(key.default):
                        try:
                            field["default"] = key.default()
                        except Exception:
                            # If the callable fails, don't set a default
                            pass
                    else:
                        field["default"] = key.default
            # Check for suggested_value in description (used by some integrations like cryptoinfo)
            if field["default"] is None and hasattr(key, "description"):
                description = key.description
                if isinstance(description, dict):
                    if "suggested_value" in description:
                        suggested = description["suggested_value"]
                        # Skip UNDEFINED suggested values
                        if not self._is_undefined(suggested):
                            field["default"] = suggested
                    # Extract help text description if present
                    if "description" in description:
                        field["description"] = description["description"]
        else:
            field["name"] = key

        # Handle list-type defaults - convert to first element for text fields
        # or keep as-is for multi-select fields
        if field["default"] is not None and isinstance(field["default"], (list, tuple)):
            _LOGGER.debug(
                f"Field {field['name']}: converting list default {field['default']} to first element"
            )
            if len(field["default"]) > 0:
                field["default"] = field["default"][0]
            else:
                field["default"] = None
            _LOGGER.debug(f"Field {field['name']}: new default is {field['default']}")

        # Make label from field name (capitalize and replace underscores)
        field["label"] = field["name"].replace("_", " ").title()

        # Parse validator type
        # Handle plain Python types (e.g., str, bool, int) used directly
        if validator is bool:
            field["type"] = "checkbox"
        elif validator is str:
            field["type"] = "text"
        elif validator is int:
            field["type"] = "number"
        elif validator is float:
            field["type"] = "number"
            field["step"] = "any"
        elif isinstance(validator, type):
            # Other Python types default to text
            field["type"] = "text"
        elif hasattr(validator, "__class__"):
            validator_class = validator.__class__.__name__

            if validator_class == "In":
                # Select field with options
                field["type"] = "select"
                if hasattr(validator, "container"):
                    container = validator.container
                    if isinstance(container, dict):
                        # Dict mapping values to labels
                        field["options"] = [
                            {
                                "value": k,
                                "label": v,
                                "selected": k == field.get("default"),
                            }
                            for k, v in container.items()
                        ]
                    elif isinstance(container, (list, tuple)):
                        # List of values
                        field["options"] = [
                            {
                                "value": v,
                                "label": str(v),
                                "selected": v == field.get("default"),
                            }
                            for v in container
                        ]
            elif validator_class == "Email":
                field["type"] = "email"
            elif validator_class == "Url":
                field["type"] = "url"
            elif validator_class == "Number":
                field["type"] = "number"
            elif validator_class == "Boolean":
                field["type"] = "checkbox"
            elif validator_class == "Password":
                field["type"] = "password"
            elif validator_class == "SelectSelector":
                # Handle SelectSelector from homeassistant.helpers.selector
                field["type"] = "select"
                _LOGGER.debug(f"SelectSelector found for field {field['name']}")
                if hasattr(validator, "config"):
                    config = validator.config
                    _LOGGER.debug(f"SelectSelector config: {config}")
                    options = config.get("options", [])
                    multiple = config.get("multiple", False)
                    _LOGGER.debug(
                        f"SelectSelector options: {options}, multiple: {multiple}"
                    )
                    # Handle options as list of dicts (SelectOptionDict) or simple values
                    parsed_options = []
                    for opt in options:
                        if isinstance(opt, dict) and "value" in opt and "label" in opt:
                            # SelectOptionDict format: {"value": "...", "label": "..."}
                            parsed_options.append(
                                {
                                    "value": opt["value"],
                                    "label": opt["label"],
                                    "selected": opt["value"] in field.get("default", [])
                                    if multiple
                                    else opt["value"] == field.get("default"),
                                }
                            )
                        else:
                            # Simple value format
                            parsed_options.append(
                                {
                                    "value": opt,
                                    "label": str(opt),
                                    "selected": opt in field.get("default", [])
                                    if multiple
                                    else opt == field.get("default"),
                                }
                            )
                    field["options"] = parsed_options
                    field["multiple"] = multiple
                    _LOGGER.debug(
                        f"Parsed {len(field['options'])} options for field {field['name']}"
                    )
                else:
                    _LOGGER.debug(f"SelectSelector has no config attribute")
            elif validator_class == "TextSelector":
                # Handle TextSelector from homeassistant.helpers.selector
                if hasattr(validator, "config"):
                    config = validator.config
                    selector_type = config.get("type", "text")
                    if selector_type == "password":
                        field["type"] = "password"
                    elif selector_type == "email":
                        field["type"] = "email"
                    elif selector_type == "url":
                        field["type"] = "url"
                    elif selector_type == "tel":
                        field["type"] = "tel"
                    else:
                        field["type"] = "text"
                else:
                    field["type"] = "text"
            elif validator_class == "NumberSelector":
                # Handle NumberSelector from homeassistant.helpers.selector
                field["type"] = "number"
                if hasattr(validator, "config"):
                    config = validator.config
                    if "min" in config:
                        field["min"] = config["min"]
                    if "max" in config:
                        field["max"] = config["max"]
                    if "step" in config:
                        field["step"] = config["step"]
            elif validator_class == "BooleanSelector":
                # Handle BooleanSelector from homeassistant.helpers.selector
                field["type"] = "checkbox"

        # Check for Coerce (type conversion)
        if hasattr(validator, "type") and validator_class == "Coerce":
            if validator.type == int:
                field["type"] = "number"
            elif validator.type == float:
                field["type"] = "number"
                field["step"] = "any"

        # Handle dict-based selectors (e.g., selector({"select": {...}}) or {"select": {...}})
        if isinstance(validator, dict):
            # Check if this is a selector dict (has single key like "select", "text", "number", etc.)
            if len(validator) == 1:
                selector_type = list(validator.keys())[0]
                selector_config = validator[selector_type]

                if selector_type == "select":
                    field["type"] = "select"
                    options = selector_config.get("options", [])
                    mode = selector_config.get("mode", "list")  # "list" or "dropdown"

                    # Parse options
                    parsed_options = []
                    for opt in options:
                        if isinstance(opt, dict) and "value" in opt:
                            # Dict format: {"value": "...", "label": "..."}
                            parsed_options.append(
                                {
                                    "value": opt["value"],
                                    "label": opt.get("label", str(opt["value"])),
                                    "selected": opt["value"] == field.get("default"),
                                }
                            )
                        else:
                            # Simple value format
                            parsed_options.append(
                                {
                                    "value": opt,
                                    "label": str(opt),
                                    "selected": opt == field.get("default"),
                                }
                            )
                    field["options"] = parsed_options
                    field["mode"] = mode
                    _LOGGER.debug(
                        f"Dict-based select selector: {len(parsed_options)} options, mode={mode}"
                    )
                elif selector_type == "boolean":
                    field["type"] = "checkbox"
                elif selector_type == "text":
                    text_type = selector_config.get("type", "text")
                    if text_type == "password":
                        field["type"] = "password"
                    elif text_type == "email":
                        field["type"] = "email"
                    else:
                        field["type"] = "text"
                elif selector_type == "number":
                    field["type"] = "number"
                    if "min" in selector_config:
                        field["min"] = selector_config["min"]
                    if "max" in selector_config:
                        field["max"] = selector_config["max"]
                    if "step" in selector_config:
                        field["step"] = selector_config["step"]

        # Detect password fields by name (in addition to Password validator)
        # But don't override checkbox (boolean) types
        if field.get("type") != "checkbox":
            password_keywords = [
                "password",
                "secret",
                "token",
                "api_key",
                "credential",
                "otp",
                "key",
            ]
            field_name_lower = field["name"].lower()
            if any(keyword in field_name_lower for keyword in password_keywords):
                field["type"] = "password"

        # Clean up None values
        field = {k: v for k, v in field.items() if v is not None}

        _LOGGER.debug(
            f"Parsed field {field['name']}: type={field.get('type')}, default={field.get('default')!r}"
        )

        return field

    def _load_integration_translations(self, domain: str) -> dict:
        """Load translations for an integration.

        Args:
            domain: The integration domain

        Returns:
            The translations dictionary, or empty dict if not found
        """
        try:
            # Use the integration manager to find the integration path
            integration_path = self._integration_manager.get_integration_path(domain)

            if not integration_path:
                return {}

            # Look for translations/en.json (or strings.json as fallback)
            translations_file = integration_path / "translations" / "en.json"
            if not translations_file.exists():
                translations_file = integration_path / "strings.json"

            if not translations_file.exists():
                return {}

            with open(translations_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError) as e:
            _LOGGER.debug(f"Failed to load translations for {domain}: {e}")
            return {}

    def _apply_field_translations(
        self, fields: list, translations: dict, step_id: str
    ) -> None:
        """Apply field labels and descriptions from translations.

        Args:
            fields: List of field dictionaries to modify
            translations: The loaded translations dictionary
            step_id: The current config flow step ID (e.g., "user", "reconfigure")
        """
        # Get the config section for this step
        config_section = translations.get("config", {})
        steps = config_section.get("step", {})
        step_data = steps.get(step_id, {})

        # Get field labels from data.{field_name}
        data_labels = step_data.get("data", {})
        # Get field descriptions from data_description.{field_name}
        data_descriptions = step_data.get("data_description", {})

        # Get selector translations for options
        selector_translations = translations.get("selector", {})

        for field in fields:
            field_name = field.get("name", "")
            if not field_name:
                continue

            # Apply label if available in translations
            if field_name in data_labels:
                field["label"] = data_labels[field_name]

            # Apply description if available in translations
            if field_name in data_descriptions:
                field["description"] = data_descriptions[field_name]

            # Apply selector option translations for select fields
            if field.get("type") == "select" and field_name in selector_translations:
                selector_config = selector_translations[field_name]
                option_labels = selector_config.get("options", {})
                if option_labels and field.get("options"):
                    # Map the option values to their translated labels
                    for option in field["options"]:
                        option_value = option.get("value", "")
                        if option_value in option_labels:
                            option["label"] = option_labels[option_value]

    def _convert_form_value(self, value: str, validator, field_name: str = "") -> Any:
        """Convert a form string value to the appropriate type based on validator."""
        import voluptuous as vol

        if not isinstance(value, str):
            return value

        validator_class = validator.__class__.__name__

        # Handle plain Python types (int, float, bool, str)
        if validator is str or validator_class == "type" and validator == str:
            # Keep empty strings for str validators (integrations expect "" not None)
            return value

        # Handle empty strings for non-str types
        if value == "":
            # For numeric types, return 0 instead of None to avoid comparison issues
            if (
                validator is int
                or validator_class == "Coerce"
                and hasattr(validator, "type")
                and validator.type == int
            ):
                return 0
            if (
                validator is float
                or validator_class == "Coerce"
                and hasattr(validator, "type")
                and validator.type == float
            ):
                return 0.0
            # For string validators (cv.string), keep empty string
            if validator_class == "function":
                return ""
            return None

        if validator is int or validator_class == "type" and validator == int:
            try:
                return int(value)
            except (ValueError, TypeError):
                return value

        if validator is float or validator_class == "type" and validator == float:
            try:
                return float(value)
            except (ValueError, TypeError):
                return value

        if validator is bool or validator_class == "type" and validator == bool:
            return value.lower() in ("true", "1", "yes", "on")

        # Handle Coerce validators (most common for type conversion)
        if validator_class == "Coerce" and hasattr(validator, "type"):
            coerce_type = validator.type
            try:
                if coerce_type == int:
                    return int(value)
                elif coerce_type == float:
                    return float(value)
                elif coerce_type == bool:
                    return value.lower() in ("true", "1", "yes", "on")
                else:
                    return coerce_type(value)
            except (ValueError, TypeError):
                return value

        # Handle Number validator
        if validator_class == "Number":
            try:
                return float(value)
            except (ValueError, TypeError):
                return value

        # Handle Boolean validator
        if validator_class == "Boolean":
            return value.lower() in ("true", "1", "yes", "on")

        # Handle Range validator (wrapper around another validator)
        if validator_class == "Range" and hasattr(validator, "schema"):
            return self._convert_form_value(value, validator.schema, field_name)

        # Handle All validator (composite)
        if validator_class == "All" and hasattr(validator, "validators"):
            for v in validator.validators:
                result = self._convert_form_value(value, v, field_name)
                if result != value:  # If conversion happened
                    return result

        # Handle function validators (like cv.latitude, cv.longitude)
        # These are plain functions, not classes
        if validator_class == "function":
            # Try to infer type from field name
            field_lower = field_name.lower()
            if any(
                x in field_lower for x in ["latitude", "longitude", "lat", "lon", "lng"]
            ):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value
            elif (
                "radius" in field_lower
                or "interval" in field_lower
                or "altitude" in field_lower
            ):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value

        # Default: return as-is
        return value

        # Handle empty strings
        if value == "":
            return None

        validator_class = validator.__class__.__name__

        # Handle Coerce validators (most common for type conversion)
        if validator_class == "Coerce" and hasattr(validator, "type"):
            coerce_type = validator.type
            try:
                if coerce_type == int:
                    return int(value)
                elif coerce_type == float:
                    return float(value)
                elif coerce_type == bool:
                    return value.lower() in ("true", "1", "yes", "on")
                else:
                    return coerce_type(value)
            except (ValueError, TypeError):
                return value

        # Handle Number validator
        if validator_class == "Number":
            try:
                return float(value)
            except (ValueError, TypeError):
                return value

        # Handle Boolean validator
        if validator_class == "Boolean":
            return value.lower() in ("true", "1", "yes", "on")

        # Handle Range validator (wrapper around another validator)
        if validator_class == "Range" and hasattr(validator, "schema"):
            return self._convert_form_value(value, validator.schema, field_name)

        # Handle All validator (composite)
        if validator_class == "All" and hasattr(validator, "validators"):
            for v in validator.validators:
                result = self._convert_form_value(value, v, field_name)
                if result != value:  # If conversion happened
                    return result

        # Handle function validators (like cv.latitude, cv.longitude)
        # These are plain functions, not classes
        if validator_class == "function":
            # Try to infer type from field name
            field_lower = field_name.lower()
            if any(
                x in field_lower for x in ["latitude", "longitude", "lat", "lon", "lng"]
            ):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value
            elif (
                "radius" in field_lower
                or "interval" in field_lower
                or "altitude" in field_lower
            ):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value

        # Default: return as-is
        return value

    def _is_undefined(self, value: Any) -> bool:
        """Check if a value is UNDEFINED (either HA's or voluptuous's).

        Args:
            value: The value to check

        Returns:
            True if the value is UNDEFINED, False otherwise
        """
        import voluptuous as vol

        if value is None:
            return False

        # Check for HA's UNDEFINED class (defined in import_patch.py)
        if hasattr(value, "__class__") and value.__class__.__name__ == "UNDEFINED":
            return True

        # Check for voluptuous's UNDEFINED (which is ... Ellipsis)
        if value is vol.UNDEFINED:
            return True

        return False

    def get_app(self) -> FastAPI:
        """Get the FastAPI application."""
        return self._app

    async def start(self) -> None:
        """Start the web server."""
        import logging
        import uvicorn

        # Custom formatter that cleans up uvicorn logger names for display
        class UvicornFormatter(colorlog.ColoredFormatter):
            def format(self, record):
                # Show uvicorn.error as just uvicorn for cleaner logs
                if record.name == "uvicorn.error":
                    record.name = "uvicorn"
                return super().format(record)

        # Custom logging config to match main app format
        log_format = "%(asctime)s %(log_color)s%(levelname)s%(reset)s: %(name)s - %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"
        log_colors = {
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        }

        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": UvicornFormatter,
                    "format": log_format,
                    "datefmt": date_format,
                    "log_colors": log_colors,
                },
                "access": {
                    "()": UvicornFormatter,
                    "format": log_format,
                    "datefmt": date_format,
                    "log_colors": log_colors,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "uvicorn": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["access"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
        }

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="info",
            log_config=log_config,
            loop="asyncio",  # Use standard asyncio loop explicitly
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except asyncio.CancelledError:
            # Graceful shutdown
            _LOGGER.debug("Web server shutting down...")
            await server.shutdown()
            raise
