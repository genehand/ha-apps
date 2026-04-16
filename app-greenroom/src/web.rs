use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use axum::{
    extract::{Form, Query, State},
    http::header::HeaderMap,
    response::Html,
    routing::{get, post},
    Router,
};
use serde::Deserialize;
use tokio::sync::{broadcast, RwLock};
use tracing::{error, info};

use crate::token::{self, AuthCredentials, SPOTIFY_CLIENT_ID};
use crate::{Config, PlaybackState};

/// Get the base path for URLs, respecting X-Ingress-Path header
fn get_base_path(headers: &HeaderMap) -> String {
    headers
        .get("x-ingress-path")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim_end_matches('/').to_string())
        .unwrap_or_default()
}

/// Build a full URL path with ingress prefix
fn build_url(headers: &HeaderMap, path: &str) -> String {
    let base = get_base_path(headers);
    let path = path.trim_start_matches('/');
    if base.is_empty() {
        format!("/{}", path)
    } else {
        format!("{}/{}", base, path)
    }
}

/// In-memory storage for PKCE verifiers during OAuth flow
#[derive(Clone)]
struct OauthFlowState {
    code_verifier: String,
    redirect_uri: String,
    created_at: Instant,
}

/// Shared state for the web server
#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub playback_state: Arc<RwLock<PlaybackState>>,
    pub token_file: std::path::PathBuf,
    oauth_flows: Arc<Mutex<HashMap<String, OauthFlowState>>>,
    token_tx: broadcast::Sender<()>,
}

impl AppState {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        token_file: std::path::PathBuf,
        token_tx: broadcast::Sender<()>,
    ) -> Self {
        Self {
            config,
            playback_state,
            token_file,
            oauth_flows: Arc::new(Mutex::new(HashMap::new())),
            token_tx,
        }
    }

    /// Check if credentials exist (doesn't indicate session status)
    pub async fn has_credentials(&self) -> bool {
        token::has_credentials_file(&self.token_file).await
    }

    /// Load credentials if available
    pub async fn load_credentials(&self) -> Option<AuthCredentials> {
        token::load_credentials(&self.token_file).await
    }

    /// Save credentials to file and notify daemon
    pub async fn save_credentials(&self, credentials: &AuthCredentials) -> anyhow::Result<()> {
        token::save_credentials(&self.token_file, credentials, Some(&self.token_tx)).await
    }

    /// Clear credentials (logout)
    pub async fn clear_credentials(&self) -> anyhow::Result<()> {
        token::clear_credentials(&self.token_file).await
    }
}

/// Query params for OAuth callback
#[derive(Deserialize)]
struct AuthCallback {
    code: String,
    state: String,
}

/// Form data for manual code entry
#[derive(Deserialize)]
struct ManualAuthForm {
    code: String,
    state: String,
}

/// Generate PKCE code verifier
fn generate_code_verifier() -> String {
    use rand::Rng;
    const CHARSET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    const LEN: usize = 128;
    let mut rng = rand::thread_rng();
    (0..LEN)
        .map(|_| {
            let idx = rng.gen_range(0..CHARSET.len());
            CHARSET[idx] as char
        })
        .collect()
}

/// Extract OAuth code from user input (either raw code or full URL).
/// Handles URLs like: http://127.0.0.1:5588/login?code=AQ...&state=...
fn extract_oauth_code(input: &str) -> String {
    let input = input.trim();

    // If it looks like a URL (contains :// or starts with http), try to parse it
    if input.contains("://") || input.starts_with("http") {
        // Try to find code= in the URL
        if let Some(code_start) = input.find("code=") {
            let after_code = &input[code_start + 5..];
            // Extract until & or end of string
            if let Some(amp_pos) = after_code.find('&') {
                return after_code[..amp_pos].to_string();
            } else {
                return after_code.to_string();
            }
        }
    }

    // Not a URL or no code found, return as-is (assume it's already just the code)
    input.to_string()
}

/// Generate PKCE code challenge from verifier
fn generate_code_challenge(verifier: &str) -> String {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let result = hasher.finalize();
    URL_SAFE_NO_PAD.encode(result)
}

