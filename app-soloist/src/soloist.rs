//! Gateway to the soloist daemon: the WebSocket client, its event wire model,
//! command serialization, artist-name fallback, and daemon supervision.
//!
//! - [`events`] — wire-format model for the Soloist WebSocket API
//! - [`commands`] — `SoloistCommand`, the messages the bridge sends
//! - [`client`] — the WebSocket client lifecycle: connects with backoff,
//!   forwards commands, drives [`apply`] on incoming messages
//! - [`apply`] — parses soloist events and applies them to
//!   [`crate::state::PlaybackState`]
//! - [`oembed`] — last-resort artist-name resolution via Spotify's public
//!   oEmbed API
//! - [`daemon`] — soloist binary download/refresh and the daemon supervisor
//!
//! [`run_client`], [`SoloistCommand`], and [`SoloistDaemon`] are the public
//! surface consumed by `main.rs` and `mqtt.rs`.

mod apply;
mod client;
mod commands;
mod daemon;
mod events;
mod oembed;

pub use client::run_client;
pub use commands::SoloistCommand;
pub use daemon::SoloistDaemon;
