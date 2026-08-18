//! Parses soloist WebSocket messages and applies them to
//! [`crate::state::PlaybackState`]: [`handle_text`] dispatches each
//! [`SoloistEvent`] to the `apply_*` helpers in this module, records the
//! last-processed timestamp for the client watchdog, and triggers oEmbed
//! lookups for artist URIs that still lack a name.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::{debug, info, warn};

use crate::soloist::events::{
    CoverImage, Entity, PlaybackOptions, Position, QueueEntry, SoloistEvent,
};
use crate::soloist::oembed::OembedResolver;
use crate::state::{now_unix_ms, CreatorRef, PlaybackState, PositionAnchor, QueueMeta};

/// Epoch-ms of the most recently parsed soloist event (lock-free; updated in
/// `handle_text`). Lets the watchdog tell "bridge is processing events" from
/// "stuck" without touching the state lock.
pub(crate) static LAST_EVENT_MS: AtomicU64 = AtomicU64::new(0);

/// Parse a soloist event and apply it to shared state. Returns
/// `Some(true)` when the event is an auth_state reporting `logged_in=true`
/// (used to trigger the post-login bootstrap in `handle_connection`),
/// `Some(false)` for a logged-out auth_state, and `None` for any other event.
pub(crate) async fn handle_text(
    text: &str,
    state: &Arc<RwLock<PlaybackState>>,
    state_tx: &broadcast::Sender<()>,
    resolver: Option<&OembedResolver>,
) -> Option<bool> {
    let event: SoloistEvent = match serde_json::from_str(text) {
        Ok(e) => e,
        Err(e) => {
            debug!("Unparseable soloist message ({}): {}", e, text);
            return None;
        }
    };
    debug!("<- {}", text);
    LAST_EVENT_MS.store(now_unix_ms() as u64, Ordering::Relaxed);

    // While the power switch is off the bridge stays quiet: events are still
    // processed (state must stay current for the next power-on) but the
    // per-event logs are suppressed so a hidden device doesn't spam the log.
    let powered_on = state.read().await.powered_on;

    // Set by the item/queue arms below: only those events can change which of
    // the current item's artist URIs are resolvable, so only they run the
    // oEmbed dispatch after the match. Position syncs and volume/options/device
    // ticks can't create new unresolved URIs and skip the read-lock + URI
    // collection entirely.
    let mut dispatch_oembed = false;

    match event {
        SoloistEvent::AuthState {
            logged_in,
            is_active,
            device_name,
        } => {
            let mut st = state.write().await;
            st.logged_in = logged_in;
            st.is_active = is_active;
            if !device_name.is_empty() {
                st.device_name = device_name;
            }
            info!(
                "auth_state: logged_in={} is_active={}",
                logged_in, st.is_active
            );
            let _ = state_tx.send(());
            return Some(logged_in);
        }
        SoloistEvent::PlaybackState {
            status,
            item,
            context,
            position,
            volume,
            is_active,
            options,
        } => {
            dispatch_oembed = true;
            let mut st = state.write().await;
            st.set_status(&status);
            apply_entity(&mut st, item.as_ref(), true);
            apply_context(&mut st, context.as_ref());
            if let Some(p) = position {
                apply_position(&mut st, p);
            }
            if let Some(v) = volume {
                st.volume = v.min(100);
            }
            if let Some(a) = is_active {
                st.is_active = a;
            }
            if let Some(o) = options {
                apply_options(&mut st, o);
            }
            if powered_on {
                info!("playback_state: status={} track={:?}", status, st.track);
            }
        }
        SoloistEvent::TrackChanged { item } => {
            dispatch_oembed = true;
            let mut st = state.write().await;
            apply_entity(&mut st, Some(&item), true);
            if powered_on {
                info!(
                    "track_changed: {} - {}",
                    st.track.as_deref().unwrap_or("?"),
                    st.artist.as_deref().unwrap_or("?")
                );
            }
        }
        SoloistEvent::PlaybackChanged { status } => {
            state.write().await.set_status(&status);
            if powered_on {
                debug!("playback_changed: {}", status);
            }
        }
        SoloistEvent::VolumeChanged { volume } => {
            state.write().await.volume = volume.min(100);
            if powered_on {
                debug!("volume_changed: {}", volume);
            }
        }
        SoloistEvent::DeviceChanged {
            is_active,
            device_name,
        } => {
            let mut st = state.write().await;
            st.is_active = is_active;
            if !device_name.is_empty() {
                st.device_name = device_name;
            }
            if powered_on {
                debug!("device_changed: is_active={}", is_active);
            }
        }
        SoloistEvent::ContextChanged { context } => {
            let mut st = state.write().await;
            apply_context(&mut st, Some(&context));
            if powered_on {
                debug!("context_changed: {:?}", st.source);
            }
        }
        SoloistEvent::OptionsChanged { options } => {
            let mut st = state.write().await;
            apply_options(&mut st, options);
            if powered_on {
                debug!(
                    "options_changed: shuffle={} repeat={}",
                    st.shuffle, st.repeat
                );
            }
        }
        SoloistEvent::PositionSync { position } => {
            let mut st = state.write().await;
            apply_position(&mut st, position);
            if powered_on {
                debug!(
                    "position_sync: {}ms speed={}",
                    st.position_anchor.position_ms, st.position_anchor.speed
                );
            }
        }
        SoloistEvent::QueueChanged { previous, upcoming } => {
            dispatch_oembed = true;
            let entries = upcoming
                .iter()
                .filter_map(|e| e.item.as_ref())
                .map(short_label)
                .collect::<Vec<_>>();
            let meta = queue_meta(&upcoming);
            // Artist URIs in this snapshot that shipped without a name: resolve
            // them via oEmbed now so the name is cached before the track plays.
            let unresolved: Vec<String> = upcoming
                .iter()
                .filter_map(|e| e.item.as_ref())
                .flat_map(|item| item.decorations.creators.iter())
                .filter_map(|c| c.entity.as_ref())
                .filter(|ce| !ce.uri.is_empty() && ce.decorations.identity.name.is_empty())
                .map(|ce| ce.uri.clone())
                .collect();
            {
                let mut st = state.write().await;
                st.upcoming = entries;
                // Replace, don't accumulate: only the most recent queue snapshot is
                // kept, otherwise queue_meta would grow without bound over a long
                // session. The now-playing track re-seeds its entry, though: it
                // just left `upcoming` (it is playing now) and its first playback
                // snapshot often ships without creator decorations.
                let current_uri = st.media_content_id.clone();
                let current_artists = current_uri
                    .as_deref()
                    .and_then(|uri| st.queue_meta.track_artists.get(uri).cloned());
                st.queue_meta = meta;
                if let (Some(uri), Some(names)) = (current_uri, current_artists) {
                    st.queue_meta.track_artists.entry(uri).or_insert(names);
                }
                // Track -> artist URI learning is persistent (bounded): record the
                // creator URIs of both the upcoming and just-played entries, so a
                // track's artist URIs survive queue rotation even when the
                // rotation races the playback snapshot of the newly started track.
                for entry in previous.iter().chain(upcoming.iter()) {
                    let item_uri = entry
                        .item
                        .as_ref()
                        .map(|i| i.uri.clone())
                        .unwrap_or_default();
                    if item_uri.is_empty() {
                        continue;
                    }
                    let uris = entry_artist_uris(entry);
                    if !uris.is_empty() {
                        st.track_artist_uris.insert(item_uri, uris);
                    }
                }
                if powered_on {
                    debug!("queue_changed: {} upcoming", upcoming.len());
                }
            }
            // The write guard above is scoped so it is released BEFORE the
            // oEmbed dispatch: maybe_lookup takes the state read lock itself,
            // and holding the write lock across that await self-deadlocks
            // (Rust drops let-bound guards at end of scope, not last use).
            if let Some(r) = resolver {
                r.maybe_lookup(unresolved).await;
            }
        }
        SoloistEvent::CommandResult { command } => {
            info!("soloist accepted command: {}", command);
            return None;
        }
        SoloistEvent::Error { message } => {
            warn!("soloist error: {}", message);
            return None;
        }
        SoloistEvent::Unknown => {
            debug!("ignoring unknown soloist event");
            return None;
        }
    }
    // oEmbed fallback: the current item's unresolved artist URIs are looked up
    // via Spotify's public oEmbed API — but only on events that can change the
    // resolution set (a new item snapshot, or a queue rotation that recorded
    // the current track's creators). Deduped against the caches and in-flight
    // requests inside maybe_lookup, so re-dispatches are no-ops once a name is
    // known or a lookup is already running. The read guard is scoped so it is
    // released before maybe_lookup (which takes the read lock itself).
    if dispatch_oembed {
        if let Some(resolver) = resolver {
            let mut uris: Vec<String> = Vec::new();
            {
                let st = state.read().await;
                // Creators of the current item whose name is still unknown.
                for c in st.item_creators.iter().filter(|c| c.name.is_none()) {
                    uris.push(c.uri.clone());
                }
                // Creator URIs recorded from queue snapshots for the current
                // track (the playback snapshot may have shipped with empty
                // creators).
                if let Some(track) = st.media_content_id.as_deref() {
                    if let Some(quris) = st.track_artist_uris.get(track) {
                        for u in quris {
                            if !uris.contains(u) {
                                uris.push(u.clone());
                            }
                        }
                    }
                }
            }
            resolver.maybe_lookup(uris).await;
        }
    }
    let _ = state_tx.send(());
    None
}

