//! Wire-format model for the Spotify Soloist WebSocket API (mirrors the
//! Soloist WebSocket API reference). These types are only ever deserialized
//! from daemon messages; the commands the bridge sends back live in
//! [`super::commands`].
//!
//! Two decoding rules apply here, both to keep a partially-understood payload
//! from costing us the whole event (a dropped `playback_state` means stale
//! metadata in Home Assistant until the next one):
//!
//! - Fields that are only meaningful when present are `Option`s, so a stripped
//!   payload is ignored per field instead of aborting the parse.
//! - Numbers are decoded tolerantly ([`de_u8`] and friends): a value that
//!   arrives as a float (`65.0`) or wider than the target type is rounded /
//!   saturated rather than rejected.

use serde::{Deserialize, Deserializer};
use std::collections::BTreeMap;

/// Decode any JSON number as `f64`. Rust's float-to-integer casts saturate
/// (`300.0 as u8 == 255`, `-5.0 as u8 == 0`), which is exactly the coercion we
/// want for the numeric fields below.
fn de_f64<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    f64::deserialize(d)
}

/// Tolerant numeric field decoding (see the module docs).
macro_rules! tolerant_num {
    ($fn_name:ident, $ty:ty) => {
        fn $fn_name<'de, D: Deserializer<'de>>(d: D) -> Result<$ty, D::Error> {
            Ok(de_f64(d)? as $ty)
        }
    };
    ($fn_name:ident, $ty:ty, opt) => {
        fn $fn_name<'de, D: Deserializer<'de>>(d: D) -> Result<Option<$ty>, D::Error> {
            Ok(Option::<f64>::deserialize(d)?.map(|v| v as $ty))
        }
    };
}

tolerant_num!(de_u64, u64);
tolerant_num!(de_i64, i64);
tolerant_num!(de_opt_u8, u8, opt);
tolerant_num!(de_opt_u64, u64, opt);

