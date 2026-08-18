use anyhow::{anyhow, bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use tokio::io::AsyncBufReadExt;
use tokio::process::Command;
use tokio::sync::{broadcast, mpsc, Mutex, RwLock};
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, error, info, warn};

use crate::state::{now_unix_ms, CreatorRef, PlaybackState, PositionAnchor, QueueMeta};

// ---------------------------------------------------------------------------
// WebSocket event model (mirrors the Spotify Soloist WebSocket API reference)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct Entity {
    #[serde(default)]
    uri: String,
    #[allow(dead_code)] // part of the wire format; kept for future use
    #[serde(default)]
    entity_type: String,
    #[serde(default)]
    decorations: Decorations,
}

#[derive(Debug, Deserialize, Default)]
struct Decorations {
    #[serde(default)]
    identity: Identity,
    #[serde(default)]
    visual_identity: VisualIdentity,
    #[serde(default)]
    parent: Option<Parent>,
    #[serde(default)]
    creators: Vec<Creator>,
    #[serde(default)]
    playback: ItemPlayback,
}

#[derive(Debug, Deserialize, Default)]
struct Identity {
    #[serde(default)]
    name: String,
}

#[derive(Debug, Deserialize, Default)]
struct VisualIdentity {
    #[serde(default)]
    cover: Vec<CoverImage>,
}

#[derive(Debug, Deserialize)]
struct CoverImage {
    url: String,
    #[serde(default)]
    size: String,
}

#[derive(Debug, Deserialize)]
struct Parent {
    #[serde(default)]
    entity: Option<Box<Entity>>,
}

#[derive(Debug, Deserialize)]
struct Creator {
    #[serde(default)]
    entity: Option<Box<Entity>>,
}

#[derive(Debug, Deserialize, Default)]
struct ItemPlayback {
    #[serde(default)]
    duration_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct Position {
    #[serde(default)]
    position_ms: u64,
    #[serde(default)]
    timestamp_ms: i64,
    #[serde(default)]
    speed: f64,
}

#[derive(Debug, Deserialize, Default)]
struct PlaybackOptions {
    #[serde(default)]
    shuffle: bool,
    #[serde(default)]
    repeat: String,
}

#[derive(Debug, Deserialize)]
struct QueueEntry {
    #[allow(dead_code)] // part of the wire format; kept for future use
    #[serde(default)]
    uid: String,
    #[allow(dead_code)]
    #[serde(default)]
    source: String,
    #[serde(default)]
    item: Option<Entity>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)]