/// Get redirect URI (ingress-aware)
fn get_redirect_uri(ingress_path: Option<&str>) -> String {
    if let Some(path) = ingress_path {
        // Running under HA ingress
        format!("{}/auth/callback", path.trim_end_matches('/'))
    } else {
        // Direct access - not supported in this mode
        panic!("Direct access not supported - must run under HA ingress")
    }
}

/// Build the router
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index_handler))
        .route("/auth/login", get(auth_login_handler))
        .route("/auth/callback", get(auth_callback_handler))
        .route("/auth/manual", post(auth_manual_handler))
        .route("/auth/disconnect", post(auth_disconnect_handler))
        .route("/api/status", get(status_api_handler))
        .with_state(state)
}

/// Check if we have actual connection
/// Prioritize the connection flag - if WebSocket is active, we're connected
/// even if token shows as expired (librespot sessions stay alive via WebSocket)
fn is_connected(_has_token: bool, playback: &PlaybackState) -> bool {
    // Use the explicit connection flag - this is set to true when WebSocket is active
    // and false when connection closes (regardless of token expiry)
    playback.is_spotify_connected
}

/// Render just the status content (inner HTML for the content div)
fn render_status_content(
    has_credentials: bool,
    _credentials: Option<AuthCredentials>,
    playback: PlaybackState,
    _config: &Config,
    login_url: &str,
    disconnect_url: &str,
) -> String {
    let connected = is_connected(has_credentials, &playback);

    // Check if connection is disabled via MQTT switch
    let is_disabled = !playback.connection_enabled;

    if is_disabled {
        // Connection is disabled via MQTT switch
        let disconnect_form = format!(
            r#"<form action="{}" method="post" target="_blank" rel="noopener noreferrer">
                <button type="submit" class="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
                    Disconnect
                </button>
            </form>"#,
            disconnect_url
        );
        format!(
            r#"<div class="space-y-4">
                <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full bg-gray-500"></div>
                    <p class="font-medium">Connection Disabled</p>
                </div>
                <p class="text-gray-400">The Spotify connection is currently disabled. Enable the "{} Active" switch in Home Assistant to reconnect.</p>
                {}
            </div>"#,
            _config.device_name, disconnect_form
        )
    } else if connected {
        let account_status =
            format!("<p class=\"text-sm text-gray-400\">Session active via WebSocket</p>");

        let playback_html = if playback.track.is_some() {
            format!(
                "<div class=\"mt-4 p-4 bg-gray-700 rounded\">\
                    <h3 class=\"font-semibold mb-2\">Current Playback</h3>\
                    <p><strong>{}</strong> by {}</p>\
                    <p class=\"text-sm text-gray-400\">Status: {}</p>\
                </div>",
                playback.track.as_ref().unwrap_or(&"Unknown".to_string()),
                playback.artist.as_ref().unwrap_or(&"Unknown".to_string()),
                if playback.is_playing {
                    "Playing"
                } else if playback.is_idle {
                    "Idle"
                } else {
                    "Paused"
                }
            )
        } else {
            // No track metadata - show idle status
            format!(
                r#"<div class="mt-4 p-4 bg-gray-700 rounded">
                    <p class="text-gray-400">{}</p>
                </div>"#,
                if playback.is_idle {
                    "No active playback"
                } else {
                    "Waiting for playback..."
                }
            )
        };

        let disconnect_form = format!(
            r#"<form action="{}" method="post" target="_blank" rel="noopener noreferrer">
                <button type="submit" class="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
                    Disconnect
                </button>
            </form>"#,
            disconnect_url
        );
        let mut connected_html =
            String::from("<div class=\"space-y-4\"><div class=\"flex items-center gap-2\">");
        connected_html.push_str("<div class=\"w-3 h-3 rounded-full bg-green-500\"></div>");
        connected_html.push_str("<p class=\"font-medium\">Connected to Spotify</p></div>");
        connected_html.push_str(&account_status);
        connected_html.push_str(&playback_html);
        connected_html.push_str(&disconnect_form);
        connected_html.push_str("</div>");
        connected_html
    } else if has_credentials {
        // Have token but connection lost - show reconnecting status
        let disconnect_form = format!(
            r#"<form action="{}" method="post" target="_blank" rel="noopener noreferrer">
                <button type="submit" class="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
                    Disconnect
                </button>
            </form>"#,
            disconnect_url
        );
        format!(
            r#"<div class="space-y-4">
                <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full bg-yellow-500 animate-pulse"></div>
                    <p class="font-medium">Reconnecting to Spotify...</p>
                </div>
                <p class="text-gray-400">Connection lost. The daemon will try to reconnect automatically.</p>
                {}
            </div>"#,
            disconnect_form
        )
    } else {
        format!(
            r#"<div class="space-y-4">
                <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full bg-red-500"></div>
                    <p class="font-medium">Not Connected</p>
                </div>
                <p class="text-gray-400">Connect your Spotify account to start monitoring playback.</p>
                <button onclick="window.open('{}', '_blank', 'noopener,noreferrer'); return false;" class="inline-block px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 cursor-pointer">
                    Connect Spotify
                </button>
                <p class="text-xs text-gray-500 mt-2">Opens in new window - close it after connecting</p>
            </div>"#,
            login_url
        )
    }
}

