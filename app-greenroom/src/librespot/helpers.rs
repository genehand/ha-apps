use librespot_core::config::DeviceType;
use librespot_core::dealer::protocol::Message;
use librespot_core::session::Session;
use librespot_core::version;
use librespot_protocol::connect::{
    Capabilities, Device, DeviceInfo, MemberType, PutStateReason, PutStateRequest,
};
use librespot_protocol::media::AudioQuality;
use librespot_protocol::player::{ContextPlayerOptions, PlayOrigin, PlayerState, Suppressions};
use protobuf::{EnumOrUnknown, MessageField};
use tracing::warn;

/// Max backoff for reconnection attempts (5 minutes)
const MAX_BACKOFF_SECS: u64 = 300;

/// Calculate exponential backoff delay for reconnection attempts.
/// Returns delay in seconds: 5, 5, 10, 20, 40, 80, 160, capped at 300.
pub fn calculate_backoff(consecutive_errors: u32) -> u64 {
    // First two errors use 5 second delay, then exponential
    let power: u32 = consecutive_errors.saturating_sub(2);
    let multiplier = 2u64.checked_pow(power).unwrap_or(u64::MAX);
    std::cmp::min(5u64.saturating_mul(multiplier), MAX_BACKOFF_SECS)
}

/// Extract connection ID from WebSocket message headers.
pub fn extract_connection_id(msg: Message) -> Result<String, librespot_core::Error> {
    let connection_id = msg.headers.get("Spotify-Connection-Id").ok_or_else(|| {
        librespot_core::Error::invalid_argument("Missing Spotify-Connection-Id header")
    })?;
    Ok(connection_id.to_owned())
}

/// This is called if a user tries to select Greenroom as the active playback device.
pub fn log_player_command(_msg: Message) -> Result<(), librespot_core::Error> {
    warn!("Greenroom is a monitor-only device - please select another Spotify device for playback");
    Ok(())
}

/// Send a lightweight keepalive to maintain WebSocket session.
/// This generates outbound traffic to prevent server-side idle timeout (~60s).
pub async fn send_keepalive(session: &Session, device_name: &str) -> anyhow::Result<()> {
    let keepalive_request =
        create_join_cluster_request(session, device_name, PutStateReason::NEW_CONNECTION);
    session
        .spclient()
        .put_connect_state_request(&keepalive_request)
        .await
        .map_err(|e| anyhow::anyhow!("Keepalive failed: {}", e))?;
    Ok(())
}

/// Close the WebSocket connection to remove device from Spotify's device list.
/// Called before intentional disconnections to properly clean up.
pub async fn close_websocket(session: &Session) {
    session.dealer().close().await;
}

/// Create a PutStateRequest for joining the Spotify Connect cluster.
pub fn create_join_cluster_request(
    session: &Session,
    device_name: &str,
    reason: PutStateReason,
) -> PutStateRequest {
    let device_info = DeviceInfo {
        can_play: true,
        volume: 32767,
        name: device_name.to_string(),
        device_id: session.device_id().to_string(),
        device_type: EnumOrUnknown::new(DeviceType::Speaker.into()),
        device_software_version: version::SEMVER.to_string(),
        spirc_version: version::SPOTIFY_SPIRC_VERSION.to_string(),
        client_id: session.client_id(),
        is_group: false,
        capabilities: MessageField::some(Capabilities {
            volume_steps: 64,
            disable_volume: false,
            gaia_eq_connect_id: true,
            can_be_player: false,
            needs_full_player_state: true,
            is_observable: true,
            is_controllable: false, // Monitor-only: we don't accept control commands
            hidden: false,
            supports_gzip_pushes: true,
            supports_logout: false,
            supported_types: vec![],
            supports_playlist_v2: true,
            supports_transfer_command: false,
            supports_command_request: false,
            supports_set_options_command: false,
            is_voice_enabled: false,
            restrict_to_local: false,
            connect_disabled: false,
            supports_rename: false,
            supports_external_episodes: false,
            supports_set_backend_metadata: false,
            supports_dj: false,
            supports_rooms: false,
            supported_audio_quality: EnumOrUnknown::new(AudioQuality::VERY_HIGH),
            command_acks: false,
            ..Default::default()
        }),
        ..Default::default()
    };

    PutStateRequest {
        member_type: EnumOrUnknown::new(MemberType::CONNECT_STATE),
        put_state_reason: EnumOrUnknown::new(reason),
        device: MessageField::some(Device {
            device_info: MessageField::some(device_info),
            player_state: MessageField::some(PlayerState {
                session_id: session.session_id(),
                is_system_initiated: true,
                playback_speed: 1.0,
                play_origin: MessageField::some(PlayOrigin::new()),
                suppressions: MessageField::some(Suppressions::new()),
                options: MessageField::some(ContextPlayerOptions::new()),
                ..Default::default()
            }),
            ..Default::default()
        }),
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_calculate_backoff() {
        // First error: 5 seconds
        assert_eq!(calculate_backoff(1), 5);
        // Second error: 5 seconds
        assert_eq!(calculate_backoff(2), 5);
        // Third error: 10 seconds
        assert_eq!(calculate_backoff(3), 10);
        // Fourth error: 20 seconds
        assert_eq!(calculate_backoff(4), 20);
        // Fifth error: 40 seconds
        assert_eq!(calculate_backoff(5), 40);
        // Sixth error: 80 seconds
        assert_eq!(calculate_backoff(6), 80);
        // Seventh error: 160 seconds
        assert_eq!(calculate_backoff(7), 160);
        // Eighth error: capped at 300 seconds
        assert_eq!(calculate_backoff(8), 300);
        // Many errors: still capped
        assert_eq!(calculate_backoff(100), 300);
    }
}
