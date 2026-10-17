//! Track-metadata fallback via Spotify's public embed page.
//!
//! soloist's first snapshot for a new track can ship nothing but the URI and
//! the duration — no `identity`, no `creators`, no `parent` album, no cover:
//!
//! ```json
//! {"uri":"spotify:track:0u5aO1GYsIhAWCPuXdwnak","entity_type":"track",
//!  "decorations":{"playback":{"duration_ms":201173}}}
//! ```
//!
//! With no creator entities there is no artist URI either, so the oEmbed
//! fallback in [`super::oembed`] has nothing to look up and every label
//! degrades to "Unknown" — the artist-URI path can only learn from
//! `queue_changed` entries, which never mention a track that was started
//! without being seen in the queue first.
//!
//! The page the oEmbed iframe itself loads carries the missing fields in its
//! `__NEXT_DATA__` payload (unauthenticated, same origin and trust as the
//! oEmbed call):
//!
//! ```text
//! props.pageProps.state.data.entity
//!   name    "Today - 2011 Remaster"
//!   artists [{ "name": "The Smashing Pumpkins", "uri": "spotify:artist:40Yq…" }]
//!   duration 201173
//!   visualIdentity.image [{ url, maxWidth, … }]
//! ```
//!
//! The embed payload has no album field, so the album falls back to the
//! playback context when that context *is* an album (see
//! [`super::apply::apply_context`]). The anonymous access token in the same
//! payload is *not* usable against `api.spotify.com` (`/api/token` now rejects
//! third-party use and the Web API returns `QUOTA_EXCEEDED`), so this page is
//! the only no-auth route from a track URI to its artist.
//!
//! Lookups are one-per-track-per-session: results are cached per track URI
//! (bounded FIFO in [`PlaybackState`]) and completed attempts are recorded, so
//! the repeated bare snapshots soloist sends for the same track neither
//! re-request nor blank the labels again.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;
use tokio::sync::{broadcast, Mutex, RwLock};
use tracing::{debug, warn};

use crate::state::{PlaybackState, TrackMeta};

/// Resolves track metadata from Spotify's public embed page — the fallback
/// when soloist's snapshot ships a track URI whose identity, creators, album
/// and cover are all empty. Owns the in-flight set so repeated snapshots of
/// the same track never fire duplicate requests.
pub struct EmbedResolver {
    state: Arc<RwLock<PlaybackState>>,
    state_tx: broadcast::Sender<()>,
    /// Track URIs currently being fetched.
    in_flight: Arc<Mutex<HashSet<String>>>,
}