/// Main index page - status and auth UI
async fn index_handler(State(state): State<AppState>, headers: HeaderMap) -> Html<String> {
    let has_credentials = state.has_credentials().await;
    let credentials = if has_credentials {
        state.load_credentials().await
    } else {
        None
    };

    // Get current playback info if available
    let playback = state.playback_state.read().await.clone();

    let login_url = build_url(&headers, "auth/login");
    let disconnect_url = build_url(&headers, "auth/disconnect");
    let status_html = render_status_content(
        has_credentials,
        credentials,
        playback,
        &state.config,
        &login_url,
        &disconnect_url,
    );

    Html(format!(
        r#"<!DOCTYPE html>
<html class="dark">
<head>
    <title>Greenroom - Spotify Connect</title>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body class="bg-gray-900 min-h-screen text-gray-100">
    <div class="max-w-md mx-auto p-6">
        <div class="bg-gray-800 rounded-lg shadow p-6">
            <h1 class="text-2xl font-bold mb-2">Greenroom</h1>
            <p class="text-gray-400 mb-6">Spotify Connect Monitor</p>
            <div id="content" hx-get="api/status" hx-trigger="every 5s" hx-swap="outerHTML">
                {}
            </div>
        </div>
        <div class="mt-4 text-center text-sm text-gray-500">
            <p>Device: {}</p>
        </div>
    </div>
</body>
</html>"#,
        status_html, state.config.device_name
    ))
}

/// API endpoint for status (used by HTMX polling)
async fn status_api_handler(State(state): State<AppState>, headers: HeaderMap) -> Html<String> {
    // Returns just the inner content for HTMX to swap (not the full page)
    let has_credentials = state.has_credentials().await;
    let credentials = if has_credentials {
        state.load_credentials().await
    } else {
        None
    };
    let playback = state.playback_state.read().await.clone();

    let login_url = build_url(&headers, "auth/login");
    let disconnect_url = build_url(&headers, "auth/disconnect");
    Html(render_status_content(
        has_credentials,
        credentials,
        playback,
        &state.config,
        &login_url,
        &disconnect_url,
    ))
}

