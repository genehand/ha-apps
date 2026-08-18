//! Last-resort artist-name resolution via Spotify's public oEmbed API
//! (`open.spotify.com/oembed`, no auth): when a creator entity ships a
//! `spotify:artist:` URI but no display name, the `title` of the artist's
//! embed is the artist name. Results are cached per artist URI (bounded FIFO
//! in [`PlaybackState`]) and deduplicated against in-flight lookups.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;
use tokio::sync::{broadcast, Mutex, RwLock};
use tracing::{debug, warn};

use crate::state::PlaybackState;

/// Resolves artist names from Spotify's public oEmbed API — the last-resort
/// source when a creator entity ships a `spotify:artist:` URI but no display
/// name and neither the queue metadata nor a previous lookup knows it.
/// oEmbed needs no auth: the `title` of an artist embed is the artist name.
pub struct OembedResolver {
    state: Arc<RwLock<PlaybackState>>,
    state_tx: broadcast::Sender<()>,
    /// Artist URIs currently being fetched, so repeated snapshots of the same
    /// track never fire duplicate requests.
    in_flight: Arc<Mutex<HashSet<String>>>,
}

impl OembedResolver {
    pub fn new(state: Arc<RwLock<PlaybackState>>, state_tx: broadcast::Sender<()>) -> Self {
        Self {
            state,
            state_tx,
            in_flight: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Fire oEmbed lookups for artist URIs that have no resolvable name yet.
    /// Skipped when the name is already cached (queue metadata or a previous
    /// oEmbed result) or already being fetched; otherwise a background task
    /// fetches the name and updates the artist label if the URI belongs to
    /// the currently playing item.
    pub async fn maybe_lookup(&self, uris: Vec<String>) {
        if uris.is_empty() {
            return;
        }
        let mut pending = Vec::new();
        {
            let st = self.state.read().await;
            for uri in uris {
                if st.queue_meta.artist_names.contains_key(&uri) || st.oembed.contains(&uri) {
                    continue;
                }
                pending.push(uri);
            }
        }
        if pending.is_empty() {
            return;
        }
        let mut in_flight = self.in_flight.lock().await;
        for uri in pending {
            if in_flight.insert(uri.clone()) {
                self.spawn_lookup(uri);
            }
        }
    }

    fn spawn_lookup(&self, uri: String) {
        let state = self.state.clone();
        let state_tx = self.state_tx.clone();
        let in_flight = self.in_flight.clone();
        tokio::spawn(async move {
            let name = fetch_oembed_artist_name(&uri).await;
            {
                let mut guard = in_flight.lock().await;
                guard.remove(&uri);
            }
            let Some(name) = name else {
                debug!("oEmbed lookup failed for {}", uri);
                return;
            };
            debug!("oEmbed resolved artist: {} = {}", uri, name);
            // Never block on the state lock forever: if the lock is wedged
            // (e.g. by a stuck publisher) log it and skip, so the resolution
            // degrades into a logged warning instead of a silent hang.
            let Ok(mut st) = tokio::time::timeout(Duration::from_secs(5), state.write()).await
            else {
                warn!("oEmbed apply: timed out waiting for state lock ({})", uri);
                return;
            };
            if apply_oembed_result(&mut st, &uri, &name) {
                drop(st);
                let _ = state_tx.send(());
            }
        });
    }
}

/// Apply a resolved oEmbed artist name: cache it for the session, then
/// recompute the current item's artist label and refresh it (and return true
/// so the caller broadcasts) whenever the resolution changed it. The label
/// is recomputed unconditionally — resolution order per creator is
/// snapshot-time name > queue metadata > oEmbed cache — so any resolution
/// that affects the currently playing item publishes, regardless of whether
/// the URI came from the item's own snapshot or from queue metadata.
pub(crate) fn apply_oembed_result(state: &mut PlaybackState, uri: &str, name: &str) -> bool {
    state.oembed.insert(uri.to_string(), name.to_string());
    let label = state.artist_label();
    if state.artist != label {
        debug!(
            "oEmbed artist updated: {} = {}; {} -> {}",
            uri,
            name,
            state.artist.as_deref().unwrap_or("<none>"),
            label.as_deref().unwrap_or("<none>")
        );
        state.artist = label;
        true
    } else {
        debug!("oEmbed artist cached for queued: {} = {}", uri, name);
        false
    }
}

/// Build the URL for Spotify's public oEmbed endpoint from an artist URI.
/// None when the URI isn't an artist URI — the only entity type whose embed
/// `title` is usable as an artist display name.
fn oembed_url(uri: &str) -> Option<String> {
    if !uri.starts_with("spotify:artist:") {
        return None;
    }
    Some(format!(
        "https://open.spotify.com/oembed?url={}",
        uri.replace(':', "%3A")
    ))
}

/// Fetch the artist name for a `spotify:artist:` URI via Spotify's public
/// oEmbed endpoint (no auth). Uses `curl`, already required at runtime for
/// the soloist binary download. None on any failure (network, non-success
/// status, missing/empty title).
async fn fetch_oembed_artist_name(uri: &str) -> Option<String> {
    let url = oembed_url(uri)?;
    let out = Command::new("curl")
        .args([
            "-fsSL",
            "--max-time",
            "10",
            "-A",
            "soloist-bridge (artist lookup)",
        ])
        .arg(&url)
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    parse_oembed_title(&String::from_utf8_lossy(&out.stdout))
}

/// Extract the `title` field from an oEmbed response — the artist name for an
/// artist embed. None when the payload isn't valid JSON or the title is empty.
fn parse_oembed_title(body: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    let title = v.get("title")?.as_str()?.trim();
    if title.is_empty() {
        None
    } else {
        Some(title.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_oembed_title() {
        // The oEmbed response for an artist embed carries the artist name in
        // `title` (verified against https://open.spotify.com/oembed).
        assert_eq!(
            parse_oembed_title(
                "{\"html\":\"<iframe></iframe>\",\"title\":\"Daft Punk\",\"provider_name\":\"Spotify\",\"type\":\"rich\"}"
            ),
            Some("Daft Punk".to_string())
        );
        // Empty or missing title, or a non-JSON body, is a miss.
        assert_eq!(parse_oembed_title("{\"title\":\"\"}"), None);
        assert_eq!(parse_oembed_title("{\"type\":\"rich\"}"), None);
        assert_eq!(parse_oembed_title("not json"), None);
    }

    #[test]
    fn oembed_url_encodes_artist_uris() {
        assert_eq!(
            oembed_url("spotify:artist:abc"),
            Some("https://open.spotify.com/oembed?url=spotify%3Aartist%3Aabc".to_string())
        );
        // Only artist URIs are eligible: other entity types' embed titles
        // aren't usable as artist display names.
        assert_eq!(oembed_url("spotify:track:abc"), None);
        assert_eq!(oembed_url("spotify:playlist:abc"), None);
    }

    #[tokio::test]
    async fn maybe_lookup_skips_known_names() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        let resolver = OembedResolver::new(state.clone(), tx.clone());
        {
            let mut st = state.write().await;
            st.oembed
                .insert("spotify:artist:known".to_string(), "Known".to_string());
            st.queue_meta
                .artist_names
                .insert("spotify:artist:fromq".to_string(), "FromQ".to_string());
        }
        // Cached (oEmbed + queue metadata) URIs are skipped: no task spawns.
        resolver
            .maybe_lookup(vec![
                "spotify:artist:known".to_string(),
                "spotify:artist:fromq".to_string(),
            ])
            .await;
        assert_eq!(resolver.in_flight.lock().await.len(), 0);
        // Empty input is a no-op.
        resolver.maybe_lookup(vec![]).await;
        assert_eq!(resolver.in_flight.lock().await.len(), 0);
    }
}
