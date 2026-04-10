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
use tokio::sync::{RwLock, broadcast};
use tracing::{error, info};

use crate::{Config, PlaybackState};
use crate::token::{self, Token};

/// Librespot's OAuth client ID (KEYMASTER_CLIENT_ID)
const LIBRESPOT_CLIENT_ID: &str = "65b708073fc0480ea92a077233ca87bd";

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

    /// Check if a valid token exists
    pub async fn has_valid_token(&self) -> bool {
        match token::load_token(&self.token_file).await {
            Some(token) => token.is_valid(),
            None => false,
        }
    }

    /// Load token if available
    pub async fn load_token(&self) -> Option<Token> {
        token::load_token(&self.token_file).await
    }

    /// Save token to file and notify daemon
    pub async fn save_token(&self, token: &Token) -> anyhow::Result<()> {
        token::save_token(&self.token_file, token, Some(&self.token_tx)).await
    }

    /// Clear token (logout)
    pub async fn clear_token(&self) -> anyhow::Result<()> {
        token::clear_token(&self.token_file).await
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

/// Render just the status content (inner HTML for the content div)
fn render_status_content(
    has_token: bool,
    token_info: Option<Token>,
    playback: PlaybackState,
    _config: &Config,
    login_url: &str,
    disconnect_url: &str,
) -> String {
    if has_token {
        let account_status = if let Some(token) = token_info {
            format!(
                "<p class=\"text-sm text-gray-400\">Token expires at: {}</p>",
                chrono::DateTime::from_timestamp(token.expires_at as i64, 0)
                    .map(|d| d.format("%Y-%m-%d %H:%M:%S UTC").to_string())
                    .unwrap_or_else(|| "Unknown".to_string())
            )
        } else {
            String::new()
        };

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
            String::new()
        };

        let disconnect_form = format!(
            r#"<form action="{}" method="post" target="_blank" rel="noopener noreferrer">
                <button type="submit" class="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
                    Disconnect
                </button>
            </form>"#,
            disconnect_url
        );
        let mut connected_html = String::from(
            "<div class=\"space-y-4\"><div class=\"flex items-center gap-2\">",
        );
        connected_html.push_str("<div class=\"w-3 h-3 rounded-full bg-green-500\"></div>");
        connected_html.push_str("<p class=\"font-medium\">Connected to Spotify</p></div>");
        connected_html.push_str(&account_status);
        connected_html.push_str(&playback_html);
        connected_html.push_str(&disconnect_form);
        connected_html.push_str("</div>");
        connected_html
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
async fn index_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Html<String> {
    let has_token = state.has_valid_token().await;
    let token_info = if has_token {
        state.load_token().await
    } else {
        None
    };

    // Get current playback info if available
    let playback = state.playback_state.read().await.clone();

    let login_url = build_url(&headers, "auth/login");
    let disconnect_url = build_url(&headers, "auth/disconnect");
    let status_html = render_status_content(has_token, token_info, playback, &state.config, &login_url, &disconnect_url);

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
        status_html,
        state.config.device_name
    ))
}

/// API endpoint for status (used by HTMX polling)
async fn status_api_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Html<String> {
    // Returns just the inner content for HTMX to swap (not the full page)
    let has_token = state.has_valid_token().await;
    let token_info = if has_token {
        state.load_token().await
    } else {
        None
    };
    let playback = state.playback_state.read().await.clone();
    let login_url = build_url(&headers, "auth/login");
    let disconnect_url = build_url(&headers, "auth/disconnect");
    Html(render_status_content(has_token, token_info, playback, &state.config, &login_url, &disconnect_url))
}

