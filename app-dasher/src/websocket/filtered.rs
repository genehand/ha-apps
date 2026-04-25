use axum::{
    body::Body,
    extract::{ConnectInfo, Request, State},
    response::Response,
};
use futures::{sink::SinkExt, stream::StreamExt, Sink, Stream};
use serde_json::Value;
use tracing::{debug, error, info, warn};
use yawc::{
    close::CloseCode, frame::Frame, frame::OpCode, CompressionLevel, IncomingUpgrade, Options,
    WebSocket,
};

use crate::state::{AppState, ClientStates};
use crate::utils;
use crate::websocket::entities::{parse_lovelace_entities, resolve_rules_and_update_entities};

/// Process and forward frames from client to HA, tracking subscription IDs
async fn forward_client_to_ha<S, D>(
    mut source: S,
    mut destination: D,
    conn_id: &str,
    client_states: &ClientStates,
    client_ip: &str,
) where
    S: Stream<Item = Frame> + Unpin,
    D: Sink<Frame> + Unpin,
    D::Error: std::fmt::Display,
{
    while let Some(frame) = source.next().await {
        match frame.opcode() {
            OpCode::Text => {
                let text = frame.as_str();
                // Process client message to track subscription IDs
                if let Ok(data) = serde_json::from_str::<Value>(text) {
                    process_client_message(data, conn_id, client_states, client_ip).await;
                }

                if let Err(e) = destination.send(frame).await {
                    error!("Error forwarding client to HA: {}", e);
                    break;
                }
            }
            OpCode::Binary => {
                if let Err(e) = destination.send(frame).await {
                    error!("Error forwarding client to HA: {}", e);
                    break;
                }
            }
            OpCode::Close => {
                let _ = destination.send(Frame::close(CloseCode::Normal, "")).await;
                break;
            }
            _ => {}
        }
    }
}

/// Process and filter frames from HA to client based on entity subscriptions
async fn forward_ha_to_client<S, D>(
    mut source: S,
    mut destination: D,
    conn_id: &str,
    client_states: &ClientStates,
    client_ip: &str,
) where
    S: Stream<Item = Frame> + Unpin,
    D: Sink<Frame> + Unpin,
    D::Error: std::fmt::Display,
{
    while let Some(frame) = source.next().await {
        match frame.opcode() {
            OpCode::Text => {
                let text = frame.as_str();
                match serde_json::from_str::<Value>(text) {
                    Ok(Value::Array(messages)) => {
                        // Handle batched JSON array messages
                        let mut responses = Vec::new();
                        for data in messages {
                            if let Some(processed) =
                                process_server_message(data, conn_id, client_states, client_ip)
                                    .await
                            {
                                responses.push(processed);
                            }
                        }
                        if !responses.is_empty() {
                            let response_text =
                                serde_json::to_string(&responses).unwrap_or_default();
                            let response_frame = Frame::text(response_text);
                            if let Err(e) = destination.send(response_frame).await {
                                warn!("Error sending to client: {}", e);
                                break;
                            }
                        }
                    }
                    Ok(data) => {
                        // Single message
                        let processed =
                            process_server_message(data, conn_id, client_states, client_ip).await;

                        if let Some(response) = processed {
                            let response_frame = Frame::text(response.to_string());
                            if let Err(e) = destination.send(response_frame).await {
                                warn!("Error sending to client: {}", e);
                                break;
                            }
                        }
                    }
                    Err(_) => {
                        // Pass through non-JSON messages
                        if let Err(e) = destination.send(frame).await {
                            warn!("Error sending to client: {}", e);
                            break;
                        }
                    }
                }
            }
            OpCode::Binary => {
                if let Err(e) = destination.send(frame).await {
                    warn!("Error sending to client: {}", e);
                    break;
                }
            }
            OpCode::Close => {
                let _ = destination.send(Frame::close(CloseCode::Normal, "")).await;
                break;
            }
            _ => {}
        }
    }
}

