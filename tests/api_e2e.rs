//! End-to-end API tests: the real axum [`router`] on port 0, driven over HTTP
//! with `reqwest`, against the mock `Retriever`/`Completer` (feature `mock`).
//!
//! Run with `cargo test --features mock`.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use eventsource_stream::Eventsource;
use futures::{StreamExt, future::BoxFuture};
use reqwest::StatusCode;
use rig_rag::serve::{
    self,
    mock::{MockCompleter, MockRetriever, mock_answer},
    state::{AppState, Completer, Retriever},
};
use shared::{ChatEvent, ChatRequest, DocHit, Msg, QueryRequest, Role, SearchMode};
use tokio::net::TcpListener;

/// A [`Retriever`] that records the `mode` of every call and delegates the
/// hits to [`MockRetriever`], so tests can assert the request body reached
/// retrieval unchanged without duplicating the mock's hit selection.
#[derive(Clone, Default)]
struct RecordingRetriever {
    modes: Arc<std::sync::Mutex<Vec<SearchMode>>>,
    inner: MockRetriever,
}

impl RecordingRetriever {
    /// The modes captured so far, in call order.
    fn modes(&self) -> Vec<SearchMode> {
        self.modes.lock().unwrap().clone()
    }
}

impl Retriever for RecordingRetriever {
    fn retrieve<'a>(
        &'a self,
        question: &'a str,
        k: u64,
        mode: SearchMode,
    ) -> BoxFuture<'a, anyhow::Result<Vec<DocHit>>> {
        self.modes.lock().unwrap().push(mode);
        self.inner.retrieve(question, k, mode)
    }
}

/// Builds state around a retriever and an optional completer.
fn state(retriever: Arc<dyn Retriever>, completer: Option<Arc<dyn Completer>>) -> AppState {
    AppState {
        retriever,
        completer,
        // A directory that does not exist, so the router has no static fallback.
        site_dir: PathBuf::from("./rig-rag-no-such-site-dir"),
        keep_alive: Duration::from_secs(15),
    }
}

/// Binds the router around `state`, returning its base URL.
async fn spawn_state(state: AppState) -> String {
    let app = serve::router(state);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://{addr}")
}

/// Binds the router on an ephemeral port and returns its base URL.
async fn spawn(completer: MockCompleter) -> String {
    spawn_with(Arc::new(MockRetriever::new()), completer).await
}

/// Binds the router around a specific retriever and completer.
async fn spawn_with(retriever: Arc<dyn Retriever>, completer: MockCompleter) -> String {
    spawn_state(state(retriever, Some(Arc::new(completer)))).await
}

/// Binds the router around a retriever with chat disabled.
async fn spawn_without_completer(retriever: Arc<dyn Retriever>) -> String {
    spawn_state(state(retriever, None)).await
}

/// Reads a chat turn to completion, returning `(event_name, event)` pairs.
async fn read_chat_events(url: &str, body: &ChatRequest) -> Vec<(String, ChatEvent)> {
    let response = reqwest::Client::new()
        .post(format!("{url}/api/chat"))
        .json(body)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let mut stream = response.bytes_stream().eventsource();
    let mut events = Vec::new();
    while let Some(event) = stream.next().await {
        let event = event.expect("well-formed SSE frame");
        let parsed: ChatEvent = serde_json::from_str(&event.data).expect("chat event JSON decodes");
        events.push((event.event, parsed));
    }
    events
}

#[tokio::test]
async fn health_returns_ok() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .get(format!("{url}/api/health"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.json::<serde_json::Value>().await.unwrap()["status"],
        "ok"
    );
}

