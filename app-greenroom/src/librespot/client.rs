use futures::StreamExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, RwLock};
use tokio::time::sleep;
use tracing::{debug, error, info, warn};

use librespot_core::authentication::Credentials;
use librespot_core::cache::Cache;
use librespot_core::config::SessionConfig;
use librespot_core::dealer::protocol::Message;
use librespot_core::session::Session;
use librespot_protocol::connect::{ClusterUpdate, PutStateReason};

use crate::token;
use crate::{Config, PlaybackState};

use super::cluster::handle_cluster_update;
use super::connection::{wait_for_connection_enabled, wait_for_disabled_state};
use super::demo::run_demo_mode;
use super::helpers::{
    calculate_backoff, close_websocket, create_join_cluster_request, extract_connection_id,
    log_player_command, send_keepalive,
};
use super::state::{apply_waiting_state, set_disabled_state, set_disconnected_state};

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
            if !wait_for_connection_enabled(
                &self.playback_state,
                &mut self.connection_rx,
                &self.state_tx,
                &self.shutdown,
            )
            .await
            {
                info!("Spotify client shutting down gracefully");
                return Ok(());
            }

            let has_credentials = self.has_credentials().await;

            if !has_credentials {
                info!("No Spotify credentials found, running in demo mode");
                info!("Use the web UI to authenticate with Spotify");
                run_demo_mode(&self.playback_state, &self.token_file, &self.token_tx).await?;
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
                    set_disconnected_state(&self.playback_state, &self.state_tx).await;
                }
                Err(e) => {
                    let err_msg = format!("{}", e);

                    // Handle intentional disconnect via MQTT switch
                    if err_msg.contains("Connection disabled via MQTT switch") {
                        debug!("Connection intentionally disabled via MQTT switch, entering wait state");
                        set_disabled_state(&self.playback_state, &self.state_tx).await;
                        // Reset error count since this was intentional
                        consecutive_errors = 0;
                        // Wait for connection to be re-enabled
                        if !wait_for_disabled_state(
                            &self.playback_state,
                            &mut self.connection_rx,
                            &self.shutdown,
                        )
                        .await
                        {
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
                        set_disconnected_state(&self.playback_state, &self.state_tx).await;
                        consecutive_errors = 0;
                        run_demo_mode(&self.playback_state, &self.token_file, &self.token_tx)
                            .await?;
                        continue;
                    }

                    if err_msg.contains("Connection to Spotify server closed")
                        || err_msg.contains("WebSocket")
                        || err_msg.contains("Connection reset")
                        || err_msg.contains("closing handshake")
                        || err_msg.contains("timed out")
                        || err_msg.contains("Session invalidated")
                    {
                        warn!("Spotify connection lost ({}), will reconnect...", err_msg);
                    } else {
                        error!("Connection error: {}", e);
                    }

                    set_disconnected_state(&self.playback_state, &self.state_tx).await;
                    consecutive_errors += 1;

                    let backoff_secs = calculate_backoff(consecutive_errors);
                    warn!(
                        "Waiting {} seconds before reconnection attempt (error count: {})...",
                        backoff_secs, consecutive_errors
                    );

                    // Interruptible backoff - check for disable signal during sleep
                    tokio::select! {
                        _ = sleep(Duration::from_secs(backoff_secs)) => {
                            // Normal backoff completed, continue to next attempt
                        }
                        result = self.connection_rx.recv() => {
                            match result {
                                Ok(enabled) if !enabled => {
                                    info!("Connection disabled via MQTT switch during backoff");
                                    set_disabled_state(&self.playback_state, &self.state_tx).await;
                                    consecutive_errors = 0;
                                    // Wait for re-enable
                                    if !wait_for_disabled_state(
                                        &self.playback_state,
                                        &mut self.connection_rx,
                                        &self.shutdown,
                                    )
                                    .await
                                    {
                                        return Ok(());
                                    }
                                }
                                Ok(_) => {
                                    // Enabled signal, just continue to next attempt
                                }
                                Err(broadcast::error::RecvError::Closed) => {
                                    warn!("Connection control channel closed during backoff");
                                    return Ok(());
                                }
                                Err(broadcast::error::RecvError::Lagged(_)) => {
                                    // Check shared state after lag
                                    let enabled = {
                                        let state = self.playback_state.read().await;
                                        state.connection_enabled
                                    };
                                    if !enabled {
                                        info!("Connection disabled (detected after lag), entering wait state");
                                        set_disabled_state(&self.playback_state, &self.state_tx).await;
                                        consecutive_errors = 0;
                                        if !wait_for_disabled_state(
                                            &self.playback_state,
                                            &mut self.connection_rx,
                                            &self.shutdown,
                                        )
                                        .await
                                        {
                                            return Ok(());
                                        }
                                    }
                                }
                            }
                        }
                    }
                    continue;
                }
            }

            // Standard reconnection delay after clean connection - also interruptible
            tokio::select! {
                _ = sleep(Duration::from_secs(5)) => {}
                result = self.connection_rx.recv() => {
                    if let Ok(enabled) = result {
                        if !enabled {
                            info!("Connection disabled via MQTT switch during reconnection delay");
                            set_disabled_state(&self.playback_state, &self.state_tx).await;
                            if !wait_for_disabled_state(
                                &self.playback_state,
                                &mut self.connection_rx,
                                &self.shutdown,
                            )
                            .await
                            {
                                return Ok(());
                            }
                        }
                    }
                }
            }
        }
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

        let put_state_request = create_join_cluster_request(
            &session,
            &self.config.device_name,
            PutStateReason::NEW_DEVICE,
        );
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
            apply_waiting_state(&mut state, &self.state_tx).await;
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
                    close_websocket(&session_clone).await;
                    *self.shutdown.write().await = true;
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Received SIGINT, shutting down Spotify client gracefully...");
                    close_websocket(&session_clone).await;
                    *self.shutdown.write().await = true;
                    return Ok(());
                }
                // Handle connection control commands
                result = self.connection_rx.recv() => {
                    match result {
                        Ok(enabled) => {
                            if !enabled {
                                info!("Connection disabled via MQTT switch, disconnecting...");
                                close_websocket(&session_clone).await;
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
                                close_websocket(&session_clone).await;
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
                            handle_cluster_update(&cluster_update, &playback_state, &session_clone, &state_tx).await;
                        }
                        Some(Err(e)) => {
                            // Stream errors indicate WebSocket connection issues - reconnect immediately
                            warn!("Cluster stream error (WebSocket connection issue): {}", e);
                            return Err(anyhow::anyhow!("Connection lost: {}", e));
                        }
                        None => {
                            // Stream ended - WebSocket closed
                            warn!("Cluster stream ended (WebSocket connection closed)");
                            return Err(anyhow::anyhow!("Connection to Spotify server closed"));
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
                            // Stream errors indicate WebSocket connection issues - reconnect immediately
                            warn!("Connection ID stream error (WebSocket connection issue): {}", e);
                            return Err(anyhow::anyhow!("Connection health check failed: {}", e));
                        }
                        None => {
                            // Stream ended - WebSocket closed
                            warn!("Connection ID stream ended (WebSocket connection closed)");
                            return Err(anyhow::anyhow!("WebSocket connection closed"));
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
                        if let Err(e) = send_keepalive(&session_clone, &self.config.device_name).await {
                            warn!("Failed to send keepalive: {}", e);
                        }
                    }
                }
            }
        }
    }
}
