use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tokio::sync::broadcast;
use tracing::{debug, info};

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
