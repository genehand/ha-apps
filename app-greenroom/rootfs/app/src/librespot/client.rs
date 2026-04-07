use std::sync::Arc;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH, Duration};
use tokio::sync::{RwLock, broadcast};
use tokio::time::sleep;
use tracing::{info, error, debug, warn};
use serde::{Serialize, Deserialize};
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

/// Stored OAuth token with expiration info
#[derive(Debug, Clone, Serialize, Deserialize)]
struct StoredToken {
    access_token: String,
    refresh_token: String,
    expires_at: u64, // Unix timestamp
    scopes: Vec<String>,
}

impl From<OAuthToken> for StoredToken {
    fn from(token: OAuthToken) -> Self {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let expires_at = now + 3600;
        
        Self {
            access_token: token.access_token,
            refresh_token: token.refresh_token,
            expires_at,
            scopes: token.scopes,
        }
    }
}

pub struct SpotifyClient {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    token_file: PathBuf,
    state_tx: broadcast::Sender<()>,
}

impl SpotifyClient {
    pub fn new(config: Config, playback_state: Arc<RwLock<PlaybackState>>, state_tx: broadcast::Sender<()>) -> Self {
        let token_file = std::env::var("TOKEN_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("greenroom_token.json"));
        
        Self {
            config,
            playback_state,
            token_file,
            state_tx,
        }
    }

    pub async fn run(self) -> anyhow::Result<()> {
        info!("Starting Greenroom Spotify client (media monitor mode)...");
        info!("Note: This monitors playback from other devices, not a Connect device itself");

        // Check if credentials are configured
        if self.config.spotify_username.is_empty() {
            info!("No Spotify username configured, running in demo mode");
            return self.run_demo_mode().await;
        }

        // Run connection loop with reconnection support
        let mut consecutive_errors: u32 = 0;
        let max_backoff_secs = 300; // Cap at 5 minutes
        
        loop {
            info!("Attempting to connect to Spotify...");
            
            match self.attempt_connection().await {
                Ok(()) => {
                    // Connection ended normally (shouldn't happen in normal operation)
                    warn!("Spotify connection ended, will reconnect...");
                }
                Err(e) => {
                    let err_msg = format!("{}", e);
                    if err_msg.contains("Connection to Spotify server closed") {
                        warn!("Spotify server connection closed, will reconnect...");
                    } else {
                        error!("Connection error: {}", e);
                    }
                    consecutive_errors += 1;
                    
                    // Calculate backoff: 5s, 10s, 20s, 40s, 80s... capped at max_backoff_secs
                    let backoff_secs = std::cmp::min(5 * 2u64.saturating_pow(consecutive_errors.saturating_sub(1).into()), max_backoff_secs);
                    warn!("Waiting {} seconds before reconnection attempt...", backoff_secs);
                    sleep(Duration::from_secs(backoff_secs)).await;
                    continue;
                }
            }
            
            // Reset error counter on successful completion (clean disconnect)
            consecutive_errors = 0;
            
            // Wait before attempting reconnection
            sleep(Duration::from_secs(5)).await;
        }
    }

    /// Attempt a single connection to Spotify
    async fn attempt_connection(&self) -> anyhow::Result<()> {
        // Try to load existing token
        if let Some(token) = self.load_token().await {
            info!("Found existing OAuth token");
            
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs();
            
            if token.expires_at > now + 300 {
                info!("Token is still valid, using it to connect");
                match self.connect_with_token(token).await {
                    Ok(()) => return Ok(()),
                    Err(e) => {
                        error!("Failed to connect with stored token: {}", e);
                        info!("Will try to refresh or re-authenticate");
                    }
                }
            } else {
                info!("Token is expired or expiring soon, will try to refresh");
                match self.refresh_token(token).await {
                    Ok(()) => return Ok(()),
                    Err(e) => {
                        error!("Failed to refresh token: {}", e);
                        info!("Will re-authenticate");
                    }
                }
            }
        }

        // No valid token, do OAuth flow
        info!("No valid token found, starting OAuth authentication...");
        match self.run_oauth_auth().await {
            Ok(()) => Ok(()),
            Err(e) => {
                error!("OAuth authentication failed: {}", e);
                Err(e)
            }
        }
    }

    async fn load_token(&self) -> Option<StoredToken> {
        if !self.token_file.exists() {
            return None;
        }
        
        match tokio::fs::read_to_string(&self.token_file).await {
            Ok(contents) => {
                match serde_json::from_str::<StoredToken>(&contents) {
                    Ok(token) => {
                        debug!("Loaded token from {}", self.token_file.display());
                        Some(token)
                    }
                    Err(e) => {
                        error!("Failed to parse token file: {}", e);
                        None
                    }
                }
            }
            Err(e) => {
                error!("Failed to read token file: {}", e);
                None
            }
        }
    }

    async fn save_token(&self, token: &StoredToken) -> anyhow::Result<()> {
        let contents = serde_json::to_string_pretty(token)?;
        tokio::fs::write(&self.token_file, contents).await?;
        info!("Saved OAuth token to {}", self.token_file.display());
        Ok(())
    }

    async fn connect_with_token(&self, token: StoredToken) -> anyhow::Result<()> {
        let credentials = Credentials::with_access_token(&token.access_token);
        
        let session_config = SessionConfig::default();
        let session = Session::new(session_config, None);
        
        match session.connect(credentials, false).await {
            Ok(()) => {
                info!("Connected to Spotify! Starting media monitoring...");
                self.run_media_monitor(session).await
            }
            Err(e) => {
                error!("Failed to connect with stored token: {}", e);
                Err(e.into())
            }
        }
    }

    async fn refresh_token(&self, token: StoredToken) -> anyhow::Result<()> {
        info!("Refreshing OAuth token...");
        
        let oauth_client = OAuthClientBuilder::new(
            "65b708073fc0480ea92a077233ca87bd",
            "http://127.0.0.1:5588/login",
            vec!["streaming"],  // Only need 'streaming' for librespot session, no Web API scopes
        )
        .build()
        .map_err(|e| anyhow::anyhow!("Failed to create OAuth client: {}", e))?;

        let new_token = oauth_client
            .refresh_token_async(&token.refresh_token)
            .await
            .map_err(|e| anyhow::anyhow!("Token refresh failed: {}", e))?;

        info!("Token refreshed successfully!");
        
        let stored_token = StoredToken::from(new_token);
        self.save_token(&stored_token).await?;
        
        self.connect_with_token(stored_token).await
    }

    async fn run_oauth_auth(&self) -> anyhow::Result<()> {
        info!("Starting OAuth authentication...");
        info!("A browser window should open automatically.");
        info!("Please log in to Spotify and authorize Greenroom.");
        
        let oauth_client = OAuthClientBuilder::new(
            "65b708073fc0480ea92a077233ca87bd",
            "http://127.0.0.1:5588/login",
            vec!["streaming"],  // 'streaming' required for librespot, no Web API scopes needed
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

        let stored_token = StoredToken::from(token);
        self.save_token(&stored_token).await?;
        
        self.connect_with_token(stored_token).await
    }

    async fn run_media_monitor(&self, session: Session) -> anyhow::Result<()> {
        info!("Greenroom media monitor active!");
        info!("Starting dealer connection for real-time playback monitoring...");

        // Start the dealer connection
        session.dealer().start().await
            .map_err(|e| anyhow::anyhow!("Failed to start dealer: {}", e))?;
        
        info!("Dealer connected! Waiting for connection ID...");

        // Subscribe to connection_id first (required before PutStateRequest)
        let mut connection_id_stream = session
            .dealer()
            .listen_for("hm://pusher/v1/connections/", extract_connection_id)
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to connection_id: {}", e))?;

        // Wait for the connection_id
        let connection_id = match connection_id_stream.next().await {
            Some(Ok(id)) => {
                info!("Received connection ID: {}", id);
                id
            }
            Some(Err(e)) => return Err(anyhow::anyhow!("Failed to get connection ID: {}", e)),
            None => return Err(anyhow::anyhow!("Connection ID stream ended unexpectedly")),
        };

        // Set connection_id on session (required for PutStateRequest)
        session.set_connection_id(&connection_id);
        
        info!("Registering device in cluster...");

        // Create and send PutStateRequest to join the cluster (Spirc Hello)
        let put_state_request = create_join_cluster_request(&session, &self.config.device_name);
        session.spclient()
            .put_connect_state_request(&put_state_request)
            .await
            .map_err(|e| anyhow::anyhow!("Failed to register in cluster: {}", e))?;
        
        info!("Registered in cluster! Subscribing to state updates...");

        // Subscribe to cluster updates via the dealer
        let mut cluster_updates = session
            .dealer()
            .listen_for("hm://connect-state/v1/cluster", Message::from_raw::<ClusterUpdate>)
            .map_err(|e| anyhow::anyhow!("Failed to subscribe to cluster: {}", e))?;

        info!("Subscribed to cluster updates! Monitoring playback from other Spotify devices...");

        // Set initial state
        {
            let mut state = self.playback_state.write().await;
            state.track = Some("Waiting for playback...".to_string());
            state.artist = Some("Monitor active".to_string());
            state.is_playing = false;
            state.is_idle = true;  // No active playback yet
            state.volume = 0.0;
        }

        let playback_state = self.playback_state.clone();
        let session_clone = session.clone();
        let state_tx = self.state_tx.clone();

        // Process cluster updates in real-time
        while let Some(cluster_result) = cluster_updates.next().await {
            match cluster_result {
                Ok(cluster_update) => {
                    debug!("Received cluster update");
                    
                    // Extract cluster data using as_ref() since MessageField<T> wraps Option<Box<T>>
                    if let Some(cluster) = cluster_update.cluster.as_ref() {
                        let active_device_id = &cluster.active_device_id;
                        debug!("Active device: {}", active_device_id);

                        // Get player state from cluster (also a MessageField)
                        if let Some(player_state) = cluster.player_state.as_ref() {
                            // Spotify protocol has both is_playing AND is_paused - check both!
                            let is_playing = player_state.is_playing && !player_state.is_paused;
                            let is_paused = player_state.is_paused;
                            let track_uri = player_state.track.as_ref()
                                .map(|t| t.uri.clone())
                                .unwrap_or_default();
                            let position_ms = player_state.position_as_of_timestamp;
                            let duration_ms = player_state.duration as u32;
                            let media_content_id = Some(player_state.context_uri.clone())
                                .filter(|s| !s.is_empty());
                            
                            // Get shuffle/repeat from options
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
                            
                            // Determine if we're idle (no active device)
                            let is_idle = active_device_id.is_empty();

                            // Get volume and device name from active device's DeviceInfo
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

                            // Parse track URI to get Spotify ID
                            let track_uri_obj = if track_uri.starts_with("spotify:track:") {
                                let id_str = &track_uri[14..]; // Remove "spotify:track:" prefix
                                SpotifyId::from_base62(id_str).ok()
                                    .map(|id| librespot_core::SpotifyUri::Track { id })
                            } else {
                                None
                            };

                            // Fetch track metadata if we have a valid track URI
                            let track_name: String;
                            let artist_name: String;
                            let album_name: Option<String>;
                            let artwork_url: Option<String>;
                            
                            if let Some(uri) = track_uri_obj.as_ref() {
                                match Track::get(&session_clone, uri).await {
                                    Ok(track) => {
                                        track_name = track.name.clone();
                                        
                                        // Get primary artist name (track.artists is Artists(Vec<Artist>))
                                        artist_name = track.artists.first()
                                            .map(|artist| artist.name.clone())
                                            .unwrap_or_else(|| "Unknown Artist".to_string());

                                        // Get album name (track.album is Album directly)
                                        album_name = Some(track.album.name.clone());

                                        // Get artwork URL from album covers
                                        // Spotify image URL format: https://i.scdn.co/image/{file_id}
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

                            // Update shared playback state
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
                            
                            // Notify MQTT bridge to push state update
                            let _ = state_tx.send(());

                            info!(
                                "Playback update: {} - {} (playing: {}, volume: {}%)",
                                track_name,
                                artist_name,
                                is_playing,
                                (volume * 100.0) as u32
                            );
                        } else {
                            debug!("No player state in cluster update");
                            
                            // No active playback - we're idle
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
                Err(e) => {
                    error!("Error receiving cluster update: {}", e);
                }
            }
        }

        warn!("Spotify cluster update stream ended - connection to server closed");
        Err(anyhow::anyhow!("Connection to Spotify server closed"))
    }
        
    async fn run_demo_mode(self) -> anyhow::Result<()> {
        {
            let mut state = self.playback_state.write().await;
            state.track = Some("Not Connected".to_string());
            state.artist = Some("Configure Spotify credentials in add-on settings".to_string());
            state.album = Some("-".to_string());
            state.is_playing = false;
            state.is_idle = true;
            state.volume = 0.0;
        }

        loop {
            tokio::time::sleep(Duration::from_secs(30)).await;
        }
    }
}

/// Extract connection_id from dealer message
fn extract_connection_id(msg: Message) -> Result<String, librespot_core::Error> {
    let connection_id = msg
        .headers
        .get("Spotify-Connection-Id")
        .ok_or_else(|| librespot_core::Error::invalid_argument("Missing Spotify-Connection-Id header"))?;
    Ok(connection_id.to_owned())
}

/// Create a PutStateRequest to join the Spotify Connect cluster
fn create_join_cluster_request(session: &Session, device_name: &str) -> PutStateRequest {
    let device_info = DeviceInfo {
        can_play: true,
        volume: 32767, // 50% volume (0-65535)
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
