use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::info;

use crate::PlaybackState;

/// Set playback state to disabled status.
pub async fn set_disabled_state(
    playback_state: &Arc<RwLock<PlaybackState>>,
    state_tx: &broadcast::Sender<()>,
) {
    let mut state = playback_state.write().await;
    state.is_spotify_connected = false;
    state.track = Some("Connection Disabled".to_string());
    state.artist = Some("Enable via Home Assistant switch to connect".to_string());
    state.album = None;
    state.artwork_url = None;
    state.is_playing = false;
    state.is_idle = true;
    state.volume = 0.0;
    state.source = None;
    // Notify MQTT bridge to publish the updated state
    let _ = state_tx.send(());
    info!("Playback state set to disabled");
}

/// Set playback state to disconnected status.
pub async fn set_disconnected_state(
    playback_state: &Arc<RwLock<PlaybackState>>,
    state_tx: &broadcast::Sender<()>,
) {
    let mut state = playback_state.write().await;
    state.is_spotify_connected = false;
    state.track = Some("Not Connected".to_string());
    state.artist = Some("Connection lost - reconnecting...".to_string());
    state.album = None;
    state.artwork_url = None;
    state.is_playing = false;
    state.is_idle = true;
    state.volume = 0.0;
    state.source = None;
    // Notify MQTT bridge to publish the updated state
    let _ = state_tx.send(());
    info!("Playback state reset to disconnected");
}

/// Set playback state to waiting (after being re-enabled).
/// Always clears the disabled message to ensure fresh UI state.
pub async fn set_waiting_state(
    playback_state: &Arc<RwLock<PlaybackState>>,
    state_tx: &broadcast::Sender<()>,
) {
    let mut state = playback_state.write().await;
    apply_waiting_state(&mut state, state_tx).await;
    info!("Playback state reset to waiting after re-enable");
}

/// Apply waiting state to playback state (internal helper).
pub async fn apply_waiting_state(state: &mut PlaybackState, state_tx: &broadcast::Sender<()>) {
    state.track = Some("Waiting for playback...".to_string());
    state.artist = Some("Greenroom".to_string());
    state.album = None;
    state.artwork_url = None;
    state.is_playing = false;
    state.is_idle = true;
    state.volume = 0.0;
    state.active_device_id = None;
    state.source = None;
    // Notify MQTT bridge to publish the updated state
    let _ = state_tx.send(());
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    use crate::{Config, PlaybackState};

    fn create_test_client_state(
        _token_file: std::path::PathBuf,
    ) -> (Arc<RwLock<PlaybackState>>, broadcast::Sender<()>) {
        let _config = Config {
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };
        let (state_tx, _) = broadcast::channel(16);

        let playback_state = Arc::new(RwLock::new(PlaybackState::default()));
        (playback_state, state_tx)
    }

    #[tokio::test]
    async fn test_set_disabled_state() {
        let temp_file = NamedTempFile::new().unwrap();
        let (playback_state, state_tx) = create_test_client_state(temp_file.path().to_path_buf());

        // Set some initial state
        {
            let mut state = playback_state.write().await;
            state.track = Some("Some Track".to_string());
            state.artist = Some("Some Artist".to_string());
            state.is_playing = true;
            state.is_spotify_connected = true;
        }

        // Call set_disabled_state
        set_disabled_state(&playback_state, &state_tx).await;

        // Verify state was reset to disabled
        let state = playback_state.read().await;
        assert_eq!(state.track, Some("Connection Disabled".to_string()));
        assert_eq!(
            state.artist,
            Some("Enable via Home Assistant switch to connect".to_string())
        );
        assert!(!state.is_playing);
        assert!(!state.is_spotify_connected);
        assert!(state.is_idle);
    }

    #[tokio::test]
    async fn test_set_disconnected_state() {
        let temp_file = NamedTempFile::new().unwrap();
        let (playback_state, state_tx) = create_test_client_state(temp_file.path().to_path_buf());

        // Set some initial state
        {
            let mut state = playback_state.write().await;
            state.track = Some("Some Track".to_string());
            state.artist = Some("Some Artist".to_string());
            state.is_playing = true;
            state.is_spotify_connected = true;
        }

        // Call set_disconnected_state
        set_disconnected_state(&playback_state, &state_tx).await;

        // Verify state was reset
        let state = playback_state.read().await;
        assert_eq!(state.track, Some("Not Connected".to_string()));
        assert_eq!(
            state.artist,
            Some("Connection lost - reconnecting...".to_string())
        );
        assert!(!state.is_playing);
        assert!(!state.is_spotify_connected);
    }

    #[tokio::test]
    async fn test_apply_waiting_state_clears_disabled_text() {
        let temp_file = NamedTempFile::new().unwrap();
        let (playback_state, state_tx) = create_test_client_state(temp_file.path().to_path_buf());

        // Set state to disabled
        {
            let mut state = playback_state.write().await;
            state.track = Some("Connection Disabled".to_string());
            state.artist = Some("Enable via Home Assistant switch to connect".to_string());
        }

        // Apply waiting state
        {
            let mut state = playback_state.write().await;
            apply_waiting_state(&mut state, &state_tx).await;
        }

        // Verify state was cleared to waiting
        let state = playback_state.read().await;
        assert_eq!(state.track, Some("Waiting for playback...".to_string()));
        assert_eq!(state.artist, Some("Greenroom".to_string()));
    }

    #[tokio::test]
    async fn test_apply_waiting_state_clears_not_connected() {
        let temp_file = NamedTempFile::new().unwrap();
        let (playback_state, state_tx) = create_test_client_state(temp_file.path().to_path_buf());

        // Set state to not connected
        {
            let mut state = playback_state.write().await;
            state.track = Some("Not Connected".to_string());
            state.artist = Some("Connection lost - reconnecting...".to_string());
        }

        // Apply waiting state
        {
            let mut state = playback_state.write().await;
            apply_waiting_state(&mut state, &state_tx).await;
        }

        // Verify state was cleared to waiting
        let state = playback_state.read().await;
        assert_eq!(state.track, Some("Waiting for playback...".to_string()));
        assert_eq!(state.artist, Some("Greenroom".to_string()));
    }
}
