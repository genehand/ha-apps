use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use dashmap::DashMap;
use tracing::debug;

use crate::config::Config;

#[derive(Clone, Debug)]
pub struct DashboardConfig {
    pub lovelace_entities: HashSet<String>,
    pub filter_rules: Vec<serde_json::Value>,
}

#[derive(Clone)]
pub struct ClientState {
    pub client_ip: String,
    pub lovelace_entities: HashSet<String>,
    pub filter_rules: Vec<serde_json::Value>,
    pub lovelace_config_id: Option<u64>,
    pub subscribe_entities_id: Option<u64>,
    pub all_states: Option<serde_json::Value>,
    pub tab_id: Option<String>,
    pub filtering_active: bool,
    pub dashboard_configs: HashMap<String, DashboardConfig>,
    pub pending_configs: HashMap<u64, String>,
    pub current_url_path: Option<String>,
}

impl ClientState {
    pub fn new(client_ip: String) -> Self {
        Self {
            client_ip,
            lovelace_entities: HashSet::new(),
            filter_rules: Vec::new(),
            lovelace_config_id: None,
            subscribe_entities_id: None,
            all_states: None,
            tab_id: None,
            filtering_active: false,
            dashboard_configs: HashMap::new(),
            pending_configs: HashMap::new(),
            current_url_path: None,
        }
    }

    /// Map a panel URL path to the key used for dashboard config caching.
    ///
    /// Browser paths include view suffixes (e.g. `/lovelace/home`), but the
    /// websocket `url_path` is the dashboard identifier (`lovelace`). This
    /// normalizes both to the same key so cached configs can be restored.
    pub fn url_path_to_config_key(url_path: &str) -> String {
        if url_path.is_empty() || url_path == "/" {
            return String::new();
        }
        let normalized = url_path.strip_suffix('/').unwrap_or(url_path);
        let without_leading = normalized.strip_prefix('/').unwrap_or(normalized);

        // The default lovelace dashboard and its views all share the same key
        if without_leading == "lovelace" || without_leading.starts_with("lovelace/") {
            return "lovelace".to_string();
        }

        // For everything else, the first path segment is the dashboard identifier
        without_leading
            .split('/')
            .next()
            .unwrap_or(without_leading)
            .to_string()
    }

    /// Restore entities and filter rules for the given panel url_path.
    pub fn restore_dashboard_config(&mut self, url_path: &str) {
        let key = Self::url_path_to_config_key(url_path);
        if let Some(config) = self.dashboard_configs.get(&key).cloned() {
            let entities_changed = self.lovelace_entities != config.lovelace_entities;
            let rules_changed = self.filter_rules != config.filter_rules;

            if entities_changed || rules_changed {
                self.lovelace_entities = config.lovelace_entities;
                self.filter_rules = config.filter_rules;
                debug!(
                    "Restored {} entities with {} auto-entities rules for {} (panel={})",
                    self.lovelace_entities.len(),
                    self.filter_rules.len(),
                    self.client_ip,
                    url_path
                );
            }
        }
        self.current_url_path = Some(url_path.to_string());
    }

    /// Save current entities and rules under the given url_path key.
    pub fn save_dashboard_config(&mut self, url_path: &str) {
        let key = Self::url_path_to_config_key(url_path);
        self.dashboard_configs.insert(
            key,
            DashboardConfig {
                lovelace_entities: self.lovelace_entities.clone(),
                filter_rules: self.filter_rules.clone(),
            },
        );
    }
}

#[derive(Clone)]
pub struct ClientStates {
    states: Arc<DashMap<String, ClientState>>,
}

impl ClientStates {
    pub fn new() -> Self {
        Self {
            states: Arc::new(DashMap::new()),
        }
    }