/// Initiate OAuth login flow - shows instructions page
async fn auth_login_handler(State(state): State<AppState>, headers: HeaderMap) -> Html<String> {
    // Generate PKCE
    let code_verifier = generate_code_verifier();
    let code_challenge = generate_code_challenge(&code_verifier);
    let state_param = generate_code_verifier()[..32].to_string(); // Random state

    // Use localhost redirect (must match librespot's registered redirect)
    let redirect_uri = "http://127.0.0.1:5588/login".to_string();

    // Store verifier and redirect_uri for callback
    {
        let mut flows = state.oauth_flows.lock().unwrap();
        // Clean up old flows (older than 10 minutes)
        flows.retain(|_, v| v.created_at.elapsed() < Duration::from_secs(600));
        flows.insert(
            state_param.clone(),
            OauthFlowState {
                code_verifier: code_verifier.clone(),
                redirect_uri: redirect_uri.clone(),
                created_at: Instant::now(),
            },
        );
    }

    // Build auth URL
    let auth_url = format!(
        "https://accounts.spotify.com/authorize?client_id={}&response_type=code&redirect_uri={}&code_challenge={}&code_challenge_method=S256&scope=streaming&state={}",
        SPOTIFY_CLIENT_ID,
        urlencoding::encode(&redirect_uri),
        code_challenge,
        state_param
    );

    info!("Initiating OAuth flow, state={}", state_param);

    // Build URLs for the page
    let manual_url = build_url(&headers, "auth/manual");
    let home_url = build_url(&headers, "");

    // Show instructions page with manual code entry form
    Html(format!(
        r#"<!DOCTYPE html>
<html class="dark">
<head>
    <title>Connect Spotify - Greenroom</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body class="bg-gray-900 min-h-screen text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-gray-800 rounded-lg shadow p-6 mb-4">
            <h1 class="text-xl font-bold mb-4">Connect Spotify Account</h1>
            
            <div class="space-y-4">
                <div class="bg-blue-900/50 border border-blue-700 rounded p-3">
                    <p class="text-sm text-blue-200">
                        <strong>Step 1:</strong> Click the button below to open Spotify authorization in a new tab.
                    </p>
                </div>
                
                <a href="{}" target="_blank" rel="noopener noreferrer" 
                   class="inline-block w-full text-center px-4 py-3 bg-green-500 text-white rounded hover:bg-green-600 font-medium">
                    Open Spotify Authorization
                </a>
                
                <div class="bg-yellow-900/50 border border-yellow-700 rounded p-3">
                    <p class="text-sm text-yellow-200">
                        <strong>Step 2:</strong> After logging in, the page will redirect to <code class="bg-yellow-800 px-1 rounded">127.0.0.1:5588</code> and show an error. 
                        This is expected! Copy the <strong>entire URL</strong> from the address bar (or just the code if you prefer).
                    </p>
                </div>
                
                <div class="bg-gray-700 rounded p-3">
                    <p class="text-sm text-gray-300 mb-2">
                        <strong>Step 3:</strong> Paste the full URL or just the code here:
                    </p>
                    <form action="{}" method="post" class="space-y-3">
                        <input type="hidden" name="state" value="{}">
                        <input type="text" name="code" placeholder="http://127.0.0.1:5588/login?code=AQ... " required
                               class="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded text-white placeholder-gray-400 focus:outline-none focus:border-green-500">
                        <button type="submit" class="w-full px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
                            Complete Connection
                        </button>
                    </form>
                </div>
            </div>
        </div>
        
        <p class="text-center text-sm text-gray-500">
            <a href="{}" class="text-blue-400 hover:underline">&larr; Back to Greenroom</a>
        </p>
    </div>
</body>
</html>"#,
        auth_url, manual_url, state_param, home_url
    ))
}

