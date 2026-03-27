use std::sync::Arc;

use axum::{
    body::Body,
    extract::{ConnectInfo, Request, State},
    response::{IntoResponse, Response},
    routing::Router,
};
use tokio::net::TcpListener;
use tower::ServiceBuilder;
use tower_http::trace::TraceLayer;
use tracing::{debug, error, info};
use yawc::IncomingUpgrade;

mod config;
mod http;
mod logging;
mod state;
mod websocket;

use config::Config;
use state::ClientStates;

#[derive(Clone)]
struct AppState {
    config: Arc<Config>,
    client_states: ClientStates,
    http_client: reqwest::Client,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    logging::init();

    let config = Arc::new(Config::load()?);
    info!("Starting Dasher proxy");
    info!("Proxy port: {} -> {}", config.proxy_port, config.ha_host);

    let app_state = AppState {
        config: config.clone(),
        client_states: ClientStates::new(),
        http_client: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .danger_accept_invalid_certs(true)
            .build()?,
    };

    let app = Router::new()
        .route("/api/websocket", axum::routing::get(filtered_ws_handler))
        .route("/", axum::routing::any(catchall_handler))
        .route("/{*path}", axum::routing::any(catchall_handler))
        .layer(ServiceBuilder::new().layer(TraceLayer::new_for_http()))
        .with_state(app_state);

    let addr = format!("0.0.0.0:{}", config.proxy_port);
    let listener = TcpListener::bind(&addr).await?;
    info!("Listening on {}", addr);

    // Graceful shutdown
    let shutdown = tokio::spawn(async move {
        tokio::signal::ctrl_c().await.ok();
        info!("Received shutdown signal");
    });

    let server = axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    );

    tokio::select! {
        result = server => {
            if let Err(e) = result {
                error!("Server error: {}", e);
            }
        }
        _ = shutdown => {
            info!("Shutting down gracefully");
        }
    }

    Ok(())
}

async fn filtered_ws_handler(
    upgrade: IncomingUpgrade,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let client_ip = get_client_ip(&req, addr);
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
        yawc::Options::default().with_compression_level(yawc::CompressionLevel::default())
    } else {
        yawc::Options::default()
    };

    let (response, ws_future) = upgrade.upgrade(options).unwrap();

    // Check what compression was actually negotiated in the response
    let compression_negotiated = response
        .headers()
        .get("sec-websocket-extensions")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.contains("permessage-deflate"))
        .unwrap_or(false);

    if compression_negotiated {
        debug!("WebSocket compression negotiated (permessage-deflate)");
    }

    // Convert response body type
    let response = response.map(|_| Body::empty());

    tokio::spawn(async move {
        match ws_future.await {
            Ok(socket) => {
                websocket::filtered::handle(socket, state, client_ip, compression_negotiated).await;
            }
            Err(e) => {
                error!("WebSocket upgrade failed for {}: {}", client_ip, e);
            }
        }
    });

    response
}

// Catch-all handler that detects WebSocket upgrades vs regular HTTP
async fn catchall_handler(
    upgrade: Result<IncomingUpgrade, axum::http::StatusCode>,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let client_ip = get_client_ip(&req, addr);

    match upgrade {
        Ok(upgrade) => {
            // This is a WebSocket upgrade request
            info!(
                "Transparent WebSocket connection for {} from {}",
                req.uri().path(),
                client_ip
            );

            // Check if client requested compression via Sec-WebSocket-Extensions header
            let client_requested_compression = req
                .headers()
                .get("sec-websocket-extensions")
                .and_then(|v| v.to_str().ok())
                .map(|v| v.contains("permessage-deflate"))
                .unwrap_or(false);

            let options = if client_requested_compression {
                yawc::Options::default().with_compression_level(yawc::CompressionLevel::default())
            } else {
                yawc::Options::default()
            };

            let (response, ws_future) = upgrade.upgrade(options).unwrap();

            // Check what compression was actually negotiated in the response
            let compression_negotiated = response
                .headers()
                .get("sec-websocket-extensions")
                .and_then(|v| v.to_str().ok())
                .map(|v| v.contains("permessage-deflate"))
                .unwrap_or(false);

            if compression_negotiated {
                debug!("WebSocket compression negotiated (permessage-deflate)");
            }

            // Convert response body type
            let response = response.map(|_| Body::empty());

            tokio::spawn(async move {
                match ws_future.await {
                    Ok(socket) => {
                        websocket::transparent::handle(socket, state, client_ip, req, compression_negotiated).await;
                    }
                    Err(e) => {
                        error!("WebSocket upgrade failed for {}: {}", client_ip, e);
                    }
                }
            });

            response
        }
        Err(_) => {
            // Not a WebSocket request, proxy as HTTP
            tracing::debug!("{} {} from {}", req.method(), req.uri().path(), client_ip);
            http::proxy::handle(req, state, client_ip)
                .await
                .into_response()
        }
    }
}

fn get_client_ip(req: &Request, addr: std::net::SocketAddr) -> String {
    if let Some(x_forwarded_for) = req.headers().get("X-Forwarded-For") {
        if let Ok(value) = x_forwarded_for.to_str() {
            return value.split(',').next().unwrap_or("").trim().to_string();
        }
    }
    addr.ip().to_string()
}