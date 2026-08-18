use serde::Serialize;
use std::collections::HashMap;
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

/// Artist identity captured from `queue_changed` events. Holds only the most
/// recent queue snapshot (replaced on every event, plus the now-playing
/// track, so it stays bounded). A track's full artist info is captured while
/// it is still upcoming, then used to fill in the artist when that track
/// starts playing and its first snapshot ships without creator decorations.
/// Keyed by Spotify URI.
#[derive(Clone, Debug, Default)]
pub struct QueueMeta {
    /// Track URI -> creator names (e.g. `spotify:track:...` -> ["Manic Focus"]).
    pub track_artists: HashMap<String, Vec<String>>,
    /// Artist URI -> name (e.g. `spotify:artist:...` -> "Manic Focus");
    /// resolves creator entities that ship with a URI but no display name.
    pub artist_names: HashMap<String, String>,
}

/// Playback state shared between the soloist WebSocket client and the MQTT bridge.
#[derive(Clone, Debug)]
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
    /// Artist identity captured from queue_changed metadata (see `QueueMeta`)
    pub queue_meta: QueueMeta,
    /// True while the soloist daemon process is running
    pub soloist_running: bool,
    /// Last soloist event / error for diagnostics
    pub last_error: Option<String>,
    /// MQTT "power" switch: when false, ha_state is forced to "idle"
    /// regardless of the real playback status. Reporting-only — it never
    /// pauses playback.
    pub powered_on: bool,
    /// Set on power-on while the device is paused: ha_state stays "idle"
    /// until playback actually starts ("playing"/"buffering"), after which
    /// paused is reported normally until the power switch is turned off again.
    pub awaiting_playing: bool,
}

impl Default for PlaybackState {
    fn default() -> Self {
        Self {
            status: String::new(),
            track: None,
            artist: None,
            album: None,
            artwork_url: None,
            media_content_id: None,
            source: None,
            volume: 0,
            shuffle: false,
            repeat: String::new(),
            media_duration_ms: None,
            position_anchor: PositionAnchor::default(),
            logged_in: false,
            is_active: false,
            device_name: String::new(),
            upcoming: Vec::new(),
            queue_meta: QueueMeta::default(),
            soloist_running: false,
            last_error: None,
            // The power switch defaults to ON: normal reporting until the
            // user explicitly turns it off.
            powered_on: true,
            awaiting_playing: false,
        }
    }
}

impl PlaybackState {
    /// HA media player state derived from soloist status. The MQTT power
    /// switch gates this: while powered off (or waiting for playback to
    /// actually start after a power-on) the state is reported as "idle".
    pub fn ha_state(&self) -> &'static str {
        if !self.powered_on {
            return "idle";
        }
        match self.status.as_str() {
            "playing" | "buffering" => "playing",
            // Power was switched on while paused: don't report "paused"
            // until playback has actually started at least once.
            "paused" if self.awaiting_playing => "idle",
            "paused" => "paused",
            _ => "idle",
        }
    }

    /// Apply a soloist playback status, releasing the power-on gate as soon
    /// as playback actually starts (playing or buffering).
    pub fn set_status(&mut self, status: &str) {
        self.status = status.to_string();
        if matches!(status, "playing" | "buffering") {
            self.awaiting_playing = false;
        }
    }

    /// Set the MQTT power switch. Power-off forces "idle"; power-on re-arms
    /// the playing gate unless playback is already running.
    pub fn set_power(&mut self, on: bool) {
        self.powered_on = on;
        self.awaiting_playing = on && !matches!(self.status.as_str(), "playing" | "buffering");
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
///
/// The media attributes are `Option`s so they can be published as `null` while
/// the power switch is off (no stale track info for a powered-off device);
/// device-level attributes (volume, shuffle, repeat, ...) are always present.
#[derive(Serialize)]
pub struct StateAttributes<'a> {
    pub media_title: Option<&'a str>,
    pub media_artist: Option<&'a str>,
    pub media_album_name: Option<&'a str>,
    pub media_image_url: Option<&'a str>,
    pub volume: f64,
    pub is_volume_muted: bool,
    pub media_position: Option<u64>,
    pub media_duration: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_position_updated_at: Option<String>,
    pub media_content_id: Option<&'a str>,
    pub source: Option<&'a str>,
    pub shuffle: bool,
    pub repeat: &'a str,
    pub device_name: &'a str,
    pub logged_in: bool,
    pub is_active: bool,
    pub upcoming: Option<&'a [String]>,
}

