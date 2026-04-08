use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, RwLock, mpsc};
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS, ConnectReturnCode};
use tracing::{info, debug, error};
use serde_json::json;
use chrono::Utc;

use crate::Config;
use crate::PlaybackState;
use crate::PlayerCommand;

/// MQTT bridge for Home Assistant discovery
pub struct MqttBridge {
    config: Config,
    playback_state: Arc<RwLock<PlaybackState>>,
    state_rx: broadcast::Receiver<()>,
    #[allow(dead_code)]
    command_tx: mpsc::UnboundedSender<PlayerCommand>,
}

impl MqttBridge {
    pub fn new(
        config: Config,
        playback_state: Arc<RwLock<PlaybackState>>,
        state_rx: broadcast::Receiver<()>,
        command_tx: mpsc::UnboundedSender<PlayerCommand>,
    ) -> Self {
        Self {
            config,
            playback_state,
            state_rx,
            command_tx,
        }
    }

    pub async fn run(mut self) -> anyhow::Result<()> {
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
        let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;

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
                    if discovery_published {
                        let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                        client.disconnect().await?;
                    }
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Received SIGINT, shutting down gracefully...");
                    if discovery_published {
                        let _ = client.publish(&avail_topic, QoS::AtLeastOnce, true, "offline").await;
                        client.disconnect().await?;
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
                                    }
                                }
                                // No command subscription - this is a monitor-only integration
                            } else {
                                error!("MQTT connection failed: {:?}", connack.code);
                            }
                        }
                        Ok(Event::Incoming(Packet::Disconnect)) => {
                            error!("MQTT disconnected");
                            discovery_published = false;
                        }
                        Err(e) => {
                            error!("MQTT error: {}", e);
                            tokio::time::sleep(Duration::from_secs(5)).await;
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
        client.publish(&topic, QoS::AtLeastOnce, true, payload).await?;
        debug!("Published sensor discovery config to topic: {}", topic);

        // Publish online status
        let avail_topic = format!("greenroom/{}/availability", device_id);
        client.publish(avail_topic, QoS::AtLeastOnce, true, "online").await?;
        
        debug!("Published MQTT discovery config for monitor sensor");
        Ok(())
    }

    async fn publish_state(
        &self,
        client: &AsyncClient,
        device_id: &str,
    ) -> anyhow::Result<()> {
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
        client.publish(state_topic, QoS::AtLeastOnce, false, status).await?;

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
            "is_volume_muted": state.is_volume_muted,
            "media_position": state.position_ms / 1000,
            "media_duration": state.duration_ms / 1000,
            "media_position_updated_at": media_position_updated_at,
            "media_content_id": state.media_content_id.as_deref().unwrap_or(""),
            "source": state.source.as_deref().unwrap_or("Unknown"),
            "shuffle": state.shuffle,
            "repeat": state.repeat,
        });

        let attr_topic = format!("greenroom/{}/attributes", device_id);
        client.publish(attr_topic, QoS::AtLeastOnce, false, attributes.to_string()).await?;

        debug!("Published state: {} ({} - {})", 
            status,
            state.track.as_deref().unwrap_or("Unknown"),
            state.artist.as_deref().unwrap_or("Unknown")
        );

        Ok(())
    }
}
