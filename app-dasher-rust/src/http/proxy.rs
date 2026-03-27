use axum::{
    body::Body,
    extract::Request,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use std::time::Duration;
use tracing::{debug, error};

use crate::AppState;

pub async fn handle(
    req: Request,
    state: AppState,
    client_ip: String,
) -> Response {
    let config = state.config;
    let client = state.http_client;
    
    let method = req.method().clone();
    let path = req.uri().path();
    let query = req.uri().query().map(|q| format!("?{}", q)).unwrap_or_default();
    let url = format!("http://{}{}{}", config.ha_host, path, query);
    
    let timeout = get_timeout_for_path(path);
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
        let existing = headers.get("X-Forwarded-For")
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
    let status = StatusCode::from_u16(upstream_response.status().as_u16())
        .unwrap_or(StatusCode::OK);
    
    // Extract response headers
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

fn get_timeout_for_path(path: &str) -> Option<u64> {
    if path.ends_with(".m3u8") {
        Some(15)
    } else if path.ends_with("/logs/follow") {
        None // No timeout for streaming logs
    } else {
        Some(5) // Default timeout
    }
}