#[derive(Debug, Deserialize)]
pub(crate) struct Entity {
    #[serde(default)]
    pub uri: String,
    /// `track`, `episode`, `artist`, `album`, `playlist`, `show`, `ad` or
    /// `unknown` (empty when no entity is available). Drives the published
    /// `media_content_type` and the episode/ad special cases in
    /// [`super::apply`].
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
    /// Defaults to empty so a cover entry without a URL cannot fail the whole
    /// entity; [`super::apply::best_cover`] skips empty URLs.
    #[serde(default)]
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
    #[serde(default, deserialize_with = "de_opt_u64")]
    pub duration_ms: Option<u64>,
    /// Content rating labels, e.g. `explicit` or `south_korea_19`. The first is
    /// published as the HA `media_content_rating` attribute.
    #[serde(default)]
    pub content_ratings: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct Position {
    #[serde(default, deserialize_with = "de_u64")]
    pub position_ms: u64,
    #[serde(default, deserialize_with = "de_i64")]
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

/// Parameters a `playback_state` advertises for one available action. Only
/// `step_ms` is documented today (for `seek_forward` / `seek_backward`).
#[derive(Debug, Deserialize, Default, Clone, PartialEq, Eq)]
pub(crate) struct ActionParams {
    #[serde(default, deserialize_with = "de_opt_u64")]
    pub step_ms: Option<u64>,
}

/// Player action name -> parameters, as advertised by the current
/// `playback_state`. A map rather than a struct because the API adds action
/// names over time (and the names are player actions, not command names).
pub(crate) type AvailableActions = BTreeMap<String, ActionParams>;

/// Decode `available_actions` tolerantly: unknown action names are kept, and a
/// value that is not an object (null, string, ...) is treated as "no
/// parameters" instead of failing the whole snapshot.
fn de_available_actions<'de, D: Deserializer<'de>>(
    d: D,
) -> Result<Option<AvailableActions>, D::Error> {
    let raw = Option::<BTreeMap<String, serde_json::Value>>::deserialize(d)?;
    Ok(raw.map(|entries| {
        entries
            .into_iter()
            .map(|(name, value)| {
                let params = serde_json::from_value::<ActionParams>(value)
                    .unwrap_or_else(|_| ActionParams::default());
                (name, params)
            })
            .collect()
    }))
}

#[derive(Debug, Deserialize)]
pub(crate) struct QueueEntry {
    /// Stable queue entry identifier when available, otherwise the item URI.
    /// Unused: queue entries are published by label, keyed by item URI.
    #[allow(dead_code)] // part of the wire format; kept for future use
    #[serde(default)]
    pub uid: String,
    /// `context`, `queue` or `autoplay`.
    #[allow(dead_code)] // part of the wire format; kept for future use
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
        /// `None` when the field is missing entirely: a stripped payload must be
        /// ignored, not read as a logout (which clears the published track).
        #[serde(default)]
        logged_in: Option<bool>,
        #[serde(default)]
        is_active: Option<bool>,
        #[serde(default)]
        device_name: String,
    },
    PlaybackState {
        #[serde(default)]
        status: String,
        #[serde(default)]
        item: Option<Entity>,
        #[serde(default)]
        context: Option<Entity>,
        #[serde(default)]
        position: Option<Position>,
        #[serde(default, deserialize_with = "de_opt_u8")]
        volume: Option<u8>,
        #[serde(default)]
        is_active: Option<bool>,
        #[serde(default)]
        options: Option<PlaybackOptions>,
        /// `None` (field absent) keeps the previously advertised actions.
        #[serde(default, deserialize_with = "de_available_actions")]
        available_actions: Option<AvailableActions>,
    },
    TrackChanged {
        #[serde(default)]
        item: Option<Entity>,
    },
    PlaybackChanged {
        #[serde(default)]
        status: Option<String>,
    },
    VolumeChanged {
        #[serde(default, deserialize_with = "de_opt_u8")]
        volume: Option<u8>,
    },
    DeviceChanged {
        #[serde(default)]
        is_active: Option<bool>,
        #[serde(default)]
        device_name: String,
    },
    ContextChanged {
        #[serde(default)]
        context: Option<Entity>,
    },
    OptionsChanged {
        #[serde(default)]
        options: Option<PlaybackOptions>,
    },
    PositionSync {
        #[serde(default)]
        position: Option<Position>,
    },
    QueueChanged {
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

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(json: &str) -> SoloistEvent {
        serde_json::from_str(json).expect("event should parse")
    }

    /// The reference encodes these as plain integers, but a float encoding must
    /// not cost us the whole snapshot: values are rounded instead.
    #[test]
    fn numeric_fields_accept_floats() {
        let ev = parse(
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","decorations":{"playback":{"duration_ms":201173.0}}},"volume":65.0,"position":{"position_ms":45000.0,"timestamp_ms":1747654321000.0,"speed":1}}"#,
        );
        match ev {
            SoloistEvent::PlaybackState {
                item,
                volume,
                position,
                ..
            } => {
                assert_eq!(volume, Some(65));
                let position = position.expect("position");
                assert_eq!(position.position_ms, 45000);
                assert_eq!(position.timestamp_ms, 1747654321000);
                let item = item.expect("item");
                assert_eq!(item.decorations.playback.duration_ms, Some(201173));
            }
            other => panic!("unexpected event: {:?}", other),
        }
    }

    /// Out-of-range and negative numbers saturate rather than failing the parse.
    #[test]
    fn numeric_fields_saturate() {
        let ev = parse(r#"{"type":"volume_changed","volume":-5}"#);
        match ev {
            SoloistEvent::VolumeChanged { volume } => assert_eq!(volume, Some(0)),
            other => panic!("unexpected event: {:?}", other),
        }
        let ev = parse(r#"{"type":"volume_changed","volume":100000}"#);
        match ev {
            SoloistEvent::VolumeChanged { volume } => assert_eq!(volume, Some(255)),
            other => panic!("unexpected event: {:?}", other),
        }
    }

    /// A field that is absent must not abort the parse: the payload is decoded
    /// with defaults and the missing pieces stay `None` (and are skipped by
    /// `apply`, never applied as a zero/disabled value).
    #[test]
    fn missing_fields_do_not_drop_the_event() {
        match parse(r#"{"type":"playback_state","item":{"uri":"spotify:track:abc"}}"#) {
            SoloistEvent::PlaybackState {
                status,
                volume,
                is_active,
                options,
                ..
            } => {
                assert_eq!(status, "");
                assert_eq!(volume, None);
                assert_eq!(is_active, None);
                assert!(options.is_none());
            }
            other => panic!("unexpected event: {:?}", other),
        }
        match parse(r#"{"type":"auth_state"}"#) {
            SoloistEvent::AuthState { logged_in, .. } => assert_eq!(logged_in, None),
            other => panic!("unexpected event: {:?}", other),
        }
        match parse(r#"{"type":"volume_changed"}"#) {
            SoloistEvent::VolumeChanged { volume } => assert_eq!(volume, None),
            other => panic!("unexpected event: {:?}", other),
        }
    }

    /// A cover entry without a `url` must not fail the whole entity.
    #[test]
    fn cover_entry_without_url_is_tolerated() {
        match parse(
            r#"{"type":"track_changed","item":{"uri":"spotify:track:abc","decorations":{"visual_identity":{"cover":[{"size":"large"}]}}}}"#,
        ) {
            SoloistEvent::TrackChanged { item } => {
                let covers = item.unwrap().decorations.visual_identity.cover;
                assert_eq!(covers.len(), 1);
                assert!(covers[0].url.is_empty());
            }
            other => panic!("unexpected event: {:?}", other),
        }
    }

    #[test]
    fn available_actions_are_parsed_tolerantly() {
        let ev = parse(
            r#"{"type":"playback_state","status":"playing","available_actions":{"pause":{},"seek_forward":{"step_ms":15000},"shuffle":null,"future_action":{"foo":1}}}"#,
        );
        match ev {
            SoloistEvent::PlaybackState {
                available_actions, ..
            } => {
                let actions = available_actions.expect("actions");
                assert_eq!(
                    actions.keys().collect::<Vec<_>>(),
                    vec!["future_action", "pause", "seek_forward", "shuffle"]
                );
                assert_eq!(actions["seek_forward"].step_ms, Some(15000));
                assert_eq!(actions["pause"].step_ms, None);
                assert_eq!(actions["shuffle"].step_ms, None);
            }
            other => panic!("unexpected event: {:?}", other),
        }
    }

    #[test]
    fn unknown_event_type_is_ignored() {
        assert!(matches!(
            parse(r#"{"type":"something_new","payload":42}"#),
            SoloistEvent::Unknown
        ));
    }

    #[test]
    fn content_ratings_are_parsed() {
        match parse(
            r#"{"type":"track_changed","item":{"uri":"spotify:track:abc","decorations":{"playback":{"duration_ms":1000,"content_ratings":["explicit"]}}}}"#,
        ) {
            SoloistEvent::TrackChanged { item } => {
                assert_eq!(
                    item.unwrap().decorations.playback.content_ratings,
                    vec!["explicit".to_string()]
                );
            }
            other => panic!("unexpected event: {:?}", other),
        }
    }
}
