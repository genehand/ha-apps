"""Template and response rendering utilities.

Provides functions for rendering Jinja2 templates, config flow form steps,
menu steps, external auth steps, and custom repo list HTML fragments.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from .const import (
    ERROR_TRANSLATIONS,
    HTMX_SRI,
    HTMX_URL,
    PICO_COLORS_URL,
    PICO_CSS_URL,
)
from .schema import is_undefined, parse_schema
from .translations import apply_field_translations, load_integration_translations

_LOGGER = logging.getLogger(__name__)


def render_template(template_dir: Path, template_name: str, **context) -> str:
    """Render a template using Jinja2 environment with inheritance support."""
    # Create environment with file system loader for template inheritance
    env = Environment(loader=FileSystemLoader(str(template_dir)))

    context.setdefault("PICO_CSS_URL", PICO_CSS_URL)
    context.setdefault("PICO_COLORS_URL", PICO_COLORS_URL)
    context.setdefault("HTMX_URL", HTMX_URL)
    context.setdefault("HTMX_SRI", HTMX_SRI)

    template = env.get_template(template_name)
    return template.render(**context)


def check_loading(shim_manager) -> Optional[HTMLResponse]:
    """Check if integrations are still loading and return a 'please wait' response if so.

    Args:
        shim_manager: The shim manager instance

    Returns:
        HTMLResponse with 'please wait' message if loading, None otherwise.
    """
    if shim_manager.is_loading:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            '<span class="spinner" style="width: 14px; height: 14px; margin-right: 8px;"></span>'
            "Integrations are still loading. Please wait a moment and try again."
            "</div>",
            status_code=503,
        )
    return None


def get_detail_redirect(request: Request, domain: str) -> str:
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


def render_config_step(
    shim_manager,
    request: Request,
    domain: str,
    result: dict,
    template_dir: Path,
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
        hass = shim_manager.get_hass()
        flow = hass.config_entries._flow_progress.get(flow_id)
        if flow:
            flow._last_form_schema = schema

    # Load translations for field labels and error messages
    translations = load_integration_translations(
        shim_manager._integration_manager, domain
    )

    # Translate error messages (check integration translations first, then fall back)
    translated_errors = {}
    config_errors = translations.get("config", {}).get("error", {})
    for key, error_key in errors.items():
        if isinstance(error_key, str):
            # First check integration translations, then static translations
            translated_errors[key] = config_errors.get(
                error_key, ERROR_TRANSLATIONS.get(error_key, error_key)
            )
        else:
            translated_errors[key] = str(error_key)

    # Convert voluptuous schema to form fields
    fields = parse_schema(schema)

    # Apply field translations
    if translations:
        apply_field_translations(fields, translations, step_id)

    # Check if this is an HTMX request for partial rendering
    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx and is_options_flow:
        # Render only the form content without full page wrapper for HTMX
        html = render_template(
            template_dir,
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
        html = render_template(
            template_dir,
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


def render_menu_step(
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

    # Determine form action URL (relative for HA ingress compatibility)
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
    # We need a shim_manager to load translations, but for menu steps we load
    # translations with a minimal approach
    import json
    from pathlib import Path

    menu_title = f"{title_prefix} {domain.title()}"
    menu_description = ""

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


def render_external_step(
    request: Request,
    domain: str,
    result: dict,
    is_options_flow: bool = False,
    entry_id: str = None,
    redirect_uri: str = None,
) -> HTMLResponse:
    """Render an OAuth2 external authorization step (copy-paste flow)."""
    url = result.get("url", "")
    description = result.get("description_placeholders", {})
    flow_id = result.get("flow_id")

    # Compute the form action URL (relative for HA ingress compatibility)
    if is_options_flow and entry_id:
        form_action = entry_id
        title_prefix = "Reconfigure"
    else:
        form_action = domain
        title_prefix = "Configure"

    # Build description placeholders text
    desc_text = ""
    if description:
        for key, value in description.items():
            desc_text += f"<p><strong>{key}:</strong> {value}</p>"

    # Redirect URI display block
    redirect_html = ""
    if redirect_uri:
        redirect_html = f"""
        <div style="margin-top: 15px; margin-bottom: 15px; padding: 12px;
                    border: 1px solid var(--pico-form-element-border-color);
                    border-radius: var(--pico-border-radius);
                    background: var(--pico-card-background-color);">
            <p style="margin: 0;">
                <strong>Redirect URI:</strong> <code>{redirect_uri}</code>
            </p>
            <p style="font-size: 0.85rem; margin: 8px 0 0 0;">
                Make sure this redirect URI is configured in your OAuth application settings.
            </p>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title_prefix} {domain.title()}</title>
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
            <h2 style="margin-top: 20px;">{title_prefix} {domain.title()}</h2>
            {desc_text}
            {redirect_html}
            <div class="alert alert-info" role="alert">
                <p><strong>Step 1:</strong> Click the button below to open
                the authorization page in a new tab.</p>
            </div>
            <div style="margin: 20px 0;">
                <a href="{url}" target="_blank" rel="noopener noreferrer"
                   class="btn btn-primary" role="button"
                   style="padding: 15px 30px; font-size: 1.1rem;">
                    Authorize with {domain.title()}
                </a>
            </div>
            <div class="alert alert-warning" role="alert"
                 style="word-break: break-all;">
                <p><strong>Step 2:</strong> After authorizing, the page will
                redirect to <code>{redirect_uri or "localhost"}</code> and
                show an error. <strong>This is expected!</strong></p>
                <p>Copy the <strong>entire URL</strong> from the browser's
                address bar (or just the <code>code</code> parameter).</p>
            </div>
            <div class="alert alert-info" role="alert">
                <p><strong>Step 3:</strong> Paste the URL or code below and
                click <strong>Complete Setup</strong>.</p>
            </div>
            <form hx-post="{form_action}" hx-target="#config-result"
                  hx-swap="innerHTML" style="margin: 20px 0;">
                <input type="hidden" name="flow_id" value="{flow_id}">
                <label for="oauth_callback_url">
                    Authorization callback URL or code
                </label>
                <input type="text" id="oauth_callback_url"
                       name="oauth_callback_url" required
                       placeholder="http://localhost:8080/auth/external/callback?code=...&amp;state=..."
                       style="width: 100%;">
                <button type="submit" class="btn btn-primary"
                        style="margin-top: 15px;">
                    Complete Setup
                </button>
            </form>
            <div id="config-result"></div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


def render_custom_repos_list(
    repos: List[dict],
    success_message: str = None,
    error_message: str = None,
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
                '<span class="pico-color-jade-500" style="font-weight: 600; margin-left: 10px;">\u2713 Installed</span>'
                if is_installed
                else ""
            )

            if is_installed:
                actions_html = (
                    '<span class="pico-color-muted" style="font-size: 12px;">Remove integration first</span>'
                )
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
