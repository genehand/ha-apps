use std::collections::HashSet;

use dashmap::DashMap;

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

use std::sync::Arc;

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
