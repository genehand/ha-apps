use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::debug;

use librespot_core::session::Session;
use librespot_core::spotify_id::SpotifyId;
use librespot_metadata::{Metadata, Track};
use librespot_protocol::connect::ClusterUpdate;

use crate::PlaybackState;

/// Handle a cluster update from Spotify Connect.
pub async fn handle_cluster_update(
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
            let track_uri = player_state
                .track
                .as_ref()
                .map(|t| t.uri.clone())
                .unwrap_or_default();
            let position_ms = player_state.position_as_of_timestamp;
            let duration_ms = player_state.duration as u32;
            let position_timestamp_ms = player_state.timestamp;
            let media_content_id = Some(player_state.context_uri.clone()).filter(|s| !s.is_empty());

            let shuffle = player_state
                .options
                .as_ref()
                .map(|o| o.shuffling_context)
                .unwrap_or(false);
            let repeat = player_state
                .options
                .as_ref()
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
                cluster
                    .device
                    .get(active_device_id)
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
                SpotifyId::from_base62(id_str)
                    .ok()
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

                        artist_name = track
                            .artists
                            .first()
                            .map(|artist| artist.name.clone())
                            .unwrap_or_else(|| "Unknown Artist".to_string());

                        album_name = Some(track.album.name.clone());

                        artwork_url = track
                            .album
                            .covers
                            .first()
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
                if is_idle {
                    // No active device - clear track metadata but preserve playback state
                    state.track = None;
                    state.artist = None;
                    state.album = None;
                    state.artwork_url = None;
                } else {
                    // Active playback - update track metadata
                    state.track = Some(track_name.clone());
                    state.artist = Some(artist_name.clone());
                    state.album = album_name;
                    state.artwork_url = artwork_url;
                }
                state.is_playing = is_playing;
                state.is_idle = is_idle;
                state.volume = volume as f64;
                state.is_muted = is_volume_muted;
                state.media_position = Some(position_ms as u32);
                state.media_duration = Some(duration_ms);
                state.media_position_updated_at_ms = Some(position_timestamp_ms);
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
