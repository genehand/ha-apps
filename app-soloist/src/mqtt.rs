use anyhow::{anyhow, Result};
use rumqttc::{AsyncClient, ConnectReturnCode, Event, MqttOptions, Packet, QoS};
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, mpsc, RwLock};
use tracing::{debug, error, info, warn};

use crate::config::Config;
use crate::soloist::SoloistCommand;
use crate::state::PlaybackState;

/// Volume step used by the volume_up / volume_down commands.
const VOLUME_STEP: u8 = 5;

/// MQTT bridge: publishes playback state via Home Assistant discovery and
/// translates MQTT command topics into Soloist WebSocket commands.
pub struct MqttBridge {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    state_rx: broadcast::Receiver<()>,
    cmd_tx: mpsc::Sender<SoloistCommand>,
}

impl MqttBridge {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        state_rx: broadcast::Receiver<()>,
        cmd_tx: mpsc::Sender<SoloistCommand>,
    ) -> Self {
        Self {
            config,
            playback_state,
            state_rx,
            cmd_tx,
        }
    }

    pub async fn run(mut self) -> Result<()> {
        let mut consecutive_errors: u32 = 0;

        loop {
            match self.run_connection().await {
                // Ok means a shutdown signal (SIGTERM/SIGINT) was received or the
                // connection was closed after client.disconnect(); never reconnect.
                Ok(()) => {
                    info!("MQTT bridge shutting down");
                    return Ok(());
                }
                Err(e) => {
                    error!("MQTT connection error: {}", e);
                    consecutive_errors += 1;
                    let backoff_secs =
                        (consecutive_errors * consecutive_errors).clamp(1, 60) as u64;
                    warn!(
                        "Waiting {}s before MQTT reconnection attempt (error count: {})...",
                        backoff_secs, consecutive_errors
                    );
                    tokio::time::sleep(Duration::from_secs(backoff_secs)).await;
                }
            }
        }
    }

    /// Run a single MQTT connection until it drops.
    async fn run_connection(&mut self) -> Result<()> {
        let device_id = &self.config.mqtt_device_id;
        info!(
            "Connecting to MQTT broker at {}:{}",
            self.config.mqtt_host, self.config.mqtt_port
        );

        let mut mqttoptions = MqttOptions::new(
            format!("soloist_{}", device_id),
            self.config.mqtt_host.clone(),
            self.config.mqtt_port,
        );
        mqttoptions.set_keep_alive(Duration::from_secs(30));

        // Last Will: broker publishes "offline" if we disconnect unexpectedly
        let avail_topic = format!("soloist/{}/availability", device_id);
        let will = rumqttc::LastWill::new(&avail_topic, "offline", QoS::AtLeastOnce, true);
        mqttoptions.set_last_will(will);

        if let (Some(user), Some(pass)) = (&self.config.mqtt_username, &self.config.mqtt_password) {
            mqttoptions.set_credentials(user, pass);
            debug!("MQTT authentication enabled");
        }

        // Channel capacity between AsyncClient and the eventloop. The discovery/
        // state publishes and command subscriptions below are all queued while
        // the eventloop is NOT being polled (they run inside the ConnAck match
        // arm), so the capacity must exceed the worst-case burst: 7 publishes +
        // 18 subscriptions. If the channel fills, send_async blocks forever and
        // the queued messages never reach the broker (requests are only flushed
        // by the next poll()).
        let (client, mut eventloop) = AsyncClient::new(mqttoptions, 64);
        let mut discovery_published = false;
        // For mute restore: last non-zero volume we set/heard
        let mut last_volume: u8 = 80;

        // Command topics we subscribe to (full list including generic command topic)
        let cmd_topics: Vec<String> = [
            "command",
            "cmd/play",
            "cmd/pause",
            "cmd/next",
            "cmd/previous",
            "cmd/seek",
            "cmd/volume",
            "cmd/volume_up",
            "cmd/volume_down",
            "cmd/volume_mute",
            "cmd/shuffle",
            "cmd/repeat",
            "cmd/play_media",
            "cmd/add_to_queue",
            "cmd/activate",
            "cmd/deactivate",
            "active/set",
            "power/set",
        ]
        .iter()
        .map(|suffix| format!("soloist/{}/{}", device_id, suffix))
        .collect();

        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;

        loop {
            tokio::select! {
                // State change notifications from the soloist client
                _ = self.state_rx.recv() => {
                    // While the power switch is off the published state is
                    // static (idle + null attributes), so there is nothing to
                    // republish: stay quiet on MQTT until power comes back on.
                    // The power command itself publishes the fresh state, so
                    // the single power-off publish is the last one until then.
                    if discovery_published && self.playback_state.read().await.powered_on {
                        if let Err(e) = self.publish_state(&client, device_id).await {
                            error!("Failed to publish state: {}", e);
                        }
                    }
                }

                _ = sigterm.recv() => {
                    info!("Received SIGTERM, shutting down gracefully...");
                    let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                    let _ = client.disconnect().await;
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Received SIGINT, shutting down gracefully...");
                    let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                    let _ = client.disconnect().await;
                    return Ok(());
                }

                event = eventloop.poll() => {
                    match event {
                        Ok(Event::Incoming(Packet::ConnAck(connack))) => {
                            if connack.code == ConnectReturnCode::Success {
                                debug!("Connected to MQTT broker");
                                if !discovery_published {
                                    if let Err(e) = self.publish_discovery_configs(&client, device_id).await {
                                        error!("Failed to publish discovery configs: {}", e);
                                    } else {
                                        discovery_published = true;
                                        if let Err(e) = self.publish_state(&client, device_id).await {
                                            error!("Failed to publish initial state: {}", e);
                                        }
                                    }
                                }
                                for topic in &cmd_topics {
                                    if let Err(e) = client.subscribe(topic, QoS::AtLeastOnce).await {
                                        error!("Failed to subscribe to {}: {}", topic, e);
                                    }
                                }
                                info!("Subscribed to {} command topics", cmd_topics.len());
                            } else {
                                error!("MQTT connection failed: {:?}", connack.code);
                                return Err(anyhow!("MQTT connection failed: {:?}", connack.code));
                            }
                        }
                        Ok(Event::Incoming(Packet::Publish(publish))) => {
                            let payload = String::from_utf8_lossy(&publish.payload).to_string();
                            // The power switch is a local reporting gate (not a
                            // soloist command): handle it before translation.
                            if publish.topic == format!("soloist/{}/power/set", device_id) {
                                if let Err(e) =
                                    self.handle_power_command(&client, device_id, &payload).await
                                {
                                    error!("Failed to process power command: {}", e);
                                }
                            } else if let Some(commands) = self
                                .translate_command(&publish.topic, &payload, &mut last_volume)
                                .await
                            {
                                for cmd in commands {
                                    debug!("Sending soloist command: {:?}", cmd);
                                    if self.cmd_tx.send(cmd).await.is_err() {
                                        warn!("Soloist client not running; command dropped");
                                    }
                                }
                            }
                        }
                        Ok(Event::Incoming(Packet::Disconnect)) => {
                            error!("MQTT disconnected by broker");
                            return Err(anyhow!("MQTT disconnected by broker"));
                        }
                        Ok(Event::Outgoing(rumqttc::Outgoing::Disconnect)) => {
                            debug!("MQTT disconnect acknowledged");
                            return Ok(());
                        }
                        Err(e) => {
                            error!("MQTT error: {}", e);
                            return Err(anyhow!("MQTT error: {}", e));
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    /// Translate an MQTT command payload into one or more Soloist commands.
    /// Returns None when the message is not a command (or not for us).
    async fn translate_command(
        &self,
        topic: &str,
        payload: &str,
        last_volume: &mut u8,
    ) -> Option<Vec<SoloistCommand>> {
        let device_id = &self.config.mqtt_device_id;
        let prefix = format!("soloist/{}/", device_id);
        let suffix = topic.strip_prefix(&prefix)?;

        let commands = match suffix {
            "active/set" => {
                let active = payload.trim().eq_ignore_ascii_case("ON");
                Some(vec![if active {
                    SoloistCommand::Activate
                } else {
                    SoloistCommand::Deactivate
                }])
            }
            "cmd/play" | "cmd/play_media" => {
                let uri = parse_uri(payload);
                match uri {
                    Some(uri) => Some(vec![SoloistCommand::Play { uri: Some(uri) }]),
                    None if payload.trim().is_empty() => {
                        Some(vec![SoloistCommand::Play { uri: None }])
                    }
                    None => {
                        warn!("Ignoring play command with non-URI payload: {}", payload);
                        None
                    }
                }
            }
            "cmd/pause" => Some(vec![SoloistCommand::Pause]),
            "cmd/next" => Some(vec![SoloistCommand::SkipNext]),
            "cmd/previous" => Some(vec![SoloistCommand::SkipPrev]),
            "cmd/seek" => {
                let secs: f64 = match payload.trim().parse() {
                    Ok(v) => v,
                    Err(_) => {
                        warn!("Invalid seek payload (seconds expected): {}", payload);
                        return None;
                    }
                };
                if secs < 0.0 {
                    warn!("Negative seek ignored");
                    return None;
                }
                Some(vec![SoloistCommand::Seek {
                    position_ms: (secs * 1000.0) as u64,
                }])
            }
            "cmd/volume" => {
                let volume = match parse_volume(payload) {
                    Some(v) => v,
                    None => {
                        warn!("Invalid volume payload: {}", payload);
                        return None;
                    }
                };
                if volume > 0 {
                    *last_volume = volume;
                }
                Some(vec![SoloistCommand::SetVolume { volume }])
            }
            "cmd/volume_up" | "cmd/volume_down" => {
                let current = self.playback_state.read().await.volume;
                let target = if suffix.ends_with("up") {
                    current.saturating_add(VOLUME_STEP).min(100)
                } else {
                    current.saturating_sub(VOLUME_STEP)
                };
                if target != current {
                    *last_volume = if target > 0 { target } else { *last_volume };
                }
                Some(vec![SoloistCommand::SetVolume { volume: target }])
            }
            "cmd/volume_mute" => {
                let muted = matches!(
                    payload.trim().to_uppercase().as_str(),
                    "ON" | "TRUE" | "1" | "MUTE"
                );
                if muted {
                    let current = self.playback_state.read().await.volume;
                    if current > 0 {
                        *last_volume = current;
                    }
                    Some(vec![SoloistCommand::SetVolume { volume: 0 }])
                } else {
                    Some(vec![SoloistCommand::SetVolume {
                        volume: *last_volume,
                    }])
                }
            }
            "cmd/shuffle" => {
                let enabled = parse_bool(payload)?;
                Some(vec![SoloistCommand::SetShuffle { enabled }])
            }
            "cmd/repeat" => Some(repeat_commands(payload.trim())),
            "cmd/add_to_queue" => {
                let uri = parse_uri(payload);
                match uri {
                    Some(uri) => Some(vec![SoloistCommand::AddToQueue { uri }]),
                    None => {
                        warn!("Ignoring add_to_queue with non-URI payload: {}", payload);
                        None
                    }
                }
            }
            "cmd/activate" => Some(vec![SoloistCommand::Activate]),
            "cmd/deactivate" => Some(vec![SoloistCommand::Deactivate]),
            "command" => parse_generic_command(payload),
            _ => None,
        };

        // Playback commands need an authenticated session; soloist rejects
        // them with "command requires authentication" otherwise. Drop them
        // early with a clearer message. get_auth_state / get_state / get_queue
        // are diagnostics and always pass through.
        let commands = commands?;
        if commands_require_auth(&commands) && !self.playback_state.read().await.logged_in {
            warn!(
                "Ignoring {} command(s): soloist is not logged in (open the Spotify app \
                 and select this device)",
                commands.len()
            );
            return None;
        }
        Some(commands)
    }

    /// Apply an MQTT power switch command (ON/OFF). This is a local reporting
    /// switch: it never sends commands to soloist, it only gates the reported
    /// ha_state (see `PlaybackState::set_power`). The full state is
    /// republished because both the power state and ha_state may have changed.
    async fn handle_power_command(
        &self,
        client: &AsyncClient,
        device_id: &str,
        payload: &str,
    ) -> Result<()> {
        let Some(on) = parse_bool(payload) else {
            return Ok(());
        };
        self.playback_state.write().await.set_power(on);
        self.publish_state(client, device_id).await?;
        info!("Power switch set to {}", if on { "ON" } else { "OFF" });
        Ok(())
    }

    async fn publish_discovery_configs(&self, client: &AsyncClient, device_id: &str) -> Result<()> {
        let device_name = &self.config.device_name;

        // Main playback sensor with all media attributes
        let topic = format!("homeassistant/sensor/{}/config", device_id);
        let config = json!({
            "name": null,
            "unique_id": device_id,
            "state_topic": format!("soloist/{}/state", device_id),
            "json_attributes_topic": format!("soloist/{}/attributes", device_id),
            "icon": "mdi:spotify",
            "availability": {
                "topic": format!("soloist/{}/availability", device_id),
                "payload_available": "online",
                "payload_not_available": "offline"
            },
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "manufacturer": "Spotify",
                "model": "Soloist Connect Device",
                "sw_version": env!("CARGO_PKG_VERSION")
            }
        });
        client
            .publish(
                &topic,
                QoS::AtLeastOnce,
                true,
                serde_json::to_string(&config)?,
            )
            .await?;
        debug!("Published sensor discovery config to: {}", topic);

        // Active-device switch (activate / deactivate as Spotify Connect device)
        let switch_topic = format!("homeassistant/switch/{}/active/config", device_id);
        let switch_config = json!({
            "name": "Active",
            "unique_id": format!("{}_active", device_id),
            "state_topic": format!("soloist/{}/active/state", device_id),
            "command_topic": format!("soloist/{}/active/set", device_id),
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": "mdi:lan-connect",
            "availability": {
                "topic": format!("soloist/{}/availability", device_id),
                "payload_available": "online",
                "payload_not_available": "offline"
            },
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "manufacturer": "Spotify",
                "model": "Soloist Connect Device",
                "sw_version": env!("CARGO_PKG_VERSION")
            }
        });
        client
            .publish(
                &switch_topic,
                QoS::AtLeastOnce,
                true,
                serde_json::to_string(&switch_config)?,
            )
            .await?;
        debug!("Published switch discovery config to: {}", switch_topic);

        // Power switch: reporting gate only (off forces "idle", power-on
        // waits for playback to actually start before reporting "paused").
        let power_topic = format!("homeassistant/switch/{}/power/config", device_id);
        let power_config = json!({
            "name": "Power",
            "unique_id": format!("{}_power", device_id),
            "state_topic": format!("soloist/{}/power/state", device_id),
            "command_topic": format!("soloist/{}/power/set", device_id),
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": "mdi:power",
            "availability": {
                "topic": format!("soloist/{}/availability", device_id),
                "payload_available": "online",
                "payload_not_available": "offline"
            },
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "manufacturer": "Spotify",
                "model": "Soloist Connect Device",
                "sw_version": env!("CARGO_PKG_VERSION")
            }
        });
        client
            .publish(
                &power_topic,
                QoS::AtLeastOnce,
                true,
                serde_json::to_string(&power_config)?,
            )
            .await?;
        debug!(
            "Published power switch discovery config to: {}",
            power_topic
        );

        let avail_topic = format!("soloist/{}/availability", device_id);
        client
            .publish(avail_topic, QoS::AtLeastOnce, true, "online")
            .await?;
        Ok(())
    }

    async fn publish_state(&self, client: &AsyncClient, device_id: &str) -> Result<()> {
        let state = self.playback_state.read().await;

        let state_topic = format!("soloist/{}/state", device_id);
        client
            .publish(state_topic, QoS::AtLeastOnce, false, state.ha_state())
            .await?;

        let attributes = state.attributes();
        let attr_topic = format!("soloist/{}/attributes", device_id);
        client
            .publish(
                attr_topic,
                QoS::AtLeastOnce,
                false,
                serde_json::to_string(&attributes)?,
            )
            .await?;

        // Keep the switches in sync
        let active_state = if state.is_active { "ON" } else { "OFF" };
        let active_topic = format!("soloist/{}/active/state", device_id);
        client
            .publish(active_topic, QoS::AtLeastOnce, true, active_state)
            .await?;

        let power_state = if state.powered_on { "ON" } else { "OFF" };
        let power_topic = format!("soloist/{}/power/state", device_id);
        client
            .publish(power_topic, QoS::AtLeastOnce, true, power_state)
            .await?;

        debug!(
            "Published state: {} ({} - {}) volume={} repeat={} shuffle={} power={}",
            state.ha_state(),
            state.track.as_deref().unwrap_or("Unknown"),
            state.artist.as_deref().unwrap_or("Unknown"),
            state.volume,
            state.repeat,
            state.shuffle,
            power_state
        );
        Ok(())
    }
}

/// True if any command requires an authenticated soloist session (the
/// get_* diagnostic commands always work).
fn commands_require_auth(commands: &[SoloistCommand]) -> bool {
    commands.iter().any(|c| {
        !matches!(
            c,
            SoloistCommand::GetAuthState
                | SoloistCommand::GetState
                | SoloistCommand::GetQueue { .. }
        )
    })
}

// ---------------------------------------------------------------------------
// Payload helpers
// ---------------------------------------------------------------------------

/// Parse a spotify URI from a payload (raw URI or JSON object).
fn parse_uri(payload: &str) -> Option<String> {
    let trimmed = payload.trim();
    if trimmed.is_empty() {
        return None;
    }
    if trimmed.starts_with("spotify:") {
        return Some(trimmed.to_string());
    }
    if trimmed.starts_with('{') {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(trimmed) {
            for key in ["uri", "media_id", "url"] {
                if let Some(s) = v.get(key).and_then(|x| x.as_str()) {
                    if s.starts_with("spotify:") {
                        return Some(s.to_string());
                    }
                }
            }
        }
    }
    None
}

/// Parse volume: accepts 0-100 or 0.0-1.0 (fraction).
fn parse_volume(payload: &str) -> Option<u8> {
    let trimmed = payload.trim();
    if let Ok(v) = trimmed.parse::<f64>() {
        if !v.is_finite() || v < 0.0 {
            return None;
        }
        if v <= 1.0 {
            return Some((v * 100.0).round() as u8);
        }
        if v <= 100.0 {
            return Some(v.round() as u8);
        }
    }
    None
}

fn parse_bool(payload: &str) -> Option<bool> {
    match payload.trim().to_uppercase().as_str() {
        "ON" | "TRUE" | "1" | "YES" => Some(true),
        "OFF" | "FALSE" | "0" | "NO" => Some(false),
        _ => {
            warn!("Invalid boolean payload: {}", payload);
            None
        }
    }
}

/// Map a repeat payload (HA-style all/one/off or soloist-style context/track/off)
/// to the required pair of soloist commands.
fn repeat_commands(repeat: &str) -> Vec<SoloistCommand> {
    match repeat.to_lowercase().as_str() {
        "context" | "all" => vec![
            SoloistCommand::SetRepeatTrack { enabled: false },
            SoloistCommand::SetRepeatContext { enabled: true },
        ],
        "track" | "one" => vec![
            SoloistCommand::SetRepeatContext { enabled: false },
            SoloistCommand::SetRepeatTrack { enabled: true },
        ],
        _ => vec![
            SoloistCommand::SetRepeatTrack { enabled: false },
            SoloistCommand::SetRepeatContext { enabled: false },
        ],
    }
}

/// Parse the generic `soloist/{id}/command` topic: a JSON object with
/// {"command": "...", ...optional fields}. Optional fields follow the
/// Soloist WebSocket API field names (uri, position_ms, volume, enabled).
fn parse_generic_command(payload: &str) -> Option<Vec<SoloistCommand>> {
    let v: serde_json::Value = match serde_json::from_str(payload) {
        Ok(v) => v,
        Err(e) => {
            warn!("Invalid JSON on command topic: {}", e);
            return None;
        }
    };
    let name = v.get("command").and_then(|c| c.as_str())?.to_string();

    let mut obj = v.clone();
    // Accept position_secs as an alternative to position_ms
    if let Some(secs) = obj.get("position_secs").and_then(|s| s.as_f64()) {
        if obj.get("position_ms").is_none() {
            obj["position_ms"] = json!((secs * 1000.0) as u64);
        }
    }

    let cmd = match name.as_str() {
        "get_auth_state" => SoloistCommand::GetAuthState,
        "get_state" => SoloistCommand::GetState,
        "get_queue" => SoloistCommand::GetQueue {
            limit: obj.get("limit").and_then(|l| l.as_u64()).map(|l| l as u32),
        },
        "play" => SoloistCommand::Play {
            uri: obj
                .get("uri")
                .and_then(|u| u.as_str())
                .map(|s| s.to_string()),
        },
        "pause" => SoloistCommand::Pause,
        "skip_next" => SoloistCommand::SkipNext,
        "skip_prev" => SoloistCommand::SkipPrev,
        "seek" => SoloistCommand::Seek {
            position_ms: obj.get("position_ms").and_then(|p| p.as_u64())?,
        },
        "set_volume" => SoloistCommand::SetVolume {
            volume: obj
                .get("volume")
                .and_then(|v| v.as_u64())
                .map(|v| v.min(100) as u8)?,
        },
        "set_shuffle" => SoloistCommand::SetShuffle {
            enabled: obj.get("enabled").and_then(|e| e.as_bool())?,
        },
        "set_repeat_context" => SoloistCommand::SetRepeatContext {
            enabled: obj.get("enabled").and_then(|e| e.as_bool())?,
        },
        "set_repeat_track" => SoloistCommand::SetRepeatTrack {
            enabled: obj.get("enabled").and_then(|e| e.as_bool())?,
        },
        "add_to_queue" => SoloistCommand::AddToQueue {
            uri: obj.get("uri").and_then(|u| u.as_str())?.to_string(),
        },
        "activate" => SoloistCommand::Activate,
        "deactivate" => SoloistCommand::Deactivate,
        other => {
            warn!("Unknown command on command topic: {}", other);
            return None;
        }
    };
    Some(vec![cmd])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_uri_raw_and_json() {
        assert_eq!(
            parse_uri("spotify:track:abc").as_deref(),
            Some("spotify:track:abc")
        );
        assert_eq!(
            parse_uri(r#"{"uri":"spotify:track:abc"}"#).as_deref(),
            Some("spotify:track:abc")
        );
        assert_eq!(
            parse_uri(r#"{"media_id":"spotify:track:abc"}"#).as_deref(),
            Some("spotify:track:abc")
        );
        assert_eq!(parse_uri(""), None);
        assert_eq!(parse_uri("not a uri"), None);
    }

    #[test]
    fn parse_volume_accepts_both_ranges() {
        assert_eq!(parse_volume("50"), Some(50));
        assert_eq!(parse_volume("0.5"), Some(50));
        assert_eq!(parse_volume("1"), Some(100));
        assert_eq!(parse_volume("100"), Some(100));
        assert_eq!(parse_volume("0"), Some(0));
        assert_eq!(parse_volume("-5"), None);
        assert_eq!(parse_volume("abc"), None);
    }

    #[test]
    fn parse_bool_variants() {
        assert_eq!(parse_bool("ON"), Some(true));
        assert_eq!(parse_bool("false"), Some(false));
        assert_eq!(parse_bool("1"), Some(true));
        assert_eq!(parse_bool("0"), Some(false));
        assert_eq!(parse_bool("maybe"), None);
    }

    #[test]
    fn repeat_mapping() {
        let off = repeat_commands("off");
        assert!(matches!(
            off[0],
            SoloistCommand::SetRepeatTrack { enabled: false }
        ));
        assert!(matches!(
            off[1],
            SoloistCommand::SetRepeatContext { enabled: false }
        ));

        let context = repeat_commands("all");
        assert!(matches!(
            context[1],
            SoloistCommand::SetRepeatContext { enabled: true }
        ));

        let track = repeat_commands("one");
        assert!(matches!(
            track[1],
            SoloistCommand::SetRepeatTrack { enabled: true }
        ));
    }

    #[test]
    fn auth_gate_lets_diagnostics_through() {
        assert!(!commands_require_auth(&[SoloistCommand::GetAuthState]));
        assert!(!commands_require_auth(&[SoloistCommand::GetQueue {
            limit: None
        }]));
        assert!(commands_require_auth(&[SoloistCommand::Pause]));
        assert!(commands_require_auth(&[
            SoloistCommand::GetState,
            SoloistCommand::SetVolume { volume: 50 }
        ]));
    }

    #[test]
    fn generic_command_parsing() {
        let cmds = parse_generic_command(r#"{"command":"seek","position_ms":30000}"#).unwrap();
        assert!(matches!(
            cmds[0],
            SoloistCommand::Seek { position_ms: 30000 }
        ));

        let cmds = parse_generic_command(r#"{"command":"seek","position_secs":45}"#).unwrap();
        assert!(matches!(
            cmds[0],
            SoloistCommand::Seek { position_ms: 45000 }
        ));

        let cmds =
            parse_generic_command(r#"{"command":"play","uri":"spotify:playlist:x"}"#).unwrap();
        assert!(matches!(
            cmds[0],
            SoloistCommand::Play { uri: Some(ref u) } if u == "spotify:playlist:x"
        ));

        assert!(parse_generic_command("not json").is_none());
        assert!(parse_generic_command(r#"{"command":"explode"}"#).is_none());
    }
}
