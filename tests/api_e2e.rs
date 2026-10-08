//! End-to-end API tests: the real axum [`router`] on port 0, driven over HTTP
//! with `reqwest`, against the mock `Retriever`/`Completer` (feature `mock`).
//!
//! Run with `cargo test --features mock`.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use eventsource_stream::Eventsource;
use futures::StreamExt;
use reqwest::StatusCode;
use rig_rag::serve::{
    self,
    mock::{MockCompleter, MockRetriever, mock_answer},
    state::AppState,
};
use shared::{ChatEvent, ChatRequest, DocHit, Msg, QueryRequest, Role};
use tokio::net::TcpListener;

/// Builds state with the mock retriever and the given completer.
fn state(completer: MockCompleter) -> AppState {
    AppState {
        retriever: Arc::new(MockRetriever::new()),
        completer: Arc::new(completer),
        // A directory that does not exist, so the router has no static fallback.
        site_dir: PathBuf::from("./rig-rag-no-such-site-dir"),
        keep_alive: Duration::from_secs(15),
    }
}

/// Binds the router on an ephemeral port and returns its base URL.
async fn spawn(completer: MockCompleter) -> String {
    let app = serve::router(state(completer));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://{addr}")
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
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.json::<Vec<DocHit>>().await.unwrap().len(), 3);
}

#[tokio::test]
async fn query_rejects_out_of_range_k() {
    let url = spawn(MockCompleter::new()).await;
    let client = reqwest::Client::new();
    for k in [0u64, 21] {
        let response = client
            .post(format!("{url}/api/query"))
            .json(&QueryRequest {
                question: "q".into(),
                k: Some(k),
            })
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "k={k}");
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