enum SoloistEvent {
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

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum SoloistCommand {
    GetAuthState,
    GetState,
    GetQueue { limit: Option<u32> },
    Play { uri: Option<String> },
    Pause,
    SkipNext,
    SkipPrev,
    Seek { position_ms: u64 },
    SetVolume { volume: u8 },
    SetShuffle { enabled: bool },
    SetRepeatContext { enabled: bool },
    SetRepeatTrack { enabled: bool },
    AddToQueue { uri: String },
    Activate,
    Deactivate,
}

impl SoloistCommand {
    fn to_json(&self) -> serde_json::Value {
        let mut v = serde_json::json!({ "type": "command" });
        let obj = v.as_object_mut().unwrap();
        match self {
            Self::GetAuthState => {
                obj.insert("command".into(), "get_auth_state".into());
            }
            Self::GetState => {
                obj.insert("command".into(), "get_state".into());
            }
            Self::GetQueue { limit } => {
                obj.insert("command".into(), "get_queue".into());
                if let Some(l) = limit {
                    obj.insert("limit".into(), (*l).into());
                }
            }
            Self::Play { uri } => {
                obj.insert("command".into(), "play".into());
                if let Some(u) = uri {
                    obj.insert("uri".into(), u.clone().into());
                }
            }
            Self::Pause => {
                obj.insert("command".into(), "pause".into());
            }
            Self::SkipNext => {
                obj.insert("command".into(), "skip_next".into());
            }
            Self::SkipPrev => {
                obj.insert("command".into(), "skip_prev".into());
            }
            Self::Seek { position_ms } => {
                obj.insert("command".into(), "seek".into());
                obj.insert("position_ms".into(), (*position_ms).into());
            }
            Self::SetVolume { volume } => {
                obj.insert("command".into(), "set_volume".into());
                obj.insert("volume".into(), (*volume).into());
            }
            Self::SetShuffle { enabled } => {
                obj.insert("command".into(), "set_shuffle".into());
                obj.insert("enabled".into(), (*enabled).into());
            }
            Self::SetRepeatContext { enabled } => {
                obj.insert("command".into(), "set_repeat_context".into());
                obj.insert("enabled".into(), (*enabled).into());
            }
            Self::SetRepeatTrack { enabled } => {
                obj.insert("command".into(), "set_repeat_track".into());
                obj.insert("enabled".into(), (*enabled).into());
            }
            Self::AddToQueue { uri } => {
                obj.insert("command".into(), "add_to_queue".into());
                obj.insert("uri".into(), uri.clone().into());
            }
            Self::Activate => {
                obj.insert("command".into(), "activate".into());
            }
            Self::Deactivate => {
                obj.insert("command".into(), "deactivate".into());
            }
        }
        v
    }
}

// ---------------------------------------------------------------------------
// Soloist binary download / refresh
// ---------------------------------------------------------------------------

/// Spotify Soloist builds are only valid for 90 days from their build date, so
/// we refresh the binary at least once a week to stay far from the expiry
/// window (the daemon exits with code 10 when a build has expired).
const BINARY_MAX_AGE: Duration = Duration::from_secs(7 * 24 * 60 * 60);

/// Map the compile-time architecture to the soloist CDN archive name.
fn soloist_arch() -> Result<&'static str> {
    match std::env::consts::ARCH {
        "aarch64" => Ok("arm64"),
        "arm" => Ok("arm32"),
        "x86_64" => Ok("x86_64"),
        other => Err(anyhow!(
            "unsupported architecture for soloist download: {}",
            other
        )),
    }
}

/// Official Spotify CDN URL for the current architecture's soloist release.
fn download_url() -> Result<String> {
    Ok(format!(
        "https://soloist-builds.spotifycdn.com/soloist_release_{}.tar.gz",
        soloist_arch()?
    ))
}

/// True when `path` is missing or its mtime is older than `BINARY_MAX_AGE`.
fn binary_needs_refresh(path: &Path) -> bool {
    let modified = match std::fs::metadata(path).and_then(|m| m.modified()) {
        Ok(t) => t,
        // Missing file, or mtime unavailable -> (re)download.
        Err(_) => return true,
    };
    match SystemTime::now().duration_since(modified) {
        // Older than the refresh window -> download a fresh build.
        Ok(age) => age > BINARY_MAX_AGE,
        // Clock went backwards; re-check on the next start instead of trusting
        // a mtime in the future.
        Err(_) => true,
    }
}