/// Handle OAuth callback from Spotify
async fn auth_callback_handler(
    State(state): State<AppState>,
    Query(params): Query<AuthCallback>,
) -> Html<String> {
    let code = params.code;
    let state_param = params.state;

    // Get the stored verifier and redirect_uri
    let (code_verifier, redirect_uri) = {
        let mut flows = state.oauth_flows.lock().unwrap();
        flows
            .remove(&state_param)
            .map(|f| (f.code_verifier, f.redirect_uri))
            .unzip()
    };
    let code_verifier = match code_verifier {
        Some(v) => v,
        None => {
            error!("No matching OAuth flow found for state parameter");
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Session expired.</strong>
            <p class="text-red-300 mt-2">Please close this window and try again.</p>
        </div>
    </div>
</body>
</html>"#
            ));
        }
    };

    let redirect_uri = redirect_uri.unwrap_or_else(|| {
        // Fallback - shouldn't happen if flow state is properly stored
        let ingress_path = std::env::var("INGRESS_PATH").ok();
        get_redirect_uri(ingress_path.as_deref())
    });

    // Exchange code for token
    let token_url = "https://accounts.spotify.com/api/token";
    let params = [
        ("grant_type", "authorization_code"),
        ("code", &code),
        ("redirect_uri", &redirect_uri),
        ("client_id", SPOTIFY_CLIENT_ID),
        ("code_verifier", &code_verifier),
    ];

    let client = reqwest::Client::new();
    let response = match client.post(token_url).form(&params).send().await {
        Ok(r) => r,
        Err(e) => {
            error!("Token exchange request failed: {}", e);
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Failed to contact Spotify.</strong>
            <p class="text-red-300 mt-2">Please close this window and try again.</p>
        </div>
    </div>
</body>
</html>"#
            ));
        }
    };

    if !response.status().is_success() {
        let error_text = response.text().await.unwrap_or_default();
        error!("Token exchange failed: {}", error_text);
        return Html(format!(
            r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Spotify authorization failed.</strong>
            <p class="text-red-300 mt-2 text-sm">{}</p>
        </div>
    </div>
</body>
</html>"#,
            error_text
        ));
    }

    #[derive(Deserialize)]
    struct TokenResponse {
        access_token: String,
        refresh_token: String,
        expires_in: u64,
        scope: String,
    }

    let token_response: TokenResponse = match response.json().await {
        Ok(t) => t,
        Err(e) => {
            error!("Failed to parse token response: {}", e);
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Invalid response from Spotify.</strong>
        </div>
    </div>
</body>
</html>"#
            ));
        }
    };

    // Calculate expiration
    let expires_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + token_response.expires_in;

    // Save credentials
    let credentials = AuthCredentials {
        access_token: token_response.access_token,
        refresh_token: token_response.refresh_token,
        expires_at,
        scopes: token_response
            .scope
            .split(' ')
            .map(|s| s.to_string())
            .collect(),
    };

    if let Err(e) = state.save_credentials(&credentials).await {
        error!("Failed to save credentials: {}", e);
        return Html(format!(
            r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Failed to save credentials.</strong>
            <p class="text-sm text-gray-400">Please try connecting again.</p>
        </div>
    </div>
</body>
</html>"#,
        ));
    }

    info!("OAuth authentication successful!");

    // Return success HTML
    Html(format!(
        r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-green-900/50 border border-green-700 rounded p-4">
            <strong class="text-green-200">Connected to Spotify!</strong>
            <p class="text-green-300 mt-2">You can close this window.</p>
        </div>
        <script>setTimeout(() => window.close(), 2000);</script>
    </div>
</body>
</html>"#
    ))
}

