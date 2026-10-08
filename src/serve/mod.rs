//! Web server (HTTP + SSE) for retrieval and RAG chat.
//!
//! [`router`] wires the API onto an [`AppState`]; [`run`] loads config, builds
//! the production [`state::Retriever`] / [`state::Completer`], and serves it.

pub mod chat;
pub mod health;
pub mod prod;
pub mod query;
pub mod state;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use axum::{
    Router,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use tower_http::services::ServeDir;

use crate::config::Config;
use state::AppState;

/// Default address `serve` binds when no flag or config value is given.
pub const DEFAULT_BIND: &str = "127.0.0.1:8080";
/// Default directory of built UI assets.
pub const DEFAULT_SITE_DIR: &str = "./site/dist";
/// Default SSE keep-alive interval.
pub const DEFAULT_KEEP_ALIVE: Duration = Duration::from_secs(15);

/// Flags for the `serve` subcommand; each falls back to `[server]`, then a default.
pub struct ServeArgs {
    pub bind: Option<String>,
    pub site_dir: Option<PathBuf>,
}

/// Loads config, connects the production impls (failing fast), and serves.
pub async fn run(args: ServeArgs) -> Result<()> {
    let config = Config::load()?;
    let server = config.server.as_ref();

    let bind = args
        .bind
        .or_else(|| server.and_then(|s| s.bind.clone()))
        .unwrap_or_else(|| DEFAULT_BIND.to_owned());
    let site_dir = args
        .site_dir
        .or_else(|| server.and_then(|s| s.site_dir.clone()))
        .unwrap_or_else(|| PathBuf::from(DEFAULT_SITE_DIR));
    let keep_alive = server
        .and_then(|s| s.keep_alive_secs)
        .map(Duration::from_secs)
        .unwrap_or(DEFAULT_KEEP_ALIVE);

    let retriever = prod::QdrantRetriever::connect(&config).await?;
    let completer = prod::RigCompleter::connect()?;

    let app = router(AppState {
        retriever: Arc::new(retriever),
        completer: Arc::new(completer),
        site_dir: site_dir.clone(),
        keep_alive,
    });

    let listener = tokio::net::TcpListener::bind(&bind).await?;
    println!(
        "rig-rag serving on http://{} (site: {})",
        listener.local_addr()?,
        site_dir.display()
    );
    axum::serve(listener, app).await?;
    Ok(())
}

/// Builds the API router, adding static-file serving when `site_dir` exists.
pub fn router(state: AppState) -> Router {
    let site_dir = state.site_dir.clone();
    let app = Router::new()
        .route("/api/health", get(health::handler))
        .route("/api/query", post(query::handler))
        .route("/api/chat", post(chat::handler))
        .with_state(state);

    if site_dir.is_dir() {
        app.fallback_service(ServeDir::new(site_dir))
    } else {
        app
    }
}

/// Logs `error` and returns an opaque `500` for a failure outside the stream.
pub(crate) fn internal_error(error: anyhow::Error) -> Response {
    eprintln!("serve: request failed: {error:#}");
    (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
}