fn apply_entity(st: &mut PlaybackState, entity: Option<&Entity>, is_item: bool) {
    let Some(e) = entity else {
        if is_item {
            st.track = None;
            st.artist = None;
            st.album = None;
            st.artwork_url = None;
            st.media_content_id = None;
            st.media_duration_ms = None;
            st.item_creators.clear();
        }
        return;
    };

    if is_item {
        st.media_content_id = if e.uri.is_empty() {
            None
        } else {
            Some(e.uri.clone())
        };
        if let Some(d) = e.decorations.playback.duration_ms {
            st.media_duration_ms = Some(d);
        }
        if !e.decorations.identity.name.is_empty() {
            st.track = Some(e.decorations.identity.name.clone());
        }
        st.artwork_url = best_cover(&e.decorations.visual_identity.cover);
        st.album = e
            .decorations
            .parent
            .as_ref()
            .and_then(|p| p.entity.as_ref())
            .and_then(|pe| {
                if !pe.decorations.identity.name.is_empty() {
                    Some(pe.decorations.identity.name.clone())
                } else {
                    None
                }
            });
        st.item_creators = collect_creators(e, st);
        st.artist = st.artist_label();
        if st.artist.is_none() {
            debug!(
                "artist unresolved for {:?}: snapshot_creators={:?} known_queue_uris={:?}",
                st.media_content_id,
                st.item_creators
                    .iter()
                    .map(|c| c.uri.as_str())
                    .collect::<Vec<_>>(),
                st.media_content_id
                    .as_deref()
                    .and_then(|t| st.track_artist_uris.get(t))
                    .map(|u| u.to_vec())
            );
        }
    }
}