/// Handle manual code entry from user
async fn auth_manual_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Form(form): Form<ManualAuthForm>,
) -> Html<String> {
    let login_url = build_url(&headers, "auth/login");
    let home_url = build_url(&headers, "");
    // Extract code from user input (handles both full URL and raw code)
    let code = extract_oauth_code(&form.code);
    let state_param = form.state;

    // Get the stored verifier and redirect_uri
    let (code_verifier, redirect_uri) = {
        let mut flows = state.oauth_flows.lock().unwrap();
        flows
            .remove(&state_param)
            .map(|f| (f.code_verifier, f.redirect_uri))
            .unzip()
    };
    let code_verifier = match code_verifier {
        Some(v) => v,
        None => {
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Session expired.</strong>
            <p class="text-red-300 mt-2">Please go back and click "Connect Spotify" again.</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">&larr; Back to Greenroom</a></p>
    </div>
</body>
</html>"#,
                home_url
            ));
        }
    };

    let redirect_uri = redirect_uri.unwrap_or_else(|| "http://127.0.0.1:5588/login".to_string());

    // Exchange code for token
    let token_url = "https://accounts.spotify.com/api/token";
    let params = [
        ("grant_type", "authorization_code"),
        ("code", &code),
        ("redirect_uri", &redirect_uri),
        ("client_id", SPOTIFY_CLIENT_ID),
        ("code_verifier", &code_verifier),
    ];

    let client = reqwest::Client::new();
    let response = match client.post(token_url).form(&params).send().await {
        Ok(r) => r,
        Err(e) => {
            error!("Token exchange request failed: {}", e);
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Failed to contact Spotify.</strong>
            <p class="text-red-300 mt-2">{}</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">Try Again</a></p>
    </div>
</body>
</html>"#,
                e, login_url
            ));
        }
    };

    if !response.status().is_success() {
        let error_text = response.text().await.unwrap_or_default();
        error!("Token exchange failed: {}", error_text);
        return Html(format!(
            r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Spotify authorization failed.</strong>
            <p class="text-red-300 mt-2 text-sm">{}</p>
            <p class="text-red-300 mt-2">Make sure you pasted the full URL from the address bar or the code value.</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">Try Again</a></p>
    </div>
</body>
</html>"#,
            error_text, login_url
        ));
    }

    #[derive(Deserialize)]
    struct TokenResponse {
        access_token: String,
        refresh_token: String,
        expires_in: u64,
        scope: String,
    }

    let token_response: TokenResponse = match response.json().await {
        Ok(t) => t,
        Err(e) => {
            error!("Failed to parse token response: {}", e);
            return Html(format!(
                r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Invalid response from Spotify.</strong>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">Try Again</a></p>
    </div>
</body>
</html>"#,
                login_url
            ));
        }
    };

    // Calculate expiration
    let expires_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + token_response.expires_in;

    // Save credentials
    let credentials = AuthCredentials {
        access_token: token_response.access_token,
        refresh_token: token_response.refresh_token,
        expires_at,
        scopes: token_response
            .scope
            .split(' ')
            .map(|s| s.to_string())
            .collect(),
    };

    if let Err(e) = state.save_credentials(&credentials).await {
        error!("Failed to save credentials: {}", e);
        return Html(format!(
            r#"<!DOCTYPE html>
<html class="dark">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-red-900/50 border border-red-700 rounded p-4">
            <strong class="text-red-200">Failed to save credentials.</strong>
            <p class="text-red-300 mt-2">{}</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">&larr; Back to Greenroom</a></p>
    </div>
</body>
</html>"#,
            e, home_url
        ));
    }

    info!("Successfully authenticated with Spotify via manual code entry");

    // Show success with link back to main page
    Html(format!(
        r#"<!DOCTYPE html>
<html class="dark">
<head>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta http-equiv="refresh" content="3;url={}">
</head>
<body class="bg-gray-900 text-gray-100 p-6">
    <div class="max-w-md mx-auto">
        <div class="bg-green-900/50 border border-green-700 rounded p-4">
            <strong class="text-green-200">Connected to Spotify!</strong>
            <p class="text-green-300 mt-2">Redirecting back to Greenroom...</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">&larr; Back to Greenroom</a></p>
    </div>
</body>
</html>"#,
        home_url, home_url
    ))
}

