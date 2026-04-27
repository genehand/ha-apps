# OAuth2 and Application Credentials

The shim provides real OAuth2 authorization-code flow support with PKCE for integrations running outside of Home Assistant core.

## Architecture

OAuth2 support is split across two modules in `shim/stubs/`:

- **`shim/stubs/oauth2.py`** — The core OAuth2 implementation, adapted from `homeassistant.helpers.config_entry_oauth2_flow`. Provides:
  - `AbstractOAuth2Implementation` — base class for OAuth2 provider implementations
  - `LocalOAuth2Implementation` / `LocalOAuth2ImplementationWithPkce` — client-credential-based implementations
  - `OAuth2Session` — auto-refreshing token session for API calls
  - `AbstractOAuth2FlowHandler` — config flow handler for OAuth2 authorization
  - `async_oauth2_request` — low-level authenticated HTTP request helper

- **`shim/stubs/application_credentials.py`** — The HA `application_credentials` component stub. Provides:
  - `ClientCredential` / `AuthorizationServer` — dataclasses for OAuth2 client config
  - `ApplicationCredentialsStorage` — JSON-file-backed credential persistence
  - `AuthImplementation` — bridges stored credentials into the OAuth2 flow
  - `async_import_client_credential` — imports credentials from `configuration.yaml`

## How It's Wired

Both modules are wired through existing stub infrastructure — no separate `create_X_stubs()` calls:

1. **oauth2** is registered in `shim/stubs/helpers.py` as `homeassistant.helpers.config_entry_oauth2_flow`
2. **application_credentials** is registered in `shim/stubs/components.py` as `homeassistant.components.application_credentials`
3. **Startup** (`shim/manager.py`): Calls `setup_application_credentials(hass, shim_dir)` to initialize credential storage and registers the `async_add_implementation_provider` callback
4. **Web UI** (`shim/web/routes/config_flows.py` / `shim/web/routes/auth.py`): Handles the OAuth config flow and callback

## Copy-Paste OAuth Flow

Because the app runs behind Home Assistant ingress, OAuth providers cannot redirect back to the app directly. Instead, the shim uses a **copy-paste flow**:

1. The redirect URI is always `http://localhost:8080/auth/external/callback` (`LOCALHOST_REDIRECT_URI` in `shim/stubs/oauth2.py`). Register this exact URI in your OAuth application settings.
2. During config flow, the user clicks **Authorize with {Domain}** to open the provider's authorization page in a new tab.
3. After authorizing, the provider redirects to `localhost:8080`, which fails (this is expected — the browser is not running on the server).
4. The user **copies the full URL** from the browser's address bar (or just the `code` parameter) and pastes it into the form field in the Shack UI.
5. `shim/web/routes/config_flows.py` parses the pasted URL with `_extract_oauth_params()`, extracts `code` and `state`, and resumes the config flow.

### Why localhost?

- `async_get_redirect_uri()` ignores `hass.config.external_url` and any stored `_oauth2_redirect_uri`, returning the hardcoded localhost URL instead.
- The user pastes the callback URL back into the UI, so the app receives the authorization code without needing a publicly reachable redirect endpoint.

### Code paths

- **`shim/stubs/oauth2.py`**: `async_get_redirect_uri()` → always returns `LOCALHOST_REDIRECT_URI`
- **`shim/web/renderers.py`**: `render_external_step()` → shows the redirect URI, authorization button, and the paste-back form
- **`shim/web/routes/config_flows.py`**: `_extract_oauth_params()` → parses `code`, `state`, and `error` from a full URL or query string
- **`shim/web/routes/auth.py`**: `oauth_callback()` → if reached directly, shows a simple instruction page telling the user to copy the URL

## Credential Management UX

- **Credentials page**: `shim/web/templates/credentials.html` shows all domains with stored credentials, supports add/remove operations
- **Config flow**: When an integration needs credentials, the user is redirected to a credential management page or an external OAuth2 authorization URL
