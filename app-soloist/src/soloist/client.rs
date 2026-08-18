//! WebSocket client: connects to the soloist daemon (with backoff), drives
//! [`handle_text`] on incoming messages, and forwards commands from the MQTT
//! bridge. Event parsing and state application live in [`super::apply`]; the
//! wire model, command serialization, and the artist-name fallback live in
//! [`super::events`], [`super::commands`], and [`super::oembed`].

use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, error, info, warn};

use crate::soloist::apply::{handle_text, LAST_EVENT_MS};
use crate::soloist::commands::SoloistCommand;
use crate::soloist::oembed::OembedResolver;
use crate::state::{now_unix_ms, PlaybackState};

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
