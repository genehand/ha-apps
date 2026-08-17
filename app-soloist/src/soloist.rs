use anyhow::{anyhow, bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use tokio::io::AsyncBufReadExt;
use tokio::process::Command;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, error, info, warn};

use crate::state::{now_unix_ms, PlaybackState, PositionAnchor};

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
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    return Ok(());
                }
                _ = sigint.recv() => {
                    info!("Interrupt signal received, stopping soloist daemon");
                    let _ = child.kill().await;
                    let _ = child.wait().await;
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
                if let Err(e) =
                    handle_connection(ws, &state, &state_tx, &mut cmd_rx, cmd_tx.clone()).await
                {
                    warn!("Soloist WebSocket connection ended: {}", e);
                }
                {
                    let mut st = state.write().await;
                    st.status = "idle".to_string();
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
                        if handle_text(&text, state, state_tx).await == Some(true)
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
            _ = &mut bootstrap_timeout => {
                if !bootstrapped {
                    info!("No auth_state received within 5s; fetching playback state anyway");
                    send_command(&mut ws, &SoloistCommand::GetState).await?;
                    send_command(&mut ws, &SoloistCommand::GetQueue { limit: Some(10) })
                        .await?;
                    bootstrapped = true;
                }
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
) -> Option<bool> {
    let event: SoloistEvent = match serde_json::from_str(text) {
        Ok(e) => e,
        Err(e) => {
            debug!("Unparseable soloist message ({}): {}", e, text);
            return None;
        }
    };
    debug!("<- {}", text);

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
            let mut st = state.write().await;
            st.status = status.clone();
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
            info!("playback_state: status={} track={:?}", status, st.track);
        }
        SoloistEvent::TrackChanged { item } => {
            let mut st = state.write().await;
            apply_entity(&mut st, Some(&item), true);
            info!(
                "track_changed: {} - {}",
                st.track.as_deref().unwrap_or("?"),
                st.artist.as_deref().unwrap_or("?")
            );
        }
        SoloistEvent::PlaybackChanged { status } => {
            state.write().await.status = status.clone();
            debug!("playback_changed: {}", status);
        }
        SoloistEvent::VolumeChanged { volume } => {
            state.write().await.volume = volume.min(100);
            debug!("volume_changed: {}", volume);
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
            debug!("device_changed: is_active={}", is_active);
        }
        SoloistEvent::ContextChanged { context } => {
            let mut st = state.write().await;
            apply_context(&mut st, Some(&context));
            debug!("context_changed: {:?}", st.source);
        }
        SoloistEvent::OptionsChanged { options } => {
            let mut st = state.write().await;
            apply_options(&mut st, options);
            debug!(
                "options_changed: shuffle={} repeat={}",
                st.shuffle, st.repeat
            );
        }
        SoloistEvent::PositionSync { position } => {
            let mut st = state.write().await;
            apply_position(&mut st, position);
            debug!(
                "position_sync: {}ms speed={}",
                st.position_anchor.position_ms, st.position_anchor.speed
            );
        }
        SoloistEvent::QueueChanged {
            previous: _,
            upcoming,
        } => {
            let entries = upcoming
                .iter()
                .filter_map(|e| e.item.as_ref())
                .map(short_label)
                .collect::<Vec<_>>();
            state.write().await.upcoming = entries;
            debug!("queue_changed: {} upcoming", upcoming.len());
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
        st.artist = creators_label(e);
    }
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

/// All creator names joined with ", " (e.g. "Artist A, Artist B"), skipping
/// entities without a display name. None when there are no named creators.
fn creators_label(e: &Entity) -> Option<String> {
    let names: Vec<&str> = e
        .decorations
        .creators
        .iter()
        .filter_map(|c| c.entity.as_ref())
        .map(|ce| ce.decorations.identity.name.as_str())
        .filter(|n| !n.is_empty())
        .collect();
    if names.is_empty() {
        None
    } else {
        Some(names.join(", "))
    }
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
        handle_text(json, state, tx).await;
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
    async fn joins_multiple_creators_with_commas() {
        let state = Arc::new(RwLock::new(PlaybackState::default()));
        let (tx, _) = broadcast::channel(4);
        feed(
            &state,
            &tx,
            "{\"type\":\"playback_state\",\"status\":\"playing\",\"item\":{\"uri\":\"spotify:track:abc\",\"entity_type\":\"track\",\"decorations\":{\"identity\":{\"name\":\"My Song\"},\"creators\":[{\"entity\":{\"uri\":\"spotify:artist:1\",\"decorations\":{\"identity\":{\"name\":\"Artist One\"}}}},{\"entity\":{\"uri\":\"spotify:artist:2\",\"decorations\":{\"identity\":{\"name\":\"Artist Two\"}}}}]}}}",
        )
        .await;
        let st = state.read().await;
        assert_eq!(st.artist.as_deref(), Some("Artist One, Artist Two"));
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
}
