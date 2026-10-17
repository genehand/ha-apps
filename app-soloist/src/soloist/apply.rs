//! Parses soloist WebSocket messages and applies them to
//! [`crate::state::PlaybackState`]: [`handle_text`] dispatches each
//! [`SoloistEvent`] to the `apply_*` helpers in this module, records the
//! last-processed timestamp for the client watchdog, and triggers oEmbed
//! lookups for artist URIs that still lack a name.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::{debug, info, warn};

use crate::soloist::embed::EmbedResolver;
use crate::soloist::events::{
    CoverImage, Entity, PlaybackOptions, Position, QueueEntry, SoloistEvent,
};
use crate::soloist::oembed::OembedResolver;
use crate::state::{
    now_unix_ms, ContentKind, CreatorRef, PlaybackState, PositionAnchor, QueueMeta, TrackMeta,
};

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
    embed: Option<&EmbedResolver>,
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
            let Some(logged_in) = logged_in else {
                // A stripped auth_state must not be read as a logout: that would
                // clear the published track.
                debug!("auth_state without logged_in; ignoring");
                return None;
            };
            let mut st = state.write().await;
            if !logged_in {
                // Documented on logout and on session replacement. A logged-out
                // device cannot be playing, so stop publishing the previous
                // session's track instead of leaving it on the sensor forever.
                st.clear_session();
            }
            st.logged_in = logged_in;
            if let Some(active) = is_active {
                st.is_active = active;
            }
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
            available_actions,
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
            // An absent `available_actions` keeps the previous set rather than
            // clearing it: only a snapshot that really says "nothing" clears.
            if let Some(actions) = available_actions {
                st.available_actions = actions;
            }
            if powered_on {
                info!("playback_state: status={} track={:?}", status, st.track);
            }
        }
        SoloistEvent::TrackChanged { item } => {
            let Some(item) = item else {
                debug!("track_changed without item; ignoring");
                return None;
            };
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
            let Some(status) = status else {
                debug!("playback_changed without status; ignoring");
                return None;
            };
            state.write().await.set_status(&status);
            if powered_on {
                debug!("playback_changed: {}", status);
            }
        }
        SoloistEvent::VolumeChanged { volume } => {
            let Some(volume) = volume else {
                debug!("volume_changed without volume; ignoring");
                return None;
            };
            state.write().await.volume = volume.min(100);
            if powered_on {
                debug!("volume_changed: {}", volume);
            }
        }
        SoloistEvent::DeviceChanged {
            is_active,
            device_name,
        } => {
            let Some(is_active) = is_active else {
                debug!("device_changed without is_active; ignoring");
                return None;
            };
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
            let Some(context) = context else {
                debug!("context_changed without context; ignoring");
                return None;
            };
            let mut st = state.write().await;
            apply_context(&mut st, Some(&context));
            if powered_on {
                debug!("context_changed: {:?}", st.source);
            }
        }
        SoloistEvent::OptionsChanged { options } => {
            let Some(options) = options else {
                debug!("options_changed without options; ignoring");
                return None;
            };
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
            let Some(position) = position else {
                debug!("position_sync without position; ignoring");
                return None;
            };
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
                .filter_map(short_label)
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
        let mut uris: Vec<String> = Vec::new();
        // Track URI whose identity soloist left unresolved (missing title or
        // artist): the only case where the embed-page fallback can help.
        let mut unidentified_track: Option<String> = None;
        {
            let st = state.read().await;
            // Creators of the current item whose name is still unknown. Only
            // URI-bearing ones are resolvable: a name-only creator has nothing
            // to look up (and is already usable as a label).
            for c in st
                .item_creators
                .iter()
                .filter(|c| c.name.is_none())
                .filter_map(|c| c.uri.clone())
            {
                uris.push(c);
            }
            // Creator URIs recorded from queue snapshots for the current
            // track, used only when the playback snapshot shipped *no*
            // creators at all (the documented reason for this fallback).
            // When the snapshot did ship them, its own creator list is the
            // complete resolution set — consulting the queue record as well
            // would re-request artist URIs whose names the snapshot already
            // spelled out, for no gain.
            if st.item_creators.is_empty() {
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
            // A bare snapshot ships no creators at all, so there is no artist
            // URI for the oEmbed pass above to look up; the embedded track page
            // is then the only source of the title and artist.
            if st.track.is_none() || st.artist.is_none() {
                unidentified_track = st.media_content_id.clone();
            }
        }
        if let Some(resolver) = resolver {
            resolver.maybe_lookup(uris).await;
        }
        if let Some(embed) = embed {
            embed.maybe_fetch(unidentified_track.as_deref()).await;
        }
    }
    let _ = state_tx.send(());
    None
}