/// Download the latest soloist build from Spotify's CDN and atomically place
/// the binary at `dest`. Requires `curl` and `tar`, both of which are present
/// in the add-on runtime image.
async fn download_soloist_binary(dest: &Path) -> Result<()> {
    let url = download_url()?;
    let parent = dest.parent().ok_or_else(|| {
        anyhow!(
            "cannot download soloist binary: {} has no parent directory",
            dest.display()
        )
    })?;
    tokio::fs::create_dir_all(parent).await?;

    // Download + extract into a scratch dir, then atomically rename into place
    // so a failed/interrupted download never leaves a partial binary at dest.
    let tmp_dir = parent.join(format!(".soloist-download-{}", std::process::id()));
    let _ = tokio::fs::remove_dir_all(&tmp_dir).await;
    tokio::fs::create_dir_all(&tmp_dir).await?;
    let tarball = tmp_dir.join("soloist.tar.gz");

    info!("Downloading soloist build from {}", url);
    let curl = Command::new("curl")
        .args([
            "-fsSL",
            "--retry",
            "5",
            "--retry-all-errors",
            "--http1.1",
            "-o",
        ])
        .arg(&tarball)
        .arg(&url)
        .output()
        .await
        .with_context(|| format!("failed to run curl while downloading {}", url))?;
    if !curl.status.success() {
        let stderr = String::from_utf8_lossy(&curl.stderr);
        bail!(
            "curl failed ({}) downloading {}: {}",
            curl.status,
            url,
            stderr.trim()
        );
    }

    let extract = Command::new("tar")
        .args(["xzf"])
        .arg(&tarball)
        .arg("-C")
        .arg(&tmp_dir)
        .output()
        .await
        .with_context(|| format!("failed to run tar while extracting {}", url))?;
    if !extract.status.success() {
        let stderr = String::from_utf8_lossy(&extract.stderr);
        bail!(
            "tar failed ({}) extracting {}: {}",
            extract.status,
            url,
            stderr.trim()
        );
    }

    let extracted = tmp_dir.join("soloist");
    if !extracted.is_file() {
        bail!("archive from {} did not contain a 'soloist' binary", url);
    }

    // Make executable, then replace any existing binary atomically.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        tokio::fs::set_permissions(&extracted, std::fs::Permissions::from_mode(0o755)).await?;
    }

    tokio::fs::rename(&extracted, dest)
        .await
        .with_context(|| format!("failed to move soloist binary into {}", dest.display()))?;
    let _ = tokio::fs::remove_dir_all(&tmp_dir).await;

    info!("Downloaded soloist build to {}", dest.display());
    Ok(())
}

// ---------------------------------------------------------------------------
// Soloist daemon supervisor
// ---------------------------------------------------------------------------

/// Spawns and supervises the soloist daemon process, keeping `soloist_running`
/// in shared state up to date and restarting it with backoff on exit.
pub struct SoloistDaemon {
    bin: PathBuf,
    api_key: String,
    device_name: String,
    data_dir: PathBuf,
    cache_dir: PathBuf,
    initial_volume: Option<u8>,
    state: Arc<RwLock<PlaybackState>>,
    state_tx: broadcast::Sender<()>,
}

/// Stop the soloist daemon gracefully: SIGTERM first,
/// then SIGKILL after a grace period if it does not exit
async fn stop_child_gracefully(child: &mut tokio::process::Child) {
    let Some(pid) = child.id() else {
        // Already reaped; nothing to signal.
        return;
    };
    info!("Sending SIGTERM to soloist daemon (pid {})", pid);
    // SAFETY: pid belongs to the soloist child we spawned; signaling it is
    // valid and does not alias any other process.
    let _ = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
    match tokio::time::timeout(Duration::from_secs(5), child.wait()).await {
        Ok(_) => debug!("Soloist daemon exited cleanly after SIGTERM"),
        Err(_) => {
            warn!("Soloist did not exit within 5s of SIGTERM; sending SIGKILL");
            let _ = child.kill().await;
            let _ = child.wait().await;
        }
    }
}

/// Remove the soloist playback-state restore snapshot written into
/// `<data-dir>/cache/Users/*/context_player_state_restore`. The daemon
/// persists it on pause/state changes but never flushes it on shutdown (not
/// even on SIGTERM), so a restart replays the last *paused* track before the
/// live session state arrives — and indefinitely, if the session stays
/// quiet. The snapshot is a UI-resume convenience, not credentials: soloist
/// re-attaches to its session via auth state and repopulates the file on the
/// next state event, so clearing it just makes the daemon boot into idle and
/// report the live session state. Only this one file is removed; the other
/// `cache/Users/*` stores (e.g. `primary.ldb`) are left alone.
fn remove_restore_state(data_dir: &Path) {
    let Ok(entries) = std::fs::read_dir(data_dir.join("cache/Users")) else {
        return; // No cache dir yet (first run) — nothing to clear.
    };
    for entry in entries.flatten() {
        let restore = entry.path().join("context_player_state_restore");
        if restore.is_file() {
            match std::fs::remove_file(&restore) {
                Ok(()) => info!("Cleared stale soloist restore state: {}", restore.display()),
                Err(e) => warn!("Failed to clear {}: {}", restore.display(), e),
            }
        }
    }
}

