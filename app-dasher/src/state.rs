use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};

use dashmap::DashMap;
use tracing::debug;

use crate::config::Config;

/// How long a tab's persistent state is kept after its last connection ends,
/// so a reconnect (e.g. after the 5-minute background-suspend) can resume
/// filtering. The frontend re-fetches `lovelace/config` on every reconnect,
/// so any stale entity cache self-corrects within a second of reconnecting;
/// a generous TTL only costs a few KB per tab. Old entries are pruned
/// opportunistically on new connections.
pub const TAB_STATE_TTL: Duration = Duration::from_secs(24 * 3600);

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

    pub fn get(&self, key: &str) -> Option<dashmap::mapref::one::Ref<'_, String, ClientState>> {
        self.states.get(key)
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

/// Persistent per-tab state that survives connection teardown. Written when a
/// connection closes and restored when the same tab reconnects, so filtering
/// resumes immediately without waiting for a fresh panel report.
#[derive(Clone)]
pub struct TabState {
    pub filtering_active: bool,
    pub current_url_path: Option<String>,
    pub dashboard_configs: HashMap<String, DashboardConfig>,
    pub last_seen: Instant,
}

#[derive(Clone)]
pub struct TabStates {
    states: Arc<DashMap<String, TabState>>,
}

impl TabStates {
    pub fn new() -> Self {
        Self {
            states: Arc::new(DashMap::new()),
        }
    }

    /// Snapshot a connection's per-tab state for later restoration.
    pub fn save(&self, tab_id: &str, state: &ClientState) {
        self.states.insert(
            tab_id.to_string(),
            TabState {
                filtering_active: state.filtering_active,
                current_url_path: state.current_url_path.clone(),
                dashboard_configs: state.dashboard_configs.clone(),
                last_seen: Instant::now(),
            },
        );
    }

    pub fn get(&self, tab_id: &str) -> Option<TabState> {
        self.states.get(tab_id).map(|entry| entry.clone())
    }

    /// Apply saved state to a freshly connected client state. A fresh panel
    /// update (from `POST /dasher/panel`) takes precedence for filtering;
    /// the dashboard entity cache is always restored.
    pub fn restore_into(
        &self,
        tab_id: &str,
        client_state: &mut ClientState,
        panel_update_applied: bool,
    ) {
        let Some(tab) = self.get(tab_id) else {
            return;
        };
        if !panel_update_applied {
            client_state.filtering_active = tab.filtering_active;
        }
        client_state.dashboard_configs = tab.dashboard_configs.clone();
        if let Some(path) = tab.current_url_path.as_deref() {
            client_state.restore_dashboard_config(path);
        }
    }

    /// Drop entries whose last connection ended longer ago than `ttl`.
    pub fn prune(&self, now: Instant, ttl: Duration) {
        self.states
            .retain(|_, v| now.duration_since(v.last_seen) < ttl);
    }

    #[cfg(test)]
    fn insert(&self, tab_id: &str, state: TabState) {
        self.states.insert(tab_id.to_string(), state);
    }
}

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub client_states: ClientStates,
    pub http_client: reqwest::Client,
    pub panel_updates: Arc<DashMap<String, PanelUpdate>>,
    pub tab_states: TabStates,
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

    #[test]
    fn test_tab_states_save_and_get() {
        let states = TabStates::new();
        let mut client = ClientState::new("192.168.1.100".to_string());
        client.filtering_active = true;
        client.current_url_path = Some("/lovelace/main".to_string());
        client.lovelace_entities.insert("light.kitchen".to_string());
        client.save_dashboard_config("/lovelace/main");

        states.save("tab-abc", &client);

        let tab = states.get("tab-abc").expect("tab state saved");
        assert!(tab.filtering_active);
        assert_eq!(tab.current_url_path.as_deref(), Some("/lovelace/main"));
        assert!(tab
            .dashboard_configs
            .get("lovelace")
            .unwrap()
            .lovelace_entities
            .contains("light.kitchen"));
        assert!(states.get("tab-unknown").is_none());
    }

    #[test]
    fn test_tab_states_restore_into_restores_filtering_and_entities() {
        let states = TabStates::new();
        let mut saved = ClientState::new("192.168.1.100".to_string());
        saved.filtering_active = true;
        saved.current_url_path = Some("/lovelace/main".to_string());
        saved.lovelace_entities.insert("light.kitchen".to_string());
        saved.save_dashboard_config("/lovelace/main");
        states.save("tab-abc", &saved);

        // Fresh connection state, as created on reconnect after suspend
        let mut fresh = ClientState::new("192.168.1.100".to_string());
        states.restore_into("tab-abc", &mut fresh, false);

        assert!(fresh.filtering_active);
        assert!(fresh.lovelace_entities.contains("light.kitchen"));
        assert_eq!(fresh.current_url_path.as_deref(), Some("/lovelace/main"));
        assert!(fresh.dashboard_configs.contains_key("lovelace"));
    }

    #[test]
    fn test_tab_states_restore_into_panel_update_wins_for_filtering() {
        let states = TabStates::new();
        let mut saved = ClientState::new("192.168.1.100".to_string());
        saved.filtering_active = true;
        saved.current_url_path = Some("/lovelace/main".to_string());
        saved.lovelace_entities.insert("light.kitchen".to_string());
        saved.save_dashboard_config("/lovelace/main");
        states.save("tab-abc", &saved);

        // Fresh state where a panel update already set filtering=false
        let mut fresh = ClientState::new("192.168.1.100".to_string());
        fresh.filtering_active = false;
        states.restore_into("tab-abc", &mut fresh, true);

        // Filtering stays as the panel update set it, but the entity cache
        // and dashboard configs are still restored
        assert!(!fresh.filtering_active);
        assert!(fresh.lovelace_entities.contains("light.kitchen"));
        assert!(fresh.dashboard_configs.contains_key("lovelace"));
    }

    #[test]
    fn test_tab_states_restore_into_no_entry() {
        let states = TabStates::new();
        let mut fresh = ClientState::new("192.168.1.100".to_string());
        fresh.filtering_active = true;
        fresh.lovelace_entities.insert("light.bedroom".to_string());

        states.restore_into("tab-nope", &mut fresh, false);

        assert!(fresh.filtering_active);
        assert!(fresh.lovelace_entities.contains("light.bedroom"));
    }

    #[test]
    fn test_tab_states_prune() {
        let states = TabStates::new();
        let now = Instant::now();
        states.insert(
            "tab-old",
            TabState {
                filtering_active: true,
                current_url_path: None,
                dashboard_configs: HashMap::new(),
                last_seen: now - Duration::from_secs(48 * 3600),
            },
        );
        states.insert(
            "tab-fresh",
            TabState {
                filtering_active: false,
                current_url_path: None,
                dashboard_configs: HashMap::new(),
                last_seen: now - Duration::from_secs(2 * 3600),
            },
        );

        states.prune(now, TAB_STATE_TTL);

        assert!(states.get("tab-old").is_none());
        assert!(states.get("tab-fresh").is_some());
    }
}