impl<'a> PlaybackState {
    pub fn attributes(&'a self) -> StateAttributes<'a> {
        // While the power switch is off, media attributes are nulled so HA
        // doesn't show stale playback info for a powered-off device. The
        // device-level attributes (volume, shuffle, repeat, ...) stay.
        let media_visible = self.powered_on;
        let position_updated_at = if media_visible && self.position_anchor.timestamp_ms > 0 {
            chrono::DateTime::from_timestamp_millis(self.position_anchor.timestamp_ms)
                .map(|dt| dt.to_rfc3339())
        } else {
            None
        };

        StateAttributes {
            media_title: media_visible.then_some(self.track.as_deref().unwrap_or("Unknown")),
            media_artist: media_visible.then_some(self.artist.as_deref().unwrap_or("Unknown")),
            media_album_name: media_visible.then_some(self.album.as_deref().unwrap_or("Unknown")),
            media_image_url: media_visible.then_some(self.artwork_url.as_deref().unwrap_or("")),
            volume: self.volume_level(),
            is_volume_muted: self.volume == 0,
            media_position: if media_visible {
                self.media_position_secs()
            } else {
                None
            },
            media_duration: if media_visible {
                self.media_duration_secs()
            } else {
                None
            },
            media_position_updated_at: position_updated_at,
            media_content_id: media_visible
                .then_some(self.media_content_id.as_deref().unwrap_or("")),
            source: media_visible.then_some(self.source.as_deref().unwrap_or("")),
            shuffle: self.shuffle,
            repeat: &self.repeat,
            device_name: &self.device_name,
            logged_in: self.logged_in,
            is_active: self.is_active,
            upcoming: media_visible.then_some(&self.upcoming),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn power_off_forces_idle() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            ..Default::default()
        };
        assert_eq!(st.ha_state(), "playing");

        st.set_power(false);
        assert_eq!(st.ha_state(), "idle");

        // Turning it back on while still playing resumes normal reporting.
        st.set_power(true);
        assert!(!st.awaiting_playing);
        assert_eq!(st.ha_state(), "playing");
    }

    #[test]
    fn power_on_while_paused_gates_until_playing() {
        let mut st = PlaybackState {
            status: "paused".to_string(),
            ..Default::default()
        };
        // Powered off, then on again while the device is paused.
        st.set_power(false);
        st.set_power(true);
        assert!(st.awaiting_playing);
        assert_eq!(st.ha_state(), "idle");

        // Still gated on a repeated paused event...
        st.set_status("paused");
        assert_eq!(st.ha_state(), "idle");

        // ...until playback actually starts.
        st.set_status("playing");
        assert!(!st.awaiting_playing);
        assert_eq!(st.ha_state(), "playing");

        // After that, paused is reported normally.
        st.set_status("paused");
        assert_eq!(st.ha_state(), "paused");
    }

    #[test]
    fn power_on_while_playing_does_not_gate() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            ..Default::default()
        };
        st.set_power(true);
        assert!(!st.awaiting_playing);

        // A later pause is reported normally (playback already started).
        st.set_status("paused");
        assert_eq!(st.ha_state(), "paused");
    }

    #[test]
    fn buffering_releases_the_gate() {
        let mut st = PlaybackState {
            status: "paused".to_string(),
            ..Default::default()
        };
        st.set_power(true);
        assert_eq!(st.ha_state(), "idle");

        st.set_status("buffering");
        assert!(!st.awaiting_playing);
        assert_eq!(st.ha_state(), "playing");
    }

    #[test]
    fn default_state_is_powered_on() {
        let st = PlaybackState::default();
        assert!(st.powered_on);
        assert!(!st.awaiting_playing);
        assert_eq!(st.ha_state(), "idle");
    }

    #[test]
    fn media_attributes_are_nulled_while_powered_off() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            track: Some("My Song".to_string()),
            artist: Some("Artist".to_string()),
            album: Some("Album".to_string()),
            artwork_url: Some("https://i.scdn.co/x".to_string()),
            media_content_id: Some("spotify:track:abc".to_string()),
            source: Some("Playlist".to_string()),
            volume: 42,
            upcoming: vec!["Next".to_string()],
            ..Default::default()
        };

        // Powered on: media attributes are populated.
        let on = st.attributes();
        assert_eq!(on.media_title, Some("My Song"));
        assert_eq!(on.media_artist, Some("Artist"));
        assert_eq!(on.media_album_name, Some("Album"));
        assert_eq!(on.media_image_url, Some("https://i.scdn.co/x"));
        assert_eq!(on.media_content_id, Some("spotify:track:abc"));
        assert_eq!(on.source, Some("Playlist"));
        assert!(on.upcoming.is_some());

        st.set_power(false);
        let off = st.attributes();
        assert_eq!(off.media_title, None);
        assert_eq!(off.media_artist, None);
        assert_eq!(off.media_album_name, None);
        assert_eq!(off.media_image_url, None);
        assert_eq!(off.media_content_id, None);
        assert_eq!(off.source, None);
        assert_eq!(off.upcoming, None);
        assert_eq!(off.media_position, None);
        assert_eq!(off.media_duration, None);
        // Device-level attributes remain available.
        assert_eq!(off.volume, 0.42);
        assert!(!off.is_volume_muted);
        assert_eq!(off.repeat, "");
        assert_eq!(off.device_name, "");
        assert!(!off.logged_in);
        assert!(!off.is_active);
    }

    #[test]
    fn powered_off_attributes_serialize_as_null() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            track: Some("My Song".to_string()),
            volume: 42,
            ..Default::default()
        };
        st.set_power(false);
        let v = serde_json::to_value(st.attributes()).unwrap();
        assert_eq!(v["media_title"], serde_json::Value::Null);
        assert_eq!(v["source"], serde_json::Value::Null);
        assert_eq!(v["upcoming"], serde_json::Value::Null);
        // Device-level attributes stay populated; position anchor is omitted.
        assert_eq!(v["volume"], 0.42);
        assert!(v.get("media_position_updated_at").is_none());
    }
}
