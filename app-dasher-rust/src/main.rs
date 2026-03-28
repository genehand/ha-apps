use std::sync::Arc;

use axum::{
    extract::{ConnectInfo, Request, State},
    response::{IntoResponse, Response},
    routing::Router,
};
use tokio::net::TcpListener;
use tower::ServiceBuilder;
use tower_http::trace::TraceLayer;
use tracing::{error, info};
use yawc::IncomingUpgrade;

mod config;
mod http;
mod logging;
mod state;
mod utils;
mod websocket;

use config::Config;
use state::{AppState, ClientStates};

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
            .build()?,
    };

    let app = Router::new()
        .route("/api/websocket", axum::routing::get(websocket::filtered::handler))
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

// Catch-all handler detects WebSocket upgrades and regular HTTP
async fn catchall_handler(
    upgrade: Result<IncomingUpgrade, axum::http::StatusCode>,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    State(state): State<AppState>,
    req: Request,
) -> Response {
    match upgrade {
        Ok(upgrade) => {
            websocket::transparent::handler(upgrade, ConnectInfo(addr), State(state), req).await
        }
        Err(_) => {
            // Not a WebSocket request, proxy as HTTP
            let client_ip = utils::get_client_ip(&req, addr);
            tracing::debug!("{} {} from {}", req.method(), req.uri().path(), client_ip);
            http::proxy::handle(req, state, client_ip)
                .await
                .into_response()
        }
    }
}