#[tokio::test]
async fn query_returns_canned_hits() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/query"))
        .json(&QueryRequest {
            question: "how do tokens work?".into(),
            k: Some(2),
            mode: None,
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let hits: Vec<DocHit> = response.json().await.unwrap();
    assert_eq!(hits.len(), 2);
    assert!(hits[0].score >= hits[1].score);
    assert_eq!(hits[0].path, "docs/auth.md");
    assert_eq!(hits[1].start_line, 20);
}

#[tokio::test]
async fn query_more_than_available_returns_all() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/query"))
        .json(&QueryRequest {
            question: "q".into(),
            k: Some(20),
            mode: None,
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.json::<Vec<DocHit>>().await.unwrap().len(), 3);
}

#[tokio::test]
async fn query_clamps_out_of_range_k() {
    let url = spawn(MockCompleter::new()).await;
    let client = reqwest::Client::new();
    for (k, expected_hits) in [(-1i64, 1), (0, 1), (21, 3)] {
        let response = client
            .post(format!("{url}/api/query"))
            .json(&QueryRequest {
                question: "q".into(),
                k: Some(k),
                mode: None,
            })
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "k={k}");
        assert_eq!(
            response.json::<Vec<DocHit>>().await.unwrap().len(),
            expected_hits,
            "k={k}"
        );
    }
}

#[tokio::test]
async fn query_rejects_malformed_body() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/query"))
        .header("content-type", "application/json")
        .body("{ not json")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn query_defaults_to_hybrid_and_forwards_the_requested_mode() {
    let retriever = RecordingRetriever::default();
    let url = spawn_with(Arc::new(retriever.clone()), MockCompleter::new()).await;
    let client = reqwest::Client::new();

    for mode in [None, Some(SearchMode::Dense), Some(SearchMode::Sparse)] {
        let response = client
            .post(format!("{url}/api/query"))
            .json(&QueryRequest {
                question: "q".into(),
                k: Some(1),
                mode,
            })
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "mode={mode:?}");
    }

    assert_eq!(
        retriever.modes(),
        [SearchMode::Hybrid, SearchMode::Dense, SearchMode::Sparse]
    );
}

