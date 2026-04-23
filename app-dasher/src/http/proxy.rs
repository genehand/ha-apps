use axum::{
    body::Body,
    extract::Request,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use std::time::Duration;
use tracing::{debug, error};

use crate::AppState;

pub async fn handle(req: Request, state: AppState, client_ip: String) -> Response {
    let config = state.config;
    let client = state.http_client;

    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let query = req
        .uri()
        .query()
        .map(|q| format!("?{}", q))
        .unwrap_or_default();
    let url = format!("http://{}{}{}", config.ha_host, path, query);

    let timeout = get_timeout_for_path(&path);
    if timeout != Some(5) {
        debug!("Timeout set to {:?} for {}", timeout, path);
    }

    // Build headers
    let mut headers = reqwest::header::HeaderMap::new();
    for (key, value) in req.headers() {
        let key_lower = key.as_str().to_lowercase();
        // Skip host header (we set it via reqwest)
        if key_lower != "host" {
            if let Ok(name) = reqwest::header::HeaderName::from_bytes(key.as_ref()) {
                if let Ok(val) = reqwest::header::HeaderValue::from_bytes(value.as_bytes()) {
                    headers.insert(name, val);
                }
            }
        }
    }

    // Handle X-Forwarded-For
    if config.transparent {
        headers.remove("X-Forwarded-For");
    } else {
        let existing = headers
            .get("X-Forwarded-For")
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());

        let new_value = match existing {
            Some(existing) => format!("{}, {}", existing, client_ip),
            None => client_ip,
        };

        if let Ok(val) = reqwest::header::HeaderValue::from_str(&new_value) {
            headers.insert("X-Forwarded-For", val);
        }
    }

    // Read body
    let body_bytes = match axum::body::to_bytes(req.into_body(), usize::MAX).await {
        Ok(bytes) => bytes,
        Err(e) => {
            error!("Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "Bad Request").into_response();
        }
    };

    // Convert axum Method to reqwest Method
    let reqwest_method = match method.as_str() {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        "PUT" => reqwest::Method::PUT,
        "DELETE" => reqwest::Method::DELETE,
        "PATCH" => reqwest::Method::PATCH,
        "HEAD" => reqwest::Method::HEAD,
        "OPTIONS" => reqwest::Method::OPTIONS,
        "TRACE" => reqwest::Method::TRACE,
        "CONNECT" => reqwest::Method::CONNECT,
        _ => reqwest::Method::GET,
    };

    // Build and send request
    let request_builder = client
        .request(reqwest_method, &url)
        .headers(headers)
        .body(body_bytes);

    let request_builder = match timeout {
        Some(secs) => request_builder.timeout(Duration::from_secs(secs)),
        None => request_builder,
    };

    let upstream_response = match request_builder.send().await {
        Ok(resp) => resp,
        Err(e) => {
            error!("HTTP proxy error: {}", e);
            if e.is_timeout() {
                return (StatusCode::GATEWAY_TIMEOUT, "Gateway Timeout").into_response();
            }
            return (StatusCode::BAD_GATEWAY, "Proxy Error").into_response();
        }
    };

    // Build response
    let status =
        StatusCode::from_u16(upstream_response.status().as_u16()).unwrap_or(StatusCode::OK);

    // Check if this is an HTML response that needs script injection
    let content_type = upstream_response
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok());
    let is_html = content_type
        .map(|ct| ct.starts_with("text/html"))
        .unwrap_or(false);

    if is_html {
        return build_html_response(upstream_response, status).await;
    }

    // Extract response headers for non-HTML
    let mut response_builder = axum::response::Response::builder().status(status);
    for (key, value) in upstream_response.headers() {
        if let Ok(name) = axum::http::HeaderName::from_bytes(key.as_ref()) {
            if let Ok(val) = axum::http::HeaderValue::from_bytes(value.as_bytes()) {
                response_builder = response_builder.header(name, val);
            }
        }
    }

    // Stream the response body
    let stream = upstream_response.bytes_stream();
    let body = Body::from_stream(stream);

    match response_builder.body(body) {
        Ok(response) => response,
        Err(_) => (StatusCode::INTERNAL_SERVER_ERROR, "Response Error").into_response(),
    }
}

