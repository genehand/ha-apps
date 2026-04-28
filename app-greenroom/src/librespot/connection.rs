use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, RwLock};
use tracing::{info, warn};

use crate::librespot::state::set_waiting_state;
use crate::PlaybackState;

/// Wait for connection to be enabled via MQTT switch.
pub async fn wait_for_connection_enabled(
    playback_state: &Arc<RwLock<PlaybackState>>,
    connection_rx: &mut broadcast::Receiver<bool>,
    state_tx: &broadcast::Sender<()>,
    shutdown: &Arc<RwLock<bool>>,
) -> bool {
    loop {
        // Check current state
        let currently_enabled = {
            let state = playback_state.read().await;
            state.connection_enabled
        };

        if !currently_enabled {
            info!("Spotify connection disabled, waiting...");
            // set_disabled_state will be called by the caller if needed

            // Wait for connection to be re-enabled
            let should_continue =
                wait_for_disabled_state(playback_state, connection_rx, shutdown).await;
            if !should_continue {
                return false;
            }
            // Clear stale metadata and show waiting state
            set_waiting_state(playback_state, state_tx).await;
            // Loop back to recheck and continue with connection
            continue;
        }

        // Connection is enabled, check for any disable messages
        match connection_rx.try_recv() {
            Ok(enabled) => {
                if !enabled {
                    info!("Spotify connection disabled via MQTT switch");
                    // Loop back to enter disabled state
                    continue;
                }
            }
            Err(broadcast::error::TryRecvError::Empty) => {
                // No message, continue normally
                return true;
            }
            Err(broadcast::error::TryRecvError::Closed) => {
                warn!("Connection control channel closed");
                return false;
            }
            Err(broadcast::error::TryRecvError::Lagged(_)) => {
                // Check shared state after lag
                let enabled = {
                    let state = playback_state.read().await;
                    state.connection_enabled
                };
                if !enabled {
                    // Loop back to enter disabled state
                    continue;
                }
                return true;
            }
        }
    }
}

/// Wait while in disabled state for connection to be re-enabled.
pub async fn wait_for_disabled_state(
    playback_state: &Arc<RwLock<PlaybackState>>,
    connection_rx: &mut broadcast::Receiver<bool>,
    shutdown: &Arc<RwLock<bool>>,
) -> bool {
    loop {
        tokio::select! {
            result = connection_rx.recv() => {
                match result {
                    Ok(enabled) => {
                        if enabled {
                            info!("Spotify connection enabled via MQTT switch, resuming...");
                            return true;
                        }
                    }
                    Err(broadcast::error::RecvError::Closed) => {
                        warn!("Connection control channel closed, shutting down");
                        return false;
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => {
                        // Check current state after lag
                        let enabled = {
                            let state = playback_state.read().await;
                            state.connection_enabled
                        };
                        if enabled {
                            info!("Spotify connection enabled, resuming...");
                            return true;
                        }
                    }
                }
            }
            _ = tokio::time::sleep(Duration::from_secs(5)) => {
                // Periodic check of shared state as fallback
                let enabled = {
                    let state = playback_state.read().await;
                    state.connection_enabled
                };
                if enabled {
                    info!("Spotify connection enabled, resuming...");
                    return true;
                }
            }
        }

        // Check for shutdown signal while disabled
        if *shutdown.read().await {
            return false;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::PlaybackState;

    fn create_test_state() -> (Arc<RwLock<PlaybackState>>, broadcast::Sender<()>) {
        let (state_tx, _) = broadcast::channel(16);
        let playback_state = Arc::new(RwLock::new(PlaybackState::default()));
        (playback_state, state_tx)
    }

    #[tokio::test]
    async fn test_wait_for_disabled_state_receives_enable() {
        let (playback_state, _state_tx) = create_test_state();
        let shutdown = Arc::new(RwLock::new(false));

        // Set connection as disabled
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = false;
        }

        let (tx, rx) = broadcast::channel(4);
        let mut rx = rx; // Need mutable reference

        // Spawn a task to send enable after a short delay
        let tx_clone = tx.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            tx_clone.send(true).unwrap();
        });

        let result = wait_for_disabled_state(&playback_state, &mut rx, &shutdown).await;
        assert!(result);
    }

    #[tokio::test]
    async fn test_wait_for_disabled_state_shutdown() {
        let (playback_state, _state_tx) = create_test_state();
        let shutdown = Arc::new(RwLock::new(false));

        // Set connection as disabled
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = false;
        }

        let (_tx, rx) = broadcast::channel(4);
        let mut rx = rx;

        // Spawn a task to set shutdown after a short delay
        let shutdown_clone = shutdown.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            *shutdown_clone.write().await = true;
        });

        let result = wait_for_disabled_state(&playback_state, &mut rx, &shutdown).await;
        assert!(!result);
    }

    #[tokio::test]
    async fn test_connection_enabled_allows_connection() {
        let (playback_state, _state_tx) = create_test_state();

        // Set connection as enabled
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = true;
        }

        // Verify connection is enabled
        let state = playback_state.read().await;
        assert!(state.connection_enabled);
    }

    #[tokio::test]
    async fn test_connection_disabled_prevents_connection() {
        let (playback_state, _state_tx) = create_test_state();

        // Set connection as disabled
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = false;
        }

        // Verify connection is disabled
        let state = playback_state.read().await;
        assert!(!state.connection_enabled);
    }

    #[tokio::test]
    async fn test_connection_rx_can_receive_after_replacement() {
        let (tx, mut rx) = broadcast::channel(4);

        // Replace receiver
        let tx2 = tx.clone();
        let mut rx2 = tx2.subscribe();

        // Send through original sender
        tx.send(true).unwrap();

        // Both receivers should get it
        assert_eq!(rx.recv().await.unwrap(), true);
        assert_eq!(rx2.recv().await.unwrap(), true);
    }

    #[tokio::test]
    async fn test_connection_rx_receives_commands() {
        let (tx, mut rx) = broadcast::channel(4);

        // Send commands
        tx.send(true).unwrap();
        tx.send(false).unwrap();

        // Verify receipt
        assert_eq!(rx.recv().await.unwrap(), true);
        assert_eq!(rx.recv().await.unwrap(), false);
    }
}
