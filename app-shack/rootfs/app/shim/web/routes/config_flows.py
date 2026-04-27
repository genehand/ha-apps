"""Config flow and options flow routes.

Handles starting, submitting, and continuing config flows for integrations,
including OAuth2 external steps and reconfiguration (options flow).
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..renderers import (
    check_loading,
    get_detail_redirect,
    render_config_step,
    render_external_step,
    render_menu_step,
    render_template,
)
from urllib.parse import parse_qs, urlparse

from ..schema import convert_form_value, is_undefined

_LOGGER = logging.getLogger(__name__)


def _extract_oauth_params(callback_url: str) -> dict:
    """Extract OAuth code, state, and error from a callback URL.

    Accepts either a full URL (e.g. http://localhost:8080/...?code=...)
    or just a query string.  Returns a dict with ``code``, ``state``,
    and ``error`` keys (values may be None).
    """
    url = callback_url.strip()
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "code": query.get("code", [None])[0],
        "state": query.get("state", [None])[0],
        "error": query.get("error", [None])[0],
    }


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register config flow and options flow routes."""

    # ------------------------------------------------------------------ #
    #  Start config flow
    # ------------------------------------------------------------------ #

    @app.get("/config/{domain}", response_class=HTMLResponse)
    async def config_flow_start(request: Request, domain: str):
        """Start a config flow for an integration."""
        info = shim_manager.get_integration_manager().get_integration(domain)
        if not info:
            raise HTTPException(status_code=404, detail="Integration not found")

        hass = shim_manager.get_hass()

        # Store the localhost redirect URI so oauth2 stubs can read it.
        # The provider will redirect here, which will fail. The user copies
        # the full failed URL and pastes it back into the config form.
        from ...stubs.oauth2 import LOCALHOST_REDIRECT_URI

        hass.data["_oauth2_redirect_uri"] = LOCALHOST_REDIRECT_URI
        _LOGGER.debug("OAuth2 redirect URI set to: %s", LOCALHOST_REDIRECT_URI)

        result = (
            await shim_manager.get_integration_loader().start_config_flow(
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
                    content=render_template(
                        template_dir,
                        "config_form.html",
                        request=request,
                        domain=domain,
                        fields=[],
                        errors={"base": "Missing OAuth credentials. "
                                "Please add application credentials first."},
                        description={
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

        return render_config_step(
            shim_manager, request, domain, result, template_dir
        )

    # ------------------------------------------------------------------ #
    #  Submit config flow step
    # ------------------------------------------------------------------ #

    @app.post("/config/{domain}")
    async def config_flow_submit(request: Request, domain: str):
        """Submit a config flow step."""
        form_data = await request.form()
        user_input = dict(form_data)

        flow_id = user_input.pop("flow_id", None)

        hass = shim_manager.get_hass()
        flow = hass.config_entries._flow_progress.get(flow_id)
        schema = getattr(flow, "_last_form_schema", None) if flow else None

        # Handle pasted OAuth callback URL (copy-paste flow)
        oauth_callback_url = user_input.pop("oauth_callback_url", None)
        if oauth_callback_url:
            params = _extract_oauth_params(oauth_callback_url)
            state_param = params.get("state")
            code = params.get("code")
            error = params.get("error")

            if not state_param:
                return HTMLResponse(
                    '<div class="alert alert-error">'
                    "Missing <code>state</code> parameter in the callback URL. "
                    "Make sure you copied the full URL."
                    "</div>"
                )

            from ...stubs.oauth2 import _decode_jwt

            decoded_state = _decode_jwt(hass, state_param)
            if decoded_state is None:
                return HTMLResponse(
                    '<div class="alert alert-error">'
                    "Invalid <code>state</code> parameter. "
                    "The authorization link may have expired."
                    "</div>"
                )

            user_input = {"state": decoded_state}
            if code:
                user_input["code"] = code
            elif error:
                user_input["error"] = error
            else:
                return HTMLResponse(
                    '<div class="alert alert-error">'
                    "Missing <code>code</code> or <code>error</code> parameter "
                    "in the callback URL."
                    "</div>"
                )
            schema = None  # skip schema conversion for OAuth callback

        # Handle menu selection
        if "next_step" in user_input:
            user_input = {"next_step": user_input["next_step"]}
            _LOGGER.debug(
                "Config flow menu selection for %s: flow_id=%s, "
                "selected_option=%s",
                domain,
                flow_id,
                user_input["next_step"],
            )
        else:
            _LOGGER.debug(
                "Config flow submit for %s: flow_id=%s, schema=%s, user_input=%s",
                domain,
                flow_id,
                schema,
                user_input,
            )

        # Convert form values based on schema field types
        if schema and hasattr(schema, "schema"):
            import voluptuous as vol

            for key, value in user_input.items():
                _LOGGER.debug(
                    "Form input '%s': %r (type: %s, is_undefined: %s)",
                    key,
                    value,
                    type(value).__name__,
                    is_undefined(value),
                )

            schema_dict = schema.schema
            _LOGGER.debug("Schema dict: %s", schema_dict)
            if isinstance(schema_dict, dict):
                converted_input = {}
                for key, value in user_input.items():
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
                        if is_undefined(value):
                            _LOGGER.debug("Field '%s': value is UNDEFINED, skipping", key)
                            continue

                        converted_value = convert_form_value(
                            value, validator, key
                        )

                        if is_undefined(converted_value):
                            _LOGGER.debug(
                                "Field '%s': converted value is UNDEFINED, skipping", key
                            )
                            continue

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
                                default_value = field_schema_key.default
                                if callable(default_value):
                                    try:
                                        default_value = default_value()
                                    except Exception:
                                        default_value = None
                                _LOGGER.debug(
                                    "Field '%s': empty string on Optional field "
                                    "with default, using default=%r",
                                    key,
                                    default_value,
                                )
                                converted_input[key] = default_value
                            else:
                                _LOGGER.debug(
                                    "Field '%s': empty string on Optional field "
                                    "without default, skipping key",
                                    key,
                                )
                        else:
                            _LOGGER.debug(
                                "Field '%s': original='%s' (%s), "
                                "validator=%s, converted='%s' (%s)",
                                key,
                                value,
                                type(value).__name__,
                                validator.__class__.__name__,
                                converted_value,
                                type(converted_value).__name__,
                            )
                            converted_input[key] = converted_value
                    else:
                        _LOGGER.debug("Field '%s': no validator found, keeping as-is", key)
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
                            _LOGGER.debug(
                                "Field '%s': missing from form, using default=%r",
                                field_name,
                                default_value,
                            )
                            converted_input[field_name] = default_value

                # Final pass: remove any UNDEFINED values
                for key in list(converted_input.keys()):
                    if is_undefined(converted_input[key]):
                        _LOGGER.debug(
                            "Final cleanup: removing UNDEFINED value for '%s'", key
                        )
                        del converted_input[key]

                user_input = converted_input
                _LOGGER.debug("Final user_input: %s", user_input)

        # Continue config flow
        result = (
            await shim_manager.get_integration_loader().continue_config_flow(
                domain, flow_id, user_input
            )
        )

        if not result:
            raise HTTPException(
                status_code=400, detail="Failed to process config flow"
            )

        # Handle result
        if result.get("type") == "create_entry":
            entry_data = result.get("data", {})
            entry_options = result.get("options")
            entry = await shim_manager.create_config_entry(
                domain, entry_data, entry_options
            )

            if entry:
                response = HTMLResponse(
                    '<div class="alert alert-success">Configuration successful! Please enable the integration to use it.</div>'
                )
                response.headers["HX-Redirect"] = get_detail_redirect(
                    request, domain
                )
                return response
            else:
                return HTMLResponse(
                    '<div class="alert alert-error">Failed to create entry</div>'
                )

        elif result.get("type") == "form":
            return render_config_step(
                shim_manager, request, domain, result, template_dir
            )

        elif result.get("type") == "menu":
            return render_menu_step(request, domain, result)

        elif result.get("type") == "external":
            from ...stubs.oauth2 import LOCALHOST_REDIRECT_URI
            return render_external_step(
                request, domain, result, redirect_uri=LOCALHOST_REDIRECT_URI
            )

        elif result.get("type") == "external_done":
            next_step_id = result.get("next_step_id", "creation")
            result = (
                await shim_manager.get_integration_loader().continue_config_flow(
                    domain, flow_id, {"next_step": next_step_id}
                )
            )
            if not result:
                return HTMLResponse(
                    '<div class="alert alert-error">Failed to continue OAuth flow</div>'
                )
            if result.get("type") == "create_entry":
                entry_data = result.get("data", {})
                entry_options = result.get("options")
                entry = await shim_manager.create_config_entry(
                    domain, entry_data, entry_options
                )
                if entry:
                    response = HTMLResponse(
                        '<div class="alert alert-success">Configuration successful! Please enable the integration to use it.</div>'
                    )
                    response.headers["HX-Redirect"] = get_detail_redirect(
                        request, domain
                    )
                    return response
                else:
                    return HTMLResponse(
                        '<div class="alert alert-error">Failed to create entry</div>'
                    )
            elif result.get("type") == "form":
                return render_config_step(
                    shim_manager, request, domain, result, template_dir
                )
            elif result.get("type") == "abort":
                return HTMLResponse(
                    f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
                )
            else:
                return render_config_step(
                    shim_manager, request, domain, result, template_dir
                )

        elif result.get("type") == "abort":
            return HTMLResponse(
                f'<div class="alert alert-error">Aborted: {result.get("reason")}</div>'
            )

        else:
            return HTMLResponse(
                f'<div class="alert alert-error">Unknown result: {result.get("type")}</div>'
            )

    # ------------------------------------------------------------------ #
    #  Options flow (reconfigure)
    # ------------------------------------------------------------------ #

    @app.get("/config/{entry_id}/reconfigure", response_class=HTMLResponse)
    async def options_flow_start(request: Request, entry_id: str):
        """Start an options flow (reconfiguration) for an existing config entry."""
        loading_response = check_loading(shim_manager)
        if loading_response:
            return loading_response

        entry = shim_manager.get_hass().config_entries.async_get_entry(
            entry_id
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Config entry not found")

        domain = entry.domain

        result = (
            await shim_manager.get_integration_loader().start_options_flow(
                entry
            )
        )

        if not result:
            return HTMLResponse(
                '<div class="alert alert-warning">'
                "This integration does not support reconfiguration."
                "</div>",
                status_code=400,
            )

        if result.get("type") == "menu":
            return render_menu_step(
                request, domain, result, is_options_flow=True, entry_id=entry_id
            )
        else:
            return render_config_step(
                shim_manager, request, domain, result, template_dir,
                is_options_flow=True, entry_id=entry_id
            )

    @app.post("/config/{entry_id}/reconfigure")
    async def options_flow_submit(request: Request, entry_id: str):
        """Submit an options flow step."""
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
        user_input = dict(form_data)
        _LOGGER.debug("Options flow raw form data for %s: %s", domain, user_input)

        flow_id = user_input.pop("flow_id", None)

        hass = shim_manager.get_hass()
        flow = hass.config_entries._flow_progress.get(flow_id)
        schema = getattr(flow, "_last_form_schema", None) if flow else None

        # Handle menu selection
        if "next_step" in user_input:
            user_input = {"next_step": user_input["next_step"]}
            _LOGGER.debug(
                "Options flow menu selection for %s: flow_id=%s, "
                "selected_option=%s",
                domain,
                flow_id,
                user_input["next_step"],
            )
        else:
            _LOGGER.debug(
                "Options flow submit for %s: flow_id=%s, schema=%s, user_input=%s",
                domain,
                flow_id,
                schema,
                user_input,
            )

        # Convert form values based on schema field types
        if schema and hasattr(schema, "schema"):
            import voluptuous as vol

            for key, value in user_input.items():
                _LOGGER.debug(
                    "Options form input '%s': %r (type: %s, is_undefined: %s)",
                    key,
                    value,
                    type(value).__name__,
                    is_undefined(value),
                )

            schema_dict = schema.schema
            _LOGGER.debug("Options flow schema dict: %s", schema_dict)
            if isinstance(schema_dict, dict):
                converted_input = {}
                for key, value in user_input.items():
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
                        if is_undefined(value):
                            _LOGGER.debug(
                                "Field '%s': value is UNDEFINED, skipping", key
                            )
                            continue

                        converted_value = convert_form_value(
                            value, validator, key
                        )

                        if is_undefined(converted_value):
                            _LOGGER.debug(
                                "Field '%s': converted value is UNDEFINED, skipping",
                                key,
                            )
                            continue

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
                                default_value = field_schema_key.default
                                if callable(default_value):
                                    try:
                                        default_value = default_value()
                                    except Exception:
                                        default_value = None
                                _LOGGER.debug(
                                    "Field '%s': empty string on Optional field "
                                    "with default, using default=%r",
                                    key,
                                    default_value,
                                )
                                converted_input[key] = default_value
                            else:
                                _LOGGER.debug(
                                    "Field '%s': empty string on Optional field "
                                    "without default, skipping key",
                                    key,
                                )
                        else:
                            _LOGGER.debug(
                                "Field '%s': original='%s' (%s), "
                                "validator=%s, converted='%s' (%s)",
                                key,
                                value,
                                type(value).__name__,
                                validator.__class__.__name__,
                                converted_value,
                                type(converted_value).__name__,
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

                # Final pass: remove any UNDEFINED values
                for key in list(converted_input.keys()):
                    if is_undefined(converted_input[key]):
                        _LOGGER.debug(
                            "Final cleanup: removing UNDEFINED value for '%s'", key
                        )
                        del converted_input[key]

                user_input = converted_input
                _LOGGER.debug("Options flow final user_input: %s", user_input)

        # Continue options flow
        result = (
            await shim_manager.get_integration_loader().continue_options_flow(
                entry, flow_id, user_input
            )
        )

        if not result:
            raise HTTPException(
                status_code=400, detail="Failed to process options flow"
            )

        if result.get("type") == "create_entry":
            if entry.state == "loaded":
                await (
                    shim_manager.get_integration_loader().reload_config_entry(
                        entry
                    )
                )

            response = HTMLResponse(
                '<div class="alert alert-success">Configuration updated successfully!</div>'
            )
            response.headers["HX-Redirect"] = get_detail_redirect(
                request, domain
            )
            return response

        elif result.get("type") == "form":
            return render_config_step(
                shim_manager, request, domain, result, template_dir,
                is_options_flow=True, entry_id=entry_id
            )

        elif result.get("type") == "menu":
            return render_menu_step(
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
