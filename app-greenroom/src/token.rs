use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::broadcast;
use tracing::{debug, info};

/// OAuth token storage structure used across the application.
///
/// This is the canonical token type used by both the web UI (for PKCE flow)
/// and the librespot client (for storing/loading tokens).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Token {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_at: u64, // Unix timestamp
    pub scopes: Vec<String>,
}

impl Token {
    /// Check if the token is still valid (with 5 minute buffer).
    pub fn is_valid(&self) -> bool {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        self.expires_at > now + 300
    }
}

/// Save a token to a file, creating parent directories if needed.
///
/// If a `notify_tx` is provided, sends a notification after successful save.
pub async fn save_token(
    token_file: &PathBuf,
    token: &Token,
    notify_tx: Option<&broadcast::Sender<()>>,
) -> anyhow::Result<()> {
    // Ensure parent directory exists
    if let Some(parent) = token_file.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let contents = serde_json::to_string_pretty(token)?;
    tokio::fs::write(token_file, contents).await?;
    debug!("Saved OAuth token to {}", token_file.display());

    // Notify daemon that a new token is available
    if let Some(tx) = notify_tx {
        let _ = tx.send(());
        debug!("Sent token notification to daemon");
    }

    Ok(())
}

/// Load a token from a file if it exists and is valid JSON.
pub async fn load_token(token_file: &PathBuf) -> Option<Token> {
    if !token_file.exists() {
        return None;
    }

    match tokio::fs::read_to_string(token_file).await {
        Ok(contents) => serde_json::from_str::<Token>(&contents).ok(),
        Err(_) => None,
    }
}

/// Clear token file (logout).
pub async fn clear_token(token_file: &PathBuf) -> anyhow::Result<()> {
    if token_file.exists() {
        tokio::fs::remove_file(token_file).await?;
        info!("Cleared OAuth token from {}", token_file.display());
    }
    Ok(())
}
