//! Soloist binary download/refresh and the daemon supervisor. The bridge
//! downloads the official soloist build at startup (the binary is never
//! vendored — Spotify forbids redistribution) and supervises the daemon
//! process, restarting it with backoff on exit.

use anyhow::{anyhow, bail, Context, Result};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use tokio::io::AsyncBufReadExt;
use tokio::process::Command;
use tokio::sync::{broadcast, RwLock};
use tracing::{debug, error, info, warn};

use crate::state::PlaybackState;

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