impl SoloistDaemon {
    pub fn new(
        config: &crate::config::Config,
        state: Arc<RwLock<PlaybackState>>,
        state_tx: broadcast::Sender<()>,
    ) -> Result<Self> {
        let api_key = config.soloist_api_key.clone().ok_or_else(|| {
            anyhow!(
                "SOLOIST_API_KEY is required to spawn the soloist daemon. Set it in \
                 options.json or the SOLOIST_API_KEY environment variable, or connect \
                 to an external daemon with SOLOIST_WS_URL."
            )
        })?;
        Ok(Self {
            bin: config.soloist_bin.clone(),
            api_key,
            device_name: config.device_name.clone(),
            data_dir: config.soloist_data_dir.clone(),
            cache_dir: config.soloist_cache_dir.clone(),
            initial_volume: config.initial_volume,
            state,
            state_tx,
        })
    }

    pub async fn run(&self) -> Result<()> {
        let mut backoff: u64 = 2;
        // Set when soloist exits with code 10 (expired build) so the next loop
        // iteration re-downloads even if the on-disk binary is younger than the
        // 7-day refresh window.
        let mut force_redownload = false;
        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
        loop {
            // Ensure a fresh soloist binary before spawning: download when the
            // binary is missing (first run), older than 7 days, or after an
            // expired build (exit 10) was observed.
            if force_redownload || binary_needs_refresh(&self.bin) {
                match download_soloist_binary(&self.bin).await {
                    Ok(()) => {
                        force_redownload = false;
                        backoff = 2;
                    }
                    Err(e) => {
                        error!(
                            "Failed to refresh soloist binary ({}): {:#}",
                            self.bin.display(),
                            e
                        );
                        warn!("Retrying in {}s...", backoff);
                        tokio::time::sleep(Duration::from_secs(backoff)).await;
                        backoff = (backoff * 2).min(60);
                        continue;
                    }
                }
            }
            info!("Starting soloist daemon: {}", self.bin.display());
            std::fs::create_dir_all(&self.data_dir)?;
            std::fs::create_dir_all(&self.cache_dir)?;
            // Remove a stale ws.port from a previous daemon run so the ws
            // client polls for this run's fresh port instead of connecting to
            // a dead endpoint advertised by the previous run.
            let _ = std::fs::remove_file(self.data_dir.join("ws.port"));
            // The daemon never flushes its playback-state restore snapshot on
            // shutdown, so clear it now: otherwise every restart replays the
            // last paused track before the live session state arrives.
            remove_restore_state(&self.data_dir);

            let mut cmd = Command::new(&self.bin);
            cmd.arg("--device-name")
                .arg(&self.device_name)
                .arg("--api-key")
                .arg(&self.api_key)
                .arg("--data-dir")
                .arg(&self.data_dir)
                .arg("--cache-dir")
                .arg(&self.cache_dir)
                .arg("--ws")
                .arg("127.0.0.1:0")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());

            if let Some(v) = self.initial_volume {
                cmd.arg("--initial-volume").arg(v.to_string());
            }

            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(e) => {
                    error!(
                        "Failed to spawn soloist daemon ({}): {:#}",
                        self.bin.display(),
                        e
                    );
                    error!(
                        "The binary at {} may be corrupt; a fresh build will be downloaded on the next attempt.",
                        self.bin.display()
                    );
                    force_redownload = true;
                    tokio::time::sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(60);
                    continue;
                }
            };

