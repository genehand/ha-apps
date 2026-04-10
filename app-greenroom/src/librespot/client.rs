use std::sync::Arc;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH, Duration, Instant};
use tokio::sync::{RwLock, broadcast};
use tokio::time::sleep;
use tracing::{info, error, debug, warn};
use futures::StreamExt;
use protobuf::{EnumOrUnknown, MessageField};

use librespot_core::session::Session;
use librespot_core::config::SessionConfig;
use librespot_core::authentication::Credentials;
use librespot_core::spotify_id::SpotifyId;
use librespot_core::dealer::protocol::Message;
use librespot_core::config::DeviceType;
use librespot_core::version;
use librespot_oauth::{OAuthClientBuilder, OAuthToken};
use librespot_metadata::{Metadata, Track};
use librespot_protocol::connect::{ClusterUpdate, PutStateRequest, Device, DeviceInfo, Capabilities, MemberType, PutStateReason};
use librespot_protocol::media::AudioQuality;
use librespot_protocol::player::{PlayOrigin, Suppressions, ContextPlayerOptions, PlayerState};

use crate::{Config, PlaybackState};
use crate::token::{self, Token};

/// Librespot's OAuth client ID (KEYMASTER_CLIENT_ID)
const LIBRESPOT_CLIENT_ID: &str = "65b708073fc0480ea92a077233ca87bd";

/// Convert librespot's OAuthToken to our shared Token type.
/// Calculates expiration as now + 3600 seconds since librespot doesn't provide expires_at.
fn token_from_oauth(token: OAuthToken) -> Token {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let expires_at = now + 3600;
    
    Token {
        access_token: token.access_token,
        refresh_token: token.refresh_token,
        expires_at,
        scopes: token.scopes,
    }
}

pub struct SpotifyClient {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    token_file: PathBuf,
    state_tx: broadcast::Sender<()>,
    token_rx: Option<broadcast::Receiver<()>>,
}