fn apply_entity(st: &mut PlaybackState, entity: Option<&Entity>, is_item: bool) {
    if !is_item {
        return;
    }
    let Some(e) = entity else {
        // Nothing is playing: drop the item identity (context, queue and
        // advertised actions are owned by their own events).
        st.clear_item();
        return;
    };

    // An `ad` item is not a track: keep the published identity *and* the track
    // URI (which the sticky rules key on) and only flag it, so an ad break
    // cannot rename the now-playing card to "Advertisement".
    if e.entity_type == "ad" {
        st.is_ad = true;
        return;
    }
    st.is_ad = false;

    let uri = if e.uri.is_empty() {
        None
    } else {
        Some(e.uri.clone())
    };
    // Only inherit the previous labels while the same track keeps playing: a
    // new URI must not display the old track's metadata.
    let same_track = uri.is_some() && uri == st.media_content_id;
    // Metadata resolved from the embed page for this URI in an earlier round
    // (see `soloist::embed`), used for whatever this snapshot omits.
    let TrackMeta {
        track: cached_track,
        album: cached_album,
        artwork: cached_artwork,
        duration_ms: cached_duration,
        ..
    } = uri
        .as_deref()
        .and_then(|u| st.track_meta.get(u))
        .cloned()
        .unwrap_or_default();
    let prev_track = same_track.then(|| st.track.clone()).flatten();
    let prev_artist = same_track.then(|| st.artist.clone()).flatten();
    let prev_album = same_track.then(|| st.album.clone()).flatten();
    let prev_artwork = same_track.then(|| st.artwork_url.clone()).flatten();
    let prev_duration = same_track.then_some(st.media_duration_ms).flatten();
    let prev_series = same_track.then(|| st.media_series_title.clone()).flatten();
    let prev_rating = same_track.then(|| st.content_rating.clone()).flatten();
    let snapshot_track =
        (!e.decorations.identity.name.is_empty()).then(|| e.decorations.identity.name.clone());
    // The parent of a music track is its album; the parent of a podcast episode
    // is its show, which is not an album and must not be published as one.
    let parent_name = e
        .decorations
        .parent
        .as_ref()
        .and_then(|p| p.entity.as_ref())
        .map(|pe| pe.decorations.identity.name.clone())
        .filter(|n| !n.is_empty());
    let is_episode = e.entity_type == "episode";
    let snapshot_album = if is_episode {
        None
    } else {
        parent_name.clone()
    };

    // Per field: what soloist shipped > what the embed fallback resolved >
    // what was already published for this same track.
    st.media_content_id = uri;
    st.track = snapshot_track.or(cached_track).or(prev_track);
    st.album = snapshot_album
        .or(if is_episode { None } else { cached_album })
        .or(prev_album);
    st.content_kind = if is_episode {
        ContentKind::Podcast
    } else {
        ContentKind::Music
    };
    st.media_series_title = if is_episode {
        parent_name.or(prev_series)
    } else {
        None
    };
    st.content_rating = e
        .decorations
        .playback
        .content_ratings
        .first()
        .cloned()
        .or(prev_rating);
    st.artwork_url = best_cover(&e.decorations.visual_identity.cover)
        .or(cached_artwork)
        .or(prev_artwork);
    st.media_duration_ms = e
        .decorations
        .playback
        .duration_ms
        .or(cached_duration)
        .or(prev_duration);
    st.item_creators = collect_creators(e, st);
    // `artist_label` only ever sees this snapshot's creators, so a repeated
    // bare snapshot of the same track would otherwise recompute its way back
    // to None and flash "Unknown" mid-track.
    st.artist = st.artist_label().or(prev_artist);
    if st.artist.is_none() {
        debug!(
            "artist unresolved for {:?}: snapshot_creators={:?} known_queue_uris={:?}",
            st.media_content_id,
            st.item_creators
                .iter()
                .map(|c| c.uri.as_deref().unwrap_or("<no uri>"))
                .collect::<Vec<_>>(),
            st.media_content_id
                .as_deref()
                .and_then(|t| st.track_artist_uris.get(t))
                .map(|u| u.to_vec())
        );
    }
}

