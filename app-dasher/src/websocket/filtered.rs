use axum::{
    body::Body,
    extract::{ConnectInfo, Request, State},
    response::Response,
};
use futures::{Sink, Stream, sink::SinkExt, stream::StreamExt};
use serde_json::Value;
use tracing::{debug, error, info, warn};
use yawc::{CompressionLevel, IncomingUpgrade, Options, WebSocket, close::CloseCode, frame::Frame, frame::OpCode};

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
                    Ok(data) => {
                        let processed = process_server_message(
                            data,
                            conn_id,
                            client_states,
                            client_ip,
                        )
                        .await;

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
                handle(socket, state, client_ip, compression_negotiated).await;
            }
            Err(e) => {
                error!("WebSocket upgrade failed for {}: {}", client_ip, e);
            }
        }
    });

    response
}

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
}
