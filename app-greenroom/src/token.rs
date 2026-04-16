use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::broadcast;
use tracing::{debug, error, info};

/// Spotify OAuth client ID (librespot's KEYMASTER_CLIENT_ID)
pub const SPOTIFY_CLIENT_ID: &str = "65b708073fc0480ea92a077233ca87bd";
/// Spotify token endpoint
const TOKEN_URL: &str = "https://accounts.spotify.com/api/token";

/// OAuth token storage for initial authentication.
///
/// This stores the initial OAuth tokens needed to establish a librespot session.
/// Once connected, librespot uses its internal TokenProvider (via Mercury keymaster API)
/// to automatically refresh tokens - we don't need to manage token refresh manually.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthCredentials {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_at: u64, // Unix timestamp (for display purposes only)
    pub scopes: Vec<String>,
}

impl AuthCredentials {
    /// Check if we have credential data (does not indicate session validity).
    ///
    /// Note: This only checks if we have stored credentials, not whether the session
    /// is still active. Once a librespot session is established, it stays alive via
    /// WebSocket and librespot internally manages token refresh via TokenProvider.
    pub fn exists(&self) -> bool {
        !self.access_token.is_empty() && !self.refresh_token.is_empty()
    }

    /// Check if the OAuth access token is expired.
    pub fn is_expired(&self) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        now >= self.expires_at
    }

    /// Check if the OAuth access token will expire within the given duration.
    pub fn will_expire_within(&self, duration: std::time::Duration) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        now + duration.as_secs() >= self.expires_at
    }
}

/// Save credentials to a file, creating parent directories if needed.
///
/// If a `notify_tx` is provided, sends a notification after successful save.
pub async fn save_credentials(
    token_file: &PathBuf,
    credentials: &AuthCredentials,
    notify_tx: Option<&broadcast::Sender<()>>,
) -> anyhow::Result<()> {
    // Ensure parent directory exists
    if let Some(parent) = token_file.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let contents = serde_json::to_string_pretty(credentials)?;
    tokio::fs::write(token_file, contents).await?;
    debug!("Saved OAuth credentials to {}", token_file.display());

    // Notify daemon that new credentials are available
    if let Some(tx) = notify_tx {
        let _ = tx.send(());
        debug!("Sent credentials notification to daemon");
    }

    Ok(())
}

/// Load credentials from a file if it exists and is valid JSON.
pub async fn load_credentials(token_file: &PathBuf) -> Option<AuthCredentials> {
    if !token_file.exists() {
        return None;
    }

    match tokio::fs::read_to_string(token_file).await {
        Ok(contents) => serde_json::from_str::<AuthCredentials>(&contents).ok(),
        Err(_) => None,
    }
}

pub async fn has_credentials_file(token_file: &PathBuf) -> bool {
    token_file.exists()
}

pub async fn clear_credentials(token_file: &PathBuf) -> anyhow::Result<()> {
    if token_file.exists() {
        tokio::fs::remove_file(token_file).await?;
        info!("Cleared OAuth credentials from {}", token_file.display());
    }
    Ok(())
}

/// Convert stored credentials to librespot Credentials for session establishment.
///
/// This creates a Credentials object with the access token for initial session connection.
/// Once connected, librespot manages tokens internally via its TokenProvider.
pub fn to_librespot_credentials(
    credentials: &AuthCredentials,
) -> librespot_core::authentication::Credentials {
    librespot_core::authentication::Credentials::with_access_token(&credentials.access_token)
}

/// Response from Spotify token refresh endpoint.
#[derive(Deserialize)]
struct TokenRefreshResponse {
    access_token: String,
    refresh_token: Option<String>,
    expires_in: u64,
    scope: String,
}

/// Refresh OAuth tokens using the refresh_token.
///
/// This calls Spotify's token endpoint to get a new access_token.
/// The new credentials are returned but NOT saved to file - the caller should save them.
pub async fn refresh_oauth_token(credentials: &AuthCredentials) -> anyhow::Result<AuthCredentials> {
    info!("Refreshing OAuth token...");

    let params = [
        ("grant_type", "refresh_token"),
        ("refresh_token", credentials.refresh_token.as_str()),
        ("client_id", SPOTIFY_CLIENT_ID),
    ];

    let client = reqwest::Client::new();
    let response = client
        .post(TOKEN_URL)
        .form(&params)
        .send()
        .await
        .map_err(|e| anyhow::anyhow!("Token refresh request failed: {}", e))?;

    if !response.status().is_success() {
        let error_text = response
            .text()
            .await
            .unwrap_or_else(|_| "Unknown error".to_string());
        error!("Token refresh failed: {}", error_text);
        return Err(anyhow::anyhow!("Token refresh failed: {}", error_text));
    }

    let token_response: TokenRefreshResponse = response
        .json()
        .await
        .map_err(|e| anyhow::anyhow!("Failed to parse token refresh response: {}", e))?;

    // Calculate new expiration
    let expires_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        + token_response.expires_in;

    // Use new refresh_token if provided, otherwise keep the old one
    let refresh_token = token_response
        .refresh_token
        .unwrap_or_else(|| credentials.refresh_token.clone());

    let new_credentials = AuthCredentials {
        access_token: token_response.access_token,
        refresh_token,
        expires_at,
        scopes: token_response
            .scope
            .split(' ')
            .map(|s| s.to_string())
            .collect(),
    };

    info!("Successfully refreshed OAuth token");
    Ok(new_credentials)
}