/// Snapshot the current item's creator refs: artist URI plus the best name
/// known at snapshot time (inline decoration, else queue metadata, else the
/// oEmbed cache). Creators with no URI are skipped — they cannot be resolved
/// by a later lookup. The refs let a later oEmbed result refresh the artist
/// label without re-parsing an event.
fn collect_creators(e: &Entity, st: &PlaybackState) -> Vec<CreatorRef> {
    e.decorations
        .creators
        .iter()
        .filter_map(|c| c.entity.as_ref())
        .filter(|ce| !ce.uri.is_empty())
        .map(|ce| CreatorRef {
            uri: ce.uri.clone(),
            name: if !ce.decorations.identity.name.is_empty() {
                Some(ce.decorations.identity.name.clone())
            } else {
                st.queue_meta
                    .artist_names
                    .get(&ce.uri)
                    .cloned()
                    .or_else(|| st.oembed.get(&ce.uri).map(str::to_string))
            },
        })
        .collect()
}

/// Extract artist identity from a queue_changed `upcoming` payload: per-track
/// creator names keyed by track URI, plus an artist URI -> name index that
/// also resolves creator entities shipping with a URI but no display name.
fn queue_meta(upcoming: &[QueueEntry]) -> QueueMeta {
    let mut meta = QueueMeta::default();
    for entry in upcoming {
        let Some(item) = &entry.item else { continue };
        for creator in &item.decorations.creators {
            if let Some(ce) = &creator.entity {
                if !ce.uri.is_empty() && !ce.decorations.identity.name.is_empty() {
                    meta.artist_names
                        .entry(ce.uri.clone())
                        .or_insert_with(|| ce.decorations.identity.name.clone());
                }
            }
        }
    }
    for entry in upcoming {
        let Some(item) = &entry.item else { continue };
        if item.uri.is_empty() {
            continue;
        }
        let mut names = Vec::new();
        for creator in &item.decorations.creators {
            let Some(ce) = &creator.entity else { continue };
            let name = if !ce.decorations.identity.name.is_empty() {
                ce.decorations.identity.name.clone()
            } else if !ce.uri.is_empty() {
                meta.artist_names.get(&ce.uri).cloned().unwrap_or_default()
            } else {
                String::new()
            };
            if !name.is_empty() {
                names.push(name);
            }
        }
        if !names.is_empty() {
            meta.track_artists.entry(item.uri.clone()).or_insert(names);
        }
    }
    meta
}

