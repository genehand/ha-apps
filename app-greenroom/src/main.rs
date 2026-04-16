mod librespot;
mod mqtt;
mod state;
mod token;
mod web;

use clap::Parser;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::{error, info};

use crate::librespot::SpotifyClient;
use crate::mqtt::MqttBridge;
use crate::state::load_connection_state;
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
    #[arg(
        long,
        env = "SPOTIFY_USERNAME",
        help = "Spotify account username or email"
    )]
    pub spotify_username: Option<String>,

    /// Device name shown in Spotify and Home Assistant
    #[arg(
        short,
        long,
        env = "DEVICE_NAME",
        default_value = "Greenroom",
        help = "Name shown in Spotify and Home Assistant"
    )]
    pub device_name: String,

    /// MQTT broker host
    #[arg(
        long,
        env = "MQTT_HOST",
        default_value = "homeassistant.local",
        help = "MQTT broker hostname or IP"
    )]
    pub mqtt_host: String,

    /// MQTT broker port
    #[arg(
        long,
        env = "MQTT_PORT",
        default_value = "1883",
        help = "MQTT broker port"
    )]
    pub mqtt_port: u16,

    /// MQTT username (optional)
    #[arg(long, env = "MQTT_USERNAME", help = "MQTT broker username")]
    pub mqtt_username: Option<String>,

    /// MQTT password (optional)
    #[arg(long, env = "MQTT_PASSWORD", help = "MQTT broker password")]
    pub mqtt_password: Option<String>,

    /// MQTT device ID for unique topics
    #[arg(
        long,
        env = "MQTT_DEVICE_ID",
        default_value = "greenroom",
        help = "MQTT device ID (used in topic names)"
    )]
    pub mqtt_device_id: String,

    /// Log level (trace, debug, info, warn, error)
    #[arg(
        short,
        long,
        env = "RUST_LOG",
        default_value = "info",
        help = "Log level: trace, debug, info, warn, error"
    )]
    pub log_level: String,

    /// Web UI port for OAuth flow
    #[arg(
        long,
        env = "GREENROOM_WEB_PORT",
        default_value = "8099",
        help = "Port for the web UI (used for OAuth)"
    )]
    pub web_port: u16,
}

/// Runtime configuration derived from CLI args
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

/// Playback state shared between Spotify client and MQTT bridge
#[derive(Clone, Default)]
pub struct PlaybackState {
    pub track: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    pub artwork_url: Option<String>,
    pub is_playing: bool,
    pub is_idle: bool,
    pub volume: f64,
    pub is_spotify_connected: bool,
    pub media_position: Option<u32>,
    pub media_duration: Option<u32>,
    pub media_position_updated_at: Option<chrono::DateTime<chrono::Utc>>,
    pub media_content_id: Option<String>,
    pub source: Option<String>,
    pub shuffle: bool,
    pub repeat: String,
    pub is_muted: bool,
    pub active_device_id: Option<String>,
    /// Whether the Spotify client connection is enabled (controlled via MQTT switch)
    pub connection_enabled: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Parse CLI arguments
    let cli = Cli::parse();

    // Initialize logging with smart defaults
    // Always suppress noisy librespot internals regardless of RUST_LOG setting
    let base_filter =
        std::env::var("RUST_LOG").unwrap_or_else(|_| format!("info,greenroom={}", cli.log_level));

    // Append our mandatory filters to suppress mercury noise
    let filter = format!(
        "{},librespot_core::mercury=off,librespot_core::session=info",
        base_filter
    );

    // Use local time for timestamps in format: "2026-04-15 07:58:09"
    let time_format =
        time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]:[second]");

    // Initialize tracing subscriber
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_timer(tracing_subscriber::fmt::time::LocalTime::new(time_format))
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

    // Connection state file (persist the Active switch state)
    let connection_state_file = token_file
        .parent()
        .map(|p| p.join("greenroom_connection_state.json"))
        .unwrap_or_else(|| PathBuf::from("greenroom_connection_state.json"));

    // Determine initial state based on whether we have auth credentials
    let has_token = token_file.exists()
        && std::fs::read_to_string(&token_file)
            .ok()
            .and_then(|contents| serde_json::from_str::<serde_json::Value>(&contents).ok())
            .map(|v| v.get("access_token").is_some())
            .unwrap_or(false);

    let (initial_track, initial_artist) = if has_token {
        (
            "Waiting for playback...".to_string(),
            "Greenroom".to_string(),
        )
    } else {
        (
            "Not Connected".to_string(),
            "Open the Greenroom web UI to connect Spotify".to_string(),
        )
    };

    // Load persisted connection state (default to enabled if not found)
    let connection_state = load_connection_state(&connection_state_file).await;
    let connection_enabled = connection_state.map(|s| s.enabled).unwrap_or(true);
    info!(
        "Connection state loaded: {}",
        if connection_enabled {
            "enabled"
        } else {
            "disabled"
        }
    );

    // Shared state - initialize with idle status so sensor shows "idle" not "paused"
    let initial_state = PlaybackState {
        track: Some(initial_track),
        artist: Some(initial_artist),
        is_idle: true,
        connection_enabled, // Use persisted state
        ..Default::default()
    };
    let playback_state = Arc::new(RwLock::new(initial_state));

    // State change notification channel (for pushing updates to MQTT)
    let (state_tx, state_rx) = broadcast::channel(16);

    // Token notification channel (for notifying daemon of new tokens from web UI)
    let (token_tx, _token_rx) = broadcast::channel(1);

    // Connection control channel (for MQTT switch to control Spotify client)
    let (connection_tx, connection_rx) = broadcast::channel(4);

    // Start web server for OAuth UI
    let app_state = AppState::new(
        config.clone(),
        playback_state.clone(),
        token_file.clone(),
        token_tx.clone(),
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
        connection_tx,
        connection_state_file,
    );

    // Start Spotify client
    let spotify_client = SpotifyClient::new(
        config.clone(),
        playback_state.clone(),
        state_tx,
        token_file,
        token_tx,
        connection_rx,
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

    // Run all components concurrently, with shutdown signal
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