pub async fn handler(
    upgrade: IncomingUpgrade,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let client_ip = utils::get_client_ip(&req, addr);
    let path = req.uri().path();

    info!(
        "Filtered WebSocket connection for {} from {}",
        path, client_ip
    );

    // Extract tab_id from query string for per-tab filtering
    let tab_id = req.uri().query().and_then(|q| {
        q.split('&')
            .find(|p| p.starts_with("dasher_tab="))
            .map(|p| &p["dasher_tab=".len()..])
            .map(|v| v.to_string())
    });

    // Check if client requested compression via Sec-WebSocket-Extensions header
    let client_requested_compression = req
        .headers()
        .get("sec-websocket-extensions")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.contains("permessage-deflate"))
        .unwrap_or(false);

    let options = if client_requested_compression {
        Options::default().with_compression_level(CompressionLevel::default())
    } else {
        Options::default()
    };

    let (response, ws_future) = upgrade.upgrade(options).unwrap();

    // Check what compression was actually negotiated in the response
    let compression_negotiated = utils::is_compression_negotiated(response.headers());

    // Convert response body type
    let response = response.map(|_| Body::empty());

    tokio::spawn(async move {
        match ws_future.await {
            Ok(socket) => {
                handle(socket, state, client_ip, compression_negotiated, tab_id).await;
            }
            Err(e) => {
                error!("WebSocket upgrade failed for {}: {}", client_ip, e);
            }
        }
    });

    response
}

pub async fn handle(
    client_socket: yawc::HttpWebSocket,
    state: crate::AppState,
    client_ip: String,
    use_compression: bool,
    tab_id: Option<String>,
) {
    let config = state.config;
    let client_states = state.client_states;
    let panel_updates = state.panel_updates;
    let conn_id = format!("{:p}", &client_socket);

    // Create client state
    {
        let mut client_state = client_states.get_or_insert(conn_id.clone(), client_ip.clone());
        client_state.client_ip = client_ip.clone();
        client_state.tab_id = tab_id.clone();

        // Check for cached panel update from before websocket connected
        if let Some(ref tab_id_str) = tab_id {
            let now = std::time::Instant::now();
            panel_updates.retain(|_, v| {
                now.duration_since(v.timestamp) < std::time::Duration::from_secs(30)
            });

            if let Some((_, update)) = panel_updates.remove(tab_id_str) {
                client_state.filtering_active = update.filtering_active;
                debug!(
                    "Applied cached panel update for tab {}: filtering={}",
                    tab_id_str, update.filtering_active
                );
            }
        }
    }

    let ha_url_str = format!("ws://{}/api/websocket", config.ha_host);

    // Parse URL
    let url = match ha_url_str.parse() {
        Ok(url) => url,
        Err(e) => {
            error!("Failed to parse HA URL: {}", e);
            return;
        }
    };

    // Build custom HTTP request with Host header
    let mut request_builder = yawc::HttpRequest::builder().method("GET").uri(&ha_url_str);

    // Set Host header from HA URL
    if let Some(host) = config.ha_host.split(':').next() {
        request_builder = request_builder.header(http::header::HOST, host);
    }

    // Connect to Home Assistant with compression only if client requested it
    let options = if use_compression {
        Options::default().with_compression_level(CompressionLevel::default())
    } else {
        Options::default()
    };

    let ha_socket = match WebSocket::connect(url)
        .with_request(request_builder)
        .with_options(options)
        .await
    {
        Ok(conn) => conn,
        Err(e) => {
            error!("Failed to connect to Home Assistant WebSocket: {}", e);
            return;
        }
    };

    debug!(
        "Filtered WebSocket proxy established to {} from {} (conn_id={}, total_clients={})",
        ha_url_str,
        client_ip,
        conn_id,
        client_states.len()
    );

    // Split sockets into sink and stream
    let (client_sink, client_stream) = client_socket.split();
    let (ha_sink, ha_stream) = ha_socket.split();

    // Client to HA task
    let conn_id_clone = conn_id.clone();
    let client_states_clone = client_states.clone();
    let client_ip_clone = client_ip.clone();
    let c2h_handle = tokio::spawn(async move {
        forward_client_to_ha(
            client_stream,
            ha_sink,
            &conn_id_clone,
            &client_states_clone,
            &client_ip_clone,
        )
        .await;
    });

    // HA to Client task
    let conn_id_clone2 = conn_id.clone();
    let client_states_clone2 = client_states.clone();
    let client_ip_clone = client_ip.clone();
    let h2c_handle = tokio::spawn(async move {
        forward_ha_to_client(
            ha_stream,
            client_sink,
            &conn_id_clone2,
            &client_states_clone2,
            &client_ip_clone,
        )
        .await;
    });

    // Wait for either task to complete
    tokio::select! {
        _ = c2h_handle => {},
        _ = h2c_handle => {},
    }

    // Cleanup
    client_states.remove(&conn_id);
    info!("WebSocket connection closed for {}", client_ip);
}

