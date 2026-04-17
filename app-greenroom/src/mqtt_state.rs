use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tracing::{debug, error};

/// Connection state storage for the MQTT "Active" switch.
///
/// Persists whether the Spotify connection is enabled/disabled across restarts.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConnectionState {
    pub enabled: bool,
}

/// Load connection state from file.
///
/// Returns saved state if file exists, otherwise returns default enabled=true.
pub async fn load_connection_state(state_file: &PathBuf) -> ConnectionState {
    match tokio::fs::read_to_string(state_file).await {
        Ok(contents) => match serde_json::from_str::<ConnectionState>(&contents) {
            Ok(state) => {
                debug!("Loaded connection state: enabled={}", state.enabled);
                state
            }
            Err(e) => {
                error!("Failed to parse connection state file: {}", e);
                ConnectionState { enabled: true }
            }
        },
        Err(e) => {
            if e.kind() != std::io::ErrorKind::NotFound {
                error!("Failed to read connection state file: {}", e);
            }
            ConnectionState { enabled: true }
        }
    }
}

/// Save connection state to file.
///
/// Creates parent directories if needed.
pub async fn save_connection_state(
    state_file: &PathBuf,
    state: &ConnectionState,
) -> anyhow::Result<()> {
    // Ensure parent directory exists
    if let Some(parent) = state_file.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let contents = serde_json::to_string_pretty(state)?;
    tokio::fs::write(state_file, contents).await?;
    debug!("Saved connection state to {}", state_file.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_save_and_load_connection_state_enabled() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir.path().join("connection_state.json");

        // Save enabled state
        let state = ConnectionState { enabled: true };
        save_connection_state(&state_file, &state).await.unwrap();

        // Load and verify
        let loaded = load_connection_state(&state_file).await;
        assert!(loaded.enabled);
    }

    #[tokio::test]
    async fn test_save_and_load_connection_state_disabled() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir.path().join("connection_state.json");

        // Save disabled state
        let state = ConnectionState { enabled: false };
        save_connection_state(&state_file, &state).await.unwrap();

        // Load and verify
        let loaded = load_connection_state(&state_file).await;
        assert!(!loaded.enabled);
    }

    #[tokio::test]
    async fn test_load_connection_state_missing_file() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir.path().join("nonexistent.json");

        // Should return default enabled=true for missing file
        let loaded = load_connection_state(&state_file).await;
        assert!(loaded.enabled);
    }

    #[tokio::test]
    async fn test_load_connection_state_invalid_json() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir.path().join("connection_state.json");

        // Write invalid JSON
        tokio::fs::write(&state_file, "not valid json")
            .await
            .unwrap();

        // Should return default enabled=true for invalid JSON
        let loaded = load_connection_state(&state_file).await;
        assert!(loaded.enabled);
    }

    #[test]
    fn test_connection_state_default() {
        // Default should be false (from Default trait)
        let state: ConnectionState = Default::default();
        assert!(!state.enabled);
    }

    #[tokio::test]
    async fn test_save_connection_state_creates_parent_dirs() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir
            .path()
            .join("subdir1")
            .join("subdir2")
            .join("connection_state.json");

        // Save to nested path
        let state = ConnectionState { enabled: true };
        save_connection_state(&state_file, &state).await.unwrap();

        // Verify file was created
        assert!(state_file.exists());

        // Verify contents
        let loaded = load_connection_state(&state_file).await;
        assert!(loaded.enabled);
    }

    #[tokio::test]
    async fn test_connection_state_round_trip() {
        let temp_dir = tempfile::tempdir().unwrap();
        let state_file = temp_dir.path().join("connection_state.json");

        // Test multiple saves and loads
        for enabled in [true, false, true, false] {
            let state = ConnectionState { enabled };
            save_connection_state(&state_file, &state).await.unwrap();

            let loaded = load_connection_state(&state_file).await;
            assert_eq!(loaded.enabled, enabled);
        }
    }
}