fn apply_context(st: &mut PlaybackState, context: Option<&Entity>) {
    st.source = context
        .filter(|c| !c.decorations.identity.name.is_empty())
        .map(|c| c.decorations.identity.name.clone());
}

fn apply_options(st: &mut PlaybackState, options: PlaybackOptions) {
    st.shuffle = options.shuffle;
    st.repeat = match options.repeat.as_str() {
        "context" => "all".to_string(),
        "track" => "one".to_string(),
        _ => "off".to_string(),
    };
}

fn apply_position(st: &mut PlaybackState, p: Position) {
    st.position_anchor = PositionAnchor {
        position_ms: p.position_ms,
        timestamp_ms: if p.timestamp_ms > 0 {
            p.timestamp_ms
        } else {
            now_unix_ms()
        },
        speed: p.speed,
    };
}

/// Name of the first creator (e.g. "Artist A"), skipping entities without a
/// display name. None when there are no named creators.
fn creators_label(e: &Entity) -> Option<String> {
    e.decorations
        .creators
        .iter()
        .filter_map(|c| c.entity.as_ref())
        .map(|ce| ce.decorations.identity.name.as_str())
        .find(|n| !n.is_empty())
        .map(str::to_string)
}

/// Collect the creator artist URIs of a queue entry (skipping entries and
/// creators without a URI).
fn entry_artist_uris(entry: &QueueEntry) -> Vec<String> {
    let Some(item) = &entry.item else {
        return Vec::new();
    };
    item.decorations
        .creators
        .iter()
        .filter_map(|c| c.entity.as_ref())
        .filter(|ce| !ce.uri.is_empty())
        .map(|ce| ce.uri.clone())
        .collect()
}

/// Compact "Title - Artist" label for queue entries.
fn short_label(e: &Entity) -> String {
    let name = if e.decorations.identity.name.is_empty() {
        e.uri.as_str()
    } else {
        e.decorations.identity.name.as_str()
    };
    match creators_label(e) {
        Some(artist) => format!("{} - {}", name, artist),
        None => name.to_string(),
    }
}

