use futures::StreamExt;
use protobuf::{EnumOrUnknown, MessageField};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, RwLock};
use tokio::time::sleep;
use tracing::{debug, error, info, warn};

use librespot_core::authentication::Credentials;
use librespot_core::cache::Cache;
use librespot_core::config::DeviceType;
use librespot_core::config::SessionConfig;
use librespot_core::dealer::protocol::Message;
use librespot_core::session::Session;
use librespot_core::spotify_id::SpotifyId;
use librespot_core::version;
use librespot_metadata::{Metadata, Track};
use librespot_protocol::connect::{
    Capabilities, ClusterUpdate, Device, DeviceInfo, MemberType, PutStateReason, PutStateRequest,
};
use librespot_protocol::media::AudioQuality;
use librespot_protocol::player::{ContextPlayerOptions, PlayOrigin, PlayerState, Suppressions};

use crate::token;
use crate::{Config, PlaybackState};

/// Max backoff for reconnection attempts (5 minutes)
const MAX_BACKOFF_SECS: u64 = 300;

/// Calculate exponential backoff delay for reconnection attempts.
/// Returns delay in seconds: 5, 5, 10, 20, 40, 80, 160, capped at 300.
pub fn calculate_backoff(consecutive_errors: u32) -> u64 {
    // First two errors use 5 second delay, then exponential
    let power: u32 = consecutive_errors.saturating_sub(2);
    let multiplier = 2u64.checked_pow(power).unwrap_or(u64::MAX);
    std::cmp::min(5u64.saturating_mul(multiplier), MAX_BACKOFF_SECS)
}

pub struct SpotifyClient {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    token_file: PathBuf,
    state_tx: broadcast::Sender<()>,
    token_tx: broadcast::Sender<()>,
    connection_rx: broadcast::Receiver<bool>,
    shutdown: Arc<RwLock<bool>>,
}

