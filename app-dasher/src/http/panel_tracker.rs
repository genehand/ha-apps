use crate::state::AppState;
use axum::{extract::State, http::StatusCode, response::IntoResponse, Json};
use serde::Deserialize;
use tracing::debug;
#[derive(Debug, Deserialize)]
pub struct PanelRequest {
    tab_id: String,
    url_path: String,
}
pub async fn panel_handler(
    State(state): State<AppState>,
    Json(req): Json<PanelRequest>,
) -> impl IntoResponse {
    let filtering = should_filter(&req.url_path);
    let updated = state
        .client_states
        .set_panel_by_tab_id(&req.tab_id, filtering, &req.url_path);
    if !updated {
        state.panel_updates.insert(
            req.tab_id.clone(),
            crate::state::PanelUpdate {
                filtering_active: filtering,
                timestamp: std::time::Instant::now(),
            },
        );
        debug!(
            "Cached panel update for tab {} (no websocket yet), filtering={}",
            req.tab_id, filtering
        );
    } else {
        debug!(
            "Updated tab {} filtering_active={} for path {}",
            req.tab_id, filtering, req.url_path
        );
    }
    StatusCode::OK
}
fn should_filter(url_path: &str) -> bool {
    url_path.starts_with("/lovelace") || url_path == "/home" || url_path.starts_with("/dashboard")
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_should_filter_dashboard_paths() {
        assert!(should_filter("/lovelace"));
        assert!(should_filter("/lovelace/main"));
        assert!(should_filter("/lovelace/0"));
        assert!(should_filter("/home"));
        assert!(should_filter("/dashboard"));
        assert!(should_filter("/dashboard/my-dash"));
    }
    #[test]
    fn test_should_not_filter_other_paths() {
        assert!(!should_filter("/config"));
        assert!(!should_filter("/config/integrations"));
        assert!(!should_filter("/developer-tools/state"));
        assert!(!should_filter("/history"));
        assert!(!should_filter("/logbook"));
        assert!(!should_filter("/"));
    }
}