impl EmbedResolver {
    pub fn new(state: Arc<RwLock<PlaybackState>>, state_tx: broadcast::Sender<()>) -> Self {
        Self {
            state,
            state_tx,
            in_flight: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Fetch embed-page metadata for the currently playing track URI. `None`,
    /// a non-track URI, an already resolved track, or one whose lookup already
    /// completed are all no-ops. The caller decides *whether* the item needs it
    /// (see the `dispatch_oembed` block in [`super::apply`]), so this only
    /// takes the read lock to consult the caches.
    pub async fn maybe_fetch(&self, uri: Option<&str>) {
        let Some(uri) = uri else {
            return;
        };
        if !uri.starts_with("spotify:track:") {
            return;
        }
        {
            let st = self.state.read().await;
            if st.track_meta.contains(uri) || st.track_meta_fetched.contains(uri) {
                return;
            }
        }
        {
            let mut in_flight = self.in_flight.lock().await;
            if !in_flight.insert(uri.to_string()) {
                return;
            }
        }
        self.spawn_fetch(uri.to_string());
    }

    /// Fetches requested but not yet completed. Test-only seam.
    #[cfg(test)]
    pub(crate) async fn pending_fetches(&self) -> usize {
        self.in_flight.lock().await.len()
    }

    fn spawn_fetch(&self, uri: String) {
        let state = self.state.clone();
        let state_tx = self.state_tx.clone();
        let in_flight = self.in_flight.clone();
        tokio::spawn(async move {
            let meta = fetch_track_meta(&uri).await;
            {
                let mut guard = in_flight.lock().await;
                guard.remove(&uri);
            }
            // Never block on the state lock forever: if the lock is wedged
            // (e.g. by a stuck publisher) log it and skip, so the resolution
            // degrades into a logged warning instead of a silent hang.
            let Ok(mut st) = tokio::time::timeout(Duration::from_secs(5), state.write()).await
            else {
                warn!("embed apply: timed out waiting for state lock ({})", uri);
                return;
            };
            // Record the attempt either way: the fallback is a last resort, so
            // a track that has no embed payload must not be retried on every
            // subsequent event.
            st.track_meta_fetched.insert(uri.clone(), ());
            let Some(meta) = meta else {
                debug!("embed lookup failed for {}", uri);
                return;
            };
            debug!("embed resolved track metadata: {} = {:?}", uri, meta);
            if apply_embed_result(&mut st, &uri, meta) {
                drop(st);
                let _ = state_tx.send(());
            }
        });
    }
}

/// Cache resolved embed metadata for `uri` and fill in whatever the current
/// item is still missing, returning true (so the caller broadcasts) when that
/// changed a published label. soloist's own snapshot values stay authoritative
/// — the embed payload only fills gaps — and nothing is touched when the track
/// has already moved on.
pub(crate) fn apply_embed_result(state: &mut PlaybackState, uri: &str, meta: TrackMeta) -> bool {
    // Seed the artist-name cache too, so any other track by this artist
    // resolves without its own lookup.
    if let (Some(artist_uri), Some(artist)) = (&meta.artist_uri, &meta.artist) {
        state.oembed.insert(artist_uri.clone(), artist.clone());
    }
    let track = meta.track.clone();
    let album = meta.album.clone();
    let artwork = meta.artwork.clone();
    let duration_ms = meta.duration_ms;
    state.track_meta.insert(uri.to_string(), meta);

    if state.media_content_id.as_deref() != Some(uri) {
        debug!(
            "embed metadata cached for a track that is no longer playing: {}",
            uri
        );
        return false;
    }

    let before = (
        state.track.clone(),
        state.artist.clone(),
        state.album.clone(),
        state.artwork_url.clone(),
        state.media_duration_ms,
    );
    if state.track.is_none() {
        state.track = track;
    }
    if state.album.is_none() {
        state.album = album;
    }
    if state.artwork_url.is_none() {
        state.artwork_url = artwork;
    }
    if state.media_duration_ms.is_none() {
        state.media_duration_ms = duration_ms;
    }
    // Recompute through the normal resolution chain so the embed artist also
    // lands when the snapshot shipped creator refs without names (and so a
    // payload with no artist can't blank a name we already published).
    state.refresh_artist();
    before
        != (
            state.track.clone(),
            state.artist.clone(),
            state.album.clone(),
            state.artwork_url.clone(),
            state.media_duration_ms,
        )
}

/// Build the embed-page URL for a track URI. None when the URI isn't a track.
fn embed_url(uri: &str) -> Option<String> {
    let id = uri.strip_prefix("spotify:track:")?;
    if id.is_empty() {
        return None;
    }
    Some(format!("https://open.spotify.com/embed/track/{}", id))
}

/// Fetch and parse the embed page for a `spotify:track:` URI (no auth, via
/// `curl`, already required at runtime for the soloist binary download). None
/// on any failure (network, non-success status, unparseable or empty payload).
async fn fetch_track_meta(uri: &str) -> Option<TrackMeta> {
    let url = embed_url(uri)?;
    let out = Command::new("curl")
        .args([
            "-fsSL",
            "--max-time",
            "10",
            "-A",
            "soloist-bridge (track metadata)",
        ])
        .arg(&url)
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    parse_embed_track_meta(&String::from_utf8_lossy(&out.stdout))
}

/// Extract the `__NEXT_DATA__` JSON payload body from the embed page HTML.
fn next_data_json(html: &str) -> Option<&str> {
    const MARKER: &str = "id=\"__NEXT_DATA__\"";
    let marker_at = html.find(MARKER)?;
    let body_at = html[marker_at..].find('>')? + marker_at + 1;
    let body_end = html[body_at..].find("</script>")? + body_at;
    Some(&html[body_at..body_end])
}

/// Parse a track's embed page into [`TrackMeta`]. None when the payload is
/// missing, isn't a track embed, or carries nothing usable.
fn parse_embed_track_meta(html: &str) -> Option<TrackMeta> {
    let json = next_data_json(html)?;
    let data: serde_json::Value = serde_json::from_str(json).ok()?;
    let entity = data
        .get("props")?
        .get("pageProps")?
        .get("state")?
        .get("data")?
        .get("entity")?;
    if entity.get("type").and_then(|t| t.as_str()) != Some("track") {
        return None;
    }

    let artists = entity.get("artists").and_then(|a| a.as_array());
    let primary = artists.and_then(|a| a.first());
    let non_empty = |v: Option<&serde_json::Value>| {
        v.and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    };

    let meta = TrackMeta {
        track: non_empty(entity.get("name")),
        artist: non_empty(primary.and_then(|a| a.get("name"))),
        artist_uri: non_empty(primary.and_then(|a| a.get("uri")))
            .filter(|u| u.starts_with("spotify:artist:")),
        // Track embeds carry no album field; `apply_context` fills it from an
        // album playback context instead.
        album: None,
        artwork: entity
            .get("visualIdentity")
            .and_then(|v| v.get("image"))
            .and_then(|i| i.as_array())
            .and_then(|images| {
                images
                    .iter()
                    .max_by_key(|i| i.get("maxWidth").and_then(|w| w.as_u64()).unwrap_or(0))
                    .and_then(|i| i.get("url"))
                    .and_then(|u| u.as_str())
                    .map(str::to_string)
            }),
        duration_ms: entity
            .get("duration")
            .and_then(|d| d.as_u64())
            .filter(|d| *d > 0),
    };
    if meta.is_empty() {
        None
    } else {
        Some(meta)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{CreatorRef, PlaybackState};

    /// A trimmed copy of the real `open.spotify.com/embed/track/<id>` payload
    /// for `spotify:track:0u5aO1GYsIhAWCPuXdwnak` (the bare-snapshot track from
    /// the field log), with the irrelevant fields dropped.
    const EMBED_HTML: &str = r#"<!DOCTYPE html><html><head>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":{"entity":{"type":"track","name":"Today - 2011 Remaster","uri":"spotify:track:0u5aO1GYsIhAWCPuXdwnak","id":"0u5aO1GYsIhAWCPuXdwnak","artists":[{"name":"The Smashing Pumpkins","uri":"spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i"}],"duration":201173,"relatedEntityUri":"spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i","visualIdentity":{"image":[{"url":"https://image-cdn-fa.spotifycdn.com/image/ab67616d000048515274788f34fc7656d2856dfd","maxHeight":64,"maxWidth":64},{"url":"https://image-cdn-fa.spotifycdn.com/image/ab67616d0000b2735274788f34fc7656d2856dfd","maxHeight":640,"maxWidth":640},{"url":"https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e025274788f34fc7656d2856dfd","maxHeight":300,"maxWidth":300}]}}}}}}}</script>
</head><body></body></html>"#;

    #[test]
    fn embed_url_only_accepts_track_uris() {
        assert_eq!(
            embed_url("spotify:track:abc"),
            Some("https://open.spotify.com/embed/track/abc".to_string())
        );
        assert_eq!(embed_url("spotify:artist:abc"), None);
        assert_eq!(embed_url("spotify:album:abc"), None);
        assert_eq!(embed_url("spotify:track:"), None);
    }

    #[test]
    fn parses_the_bare_snapshot_track() {
        let meta = parse_embed_track_meta(EMBED_HTML).expect("payload should parse");
        assert_eq!(meta.track.as_deref(), Some("Today - 2011 Remaster"));
        assert_eq!(meta.artist.as_deref(), Some("The Smashing Pumpkins"));
        assert_eq!(
            meta.artist_uri.as_deref(),
            Some("spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i")
        );
        assert_eq!(meta.album, None);
        // Largest image wins.
        assert_eq!(
            meta.artwork.as_deref(),
            Some("https://image-cdn-fa.spotifycdn.com/image/ab67616d0000b2735274788f34fc7656d2856dfd")
        );
        assert_eq!(meta.duration_ms, Some(201173));
        assert!(!meta.is_empty());
    }

    #[test]
    fn rejects_payloads_without_usable_metadata() {
        // No __NEXT_DATA__ at all (an error page, or a redirected shell).
        assert_eq!(
            parse_embed_track_meta("<html><body>nope</body></html>"),
            None
        );
        // Payload present but not a track embed.
        assert_eq!(
            parse_embed_track_meta(
                r#"<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":{"entity":{"type":"album","name":"X"}}}}}}</script>"#
            ),
            None
        );
        // A track entity with every field empty is a miss, not a cached blank.
        assert_eq!(
            parse_embed_track_meta(
                r#"<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":{"entity":{"type":"track","name":"","artists":[],"duration":0}}}}}}</script>"#
            ),
            None
        );
        // Malformed JSON.
        assert_eq!(
            parse_embed_track_meta(
                r#"<script id="__NEXT_DATA__" type="application/json">{not json</script>"#
            ),
            None
        );
    }

    /// The exact field-log shape: the playback snapshot ships only a URI and a
    /// duration, so nothing else could name the track. The embed result must
    /// fill title, artist, artwork and duration and clear "Unknown".
    #[tokio::test]
    async fn fills_labels_for_a_decoration_less_snapshot() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            media_content_id: Some("spotify:track:0u5aO1GYsIhAWCPuXdwnak".to_string()),
            media_duration_ms: Some(201173),
            ..Default::default()
        };
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        assert_eq!(st.album, None);

        let meta = parse_embed_track_meta(EMBED_HTML).expect("payload should parse");
        assert!(apply_embed_result(
            &mut st,
            "spotify:track:0u5aO1GYsIhAWCPuXdwnak",
            meta
        ));

        assert_eq!(st.track.as_deref(), Some("Today - 2011 Remaster"));
        assert_eq!(st.artist.as_deref(), Some("The Smashing Pumpkins"));
        assert_eq!(
            st.artwork_url.as_deref(),
            Some("https://image-cdn-fa.spotifycdn.com/image/ab67616d0000b2735274788f34fc7656d2856dfd")
        );
        // The snapshot's own duration is authoritative and untouched.
        assert_eq!(st.media_duration_ms, Some(201173));
        // The artist URI was seeded for future tracks.
        assert_eq!(
            st.oembed.get("spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i"),
            Some("The Smashing Pumpkins")
        );
        // Cached, so a repeated bare snapshot is a no-op rather than a re-request.
        assert!(st
            .track_meta
            .contains("spotify:track:0u5aO1GYsIhAWCPuXdwnak"));
    }

    #[tokio::test]
    async fn snapshot_values_win_over_embed_values() {
        // A snapshot that already identified everything: the artist is exactly
        // what `apply_entity` stores for a named creator.
        let mut st = PlaybackState {
            status: "playing".to_string(),
            media_content_id: Some("spotify:track:0u5aO1GYsIhAWCPuXdwnak".to_string()),
            track: Some("From soloist".to_string()),
            artist: Some("From soloist".to_string()),
            album: Some("From soloist".to_string()),
            artwork_url: Some("https://example.invalid/from-soloist".to_string()),
            media_duration_ms: Some(1234),
            item_creators: vec![CreatorRef {
                uri: Some("spotify:artist:from-soloist".to_string()),
                name: Some("From soloist".to_string()),
            }],
            ..Default::default()
        };
        let meta = parse_embed_track_meta(EMBED_HTML).expect("payload should parse");
        // Nothing was missing, so nothing changes and no publish is requested.
        assert!(!apply_embed_result(
            &mut st,
            "spotify:track:0u5aO1GYsIhAWCPuXdwnak",
            meta
        ));
        assert_eq!(st.track.as_deref(), Some("From soloist"));
        assert_eq!(st.artist.as_deref(), Some("From soloist"));
        assert_eq!(st.album.as_deref(), Some("From soloist"));
        assert_eq!(
            st.artwork_url.as_deref(),
            Some("https://example.invalid/from-soloist")
        );
        assert_eq!(st.media_duration_ms, Some(1234));
        // The embed artist URI is still cached, for other tracks by them.
        assert_eq!(
            st.oembed.get("spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i"),
            Some("The Smashing Pumpkins")
        );
    }

    #[tokio::test]
    async fn result_is_cached_but_not_applied_after_track_change() {
        let mut st = PlaybackState {
            status: "playing".to_string(),
            media_content_id: Some("spotify:track:other".to_string()),
            ..Default::default()
        };
        let meta = parse_embed_track_meta(EMBED_HTML).expect("payload should parse");
        assert!(!apply_embed_result(
            &mut st,
            "spotify:track:0u5aO1GYsIhAWCPuXdwnak",
            meta
        ));
        // The current item is untouched...
        assert_eq!(st.track, None);
        assert_eq!(st.artist, None);
        // ...but the metadata (and the seeded artist name) is cached for later.
        assert!(st
            .track_meta
            .contains("spotify:track:0u5aO1GYsIhAWCPuXdwnak"));
        assert_eq!(
            st.track_meta
                .get("spotify:track:0u5aO1GYsIhAWCPuXdwnak")
                .and_then(|m| m.track.as_deref()),
            Some("Today - 2011 Remaster")
        );
    }

    /// Live smoke test against the real embed endpoint — not part of the
    /// default run (it needs network access), but it is what verifies the
    /// parser still matches Spotify's markup. Run with:
    /// `cargo test -- --ignored embed_page_still_has_the_fields_we_parse`
    #[tokio::test]
    #[ignore = "requires network access to open.spotify.com"]
    async fn embed_page_still_has_the_fields_we_parse() {
        let meta = fetch_track_meta("spotify:track:0u5aO1GYsIhAWCPuXdwnak")
            .await
            .expect("live embed page should parse");
        assert_eq!(meta.track.as_deref(), Some("Today - 2011 Remaster"));
        assert_eq!(meta.artist.as_deref(), Some("The Smashing Pumpkins"));
        assert_eq!(
            meta.artist_uri.as_deref(),
            Some("spotify:artist:40Yq4vzPs9VNUrIBG5Jr2i")
        );
        assert!(meta.artwork.is_some());
        assert_eq!(meta.duration_ms, Some(201173));
    }

    #[tokio::test]
    async fn maybe_fetch_skips_cached_attempted_and_non_track_uris() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = EmbedResolver::new(state.clone(), tx.clone());
        {
            let mut st = state.write().await;
            st.track_meta
                .insert("spotify:track:known".to_string(), TrackMeta::default());
            st.track_meta_fetched
                .insert("spotify:track:tried".to_string(), ());
        }
        // Cached, already-attempted, non-track and absent URIs spawn nothing.
        resolver.maybe_fetch(Some("spotify:track:known")).await;
        resolver.maybe_fetch(Some("spotify:track:tried")).await;
        resolver.maybe_fetch(Some("spotify:artist:x")).await;
        resolver.maybe_fetch(None).await;
        assert_eq!(resolver.in_flight.lock().await.len(), 0);
    }

    #[tokio::test]
    async fn maybe_fetch_dedupes_a_fresh_track_uri() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = EmbedResolver::new(state.clone(), tx.clone());
        // The `#[tokio::test]` runtime is single-threaded, so the spawned fetch
        // is not polled until this test yields: `in_flight` is still marked and
        // no `curl` runs. This asserts the claim-once contract.
        resolver.maybe_fetch(Some("spotify:track:fresh")).await;
        assert!(resolver
            .in_flight
            .lock()
            .await
            .contains("spotify:track:fresh"));
        // A concurrent snapshot of the same track must not queue a second fetch.
        resolver.maybe_fetch(Some("spotify:track:fresh")).await;
        assert_eq!(resolver.in_flight.lock().await.len(), 1);
    }
}