/// Disconnect / clear credentials
async fn auth_disconnect_handler(State(state): State<AppState>) -> Html<String> {
    if let Err(e) = state.clear_credentials().await {
        error!("Failed to clear credentials: {}", e);
    }

    info!("User disconnected Spotify account");

    // Close window after disconnect
    Html(format!(
        r#"<div class="max-w-md mx-auto p-6">
            <div class="bg-blue-100 border border-blue-400 text-blue-700 px-4 py-3 rounded mb-4">
                Disconnected from Spotify.
            </div>
            <p class="text-sm text-gray-600">You can close this window.</p>
            <script>setTimeout(() => window.close(), 2000);</script>
        </div>"#
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::path::PathBuf;
    use tempfile::NamedTempFile;

    fn create_test_state(token_file: std::path::PathBuf) -> AppState {
        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };

        let (token_tx, _) = broadcast::channel(1);

        AppState::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            token_file,
            token_tx,
        )
    }

    fn create_test_credentials(expires_at: u64) -> AuthCredentials {
        AuthCredentials {
            access_token: "test_access_token".to_string(),
            refresh_token: "test_refresh_token".to_string(),
            expires_at,
            scopes: vec!["streaming".to_string()],
        }
    }

    #[tokio::test]
    async fn test_has_credentials_with_file() {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let credentials = create_test_credentials(now + 3600);
        let json = serde_json::to_string(&credentials).unwrap();

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();

        let state = create_test_state(temp_file.path().to_path_buf());
        assert!(state.has_credentials().await);
    }

    #[tokio::test]
    async fn test_has_credentials_with_missing_file() {
        let state = create_test_state(PathBuf::from("/nonexistent/path/credentials.json"));
        assert!(!state.has_credentials().await);
    }

    #[tokio::test]
    async fn test_save_and_load_credentials() {
        let temp_dir = tempfile::tempdir().unwrap();
        let creds_path = temp_dir.path().join("credentials.json");

        let state = create_test_state(creds_path.clone());

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let credentials = create_test_credentials(now + 3600);

        // Save credentials
        state.save_credentials(&credentials).await.unwrap();

        // Load and verify
        let loaded = state.load_credentials().await;
        assert!(loaded.is_some());
        let loaded = loaded.unwrap();
        assert_eq!(loaded.access_token, credentials.access_token);
        assert_eq!(loaded.refresh_token, credentials.refresh_token);
        assert_eq!(loaded.expires_at, credentials.expires_at);
    }

    #[tokio::test]
    async fn test_clear_credentials() {
        let temp_dir = tempfile::tempdir().unwrap();
        let creds_path = temp_dir.path().join("credentials.json");

        let state = create_test_state(creds_path.clone());

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let credentials = create_test_credentials(now + 3600);

        // Save credentials
        state.save_credentials(&credentials).await.unwrap();
        assert!(creds_path.exists());
        assert!(state.has_credentials().await);

        // Clear credentials
        state.clear_credentials().await.unwrap();
        assert!(!creds_path.exists());
        assert!(!state.has_credentials().await);
    }

    #[tokio::test]
    async fn test_clear_credentials_when_no_file() {
        let state = create_test_state(PathBuf::from("/nonexistent/path/credentials.json"));
        // Should not error when clearing non-existent file
        state.clear_credentials().await.unwrap();
    }

    /// Test that saving credentials sends a notification to the daemon
    #[tokio::test]
    async fn test_save_credentials_sends_notification() {
        let temp_dir = tempfile::tempdir().unwrap();
        let creds_path = temp_dir.path().join("credentials.json");

        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };

        let (token_tx, mut token_rx) = broadcast::channel(1);

        let state = AppState::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            creds_path.clone(),
            token_tx,
        );

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let credentials = create_test_credentials(now + 3600);

        // Save credentials should trigger notification
        state.save_credentials(&credentials).await.unwrap();

        // Verify notification was sent
        let result = tokio::time::timeout(Duration::from_secs(1), token_rx.recv()).await;

        assert!(
            result.is_ok(),
            "Should receive notification within 1 second"
        );
        assert!(result.unwrap().is_ok(), "Notification should be Ok");
    }

    /// Test that credentials notification is NOT sent when notify_tx is None
    #[tokio::test]
    async fn test_save_credentials_without_notification() {
        use crate::token;

        let temp_dir = tempfile::tempdir().unwrap();
        let creds_path = temp_dir.path().join("credentials.json");

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let credentials = create_test_credentials(now + 3600);

        // Save with None notification sender
        token::save_credentials(&creds_path, &credentials, None)
            .await
            .unwrap();

        // Credentials should be saved
        assert!(creds_path.exists());
        let loaded: AuthCredentials =
            serde_json::from_str(&tokio::fs::read_to_string(&creds_path).await.unwrap()).unwrap();
        assert_eq!(loaded.access_token, credentials.access_token);
    }

    // Tests for is_connected helper

    #[test]
    fn test_is_connected_no_credentials() {
        let playback = PlaybackState::default();
        assert!(!super::is_connected(false, &playback));
    }

    #[test]
    fn test_is_connected_with_flag_true() {
        // Connection flag takes precedence over track content
        let playback = PlaybackState {
            track: Some("Not Connected".to_string()), // Even with this text
            artist: Some("Connection lost...".to_string()),
            is_spotify_connected: true, // Flag says we're connected
            ..Default::default()
        };
        assert!(super::is_connected(true, &playback));
    }

    #[test]
    fn test_is_connected_with_flag_false() {
        // Connection flag takes precedence over track content
        let playback = PlaybackState {
            track: Some("Song Name".to_string()), // Even with real track data
            artist: Some("Artist Name".to_string()),
            is_spotify_connected: false, // Flag says we're disconnected
            ..Default::default()
        };
        assert!(!super::is_connected(true, &playback));
    }

    #[test]
    fn test_is_connected_default_flag_false() {
        // Default state has flag false
        let playback = PlaybackState::default();
        assert!(!super::is_connected(true, &playback));
    }

    // Tests for disabled state

    #[test]
    fn test_render_status_content_disabled() {
        let playback = PlaybackState {
            connection_enabled: false,
            is_spotify_connected: false,
            ..Default::default()
        };
        let html = super::render_status_content(
            true, // has credentials
            None,
            playback,
            &Config {
                spotify_username: "".to_string(),
                device_name: "Test".to_string(),
                mqtt_host: "localhost".to_string(),
                mqtt_port: 1883,
                mqtt_username: None,
                mqtt_password: None,
                mqtt_device_id: "test".to_string(),
            },
            "/auth/login",
            "/auth/disconnect",
        );
        // Should show disabled message with device name
        assert!(html.contains("Connection Disabled"));
        assert!(html.contains("Test Active")); // Uses device_name from config
        assert!(html.contains("gray-500")); // Disabled indicator color
    }

    #[test]
    fn test_render_status_content_disabled_takes_precedence() {
        // Even if "connected" in some way, disabled state should take precedence
        let playback = PlaybackState {
            connection_enabled: false,
            is_spotify_connected: true, // Would normally show as connected
            track: Some("Some Song".to_string()),
            artist: Some("Some Artist".to_string()),
            ..Default::default()
        };
        let html = super::render_status_content(
            true,
            None,
            playback,
            &Config {
                spotify_username: "".to_string(),
                device_name: "Test".to_string(),
                mqtt_host: "localhost".to_string(),
                mqtt_port: 1883,
                mqtt_username: None,
                mqtt_password: None,
                mqtt_device_id: "test".to_string(),
            },
            "/auth/login",
            "/auth/disconnect",
        );
        // Should show disabled, not connected
        assert!(html.contains("Connection Disabled"));
        assert!(!html.contains("Connected to Spotify"));
    }

    // Tests for extract_oauth_code

    #[test]
    fn test_extract_oauth_code_from_full_url() {
        let url = "http://127.0.0.1:5588/login?code=AQABC123XYZ&state=some_state_value";
        assert_eq!(super::extract_oauth_code(url), "AQABC123XYZ");
    }

    #[test]
    fn test_extract_oauth_code_from_https_url() {
        let url = "https://example.com/callback?code=MYCODE456&state=abc123&other=value";
        assert_eq!(super::extract_oauth_code(url), "MYCODE456");
    }

    #[test]
    fn test_extract_oauth_code_raw_code() {
        // When user just pastes the code directly
        let code = "AQABC123XYZ";
        assert_eq!(super::extract_oauth_code(code), "AQABC123XYZ");
    }

    #[test]
    fn test_extract_oauth_code_with_whitespace() {
        // Should trim whitespace
        let input = "  http://127.0.0.1:5588/login?code=AQABC123&state=test  ";
        assert_eq!(super::extract_oauth_code(input), "AQABC123");
    }

    #[test]
    fn test_extract_oauth_code_no_code_param() {
        // URL without code param should return full string
        let url = "http://127.0.0.1:5588/login?state=abc123";
        assert_eq!(super::extract_oauth_code(url), url);
    }

    #[test]
    fn test_extract_oauth_code_code_at_end() {
        // Code at end of URL without trailing params
        let url = "http://127.0.0.1:5588/login?code=ENDCODE789";
        assert_eq!(super::extract_oauth_code(url), "ENDCODE789");
    }
}
