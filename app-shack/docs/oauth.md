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
4. **Web UI** (`shim/web/app.py`): Computes and stores the OAuth2 redirect URI per request (supports both HA ingress and direct access), handles credential management, and the OAuth callback flow

## Credential Management UX

- **Credentials page**: `shim/web/templates/credentials.html` shows all domains with stored credentials, supports add/remove operations
- **Config flow**: When an integration needs credentials, the user is redirected to a credential management page or an external OAuth2 authorization URL
- **Ingress support**: Redirect URIs are computed from `x-ingress-path` headers when running through HA ingress
