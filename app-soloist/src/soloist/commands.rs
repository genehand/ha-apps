//! Commands the bridge sends to the soloist daemon over the WebSocket.
//! Serialization mirrors the Soloist WebSocket API reference; the wire-format
//! model for daemon messages lives in [`super::events`].

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
    pub(crate) fn to_json(&self) -> serde_json::Value {
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
