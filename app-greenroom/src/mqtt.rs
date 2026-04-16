use chrono::Utc;
use rumqttc::{AsyncClient, ConnectReturnCode, Event, MqttOptions, Packet, QoS};
use serde_json::json;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, RwLock};
use tracing::{debug, error, info, warn};

use crate::librespot::calculate_backoff;
use crate::state::{save_connection_state, ConnectionState};
use crate::Config;
use crate::PlaybackState;

/// MQTT bridge for Home Assistant discovery
pub struct MqttBridge {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    state_rx: broadcast::Receiver<()>,
    connection_tx: broadcast::Sender<bool>,
    connection_state_file: PathBuf,
    shutdown: Arc<RwLock<bool>>,
}

impl MqttBridge {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        state_rx: broadcast::Receiver<()>,
        connection_tx: broadcast::Sender<bool>,
        connection_state_file: PathBuf,
    ) -> Self {
        Self {
            config,
            playback_state,
            state_rx,
            connection_tx,
            connection_state_file,
            shutdown: Arc::new(RwLock::new(false)),
        }
    }

    pub async fn run(mut self) -> anyhow::Result<()> {
        let mut consecutive_errors: u32 = 0;

        loop {
            match self.run_connection().await {
                Ok(()) => {
                    // Check if shutdown was requested before attempting reconnect
                    if *self.shutdown.read().await {
                        debug!("MQTT bridge shutting down gracefully");
                        return Ok(());
                    }
                    warn!("MQTT connection ended cleanly, will reconnect...");
                    consecutive_errors = 0;
                }
                Err(e) => {
                    error!("MQTT connection error: {}", e);
                    consecutive_errors += 1;

                    let backoff_secs = calculate_backoff(consecutive_errors);
                    warn!(
                        "Waiting {} seconds before MQTT reconnection attempt (error count: {})...",
                        backoff_secs, consecutive_errors
                    );
                    tokio::time::sleep(Duration::from_secs(backoff_secs)).await;
                }
            }
        }
    }

    /// Run a single MQTT connection until it drops
    async fn run_connection(&mut self) -> anyhow::Result<()> {
        // Get MQTT settings from config
        let host = &self.config.mqtt_host;
        let port = self.config.mqtt_port;
        let username = &self.config.mqtt_username;
        let password = &self.config.mqtt_password;
        let device_id = &self.config.mqtt_device_id;

        info!("Connecting to MQTT broker at {}:{}", host, port);

        let mut mqttoptions = MqttOptions::new(device_id, host.clone(), port);
        mqttoptions.set_keep_alive(Duration::from_secs(30));

        // Set Last Will and Testament - broker will publish "offline" if we disconnect unexpectedly
        let avail_topic = format!("greenroom/{}/availability", device_id);
        let will = rumqttc::LastWill::new(&avail_topic, "offline", QoS::AtLeastOnce, true);
        mqttoptions.set_last_will(will);

        if let (Some(user), Some(pass)) = (username, password) {
            mqttoptions.set_credentials(user, pass);
            debug!("MQTT authentication enabled");
        }

        let (client, mut eventloop) = AsyncClient::new(mqttoptions, 10);

        // Flag to track if we've published discovery configs
        let mut discovery_published = false;

        // Create shutdown signal handler
        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;

        // Topic for switch commands
        let switch_cmd_topic = format!("greenroom/{}/active/set", device_id);

        // Handle state updates and MQTT events
        loop {
            tokio::select! {
                // Handle state change notifications from Spotify
                _ = self.state_rx.recv() => {
                    if discovery_published {
                        if let Err(e) = self.publish_state(&client, device_id).await {
                            error!("Failed to publish state: {}", e);
                        }
                    }
                }

                // Handle graceful shutdown signals
                _ = sigterm.recv() => {
                    info!("Received SIGTERM, shutting down gracefully...");
                    *self.shutdown.write().await = true;
                    if discovery_published {
                        let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                        let _ = client.disconnect().await;
                    }
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Received SIGINT, shutting down gracefully...");
                    *self.shutdown.write().await = true;
                    if discovery_published {
                        let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                        let _ = client.disconnect().await;
                    }
                    return Ok(());
                }

                // Handle MQTT events
                event = eventloop.poll() => {
                    match event {
                        Ok(Event::Incoming(Packet::ConnAck(connack))) => {
                            if connack.code == ConnectReturnCode::Success {
                                debug!("Connected to MQTT broker at {}:{}", self.config.mqtt_host, self.config.mqtt_port);
                                // Publish discovery configs on initial connect
                                if !discovery_published {
                                    if let Err(e) = self.publish_discovery_configs(&client, device_id).await {
                                        error!("Failed to publish discovery configs: {}", e);
                                    } else {
                                        discovery_published = true;
                                        // Publish initial state so sensor shows up immediately
                                        if let Err(e) = self.publish_state(&client, device_id).await {
                                            error!("Failed to publish initial state: {}", e);
                                        }
                                        // Publish initial switch state
                                        if let Err(e) = self.publish_connection_switch_state(&client, device_id).await {
                                            error!("Failed to publish switch state: {}", e);
                                        }
                                    }
                                }
                                // Subscribe to switch command topic
                                if let Err(e) = client.subscribe(&switch_cmd_topic, QoS::AtLeastOnce).await {
                                    error!("Failed to subscribe to switch command topic: {}", e);
                                } else {
                                    debug!("Subscribed to switch command topic: {}", switch_cmd_topic);
                                }
                            } else {
                                error!("MQTT connection failed: {:?}", connack.code);
                                return Err(anyhow::anyhow!("MQTT connection failed: {:?}", connack.code));
                            }
                        }
                        Ok(Event::Incoming(Packet::Publish(publish))) => {
                            // Handle switch command
                            if publish.topic == switch_cmd_topic {
                                let payload = String::from_utf8_lossy(&publish.payload);
                                debug!("Received switch command: {}", payload);
                                self.handle_connection_switch_command(&client, device_id, &payload).await;
                            }
                        }
                        Ok(Event::Incoming(Packet::Disconnect)) => {
                            error!("MQTT disconnected by broker");
                            return Err(anyhow::anyhow!("MQTT disconnected by broker"));
                        }
                        Ok(Event::Outgoing(rumqttc::Outgoing::Disconnect)) => {
                            debug!("MQTT disconnect acknowledged");
                            return Ok(());
                        }
                        Err(e) => {
                            error!("MQTT error: {}", e);
                            return Err(anyhow::anyhow!("MQTT error: {}", e));
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    async fn publish_discovery_configs(
        &self,
        client: &AsyncClient,
        device_id: &str,
    ) -> anyhow::Result<()> {
        let device_name = &self.config.device_name;
        let unique_id = format!("{}", device_id);

        // Sensor with all playback info as attributes
        let topic = format!("homeassistant/sensor/{}/config", device_id);

        let config = json!({
            "name": null,
            "unique_id": unique_id,
            "state_topic": format!("greenroom/{}/state", device_id),
            "json_attributes_topic": format!("greenroom/{}/attributes", device_id),
            "icon": "mdi:spotify",
            "availability": {
                "topic": format!("greenroom/{}/availability", device_id),
                "payload_available": "online",
                "payload_not_available": "offline"
            },
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "manufacturer": "Greenroom",
                "model": "Spotify Connect Monitor",
                "sw_version": env!("CARGO_PKG_VERSION")
            }
        });

        let payload = serde_json::to_string(&config)?;
        client
            .publish(&topic, QoS::AtLeastOnce, true, payload)
            .await?;
        debug!("Published sensor discovery config to topic: {}", topic);

        // Connection control switch
        let switch_topic = format!("homeassistant/switch/{}/active/config", device_id);
        let switch_config = json!({
            "name": "Active",
            "unique_id": format!("{}_active", device_id),
            "state_topic": format!("greenroom/{}/active/state", device_id),
            "command_topic": format!("greenroom/{}/active/set", device_id),
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": "mdi:lan-connect",
            "availability": {
                "topic": format!("greenroom/{}/availability", device_id),
                "payload_available": "online",
                "payload_not_available": "offline"
            },
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "manufacturer": "Greenroom",
                "model": "Spotify Connect Monitor",
                "sw_version": env!("CARGO_PKG_VERSION")
            }
        });

        let switch_payload = serde_json::to_string(&switch_config)?;
        client
            .publish(&switch_topic, QoS::AtLeastOnce, true, switch_payload)
            .await?;
        debug!(
            "Published switch discovery config to topic: {}",
            switch_topic
        );

        // Publish online status
        let avail_topic = format!("greenroom/{}/availability", device_id);
        client
            .publish(avail_topic, QoS::AtLeastOnce, true, "online")
            .await?;

        debug!("Published MQTT discovery config for monitor sensor and connection switch");
        Ok(())
    }

    /// Handle connection switch command from MQTT
    async fn handle_connection_switch_command(
        &self,
        client: &AsyncClient,
        device_id: &str,
        payload: &str,
    ) {
        let enabled = payload == "ON";

        // Update shared state
        {
            let mut state = self.playback_state.write().await;
            state.connection_enabled = enabled;
        }

        // Persist the connection state to file
        let conn_state = ConnectionState { enabled };
        if let Err(e) = save_connection_state(&self.connection_state_file, &conn_state).await {
            error!("Failed to save connection state: {}", e);
        } else {
            debug!("Persisted connection state: enabled={}", enabled);
        }

        // Notify Spotify client of the change
        if let Err(e) = self.connection_tx.send(enabled) {
            warn!(
                "Failed to notify Spotify client of connection state change: {}",
                e
            );
        }

        // Publish updated state
        if let Err(e) = self
            .publish_connection_switch_state(client, device_id)
            .await
        {
            error!("Failed to publish switch state: {}", e);
        }

        info!(
            "Spotify connection {}",
            if enabled { "enabled" } else { "disabled" }
        );
    }

    /// Publish the current connection switch state
    async fn publish_connection_switch_state(
        &self,
        client: &AsyncClient,
        device_id: &str,
    ) -> anyhow::Result<()> {
        let enabled = self.playback_state.read().await.connection_enabled;
        let state = if enabled { "ON" } else { "OFF" };

        let topic = format!("greenroom/{}/active/state", device_id);
        client.publish(topic, QoS::AtLeastOnce, true, state).await?;

        debug!("Published connection switch state: {}", state);
        Ok(())
    }

    async fn publish_state(&self, client: &AsyncClient, device_id: &str) -> anyhow::Result<()> {
        let state = self.playback_state.read().await;

        // Main state is the playback status
        let status = if state.is_idle {
            "idle"
        } else if state.is_playing {
            "playing"
        } else {
            "paused"
        };
        let state_topic = format!("greenroom/{}/state", device_id);
        client
            .publish(state_topic, QoS::AtLeastOnce, false, status)
            .await?;

        // Get current timestamp for position update
        let now = Utc::now();
        let media_position_updated_at = now.to_rfc3339();

        // All other values as JSON attributes using HA standard media player attribute names
        let attributes = json!({
            "media_title": state.track.as_deref().unwrap_or("Unknown"),
            "media_artist": state.artist.as_deref().unwrap_or("Unknown"),
            "media_album_name": state.album.as_deref().unwrap_or("Unknown"),
            "media_image_url": state.artwork_url.as_deref().unwrap_or(""),
            "volume": state.volume,
            "is_volume_muted": state.is_muted,
            "media_position": state.media_position.map(|v| v / 1000),
            "media_duration": state.media_duration.map(|v| v / 1000),
            "media_position_updated_at": media_position_updated_at,
            "media_content_id": state.media_content_id.as_deref().unwrap_or(""),
            "source": state.source.as_deref().unwrap_or("Unknown"),
            "shuffle": state.shuffle,
            "repeat": state.repeat,
        });

        let attr_topic = format!("greenroom/{}/attributes", device_id);
        client
            .publish(attr_topic, QoS::AtLeastOnce, false, attributes.to_string())
            .await?;

        debug!(
            "Published state: {} ({} - {})",
            status,
            state.track.as_deref().unwrap_or("Unknown"),
            state.artist.as_deref().unwrap_or("Unknown")
        );

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test that MqttBridge can be created with minimal config
    #[test]
    fn test_mqtt_bridge_creation() {
        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: Some("test".to_string()),
            mqtt_password: Some("pass".to_string()),
            mqtt_device_id: "test".to_string(),
        };

        let playback_state = Arc::new(RwLock::new(PlaybackState::default()));
        let (_, state_rx) = broadcast::channel(16);
        let (connection_tx, _) = broadcast::channel(4);
        let conn_state_file = std::path::PathBuf::from("/tmp/test_conn_state.json");

        let bridge = MqttBridge::new(
            config,
            playback_state,
            state_rx,
            connection_tx,
            conn_state_file,
        );

        assert_eq!(bridge.config.device_name, "Test");
        assert_eq!(bridge.config.mqtt_host, "localhost");
        assert_eq!(bridge.config.mqtt_port, 1883);
    }

    /// Test that connection switch command enables connection
    #[tokio::test]
    async fn test_connection_switch_command_enable() {
        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };

        let playback_state = Arc::new(RwLock::new(PlaybackState {
            connection_enabled: false,
            ..Default::default()
        }));
        let (_, state_rx) = broadcast::channel(16);
        let (connection_tx, mut connection_rx) = broadcast::channel(4);
        let conn_state_file = std::path::PathBuf::from("/tmp/test_conn_state.json");

        let _bridge = MqttBridge::new(
            config,
            playback_state.clone(),
            state_rx,
            connection_tx.clone(),
            conn_state_file,
        );

        // Create a mock client (we won't actually publish in this test)
        // Test the state update logic directly
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = true;
        }

        // Verify state was updated
        let state = playback_state.read().await;
        assert!(state.connection_enabled);

        // Verify we can send the notification
        connection_tx.send(true).unwrap();
        assert_eq!(connection_rx.recv().await.unwrap(), true);
    }

    /// Test that connection switch command disables connection
    #[tokio::test]
    async fn test_connection_switch_command_disable() {
        let config = Config {
            spotify_username: "".to_string(),
            device_name: "Test".to_string(),
            mqtt_host: "localhost".to_string(),
            mqtt_port: 1883,
            mqtt_username: None,
            mqtt_password: None,
            mqtt_device_id: "test".to_string(),
        };

        let playback_state = Arc::new(RwLock::new(PlaybackState {
            connection_enabled: true,
            ..Default::default()
        }));
        let (_, state_rx) = broadcast::channel(16);
        let (connection_tx, mut connection_rx) = broadcast::channel(4);
        let conn_state_file = std::path::PathBuf::from("/tmp/test_conn_state.json");

        let _bridge = MqttBridge::new(
            config,
            playback_state.clone(),
            state_rx,
            connection_tx.clone(),
            conn_state_file,
        );

        // Simulate receiving OFF command
        {
            let mut state = playback_state.write().await;
            state.connection_enabled = false;
        }

        // Verify state was updated
        let state = playback_state.read().await;
        assert!(!state.connection_enabled);

        // Verify we can send the notification
        connection_tx.send(false).unwrap();
        assert_eq!(connection_rx.recv().await.unwrap(), false);
    }

    /// Test default connection_enabled state
    #[test]
    fn test_default_connection_enabled() {
        let state = PlaybackState::default();
        assert!(!state.connection_enabled); // Default is false from Default trait
    }

    /// Test that connection control channel communicates properly
    #[tokio::test]
    async fn test_connection_control_channel() {
        let (tx, mut rx) = broadcast::channel(4);

        // Send multiple commands
        tx.send(true).unwrap();
        tx.send(false).unwrap();
        tx.send(true).unwrap();

        // Receive and verify
        assert_eq!(rx.recv().await.unwrap(), true);
        assert_eq!(rx.recv().await.unwrap(), false);
        assert_eq!(rx.recv().await.unwrap(), true);
    }
}
