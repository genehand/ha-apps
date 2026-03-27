use axum::extract::Request;
use futures::{sink::SinkExt, stream::StreamExt};
use tracing::{debug, error, info};
use yawc::{CompressionLevel, Options, WebSocket, close::CloseCode, frame::Frame, frame::OpCode};

pub async fn handle(
    client_socket: yawc::HttpWebSocket,
    state: crate::AppState,
    client_ip: String,
    req: Request,
    use_compression: bool,
) {
    let config = state.config;
    let path = req.uri().path();
    let path_qs = if let Some(qs) = req.uri().query() {
        format!("{}?{}", path, qs)
    } else {
        path.to_string()
    };
    let ha_url = format!("ws://{}{}", config.ha_host, path_qs);

    // Parse URL
    let url = match ha_url.parse() {
        Ok(url) => url,
        Err(e) => {
            error!("Failed to parse HA URL: {}", e);
            return;
        }
    };

    // Build custom HTTP request with original headers
    let mut request_builder = yawc::HttpRequest::builder()
        .method("GET")
        .uri(&ha_url);

    // Copy headers from original request, excluding Host and WebSocket-specific headers
    for (key, value) in req.headers() {
        let key_str = key.as_str().to_lowercase();
        // Skip Host and WebSocket-specific headers (yawc sets these)
        if key_str == "host" 
            || key_str == "upgrade"
            || key_str == "connection"
            || key_str == "sec-websocket-key"
            || key_str == "sec-websocket-version" {
            continue;
        }
        if let Ok(name) = http::header::HeaderName::from_bytes(key.as_ref()) {
            if let Ok(val) = http::header::HeaderValue::from_bytes(value.as_bytes()) {
                request_builder = request_builder.header(name, val);
            }
        }
    }

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

    let log_url = ha_url.split('?').next().unwrap_or(&ha_url);
    debug!(
        "Transparent WebSocket proxy established to {} for {}",
        log_url, client_ip
    );

    // Split sockets into sink and stream
    let (mut client_sink, mut client_stream) = client_socket.split();
    let (mut ha_sink, mut ha_stream) = ha_socket.split();

    // Client to HA
    let c2h = async {
        while let Some(frame) = client_stream.next().await {
            match frame.opcode() {
                OpCode::Text => {
                    let text = frame.as_str().to_string();
                    let ha_frame = Frame::text(text);
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
    };

    // HA to Client
    let h2c = async {
        while let Some(frame) = ha_stream.next().await {
            match frame.opcode() {
                OpCode::Text => {
                    let text = frame.as_str().to_string();
                    let client_frame = Frame::text(text);
                    if let Err(e) = client_sink.send(client_frame).await {
                        error!("Error forwarding HA to client: {}", e);
                        break;
                    }
                }
                OpCode::Binary => {
                    let payload = frame.payload().to_vec();
                    let client_frame = Frame::binary(payload);
                    if let Err(e) = client_sink.send(client_frame).await {
                        error!("Error forwarding HA to client: {}", e);
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
    };

    // Run both directions concurrently
    tokio::select! {
        _ = c2h => {},
        _ = h2c => {},
    }

    info!("Transparent WebSocket connection closed for {}", client_ip);
}