/// Pick the largest available cover image (xlarge > large > default > small).
fn best_cover(covers: &[CoverImage]) -> Option<String> {
    let priority = |size: &str| match size {
        "xlarge" => 3,
        "large" => 2,
        "default" => 1,
        _ => 0,
    };
    covers
        .iter()
        .max_by_key(|c| priority(&c.size))
        .map(|c| c.url.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::soloist::oembed::apply_oembed_result;
    use std::time::Duration;

    async fn feed(state: &Arc<RwLock<PlaybackState>>, tx: &broadcast::Sender<()>, json: &str) {
        // No oEmbed resolver in tests: lookups must not fire network requests.
        handle_text(json, state, tx, None).await;
    }

    #[tokio::test]
    async fn parses_auth_state() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"auth_state","logged_in":true,"is_active":false,"device_name":"Soloist"}"#,
        )
        .await;
        let st = state.read().await;
        assert!(st.logged_in);
        assert!(!st.is_active);
        assert_eq!(st.device_name, "Soloist");
    }

    #[tokio::test]
    async fn parses_playback_state_with_full_entity() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/small","size":"small"},{"url":"https://i.scdn.co/large","size":"large"}]},"parent":{"entity":{"uri":"spotify:album:def","entity_type":"album","decorations":{"identity":{"name":"Album Name"}}}},"creators":[{"entity":{"uri":"spotify:artist:ghi","entity_type":"artist","decorations":{"identity":{"name":"Artist Name"}}}}],"playback":{"duration_ms":210000,"content_ratings":[]}}},"context":{"uri":"spotify:playlist:jkl","entity_type":"playlist","decorations":{"identity":{"name":"Today's Top Hits"}}},"position":{"position_ms":45000,"timestamp_ms":1747654321000,"speed":1.0},"volume":65,"is_active":true,"options":{"shuffle":false,"repeat":"context"},"available_actions":{"pause":{}}}"##,
        )
        .await;
        // The power switch defaults to off; this test exercises playback-state
        // parsing, so report normally.
        state.write().await.set_power(true);
        let st = state.read().await;
        assert_eq!(st.ha_state(), "playing");
        assert_eq!(st.track.as_deref(), Some("My Song"));
        assert_eq!(st.artist.as_deref(), Some("Artist Name"));
        assert_eq!(st.album.as_deref(), Some("Album Name"));
        assert_eq!(st.artwork_url.as_deref(), Some("https://i.scdn.co/large"));
        assert_eq!(st.media_content_id.as_deref(), Some("spotify:track:abc"));
        assert_eq!(st.media_duration_ms, Some(210000));
        assert_eq!(st.source.as_deref(), Some("Today's Top Hits"));
        assert_eq!(st.volume, 65);
        assert!(st.is_active);
        assert!(!st.shuffle);
        assert_eq!(st.repeat, "all");
        assert_eq!(st.position_anchor.position_ms, 45000);
    }

    #[tokio::test]
    async fn shows_only_first_creator() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            "{\"type\":\"playback_state\",\"status\":\"playing\",\"item\":{\"uri\":\"spotify:track:abc\",\"entity_type\":\"track\",\"decorations\":{\"identity\":{\"name\":\"My Song\"},\"creators\":[{\"entity\":{\"uri\":\"spotify:artist:1\",\"decorations\":{\"identity\":{\"name\":\"Artist One\"}}}},{\"entity\":{\"uri\":\"spotify:artist:2\",\"decorations\":{\"identity\":{\"name\":\"Artist Two\"}}}}]}}}",
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Artist One"));
    }

    #[tokio::test]
    async fn maps_options_repeat_to_ha_values() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"options_changed","options":{"shuffle":true,"repeat":"track"}}"#,
        )
        .await;
        let st = state.read().await;
        assert!(st.shuffle);
        assert_eq!(st.repeat, "one");
    }

    #[tokio::test]
    async fn parses_position_sync() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"position_sync","position":{"position_ms":45000,"timestamp_ms":1747654321000,"speed":0.0}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.position_anchor.position_ms, 45000);
        assert_eq!(st.position_anchor.speed, 0.0);
    }

    #[tokio::test]
    async fn parses_queue_changed() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"spotify:track:1","source":"context","item":{"uri":"spotify:track:1","entity_type":"track","decorations":{"identity":{"name":"Next Song"},"creators":[{"entity":{"decorations":{"identity":{"name":"Next Artist"}}}}]}}},{"uid":"spotify:track:2","source":"queue","item":{"uri":"spotify:track:2","entity_type":"track","decorations":{"identity":{"name":"Song Two"}}}}]}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.upcoming.len(), 2);
        assert_eq!(st.upcoming[0], "Next Song - Next Artist");
        assert_eq!(st.upcoming[1], "Song Two");
    }

    #[tokio::test]
    async fn queue_changed_captures_artist_metadata() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:1v4WeozqQXPBnxiA87C3vP","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:2xx0ChFyXa0a4S48GAXFUz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}]}}}]}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(
            st.queue_meta
                .track_artists
                .get("spotify:track:1v4WeozqQXPBnxiA87C3vP")
                .map(Vec::as_slice),
            Some(&["Manic Focus".to_string()][..])
        );
        assert_eq!(
            st.queue_meta
                .artist_names
                .get("spotify:artist:2xx0ChFyXa0a4S48GAXFUz")
                .map(String::as_str),
            Some("Manic Focus")
        );
    }

    #[tokio::test]
    async fn playback_state_resolves_artist_from_queue_metadata() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // The track was seen in a queue_changed while still upcoming, with full
        // artist info...
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}]}}}]}"#,
        )
        .await;
        // ...and its playback snapshot ships with empty creator decorations:
        // the artist is resolved immediately from the queue metadata.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[]}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
        assert_eq!(st.media_content_id.as_deref(), Some("spotify:track:abc"));
    }

    #[tokio::test]
    async fn queue_rotation_replaces_metadata_but_keeps_playing_track() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // Snapshot 1: abc and a stale track are upcoming, abc with full artist
        // info.
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}]}}},{"uid":"u2","source":"context","item":{"uri":"spotify:track:old","entity_type":"track","decorations":{"identity":{"name":"Old Track"},"creators":[{"entity":{"decorations":{"identity":{"name":"Old Artist"}}}}]}}}]}"#,
        )
        .await;
        // abc starts playing (media_content_id is now set)...
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[]}}}"#,
        )
        .await;
        // ...then the queue rotates and abc drops out of upcoming.
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u3","source":"context","item":{"uri":"spotify:track:def","entity_type":"track","decorations":{"identity":{"name":"Next Up"},"creators":[]}}}]}"#,
        )
        .await;
        let st = state.read().await;
        // Storage stays bounded: the stale entry from snapshot 1 is gone, and
        // only the playing track's re-seeded entry remains (`def` ships no
        // creators in this snapshot, so it carries no artist entry).
        assert!(!st
            .queue_meta
            .track_artists
            .contains_key("spotify:track:old"));
        assert_eq!(st.queue_meta.track_artists.len(), 1);
        assert_eq!(
            st.queue_meta
                .track_artists
                .get("spotify:track:abc")
                .map(Vec::as_slice),
            Some(&["Manic Focus".to_string()][..])
        );
        // The display label for the fresh snapshot is still there.
        assert_eq!(st.upcoming, vec!["Next Up"]);
    }

    #[tokio::test]
    async fn resolves_creator_name_via_artist_uri() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // Queue metadata teaches us the name for this artist URI.
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}]}}}]}"#,
        )
        .await;
        // Later snapshot: the creator entity has a URI but no display name.
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
    }

    #[tokio::test]
    async fn unknown_track_without_queue_metadata_stays_unnamed() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:unknown","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[]}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist, None);
        assert_eq!(
            st.media_content_id.as_deref(),
            Some("spotify:track:unknown")
        );
    }

    #[tokio::test]
    async fn playback_state_without_item_is_idle() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"idle","volume":100}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.ha_state(), "idle");
        assert_eq!(st.volume, 100);
    }

    #[tokio::test]
    async fn oembed_result_fills_missing_artist() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // Creator ships with a URI but no display name and no queue metadata:
        // the artist stays unknown until the oEmbed result lands.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.artist, None);
            assert_eq!(
                st.item_creators,
                vec![CreatorRef {
                    uri: "spotify:artist:xyz".to_string(),
                    name: None,
                }]
            );
        }
        // A completed oEmbed lookup (as applied by the resolver task) fills
        // the artist and caches the name for the rest of the session.
        {
            let mut st = state.write().await;
            assert!(apply_oembed_result(
                &mut st,
                "spotify:artist:xyz",
                "Manic Focus"
            ));
        }
        {
            let st = state.read().await;
            assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
            assert_eq!(st.oembed.get("spotify:artist:xyz"), Some("Manic Focus"));
        }
        // A later snapshot resolves the creator instantly from the cache.
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
    }

    #[tokio::test]
    async fn oembed_result_ignored_after_track_change() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}"#,
        )
        .await;
        // Track changed before the lookup completed.
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:def","entity_type":"track","decorations":{"identity":{"name":"Other"},"creators":[{"entity":{"uri":"spotify:artist:other","entity_type":"artist","decorations":{"identity":{"name":"Other Artist"}}}}]}}}"#,
        )
        .await;
        let mut st = state.write().await;
        // Resolves and caches the old artist, but the current item's label is
        // left alone (the URI no longer belongs to the playing item).
        assert!(!apply_oembed_result(
            &mut st,
            "spotify:artist:xyz",
            "Manic Focus"
        ));
        assert_eq!(st.artist.as_deref(), Some("Other Artist"));
        assert_eq!(st.oembed.get("spotify:artist:xyz"), Some("Manic Focus"));
    }

    #[tokio::test]
    async fn empty_creators_snapshot_resolves_via_queue_uris_and_oembed() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // The track is upcoming with an artist URI but no name...
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}]}"#,
        )
        .await;
        // ...then starts playing with empty creator decorations (soloist's
        // common first-snapshot shape): the queue-recorded URI must let the
        // oEmbed-cached name resolve anyway.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[]}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.artist, None);
            assert_eq!(
                st.track_artist_uris.get("spotify:track:abc"),
                Some(&["spotify:artist:xyz".to_string()][..])
            );
        }
        // A completed oEmbed lookup (as applied by the resolver task) fills
        // the artist even though the snapshot shipped no creator URIs.
        {
            let mut st = state.write().await;
            assert!(apply_oembed_result(
                &mut st,
                "spotify:artist:xyz",
                "Manic Focus"
            ));
        }
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
    }

    #[tokio::test]
    async fn queue_rotation_race_keeps_artist_uris_for_new_track() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // Snapshot 1: the Tinlicker track is upcoming with an artist URI but
        // no name.
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:T","entity_type":"track","decorations":{"identity":{"name":"Tinlicker Track"},"creators":[{"entity":{"uri":"spotify:artist:5Em","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}},{"uid":"u2","source":"context","item":{"uri":"spotify:track:old","entity_type":"track","decorations":{"identity":{"name":"Old"}}}}]}"#,
        )
        .await;
        // Rotation arrives BEFORE the new track's playback_state: the current
        // track is still the old one, so the queue snapshot wholesale replace
        // would have dropped T's entry without the persistent cache.
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[{"uid":"p1","source":"context","item":{"uri":"spotify:track:old","entity_type":"track","decorations":{"identity":{"name":"Old"}}}}],"upcoming":[{"uid":"u3","source":"context","item":{"uri":"spotify:track:next","entity_type":"track","decorations":{"identity":{"name":"Next"}}}}]}"#,
        )
        .await;
        // T starts playing with empty creator decorations.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:T","entity_type":"track","decorations":{"identity":{"name":"Tinlicker Track"},"creators":[]}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            // The URI mapping survived the rotation despite the late snapshot.
            assert_eq!(
                st.track_artist_uris.get("spotify:track:T"),
                Some(&["spotify:artist:5Em".to_string()][..])
            );
            assert_eq!(st.artist, None);
        }
        // A completed oEmbed lookup fills the artist (this is the case that
        // previously stayed "Unknown" and never published).
        {
            let mut st = state.write().await;
            assert!(apply_oembed_result(
                &mut st,
                "spotify:artist:5Em",
                "Tinlicker"
            ));
        }
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Tinlicker"));
    }

    #[tokio::test]
    async fn queue_changed_with_unresolved_artist_does_not_deadlock() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = OembedResolver::new(state.clone(), tx.clone());
        // Cache the URI so the prefetch dispatch stays a no-op: the point of
        // this test is the write-guard scope, not a network lookup (oEmbed
        // lookups must not fire requests in tests).
        state
            .write()
            .await
            .oembed
            .insert("spotify:artist:73A".to_string(), "BICEP".to_string());
        // Queue snapshot with a creator that ships a URI but no name -> the
        // QueueChanged arm calls maybe_lookup with non-empty `unresolved`.
        let json = r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"BICEP Track"},"creators":[{"entity":{"uri":"spotify:artist:73A","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}]}"#;
        let result = tokio::time::timeout(
            Duration::from_secs(3),
            handle_text(json, &state, &tx, Some(&resolver)),
        )
        .await;
        assert!(
            result.is_ok(),
            "handle_text deadlocked: QueueChanged write guard held across maybe_lookup's read()"
        );
    }
}
