use futures::{sink::SinkExt, stream::StreamExt};
use serde_json::Value;
use tracing::{debug, error, info};
use yawc::{CompressionLevel, Options, WebSocket, close::CloseCode, frame::Frame, frame::OpCode};

use crate::state::ClientStates;
use crate::websocket::entities::{parse_lovelace_entities, resolve_rules_and_update_entities};

pub async fn handle(client_socket: yawc::HttpWebSocket, state: crate::AppState, client_ip: String, use_compression: bool) {
    let config = state.config;
    let client_states = state.client_states;
    let conn_id = format!("{:p}", &client_socket);

    // Create client state
    {
        let mut client_state = client_states.get_or_insert(conn_id.clone(), client_ip.clone());
        client_state.client_ip = client_ip.clone();
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
    let mut request_builder = yawc::HttpRequest::builder()
        .method("GET")
        .uri(&ha_url_str);

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
    let (mut client_sink, mut client_stream) = client_socket.split();
    let (mut ha_sink, mut ha_stream) = ha_socket.split();

    // Client to HA task
    let conn_id_clone = conn_id.clone();
    let client_states_clone = client_states.clone();
    let client_ip_clone = client_ip.clone();
    let c2h_handle = tokio::spawn(async move {
        while let Some(frame) = client_stream.next().await {
            match frame.opcode() {
                OpCode::Text => {
                    let text = frame.as_str();
                    // Process client message to track subscription IDs
                    if let Ok(data) = serde_json::from_str::<Value>(text) {
                        process_client_message(
                            data,
                            &conn_id_clone,
                            &client_states_clone,
                            &client_ip_clone,
                        )
                        .await;
                    }

                    let ha_frame = Frame::text(text.to_string());
                    if let Err(e) = ha_sink.send(ha_frame).await {
                        error!("Error forwarding client to HA: {}", e);
                        break;
                    }
                }
                OpCode::Binary => {
                    let payload = frame.payload().to_vec();
                    let ha_frame = Frame::binary(payload);
                    if let Err(e) = ha_sink.send(ha_frame).await {
                        error!("Error forwarding client to HA: {}", e);
                        break;
                    }
                }
                OpCode::Close => {
                    let _ = ha_sink.send(Frame::close(CloseCode::Normal, "")).await;
                    break;
                }
                _ => {}
            }
        }
    });

    // HA to Client task
    let conn_id_clone2 = conn_id.clone();
    let client_states_clone2 = client_states.clone();
    let client_ip_clone = client_ip.clone();
    let h2c_handle = tokio::spawn(async move {
        while let Some(frame) = ha_stream.next().await {
            match frame.opcode() {
                OpCode::Text => {
                    let text = frame.as_str();
                    match serde_json::from_str::<Value>(text) {
                        Ok(data) => {
                            let processed = process_server_message(
                                data,
                                &conn_id_clone2,
                                &client_states_clone2,
                                &client_ip_clone,
                            )
                            .await;

                            if let Some(response) = processed {
                                let client_frame = Frame::text(response.to_string());
                                if let Err(e) = client_sink.send(client_frame).await {
                                    error!("Error sending to client: {}", e);
                                    break;
                                }
                            }
                        }
                        Err(_) => {
                            // Pass through non-JSON messages
                            let client_frame = Frame::text(text.to_string());
                            if let Err(e) = client_sink.send(client_frame).await {
                                error!("Error sending to client: {}", e);
                                break;
                            }
                        }
                    }
                }
                OpCode::Binary => {
                    let payload = frame.payload().to_vec();
                    let client_frame = Frame::binary(payload);
                    if let Err(e) = client_sink.send(client_frame).await {
                        error!("Error sending to client: {}", e);
                        break;
                    }
                }
                OpCode::Close => {
                    let _ = client_sink.send(Frame::close(CloseCode::Normal, "")).await;
                    break;
                }
                _ => {}
            }
        }
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
            if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
                let mut state =
                    client_states.get_or_insert(conn_id.to_string(), client_ip.to_string());
                state.lovelace_config_id = Some(id);
                debug!("lovelace/config request with ID: {} for {}", id, client_ip);
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
            if data.get("type").and_then(|v| v.as_str()) == Some("result")
                && data.get("success").and_then(|v| v.as_bool()) == Some(true)
            {
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

    // Handle subscribe_entities response
    if let Some(id) = data.get("id").and_then(|v| v.as_u64()) {
        if Some(id) == subscribe_entities_id {
            if data.get("type").and_then(|v| v.as_str()) == Some("event") {
                if let Some(event) = data.get("event") {
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

    // Handle event messages
    if data.get("type").and_then(|v| v.as_str()) == Some("event") {
        if !client_state.lovelace_entities.is_empty() {
            if let Some(event) = data.get("event") {
                // Skip state_changed events (they duplicate compressed updates)
                if event.get("event_type").and_then(|v| v.as_str()) == Some("state_changed") {
                    return None;
                }

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
