use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::soloist::events::AvailableActions;

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

impl BoundedCache<TrackMeta> {
    pub fn get(&self, uri: &str) -> Option<&TrackMeta> {
        self.entries.get(uri)
    }
}

/// Track metadata resolved from Spotify's public embed page (see
/// [`crate::soloist::embed`]). soloist's first snapshot for a new track can
/// ship nothing but the URI and the duration — no identity, no creators, no
/// parent album, no cover — in which case there is no artist URI to feed the
/// oEmbed fallback and every label degrades to "Unknown". The embed page
/// needs no auth and carries the missing track fields. Cached per track URI
/// (bounded FIFO) so a repeated bare snapshot doesn't blank the labels again.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TrackMeta {
    pub track: Option<String>,
    pub artist: Option<String>,
    /// Primary artist URI from the embed payload; seeds the oEmbed name cache
    /// so other tracks by the same artist resolve without another lookup.
    pub artist_uri: Option<String>,
    pub album: Option<String>,
    pub artwork: Option<String>,
    pub duration_ms: Option<u64>,
}

impl TrackMeta {
    /// True when the payload carried nothing usable (no point caching it).
    pub fn is_empty(&self) -> bool {
        self.track.is_none()
            && self.artist.is_none()
            && self.album.is_none()
            && self.artwork.is_none()
            && self.duration_ms.is_none()
    }
}

/// A creator of the currently playing item as seen in the latest snapshot:
/// artist URI plus the best name known at snapshot time (`None` when the
/// snapshot shipped a URI but no display name and neither the queue metadata
/// nor the oEmbed cache had one). Kept so a later oEmbed result can refresh
/// the artist label without re-parsing an event.
///
/// `uri` is optional because soloist may omit it: the API reference states
/// that "nested related entities may omit `uri` when unknown". A creator with
/// a name but no URI is still displayable (only lookups need the URI), so it
/// must not be discarded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CreatorRef {
    pub uri: Option<String>,
    pub name: Option<String>,
}

/// Push a name onto a label list unless already present (only the first name
/// is used, so duplicates must not shift which creator is primary).
fn push_unique(names: &mut Vec<String>, n: String) {
    if !names.contains(&n) {
        names.push(n);
    }
}