#[tokio::test]
async fn query_rejects_an_unknown_mode() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/query"))
        .header("content-type", "application/json")
        .body(r#"{"question":"q","mode":"fuzzy"}"#)
        .send()
        .await
        .unwrap();
    // Well-formed JSON that fails to deserialize is a 422, not a 400.
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn chat_streams_docs_thinking_deltas_then_final() {
    let url = spawn(MockCompleter::new()).await;
    let events = read_chat_events(
        &url,
        &ChatRequest {
            history: vec![Msg {
                role: Role::User,
                content: "earlier".into(),
            }],
            prompt: "hello".into(),
        },
    )
    .await;

    let names: Vec<&str> = events.iter().map(|(name, _)| name.as_str()).collect();
    assert_eq!(names[0], "docs");
    assert_eq!(names.last().copied(), Some("final"));

    // docs → thinking* → delta* → final, in that order.
    let first_delta = names.iter().position(|n| *n == "delta").unwrap();
    let final_at = names.iter().position(|n| *n == "final").unwrap();
    assert!(
        names[..first_delta]
            .iter()
            .all(|n| *n == "docs" || *n == "thinking")
    );
    assert!(names[first_delta..final_at].iter().all(|n| *n == "delta"));

    let docs = match &events[0].1 {
        ChatEvent::Docs { docs } => docs,
        other => panic!("first event was {other:?}"),
    };
    assert_eq!(docs.len(), 3);

    let deltas: String = events
        .iter()
        .filter_map(|(name, event)| match (name.as_str(), event) {
            ("delta", ChatEvent::Delta { text }) => Some(text.as_str()),
            _ => None,
        })
        .collect();
    let final_text = events
        .iter()
        .find_map(|(name, event)| match (name.as_str(), event) {
            ("final", ChatEvent::Final { text }) => Some(text.as_str()),
            _ => None,
        });
    assert_eq!(deltas, mock_answer());
    assert_eq!(final_text, Some(mock_answer().as_str()));
}

#[tokio::test]
async fn chat_rejects_malformed_body() {
    let url = spawn(MockCompleter::new()).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/chat"))
        .header("content-type", "application/json")
        .body("nope")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn chat_always_retrieves_with_hybrid() {
    let retriever = RecordingRetriever::default();
    let url = spawn_with(Arc::new(retriever.clone()), MockCompleter::new()).await;

    let events = read_chat_events(
        &url,
        &ChatRequest {
            history: Vec::new(),
            prompt: "hello".into(),
        },
    )
    .await;
    assert_eq!(events.first().map(|(name, _)| name.as_str()), Some("docs"));

    assert_eq!(retriever.modes(), [SearchMode::Hybrid]);
}

#[tokio::test]
async fn chat_error_after_first_delta_ends_body_cleanly() {
    let url = spawn(MockCompleter::failing_after(1)).await;
    let events = read_chat_events(
        &url,
        &ChatRequest {
            history: Vec::new(),
            prompt: "hello".into(),
        },
    )
    .await;

    let names: Vec<&str> = events.iter().map(|(name, _)| name.as_str()).collect();
    assert_eq!(names[0], "docs");
    assert_eq!(names.last().copied(), Some("error"));
    assert_eq!(names.iter().filter(|n| **n == "delta").count(), 1);
    assert!(!names.contains(&"final"), "a failed turn emits no Final");

    match events.last().unwrap().1 {
        ChatEvent::Error { ref message } => assert!(message.contains("after 1 deltas")),
        ref other => panic!("last event was {other:?}"),
    }
}

#[tokio::test]
async fn chat_survives_client_disconnect() {
    let url = spawn(MockCompleter::new()).await;
    let client = reqwest::Client::new();

    let response = client
        .post(format!("{url}/api/chat"))
        .json(&ChatRequest {
            history: Vec::new(),
            prompt: "hello".into(),
        })
        .send()
        .await
        .unwrap();
    let mut stream = response.bytes_stream().eventsource();
    let first = stream.next().await.unwrap().unwrap();
    assert_eq!(first.event, "docs");
    // Drop the response mid-stream, closing the connection before `final`.
    drop(stream);

    // The server task must still be serving other requests.
    tokio::time::sleep(Duration::from_millis(50)).await;
    let health = client
        .get(format!("{url}/api/health"))
        .send()
        .await
        .unwrap();
    assert_eq!(health.status(), StatusCode::OK);
}

#[tokio::test]
async fn chat_returns_503_when_unconfigured() {
    let retriever = RecordingRetriever::default();
    let url = spawn_without_completer(Arc::new(retriever.clone())).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/chat"))
        .json(&ChatRequest {
            history: Vec::new(),
            prompt: "hello".into(),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = response.text().await.unwrap();
    assert!(body.contains("chat is not configured"), "body: {body}");
    assert!(body.contains("[llm]"), "body names the fix: {body}");
    assert!(
        retriever.modes().is_empty(),
        "the 503 must be returned before retrieval"
    );
}

#[tokio::test]
async fn query_works_without_llm() {
    let url = spawn_without_completer(Arc::new(MockRetriever::new())).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/api/query"))
        .json(&QueryRequest {
            question: "q".into(),
            k: Some(2),
            mode: None,
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.json::<Vec<DocHit>>().await.unwrap().len(), 2);
}

#[tokio::test]
async fn first_turn_error_names_provider_model_and_base_url() {
    // An OpenAI-compatible endpoint that always fails, so the error context
    // added by `RigCompleter` is what the client sees.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let app = axum::Router::new().route(
            "/v1/chat/completions",
            axum::routing::post(|| async {
                (axum::http::StatusCode::INTERNAL_SERVER_ERROR, "boom")
            }),
        );
        axum::serve(listener, app).await.unwrap();
    });

    let llm = rig_rag::config::LlmConfig {
        provider: rig_rag::config::LlmProvider::Custom,
        model: "test-model".into(),
        base_url: Some(format!("http://{addr}/v1")),
    };
    let completer = serve::prod::RigCompleter::connect(&llm).unwrap();
    let mut stream = completer.complete("hi".into(), Vec::new()).await.unwrap();
    let error = stream
        .next()
        .await
        .expect("the failing provider must yield an item")
        .expect_err("the provider returns 500");
    let message = format!("{error:#}");
    assert!(message.contains("provider=custom"), "message: {message}");
    assert!(message.contains("model=test-model"), "message: {message}");
    assert!(
        message.contains(&format!("base_url=http://{addr}/v1")),
        "message: {message}"
    );
}
