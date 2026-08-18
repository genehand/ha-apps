//! Wire-format model for the Spotify Soloist WebSocket API (mirrors the
//! Soloist WebSocket API reference). These types are only ever deserialized
//! from daemon messages; the commands the bridge sends back live in
//! [`super::commands`].

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub(crate) struct Entity {
    #[serde(default)]
    pub uri: String,
    #[allow(dead_code)] // part of the wire format; kept for future use
    #[serde(default)]
    pub entity_type: String,
    #[serde(default)]
    pub decorations: Decorations,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct Decorations {
    #[serde(default)]
    pub identity: Identity,
    #[serde(default)]
    pub visual_identity: VisualIdentity,
    #[serde(default)]
    pub parent: Option<Parent>,
    #[serde(default)]
    pub creators: Vec<Creator>,
    #[serde(default)]
    pub playback: ItemPlayback,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct Identity {
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct VisualIdentity {
    #[serde(default)]
    pub cover: Vec<CoverImage>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct CoverImage {
    pub url: String,
    #[serde(default)]
    pub size: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct Parent {
    #[serde(default)]
    pub entity: Option<Box<Entity>>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct Creator {
    #[serde(default)]
    pub entity: Option<Box<Entity>>,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct ItemPlayback {
    #[serde(default)]
    pub duration_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct Position {
    #[serde(default)]
    pub position_ms: u64,
    #[serde(default)]
    pub timestamp_ms: i64,
    #[serde(default)]
    pub speed: f64,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct PlaybackOptions {
    #[serde(default)]
    pub shuffle: bool,
    #[serde(default)]
    pub repeat: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct QueueEntry {
    #[allow(dead_code)] // part of the wire format; kept for future use
    #[serde(default)]
    pub uid: String,
    #[allow(dead_code)]
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub item: Option<Entity>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)]
pub(crate) enum SoloistEvent {
    AuthState {
        logged_in: bool,
        #[serde(default)]
        is_active: bool,
        #[serde(default)]
        device_name: String,
    },
    PlaybackState {
        status: String,
        #[serde(default)]
        item: Option<Entity>,
        #[serde(default)]
        context: Option<Entity>,
        #[serde(default)]
        position: Option<Position>,
        #[serde(default)]
        volume: Option<u8>,
        #[serde(default)]
        is_active: Option<bool>,
        #[serde(default)]
        options: Option<PlaybackOptions>,
    },
    TrackChanged {
        item: Entity,
    },
    PlaybackChanged {
        status: String,
    },
    VolumeChanged {
        volume: u8,
    },
    DeviceChanged {
        is_active: bool,
        #[serde(default)]
        device_name: String,
    },
    ContextChanged {
        context: Entity,
    },
    OptionsChanged {
        options: PlaybackOptions,
    },
    PositionSync {
        position: Position,
    },
    QueueChanged {
        #[allow(dead_code)]
        #[serde(default)]
        previous: Vec<QueueEntry>,
        #[serde(default)]
        upcoming: Vec<QueueEntry>,
    },
    CommandResult {
        #[serde(default)]
        command: String,
    },
    Error {
        #[serde(default)]
        message: String,
    },
    #[serde(other)]
    Unknown,
}
