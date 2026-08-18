use serde::Serialize;
use std::collections::{HashMap, VecDeque};
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

/// Bounded FIFO cache keyed by Spotify URI, surviving queue rotation (once
/// an entry is known it stays known for the rest of the session). Used for
/// oEmbed artist names (artist URI -> name) and for the track -> creator
/// artist URIs learned from queue snapshots.
#[derive(Clone, Debug, Default)]
pub struct BoundedCache<V> {
    entries: HashMap<String, V>,
    /// Insertion order for FIFO eviction (URI of each cache entry).
    order: VecDeque<String>,
}

impl<V> BoundedCache<V> {
    const CAP: usize = 64;

    pub fn contains(&self, uri: &str) -> bool {
        self.entries.contains_key(uri)
    }

    /// Insert (or refresh) a value, evicting the oldest entries when the
    /// cache exceeds its cap so it stays bounded over a long session.
    pub fn insert(&mut self, uri: String, value: V) {
        if !self.entries.contains_key(&uri) {
            self.order.push_back(uri.clone());
        }
        self.entries.insert(uri, value);
        while self.order.len() > Self::CAP {
            if let Some(old) = self.order.pop_front() {
                self.entries.remove(&old);
            }
        }
    }
}

impl BoundedCache<String> {
    pub fn get(&self, uri: &str) -> Option<&str> {
        self.entries.get(uri).map(String::as_str)
    }
}

impl BoundedCache<Vec<String>> {
    pub fn get(&self, uri: &str) -> Option<&[String]> {
        self.entries.get(uri).map(Vec::as_slice)
    }
}

/// A creator of the currently playing item as seen in the latest snapshot:
/// artist URI plus the best name known at snapshot time (`None` when the
/// snapshot shipped a URI but no display name and neither the queue metadata
/// nor the oEmbed cache had one). Kept so a later oEmbed result can refresh
/// the artist label without re-parsing an event.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CreatorRef {
    pub uri: String,
    pub name: Option<String>,
}