            // Stream soloist output to our logs
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();
            if let Some(out) = stdout {
                tokio::spawn(async move {
                    let mut lines = tokio::io::BufReader::new(out).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        info!("[soloist] {}", line);
                    }
                });
            }
            if let Some(err) = stderr {
                tokio::spawn(async move {
                    let mut lines = tokio::io::BufReader::new(err).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        warn!("[soloist] {}", line);
                    }
                });
            }

            {
                let mut st = self.state.write().await;
                st.soloist_running = true;
                st.last_error = None;
            }
            let _ = self.state_tx.send(());

            let status = tokio::select! {
                status = child.wait() => {
                    status.context("failed to wait on soloist daemon")?
                }
                _ = sigterm.recv() => {
                    info!("Shutdown signal received, stopping soloist daemon");
                    stop_child_gracefully(&mut child).await;
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Interrupt signal received, stopping soloist daemon");
                    stop_child_gracefully(&mut child).await;
                    return Ok(());
                }
            };
            info!("Soloist daemon exited with status: {}", status);

            let mut st = self.state.write().await;
            st.soloist_running = false;
            if let Some(code) = status.code() {
                st.last_error = match code {
                    10 => {
                        force_redownload = true;
                        Some("soloist build expired (exit 10): downloading a newer build and restarting".into())
                    }
                    1 => Some("soloist exited with code 1 (see soloist logs); check SOLOIST_API_KEY and data directory permissions".into()),
                    other => Some(format!("soloist exited with code {}", other)),
                };
            }
            drop(st);
            let _ = self.state_tx.send(());

            warn!("Restarting soloist daemon in {}s...", backoff);
            tokio::time::sleep(Duration::from_secs(backoff)).await;
            backoff = (backoff * 2).min(60);
        }
    }
}

// ---------------------------------------------------------------------------
// oEmbed artist fallback
// ---------------------------------------------------------------------------

/// Epoch-ms of the most recently parsed soloist event (lock-free; updated in
/// `handle_text`). Lets the watchdog tell "bridge is processing events" from
/// "stuck" without touching the state lock.
static LAST_EVENT_MS: AtomicU64 = AtomicU64::new(0);

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
fn apply_oembed_result(state: &mut PlaybackState, uri: &str, name: &str) -> bool {
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

// ---------------------------------------------------------------------------
// WebSocket client
// ---------------------------------------------------------------------------

/// Runs the WebSocket client: waits for the soloist ws endpoint (ws.port file or
/// explicit URL), connects with backoff, streams events into shared state, and
/// forwards commands from `cmd_rx`.
pub async fn run_client(
    config_ws_url: Option<String>,
    data_dir: PathBuf,
    state: Arc<RwLock<PlaybackState>>,
    state_tx: broadcast::Sender<()>,
    mut cmd_rx: mpsc::Receiver<SoloistCommand>,
    cmd_tx: mpsc::Sender<SoloistCommand>,
) -> Result<()> {
    // Artist-name fallback via oEmbed; created once so its cache survives
    // WebSocket reconnects.
    let resolver = OembedResolver::new(state.clone(), state_tx.clone());

    // Watchdog heartbeat: logs liveness independently of the state lock, so a
    // wedge is distinguishable from a quiet system. The "last soloist event"
    // timestamp is lock-free (updated in handle_text), so it keeps advancing
    // even if the state lock is wedged.
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        loop {
            interval.tick().await;
            let last_event_age =
                now_unix_ms().saturating_sub(LAST_EVENT_MS.load(Ordering::Relaxed) as i64);
            debug!(
                "watchdog: alive; last soloist event {}s ago",
                last_event_age / 1000
            );
        }
    });
    let mut backoff: u64 = 1;
    loop {
        let url = match resolve_ws_url(config_ws_url.clone(), &data_dir).await {
            Ok(url) => url,
            Err(e) => {
                debug!("Waiting for soloist WebSocket endpoint: {}", e);
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
        };

        info!("Connecting to soloist WebSocket: {}", url);
        match tokio_tungstenite::connect_async(&url).await {
            Ok((ws, _)) => {
                backoff = 1;
                if let Err(e) = handle_connection(
                    ws,
                    &state,
                    &state_tx,
                    &mut cmd_rx,
                    cmd_tx.clone(),
                    &resolver,
                )
                .await
                {
                    warn!("Soloist WebSocket connection ended: {}", e);
                }
                {
                    let mut st = state.write().await;
                    st.set_status("idle");
                    st.is_active = false;
                }
                let _ = state_tx.send(());
            }
            Err(e) => {
                warn!("Failed to connect to {}: {}", url, e);
                // If ws.port still advertises the port we just failed on, the
                // daemon is down or the file is stale from a previous run: drop
                // it so the next poll waits for a fresh ws.port instead of
                // retrying a dead endpoint.
                if config_ws_url.is_none() && is_connection_refused(&e) {
                    drop_stale_port_file(&data_dir, &url).await;
                }
                // If we spawned soloist ourselves and it died, log the recorded reason
                let last_error = state.read().await.last_error.clone();
                if let Some(reason) = last_error {
                    error!("Soloist daemon unavailable: {}", reason);
                }
                tokio::time::sleep(Duration::from_secs(backoff)).await;
                backoff = (backoff * 2).min(30);
            }
        }
    }
}

