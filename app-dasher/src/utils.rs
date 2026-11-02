use axum::extract::Request;
use axum::http::HeaderMap;
use yawc::{CompressionLevel, Options};

/// Largest single WebSocket frame the proxy will accept, in either direction.
///
/// yawc 0.5 enforces `max_payload_read` (default 1 MiB) on every incoming frame
/// and closes the connection with code 1009 when a frame reaches it; yawc 0.3
/// documented the same default but never compared it. Home Assistant routinely
/// sends larger single frames than that: the `subscribe_entities` snapshot
/// carries every entity plus its attributes, so a few thousand entities or a
/// handful of verbose ones already exceed 1 MiB, and `history` results are
/// bigger still. When such a frame is rejected the read stream simply ends
/// (yawc does not surface the error to the sink/stream owner), the proxy
/// synthesizes a `1001` close toward the browser, and the frontend reconnects
/// into the same frame forever. The limit only caps how much a peer can make us
/// buffer, so keep it well above anything legitimate while still bounding it.
pub const MAX_FRAME_BYTES: usize = 64 * 1024 * 1024; // 64 MiB

/// Build the WebSocket options used on both legs of the proxy.
///
/// Compression stays opt-in per connection (the client's offer drives it); the
/// frame limits are always raised, because the defaults break Home Assistant's
/// larger payloads regardless of compression.
pub fn ws_options(compression: bool) -> Options {
    let options = Options::default()
        .with_max_payload_read(MAX_FRAME_BYTES)
        .with_max_read_buffer(MAX_FRAME_BYTES);

    if compression {
        options.with_compression_level(CompressionLevel::default())
    } else {
        options
    }
}

pub fn get_client_ip(req: &Request, addr: std::net::SocketAddr) -> String {
    if let Some(x_forwarded_for) = req.headers().get("X-Forwarded-For") {
        if let Ok(value) = x_forwarded_for.to_str() {
            return value.split(',').next().unwrap_or("").trim().to_string();
        }
    }
    addr.ip().to_string()
}

/// Check if WebSocket compression (permessage-deflate) is present in the headers.
pub fn is_compression_negotiated(headers: &HeaderMap) -> bool {
    let negotiated = headers
        .get("sec-websocket-extensions")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.contains("permessage-deflate"))
        .unwrap_or(false);

    if negotiated {
        tracing::debug!("WebSocket compression negotiated (permessage-deflate)");
    }

    negotiated
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::HeaderValue;

    #[test]
    fn test_ws_options_raise_frame_limits() {
        // Defaults are 1 MiB per frame / 2 MiB per fragmented message, which a
        // Home Assistant entity snapshot can exceed.
        for compression in [false, true] {
            let options = ws_options(compression);
            assert_eq!(options.max_payload_read, Some(MAX_FRAME_BYTES));
            assert_eq!(options.max_read_buffer, Some(MAX_FRAME_BYTES));
            assert_eq!(options.compression.is_some(), compression);
        }
    }

    #[test]
    fn test_get_client_ip_from_addr() {
        let req = Request::new(Body::empty());
        let addr = std::net::SocketAddr::from(([192, 168, 1, 100], 8080));
        assert_eq!(get_client_ip(&req, addr), "192.168.1.100");
    }

    #[test]
    fn test_get_client_ip_from_header() {
        let mut req = Request::new(Body::empty());
        req.headers_mut()
            .insert("X-Forwarded-For", HeaderValue::from_static("10.0.0.50"));
        let addr = std::net::SocketAddr::from(([192, 168, 1, 100], 8080));
        assert_eq!(get_client_ip(&req, addr), "10.0.0.50");
    }

    #[test]
    fn test_get_client_ip_from_header_with_multiple() {
        let mut req = Request::new(Body::empty());
        req.headers_mut().insert(
            "X-Forwarded-For",
            HeaderValue::from_static("10.0.0.50, 172.16.0.1, 192.168.0.1"),
        );
        let addr = std::net::SocketAddr::from(([192, 168, 1, 100], 8080));
        assert_eq!(get_client_ip(&req, addr), "10.0.0.50");
    }

    #[test]
    fn test_get_client_ip_from_header_with_whitespace() {
        let mut req = Request::new(Body::empty());
        req.headers_mut()
            .insert("X-Forwarded-For", HeaderValue::from_static("  10.0.0.50  "));
        let addr = std::net::SocketAddr::from(([192, 168, 1, 100], 8080));
        assert_eq!(get_client_ip(&req, addr), "10.0.0.50");
    }

    #[test]
    fn test_is_compression_negotiated_present() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "sec-websocket-extensions",
            HeaderValue::from_static("permessage-deflate; client_max_window_bits"),
        );
        assert!(is_compression_negotiated(&headers));
    }

    #[test]
    fn test_is_compression_negotiated_absent() {
        let headers = HeaderMap::new();
        assert!(!is_compression_negotiated(&headers));
    }

    #[test]
    fn test_is_compression_negotiated_wrong_extension() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "sec-websocket-extensions",
            HeaderValue::from_static("x-webkit-deflate-frame"),
        );
        assert!(!is_compression_negotiated(&headers));
    }
}
