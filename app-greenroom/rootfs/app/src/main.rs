mod librespot;
mod mqtt;

use std::sync::Arc;
use clap::Parser;
use tokio::sync::{RwLock, broadcast};
use tracing::{info, error};

use crate::librespot::SpotifyClient;
use crate::mqtt::MqttBridge;

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

    // Shared state
    let playback_state = Arc::new(RwLock::new(PlaybackState::default()));
    
    // State change notification channel (for pushing updates to MQTT)
    let (state_tx, state_rx) = broadcast::channel(16);

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
    );

    // Run both concurrently
    tokio::select! {
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
