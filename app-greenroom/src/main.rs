mod librespot;
mod mqtt;
mod token;
mod web;

use std::sync::Arc;
use std::path::PathBuf;
use clap::Parser;
use tokio::sync::{RwLock, broadcast};
use tracing::{info, error};

use crate::librespot::SpotifyClient;
use crate::mqtt::MqttBridge;
use crate::web::{router, AppState};

/// Notify S6-overlay that the service is ready (only when running as addon).
fn notify_readiness() {
    use std::fs::OpenOptions;
    use std::io::Write;
    use std::path::Path;

    // Only signal readiness when running in addon environment
    if !Path::new("/data/options.json").exists() {
        return;
    }

    // Write a newline to file descriptor 3 to signal readiness to S6
    match OpenOptions::new().write(true).open("/dev/fd/3") {
        Ok(mut fd) => {
            if let Err(e) = writeln!(fd) {
                tracing::debug!("Could not write readiness notification: {}", e);
            } else {
                tracing::debug!("Readiness notification sent to S6");
            }
        }
        Err(e) => {
            tracing::debug!("Could not open readiness notification fd: {}", e);
        }
    }
}

/// Greenroom - Spotify Connect device with MQTT discovery for Home Assistant
#[derive(Parser, Debug, Clone)]
#[command(name = "greenroom")]
#[command(about = "A Spotify Connect device that exposes media info to Home Assistant via MQTT")]
#[command(version)]
pub struct Cli {
    /// Spotify username/email (for OAuth login)
    #[arg(long, env = "SPOTIFY_USERNAME", help = "Spotify account username or email")]
    pub spotify_username: Option<String>,

    /// Device name shown in Spotify and Home Assistant
    #[arg(short, long, env = "DEVICE_NAME", default_value = "Greenroom", help = "Name shown in Spotify and Home Assistant")]
    pub device_name: String,

    /// MQTT broker host
    #[arg(long, env = "MQTT_HOST", default_value = "homeassistant.local", help = "MQTT broker hostname or IP")]
    pub mqtt_host: String,

    /// MQTT broker port
    #[arg(long, env = "MQTT_PORT", default_value = "1883", help = "MQTT broker port")]
    pub mqtt_port: u16,

    /// MQTT username (optional)
    #[arg(long, env = "MQTT_USERNAME", help = "MQTT broker username")]
    pub mqtt_username: Option<String>,

    /// MQTT password (optional)
    #[arg(long, env = "MQTT_PASSWORD", help = "MQTT broker password")]
    pub mqtt_password: Option<String>,

    /// MQTT device ID for unique topics
    #[arg(long, env = "MQTT_DEVICE_ID", default_value = "greenroom", help = "MQTT device ID (used in topic names)")]
    pub mqtt_device_id: String,

    /// Log level (trace, debug, info, warn, error)
    #[arg(short, long, env = "RUST_LOG", default_value = "info", help = "Log level: trace, debug, info, warn, error")]
    pub log_level: String,

    /// Web UI port for OAuth flow
    #[arg(long, env = "GREENROOM_WEB_PORT", default_value = "8099", help = "Port for the web UI (used for OAuth)")]
    pub web_port: u16,
}

/// Shared state between Spotify client and MQTT bridge
#[derive(Debug, Clone, Default)]
pub struct PlaybackState {
    pub track: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    pub artwork_url: Option<String>,
    pub is_playing: bool,
    pub is_idle: bool,  // true when Spotify is open but no active playback
    pub volume: f32,  // 0.0 to 1.0
    pub is_volume_muted: bool,
    pub duration_ms: u32,
    pub position_ms: u32,
    pub media_content_id: Option<String>, // Spotify URI (context_uri)
    pub source: Option<String>,           // Current device name
    pub shuffle: bool,
    pub repeat: String, // "off", "context", "track"
    pub active_device_id: Option<String>,   // Device ID of the active Spotify device
    pub is_spotify_connected: bool,  // true when actively connected to Spotify WebSocket
}

#[derive(Clone)]
pub struct Config {
    pub spotify_username: String,
    pub device_name: String,
    pub mqtt_host: String,
    pub mqtt_port: u16,
    pub mqtt_username: Option<String>,
    pub mqtt_password: Option<String>,
    pub mqtt_device_id: String,
}

