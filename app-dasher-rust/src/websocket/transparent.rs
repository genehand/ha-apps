use axum::extract::ws::{Message, WebSocket};
use axum::extract::Request;
use futures::{sink::SinkExt, stream::StreamExt};
use tracing::{error, info};

pub async fn handle(
    client_socket: WebSocket,
    state: crate::AppState,
    client_ip: String,
    req: Request,
) {
    let config = state.config;
    let path = req.uri().path();
    let path_qs = if let Some(qs) = req.uri().query() {
        format!("{}?{}", path, qs)
    } else {
        path.to_string()
    };
    let ha_url = format!("ws://{}{}", config.ha_host, path_qs);
    
    // Build the request with original headers
    let mut ws_request = tokio_tungstenite::tungstenite::handshake::client::Request::builder()
        .method("GET")
        .uri(&ha_url)
        .body(())
        .unwrap();

    // Copy headers from original request, excluding Host
    for (key, value) in req.headers() {
        if !key.as_str().eq_ignore_ascii_case("host") {
            ws_request.headers_mut().insert(key.clone(), value.clone());
        }
    }

    // Set Host header from HA URL (tungstenite doesn't auto-add it for custom requests)
    if let Some(host) = config.ha_host.split(':').next() {
        ws_request.headers_mut().insert(
            http::header::HOST,
            http::HeaderValue::from_str(host).unwrap(),
        );
    }

    // Connect to Home Assistant
    let (ha_socket, _) = match tokio_tungstenite::connect_async(ws_request).await {
        Ok(conn) => conn,
        Err(e) => {
            error!("Failed to connect to Home Assistant WebSocket: {}", e);
            return;
        }
    };
    
    let log_url = ha_url.split('?').next().unwrap_or(&ha_url);
    info!("Transparent WebSocket proxy established to {} for {}", log_url, client_ip);
    
    let (mut ha_sink, mut ha_stream) = ha_socket.split();
    let (mut client_sink, mut client_stream) = client_socket.split();
    
    // Client to HA
    let c2h = async {
        while let Some(Ok(msg)) = client_stream.next().await {
            let tungstenite_msg = match msg {
                Message::Text(text) => {
                    tokio_tungstenite::tungstenite::Message::Text(text.to_string().into())
                }
                Message::Binary(bin) => tokio_tungstenite::tungstenite::Message::Binary(bin),
                Message::Close(close) => {
                    let close_frame = close.map(|c| tokio_tungstenite::tungstenite::protocol::CloseFrame {
                        code: c.code.into(),
                        reason: c.reason.to_string().into(),
                    });
                    let _ = ha_sink.send(tokio_tungstenite::tungstenite::Message::Close(close_frame)).await;
                    break;
                }
                _ => continue,
            };
            
            if let Err(e) = ha_sink.send(tungstenite_msg).await {
                error!("Error forwarding client to HA: {}", e);
                break;
            }
        }
    };
    
    // HA to Client
    let h2c = async {
        while let Some(Ok(msg)) = ha_stream.next().await {
            let axum_msg = match msg {
                tokio_tungstenite::tungstenite::Message::Text(text) => {
                    Message::Text(text.to_string().into())
                }
                tokio_tungstenite::tungstenite::Message::Binary(bin) => Message::Binary(bin),
                tokio_tungstenite::tungstenite::Message::Close(close) => {
                    let axum_close = close.map(|c| axum::extract::ws::CloseFrame {
                        code: c.code.into(),
                        reason: c.reason.to_string().into(),
                    });
                    let _ = client_sink.send(Message::Close(axum_close)).await;
                    break;
                }
                _ => continue,
            };
            
            if let Err(e) = client_sink.send(axum_msg).await {
                error!("Error forwarding HA to client: {}", e);
                break;
            }
        }
    };
    
    // Run both directions concurrently
    tokio::select! {
        _ = c2h => {},
        _ = h2c => {},
    }
    
    info!("Transparent WebSocket connection closed for {}", client_ip);
}