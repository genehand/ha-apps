use axum::{
    body::Body,
    extract::{ConnectInfo, Request, State},
    response::Response,
};
use futures::{sink::SinkExt, stream::StreamExt, Sink, Stream};
use tracing::{debug, error, info};
use yawc::{
    close::CloseCode, frame::Frame, frame::OpCode, CompressionLevel, IncomingUpgrade, Options,
    WebSocket,
};

use crate::state::AppState;
use crate::utils;

/// Forward WebSocket frames from source to destination
async fn forward_frames<S, D>(mut source: S, mut destination: D, direction: &str)
where
    S: Stream<Item = Frame> + Unpin,
    D: Sink<Frame> + Unpin,
    D::Error: std::fmt::Display,
{
    while let Some(frame) = source.next().await {
        match frame.opcode() {
            OpCode::Text | OpCode::Binary => {
                if let Err(e) = destination.send(frame).await {
                    error!("Error forwarding {}: {}", direction, e);
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

    info!(
        "Transparent WebSocket connection for {} from {}",
        req.uri().path(),
        client_ip
    );

    // Check if client requested compression via Sec-WebSocket-Extensions header
    let client_requested_compression = utils::is_compression_negotiated(req.headers());

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
                handle(socket, state, client_ip, req, compression_negotiated).await;
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
    let mut request_builder = yawc::HttpRequest::builder().method("GET").uri(&ha_url);

    // Copy headers from original request, excluding Host and WebSocket-specific headers
    for (key, value) in req.headers() {
        let key_str = key.as_str().to_lowercase();
        // Skip Host and WebSocket-specific headers (yawc sets these)
        if key_str == "host"
            || key_str == "upgrade"
            || key_str == "connection"
            || key_str == "sec-websocket-key"
            || key_str == "sec-websocket-version"
        {
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
    let (client_sink, client_stream) = client_socket.split();
    let (ha_sink, ha_stream) = ha_socket.split();

    // Client to HA
    let c2h = forward_frames(client_stream, ha_sink, "client to HA");

    // HA to Client
    let h2c = forward_frames(ha_stream, client_sink, "HA to client");

    // Run both directions concurrently
    tokio::select! {
        _ = c2h => {},
        _ = h2c => {},
    }

    info!("Transparent WebSocket connection closed for {}", client_ip);
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::channel::mpsc;
    use futures::StreamExt;

    #[tokio::test]
    async fn test_forward_frames_text() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        // Send a text frame
        let text_frame = Frame::text("Hello, World!");
        tx.send(text_frame).await.unwrap();
        drop(tx); // Close the stream

        forward_frames(rx, sink_tx, "test").await;

        // Verify the frame was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());
        let frame = received.unwrap();
        assert_eq!(frame.opcode(), OpCode::Text);
        assert_eq!(frame.as_str(), "Hello, World!");
    }

    #[tokio::test]
    async fn test_forward_frames_binary() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        // Send a binary frame
        let binary_data = vec![0x01, 0x02, 0x03, 0x04];
        let binary_frame = Frame::binary(binary_data.clone());
        tx.send(binary_frame).await.unwrap();
        drop(tx);

        forward_frames(rx, sink_tx, "test").await;

        let received = sink_rx.next().await;
        assert!(received.is_some());
        let frame = received.unwrap();
        assert_eq!(frame.opcode(), OpCode::Binary);
        assert_eq!(frame.payload().to_vec(), binary_data);
    }

    #[tokio::test]
    async fn test_forward_frames_close() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        // Send a close frame
        let close_frame = Frame::close(CloseCode::Normal, "Goodbye");
        tx.send(close_frame).await.unwrap();

        forward_frames(rx, sink_tx, "test").await;

        // Verify close frame was forwarded
        let received = sink_rx.next().await;
        assert!(received.is_some());
        let frame = received.unwrap();
        assert_eq!(frame.opcode(), OpCode::Close);
    }

    #[tokio::test]
    async fn test_forward_frames_multiple() {
        let (mut tx, rx) = mpsc::channel::<Frame>(10);
        let (sink_tx, mut sink_rx) = mpsc::channel::<Frame>(10);

        // Send multiple frames
        tx.send(Frame::text("Message 1")).await.unwrap();
        tx.send(Frame::text("Message 2")).await.unwrap();
        tx.send(Frame::binary(vec![0xAB, 0xCD])).await.unwrap();
        drop(tx);

        forward_frames(rx, sink_tx, "test").await;

        // Verify all frames were forwarded
        assert_eq!(sink_rx.next().await.unwrap().as_str(), "Message 1");
        assert_eq!(sink_rx.next().await.unwrap().as_str(), "Message 2");
        let binary = sink_rx.next().await.unwrap();
        assert_eq!(binary.opcode(), OpCode::Binary);
        assert!(sink_rx.next().await.is_none());
    }
}
