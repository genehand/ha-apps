use serde::Serialize;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// Position anchor from soloist, passed through as-is: `position_ms` is the
/// position at `timestamp_ms` (epoch ms). HA interpolates the progress bar
/// on its own from `media_position_updated_at`.
#[derive(Clone, Copy, Debug, Default)]
pub struct PositionAnchor {
    pub position_ms: u64,
    pub timestamp_ms: i64,
    pub speed: f64,
}

pub fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis() as i64
}

/// Playback state shared between the soloist WebSocket client and the MQTT bridge.
#[derive(Clone, Debug, Default)]
pub struct PlaybackState {
    /// soloist status: idle, playing, paused, buffering
    pub status: String,
    pub track: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    pub artwork_url: Option<String>,
    pub media_content_id: Option<String>,
    /// Context name (playlist/album/show) used as the HA "source" attribute
    pub source: Option<String>,
    pub volume: u8,
    pub shuffle: bool,
    /// HA-style repeat: off | all | one
    pub repeat: String,
    pub media_duration_ms: Option<u64>,
    pub position_anchor: PositionAnchor,
    pub logged_in: bool,
    pub is_active: bool,
    pub device_name: String,
    pub upcoming: Vec<String>,
    /// True while the soloist daemon process is running
    pub soloist_running: bool,
    /// Last soloist event / error for diagnostics
    pub last_error: Option<String>,
}

impl PlaybackState {
    /// HA media player state derived from soloist status.
    pub fn ha_state(&self) -> &'static str {
        match self.status.as_str() {
            "playing" | "buffering" => "playing",
            "paused" => "paused",
            _ => "idle",
        }
    }

    /// Current position in seconds (HA convention), passed through from the
    /// latest soloist position anchor. HA advances it on its own while playing.
    pub fn media_position_secs(&self) -> Option<u64> {
        if self.status == "idle" || self.position_anchor.timestamp_ms == 0 {
            return None;
        }
        Some(self.position_anchor.position_ms / 1000)
    }

    pub fn media_duration_secs(&self) -> Option<u64> {
        self.media_duration_ms.map(|ms| ms / 1000)
    }

    /// Volume as a 0.0-1.0 float (HA volume_level convention).
    pub fn volume_level(&self) -> f64 {
        (self.volume as f64) / 100.0
    }
}

/// JSON attributes published to the MQTT attributes topic, following HA media player names.
#[derive(Serialize)]
pub struct StateAttributes<'a> {
    pub media_title: &'a str,
    pub media_artist: &'a str,
    pub media_album_name: &'a str,
    pub media_image_url: &'a str,
    pub volume: f64,
    pub is_volume_muted: bool,
    pub media_position: Option<u64>,
    pub media_duration: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_position_updated_at: Option<String>,
    pub media_content_id: &'a str,
    pub source: &'a str,
    pub shuffle: bool,
    pub repeat: &'a str,
    pub device_name: &'a str,
    pub logged_in: bool,
    pub is_active: bool,
    pub upcoming: &'a [String],
}

impl<'a> PlaybackState {
    pub fn attributes(&'a self) -> StateAttributes<'a> {
        let position_updated_at = if self.position_anchor.timestamp_ms > 0 {
            chrono::DateTime::from_timestamp_millis(self.position_anchor.timestamp_ms)
                .map(|dt| dt.to_rfc3339())
        } else {
            None
        };

        StateAttributes {
            media_title: self.track.as_deref().unwrap_or("Unknown"),
            media_artist: self.artist.as_deref().unwrap_or("Unknown"),
            media_album_name: self.album.as_deref().unwrap_or("Unknown"),
            media_image_url: self.artwork_url.as_deref().unwrap_or(""),
            volume: self.volume_level(),
            is_volume_muted: self.volume == 0,
            media_position: self.media_position_secs(),
            media_duration: self.media_duration_secs(),
            media_position_updated_at: position_updated_at,
            media_content_id: self.media_content_id.as_deref().unwrap_or(""),
            source: self.source.as_deref().unwrap_or(""),
            shuffle: self.shuffle,
            repeat: &self.repeat,
            device_name: &self.device_name,
            logged_in: self.logged_in,
            is_active: self.is_active,
            upcoming: &self.upcoming,
        }
    }
}