    pub fn get_or_insert(
        &self,
        key: String,
        client_ip: String,
    ) -> dashmap::mapref::one::RefMut<'_, String, ClientState> {
        self.states
            .entry(key)
            .or_insert_with(|| ClientState::new(client_ip))
    }

    pub fn remove(&self, key: &str) {
        self.states.remove(key);
    }

    pub fn len(&self) -> usize {
        self.states.len()
    }

    pub fn set_panel_by_tab_id(
        &self,
        tab_id: &str,
        filtering_active: bool,
        url_path: &str,
    ) -> bool {
        for mut entry in self.states.iter_mut() {
            if entry.tab_id.as_deref() == Some(tab_id) {
                entry.filtering_active = filtering_active;
                entry.restore_dashboard_config(url_path);
                return true;
            }
        }
        false
    }
}

#[derive(Clone)]
pub struct PanelUpdate {
    pub filtering_active: bool,
    pub timestamp: Instant,
}

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub client_states: ClientStates,
    pub http_client: reqwest::Client,
    pub panel_updates: Arc<DashMap<String, PanelUpdate>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_state_new() {
        let client = ClientState::new("192.168.1.100".to_string());
        assert_eq!(client.client_ip, "192.168.1.100");
        assert!(client.lovelace_entities.is_empty());
        assert!(client.filter_rules.is_empty());
        assert!(client.lovelace_config_id.is_none());
        assert!(client.subscribe_entities_id.is_none());
        assert!(client.all_states.is_none());
        assert!(client.tab_id.is_none());
        assert!(!client.filtering_active);
    }

    #[test]
    fn test_client_states_new() {
        let states = ClientStates::new();
        assert_eq!(states.len(), 0);
    }

    #[test]
    fn test_client_states_get_or_insert() {
        let states = ClientStates::new();

        // Insert a new client
        {
            let mut state = states.get_or_insert("conn1".to_string(), "192.168.1.100".to_string());
            state.lovelace_entities.insert("light.test".to_string());
        }

        // Verify it was stored
        assert_eq!(states.len(), 1);

        // Retrieve the same client
        {
            let state = states.get_or_insert("conn1".to_string(), "ignored".to_string());
            assert!(state.lovelace_entities.contains("light.test"));
            assert_eq!(state.client_ip, "192.168.1.100");
        }
    }

    #[test]
    fn test_client_states_remove() {
        let states = ClientStates::new();

        // Insert a client
        {
            let _ = states.get_or_insert("conn1".to_string(), "192.168.1.100".to_string());
        }

        assert_eq!(states.len(), 1);

        // Remove it
        states.remove("conn1");

        assert_eq!(states.len(), 0);
    }

    #[test]
    fn test_client_states_multiple_clients() {
        let states = ClientStates::new();

        // Insert multiple clients
        {
            let mut state1 = states.get_or_insert("conn1".to_string(), "192.168.1.100".to_string());
            state1.lovelace_entities.insert("light.kitchen".to_string());
        }

        {
            let mut state2 = states.get_or_insert("conn2".to_string(), "192.168.1.101".to_string());
            state2.lovelace_entities.insert("light.bedroom".to_string());
        }

        assert_eq!(states.len(), 2);

        // Verify each client has its own state
        {
            let state1 = states.get_or_insert("conn1".to_string(), "".to_string());
            assert!(state1.lovelace_entities.contains("light.kitchen"));
            assert!(!state1.lovelace_entities.contains("light.bedroom"));
        }

        {
            let state2 = states.get_or_insert("conn2".to_string(), "".to_string());
            assert!(state2.lovelace_entities.contains("light.bedroom"));
            assert!(!state2.lovelace_entities.contains("light.kitchen"));
        }
    }

    #[test]
    fn test_set_panel_by_tab_id() {
        let states = ClientStates::new();

        {
            let mut state = states.get_or_insert("conn1".to_string(), "192.168.1.100".to_string());
            state.tab_id = Some("tab-abc".to_string());
            state.filtering_active = false;
        }

        assert!(states.set_panel_by_tab_id("tab-abc", true, "/lovelace/main"));

        {
            let state = states.get_or_insert("conn1".to_string(), "".to_string());
            assert!(state.filtering_active);
            assert_eq!(state.current_url_path, Some("/lovelace/main".to_string()));
        }

        assert!(!states.set_panel_by_tab_id("tab-unknown", true, "/lovelace/other"));
    }

    #[test]
    fn test_url_path_to_config_key() {
        assert_eq!(ClientState::url_path_to_config_key(""), "");
        assert_eq!(ClientState::url_path_to_config_key("/"), "");
        assert_eq!(ClientState::url_path_to_config_key("/lovelace"), "lovelace");
        assert_eq!(
            ClientState::url_path_to_config_key("/lovelace/"),
            "lovelace"
        );
        assert_eq!(
            ClientState::url_path_to_config_key("/lovelace/main"),
            "lovelace"
        );
        assert_eq!(
            ClientState::url_path_to_config_key("/lovelace/0"),
            "lovelace"
        );
        assert_eq!(
            ClientState::url_path_to_config_key("/dashboard/my-dash"),
            "dashboard"
        );
        assert_eq!(ClientState::url_path_to_config_key("/home"), "home");
        assert_eq!(
            ClientState::url_path_to_config_key("/dashboard-training"),
            "dashboard-training"
        );
        assert_eq!(
            ClientState::url_path_to_config_key("/dashboard-training/ml2mqtt"),
            "dashboard-training"
        );
    }

    #[test]
    fn test_save_and_restore_dashboard_config() {
        let mut state = ClientState::new("192.168.1.100".to_string());

        // Simulate loading a dashboard config
        state.lovelace_entities.insert("light.kitchen".to_string());
        state
            .filter_rules
            .push(serde_json::json!({"domain": "light"}));
        state.save_dashboard_config("/lovelace/main");

        // Clear current state (simulating navigation away)
        state.lovelace_entities.clear();
        state.filter_rules.clear();

        // Restore
        state.restore_dashboard_config("/lovelace/main");

        assert!(state.lovelace_entities.contains("light.kitchen"));
        assert_eq!(state.filter_rules.len(), 1);
        assert_eq!(state.current_url_path, Some("/lovelace/main".to_string()));
    }

    #[test]
    fn test_restore_dashboard_config_no_match() {
        let mut state = ClientState::new("192.168.1.100".to_string());

        // Set some current entities
        state.lovelace_entities.insert("light.bedroom".to_string());

        // Try to restore a config that was never saved
        state.restore_dashboard_config("/lovelace/unknown");

        // Current entities should remain unchanged
        assert!(state.lovelace_entities.contains("light.bedroom"));
        assert_eq!(
            state.current_url_path,
            Some("/lovelace/unknown".to_string())
        );
    }

    #[test]
    fn test_restore_dashboard_config_default_lovelace() {
        let mut state = ClientState::new("192.168.1.100".to_string());

        state
            .lovelace_entities
            .insert("switch.living_room".to_string());
        state.save_dashboard_config("lovelace");

        state.lovelace_entities.clear();
        state.restore_dashboard_config("/lovelace");

        assert!(state.lovelace_entities.contains("switch.living_room"));
    }

    #[test]
    fn test_dashboard_configs_multiple_panels() {
        let mut state = ClientState::new("192.168.1.100".to_string());

        // Save config for main dashboard
        state.lovelace_entities = ["light.kitchen".to_string()].into_iter().collect();
        state.filter_rules = vec![];
        state.save_dashboard_config("/lovelace/main");

        // Save config for another dashboard
        state.lovelace_entities = ["sensor.temp".to_string()].into_iter().collect();
        state.filter_rules = vec![serde_json::json!({"domain": "sensor"})];
        state.save_dashboard_config("/dashboard/weather");

        // Restore main
        state.restore_dashboard_config("/lovelace/main");
        assert!(state.lovelace_entities.contains("light.kitchen"));
        assert!(!state.lovelace_entities.contains("sensor.temp"));
        assert!(state.filter_rules.is_empty());

        // Restore weather
        state.restore_dashboard_config("/dashboard/weather");
        assert!(!state.lovelace_entities.contains("light.kitchen"));
        assert!(state.lovelace_entities.contains("sensor.temp"));
        assert_eq!(state.filter_rules.len(), 1);
    }
}