/// Snapshot the current item's creator refs: artist URI (when soloist supplied
/// one) plus the best name known at snapshot time (inline decoration, else
/// queue metadata, else the oEmbed cache). Creators with *neither* a URI nor a
/// name are skipped — there is nothing to show and nothing to look up — but a
/// name-only creator is kept: nested entities may omit `uri`, and the name is
/// still displayable. The refs let a later oEmbed result refresh the artist
/// label without re-parsing an event.
fn collect_creators(e: &Entity, st: &PlaybackState) -> Vec<CreatorRef> {
    e.decorations
        .creators
        .iter()
        .filter_map(|c| c.entity.as_ref())
        .filter(|ce| !ce.uri.is_empty() || !ce.decorations.identity.name.is_empty())
        .map(|ce| CreatorRef {
            uri: (!ce.uri.is_empty()).then(|| ce.uri.clone()),
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

/// Apply the playback context. The context name always feeds the HA "source"
/// attribute. When the context *is* an album, its identity name and cover are
/// also the fallback for the item's album name and artwork: soloist's first
/// snapshot for a new track often ships no `parent` album and no cover, even
/// though the album it is playing from is right there in the context. Used
/// only as a fallback — the item's own parent/cover always win (see
/// [`apply_entity`]), and only while an item is actually identified: with no
/// current item there is no album to fill, and borrowing the context's would
/// publish an album next to an "Unknown" title.
fn apply_context(st: &mut PlaybackState, context: Option<&Entity>) {
    st.source = context
        .filter(|c| !c.decorations.identity.name.is_empty())
        .map(|c| c.decorations.identity.name.clone());
    let Some(c) = context else {
        return;
    };
    if st.media_content_id.is_none() {
        return;
    }
    if !c.uri.starts_with("spotify:album:") {
        return;
    }
    if st.album.is_none() && !c.decorations.identity.name.is_empty() {
        st.album = Some(c.decorations.identity.name.clone());
    }
    if st.artwork_url.is_none() {
        st.artwork_url = best_cover(&c.decorations.visual_identity.cover);
    }
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

/// Compact "Title - Artist" label for a queue entry. `None` when the entry's
/// decorations carry no name: the API allows queue entries with empty
/// decorations, and publishing a bare `spotify:track:...` URI as "next up" is
/// worse than publishing nothing.
fn short_label(e: &Entity) -> Option<String> {
    if e.decorations.identity.name.is_empty() {
        return None;
    }
    let name = e.decorations.identity.name.as_str();
    Some(match creators_label(e) {
        Some(artist) => format!("{} - {}", name, artist),
        None => name.to_string(),
    })
}

/// Pick the largest available cover image (xlarge > large > default > small).
/// Entries without a URL are skipped, and a size name this bridge does not know
/// yet ranks with `default` (above `small`, below `large`).
fn best_cover(covers: &[CoverImage]) -> Option<String> {
    let priority = |size: &str| match size {
        "xlarge" => 3,
        "large" => 2,
        "small" => 0,
        _ => 1,
    };
    covers
        .iter()
        .filter(|c| !c.url.is_empty())
        .max_by_key(|c| priority(&c.size))
        .map(|c| c.url.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::soloist::oembed::apply_oembed_result;
    use std::time::Duration;

    async fn feed(state: &Arc<RwLock<PlaybackState>>, tx: &broadcast::Sender<()>, json: &str) {
        // Hand-written test JSON: make sure it really is a well-formed known
        // event, otherwise a typo would silently apply nothing and the test
        // would assert against an empty state.
        let parsed: crate::soloist::events::SoloistEvent =
            serde_json::from_str(json).expect("test event parses");
        assert!(
            !matches!(parsed, crate::soloist::events::SoloistEvent::Unknown),
            "test uses an event type the bridge does not know"
        );
        // No oEmbed/embed resolvers in tests: network lookups must not fire.
        handle_text(json, state, tx, None, None).await;
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
    async fn album_context_fills_a_decoration_less_item() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // The exact field-log shape: the item carries nothing but its URI and
        // duration, while the context names the album and ships its cover.
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:0u5aO1GYsIhAWCPuXdwnak","entity_type":"track","decorations":{"playback":{"duration_ms":201173,"content_ratings":[]}}},"context":{"uri":"spotify:album:0bQglEvsHphrS19FGODEGo","entity_type":"album","decorations":{"identity":{"name":"Siamese Dream (Deluxe Edition)"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/image/ab67616d00001e025274788f34fc7656d2856dfd","size":"default"}]}}},"position":{"position_ms":3004,"timestamp_ms":1792192428899,"speed":1},"volume":100,"is_active":false}"##,
        )
        .await;
        let st = state.read().await;
        // Nothing soloist-side names the track or its artist; only the album
        // context can fill anything, and it must.
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.album.as_deref(), Some("Siamese Dream (Deluxe Edition)"));
        assert_eq!(
            st.artwork_url.as_deref(),
            Some("https://i.scdn.co/image/ab67616d00001e025274788f34fc7656d2856dfd")
        );
        assert_eq!(st.source.as_deref(), Some("Siamese Dream (Deluxe Edition)"));
    }

    /// A snapshot with an album context but no item must not publish the
    /// context's album name/cover next to an "Unknown" title: the album fallback
    /// belongs to the item.
    #[tokio::test]
    async fn album_context_is_not_borrowed_without_an_item() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"idle","item":{"uri":"","entity_type":"unknown","decorations":{}},"context":{"uri":"spotify:album:ctx","entity_type":"album","decorations":{"identity":{"name":"Context Album"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/ctx","size":"default"}]}}}}"##,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.album, None);
        assert_eq!(st.artwork_url, None);
        // The context name still feeds the source attribute.
        assert_eq!(st.source.as_deref(), Some("Context Album"));
    }

    #[tokio::test]
    async fn item_album_and_cover_win_over_the_context() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Song"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/item","size":"default"}]},"parent":{"entity":{"uri":"spotify:album:item","entity_type":"album","decorations":{"identity":{"name":"Item Album"}}}}}},"context":{"uri":"spotify:album:ctx","entity_type":"album","decorations":{"identity":{"name":"Context Album"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/ctx","size":"default"}]}}}}"##,
        )
        .await;
        let st = state.read().await;
        // The context is only a fallback: the item's own parent/cover win.
        assert_eq!(st.album.as_deref(), Some("Item Album"));
        assert_eq!(st.artwork_url.as_deref(), Some("https://i.scdn.co/item"));
        // The context name always feeds the source attribute.
        assert_eq!(st.source.as_deref(), Some("Context Album"));
    }

    #[tokio::test]
    async fn non_album_context_does_not_borrow_its_name_for_the_album() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Song"}}},"context":{"uri":"spotify:playlist:xyz","entity_type":"playlist","decorations":{"identity":{"name":"Today's Top Hits"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/mosaic","size":"default"}]}}}}"##,
        )
        .await;
        let st = state.read().await;
        // A playlist is not the track's album, and its mosaic is not the
        // track's cover: neither is borrowed.
        assert_eq!(st.album, None);
        assert_eq!(st.artwork_url, None);
        assert_eq!(st.source.as_deref(), Some("Today's Top Hits"));
    }

    #[tokio::test]
    async fn bare_snapshots_do_not_blank_resolved_labels() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/cover","size":"large"}]},"parent":{"entity":{"uri":"spotify:album:def","entity_type":"album","decorations":{"identity":{"name":"Album Name"}}}},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}],"playback":{"duration_ms":210000}}}}"##,
        )
        .await;
        // soloist then repeats the same track with nothing but its URI: the
        // labels must not flash back to "Unknown".
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"playback":{"duration_ms":210000}}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.track.as_deref(), Some("Buoyant"));
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
        assert_eq!(st.album.as_deref(), Some("Album Name"));
        assert_eq!(st.artwork_url.as_deref(), Some("https://i.scdn.co/cover"));
        assert_eq!(st.media_duration_ms, Some(210000));
    }

    #[tokio::test]
    async fn a_new_bare_track_does_not_inherit_the_previous_labels() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"First Song"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/first","size":"large"}]},"parent":{"entity":{"uri":"spotify:album:def","entity_type":"album","decorations":{"identity":{"name":"First Album"}}}},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"First Artist"}}}}]}}}"##,
        )
        .await;
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:next","entity_type":"track","decorations":{"playback":{"duration_ms":5000}}}}"#,
        )
        .await;
        let st = state.read().await;
        // A different URI with no metadata must read as unknown, not as the
        // previous track's labels.
        assert_eq!(st.media_content_id.as_deref(), Some("spotify:track:next"));
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.album, None);
        assert_eq!(st.artwork_url, None);
    }

    #[tokio::test]
    async fn idle_snapshot_without_item_clears_everything() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Song"},"creators":[{"entity":{"uri":"spotify:artist:xyz","decorations":{"identity":{"name":"Artist"}}}}]}}}"##,
        )
        .await;
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"idle","volume":100}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.media_content_id, None);
        assert!(st.item_creators.is_empty());
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
    async fn unrelated_oembed_result_does_not_blank_a_preserved_artist() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        // The track is identified by its snapshot...
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}]}}}"##,
        )
        .await;
        // ...then repeated bare, so the name is only kept by the sticky rule.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"playback":{"duration_ms":210000}}}}"#,
        )
        .await;
        assert_eq!(state.read().await.artist.as_deref(), Some("Manic Focus"));
        let mut st = state.write().await;
        // A queued track's artist resolves while this one plays: the published
        // artist must survive (only a track change clears it).
        assert!(!apply_oembed_result(
            &mut st,
            "spotify:artist:unrelated",
            "Someone Else"
        ));
        assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
        assert_eq!(
            st.oembed.get("spotify:artist:unrelated"),
            Some("Someone Else")
        );
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
                    uri: Some("spotify:artist:xyz".to_string()),
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

    /// A snapshot that already identifies the track (title, artist, album,
    /// cover) must not fire any network fallback — not the embed fetch, and
    /// not an oEmbed artist lookup for a URI that the queue recorded back when
    /// that creator still had no name.
    /// soloist may omit a nested entity's `uri` (the API reference allows it),
    /// in which case the snapshot still hands us the artist *name*. It must be
    /// shown, not discarded for want of a URI to look up — and nothing needs
    /// looking up either.
    #[tokio::test]
    async fn creator_with_a_name_but_no_uri_still_labels_the_artist() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = OembedResolver::new(state.clone(), tx.clone());
        let embed = EmbedResolver::new(state.clone(), tx.clone());
        handle_text(
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"creators":[{"entity":{"entity_type":"artist","decorations":{"identity":{"name":"Artist Name"}}}}]}}}"#,
            &state,
            &tx,
            Some(&resolver),
            Some(&embed),
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.track.as_deref(), Some("My Song"));
            assert_eq!(st.artist.as_deref(), Some("Artist Name"));
            assert_eq!(
                st.item_creators,
                vec![CreatorRef {
                    uri: None,
                    name: Some("Artist Name".to_string()),
                }]
            );
        }
        // Nothing is resolvable here, so nothing is requested.
        assert_eq!(resolver.pending_lookups().await, 0);
        assert_eq!(embed.pending_fetches().await, 0);
    }

    /// The name-only creator must also survive a repeated bare snapshot, and a
    /// creator with neither a URI nor a name must still be dropped.
    #[tokio::test]
    async fn name_only_creator_is_kept_but_empty_creators_are_dropped() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"creators":[{"entity":{"decorations":{"identity":{"name":"Artist Name"}}}},{"entity":{"uri":"spotify:artist:xyz","decorations":{"identity":{"name":"Second Artist"}}}},{"entity":{"decorations":{"identity":{"name":""}}}}]}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.artist.as_deref(), Some("Artist Name"));
            assert_eq!(st.item_creators.len(), 2, "empty creator not dropped");
            assert_eq!(st.item_creators[0].uri, None);
            assert_eq!(
                st.item_creators[1].uri.as_deref(),
                Some("spotify:artist:xyz")
            );
        }
        // A repeated bare snapshot keeps the label (sticky rule).
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"playback":{"duration_ms":210000}}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Artist Name"));
        assert!(st.item_creators.is_empty());
    }

    #[tokio::test]
    async fn fully_identified_snapshot_triggers_no_lookups() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = OembedResolver::new(state.clone(), tx.clone());
        let embed = EmbedResolver::new(state.clone(), tx.clone());
        // Queue snapshot: this track is upcoming, its artist URI known but not
        // yet named. (Fed without resolvers so the queue prefetch, which is
        // legitimate here, doesn't pollute the assertion below.)
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":""}}}}]}}}]}"#,
        )
        .await;
        // Playback snapshot with everything soloist can provide.
        handle_text(
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"Buoyant"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/cover","size":"large"}]},"parent":{"entity":{"uri":"spotify:album:def","entity_type":"album","decorations":{"identity":{"name":"Album Name"}}}},"creators":[{"entity":{"uri":"spotify:artist:xyz","entity_type":"artist","decorations":{"identity":{"name":"Manic Focus"}}}}],"playback":{"duration_ms":210000}}}}"##,
            &state,
            &tx,
            Some(&resolver),
            Some(&embed),
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.track.as_deref(), Some("Buoyant"));
            assert_eq!(st.artist.as_deref(), Some("Manic Focus"));
            assert_eq!(st.album.as_deref(), Some("Album Name"));
        }
        assert_eq!(resolver.pending_lookups().await, 0, "oEmbed lookup fired");
        assert_eq!(embed.pending_fetches().await, 0, "embed fetch fired");
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
            handle_text(json, &state, &tx, Some(&resolver), None),
        )
        .await;
        assert!(
            result.is_ok(),
            "handle_text deadlocked: QueueChanged write guard held across maybe_lookup's read()"
        );
    }

    /// The reference encodes numbers as integers, but a float encoding used to
    /// discard the whole snapshot (only a debug line, no state update).
    #[tokio::test]
    async fn float_encoded_snapshot_is_applied() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"playback":{"duration_ms":210000.0}}},"position":{"position_ms":45000.0,"timestamp_ms":1747654321000.0,"speed":1},"volume":65.0,"is_active":true}"##,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.track.as_deref(), Some("My Song"));
        assert_eq!(st.media_duration_ms, Some(210000));
        assert_eq!(st.volume, 65);
        assert_eq!(st.position_anchor.position_ms, 45000);
        assert!(st.is_active);
    }

    /// A snapshot that omits optional fields must still apply what it carries —
    /// before this, any single missing field dropped the entire event.
    #[tokio::test]
    async fn stripped_snapshot_applies_what_it_carries() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.volume = 42;
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"}}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.track.as_deref(), Some("My Song"));
        assert_eq!(st.media_content_id.as_deref(), Some("spotify:track:abc"));
        // The absent fields were skipped, not applied as zero/false: the device
        // volume must not be reset by a snapshot with no volume.
        assert_eq!(st.volume, 42);
    }

    /// A granular event that arrives without its one payload field is ignored:
    /// no state is guessed from a missing value.
    #[tokio::test]
    async fn granular_event_without_its_field_is_ignored() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.volume = 42;
        feed(
            &state,
            &tx,
            r#"{"type":"playback_changed","status":"playing"}"#,
        )
        .await;
        feed(&state, &tx, r#"{"type":"volume_changed"}"#).await;
        feed(&state, &tx, r#"{"type":"playback_changed"}"#).await;
        feed(&state, &tx, r#"{"type":"device_changed"}"#).await;
        feed(&state, &tx, r#"{"type":"position_sync"}"#).await;
        feed(&state, &tx, r#"{"type":"options_changed"}"#).await;
        let st = state.read().await;
        assert_eq!(st.volume, 42);
        assert_eq!(st.status, "playing");
        assert!(!st.shuffle);
        assert_eq!(st.repeat, "");
    }

    /// A stripped auth_state must not be read as a logout: that would clear the
    /// published track.
    #[tokio::test]
    async fn auth_state_without_logged_in_does_not_clear_the_track() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"}}}}"#,
        )
        .await;
        feed(&state, &tx, r#"{"type":"auth_state","is_active":false}"#).await;
        let st = state.read().await;
        assert_eq!(st.track.as_deref(), Some("My Song"));
    }

    /// A logout (documented auth_state on logout / session replacement) drops the
    /// previous session's track instead of publishing it forever.
    #[tokio::test]
    async fn logout_clears_the_published_track() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"auth_state","logged_in":true,"is_active":true,"device_name":"Soloist"}"#,
        )
        .await;
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"creators":[{"entity":{"uri":"spotify:artist:xyz","decorations":{"identity":{"name":"Artist"}}}}]}},"context":{"uri":"spotify:playlist:p","decorations":{"identity":{"name":"Playlist"}}},"upcoming":[]}"##,
        )
        .await;
        assert_eq!(state.read().await.artist.as_deref(), Some("Artist"));

        feed(
            &state,
            &tx,
            r#"{"type":"auth_state","logged_in":false,"is_active":false,"device_name":"Soloist"}"#,
        )
        .await;
        let st = state.read().await;
        assert!(!st.logged_in);
        assert!(!st.is_active);
        assert_eq!(st.status, "idle");
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.media_content_id, None);
        assert_eq!(st.source, None);
    }

    /// `available_actions` describes what the current state supports. A snapshot
    /// that omits the field keeps the previous set; an explicit empty object
    /// clears it.
    #[tokio::test]
    async fn available_actions_are_applied_and_kept_when_omitted() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.set_power(true);
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","available_actions":{"pause":{},"seek_forward":{"step_ms":15000},"skip_next":{}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(
                st.available_actions.keys().cloned().collect::<Vec<_>>(),
                vec!["pause", "seek_forward", "skip_next"]
            );
            assert_eq!(st.seek_step_ms(), Some(15000));
            assert_eq!(
                st.attributes().available_actions,
                Some(vec!["pause", "seek_forward", "skip_next"])
            );
        }
        // A later snapshot without the field keeps them...
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing"}"#,
        )
        .await;
        assert_eq!(state.read().await.available_actions.len(), 3);
        // ...and one that really says "none" clears them.
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"idle","available_actions":{}}"#,
        )
        .await;
        assert!(state.read().await.available_actions.is_empty());
    }

    /// A podcast episode reports `media_content_type: podcast` and its parent
    /// (the show) as the series title — never as the album.
    #[tokio::test]
    async fn episode_uses_the_show_as_series_title() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.set_power(true);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:episode:xyz","entity_type":"episode","decorations":{"identity":{"name":"Episode 1"},"parent":{"entity":{"uri":"spotify:show:s","entity_type":"show","decorations":{"identity":{"name":"Some Show"}}}},"playback":{"duration_ms":3000000}}},"context":{"uri":"spotify:show:s","entity_type":"show","decorations":{"identity":{"name":"Some Show"}}}}"##,
        )
        .await;
        {
            let st = state.read().await;
            assert_eq!(st.track.as_deref(), Some("Episode 1"));
            assert_eq!(st.content_kind, ContentKind::Podcast);
            assert_eq!(st.media_series_title.as_deref(), Some("Some Show"));
            assert_eq!(st.album, None);
            let attrs = st.attributes();
            assert_eq!(attrs.media_content_type, Some("podcast"));
            assert_eq!(attrs.media_series_title, Some("Some Show"));
        }
        // A music track afterwards goes back to music + album.
        feed(
            &state,
            &tx,
            r##"{"type":"track_changed","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"parent":{"entity":{"uri":"spotify:album:a","entity_type":"album","decorations":{"identity":{"name":"Album Name"}}}}}}}"##,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.content_kind, ContentKind::Music);
        assert_eq!(st.media_series_title, None);
        assert_eq!(st.album.as_deref(), Some("Album Name"));
        assert_eq!(st.attributes().media_content_type, Some("music"));
    }

    /// An `ad` item must not rename the now-playing card: the real track's
    /// identity and URI stay put, only the flag flips.
    #[tokio::test]
    async fn ad_item_keeps_the_track_identity() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.set_power(true);
        feed(
            &state,
            &tx,
            r##"{"type":"track_changed","item":{"uri":"spotify:track:a","entity_type":"track","decorations":{"identity":{"name":"Song A"},"visual_identity":{"cover":[{"url":"https://i.scdn.co/a","size":"large"}]}}}}"##,
        )
        .await;
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:ad:1","entity_type":"ad","decorations":{"identity":{"name":"Advertisement"}}}}"#,
        )
        .await;
        {
            let st = state.read().await;
            assert!(st.is_ad);
            assert_eq!(st.track.as_deref(), Some("Song A"));
            assert_eq!(st.media_content_id.as_deref(), Some("spotify:track:a"));
            assert_eq!(st.artwork_url.as_deref(), Some("https://i.scdn.co/a"));
            assert!(st.attributes().is_ad);
        }
        // The next real track clears the flag.
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:b","entity_type":"track","decorations":{"identity":{"name":"Song B"}}}}"#,
        )
        .await;
        let st = state.read().await;
        assert!(!st.is_ad);
        assert_eq!(st.track.as_deref(), Some("Song B"));
    }

    /// `content_ratings` (e.g. `explicit`) is published, survives the repeated
    /// bare snapshots of the same track, and clears on a track change.
    #[tokio::test]
    async fn content_rating_is_published_and_sticky() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        state.write().await.set_power(true);
        feed(
            &state,
            &tx,
            r##"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"playback":{"duration_ms":1000,"content_ratings":["explicit"]}}}}"##,
        )
        .await;
        assert_eq!(
            state.read().await.attributes().media_content_rating,
            Some("explicit")
        );
        feed(
            &state,
            &tx,
            r#"{"type":"playback_state","status":"playing","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"playback":{"duration_ms":1000}}}}"#,
        )
        .await;
        assert_eq!(
            state.read().await.attributes().media_content_rating,
            Some("explicit")
        );
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:def","entity_type":"track","decorations":{"identity":{"name":"Clean Song"}}}}"#,
        )
        .await;
        assert_eq!(state.read().await.attributes().media_content_rating, None);
    }

    /// Queue entries may ship empty decorations (the reference example does):
    /// showing a raw `spotify:track:...` URI as "next up" is worse than showing
    /// nothing, so those entries are dropped from the published list.
    #[tokio::test]
    async fn nameless_queue_entries_are_skipped() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r#"{"type":"queue_changed","previous":[],"upcoming":[{"uid":"u1","source":"context","item":{"uri":"spotify:track:1","entity_type":"track","decorations":{"identity":{"name":"Next Song"},"creators":[{"entity":{"uri":"spotify:artist:1","decorations":{"identity":{"name":"Next Artist"}}}}]}}},{"uid":"u2","source":"autoplay","item":{"uri":"spotify:track:2","entity_type":"track","decorations":{}}}]}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.upcoming, vec!["Next Song - Next Artist"]);
        // The URI is still learned for the artist fallback, even though the entry
        // is not published as a label.
        assert!(st.track_artist_uris.contains("spotify:track:1"));
        // The nameless entry taught us nothing (no name, no creator URI).
        assert!(!st.track_artist_uris.contains("spotify:track:2"));
    }

    /// Cover entries without a URL are skipped instead of being picked (or
    /// failing the entity), and an unknown size name outranks `small`.
    #[tokio::test]
    async fn cover_without_url_is_skipped() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            r##"{"type":"track_changed","item":{"uri":"spotify:track:abc","entity_type":"track","decorations":{"identity":{"name":"My Song"},"visual_identity":{"cover":[{"size":"large"},{"url":"https://i.scdn.co/small","size":"small"},{"url":"https://i.scdn.co/medium","size":"medium"}]}}}}"##,
        )
        .await;
        assert_eq!(
            state.read().await.artwork_url.as_deref(),
            Some("https://i.scdn.co/medium")
        );
        // Only a URL-less cover: the artwork stays empty, the item still applies.
        feed(
            &state,
            &tx,
            r#"{"type":"track_changed","item":{"uri":"spotify:track:def","entity_type":"track","decorations":{"identity":{"name":"Other Song"},"visual_identity":{"cover":[{"size":"large"}]}}}}"#,
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.track.as_deref(), Some("Other Song"));
        assert_eq!(st.artwork_url, None);
    }
}