impl SpotifyClient {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        state_tx: broadcast::Sender<()>,
        token_file: PathBuf,
        token_rx: broadcast::Receiver<()>,
    ) -> Self {
        Self {
            config,
            playback_state,
            token_file,
            state_tx,
            token_rx: Some(token_rx),
        }
    }

    /// Check if a valid token exists (for web UI status)
    pub async fn has_valid_token(&self) -> bool {
        match token::load_token(&self.token_file).await {
            Some(token) => token.is_valid(),
            None => false,
        }
    }

    /// Send a Home Assistant persistent notification
    async fn send_ha_notification(&self, title: &str, message: &str) {
        // Use HA Supervisor API to create notification
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

    pub async fn run(mut self) -> anyhow::Result<()> {
        let mut consecutive_errors: u32 = 0;
        let max_backoff_secs = 300;
        
        loop {
            // Check for token first - if present, try to connect
            let has_token = self.has_valid_token().await;
            let has_file = self.token_file.exists();
            
            if !has_token && self.config.spotify_username.is_empty() {
                // Check if we have a file but it's expired (can try refresh)
                if has_file {
                    debug!("Token file exists but may be expired, attempting refresh...");
                    match self.load_token().await {
                        Some(token) => {
                            // Try to refresh - this will connect and block until disconnect
                            match self.refresh_token(token).await {
                                Ok(()) => {
                                    info!("Connection ended, will reconnect...");
                                }
                                Err(e) => {
                                    error!("Failed to refresh expired token: {}", e);
                                    info!("Please reconnect via the web UI");
                                    self.run_demo_mode().await?;
                                    continue;
                                }
                            }
                            // After refresh+connect ends, go to reconnection logic
                            consecutive_errors = 0;
                            sleep(Duration::from_secs(5)).await;
                            continue;
                        }
                        None => {
                            info!("No valid token found in file, running in demo mode");
                            info!("Use the web UI to authenticate with Spotify");
                            self.run_demo_mode().await?;
                            continue;
                        }
                    }
                } else {
                    info!("No Spotify token configured, running in demo mode");
                    info!("Use the web UI to authenticate with Spotify");
                    // Run demo mode until token appears
                    self.run_demo_mode().await?;
                    // Demo mode returned (token appeared), continue to connection
                    continue;
                }
            }
            
            info!("Attempting to connect to Spotify...");
            
            match self.attempt_connection().await {
                Ok(()) => {
                    warn!("Spotify connection ended cleanly, will reconnect...");
                    // Reset error count on successful connection
                    if consecutive_errors > 0 {
                        debug!("Resetting consecutive error count after successful connection");
                        consecutive_errors = 0;
                    }
                }
                Err(e) => {
                    let err_msg = format!("{}", e);
                    if err_msg.contains("Connection to Spotify server closed")
                        || err_msg.contains("WebSocket")
                        || err_msg.contains("timed out") {
                        warn!("Spotify connection lost ({}), will reconnect...", err_msg);
                    } else {
                        error!("Connection error: {}", e);
                    }
                    consecutive_errors += 1;

                    // Exponential backoff: 5s, 10s, 20s, 40s, 80s, then cap at 300s (5min)
                    let backoff_secs = std::cmp::min(
                        5 * 2u64.saturating_pow(consecutive_errors.saturating_sub(1).into()),
                        max_backoff_secs
                    );
                    warn!("Waiting {} seconds before reconnection attempt (error count: {})...",
                        backoff_secs, consecutive_errors);
                    sleep(Duration::from_secs(backoff_secs)).await;
                    continue;
                }
            }

            // Standard reconnection delay after clean connection
            sleep(Duration::from_secs(5)).await;
        }
    }

    async fn attempt_connection(&mut self) -> anyhow::Result<()> {
        if let Some(token) = self.load_token().await {
            debug!("Found existing OAuth token");

            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs();

            if token.expires_at > now + 300 {
                debug!("Token is still valid, using it to connect");
                match self.connect_with_token(token).await {
                    Ok(()) => return Ok(()),
                    Err(e) => {
                        warn!("Failed to connect with stored token: {}", e);
                        // Return the error to trigger retry with backoff
                        return Err(e);
                    }
                }
            } else {
                info!("Token is expired or expiring soon, will try to refresh");
                match self.refresh_token(token).await {
                    Ok(()) => return Ok(()),
                    Err(e) => {
                        error!("Failed to refresh token: {}", e);
                        // Return the error so main loop can handle it
                        return Err(e);
                    }
                }
            }
        }

        info!("No token file found, starting OAuth authentication...");
        match self.run_oauth_auth().await {
            Ok(()) => Ok(()),
            Err(e) => {
                error!("OAuth authentication failed: {}", e);
                Err(e)
            }
        }
    }

    async fn load_token(&self) -> Option<Token> {
        match token::load_token(&self.token_file).await {
            Some(token) => {
                debug!("Loaded token from {}", self.token_file.display());
                Some(token)
            }
            None => None,
        }
    }

    async fn save_token(&self, token: &Token) -> anyhow::Result<()> {
        // Daemon saves token without notification (no need to notify itself)
        token::save_token(&self.token_file, token, None).await
    }

    async fn connect_with_token(&mut self, token: Token) -> anyhow::Result<()> {
        let credentials = Credentials::with_access_token(&token.access_token);
        
        let session_config = SessionConfig::default();
        let session = Session::new(session_config, None);
        
        match session.connect(credentials, false).await {
            Ok(()) => {
                debug!("Connected to Spotify! Starting media monitoring...");
                self.run_media_monitor(session).await
            }
            Err(e) => {
                error!("Failed to connect with stored token: {}", e);
                Err(e.into())
            }
        }
    }

    async fn refresh_token(&mut self, token: Token) -> anyhow::Result<()> {
        info!("Refreshing OAuth token...");
        
        let oauth_client = OAuthClientBuilder::new(
            LIBRESPOT_CLIENT_ID,
            "http://127.0.0.1:5588/login",
            vec!["streaming"],
        )
        .build()
        .map_err(|e| anyhow::anyhow!("Failed to create OAuth client: {}", e))?;

        let new_token = match oauth_client
            .refresh_token_async(&token.refresh_token)
            .await
        {
            Ok(t) => t,
            Err(e) => {
                error!("Token refresh failed: {}", e);
                // Send HA notification about auth failure
                self.send_ha_notification(
                    "Greenroom Authentication Failed",
                    "Spotify token refresh failed. Please reconnect your account through the Greenroom web UI."
                ).await;
                return Err(anyhow::anyhow!("Token refresh failed: {}", e));
            }
        };

        debug!("Token refreshed successfully!");
        
        let stored_token = token_from_oauth(new_token);
        self.save_token(&stored_token).await?;
        
        self.connect_with_token(stored_token).await
    }

    async fn run_oauth_auth(&mut self) -> anyhow::Result<()> {
        info!("Starting OAuth authentication...");
        info!("A browser window should open automatically.");
        info!("Please log in to Spotify and authorize Greenroom.");
        
        let oauth_client = OAuthClientBuilder::new(
            LIBRESPOT_CLIENT_ID,
            "http://127.0.0.1:5588/login",
            vec!["streaming"],
        )
        .open_in_browser()
        .build()
        .map_err(|e| anyhow::anyhow!("Failed to create OAuth client: {}", e))?;

        let token = oauth_client
            .get_access_token_async()
            .await
            .map_err(|e| anyhow::anyhow!("OAuth authentication failed: {}", e))?;

        info!("Successfully obtained OAuth access token!");
        debug!("Token scopes: {:?}", token.scopes);

        let stored_token = token_from_oauth(token);
        self.save_token(&stored_token).await?;
        
        self.connect_with_token(stored_token).await
    }

    async fn run_media_monitor(&mut self, session: Session) -> anyhow::Result<()> {
        debug!("Starting dealer connection for real-time playback monitoring...");

        session.dealer().start().await
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

        let put_state_request = create_join_cluster_request(&session, &self.config.device_name);
        session.spclient()
            .put_connect_state_request(&put_state_request)
            .await
            .map_err(|e| anyhow::anyhow!("Failed to register in cluster: {}", e))?;
        
        debug!("Registered in cluster! Subscribing to state updates...");

        let mut cluster_updates = session
            .dealer()
            .listen_for("hm://connect-state/v1/cluster", Message::from_raw::<ClusterUpdate>)
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to cluster: {}", e))?;

        // Also keep the connection_id stream open as a WebSocket health indicator
        let mut connection_id_stream = session
            .dealer()
            .listen_for("hm://pusher/v1/connections/", extract_connection_id)
            .map_err(|e| anyhow::anyhow!("Failed to re-subscribe to connection_id: {}", e))?;

        debug!("Subscribed to cluster updates! Monitoring playback from other Spotify devices...");

        // Try to fetch current playback state immediately after connecting
        // This helps us show the current track instead of waiting for the next update
        match self.request_current_state(&session).await {
            Ok(()) => debug!("Requested current playback state"),
            Err(e) => debug!("Could not request current state: {}", e),
        }

        // Only reset to "Waiting" if we don't have current playback info
        let has_current_info = {
            let state = self.playback_state.read().await;
            state.track.is_some() && state.track.as_ref().unwrap() != "Waiting for playback..."
        };

        if !has_current_info {
            let mut state = self.playback_state.write().await;
            state.track = Some("Waiting for playback...".to_string());
            state.artist = Some("Monitor active".to_string());
            state.is_playing = false;
            state.is_idle = true;
            state.volume = 0.0;
            state.active_device_id = None;
        }

        let playback_state = self.playback_state.clone();
        let session_clone = session.clone();
        let state_tx = self.state_tx.clone();

        info!("Greenroom monitor active - tracking Spotify playback");

        let mut last_update = Instant::now();
        let mut consecutive_errors = 0u32;

        loop {
            tokio::select! {
                cluster_result = cluster_updates.next() => {
                    match cluster_result {
                        Some(Ok(cluster_update)) => {
                            let elapsed = last_update.elapsed();
                            if elapsed > Duration::from_secs(180) {
                                debug!("Received update after {}s silence", elapsed.as_secs());
                            }
                            last_update = Instant::now();
                            // Reset error count on successful update
                            if consecutive_errors > 0 {
                                debug!("Resetting error count after successful update");
                                consecutive_errors = 0;
                            }
                            self.handle_cluster_update(&cluster_update, &playback_state, &session_clone, &state_tx).await;
                        }
                        Some(Err(e)) => {
                            error!("Error receiving cluster update: {}", e);
                            // Check if it's a WebSocket/protocol error that killed the connection
                            let err_str = format!("{}", e);
                            if err_str.contains("WebSocket") || err_str.contains("Connection reset") {
                                warn!("WebSocket error detected, forcing reconnection");
                                return Err(anyhow::anyhow!("WebSocket connection failed: {}", e));
                            }
                        }
                        None => {
                            warn!("Spotify cluster update stream ended - connection to server closed");
                            return Err(anyhow::anyhow!("Connection to Spotify server closed"));
                        }
                    }
                }
                // Monitor connection_id stream as WebSocket health indicator
                connection_result = connection_id_stream.next() => {
                    match connection_result {
                        Some(Ok(_id)) => {
                            // Connection ID messages indicate WebSocket is alive
                            // This works even when no playback updates are coming
                            debug!("WebSocket alive - received connection ID message");
                        }
                        Some(Err(e)) => {
                            warn!("Connection ID stream error: {}", e);
                            return Err(anyhow::anyhow!("Connection health check failed: {}", e));
                        }
                        None => {
                            warn!("Connection ID stream ended - WebSocket connection lost");
                            return Err(anyhow::anyhow!("WebSocket connection closed"));
                        }
                    }
                }
            }
        }
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
                let track_uri = player_state.track.as_ref()
                    .map(|t| t.uri.clone())
                    .unwrap_or_default();
                let position_ms = player_state.position_as_of_timestamp;
                let duration_ms = player_state.duration as u32;
                let media_content_id = Some(player_state.context_uri.clone())
                    .filter(|s| !s.is_empty());
                
                let shuffle = player_state.options.as_ref()
                    .map(|o| o.shuffling_context)
                    .unwrap_or(false);
                let repeat = player_state.options.as_ref()
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
                    cluster.device.get(active_device_id)
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
                    SpotifyId::from_base62(id_str).ok()
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
                            
                            artist_name = track.artists.first()
                                .map(|artist| artist.name.clone())
                                .unwrap_or_else(|| "Unknown Artist".to_string());

                            album_name = Some(track.album.name.clone());

                            artwork_url = track.album.covers.first()
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
                    state.track = Some(track_name.clone());
                    state.artist = Some(artist_name.clone());
                    state.album = album_name;
                    state.artwork_url = artwork_url;
                    state.is_playing = is_playing;
                    state.is_idle = is_idle;
                    state.volume = volume;
                    state.is_volume_muted = is_volume_muted;
                    state.position_ms = position_ms as u32;
                    state.duration_ms = duration_ms;
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

    /// Request current playback state from Spotify after connecting.
    /// This helps us get the current track immediately instead of waiting for the next update.
    async fn request_current_state(&self, session: &Session) -> anyhow::Result<()> {
        // In the Connect protocol, we can request the current state by sending
        // a PUT state request with the current device state. This triggers Spotify
        // to send us the current cluster state including active playback.
        debug!("Requesting current playback state from Spotify...");

        // Create a state request that asks for current state without changing anything
        let put_state_request = create_join_cluster_request(session, &self.config.device_name);

        // Send the request - this should trigger a cluster update response
        match session.spclient()
            .put_connect_state_request(&put_state_request)
            .await {
            Ok(_bytes) => {
                debug!("Sent state request, waiting for cluster update...");
                // Give Spotify a moment to respond
                tokio::time::sleep(Duration::from_millis(500)).await;
                Ok(())
            }
            Err(e) => {
                Err(anyhow::anyhow!("Failed to request current state: {}", e))
            }
        }
    }

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

        // Take the token receiver so we can listen for notifications
        let mut token_rx = self.token_rx.take()
            .ok_or_else(|| anyhow::anyhow!("Token receiver already consumed"))?;

        // Keep running but wait for either token notification or periodic check
        loop {
            tokio::select! {
                // Wait for notification from web UI that a new token was saved
                result = token_rx.recv() => {
                    match result {
                        Ok(()) => {
                            info!("Received token notification from web UI!");
                            // Token was saved, verify it and return to trigger reconnection
                            if self.has_valid_token().await {
                                info!("New token is valid, attempting to connect...");
                                return Ok(());
                            } else {
                                warn!("Token notification received but token not valid yet, continuing demo mode");
                            }
                        }
                        Err(broadcast::error::RecvError::Closed) => {
                            // Channel closed, fall back to polling
                            warn!("Token notification channel closed, falling back to polling");
                        }
                        Err(broadcast::error::RecvError::Lagged(_)) => {
                            // Missed some notifications, check token anyway
                            debug!("Token notification channel lagged, checking token...");
                            if self.has_valid_token().await {
                                info!("Token detected after lagged notification!");
                                return Ok(());
                            }
                        }
                    }
                }
                // Periodic fallback check every 10 seconds
                _ = tokio::time::sleep(Duration::from_secs(10)) => {
                    if self.has_valid_token().await {
                        info!("Token detected via polling! Attempting to connect...");
                        return Ok(());
                    }
                }
            }
        }
    }
}

