//! Thin HTTP client for the server's API, returning plain `Result`s.
//!
//! Paths are resolved against the page origin so the same code works under
//! `trunk serve`'s proxy and when the axum server serves the built `dist/`.

use eventsource_stream::Eventsource;
use futures::stream::{LocalBoxStream, StreamExt};
use shared::{ChatEvent, ChatRequest, DocHit, Msg, QueryRequest};

/// A boxed, `!Send` stream of decoded chat events (one per SSE frame).
pub type ChatStream = LocalBoxStream<'static, Result<ChatEvent, String>>;

/// Absolute URL for an API path, derived from the page origin.
///
/// `reqwest` parses the URL eagerly and rejects relative paths ("builder
/// error"), so we prefix `window.location.origin`. The browser then routes it
/// to the same origin under both `trunk serve` (proxied) and the real server.
fn endpoint(path: &str) -> String {
    let origin = web_sys::window()
        .and_then(|window| window.location().origin().ok())
        .filter(|origin| !origin.is_empty() && origin != "null")
        .unwrap_or_default();
    format!("{origin}{path}")
}

/// `POST /api/query` — retrieve the `k` closest chunks.
pub async fn query(question: String, k: u64) -> Result<Vec<DocHit>, String> {
    let response = reqwest::Client::new()
        .post(endpoint("/api/query"))
        .json(&QueryRequest {
            question,
            k: Some(k),
        })
        .send()
        .await
        .map_err(|error| format!("request failed: {error}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!("server returned {status}: {body}"));
    }

    response
        .json::<Vec<DocHit>>()
        .await
        .map_err(|error| format!("invalid response: {error}"))
}

/// `POST /api/chat` — start a turn and return its streamed events.
///
/// The caller owns the transcript and passes the relevant `history` back each
/// turn; the server keeps no conversation state.
pub async fn chat(history: Vec<Msg>, prompt: String) -> Result<ChatStream, String> {
    let response = reqwest::Client::new()
        .post(endpoint("/api/chat"))
        .json(&ChatRequest { history, prompt })
        .send()
        .await
        .map_err(|error| format!("request failed: {error}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!("server returned {status}: {body}"));
    }

    let stream = response
        .bytes_stream()
        .eventsource()
        .map(|item| match item {
            Ok(event) => serde_json::from_str::<ChatEvent>(&event.data)
                .map_err(|error| format!("invalid event: {error}")),
            Err(error) => Err(format!("stream error: {error}")),
        });

    Ok(Box::pin(stream))
}