/// What kind of item is playing, as far as Home Assistant is concerned. Soloist
/// reports this as the item's `entity_type`; an episode's `parent` is its show,
/// not an album.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ContentKind {
    #[default]
    Music,
    /// `entity_type: "episode"` -> HA `media_content_type: podcast`.
    Podcast,
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
    /// WebSocket connection health: true while a session with the soloist API is
    /// open. Mirrored into the MQTT availability topic together with the daemon
    /// supervisor, so a dead daemon (or one that failed to enable `--ws`) makes
    /// the entities unavailable instead of leaving them on the last snapshot.
    pub ws_connected: bool,
    /// Actions soloist currently advertises as possible for this playback state
    /// (`available_actions`), keyed by player action name. Empty when unknown.
    pub available_actions: AvailableActions,
    /// First `content_ratings` label of the current item, e.g. `explicit`.
    pub content_rating: Option<String>,
    /// Music or podcast episode (see [`ContentKind`]).
    pub content_kind: ContentKind,
    /// Show of the current podcast episode (`parent.entity`); for a music track
    /// the parent is the album and lands in `album` instead.
    pub media_series_title: Option<String>,
    /// True while the reported item is an `ad` entity: the identity of the last
    /// real track is kept and only this flag changes.
    pub is_ad: bool,
    pub upcoming: Vec<String>,
    /// Artist identity captured from queue_changed metadata (see `QueueMeta`)
    pub queue_meta: QueueMeta,
    /// Artist URI -> name, from the oEmbed API (see [`crate::soloist::oembed`])
    /// and seeded by resolved embed-page metadata (see
    /// [`crate::soloist::embed`]). Final fallback when a creator ships a URI
    /// but no name and the queue metadata has none. Persistent: survives
    /// queue rotation.
    pub oembed: BoundedCache<String>,
    /// Track URI -> creator artist URIs, learned from queue snapshots while
    /// a track was upcoming. Persistent (bounded) so a track whose playback
    /// snapshot ships with empty creator decorations still resolves its
    /// artist via the URIs — even when queue rotation races the snapshot.
    pub track_artist_uris: BoundedCache<Vec<String>>,
    /// Track URI -> metadata resolved from Spotify's public embed page (see
    /// [`crate::soloist::embed`]). Fills track/artist/album/artwork/duration
    /// when a snapshot ships them empty, and stays put across the repeated
    /// bare snapshots soloist sends for the same track.
    pub track_meta: BoundedCache<TrackMeta>,
    /// Track URIs whose embed-page lookup has completed (successfully or
    /// not). Bounds the fallback to one request per track per session, so an
    /// unplayable/private track can't be retried on every event.
    pub track_meta_fetched: BoundedCache<()>,
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
    /// direct names captured from that same snapshot, and finally to the
    /// artist resolved from Spotify's embed page for this track URI
    /// (see [`crate::soloist::embed`]) — which is the only path left when the
    /// snapshot ships *no* creators at all and no queue event ever saw the
    /// track. None when nothing resolved.
    pub fn artist_label(&self) -> Option<String> {
        let mut names: Vec<String> = Vec::new();
        // 1. Creators seen in the playback snapshot.
        for c in &self.item_creators {
            // A creator that shipped a name but no URI (soloist omits nested
            // URIs when unknown) still names the artist.
            let name = c
                .name
                .clone()
                .or_else(|| c.uri.as_deref().and_then(|u| self.named_artist(u)));
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
                        if let Some(n) = self.named_artist(u) {
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
            // 4. Embed-page metadata (see `soloist::embed`): the last resort
            // when the snapshot shipped no creators *and* the queue never
            // taught us this track's artist URIs.
            if let Some(track) = self.media_content_id.as_deref() {
                if let Some(name) = self.track_meta.get(track).and_then(|m| m.artist.clone()) {
                    push_unique(&mut names, name);
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

    /// A known name for an artist URI: queue metadata first, then the
    /// oEmbed/embed-page name cache.
    fn named_artist(&self, uri: &str) -> Option<String> {
        self.queue_meta
            .artist_names
            .get(uri)
            .cloned()
            .or_else(|| self.oembed.get(uri).map(str::to_string))
    }

    /// Recompute the published artist after an asynchronous lookup landed,
    /// returning true when the label changed (so the caller publishes).
    ///
    /// Deliberately never downgrades a known name to `None`: `artist_label`
    /// only sees the latest snapshot, while `apply_entity` keeps the last
    /// published name for a track whose repeated snapshots are bare. Clearing
    /// the artist is `apply_entity`'s job — it drops it when the track URI
    /// actually changes — so a lookup that resolves an unrelated artist must
    /// not blank what is on screen.
    pub fn refresh_artist(&mut self) -> bool {
        let label = self.artist_label().or_else(|| self.artist.clone());
        if self.artist == label {
            return false;
        }
        self.artist = label;
        true
    }

    /// Drop the current item's identity. The metadata caches (oEmbed artist
    /// names, embed-page track metadata, learned track -> artist URIs) are
    /// session-independent and deliberately survive.
    pub fn clear_item(&mut self) {
        self.track = None;
        self.artist = None;
        self.album = None;
        self.artwork_url = None;
        self.media_content_id = None;
        self.media_duration_ms = None;
        self.item_creators.clear();
        self.content_rating = None;
        self.media_series_title = None;
        self.content_kind = ContentKind::Music;
        self.is_ad = false;
    }

    /// Drop everything that belongs to the live session — item identity, context,
    /// queue and advertised actions — and fall back to idle. Used when the
    /// WebSocket connection ends and when soloist reports a logout: in both cases
    /// the previous session's data is no longer true, and continuing to publish
    /// it would leave Home Assistant showing a track that is not playing.
    /// `logged_in` / `ws_connected` are the caller's business.
    pub fn clear_session(&mut self) {
        self.clear_item();
        self.source = None;
        self.upcoming.clear();
        self.available_actions.clear();
        self.set_status("idle");
        self.is_active = false;
    }

    /// Step size for relative seeks, when soloist advertises one
    /// (`available_actions.seek_forward` / `seek_backward` carry `step_ms`).
    pub fn seek_step_ms(&self) -> Option<u64> {
        ["seek_forward", "seek_backward"]
            .iter()
            .find_map(|name| self.available_actions.get(*name).and_then(|a| a.step_ms))
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
    /// Show name for a podcast episode (`media_content_type: podcast`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_series_title: Option<&'a str>,
    /// `music` or `podcast` (soloist item `entity_type`).
    pub media_content_type: Option<&'a str>,
    /// First `content_ratings` label of the item, e.g. `explicit`. Published
    /// under the HA media-player attribute name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_content_rating: Option<&'a str>,
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
    /// True while soloist is reporting an `ad` item (the track labels below are
    /// still the last real track).
    pub is_ad: bool,
    /// Advertised player action names, e.g. `["pause", "seek", "skip_next"]`.
    pub available_actions: Option<Vec<&'a str>>,
    /// Relative-seek step advertised by soloist, when there is one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seek_step_ms: Option<u64>,
    pub device_name: &'a str,
    pub logged_in: bool,
    pub is_active: bool,
    /// True while the soloist WebSocket API is connected (same signal that
    /// drives MQTT availability).
    pub soloist_connected: bool,
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
            media_series_title: if media_visible {
                self.media_series_title.as_deref()
            } else {
                None
            },
            media_content_type: media_visible.then_some(match self.content_kind {
                ContentKind::Podcast => "podcast",
                ContentKind::Music => "music",
            }),
            media_content_rating: if media_visible {
                self.content_rating.as_deref()
            } else {
                None
            },
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
            is_ad: media_visible && self.is_ad,
            // BTreeMap keys: already sorted, so the published list is stable.
            available_actions: media_visible.then(|| {
                self.available_actions
                    .keys()
                    .map(String::as_str)
                    .collect::<Vec<&str>>()
            }),
            seek_step_ms: if media_visible {
                self.seek_step_ms()
            } else {
                None
            },
            device_name: &self.device_name,
            logged_in: self.logged_in,
            is_active: self.is_active,
            soloist_connected: self.ws_connected,
            upcoming: media_visible.then_some(&self.upcoming),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::soloist::events::ActionParams;

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
                uri: Some("spotify:artist:xyz".to_string()),
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
                    uri: Some("spotify:artist:1".to_string()),
                    name: Some("Inline".to_string()),
                },
                CreatorRef {
                    uri: Some("spotify:artist:2".to_string()),
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
    fn artist_label_falls_back_to_embed_metadata() {
        let mut st = PlaybackState {
            media_content_id: Some("spotify:track:abc".to_string()),
            ..Default::default()
        };
        // The embed-page fallback resolved an artist for this track URI — the
        // only source left when the snapshot shipped no creators at all and no
        // queue event ever saw the track.
        st.track_meta.insert(
            "spotify:track:abc".to_string(),
            TrackMeta {
                track: Some("Today - 2011 Remaster".to_string()),
                artist: Some("The Smashing Pumpkins".to_string()),
                ..Default::default()
            },
        );
        assert_eq!(st.artist_label(), Some("The Smashing Pumpkins".to_string()));
        // A name learned from the queue still outranks the embed metadata.
        st.queue_meta.track_artists.insert(
            "spotify:track:abc".to_string(),
            vec!["FromQueue".to_string()],
        );
        assert_eq!(st.artist_label(), Some("FromQueue".to_string()));
    }

    #[test]
    fn refresh_artist_never_downgrades_to_none() {
        let mut st = PlaybackState {
            artist: Some("Kept".to_string()),
            media_content_id: Some("spotify:track:abc".to_string()),
            ..Default::default()
        };
        // Nothing in the resolution chain knows this track's artist, but the
        // published one must survive (it is cleared on a track change only).
        assert!(!st.refresh_artist());
        assert_eq!(st.artist.as_deref(), Some("Kept"));
        // A newly resolvable name does win.
        st.oembed
            .insert("spotify:artist:xyz".to_string(), "Better".to_string());
        st.track_artist_uris.insert(
            "spotify:track:abc".to_string(),
            vec!["spotify:artist:xyz".to_string()],
        );
        assert!(st.refresh_artist());
        assert_eq!(st.artist.as_deref(), Some("Better"));
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

    #[test]
    fn clear_session_drops_session_data_but_keeps_caches() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            track: Some("My Song".to_string()),
            artist: Some("Artist".to_string()),
            album: Some("Album".to_string()),
            artwork_url: Some("https://i.scdn.co/x".to_string()),
            media_content_id: Some("spotify:track:abc".to_string()),
            media_duration_ms: Some(1000),
            source: Some("Playlist".to_string()),
            is_active: true,
            upcoming: vec!["Next".to_string()],
            content_rating: Some("explicit".to_string()),
            media_series_title: Some("Show".to_string()),
            content_kind: ContentKind::Podcast,
            is_ad: true,
            item_creators: vec![CreatorRef {
                uri: Some("spotify:artist:xyz".to_string()),
                name: Some("Artist".to_string()),
            }],
            ..Default::default()
        };
        st.available_actions
            .insert("pause".to_string(), Default::default());
        st.oembed
            .insert("spotify:artist:xyz".to_string(), "Artist".to_string());
        st.track_artist_uris.insert(
            "spotify:track:abc".to_string(),
            vec!["spotify:artist:xyz".to_string()],
        );
        st.track_meta.insert(
            "spotify:track:abc".to_string(),
            TrackMeta {
                track: Some("My Song".to_string()),
                ..Default::default()
            },
        );

        st.clear_session();

        assert_eq!(st.ha_state(), "idle");
        assert!(!st.is_active);
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.album, None);
        assert_eq!(st.artwork_url, None);
        assert_eq!(st.media_content_id, None);
        assert_eq!(st.media_duration_ms, None);
        assert_eq!(st.source, None);
        assert!(st.upcoming.is_empty());
        assert!(st.available_actions.is_empty());
        assert_eq!(st.content_rating, None);
        assert_eq!(st.media_series_title, None);
        assert_eq!(st.content_kind, ContentKind::Music);
        assert!(!st.is_ad);
        assert!(st.item_creators.is_empty());
        // The metadata caches are session-independent and must survive, so the
        // next session resolves artists/metadata without re-fetching.
        assert_eq!(st.oembed.get("spotify:artist:xyz"), Some("Artist"));
        assert!(st.track_artist_uris.contains("spotify:track:abc"));
        assert!(st.track_meta.contains("spotify:track:abc"));
    }

    #[test]
    fn media_content_type_follows_content_kind_and_power() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            content_kind: ContentKind::Podcast,
            media_series_title: Some("Some Show".to_string()),
            content_rating: Some("explicit".to_string()),
            is_ad: true,
            ..Default::default()
        };
        st.set_power(true);
        let attrs = st.attributes();
        assert_eq!(attrs.media_content_type, Some("podcast"));
        assert_eq!(attrs.media_series_title, Some("Some Show"));
        assert_eq!(attrs.media_content_rating, Some("explicit"));
        assert!(attrs.is_ad);

        st.content_kind = ContentKind::Music;
        st.media_series_title = None;
        st.content_rating = None;
        let attrs = st.attributes();
        assert_eq!(attrs.media_content_type, Some("music"));
        assert_eq!(attrs.media_series_title, None);
        assert_eq!(attrs.media_content_rating, None);

        // Power off nulls the media-side attributes like the rest of the track
        // info (the series title and content rating are omitted entirely).
        st.set_power(false);
        let attrs = st.attributes();
        assert_eq!(attrs.media_content_type, None);
        assert_eq!(attrs.media_series_title, None);
        assert_eq!(attrs.media_content_rating, None);
        assert!(!attrs.is_ad);
        let v = serde_json::to_value(&attrs).unwrap();
        assert_eq!(v["media_content_type"], serde_json::Value::Null);
        assert!(v.get("media_series_title").is_none());
    }

    #[test]
    fn available_actions_are_published_sorted_with_seek_step() {
        let mut st = PlaybackState::default();
        st.set_power(true);
        st.available_actions.insert(
            "seek_backward".to_string(),
            ActionParams {
                step_ms: Some(15000),
            },
        );
        st.available_actions
            .insert("pause".to_string(), Default::default());
        st.available_actions
            .insert("skip_next".to_string(), Default::default());

        let attrs = st.attributes();
        assert_eq!(
            attrs.available_actions,
            Some(vec!["pause", "seek_backward", "skip_next"])
        );
        assert_eq!(attrs.seek_step_ms, Some(15000));

        // Nothing advertised: an empty list while powered on, nothing at all
        // while powered off.
        st.available_actions.clear();
        assert_eq!(st.attributes().available_actions, Some(vec![]));
        assert_eq!(st.attributes().seek_step_ms, None);
        st.set_power(false);
        assert_eq!(st.attributes().available_actions, None);
        assert_eq!(st.attributes().seek_step_ms, None);
    }

    /// The attribute JSON is a contract with the README's template-player config
    /// (and with users' automations): a renamed or dropped key breaks them
    /// silently, so pin the exact key set. Adding an attribute is intentional —
    /// update this list together with README.md.
    #[test]
    fn attribute_keys_are_stable() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            track: Some("My Song".to_string()),
            artist: Some("Artist".to_string()),
            album: Some("Album".to_string()),
            artwork_url: Some("https://i.scdn.co/x".to_string()),
            media_content_id: Some("spotify:track:abc".to_string()),
            source: Some("Playlist".to_string()),
            media_duration_ms: Some(1000),
            volume: 42,
            shuffle: true,
            repeat: "all".to_string(),
            logged_in: true,
            is_active: true,
            ws_connected: true,
            device_name: "Soloist".to_string(),
            content_kind: ContentKind::Podcast,
            media_series_title: Some("Some Show".to_string()),
            content_rating: Some("explicit".to_string()),
            position_anchor: PositionAnchor {
                position_ms: 1000,
                timestamp_ms: 1_747_654_321_000,
                speed: 1.0,
            },
            ..Default::default()
        };
        st.set_power(true);
        st.available_actions
            .insert("pause".to_string(), ActionParams::default());
        st.upcoming = vec!["Next".to_string()];

        let v = serde_json::to_value(st.attributes()).unwrap();
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec![
                "available_actions",
                "device_name",
                "is_active",
                "is_ad",
                "is_volume_muted",
                "logged_in",
                "media_album_name",
                "media_artist",
                "media_content_id",
                "media_content_rating",
                "media_content_type",
                "media_duration",
                "media_image_url",
                "media_position",
                "media_position_updated_at",
                "media_series_title",
                "media_title",
                "repeat",
                "shuffle",
                "soloist_connected",
                "source",
                "upcoming",
                "volume",
            ]
        );
        // `seek_step_ms` is optional and omitted when soloist advertises no step.
        assert!(v.get("seek_step_ms").is_none());
        assert_eq!(v["media_content_type"], "podcast");
        assert_eq!(v["media_content_rating"], "explicit");
        assert_eq!(v["available_actions"], serde_json::json!(["pause"]));
    }
}
