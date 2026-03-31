"""Web UI for Home Assistant Shim.

FastAPI + HTMX interface for managing integrations and config flows.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, Template

from ..logging import get_logger
from ..core import ConfigEntry

_LOGGER = get_logger(__name__)


class WebUI:
    """Web UI for the HA shim."""

    def __init__(self, shim_manager, host: str = "0.0.0.0", port: int = 8080):
        self._shim_manager = shim_manager
        self._host = host
        self._port = port

        # Setup FastAPI
        self._app = FastAPI(title="HA Shim", version="0.1.0")

        # Setup templates - store directory path and load manually
        self._template_dir = Path(__file__).parent / "templates"

        # Setup static files
        # static_dir = Path(__file__).parent / "static"
        # self._app.mount(
        #     "/static", StaticFiles(directory=str(static_dir)), name="static"
        # )

        # Register routes
        self._register_routes()

    def _render_template(self, template_name: str, **context) -> str:
        """Render a template file directly without caching."""
        template_path = self._template_dir / template_name
        with open(template_path, "r") as f:
            template_content = f.read()

        # Create template without environment caching
        template = Template(template_content)
        return template.render(**context)

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
                    "domain": a.get("domain"),
                    "name": a.get("name"),
                    "description": a.get("description", ""),
                    "installed": a.get("installed", False),
                    "source": a.get("source", "hacs_default"),
                }
                for a in available
            ]

            html = self._render_template(
                "index.html",
                request=request,
                integrations=integrations_dicts,
                available=available_dicts,
                custom_repos=custom_repos,
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

            # Convert to dict for template compatibility
            info_dict = info.to_dict()
            entries_dicts = [
                {
                    "entry_id": e.entry_id,
                    "title": e.title,
                    "data": e.data,
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

            html = self._render_template(
                "integration_detail.html",
                request=request,
                integration=info_dict,
                entries=entries_dicts,
                entities=entities_dicts,
            )
            return HTMLResponse(content=html)

        @self._app.post("/integrations/{domain}/enable")
        async def enable_integration(domain: str):
            """Enable an integration."""
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
                return JSONResponse({"status": "success"})
            return JSONResponse({"status": "error"}, status_code=400)

        @self._app.post("/integrations/{domain}/disable")
        async def disable_integration(domain: str):
            """Disable an integration."""
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
                return JSONResponse({"status": "success"})
            return JSONResponse({"status": "error"}, status_code=400)

        @self._app.post("/integrations/{domain}/install")
        async def install_integration(domain: str, version: Optional[str] = Form(None)):
            """Install an integration."""
            success = await self._shim_manager.install_integration(
                domain, version=version
            )
            if success:
                return JSONResponse({"status": "success"})
            return JSONResponse({"status": "error"}, status_code=400)

        @self._app.post("/integrations/{domain}/remove")
        async def remove_integration(domain: str):
            """Remove an integration."""
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
                return JSONResponse({"status": "success"})
            return JSONResponse({"status": "error"}, status_code=400)

        @self._app.post("/integrations/{domain}/update")
        async def update_integration(domain: str):
            """Update an integration."""
            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if not info or not info.update_available:
                return JSONResponse({"status": "no_update"})

            # This triggers the update process
            await self._shim_manager._update_integration(domain)
            return JSONResponse({"status": "success"})

        @self._app.post("/config/{entry_id}/remove")
        async def remove_config_entry(entry_id: str):
            """Remove a config entry."""
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
                # Return redirect to integration detail page
                return HTMLResponse(
                    f'<div hx-trigger="load" hx-get="/integrations/{domain}" hx-target="body" hx-swap="outerHTML">'
                    f'<div class="alert alert-success">Configuration entry removed successfully!</div></div>'
                )
            else:
                return HTMLResponse(
                    '<div class="alert alert-error">Failed to remove configuration entry</div>',
                    status_code=400,
                )

        @self._app.get("/config/{domain}", response_class=HTMLResponse)
        async def config_flow_start(request: Request, domain: str):
            """Start a config flow for an integration."""
            # First ensure integration is loaded
            info = self._shim_manager.get_integration_manager().get_integration(domain)
            if not info:
                raise HTTPException(status_code=404, detail="Integration not found")

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
                schema_dict = schema.schema
                _LOGGER.debug(f"Schema dict: {schema_dict}")
                if isinstance(schema_dict, dict):
                    converted_input = {}
                    for key, value in user_input.items():
                        # Find the validator for this field
                        validator = None
                        for schema_key, schema_val in schema_dict.items():
                            field_name = (
                                schema_key.schema
                                if hasattr(schema_key, "schema")
                                else schema_key
                            )
                            if field_name == key:
                                validator = schema_val
                                break

                        if validator:
                            # Convert based on validator type
                            converted_value = self._convert_form_value(
                                value, validator, key
                            )
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
                entry = await self._shim_manager.create_config_entry(domain, entry_data)

                if entry:
                    # Setup the integration
                    setup_success = await self._shim_manager.get_integration_loader().setup_integration(
                        entry
                    )

                    if not setup_success:
                        # Remove the config entry since setup failed
                        await self._shim_manager.get_hass().config_entries.async_remove(
                            entry.entry_id
                        )
                        return HTMLResponse(
                            f'<div class="alert alert-error"><strong>Setup Failed</strong><br>Could not connect to the device. Please verify:<ul style="margin-top: 10px; margin-bottom: 0;"><li>The device is powered on and connected to your network</li><li>The credentials (serial, API key, etc.) are correct</li><li>The device IP address is reachable</li></ul></div>'
                        )
                        return HTMLResponse(
                            f'<div class="alert alert-error">Failed to setup integration. Please check your credentials and device connection.</div>'
                        )

                    # Return success with HTMX redirect
                    return HTMLResponse(
                        f'<div hx-trigger="load" hx-get="/integrations/{domain}" hx-target="body" hx-swap="outerHTML">'
                        f'<div class="alert alert-success">Configuration successful!</div></div>'
                    )
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

            elif result.get("type") == "abort":
                return HTMLResponse(
                    f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
                )

            else:
                return HTMLResponse(
                    f'<div class="alert alert-error">Unknown result: {result.get("type")}</div>'
                )

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
            return {
                "running": True,
                "loaded_integrations": self._shim_manager.get_integration_loader().get_loaded_integrations(),
                "total_entities": len(
                    self._shim_manager.get_integration_loader().get_entities()
                ),
            }

        @self._app.get("/api/custom-repos", response_class=JSONResponse)
        async def api_custom_repos():
            """API endpoint for custom repositories."""
            repos = (
                self._shim_manager.get_integration_manager().get_custom_repositories()
            )
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
                return self._render_custom_repos_list(
                    custom_repos, success_message=message
                )
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
                return self._render_custom_repos_list(
                    custom_repos, success_message=message
                )
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
                    '<span style="color: #4caf50; margin-left: 10px;">✓ Installed</span>'
                    if is_installed
                    else ""
                )

                if is_installed:
                    actions_html = '<span style="color: #999; font-size: 12px;">Remove integration first</span>'
                else:
                    actions_html = (
                        '<button type="button" class="btn btn-danger" '
                        'hx-delete="/custom-repos/' + domain + '" '
                        'hx-target="#custom-repos-list" '
                        'hx-swap="innerHTML" '
                        'hx-confirm="Are you sure you want to remove this repository?" '
                        'hx-on::after-request="if(event.detail.successful) location.reload();">'
                        "Remove</button>"
                    )

                html_parts.append(
                    '<div class="integration-item">'
                    '<div class="integration-info">'
                    "<h3>" + name + "</h3>"
                    "<p>" + description + "</p>"
                    '<div class="integration-meta">'
                    '<a href="'
                    + repo_url
                    + '" target="_blank" style="color: #03a9f4;">'
                    + full_name
                    + "</a>"
                    + installed_badge
                    + "</div></div>"
                    '<div class="integration-actions">' + actions_html + "</div>"
                    "</div>"
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
        self, request: Request, domain: str, result: dict
    ) -> HTMLResponse:
        """Render a config flow form step."""
        schema = result.get("data_schema")
        errors = result.get("errors", {})
        description = result.get("description_placeholders", {})
        flow_id = result.get("flow_id")

        # Store schema in flow object for type conversion on submit
        if flow_id and schema:
            hass = self._shim_manager.get_hass()
            flow = hass.config_entries._flow_progress.get(flow_id)
            if flow:
                flow._last_form_schema = schema

        # Translate error messages
        translated_errors = {}
        for key, error_key in errors.items():
            if isinstance(error_key, str):
                translated_errors[key] = self.ERROR_TRANSLATIONS.get(
                    error_key, error_key
                )
            else:
                translated_errors[key] = str(error_key)

        # Convert voluptuous schema to form fields
        # This is a simplified version - real implementation would need full schema parsing
        fields = self._parse_schema(schema)

        html = self._render_template(
            "config_form.html",
            request=request,
            domain=domain,
            fields=fields,
            errors=translated_errors,
            description=description,
            step_id=result.get("step_id", "user"),
            flow_id=result.get("flow_id"),
        )
        return HTMLResponse(content=html)

    def _render_menu_step(
        self, request: Request, domain: str, result: dict
    ) -> HTMLResponse:
        """Render a config flow menu step with options."""
        menu_options = result.get("menu_options", [])
        description = result.get("description_placeholders", {})
        flow_id = result.get("flow_id")
        step_id = result.get("step_id", "user")

        # Build HTML for menu options
        options_html = ""
        for option in menu_options:
            option_label = option.replace("_", " ").title()
            options_html += f"""
            <button type="submit" name="next_step" value="{option}" class="btn btn-secondary" style="margin: 10px; padding: 15px 30px;">
                {option_label}
            </button>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Configure {domain}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/static/styles.css">
            <script src="/static/htmx.min.js"></script>
        </head>
        <body>
            <div class="container">
                <h2>Configure {domain.title()}</h2>
                {"<p>" + str(description) + "</p>" if description else ""}
                <form hx-post="/config/{domain}" hx-target="#config-result" hx-swap="innerHTML">
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
                    field["options"] = [
                        {
                            "value": opt,
                            "label": str(opt),
                            "selected": opt in field.get("default", [])
                            if multiple
                            else opt == field.get("default"),
                        }
                        for opt in options
                    ]
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

        # Detect password fields by name (in addition to Password validator)
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

    def _convert_form_value(self, value: str, validator, field_name: str = "") -> Any:
        """Convert a form string value to the appropriate type based on validator."""
        import voluptuous as vol

        if not isinstance(value, str):
            return value

        # Handle empty strings
        if value == "":
            return None

        validator_class = validator.__class__.__name__

        # Handle plain Python types (int, float, bool)
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

    def get_app(self) -> FastAPI:
        """Get the FastAPI application."""
        return self._app

    async def start(self) -> None:
        """Start the web server."""
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except asyncio.CancelledError:
            # Graceful shutdown
            _LOGGER.info("Web server shutting down...")
            await server.shutdown()
            raise