/// Resolve the WebSocket URL: explicit override wins, otherwise read ws.port
/// from the soloist data directory.
async fn resolve_ws_url(
    override_url: Option<String>,
    data_dir: &std::path::Path,
) -> Result<String> {
    if let Some(url) = override_url {
        return Ok(url);
    }
    let port_file = data_dir.join("ws.port");
    let port = tokio::fs::read_to_string(&port_file)
        .await
        .with_context(|| format!("{} not present yet", port_file.display()))?
        .trim()
        .to_string();
    if port.is_empty() || !port.chars().all(|c| c.is_ascii_digit()) {
        return Err(anyhow!("ws.port contains invalid value: {:?}", port));
    }
    Ok(format!("ws://127.0.0.1:{}", port))
}

/// True if the error chain contains an IO "connection refused" error.
fn is_connection_refused(e: &tokio_tungstenite::tungstenite::Error) -> bool {
    let mut source: Option<&dyn std::error::Error> = Some(e);
    while let Some(err) = source {
        if let Some(io) = err.downcast_ref::<std::io::Error>() {
            if io.kind() == std::io::ErrorKind::ConnectionRefused {
                return true;
            }
        }
        source = err.source();
    }
    false
}

/// If `ws.port` still advertises the port from `failed_url` (i.e. the daemon
/// has not republished a fresh port), remove the file so the next poll waits
/// for a new ws.port instead of retrying a dead endpoint.
async fn drop_stale_port_file(data_dir: &std::path::Path, failed_url: &str) {
    let Some(port) = failed_url.rsplit(':').next() else {
        return;
    };
    if port.is_empty() {
        return;
    }
    let port_file = data_dir.join("ws.port");
    let current = tokio::fs::read_to_string(&port_file)
        .await
        .unwrap_or_default();
    if current.trim() == port {
        let _ = tokio::fs::remove_file(&port_file).await;
        debug!(
            "Removed stale {} (no soloist listening on advertised port)",
            port_file.display()
        );
    }
}