fn extract_connection_id(msg: Message) -> Result<String, librespot_core::Error> {
    let connection_id = msg
        .headers
        .get("Spotify-Connection-Id")
        .ok_or_else(|| librespot_core::Error::invalid_argument("Missing Spotify-Connection-Id header"))?;
    Ok(connection_id.to_owned())
}

fn create_join_cluster_request(session: &Session, device_name: &str) -> PutStateRequest {
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
            can_be_player: true,
            needs_full_player_state: true,
            is_observable: true,
            is_controllable: true,
            hidden: false,
            supports_gzip_pushes: true,
            supports_logout: false,
            supported_types: vec![
                "audio/episode".into(),
                "audio/track".into(),
            ],
            supports_playlist_v2: true,
            supports_transfer_command: true,
            supports_command_request: true,
            supports_set_options_command: true,
            is_voice_enabled: false,
            restrict_to_local: false,
            connect_disabled: false,
            supports_rename: false,
            supports_external_episodes: false,
            supports_set_backend_metadata: false,
            supports_dj: false,
            supports_rooms: false,
            supported_audio_quality: EnumOrUnknown::new(AudioQuality::VERY_HIGH),
            command_acks: true,
            ..Default::default()
        }),
        ..Default::default()
    };

    PutStateRequest {
        member_type: EnumOrUnknown::new(MemberType::CONNECT_STATE),
        put_state_reason: EnumOrUnknown::new(PutStateReason::NEW_DEVICE),
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
    use std::io::Write;
    use tempfile::NamedTempFile;

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
        // Create a temp file with a token that expires in 1 hour
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
        // Create minimal client to test with
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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        assert!(client.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_expired_token() {
        // Create a temp file with a token that expired 1 hour ago
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now - 3600);
        let json = serde_json::to_string(&token).unwrap();

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();

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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        // Should return false for expired token
        assert!(!client.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_near_expiry() {
        // Create a temp file with a token that expires in 2 minutes (within 5-min buffer)
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 120); // 2 minutes
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        // Should return false because it's within the 5-min buffer
        assert!(!client.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_has_valid_token_with_missing_file() {
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
        let (_, token_rx) = broadcast::channel(1);

        // Use a non-existent path
        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            PathBuf::from("/nonexistent/path/token.json"),
            token_rx,
        );

        assert!(!client.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_load_token_with_valid_file() {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        let json = serde_json::to_string(&token).unwrap();
        
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();
        
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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        let loaded = client.load_token().await;
        assert!(loaded.is_some());
        let loaded = loaded.unwrap();
        assert_eq!(loaded.access_token, "test_access_token");
        assert_eq!(loaded.refresh_token, "test_refresh_token");
    }

    #[tokio::test]
    async fn test_startup_has_file_but_expired_should_try_refresh() {
        // This test verifies the startup logic: when has_valid_token returns false
        // but file exists, we should try to refresh
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now - 3600); // Expired 1 hour ago
        let json = serde_json::to_string(&token).unwrap();

        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(json.as_bytes()).unwrap();

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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config.clone(),
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        // Verify the conditions that trigger the refresh path
        let has_token = client.has_valid_token().await;
        let has_file = client.token_file.exists();

        assert!(!has_token, "Token should be expired");
        assert!(has_file, "Token file should exist");

        // This combination should trigger the refresh attempt in run()
        // has_token = false, has_file = true, spotify_username is empty
        assert!(config.spotify_username.is_empty());
    }

    #[tokio::test]
    async fn test_has_valid_token_with_invalid_json() {
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(b"not valid json").unwrap();
        
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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        // Should handle invalid JSON gracefully
        assert!(!client.has_valid_token().await);
    }

    #[tokio::test]
    async fn test_load_token_with_invalid_json() {
        let mut temp_file = NamedTempFile::new().unwrap();
        temp_file.write_all(b"not valid json").unwrap();

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
        let (_, token_rx) = broadcast::channel(1);

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            temp_file.path().to_path_buf(),
            token_rx,
        );

        // Should return None for invalid JSON
        assert!(client.load_token().await.is_none());
    }

    #[tokio::test]
    async fn test_save_token_creates_file() {
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
        let (_, token_rx) = broadcast::channel(1);

        // Create temp directory for token file
        let temp_dir = tempfile::tempdir().unwrap();
        let token_path = temp_dir.path().join("greenroom_token.json");

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            token_path.clone(),
            token_rx,
        );
        
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);
        
        // Save the token
        client.save_token(&token).await.unwrap();
        
        // Verify file exists and contains expected data
        assert!(token_path.exists());
        let contents = tokio::fs::read_to_string(&token_path).await.unwrap();
        let loaded: Token = serde_json::from_str(&contents).unwrap();
        assert_eq!(loaded.access_token, "test_access_token");
        assert_eq!(loaded.refresh_token, "test_refresh_token");
        assert_eq!(loaded.expires_at, token.expires_at);
    }

    #[tokio::test]
    async fn test_save_token_creates_parent_dirs() {
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
        let (_, token_rx) = broadcast::channel(1);

        // Create temp directory with nested path
        let temp_dir = tempfile::tempdir().unwrap();
        let nested_path = temp_dir.path().join("data").join("subdir").join("token.json");

        let client = SpotifyClient::new(
            config,
            Arc::new(RwLock::new(PlaybackState::default())),
            state_tx,
            nested_path.clone(),
            token_rx,
        );

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let token = create_test_token(now + 3600);

        // Save should create parent directories
        client.save_token(&token).await.unwrap();

        assert!(nested_path.exists());
    }

    /// Test that backoff calculation works correctly for consecutive errors
    #[test]
    fn test_backoff_calculation() {
        let max_backoff_secs = 300u64;

        // Test backoff progression: 5s, 10s, 20s, 40s, 80s, 160s, 300s (cap)
        let test_cases: Vec<(u32, u64)> = vec![
            (0, 5),   // 5 * 2^0 = 5
            (1, 5),   // 5 * 2^0 = 5
            (2, 10),  // 5 * 2^1 = 10
            (3, 20),  // 5 * 2^2 = 20
            (4, 40),  // 5 * 2^3 = 40
            (5, 80),  // 5 * 2^4 = 80
            (6, 160), // 5 * 2^5 = 160
            (7, 300), // 5 * 2^6 = 320, capped at 300
            (10, 300), // Should still be capped
        ];

        for (consecutive_errors, expected) in test_cases {
            let consecutive_errors_u64: u64 = consecutive_errors.into();
            let power: u32 = consecutive_errors_u64.saturating_sub(1).try_into().unwrap_or(0);
            let backoff = std::cmp::min(
                5 * 2u64.saturating_pow(power),
                max_backoff_secs
            );
            assert_eq!(backoff, expected, "Backoff for {} consecutive errors should be {}s", consecutive_errors, expected);
        }
    }
}