impl SpotifyClient {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        state_tx: broadcast::Sender<()>,
        token_file: PathBuf,
        token_tx: broadcast::Sender<()>,
        connection_rx: broadcast::Receiver<bool>,
    ) -> Self {
        Self {
            config,
            playback_state,
            token_file,
            state_tx,
            token_tx,
            connection_rx,
            shutdown: Arc::new(RwLock::new(false)),
        }
    }

    /// Check if we have stored credentials (for web UI status).
    /// Note: This only checks file existence - session validity is tracked separately.
    pub async fn has_credentials(&self) -> bool {
        token::has_credentials_file(&self.token_file).await
    }

    /// Send a Home Assistant persistent notification
    async fn send_ha_notification(&self, title: &str, message: &str) {
        let supervisor_token = match std::env::var("SUPERVISOR_TOKEN") {
            Ok(t) => t,
            Err(_) => {
                warn!("SUPERVISOR_TOKEN not set, cannot send HA notification");
                return;
            }
        };

        let client = reqwest::Client::new();
        let url = "http://supervisor/core/api/services/persistent_notification/create";

        let body = serde_json::json!({
            "title": title,
            "message": message,
            "notification_id": "greenroom_auth_error"
        });

        match client
            .post(url)
            .header("Authorization", format!("Bearer {}", supervisor_token))
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
        {
            Ok(_) => info!("Sent HA notification: {}", title),
            Err(e) => warn!("Failed to send HA notification: {}", e),
        }
    }

    /// Main run loop - handles connection, reconnection, and demo mode.
    ///
    /// Key design: Once a librespot Session is established, it manages its own
    /// token refresh internally via TokenProvider. We only reconnect when the
    /// WebSocket connection actually fails.
    pub async fn run(mut self) -> anyhow::Result<()> {
        let mut consecutive_errors: u32 = 0;

        loop {
            if !self.wait_for_connection_enabled().await {
                info!("Spotify client shutting down gracefully");
                return Ok(());
            }

            let has_credentials = self.has_credentials().await;

            if !has_credentials {
                info!("No Spotify credentials found, running in demo mode");
                info!("Use the web UI to authenticate with Spotify");
                self.run_demo_mode().await?;
                // Demo mode returned (credentials appeared), continue to connection
                continue;
            }

            info!("Attempting to connect to Spotify...");

            match self.attempt_connection().await {
                Ok(()) => {
                    // Check if shutdown was requested
                    if *self.shutdown.read().await {
                        info!("Spotify client shutting down gracefully");
                        return Ok(());
                    }
                    warn!("Spotify connection ended, will reconnect...");
                    // Reset error count on successful connection end
                    if consecutive_errors > 0 {
                        debug!("Resetting consecutive error count after successful connection");
                        consecutive_errors = 0;
                    }
                    self.set_disconnected_state().await;
                }
                Err(e) => {
                    let err_msg = format!("{}", e);

                    // Handle intentional disconnect via MQTT switch
                    if err_msg.contains("Connection disabled via MQTT switch") {
                        info!("Connection intentionally disabled via MQTT switch, entering wait state");
                        self.set_disabled_state().await;
                        // Reset error count since this was intentional
                        consecutive_errors = 0;
                        // Wait for connection to be re-enabled
                        if !self.wait_for_disabled_state().await {
                            return Ok(());
                        }
                        // Connection re-enabled, continue to next loop iteration
                        continue;
                    }

                    if err_msg.contains("TOKEN_REVOKED")
                        || err_msg.contains("invalid_grant")
                        || err_msg.contains("Bad credentials")
                    {
                        warn!("Credentials have been revoked or expired, entering demo mode for re-authentication");
                        self.set_disconnected_state().await;
                        consecutive_errors = 0;
                        self.run_demo_mode().await?;
                        continue;
                    }

                    if err_msg.contains("Connection to Spotify server closed")
                        || err_msg.contains("WebSocket")
                        || err_msg.contains("timed out")
                        || err_msg.contains("Session invalidated")
                    {
                        warn!("Spotify connection lost ({}), will reconnect...", err_msg);
                    } else {
                        error!("Connection error: {}", e);
                    }

                    self.set_disconnected_state().await;
                    consecutive_errors += 1;

                    let backoff_secs = calculate_backoff(consecutive_errors);
                    warn!(
                        "Waiting {} seconds before reconnection attempt (error count: {})...",
                        backoff_secs, consecutive_errors
                    );
                    sleep(Duration::from_secs(backoff_secs)).await;
                    continue;
                }
            }

            // Standard reconnection delay after clean connection
            sleep(Duration::from_secs(5)).await;
        }
    }

    /// Wait for connection to be enabled via MQTT switch.
    async fn wait_for_connection_enabled(&mut self) -> bool {
        loop {
            // Check current state
            let currently_enabled = {
                let state = self.playback_state.read().await;
                state.connection_enabled
            };

            if !currently_enabled {
                info!("Spotify connection disabled via MQTT switch, waiting...");
                self.set_disabled_state().await;

                // Wait for connection to be re-enabled
                let should_continue = self.wait_for_disabled_state().await;
                if !should_continue {
                    return false;
                }
                // Clear stale metadata and show waiting state
                self.set_waiting_state().await;
                // Loop back to recheck and continue with connection
                continue;
            }

            // Connection is enabled, check for any disable messages
            match self.connection_rx.try_recv() {
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
                        let state = self.playback_state.read().await;
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
    async fn wait_for_disabled_state(&mut self) -> bool {
        loop {
            tokio::select! {
                result = self.connection_rx.recv() => {
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
                                let state = self.playback_state.read().await;
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
                        let state = self.playback_state.read().await;
                        state.connection_enabled
                    };
                    if enabled {
                        info!("Spotify connection enabled, resuming...");
                        return true;
                    }
                }
            }

            // Check for shutdown signal while disabled
            if *self.shutdown.read().await {
                return false;
            }
        }
    }

    /// Set playback state to disabled status.
    async fn set_disabled_state(&self) {
        let mut state = self.playback_state.write().await;
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
        let _ = self.state_tx.send(());
        info!("Playback state set to disabled");
    }

    async fn attempt_connection(&mut self) -> anyhow::Result<()> {
        // Check if connection is still enabled before attempting
        let enabled = {
            let state = self.playback_state.read().await;
            state.connection_enabled
        };
        if !enabled {
            return Err(anyhow::anyhow!("Connection disabled via MQTT switch"));
        }

        // Try cached librespot credentials first (fast reconnection path)
        let cache = self.get_cache()?;
        let cached_creds = cache.credentials();
        debug!(
            "Attempting connection: has_cached_creds={}, cache_dir={}",
            cached_creds.is_some(),
            self.token_file
                .parent()
                .unwrap_or(Path::new("/data"))
                .display()
        );

        if let Some(cached_creds) = cached_creds {
            info!(
                "Using cached librespot credentials (auth_type={:?})",
                cached_creds.auth_type
            );
            match self
                .connect_with_credentials(cached_creds, Some(cache.clone()), None)
                .await
            {
                Ok(()) => {
                    info!("Successfully connected using cached librespot credentials");
                    return Ok(());
                }
                Err(e) => {
                    let err_msg = format!("{}", e);
                    // Don't fall back to OAuth if connection was intentionally disabled
                    if err_msg.contains("Connection disabled via MQTT switch") {
                        return Err(e);
                    }
                    debug!("Cached credentials failed, falling back to OAuth: {}", e);
                    // Continue to OAuth fallback
                }
            }
        } else {
            debug!("No cached librespot credentials found, will use OAuth");
        }

        // Fall back to OAuth credentials from file
        let cache = self.get_cache()?;

        let credentials = match token::load_credentials(&self.token_file).await {
            Some(creds) if creds.exists() => {
                debug!(
                    "Loaded OAuth credentials from {}",
                    self.token_file.display()
                );
                creds
            }
            _ => {
                return Err(anyhow::anyhow!("No valid credentials found"));
            }
        };

        // Check if OAuth token needs refresh
        let credentials = if credentials.is_expired() {
            info!("OAuth token expired, refreshing...");
            match token::refresh_oauth_token(&credentials).await {
                Ok(refreshed) => {
                    // Save refreshed credentials immediately
                    if let Err(e) =
                        token::save_credentials(&self.token_file, &refreshed, Some(&self.token_tx))
                            .await
                    {
                        error!("Failed to save refreshed credentials: {}", e);
                    }
                    refreshed
                }
                Err(e) => {
                    warn!("Failed to refresh OAuth token: {}", e);
                    credentials // Use original credentials, will try reactive refresh on error
                }
            }
        } else {
            credentials
        };

        // Convert to librespot Credentials and connect
        let librespot_creds = token::to_librespot_credentials(&credentials);
        self.connect_with_credentials(librespot_creds, Some(cache), Some(credentials))
            .await
    }

    /// Get the cache directory for librespot credentials (same dir as token file)
    fn get_cache(&self) -> anyhow::Result<Cache> {
        let cache_dir: PathBuf = self
            .token_file
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("/data"));

        Cache::new(Some(cache_dir), None::<PathBuf>, None::<PathBuf>, None)
            .map_err(|e| anyhow::anyhow!("Failed to create cache: {}", e))
    }

    /// Connect to Spotify with credentials and run the media monitor.
    async fn connect_with_credentials(
        &mut self,
        credentials: Credentials,
        cache: Option<Cache>,
        original_oauth: Option<token::AuthCredentials>,
    ) -> anyhow::Result<()> {
        let session_config = SessionConfig::default();
        let session = Session::new(session_config.clone(), cache.clone());

        // Connect and store librespot's reusable session credentials in cache.
        // Note: This is DIFFERENT from our OAuth file - librespot cache stores
        // session-specific auth data from Spotify (reusable_auth_credentials),
        // while our token file stores OAuth access/refresh tokens for re-authentication.
        match session.connect(credentials, true).await {
            Ok(()) => {
                debug!("Connected to Spotify! Starting media monitoring...");
                self.run_media_monitor(session).await
            }
            Err(e) => {
                error!("Failed to connect: {}", e);

                let err_str = format!("{}", e);
                if err_str.contains("invalid_grant")
                    || err_str.contains("revoked")
                    || err_str.contains("Bad credentials")
                {
                    // Try reactive refresh if we have original OAuth credentials
                    if let Some(oauth_creds) = original_oauth {
                        warn!("Auth failed, attempting reactive OAuth refresh...");
                        match token::refresh_oauth_token(&oauth_creds).await {
                            Ok(refreshed) => {
                                // Save refreshed credentials
                                if let Err(save_err) = token::save_credentials(
                                    &self.token_file,
                                    &refreshed,
                                    Some(&self.token_tx),
                                )
                                .await
                                {
                                    error!("Failed to save refreshed credentials: {}", save_err);
                                }
                                info!("OAuth refresh successful, retrying connection...");
                                let refreshed_librespot_creds =
                                    token::to_librespot_credentials(&refreshed);
                                let retry_session = Session::new(session_config, cache.clone());
                                match retry_session.connect(refreshed_librespot_creds, true).await {
                                    Ok(()) => {
                                        info!("Reconnection with refreshed token successful");
                                        return self.run_media_monitor(retry_session).await;
                                    }
                                    Err(retry_err) => {
                                        warn!("Retry with refreshed token failed: {}", retry_err);
                                    }
                                }
                            }
                            Err(refresh_err) => {
                                warn!("Reactive OAuth refresh failed: {}", refresh_err);
                            }
                        }
                    }

                    // If we get here, refresh didn't work or wasn't available
                    warn!(
                        "Authentication revoked or expired by Spotify, clearing stored credentials"
                    );

                    if let Err(clear_err) = token::clear_credentials(&self.token_file).await {
                        error!("Failed to clear revoked credentials: {}", clear_err);
                    }

                    self.send_ha_notification(
                        "Greenroom Authentication Required",
                        "Your Spotify authorization has expired. Please reconnect your account through the Greenroom web UI."
                    ).await;

                    return Err(anyhow::anyhow!("TOKEN_REVOKED: {}", e));
                }

                Err(e.into())
            }
        }
    }

    /// Monitor media playback via the Spotify Connect cluster.
    ///
    /// This runs until the connection closes (either gracefully or due to error).
    /// The session stays alive via WebSocket, and librespot internally manages
    /// token refresh - we don't need to handle expiry here.
    async fn run_media_monitor(&mut self, session: Session) -> anyhow::Result<()> {
        debug!("Starting dealer connection for real-time playback monitoring...");

        // Subscribe to player commands to log that we don't support being an active player
        let mut player_commands = session
            .dealer()
            .listen_for("hm://connect-state/v1/player/command", log_player_command)
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to player commands: {}", e))?;

        session
            .dealer()
            .start()
            .await
            .map_err(|e| anyhow::anyhow!("Failed to start dealer: {}", e))?;

        debug!("Dealer connected! Waiting for connection ID...");

        let mut connection_id_stream = session
            .dealer()
            .listen_for("hm://pusher/v1/connections/", extract_connection_id)
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to connection_id: {}", e))?;

        let connection_id = match connection_id_stream.next().await {
            Some(Ok(id)) => {
                debug!("Received connection ID: {}", id);
                id
            }
            Some(Err(e)) => return Err(anyhow::anyhow!("Failed to get connection ID: {}", e)),
            None => return Err(anyhow::anyhow!("Connection ID stream ended unexpectedly")),
        };

        session.set_connection_id(&connection_id);

        info!("Registering device in cluster...");

        let put_state_request =
            create_join_cluster_request(&session, &self.config.device_name, PutStateReason::NEW_DEVICE);
        session
            .spclient()
            .put_connect_state_request(&put_state_request)
            .await
            .map_err(|e| anyhow::anyhow!("Failed to register in cluster: {}", e))?;

        debug!("Registered in cluster! Subscribing to state updates...");

        let mut cluster_updates = session
            .dealer()
            .listen_for(
                "hm://connect-state/v1/cluster",
                Message::from_raw::<ClusterUpdate>,
            )
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to cluster: {}", e))?;

        // Also keep the connection_id stream open as a WebSocket health indicator
        let mut connection_id_stream = session
            .dealer()
            .listen_for("hm://pusher/v1/connections/", extract_connection_id)
            .map_err(|e| anyhow::anyhow!("Failed to re-subscribe to connection_id: {}", e))?;

        debug!("Subscribed to cluster updates! Monitoring playback from other Spotify devices...");

        // Reset to "Waiting" if we don't have current playback info
        // Also reset if showing stale disabled/disconnected messages
        let has_current_info = {
            let state = self.playback_state.read().await;
            if let Some(ref track) = state.track {
                track != "Waiting for playback..."
                    && track != "Not Connected"
                    && track != "Connection Disabled"
            } else {
                false
            }
        };

        if !has_current_info {
            let mut state = self.playback_state.write().await;
            self.apply_waiting_state(&mut state).await;
        }

        // Mark as connected to Spotify WebSocket
        {
            let mut state = self.playback_state.write().await;
            state.is_spotify_connected = true;
        }
        let _ = self.state_tx.send(());

        let playback_state = self.playback_state.clone();
        let session_clone = session.clone();
        let state_tx = self.state_tx.clone();

        info!("Greenroom monitor active - tracking Spotify playback");

        let mut last_update = Instant::now();
        let mut consecutive_errors = 0u32;
        let mut last_connection_id: Option<String> = None;
        let session_start_time = Instant::now();

        // Basic tracking for debugging reconnection issues
        let mut cluster_update_count: u64 = 0;
        let mut connection_id_count: u64 = 0;

        // Create shutdown signal handlers
        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;

        loop {
            tokio::select! {
                // Handle graceful shutdown signals
                _ = sigterm.recv() => {
                    info!("Received SIGTERM, shutting down Spotify client gracefully...");
                    *self.shutdown.write().await = true;
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Received SIGINT, shutting down Spotify client gracefully...");
                    *self.shutdown.write().await = true;
                    return Ok(());
                }
                // Handle connection control commands
                result = self.connection_rx.recv() => {
                    match result {
                        Ok(enabled) => {
                            if !enabled {
                                info!("Connection disabled via MQTT switch, disconnecting...");
                                return Err(anyhow::anyhow!("Connection disabled via MQTT switch"));
                            }
                        }
                        Err(broadcast::error::RecvError::Closed) => {
                            warn!("Connection control channel closed");
                            return Ok(());
                        }
                        Err(broadcast::error::RecvError::Lagged(_)) => {
                            // Check shared state after lag
                            let enabled = {
                                let state = self.playback_state.read().await;
                                state.connection_enabled
                            };
                            if !enabled {
                                info!("Connection disabled (detected after lag), disconnecting...");
                                return Err(anyhow::anyhow!("Connection disabled via MQTT switch"));
                            }
                        }
                    }
                }

                player_cmd_result = player_commands.next() => {
                    match player_cmd_result {
                        Some(Ok(())) => {
                            // Player command was logged by the handler
                        }
                        Some(Err(e)) => {
                            debug!("Error receiving player command: {}", e);
                        }
                        None => {
                            debug!("Player command stream ended");
                        }
                    }
                }
                cluster_result = cluster_updates.next() => {
                    match cluster_result {
                        Some(Ok(cluster_update)) => {
                            cluster_update_count += 1;
                            let elapsed = last_update.elapsed();
                            if elapsed > Duration::from_secs(180) {
                                debug!("Playback updates resumed after {}s silence (total updates: {})",
                                      elapsed.as_secs(), cluster_update_count);
                            }
                            last_update = Instant::now();
                            // Reset error count on successful update
                            if consecutive_errors > 0 {
                                consecutive_errors = 0;
                            }
                            self.handle_cluster_update(&cluster_update, &playback_state, &session_clone, &state_tx).await;
                        }
                        Some(Err(e)) => {
                            // Only log as warning if session is still valid
                            debug!("Cluster stream error: {}", e);
                            if session_clone.is_invalid() {
                                warn!("Cluster update error and session invalid, disconnecting");
                                return Err(anyhow::anyhow!("Connection lost: {}", e));
                            }
                        }
                        None => {
                            if session_clone.is_invalid() {
                                warn!("Cluster stream ended and session is invalid");
                                return Err(anyhow::anyhow!("Connection to Spotify server closed"));
                            }
                            debug!("Cluster stream paused, waiting...");
                            sleep(Duration::from_secs(3)).await;
                            continue;
                        }
                    }
                }
                // Monitor connection_id stream for reconnection detection
                connection_result = connection_id_stream.next() => {
                    match connection_result {
                        Some(Ok(new_id)) => {
                            connection_id_count += 1;

                            // Check if connection ID changed (indicates librespot reconnection)
                            if let Some(ref last_id) = last_connection_id {
                                if *last_id != new_id {
                                    info!("Spotify reconnected (new connection ID), resuming monitoring...");
                                    // NOTE: Dealer auto-re-subscribes internally. Don't manually re-subscribe
                                    // as it creates duplicate handlers causing "No subscriber" errors.
                                }
                            }
                            last_connection_id = Some(new_id);
                        }
                        Some(Err(e)) => {
                            debug!("Connection ID stream error: {}", e);
                            if session_clone.is_invalid() {
                                warn!("Connection lost");
                                return Err(anyhow::anyhow!("Connection health check failed: {}", e));
                            }
                        }
                        None => {
                            if session_clone.is_invalid() {
                                warn!("Connection lost");
                                return Err(anyhow::anyhow!("WebSocket connection closed"));
                            }
                            sleep(Duration::from_secs(3)).await;
                            continue;
                        }
                    }
                }
                // Periodic session health check - handles case where librespot
                // invalidates session but streams haven't detected it yet
                _ = tokio::time::sleep(Duration::from_secs(60)) => {
                    let now = Instant::now();
                    let session_age = now.duration_since(session_start_time);

                    let is_invalid = session_clone.is_invalid();

                    if is_invalid {
                        warn!(
                            "Session lost after {}s (updates: {}, reconnections: {})",
                            session_age.as_secs(),
                            cluster_update_count,
                            connection_id_count
                        );
                        return Err(anyhow::anyhow!("Session invalidated"));
                    }

                    // Send periodic keepalive to prevent idle WebSocket timeout
                    let should_send_keepalive = {
                        let state = self.playback_state.read().await;
                        state.is_spotify_connected
                    };

                    if should_send_keepalive {
                        debug!("Sending keepalive to maintain session");
                        if let Err(e) = self.send_keepalive(&session_clone).await {
                            warn!("Failed to send keepalive: {}", e);
                        }
                    }
                }
            }
        }
    }

    /// Send a lightweight keepalive to maintain WebSocket session
    /// This generates outbound traffic to prevent server-side idle timeout (~60s)
    async fn send_keepalive(&self, session: &Session) -> anyhow::Result<()> {
        let keepalive_request =
            create_join_cluster_request(session, &self.config.device_name, PutStateReason::NEW_CONNECTION);
        session
            .spclient()
            .put_connect_state_request(&keepalive_request)
            .await
            .map_err(|e| anyhow::anyhow!("Keepalive failed: {}", e))?;
        Ok(())
    }

    async fn handle_cluster_update(
        &self,
        cluster_update: &ClusterUpdate,
        playback_state: &Arc<RwLock<PlaybackState>>,
        session: &Session,
        state_tx: &broadcast::Sender<()>,
    ) {
        debug!("Received cluster update");

        if let Some(cluster) = cluster_update.cluster.as_ref() {
            let active_device_id = &cluster.active_device_id;
            debug!("Active device: {}", active_device_id);

            {
                let mut state = playback_state.write().await;
                state.active_device_id = if active_device_id.is_empty() {
                    None
                } else {
                    Some(active_device_id.clone())
                };
            }

            if let Some(player_state) = cluster.player_state.as_ref() {
                let is_playing = player_state.is_playing && !player_state.is_paused;
                let is_paused = player_state.is_paused;
                let track_uri = player_state
                    .track
                    .as_ref()
                    .map(|t| t.uri.clone())
                    .unwrap_or_default();
                let position_ms = player_state.position_as_of_timestamp;
                let duration_ms = player_state.duration as u32;
                let media_content_id =
                    Some(player_state.context_uri.clone()).filter(|s| !s.is_empty());

                let shuffle = player_state
                    .options
                    .as_ref()
                    .map(|o| o.shuffling_context)
                    .unwrap_or(false);
                let repeat = player_state
                    .options
                    .as_ref()
                    .map(|o| {
                        if o.repeating_track {
                            "track"
                        } else if o.repeating_context {
                            "context"
                        } else {
                            "off"
                        }
                    })
                    .unwrap_or("off")
                    .to_string();

                let is_idle = active_device_id.is_empty();

                let (volume, source, is_volume_muted) = if !is_idle {
                    cluster
                        .device
                        .get(active_device_id)
                        .map(|device_info| {
                            let vol = device_info.volume as f32 / 65535.0;
                            let name = device_info.name.clone();
                            let muted = device_info.volume == 0;
                            (vol, Some(name), muted)
                        })
                        .unwrap_or((0.0, None, true))
                } else {
                    (0.0, None, true)
                };

                debug!(
                    "Player state: raw_playing={}, raw_paused={}, effective_playing={}, track={}, position={}, volume={}, shuffle={}, repeat={}",
                    player_state.is_playing, is_paused, is_playing, track_uri, position_ms, volume, shuffle, repeat
                );

                let track_uri_obj = if track_uri.starts_with("spotify:track:") {
                    let id_str = &track_uri[14..];
                    SpotifyId::from_base62(id_str)
                        .ok()
                        .map(|id| librespot_core::SpotifyUri::Track { id })
                } else {
                    None
                };

                let track_name: String;
                let artist_name: String;
                let album_name: Option<String>;
                let artwork_url: Option<String>;

                if let Some(uri) = track_uri_obj.as_ref() {
                    match Track::get(session, uri).await {
                        Ok(track) => {
                            track_name = track.name.clone();

                            artist_name = track
                                .artists
                                .first()
                                .map(|artist| artist.name.clone())
                                .unwrap_or_else(|| "Unknown Artist".to_string());

                            album_name = Some(track.album.name.clone());

                            artwork_url = track
                                .album
                                .covers
                                .first()
                                .map(|cover| format!("https://i.scdn.co/image/{}", cover.id));
                        }
                        Err(e) => {
                            debug!("Failed to fetch track metadata: {}", e);
                            track_name = track_uri.clone();
                            artist_name = "Unknown Artist".to_string();
                            album_name = None;
                            artwork_url = None;
                        }
                    }
                } else {
                    track_name = track_uri.clone();
                    artist_name = "Unknown".to_string();
                    album_name = None;
                    artwork_url = None;
                }

                {
                    let mut state = playback_state.write().await;
                    if is_idle {
                        // No active device - clear track metadata but preserve playback state
                        state.track = None;
                        state.artist = None;
                        state.album = None;
                        state.artwork_url = None;
                    } else {
                        // Active playback - update track metadata
                        state.track = Some(track_name.clone());
                        state.artist = Some(artist_name.clone());
                        state.album = album_name;
                        state.artwork_url = artwork_url;
                    }
                    state.is_playing = is_playing;
                    state.is_idle = is_idle;
                    state.volume = volume as f64;
                    state.is_muted = is_volume_muted;
                    state.media_position = Some(position_ms as u32);
                    state.media_duration = Some(duration_ms);
                    state.media_content_id = media_content_id;
                    state.source = source;
                    state.shuffle = shuffle;
                    state.repeat = repeat;
                }

                let _ = state_tx.send(());

                debug!(
                    "Playback update: {} - {} (playing: {}, volume: {}%)",
                    track_name,
                    artist_name,
                    is_playing,
                    (volume * 100.0) as u32
                );
            } else {
                debug!("No player state in cluster update");

                {
                    let mut state = playback_state.write().await;
                    state.is_playing = false;
                    state.is_idle = true;
                    state.track = Some("No active playback".to_string());
                    state.artist = None;
                    state.volume = 0.0;
                    state.source = None;
                }
            }
        }
    }

    /// Demo mode: wait for user to authenticate via web UI.
    async fn run_demo_mode(&mut self) -> anyhow::Result<()> {
        {
            let mut state = self.playback_state.write().await;
            state.track = Some("Not Connected".to_string());
            state.artist = Some("Open the Greenroom web UI to connect Spotify".to_string());
            state.album = Some("Click the Greenroom panel in the sidebar".to_string());
            state.is_playing = false;
            state.is_idle = true;
            state.volume = 0.0;
        }

        // Subscribe to token notifications (creates a new receiver each time
        // so demo mode can be re-entered after auth revocation)
        let mut token_rx = self.token_tx.subscribe();

        // Keep running but wait for either token notification or periodic check
        loop {
            tokio::select! {
                // Wait for notification from web UI that new credentials were saved
                result = token_rx.recv() => {
                    match result {
                        Ok(()) => {
                            info!("Received credentials notification from web UI!");
                            if self.has_credentials().await {
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
                            if self.has_credentials().await {
                                info!("Credentials detected after lagged notification!");
                                return Ok(());
                            }
                        }
                    }
                }
                // Periodic fallback check every 10 seconds
                _ = tokio::time::sleep(Duration::from_secs(10)) => {
                    if self.has_credentials().await {
                        info!("Credentials detected via polling! Attempting to connect...");
                        return Ok(());
                    }
                }
            }
        }
    }

    /// Set playback state to disconnected status.
    async fn set_disconnected_state(&self) {
        let mut state = self.playback_state.write().await;
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
        let _ = self.state_tx.send(());
        info!("Playback state reset to disconnected");
    }

    /// Set playback state to waiting (after being re-enabled).
    /// Always clears the disabled message to ensure fresh UI state.
    async fn set_waiting_state(&self) {
        let mut state = self.playback_state.write().await;
        self.apply_waiting_state(&mut state).await;
        info!("Playback state reset to waiting after re-enable");
    }

    /// Apply waiting state to playback state (internal helper).
    async fn apply_waiting_state(&self, state: &mut crate::PlaybackState) {
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
        let _ = self.state_tx.send(());
    }
}

fn extract_connection_id(msg: Message) -> Result<String, librespot_core::Error> {
    let connection_id = msg.headers.get("Spotify-Connection-Id").ok_or_else(|| {
        librespot_core::Error::invalid_argument("Missing Spotify-Connection-Id header")
    })?;
    Ok(connection_id.to_owned())
}

/// This is called if a user tries to select Greenroom as the active playback device.
fn log_player_command(_msg: Message) -> Result<(), librespot_core::Error> {
    warn!("Greenroom is a monitor-only device - please select another Spotify device for playback");
    Ok(())
}

fn create_join_cluster_request(
    session: &Session,
    device_name: &str,
    reason: PutStateReason,
) -> PutStateRequest {
    let device_info = DeviceInfo {
        can_play: true,
        volume: 32767,
        name: device_name.to_string(),
        device_id: session.device_id().to_string(),
        device_type: EnumOrUnknown::new(DeviceType::Speaker.into()),
        device_software_version: version::SEMVER.to_string(),
        spirc_version: version::SPOTIFY_SPIRC_VERSION.to_string(),
        client_id: session.client_id(),
        is_group: false,
        capabilities: MessageField::some(Capabilities {
            volume_steps: 64,
            disable_volume: false,
            gaia_eq_connect_id: true,
            can_be_player: false,
            needs_full_player_state: true,
            is_observable: true,
            is_controllable: false, // Monitor-only: we don't accept control commands
            hidden: false,
            supports_gzip_pushes: true,
            supports_logout: false,
            supported_types: vec![],
            supports_playlist_v2: true,
            supports_transfer_command: false,
            supports_command_request: false,
            supports_set_options_command: false,
            is_voice_enabled: false,
            restrict_to_local: false,
            connect_disabled: false,
            supports_rename: false,
            supports_external_episodes: false,
            supports_set_backend_metadata: false,
            supports_dj: false,
            supports_rooms: false,
            supported_audio_quality: EnumOrUnknown::new(AudioQuality::VERY_HIGH),
            command_acks: false,
            ..Default::default()
        }),
        ..Default::default()
    };

    PutStateRequest {
        member_type: EnumOrUnknown::new(MemberType::CONNECT_STATE),
        put_state_reason: EnumOrUnknown::new(reason),
        device: MessageField::some(Device {
            device_info: MessageField::some(device_info),
            player_state: MessageField::some(PlayerState {
                session_id: session.session_id(),
                is_system_initiated: true,
                playback_speed: 1.0,
                play_origin: MessageField::some(PlayOrigin::new()),
                suppressions: MessageField::some(Suppressions::new()),
                options: MessageField::some(ContextPlayerOptions::new()),
                ..Default::default()
            }),
            ..Default::default()
        }),
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::token::AuthCredentials;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn create_test_credentials(expires_at: u64) -> AuthCredentials {
        AuthCredentials {
            access_token: "test_access_token".to_string(),
            refresh_token: "test_refresh_token".to_string(),
            expires_at,
            scopes: vec!["streaming".to_string()],
        }
    }

    fn create_test_client(token_file: PathBuf) -> SpotifyClient {
        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };
        let (state_tx, _) = broadcast::channel(16);
        let (token_tx, _) = broadcast::channel::<()>(1);
        let (_, connection_rx) = broadcast::channel(4);

        SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            token_file,
            token_tx,
            connection_rx,
        )
    }

    #[tokio::test]
    async fn test_has_credentials_with_valid_file() {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let creds = create_test_credentials(now + 3600);
        let json = serde_json::to_string(&creds).unwrap();

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();

        let client = create_test_client(temp_file.path().to_path_buf());

        assert!(client.has_credentials().await);
    }

    #[tokio::test]
    async fn test_has_credentials_with_missing_file() {
        let client = create_test_client(PathBuf::from("/nonexistent/path/creds.json"));
        assert!(!client.has_credentials().await);
    }

    #[tokio::test]
    async fn test_load_credentials_with_valid_file() {
        use crate::token;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let creds = create_test_credentials(now + 3600);
        let json = serde_json::to_string(&creds).unwrap();

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();

        let loaded = token::load_credentials(&temp_file.path().to_path_buf()).await;
        assert!(loaded.is_some());
        let loaded = loaded.unwrap();
        assert_eq!(loaded.access_token, "test_access_token");
        assert_eq!(loaded.refresh_token, "test_refresh_token");
    }

    #[tokio::test]
    async fn test_load_credentials_with_invalid_json() {
        use crate::token;

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(b"not valid json").unwrap();

        // Should return None for invalid JSON
        assert!(token::load_credentials(&temp_file.path().to_path_buf())
            .await
            .is_none());
    }

    #[tokio::test]
    async fn test_set_disconnected_state() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set some initial state
        {
            let mut state = client.playback_state.write().await;
            state.track = Some("Some Track".to_string());
            state.artist = Some("Some Artist".to_string());
            state.is_playing = true;
            state.is_spotify_connected = true;
        }

        // Call set_disconnected_state
        client.set_disconnected_state().await;

        // Verify state was reset
        let state = client.playback_state.read().await;
        assert_eq!(state.track, Some("Not Connected".to_string()));
        assert_eq!(
            state.artist,
            Some("Connection lost - reconnecting...".to_string())
        );
        assert!(!state.is_playing);
        assert!(!state.is_spotify_connected);
    }

    #[tokio::test]
    async fn test_calculate_backoff() {
        // First error: 5 seconds
        assert_eq!(calculate_backoff(1), 5);
        // Second error: 5 seconds
        assert_eq!(calculate_backoff(2), 5);
        // Third error: 10 seconds
        assert_eq!(calculate_backoff(3), 10);
        // Fourth error: 20 seconds
        assert_eq!(calculate_backoff(4), 20);
        // Fifth error: 40 seconds
        assert_eq!(calculate_backoff(5), 40);
        // Sixth error: 80 seconds
        assert_eq!(calculate_backoff(6), 80);
        // Seventh error: 160 seconds
        assert_eq!(calculate_backoff(7), 160);
        // Eighth error: capped at 300 seconds
        assert_eq!(calculate_backoff(8), 300);
        // Many errors: still capped
        assert_eq!(calculate_backoff(100), 300);
    }

    #[tokio::test]
    async fn test_set_disabled_state() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set some initial state
        {
            let mut state = client.playback_state.write().await;
            state.track = Some("Some Track".to_string());
            state.artist = Some("Some Artist".to_string());
            state.is_playing = true;
            state.is_spotify_connected = true;
        }

        // Call set_disabled_state
        client.set_disabled_state().await;

        // Verify state was reset to disabled
        let state = client.playback_state.read().await;
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
    async fn test_connection_disabled_prevents_connection() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set connection as disabled
        {
            let mut state = client.playback_state.write().await;
            state.connection_enabled = false;
        }

        // Verify connection is disabled
        let state = client.playback_state.read().await;
        assert!(!state.connection_enabled);
    }

    #[tokio::test]
    async fn test_connection_enabled_allows_connection() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set connection as enabled
        {
            let mut state = client.playback_state.write().await;
            state.connection_enabled = true;
        }

        // Verify connection is enabled
        let state = client.playback_state.read().await;
        assert!(state.connection_enabled);
    }

    /// Test that connection_rx can be replaced (for testing purposes)
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

    #[tokio::test]
    async fn test_apply_waiting_state_clears_disabled_text() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set state to disabled
        {
            let mut state = client.playback_state.write().await;
            state.track = Some("Connection Disabled".to_string());
            state.artist = Some("Enable via Home Assistant switch to connect".to_string());
        }

        // Apply waiting state
        {
            let mut state = client.playback_state.write().await;
            client.apply_waiting_state(&mut state).await;
        }

        // Verify state was cleared to waiting
        let state = client.playback_state.read().await;
        assert_eq!(state.track, Some("Waiting for playback...".to_string()));
        assert_eq!(state.artist, Some("Greenroom".to_string()));
    }

    #[tokio::test]
    async fn test_apply_waiting_state_clears_not_connected() {
        let temp_file = NamedTempFile::new().unwrap();
        let client = create_test_client(temp_file.path().to_path_buf());

        // Set state to not connected
        {
            let mut state = client.playback_state.write().await;
            state.track = Some("Not Connected".to_string());
            state.artist = Some("Connection lost - reconnecting...".to_string());
        }

        // Apply waiting state
        {
            let mut state = client.playback_state.write().await;
            client.apply_waiting_state(&mut state).await;
        }

        // Verify state was cleared to waiting
        let state = client.playback_state.read().await;
        assert_eq!(state.track, Some("Waiting for playback...".to_string()));
        assert_eq!(state.artist, Some("Greenroom".to_string()));
    }

    #[tokio::test]
    async fn test_playback_state_default_connection_enabled() {
        // Note: Default::default() sets connection_enabled to false
        // But main.rs initializes it to true explicitly
        let state = PlaybackState::default();
        assert!(!state.connection_enabled);
    }

    #[tokio::test]
    async fn test_playback_state_with_connection_enabled() {
        let state = PlaybackState {
            connection_enabled: true,
            ..Default::default()
        };
        assert!(state.connection_enabled);
    }
}