async fn handle_connection<S>(
    mut ws: S,
    state: &Arc<RwLock<PlaybackState>>,
    state_tx: &broadcast::Sender<()>,
    cmd_rx: &mut mpsc::Receiver<SoloistCommand>,
    cmd_tx: mpsc::Sender<SoloistCommand>,
    resolver: &OembedResolver,
) -> Result<()>
where
    S: futures_util::Stream<Item = Result<Message, tokio_tungstenite::tungstenite::Error>>
        + futures_util::Sink<Message>
        + Unpin,
    <S as futures_util::Sink<Message>>::Error: std::error::Error + Send + Sync + 'static,
{
    // Track-artist refetch: soloist's first snapshot for a new track often ships
    // empty creator decorations (artist name unknown) — the artist only appears
    // in a later refreshed snapshot (e.g. after pause/resume). The WebSocket API
    // has no metadata command, so we re-request a full snapshot (`get_state`)
    // shortly after a track starts — once soloist has loaded the track metadata —
    // and let the response fill in the artist if available.
    let mut last_uri: Option<String> = None;

    // Bootstrap: soloist rejects get_state/get_queue with "command requires
    // authentication" until it has finished logging in, so only request auth
    // state first and fetch the full playback state + queue once auth_state
    // reports logged_in=true (or after a short fallback timeout in case
    // soloist never pushes an auth_state event).
    let mut bootstrapped = false;
    send_command(&mut ws, &SoloistCommand::GetAuthState).await?;
    let bootstrap_timeout = tokio::time::sleep(Duration::from_secs(5));
    tokio::pin!(bootstrap_timeout);

    loop {
        tokio::select! {
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(cmd) => {
                        if let Err(e) = send_command(&mut ws, &cmd).await {
                            warn!("Failed to send command {:?}: {}", cmd, e);
                            return Err(e);
                        }
                    }
                    None => return Err(anyhow!("command channel closed")),
                }
            }
            msg = ws.next() => {
                match msg {
                    Some(Ok(Message::Text(text))) => {
                        if handle_text(&text, state, state_tx, Some(resolver)).await == Some(true)
                            && !bootstrapped
                        {
                            info!("Soloist authenticated; fetching playback state and queue");
                            send_command(&mut ws, &SoloistCommand::GetState).await?;
                            send_command(&mut ws, &SoloistCommand::GetQueue { limit: Some(10) })
                                .await?;
                            bootstrapped = true;
                        }
                        maybe_refetch_artist(state, &cmd_tx, &mut last_uri).await;
                    }
                    Some(Ok(Message::Ping(p))) => {
                        ws.send(Message::Pong(p)).await?;
                    }
                    Some(Ok(Message::Close(_))) => {
                        return Err(anyhow!("soloist closed the connection"));
                    }
                    Some(Ok(_)) => {}
                    Some(Err(e)) => return Err(anyhow!("websocket error: {}", e)),
                    None => return Err(anyhow!("websocket stream ended")),
                }
            }
            // Precondition, not a body guard: once `bootstrapped` is true the
            // branch is disabled and the fired `Sleep` is never polled again.
            // Otherwise a completed tokio Sleep stays permanently Ready and
            // this select would busy-spin at 100% CPU whenever soloist is
            // idle (both other branches pending).
            _ = &mut bootstrap_timeout, if !bootstrapped => {
                info!("No auth_state received within 5s; fetching playback state anyway");
                send_command(&mut ws, &SoloistCommand::GetState).await?;
                send_command(&mut ws, &SoloistCommand::GetQueue { limit: Some(10) })
                    .await?;
                bootstrapped = true;
            }
        }
    }
}

/// If a new track started without artist info (soloist's first snapshot ships
/// empty creator decorations), schedule a `get_state` re-request so a later,
/// metadata-complete snapshot can fill in the artist. The task self-cancels
/// once the artist appears or the track changes, and gives up after two tries.
async fn maybe_refetch_artist(
    state: &Arc<RwLock<PlaybackState>>,
    cmd_tx: &mpsc::Sender<SoloistCommand>,
    last_uri: &mut Option<String>,
) {
    let (uri, artist_known) = {
        let st = state.read().await;
        (st.media_content_id.clone(), st.artist.is_some())
    };
    if uri == *last_uri {
        return;
    }
    *last_uri = uri.clone();
    if uri.is_none() || artist_known {
        return;
    }

    let state = state.clone();
    let cmd_tx = cmd_tx.clone();
    tokio::spawn(async move {
        for _ in 0..2 {
            tokio::time::sleep(Duration::from_millis(1500)).await;
            let st = state.read().await;
            if st.artist.is_some() || st.media_content_id.as_deref() != uri.as_deref() {
                return;
            }
            drop(st);
            debug!(
                "Artist still missing for {:?}; re-requesting get_state",
                uri
            );
            let _ = cmd_tx.send(SoloistCommand::GetState).await;
        }
    });
}

async fn send_command<S>(ws: &mut S, cmd: &SoloistCommand) -> Result<()>
where
    S: futures_util::Sink<Message> + Unpin,
    <S as futures_util::Sink<Message>>::Error: std::error::Error + Send + Sync + 'static,
{
    let payload = cmd.to_json().to_string();
    debug!("-> {}", payload);
    ws.send(Message::Text(payload.into())).await?;
    Ok(())
}