impl From<Cli> for Config {
    fn from(cli: Cli) -> Self {
        let username = cli.spotify_username.unwrap_or_default();
        
        Self {
            spotify_username: username,
            device_name: cli.device_name,
            mqtt_host: cli.mqtt_host,
            mqtt_port: cli.mqtt_port,
            mqtt_username: cli.mqtt_username,
            mqtt_password: cli.mqtt_password,
            mqtt_device_id: cli.mqtt_device_id,
        }
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Parse CLI arguments
    let cli = Cli::parse();

    // Initialize logging with smart defaults
    // Always suppress noisy librespot internals regardless of RUST_LOG setting
    let base_filter = std::env::var("RUST_LOG")
        .unwrap_or_else(|_| format!("info,greenroom={}", cli.log_level));
    
    // Append our mandatory filters to suppress mercury noise
    let filter = format!(
        "{},librespot_core::mercury=off,librespot_core::session=info",
        base_filter
    );
    
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::new(filter))
        .init();

    info!("Starting Greenroom - Spotify Connect for Home Assistant via MQTT");
    info!("Device name: {}", cli.device_name);
    
    if cli.spotify_username.is_none() {
        info!("No Spotify credentials provided - will run in demo mode");
    }

    // Convert CLI args to config
    let config: Config = cli.clone().into();

    // Token file path - use /data in add-on environment, otherwise local file
    let token_file = std::env::var("TOKEN_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            // Check for /data/options.json which is only present in HA add-on environment
            if std::path::Path::new("/data/options.json").exists() {
                PathBuf::from("/data/greenroom_token.json")
            } else {
                PathBuf::from("greenroom_token.json")
            }
        });

    // Determine initial state based on whether we have auth credentials
    let has_token = token_file.exists() && 
        std::fs::read_to_string(&token_file)
            .ok()
            .and_then(|contents| serde_json::from_str::<serde_json::Value>(&contents).ok())
            .map(|v| v.get("access_token").is_some())
            .unwrap_or(false);

    let (initial_track, initial_artist) = if has_token {
        ("Waiting for playback...".to_string(), "Greenroom".to_string())
    } else {
        ("Not Connected".to_string(), "Open the Greenroom web UI to connect Spotify".to_string())
    };

    // Shared state - initialize with idle status so sensor shows "idle" not "paused"
    let initial_state = PlaybackState {
        track: Some(initial_track),
        artist: Some(initial_artist),
        is_idle: true,
        ..Default::default()
    };
    let playback_state = Arc::new(RwLock::new(initial_state));
    
    // State change notification channel (for pushing updates to MQTT)
    let (state_tx, state_rx) = broadcast::channel(16);

    // Token notification channel (for notifying daemon of new tokens from web UI)
    let (token_tx, token_rx) = broadcast::channel(1);

    // Reconnect signal channel (for triggering immediate reconnection from web UI)
    let (reconnect_tx, reconnect_rx) = broadcast::channel(1);

    // Start web server for OAuth UI
    let app_state = AppState::new(
        config.clone(),
        playback_state.clone(),
        token_file.clone(),
        token_tx,
        reconnect_tx,
    );
    let web_app = router(app_state);
    let web_port = cli.web_port;

    // Bind the listener first, then notify readiness, then serve
    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], web_port));
    info!("Starting web server on http://{}", addr);
    let listener = match tokio::net::TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            error!("Failed to bind web server: {}", e);
            return Err(e.into());
        }
    };

    // Notify S6 that we're ready (only in addon environment)
    notify_readiness();

    let web_server = async move {
        axum::serve(listener, web_app)
            .await
            .map_err(|e| anyhow::anyhow!("Web server error: {}", e))
    };

    // Start MQTT bridge
    let mqtt_bridge = MqttBridge::new(
        config.clone(),
        playback_state.clone(),
        state_rx,
    );

    // Start Spotify client
    let spotify_client = SpotifyClient::new(
        config.clone(),
        playback_state.clone(),
        state_tx,
        token_file,
        token_rx,
        reconnect_rx,
    );

    // Set up shutdown signal handler
    let shutdown_signal = async {
        match tokio::signal::ctrl_c().await {
            Ok(()) => {
                info!("Received Ctrl+C, shutting down...");
            }
            Err(e) => {
                error!("Failed to listen for Ctrl+C: {}", e);
            }
        }
    };

    // Run all three concurrently, with shutdown signal
    tokio::select! {
        _ = shutdown_signal => {
            info!("Shutdown signal received, exiting...");
        }
        result = web_server => {
            if let Err(e) = result {
                error!("Web server error: {}", e);
            }
        }
        result = mqtt_bridge.run() => {
            if let Err(e) = result {
                error!("MQTT bridge error: {}", e);
            }
        }
        result = spotify_client.run() => {
            if let Err(e) = result {
                error!("Spotify client error: {}", e);
            }
        }
    }

    Ok(())
}
