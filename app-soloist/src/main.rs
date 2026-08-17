mod config;
mod mqtt;
mod soloist;
mod state;

use std::io::Write;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};
use tracing::{error, info};

use crate::config::{find_options_file, load_options_file, Cli, Config};
use crate::mqtt::MqttBridge;
use crate::soloist::{run_client, SoloistCommand, SoloistDaemon};
use crate::state::PlaybackState;

/// Notify S6-overlay that the service is ready (only when running as add-on).
fn notify_readiness() {
    if !Path::new("/data/options.json").exists() {
        return;
    }
    match std::fs::OpenOptions::new().write(true).open("/dev/fd/3") {
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

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Parse CLI args + env, then merge in the options file (add-on /data/options.json
    // or ./options.json for local runs) as fallback values.
    let (cli, matches) = Cli::parse_with_matches();
    let options = match find_options_file() {
        Some(path) => match load_options_file(&path) {
            Ok(options) => {
                info!("Loaded options from {}", path.display());
                options
            }
            Err(e) => {
                error!(
                    "Failed to parse {}: {}; using CLI/env only",
                    path.display(),
                    e
                );
                Default::default()
            }
        },
        None => Default::default(),
    };
    let config: Config = Config::from_cli_and_options(cli, &matches, options);

    let filter = std::env::var("RUST_LOG")
        .unwrap_or_else(|_| format!("info,soloist_bridge={}", config.log_level));

    let time_format =
        time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]:[second]");

    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_timer(tracing_subscriber::fmt::time::LocalTime::new(time_format))
        .init();

    let config: Config = config;
    info!("Starting Soloist bridge - Spotify playback device for Home Assistant via MQTT");
    info!("Device name: {}", config.device_name);
    info!("MQTT device ID: {}", config.mqtt_device_id);
    info!("Soloist data dir: {}", config.soloist_data_dir.display());
    if let Some(path) = &config.options_file {
        info!("Using options file: {}", path.display());
    }

    // Shared playback state
    let initial_state = PlaybackState {
        status: "idle".to_string(),
        device_name: config.device_name.clone(),
        ..Default::default()
    };
    let playback_state = Arc::new(RwLock::new(initial_state));

    // State change notification channel (soloist client -> MQTT bridge)
    let (state_tx, state_rx) = broadcast::channel(16);
    // Command channel (MQTT bridge -> soloist client)
    let (cmd_tx, cmd_rx) = mpsc::channel::<SoloistCommand>(64);

    // Spawn + supervise the soloist daemon (unless an external endpoint was given)
    let mut soloist_daemon_task = None;
    if config.soloist_ws_url.is_none() {
        match SoloistDaemon::new(&config, playback_state.clone(), state_tx.clone()) {
            Ok(daemon) => {
                soloist_daemon_task = Some(tokio::spawn(async move {
                    if let Err(e) = daemon.run().await {
                        error!("Soloist daemon supervisor failed: {}", e);
                    }
                }));
            }
            Err(e) => {
                error!("{}", e);
                error!("Exiting: cannot start without a soloist WebSocket endpoint.");
                return Err(e);
            }
        }
    } else {
        info!("SOLOIST_WS_URL set - connecting to external soloist, not spawning daemon");
    }

    // WebSocket client (reconnects forever)
    let client_task = tokio::spawn(run_client(
        config.soloist_ws_url.clone(),
        config.soloist_data_dir.clone(),
        playback_state.clone(),
        state_tx.clone(),
        cmd_rx,
    ));

    // MQTT bridge (reconnects forever)
    let mqtt_bridge = MqttBridge::new(
        config.clone(),
        playback_state.clone(),
        state_rx,
        cmd_tx.clone(),
    );
    let mqtt_task = tokio::spawn(async move {
        if let Err(e) = mqtt_bridge.run().await {
            error!("MQTT bridge failed: {}", e);
        }
    });

    // Notify S6 that we're ready (only in add-on environment)
    notify_readiness();

    // Shutdown handling: Ctrl+C (SIGINT) or SIGTERM (S6 / add-on stop). The MQTT
    // bridge task handles the same signals itself to publish the "offline"
    // availability, then exits on its own; the remaining tasks are aborted here.
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    tokio::select! {
        _ = tokio::signal::ctrl_c() => info!("Received Ctrl+C, shutting down..."),
        _ = sigterm.recv() => info!("Received SIGTERM, shutting down..."),
    }

    mqtt_task.abort();
    client_task.abort();
    if let Some(t) = soloist_daemon_task {
        t.abort();
    }

    Ok(())
}