/// Parse a soloist event and apply it to shared state. Returns
/// `Some(true)` when the event is an auth_state reporting `logged_in=true`
/// (used to trigger the post-login bootstrap in `handle_connection`),
/// `Some(false)` for a logged-out auth_state, and `None` for any other event.
async fn handle_text(
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

    #[test]
    fn command_serialization() {
        assert_eq!(
            SoloistCommand::Pause.to_json().to_string(),
            r#"{"command":"pause","type":"command"}"#
        );
        let v = SoloistCommand::Seek { position_ms: 30000 }.to_json();
        assert_eq!(v["position_ms"], 30000);
        let v = SoloistCommand::Play {
            uri: Some("spotify:track:x".into()),
        }
        .to_json();
        assert_eq!(v["uri"], "spotify:track:x");
    }

    #[test]
    fn binary_needs_refresh_detects_missing_fresh_and_stale_files() {
        let dir = std::env::temp_dir().join(format!("soloist-binary-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("soloist");

        // Missing -> refresh.
        assert!(binary_needs_refresh(&path));

        // Fresh file -> no refresh.
        std::fs::write(&path, b"x").unwrap();
        assert!(!binary_needs_refresh(&path));

        // Backdated past the 7-day window -> refresh.
        let old = SystemTime::now() - Duration::from_secs(8 * 24 * 60 * 60);
        std::fs::File::options()
            .write(true)
            .open(&path)
            .unwrap()
            .set_modified(old)
            .unwrap();
        assert!(binary_needs_refresh(&path));

        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn remove_restore_state_clears_only_the_restore_snapshot() {
        let dir = std::env::temp_dir().join(format!("soloist-restore-test-{}", std::process::id()));
        let users = dir.join("cache/Users/some-user");
        std::fs::create_dir_all(&users).unwrap();
        let restore = users.join("context_player_state_restore");
        std::fs::write(&restore, b"restore state").unwrap();
        let observer = users.join("restrictions_playback_observer.state");
        std::fs::write(&observer, b"observer").unwrap();
        let ldb = users.join("primary.ldb");
        std::fs::write(&ldb, b"ldb").unwrap();

        remove_restore_state(&dir);

        // The restore snapshot is cleared; the other daemon caches are
        // untouched (they may hold device identity).
        assert!(!restore.exists());
        assert!(observer.exists());
        assert!(ldb.exists());
        // A missing cache dir is a no-op (first run).
        remove_restore_state(&dir.join("does-not-exist"));

        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// Regression test for the 100% CPU busy-loop that hit `handle_connection`:
    /// a `tokio::time::Sleep` that has fired stays Ready on every re-poll, so a
    /// `select!` branch polling it must be gated by a precondition
    /// (`.await, if !bootstrapped`). Without the gate, an otherwise idle loop
    /// (all other branches pending) spins at full CPU, only stopping when the
    /// real-time deadline below fires, and racks up tens of thousands of
    /// iterations in the process. With the gate, the loop parks: the fired
    /// timer is polled once, then the loop sleeps on the pending channel until
    /// the deadline fires.
    #[tokio::test(flavor = "current_thread")]
    async fn expired_timer_branch_parks_when_gated() {
        // A timer that has already fired, like the 5s bootstrap timeout in
        // `handle_connection` after it elapses.
        let expired = tokio::time::sleep(Duration::from_millis(1));
        tokio::pin!(expired);
        // A deadline the loop must reach; real time, so even a (hypothetical)
        // busy loop eventually breaks out and trips the iteration assertions.
        let deadline = tokio::time::sleep(Duration::from_millis(50));
        tokio::pin!(deadline);
        let (_tx, mut rx) = mpsc::channel::<u8>(4);

        // The fixed pattern: once the gate flips false the fired timer is no
        // longer polled, so the loop parks on the pending channel until the
        // deadline fires. The buggy pattern (no precondition) instead spins:
        // the fired timer is immediately ready every iteration, so the loop
        // never yields until the deadline interrupts it.
        let mut gated = true;
        let mut iters = 0u64;
        loop {
            tokio::select! {
                _ = rx.recv() => {}
                _ = &mut expired, if gated => gated = false,
                _ = &mut deadline => break,
            }
            iters += 1;
            assert!(
                iters < 1_000_000,
                "select loop busy-spun {} times instead of parking",
                iters
            );
        }
        assert!(
            iters < 10,
            "gated loop should park on the pending channel, not spin ({} iters)",
            iters
        );
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
