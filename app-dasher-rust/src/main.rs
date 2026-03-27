use std::sync::Arc;

use axum::{
    extract::{ws::rejection::WebSocketUpgradeRejection, ConnectInfo, Request, State, WebSocketUpgrade},
    response::{IntoResponse, Response},
    routing::Router,
};
use tokio::net::TcpListener;
use tower::ServiceBuilder;
use tower_http::trace::TraceLayer;
use tracing::{error, info};

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
    
    // Use fallback for everything - it will check headers and route appropriately
    let app = Router::new()
        .route("/api/websocket", axum::routing::get(filtered_ws_handler))
        .route("/", axum::routing::any(catchall_handler))
        .route("/{*path}", axum::routing::any(catchall_handler))
        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
        )
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
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let client_ip = get_client_ip(&req, addr);
    let path = req.uri().path();
    
    info!("Filtered WebSocket connection for {} from {}", path, client_ip);
    ws.on_upgrade(move |socket| {
        websocket::filtered::handle(socket, state, client_ip)
    }).into_response()
}

// Handler for all other paths - only matches GET requests with WebSocket upgrade
async fn catchall_handler(
    ws: Result<WebSocketUpgrade, WebSocketUpgradeRejection>,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let client_ip = get_client_ip(&req, addr);
    
    match ws {
        Ok(ws) => {
            // This is a WebSocket upgrade request
            info!("Transparent WebSocket connection for {} from {}", req.uri().path(), client_ip);
            let response: axum::response::Response = ws.on_upgrade(move |socket| async move {
                websocket::transparent::handle(socket, state, client_ip, req).await;
            });
            response
        }
        Err(_) => {
            // Not a WebSocket request, proxy as HTTP
            tracing::debug!("{} {} from {}", req.method(), req.uri().path(), client_ip);
            http::proxy::handle(req, state, client_ip).await.into_response()
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