/// Initiate OAuth login flow - shows instructions page
async fn auth_login_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Html<String> {
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
        LIBRESPOT_CLIENT_ID,
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
                        This is expected! Copy just the <strong>code value</strong> from the URL.
                    </p>
                </div>
                
                <div class="bg-gray-700 rounded p-3">
                    <p class="text-sm text-gray-300 mb-2">
                        <strong>Step 3:</strong> Paste just the code value here:
                    </p>
                    <form action="{}" method="post" class="space-y-3">
                        <input type="hidden" name="state" value="{}">
                        <input type="text" name="code" placeholder="AQ... (just the code value, no code= or &state=)" required
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
        auth_url,
        manual_url,
        state_param,
        home_url
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
        flows.remove(&state_param).map(|f| (f.code_verifier, f.redirect_uri)).unzip()
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
        ("client_id", LIBRESPOT_CLIENT_ID),
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

    // Save token
    let token = Token {
        access_token: token_response.access_token,
        refresh_token: token_response.refresh_token,
        expires_at,
        scopes: token_response.scope.split(' ').map(|s| s.to_string()).collect(),
    };

    if let Err(e) = state.save_token(&token).await {
        error!("Failed to save token: {}", e);
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
    let code = form.code;
    let state_param = form.state;

    // Get the stored verifier and redirect_uri
    let (code_verifier, redirect_uri) = {
        let mut flows = state.oauth_flows.lock().unwrap();
        flows.remove(&state_param).map(|f| (f.code_verifier, f.redirect_uri)).unzip()
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
        ("client_id", LIBRESPOT_CLIENT_ID),
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
                e,
                login_url
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
            <p class="text-red-300 mt-2">Make sure you copied the full code from the URL.</p>
        </div>
        <p class="text-center mt-4"><a href="{}" class="text-blue-400 hover:underline">Try Again</a></p>
    </div>
</body>
</html>"#,
            error_text,
            login_url
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

    // Save token
    let token = Token {
        access_token: token_response.access_token,
        refresh_token: token_response.refresh_token,
        expires_at,
        scopes: token_response.scope.split(' ').map(|s| s.to_string()).collect(),
    };

    if let Err(e) = state.save_token(&token).await {
        error!("Failed to save token: {}", e);
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
            e,
            home_url
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
        home_url,
        home_url
    ))
}

/// Disconnect / clear token
async fn auth_disconnect_handler(State(state): State<AppState>) -> Html<String> {
    if let Err(e) = state.clear_token().await {
        error!("Failed to clear token: {}", e);
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

    fn create_test_token(expires_at: u64) -> Token {
        Token {
            access_token: "test_access_token".to_string(),
            refresh_token: "test_refresh_token".to_string(),
            expires_at,
            scopes: vec!["streaming".to_string()],
        }
    }

    #[tokio::test]
    async fn test_has_valid_token_with_valid_token() {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
        let state = create_test_state(temp_file.path().to_path_buf());
        assert!(state.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_expired_token() {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now - 3600); // Expired 1 hour ago
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
        let state = create_test_state(temp_file.path().to_path_buf());
        assert!(!state.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_near_expiry() {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 120); // 2 minutes (within 5-min buffer)
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
        let state = create_test_state(temp_file.path().to_path_buf());
        assert!(!state.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_missing_file() {
        let state = create_test_state(PathBuf::from("/nonexistent/path/token.json"));
        assert!(!state.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_save_and_load_token() {
        let temp_dir = tempfile::tempdir().unwrap();
        let token_path = temp_dir.path().join("token.json");
        
        let state = create_test_state(token_path.clone());
        
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        
        // Save token
        state.save_token(&token).await.unwrap();
        
        // Load and verify
        let loaded = state.load_token().await;
        assert!(loaded.is_some());
        let loaded = loaded.unwrap();
        assert_eq!(loaded.access_token, token.access_token);
        assert_eq!(loaded.refresh_token, token.refresh_token);
        assert_eq!(loaded.expires_at, token.expires_at);
    }

    #[tokio::test]
    async fn test_clear_token() {
        let temp_dir = tempfile::tempdir().unwrap();
        let token_path = temp_dir.path().join("token.json");
        
        let state = create_test_state(token_path.clone());
        
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        
        // Save token
        state.save_token(&token).await.unwrap();
        assert!(token_path.exists());
        assert!(state.has_valid_token().await);
        
        // Clear token
        state.clear_token().await.unwrap();
        assert!(!token_path.exists());
        assert!(!state.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_clear_token_when_no_file() {
        let state = create_test_state(PathBuf::from("/nonexistent/path/token.json"));
        // Should not error when clearing non-existent file
        state.clear_token().await.unwrap();
    }
}
