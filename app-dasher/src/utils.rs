use axum::extract::Request;
use axum::http::HeaderMap;

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