async fn build_html_response(upstream_response: reqwest::Response, status: StatusCode) -> Response {
    // Clone headers before consuming the response body
    let headers = upstream_response.headers().clone();

    let content_encoding = headers
        .get("content-encoding")
        .and_then(|v| v.to_str().ok());

    let body_bytes = match upstream_response.bytes().await {
        Ok(bytes) => bytes,
        Err(e) => {
            error!("Failed to read HTML response body: {}", e);
            return (StatusCode::BAD_GATEWAY, "Proxy Error").into_response();
        }
    };

    let (injected_bytes, new_encoding) =
        crate::http::inject::process_html_response(&body_bytes, content_encoding);

    // Build response headers, removing content-length/transfer-encoding/content-encoding
    // since body changed and we may have recompressed
    let mut response_builder = axum::response::Response::builder().status(status);
    for (key, value) in headers.iter() {
        let key_lower = key.as_str().to_lowercase();
        if key_lower == "content-length"
            || key_lower == "transfer-encoding"
            || key_lower == "content-encoding"
        {
            continue;
        }
        if let Ok(name) = axum::http::HeaderName::from_bytes(key.as_ref()) {
            if let Ok(val) = axum::http::HeaderValue::from_bytes(value.as_bytes()) {
                response_builder = response_builder.header(name, val);
            }
        }
    }

    // Set new content-encoding if we recompressed
    if let Some(enc) = new_encoding {
        if let Ok(val) = axum::http::HeaderValue::from_str(&enc) {
            response_builder = response_builder.header("content-encoding", val);
        }
    }

    let body = Body::from(injected_bytes);

    match response_builder.body(body) {
        Ok(response) => response,
        Err(_) => (StatusCode::INTERNAL_SERVER_ERROR, "Response Error").into_response(),
    }
}