/// Push a name onto a label list unless already present (only the first name
/// is used, so duplicates must not shift which creator is primary).
fn push_unique(names: &mut Vec<String>, n: String) {
    if !names.contains(&n) {
        names.push(n);
    }
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
    /// Artist identity captured from queue_changed metadata (see `QueueMeta`)
    pub queue_meta: QueueMeta,
    /// Artist names resolved via Spotify's oEmbed API (artist URI -> name).
    /// Final fallback when a creator ships a URI but no name and the queue
    /// metadata has none. Persistent: survives queue rotation.
    pub oembed: BoundedCache<String>,
    /// Track URI -> creator artist URIs, learned from queue snapshots while
    /// a track was upcoming. Persistent (bounded) so a track whose playback
    /// snapshot ships with empty creator decorations still resolves its
    /// artist via the URIs — even when queue rotation races the snapshot.
    pub track_artist_uris: BoundedCache<Vec<String>>,
    /// Creators of the currently playing item (artist URI + snapshot-time
    /// name); used to refresh the artist label when an oEmbed lookup lands.
    pub item_creators: Vec<CreatorRef>,
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

    /// Artist label for the currently playing item. Resolution per creator:
    /// snapshot-time name, then queue metadata, then the oEmbed cache (which
    /// may have been filled since the snapshot landed). When the snapshot
    /// shipped no creator URIs at all — soloist's first snapshot for a new
    /// track often ships empty creator decorations — falls back to the creator
    /// URIs captured from the `queue_changed` snapshot while this track was
    /// upcoming (resolved via queue metadata / oEmbed cache), then to the
    /// direct names captured from that same snapshot. None when no creator
    /// has a resolvable name.
    pub fn artist_label(&self) -> Option<String> {
        let mut names: Vec<String> = Vec::new();
        // 1. Creators seen in the playback snapshot.
        for c in &self.item_creators {
            let name = c
                .name
                .clone()
                .or_else(|| self.queue_meta.artist_names.get(&c.uri).cloned())
                .or_else(|| self.oembed.get(&c.uri).map(str::to_string));
            if let Some(n) = name {
                push_unique(&mut names, n);
            }
        }
        if names.is_empty() {
            // 2. Snapshot shipped no creator URIs: use the artist URIs seen
            // in queue snapshots while this track was upcoming (persistent
            // cache, so it survives queue rotation racing the snapshot).
            if let Some(track) = self.media_content_id.as_deref() {
                if let Some(uris) = self.track_artist_uris.get(track) {
                    for u in uris {
                        let name = self
                            .queue_meta
                            .artist_names
                            .get(u)
                            .cloned()
                            .or_else(|| self.oembed.get(u).map(str::to_string));
                        if let Some(n) = name {
                            push_unique(&mut names, n);
                        }
                    }
                }
            }
        }
        if names.is_empty() {
            // 3. Direct names from the queue snapshot (covers creators that
            // shipped with a name but no URI).
            if let Some(track) = self.media_content_id.as_deref() {
                if let Some(from_queue) = self.queue_meta.track_artists.get(track) {
                    for n in from_queue {
                        push_unique(&mut names, n.clone());
                    }
                }
            }
        }
        if names.is_empty() {
            None
        } else {
            // Only the first (primary) creator is shown.
            names.into_iter().next()
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
        st.set_power(true);
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
    fn default_state_is_powered_off() {
        let st = PlaybackState::default();
        assert!(!st.powered_on);
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

        // Default is powered off. Switch it on so the media attributes
        // are visible for the first half of the test.
        st.set_power(true);

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

    #[test]
    fn oembed_cache_is_bounded_fifo() {
        let mut cache = BoundedCache::<String>::default();
        for i in 0..70 {
            cache.insert(format!("spotify:artist:{}", i), format!("Artist {}", i));
        }
        // The 6 oldest entries were evicted to stay at the cap.
        assert!(!cache.contains("spotify:artist:0"));
        assert!(!cache.contains("spotify:artist:5"));
        assert!(cache.contains("spotify:artist:6"));
        assert_eq!(cache.get("spotify:artist:69"), Some("Artist 69"));
        // Refreshing an existing key doesn't evict anything...
        cache.insert("spotify:artist:69".to_string(), "Artist 69!".to_string());
        assert!(cache.contains("spotify:artist:6"));
        assert_eq!(cache.get("spotify:artist:69"), Some("Artist 69!"));
        // ...and a new insert evicts the next-oldest entry.
        cache.insert("spotify:artist:70".to_string(), "Artist 70".to_string());
        assert!(!cache.contains("spotify:artist:6"));
        assert_eq!(cache.get("spotify:artist:70"), Some("Artist 70"));
    }

    #[test]
    fn artist_label_resolves_from_oembed_cache() {
        let mut st = PlaybackState {
            item_creators: vec![CreatorRef {
                uri: "spotify:artist:xyz".to_string(),
                name: None,
            }],
            ..Default::default()
        };
        assert_eq!(st.artist_label(), None);
        st.oembed
            .insert("spotify:artist:xyz".to_string(), "Manic Focus".to_string());
        assert_eq!(st.artist_label(), Some("Manic Focus".to_string()));
    }

    #[test]
    fn artist_label_prefers_inline_then_queue_then_oembed() {
        let mut st = PlaybackState {
            item_creators: vec![
                CreatorRef {
                    uri: "spotify:artist:1".to_string(),
                    name: Some("Inline".to_string()),
                },
                CreatorRef {
                    uri: "spotify:artist:2".to_string(),
                    name: None,
                },
            ],
            ..Default::default()
        };
        st.queue_meta
            .artist_names
            .insert("spotify:artist:2".to_string(), "FromQueue".to_string());
        // oEmbed is the last resort: a cached name loses to queue metadata.
        st.oembed
            .insert("spotify:artist:2".to_string(), "FromOEmbed".to_string());
        assert_eq!(st.artist_label(), Some("Inline".to_string()));
    }

    #[test]
    fn artist_label_uses_queue_uris_when_snapshot_has_no_creators() {
        let mut st = PlaybackState {
            media_content_id: Some("spotify:track:abc".to_string()),
            ..Default::default()
        };
        // The track was seen in the queue with an artist URI but no name.
        st.track_artist_uris.insert(
            "spotify:track:abc".to_string(),
            vec!["spotify:artist:xyz".to_string()],
        );
        assert_eq!(st.artist_label(), None);
        // oEmbed later resolves the artist URI: the label appears even though
        // the playback snapshot shipped with empty creator decorations.
        st.oembed
            .insert("spotify:artist:xyz".to_string(), "Manic Focus".to_string());
        assert_eq!(st.artist_label(), Some("Manic Focus".to_string()));
    }
}
