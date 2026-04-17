use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, RwLock};
use tracing::{info, warn};

use crate::token;
use crate::PlaybackState;
use tracing::debug;

/// Demo mode: wait for user to authenticate via web UI.
pub async fn run_demo_mode(
    playback_state: &Arc<RwLock<PlaybackState>>,
    token_file: &PathBuf,
    token_tx: &broadcast::Sender<()>,
) -> anyhow::Result<()> {
    {
        let mut state = playback_state.write().await;
        state.track = Some("Not Connected".to_string());
        state.artist = Some("Open the Greenroom web UI to connect Spotify".to_string());
        state.album = Some("Click the Greenroom panel in the sidebar".to_string());
        state.is_playing = false;
        state.is_idle = true;
        state.volume = 0.0;
    }

    // Subscribe to token notifications (creates a new receiver each time
    // so demo mode can be re-entered after auth revocation)
    let mut token_rx = token_tx.subscribe();

    // Keep running but wait for either token notification or periodic check
    loop {
        tokio::select! {
            // Wait for notification from web UI that new credentials were saved
            result = token_rx.recv() => {
                match result {
                    Ok(()) => {
                        info!("Received credentials notification from web UI!");
                        if token::has_credentials_file(token_file).await {
                            info!("Credentials detected, attempting to connect...");
                            return Ok(());
                        } else {
                            warn!("Notification received but credentials not valid yet, continuing demo mode");
                        }
                    }
                    Err(broadcast::error::RecvError::Closed) => {
                        warn!("Token notification channel closed, falling back to polling");
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => {
                        debug!("Token notification channel lagged, checking credentials...");
                        if token::has_credentials_file(token_file).await {
                            info!("Credentials detected after lagged notification!");
                            return Ok(());
                        }
                    }
                }
            }
            // Periodic fallback check every 10 seconds
            _ = tokio::time::sleep(Duration::from_secs(10)) => {
                if token::has_credentials_file(token_file).await {
                    info!("Credentials detected via polling! Attempting to connect...");
                    return Ok(());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::token::AuthCredentials;

    fn create_test_credentials(expires_at: u64) -> AuthCredentials {
        AuthCredentials {
            access_token: "test_access_token".to_string(),
            refresh_token: "test_refresh_token".to_string(),
            expires_at,
            scopes: vec!["streaming".to_string()],
        }
    }

    #[tokio::test]
    async fn test_demo_mode_exits_when_credentials_appear() {
        let temp_dir = tempfile::tempdir().unwrap();
        let token_file = temp_dir.path().join("token.json");

        // Start without credentials
        let playback_state = Arc::new(RwLock::new(PlaybackState::default()));
        let (token_tx, _) = broadcast::channel(1);

        // Spawn demo mode
        let token_file_clone = token_file.clone();
        let playback_state_clone = playback_state.clone();
        let token_tx_clone = token_tx.clone();

        let demo_handle = tokio::spawn(async move {
            run_demo_mode(&playback_state_clone, &token_file_clone, &token_tx_clone).await
        });

        // Wait a bit then create credentials
        tokio::time::sleep(Duration::from_millis(100)).await;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let creds = create_test_credentials(now + 3600);
        let json = serde_json::to_string(&creds).unwrap();
        tokio::fs::write(&token_file, json).await.unwrap();

        // Notify via channel to trigger immediate check
        let _ = token_tx.send(());

        // Demo mode should complete
        let result = tokio::time::timeout(Duration::from_secs(2), demo_handle).await;
        assert!(
            result.is_ok(),
            "Demo mode should exit when credentials appear"
        );
    }
}