async fn process_client_message(
    data: Value,
    conn_id: &str,
    client_states: &ClientStates,
    client_ip: &str,
) {
    if let Some(msg_type) = data.get("type").and_then(|v| v.as_str()) {
        if msg_type == "lovelace/config" {
            // Only track lovelace/config if url_path is a non-null string
            if data.get("url_path").is_some_and(|v| !v.is_null()) {
                if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
                    let url_path = data.get("url_path").and_then(|v| v.as_str()).unwrap_or("");
                    let mut state =
                        client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
                    state.lovelace_config_id = Some(id);
                    state.pending_configs.insert(id, url_path.to_string());
                    debug!("lovelace/config request with ID: {} for {}", id, client_ip);
                }
            }
        } else if msg_type == "subscribe_entities" {
            if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
                let mut state =
                    client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
                state.subscribe_entities_id = Some(id);
                debug!(
                    "subscribe_entities request with ID: {} for {}",
                    id, client_ip
                );
            }
        }
    }
}

async fn process_server_message(
    data: Value,
    conn_id: &str,
    client_states: &ClientStates,
    client_ip: &str,
) -> Option<Value> {
    // Get client state
    let mut client_state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());

    let lovelace_config_id = client_state.lovelace_config_id;
    let subscribe_entities_id = client_state.subscribe_entities_id;

    // Handle lovelace/config response
    if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
        if Some(id) == lovelace_config_id {
            let msg_type = data.get("type").and_then(|v| v.as_str());
            let success = data.get("success").and_then(|v| v.as_bool());

            if msg_type == Some("result") && success == Some(true) {
                if let Some(result) = data.get("result") {
                    // Parse entities and rules
                    let mut entities = std::collections::HashSet::new();
                    let mut rules = Vec::new();
                    parse_lovelace_entities(result, &mut entities, &mut rules);

                    client_state.lovelace_entities = entities.clone();
                    client_state.filter_rules = rules.clone();

                    if !rules.is_empty() {
                        debug!("Auto-entities rules: {:?}", rules);
                        if let Some(ref all_states) = client_state.all_states {
                            resolve_rules_and_update_entities(all_states, &rules, &mut entities);
                            client_state.lovelace_entities = entities.clone();
                        }
                    }

                    // Save config for this dashboard so it can be restored on navigation
                    let url_path = client_state.pending_configs.remove(&id).unwrap_or_default();
                    client_state.save_dashboard_config(&url_path);

                    info!(
                        "{} entities with {} auto-entities rules tracked for {}, conn_id={}",
                        client_state.lovelace_entities.len(),
                        client_state.filter_rules.len(),
                        client_ip,
                        conn_id
                    );

                    // Clear the config ID since we've processed it
                    client_state.lovelace_config_id = None;
                }
            }

            return Some(data);
        }
    }

    // Handle subscribe_entities initial response (field 'a')
    if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
        if Some(id) == subscribe_entities_id {
            let msg_type = data.get("type").and_then(|v| v.as_str());

            if msg_type == Some("event") {
                if let Some(event) = data.get("event") {
                    // Initial response with all states (field 'a')
                    if let Some(compressed_states) = event.get("a") {
                        client_state.all_states = Some(compressed_states.clone());

                        // Process rules if we have them
                        if !client_state.filter_rules.is_empty() {
                            let mut entities = client_state.lovelace_entities.clone();
                            resolve_rules_and_update_entities(
                                compressed_states,
                                &client_state.filter_rules,
                                &mut entities,
                            );
                            client_state.lovelace_entities = entities;
                        }

                        // Clear subscription ID
                        client_state.subscribe_entities_id = None;
                    }
                }
            }

            return Some(data);
        }
    }

    // Handle event messages (non-subscribe_entities)
    if data.get("type").and_then(|v| v.as_str()) == Some("event") {
        if let Some(event) = data.get("event") {
            if client_state.filtering_active && !client_state.lovelace_entities.is_empty() {
                // Filter compressed updates
                if let Some(updates) = event.get("c").and_then(|v| v.as_object()) {
                    use regex::Regex;
                    let include_pattern = Regex::new(r"^(update|event)\..*").unwrap();

                    let filtered_updates: serde_json::Map<String, Value> = updates
                        .iter()
                        .filter(|(entity_id, _)| {
                            client_state.lovelace_entities.contains(*entity_id)
                                || include_pattern.is_match(entity_id)
                        })
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();

                    if !filtered_updates.is_empty() {
                        let mut modified = data.clone();
                        if let Some(event_obj) = modified.get_mut("event") {
                            if let Some(event_map) = event_obj.as_object_mut() {
                                event_map.insert("c".to_string(), Value::Object(filtered_updates));
                            }
                        }
                        return Some(modified);
                    } else {
                        // No matching entities, filter out this message
                        return None;
                    }
                }
            }
        }
    }

    // Pass through all other messages
    Some(data)
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::channel::mpsc;
    use futures::StreamExt;

    #[tokio::test]
    async fn test_forward_client_to_ha_text() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-conn-1";
        let client_ip = "127.0.0.1";

        // Send a text frame
        tx.send(Frame::text("Hello, HA!")).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().as_str(), "Hello, HA!");
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_tracks_subscriptions() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-conn-2";
        let client_ip = "127.0.0.1";

        // Send subscribe_entities message
        let subscribe_msg = r#"{"type":"subscribe_entities","id":42}"#;
        tx.send(Frame::text(subscribe_msg)).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify state was updated
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert_eq!(state.subscribe_entities_id, Some(42));
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_binary() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();

        let data = vec![0x01, 0x02, 0x03];
        tx.send(Frame::binary(data.clone())).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, "test", &client_states, "127.0.0.1").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().payload().to_vec(), data);
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_close() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();

        tx.send(Frame::close(CloseCode::Normal, "")).await.unwrap();

        forward_client_to_ha(rx, sink_tx, "test", &client_states, "127.0.0.1").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().opcode(), OpCode::Close);
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_passthrough() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();

        // Send a simple text message that should pass through
        tx.send(Frame::text("Hello, Client!")).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, "test", &client_states, "127.0.0.1").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().as_str(), "Hello, Client!");
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_binary_passthrough() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();

        let data = vec![0xAB, 0xCD, 0xEF];
        tx.send(Frame::binary(data.clone())).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, "test", &client_states, "127.0.0.1").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().payload().to_vec(), data);
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_close() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();

        tx.send(Frame::close(CloseCode::Normal, "")).await.unwrap();

        forward_ha_to_client(rx, sink_tx, "test", &client_states, "127.0.0.1").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        assert_eq!(received.unwrap().opcode(), OpCode::Close);
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_batched_array() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-batched";
        let client_ip = "127.0.0.1";

        // Setup: configure lovelace entities
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
            state
                .lovelace_entities
                .insert("light.living_room".to_string());
        }

        // Send batched JSON array with multiple messages
        let batched = r#"[{"type":"event","event":{"c":{"light.kitchen":{"s":"on"}}}},{"type":"event","event":{"c":{"light.living_room":{"s":"off"}}}}]"#;
        tx.send(Frame::text(batched)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Should receive batched array response
        let received = sink_rx.next().await;
        assert!(received.is_some());
        let text = received.unwrap().as_str().to_string();

        // Verify it's a JSON array with both messages
        assert!(text.starts_with('['));
        assert!(text.ends_with(']'));
        // Both entities should be in the response
        assert!(text.contains("light.kitchen"));
        assert!(text.contains("light.living_room"));
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_batched_with_filtering() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-filter-batched";
        let client_ip = "127.0.0.1";

        // Setup: configure only kitchen entity and enable filtering
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
            state.filtering_active = true;
        }

        // Send batched array with kitchen (tracked) and bedroom (not tracked)
        let batched = r#"[{"type":"event","event":{"c":{"light.kitchen":{"s":"on"},"light.bedroom":{"s":"off"}}}}]"#;
        tx.send(Frame::text(batched)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Should receive only the kitchen update
        let received = sink_rx.next().await;
        assert!(received.is_some());
        let text = received.unwrap().as_str().to_string();

        // Verify kitchen is in response
        assert!(text.contains("light.kitchen"));
        // Bedroom should be filtered out
        assert!(!text.contains("light.bedroom"));
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_batched_all_filtered_out() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-filter-all";
        let client_ip = "127.0.0.1";

        // Setup: configure only kitchen entity and enable filtering
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
            state.filtering_active = true;
        }

        // Send batched array with only untracked entities
        let batched = r#"[{"type":"event","event":{"c":{"light.bedroom":{"s":"off"},"light.office":{"s":"on"}}}}]"#;
        tx.send(Frame::text(batched)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Nothing should be sent since all entities were filtered out
        let received = sink_rx.next().await;
        assert!(received.is_none());
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_no_filtering_when_inactive() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-no-filter";
        let client_ip = "127.0.0.1";

        // Setup: configure entities but leave filtering_active=false
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
            // filtering_active defaults to false
        }

        // Send batched array with only untracked entities
        let batched = r#"[{"type":"event","event":{"c":{"light.bedroom":{"s":"off"},"light.office":{"s":"on"}}}}]"#;
        tx.send(Frame::text(batched)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Should receive everything since filtering is inactive
        let received = sink_rx.next().await;
        assert!(received.is_some());
        let text = received.unwrap().as_str().to_string();
        assert!(text.contains("light.bedroom"));
        assert!(text.contains("light.office"));
    }

    #[tokio::test]
    async fn test_forward_ha_to_client_mixed_batched_and_individual() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-mixed";
        let client_ip = "127.0.0.1";

        // Setup: configure entities and enable filtering
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
            state.filtering_active = true;
        }

        // Send batched array
        let batched = r#"[{"type":"event","event":{"c":{"light.kitchen":{"s":"on"}}}}]"#;
        tx.send(Frame::text(batched)).await.unwrap();

        // Send individual message (not batched)
        let individual = r#"{"type":"event","event":{"c":{"light.kitchen":{"s":"off"}}}}"#;
        tx.send(Frame::text(individual)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Should receive two responses
        let first = sink_rx.next().await;
        assert!(first.is_some());
        let first_text = first.unwrap().as_str().to_string();
        assert!(first_text.contains("kitchen"));

        let second = sink_rx.next().await;
        assert!(second.is_some());
        let second_text = second.unwrap().as_str().to_string();
        assert!(second_text.contains("kitchen"));
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_tracks_lovelace_config_with_url_path() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-conn-lovelace";
        let client_ip = "127.0.0.1";

        // Send lovelace/config message with url_path present
        let lovelace_msg = r#"{"type":"lovelace/config","id":100,"url_path":"my-dashboard"}"#;
        tx.send(Frame::text(lovelace_msg)).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify state was updated - ID should be tracked
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert_eq!(state.lovelace_config_id, Some(100));
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_skips_lovelace_config_with_null_url_path() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-conn-lovelace-null";
        let client_ip = "127.0.0.1";

        // Send lovelace/config message with url_path as null
        let lovelace_msg = r#"{"type":"lovelace/config","id":101,"url_path":null}"#;
        tx.send(Frame::text(lovelace_msg)).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify state was NOT updated - ID should not be tracked with null url_path
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert_eq!(state.lovelace_config_id, None);
    }

    #[tokio::test]
    async fn test_forward_client_to_ha_skips_lovelace_config_without_url_path() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-conn-no-url-path";
        let client_ip = "127.0.0.1";

        // Send lovelace/config message without url_path field
        let lovelace_msg = r#"{"type":"lovelace/config","id":102}"#;
        tx.send(Frame::text(lovelace_msg)).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify state was NOT updated - ID should not be tracked without url_path
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert_eq!(state.lovelace_config_id, None);
    }

    #[tokio::test]
    async fn test_process_server_message_saves_dashboard_config() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-save-dash";
        let client_ip = "127.0.0.1";

        // Setup: send lovelace/config request to track pending config
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state
                .pending_configs
                .insert(200, "my-dashboard".to_string());
            state.lovelace_config_id = Some(200);
        }

        // Send lovelace/config response with entities
        let config_response = r#"{"id":200,"type":"result","success":true,"result":{"views":[{"cards":[{"type":"entities","entities":["light.kitchen","switch.living_room"]}]}]}}"#;
        tx.send(Frame::text(config_response)).await.unwrap();
        drop(tx);

        forward_ha_to_client(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify config was saved under the dashboard key
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert!(state.lovelace_entities.contains("light.kitchen"));
        assert!(state.lovelace_entities.contains("switch.living_room"));
        let config = state.dashboard_configs.get("my-dashboard").unwrap();
        assert!(config.lovelace_entities.contains("light.kitchen"));
        assert!(config.lovelace_entities.contains("switch.living_room"));
    }

    #[tokio::test]
    async fn test_subscribe_entities_does_not_clear_entities() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        let client_states = ClientStates::new();
        let conn_id = "test-no-clear";
        let client_ip = "127.0.0.1";

        // Pre-populate entities (as if from a cached config)
        {
            let mut state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
            state.lovelace_entities.insert("light.kitchen".to_string());
        }

        // Send subscribe_entities message
        let subscribe_msg = r#"{"type":"subscribe_entities","id":42}"#;
        tx.send(Frame::text(subscribe_msg)).await.unwrap();
        drop(tx);

        forward_client_to_ha(rx, sink_tx, conn_id, &client_states, client_ip).await;

        // Verify message was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());

        // Verify entities were NOT cleared
        let state = client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
        assert!(state.lovelace_entities.contains("light.kitchen"));
        assert_eq!(state.subscribe_entities_id, Some(42));
    }
}
