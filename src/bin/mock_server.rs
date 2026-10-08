//! Serves the real API router backed by the mock `Retriever`/`Completer`.
//!
//! Launched by Playwright (`site/tests`) so the UI can be exercised without
//! Qdrant or OpenRouter. Build/run with `--features mock`.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use clap::Parser;
use rig_rag::serve::{self, mock, state::AppState};

#[derive(Parser)]
#[command(name = "mock_server", about = "rig-rag API with mock backends")]
struct Args {
    /// Address to bind, e.g. `127.0.0.1:8080`.
    #[arg(long, default_value = "127.0.0.1:8080")]
    bind: String,
    /// Directory of built UI assets to serve at `/`.
    #[arg(long, default_value = "./site/dist")]
    site_dir: PathBuf,
    /// SSE keep-alive interval in seconds.
    #[arg(long, default_value_t = 15)]
    keep_alive_secs: u64,
    /// Delay between mock events, in milliseconds. The UI e2e raises this so
    /// Playwright can observe the answer streaming in pieces.
    #[arg(long, default_value_t = 5)]
    event_delay_ms: u64,
    /// Fail the completer after this many answer deltas (default: never).
    #[arg(long)]
    fail_after: Option<usize>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    let completer = match args.fail_after {
        Some(n) => mock::MockCompleter::failing_after(n),
        None => mock::MockCompleter::new(),
    }
    .with_delay(Duration::from_millis(args.event_delay_ms));
    let state = AppState {
        retriever: Arc::new(mock::MockRetriever::new()),
        completer: Arc::new(completer),
        site_dir: args.site_dir,
        keep_alive: Duration::from_secs(args.keep_alive_secs),
    };

    let listener = tokio::net::TcpListener::bind(&args.bind).await?;
    println!("mock_server on http://{}", listener.local_addr()?);
    axum::serve(listener, serve::router(state)).await?;
    Ok(())
}