fn get_timeout_for_path(path: &str) -> Option<u64> {
    if path.ends_with(".m3u8") {
        Some(15)
    } else if path.ends_with("/logs/follow") {
        None // No timeout for streaming logs
    } else {
        Some(5) // Default timeout
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::io::Write;
    use wiremock::matchers::{method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    use crate::config::Config;
    use crate::state::ClientStates;
    use dashmap::DashMap;
    use std::sync::Arc;

    fn create_test_config(ha_host: String) -> Arc<Config> {
        Arc::new(Config {
            ha_host,
            proxy_port: 8125,
            transparent: false,
            log_level: "INFO".to_string(),
        })
    }

    fn create_test_state(config: Arc<Config>) -> AppState {
        let http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();

        AppState {
            config,
            client_states: ClientStates::new(),
            http_client,
            panel_updates: Arc::new(DashMap::new()),
        }
    }

    #[test]
    fn test_get_timeout_for_path_m3u8() {
        assert_eq!(get_timeout_for_path("/api/stream/playlist.m3u8"), Some(15));
        assert_eq!(get_timeout_for_path("/test.m3u8"), Some(15));
        assert_eq!(get_timeout_for_path("/path/to/video.m3u8"), Some(15));
    }

    #[test]
    fn test_get_timeout_for_path_logs_follow() {
        assert_eq!(get_timeout_for_path("/api/logs/follow"), None);
        assert_eq!(get_timeout_for_path("/config/logs/follow"), None);
    }

    #[test]
    fn test_get_timeout_for_path_default() {
        assert_eq!(get_timeout_for_path("/api/states"), Some(5));
        assert_eq!(get_timeout_for_path("/"), Some(5));
        assert_eq!(get_timeout_for_path("/websocket"), Some(5));
        assert_eq!(get_timeout_for_path("/api/config"), Some(5));
    }

    #[test]
    fn test_get_timeout_for_path_edge_cases() {
        // Case sensitivity
        assert_eq!(get_timeout_for_path("/test.M3U8"), Some(5));
        assert_eq!(get_timeout_for_path("/test.M3u8"), Some(5));

        // Partial matches
        assert_eq!(get_timeout_for_path("/logs/follow/extra"), Some(5));
        assert_eq!(get_timeout_for_path("/logs/followother"), Some(5));
        assert_eq!(get_timeout_for_path("/test.m3u8.backup"), Some(5));

        // Empty path
        assert_eq!(get_timeout_for_path(""), Some(5));
    }

    /// Test that Accept-Encoding header is forwarded to upstream
    #[tokio::test]
    async fn test_accept_encoding_header_forwarded() {
        let mock_server = MockServer::start().await;

        // Use header_exists to check that Accept-Encoding is forwarded
        // (value may be normalized by reqwest, so we don't check exact value)
        Mock::given(method("GET"))
            .and(path("/api/test"))
            .and(wiremock::matchers::header_exists("accept-encoding"))
            .respond_with(ResponseTemplate::new(200).set_body_string("OK"))
            .expect(1)
            .mount(&mock_server)
            .await;

        let config =
            create_test_config(mock_server.uri().trim_start_matches("http://").to_string());
        let state = create_test_state(config);

        let mut req = Request::new(Body::empty());
        *req.method_mut() = axum::http::Method::GET;
        *req.uri_mut() = "/api/test".parse().unwrap();
        req.headers_mut()
            .insert("Accept-Encoding", "gzip, deflate".parse().unwrap());

        let response = handle(req, state, "127.0.0.1".to_string()).await;

        assert_eq!(response.status(), StatusCode::OK);
    }

    /// Test that Content-Encoding header is passed through to client
    #[tokio::test]
    async fn test_content_encoding_header_passed_through() {
        let mock_server = MockServer::start().await;

        // Create gzip-compressed response body
        let original_text = "Hello, World! This is a test message.";
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(original_text.as_bytes()).unwrap();
        let gzipped_data = encoder.finish().unwrap();

        Mock::given(method("GET"))
            .and(path("/api/compressed"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Encoding", "gzip")
                    .insert_header("Content-Type", "text/plain")
                    .set_body_bytes(gzipped_data.clone()),
            )
            .expect(1)
            .mount(&mock_server)
            .await;

        let config =
            create_test_config(mock_server.uri().trim_start_matches("http://").to_string());
        let state = create_test_state(config);

        let mut req = Request::new(Body::empty());
        *req.uri_mut() = "/api/compressed".parse().unwrap();
        let response = handle(req, state, "127.0.0.1".to_string()).await;

        assert_eq!(response.status(), StatusCode::OK);

        // Verify Content-Encoding header is present
        let content_encoding = response
            .headers()
            .get("content-encoding")
            .expect("Content-Encoding header should be present");
        assert_eq!(content_encoding, "gzip");

        // Verify the body is still gzip-compressed (not decompressed)
        let body_bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();

        // The received body should be the gzipped data, not the original text
        assert_eq!(body_bytes.to_vec(), gzipped_data);

        // Verify it's valid gzip by decompressing
        let mut decoder = flate2::read::GzDecoder::new(&body_bytes[..]);
        let mut decompressed = String::new();
        std::io::Read::read_to_string(&mut decoder, &mut decompressed).unwrap();
        assert_eq!(decompressed, original_text);
    }

    /// Test that gzip-compressed JSON responses pass through correctly
    #[tokio::test]
    async fn test_gzip_json_pass_through() {
        let mock_server = MockServer::start().await;

        // Create a JSON response and gzip it
        let json_data = r#"{"status":"ok","entities":["light.living_room","switch.kitchen"]}"#;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(json_data.as_bytes()).unwrap();
        let gzipped_data = encoder.finish().unwrap();

        Mock::given(method("GET"))
            .and(path("/api/states"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Encoding", "gzip")
                    .insert_header("Content-Type", "application/json")
                    .set_body_bytes(gzipped_data.clone()),
            )
            .expect(1)
            .mount(&mock_server)
            .await;

        let config =
            create_test_config(mock_server.uri().trim_start_matches("http://").to_string());
        let state = create_test_state(config);

        let mut req = Request::new(Body::empty());
        *req.method_mut() = axum::http::Method::GET;
        *req.uri_mut() = "/api/states".parse().unwrap();
        req.headers_mut()
            .insert("Accept-Encoding", "gzip".parse().unwrap());

        let response = handle(req, state, "127.0.0.1".to_string()).await;

        assert_eq!(response.status(), StatusCode::OK);

        // Verify Content-Type and Content-Encoding headers
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        assert_eq!(response.headers().get("content-encoding").unwrap(), "gzip");

        // Verify body is still compressed
        let body_bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        assert_eq!(body_bytes.to_vec(), gzipped_data);

        // Decompress and verify content
        let mut decoder = flate2::read::GzDecoder::new(&body_bytes[..]);
        let mut decompressed = String::new();
        std::io::Read::read_to_string(&mut decoder, &mut decompressed).unwrap();
        assert_eq!(decompressed, json_data);
    }

    /// Test that non-gzipped responses still work correctly
    #[tokio::test]
    async fn test_non_gzip_response_passes_through() {
        let mock_server = MockServer::start().await;

        let plain_text = "Plain text response";

        Mock::given(method("GET"))
            .and(path("/api/plain"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "text/plain")
                    .set_body_string(plain_text),
            )
            .expect(1)
            .mount(&mock_server)
            .await;

        let config =
            create_test_config(mock_server.uri().trim_start_matches("http://").to_string());
        let state = create_test_state(config);

        let mut req = Request::new(Body::empty());
        *req.uri_mut() = "/api/plain".parse().unwrap();
        let response = handle(req, state, "127.0.0.1".to_string()).await;

        assert_eq!(response.status(), StatusCode::OK);

        // Verify no Content-Encoding header
        assert!(response.headers().get("content-encoding").is_none());

        // Verify body is the original plain text
        let body_bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        assert_eq!(String::from_utf8(body_bytes.to_vec()).unwrap(), plain_text);
    }
}
