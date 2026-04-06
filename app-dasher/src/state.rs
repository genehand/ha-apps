use std::collections::HashSet;
use std::sync::Arc;

use dashmap::DashMap;

use crate::config::Config;

#[derive(Clone)]
pub struct ClientState {
    pub client_ip: String,
    pub lovelace_entities: HashSet<String>,
    pub filter_rules: Vec<serde_json::Value>,
    pub lovelace_config_id: Option<u64>,
    pub subscribe_entities_id: Option<u64>,
    pub all_states: Option<serde_json::Value>,
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
        }
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
}

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub client_states: ClientStates,
    pub http_client: reqwest::Client,
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
}
