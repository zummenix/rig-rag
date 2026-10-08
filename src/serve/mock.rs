//! Deterministic [`Retriever`] / [`Completer`] implementations for tests.
//!
//! Gated behind the `mock` feature. The API e2e test drives the real router
//! with these, and `mock_server` serves them to Playwright.

use std::time::Duration;

use anyhow::{Result, anyhow};
use futures::{StreamExt, future::BoxFuture};
use shared::{ChatEvent, DocHit, Msg};

use crate::serve::state::{ChatStream, Completer, Retriever};

/// Delay inserted between mock events so a client can observe streaming.
const MOCK_EVENT_DELAY: Duration = Duration::from_millis(5);

/// Reasoning deltas emitted before the answer.
pub const MOCK_THINKING: &[&str] = &["Let me ", "think. "];
/// Answer deltas; their concatenation is [`mock_answer`].
pub const MOCK_DELTAS: &[&str] = &["Hello", ", ", "mock ", "world", "!"];

/// The full mock answer: the concatenation of [`MOCK_DELTAS`].
pub fn mock_answer() -> String {
    MOCK_DELTAS.concat()
}

/// The canned hits returned by [`MockRetriever`], best first.
pub fn mock_docs() -> Vec<DocHit> {
    vec![
        DocHit {
            score: 0.91,
            path: "docs/auth.md".into(),
            start_line: 10,
            end_line: 18,
            chunk_index: 0,
            text: "Auth > Tokens\n\nAccess tokens expire after 60 minutes.".into(),
        },
        DocHit {
            score: 0.82,
            path: "docs/auth.md".into(),
            start_line: 20,
            end_line: 26,
            chunk_index: 1,
            text: "Auth > Refresh\n\nRefresh tokens rotate on every use.".into(),
        },
        DocHit {
            score: 0.71,
            path: "docs/faq.md".into(),
            start_line: 1,
            end_line: 6,
            chunk_index: 0,
            text: "FAQ > Login\n\nUse the login endpoint to obtain a token.".into(),
        },
    ]
}

/// Returns canned hits, ignoring the question; applies `threshold` then `k`.
#[derive(Default, Clone, Copy)]
pub struct MockRetriever;

impl MockRetriever {
    pub fn new() -> Self {
        Self
    }
}

impl Retriever for MockRetriever {
    fn retrieve<'a>(
        &'a self,
        _question: &'a str,
        k: u64,
        threshold: f32,
    ) -> BoxFuture<'a, Result<Vec<DocHit>>> {
        Box::pin(async move {
            let hits = mock_docs()
                .into_iter()
                .filter(|doc| doc.score >= f64::from(threshold))
                .take(k as usize)
                .collect();
            Ok(hits)
        })
    }
}

/// How [`MockCompleter`] should end its stream.
#[derive(Debug, Clone, Copy)]
enum MockMode {
    /// Deltas then `Final`.
    Happy,
    /// `n` deltas, then an `Err` (surfaced as a `ChatEvent::Error`).
    FailAfter(usize),
}

/// Emits [`MOCK_THINKING`], [`MOCK_DELTAS`], then `Final` (or an error).
pub struct MockCompleter {
    mode: MockMode,
    delay: Duration,
}

impl MockCompleter {
    /// A completer that ends with `Final { text: mock_answer() }`.
    pub fn new() -> Self {
        Self {
            mode: MockMode::Happy,
            delay: MOCK_EVENT_DELAY,
        }
    }

    /// A completer that emits `deltas` answer deltas, then fails.
    pub fn failing_after(deltas: usize) -> Self {
        Self {
            mode: MockMode::FailAfter(deltas),
            delay: MOCK_EVENT_DELAY,
        }
    }

    /// Overrides the per-event delay. The UI e2e raises it so a browser can
    /// observe the answer arriving in pieces rather than all at once.
    pub fn with_delay(mut self, delay: Duration) -> Self {
        self.delay = delay;
        self
    }
}

impl Default for MockCompleter {
    fn default() -> Self {
        Self::new()
    }
}

impl Completer for MockCompleter {
    fn complete<'a>(
        &'a self,
        _prompt: String,
        _history: Vec<Msg>,
    ) -> BoxFuture<'a, Result<ChatStream>> {
        let mode = self.mode;
        let delay = self.delay;
        Box::pin(async move {
            let mut events: Vec<Result<ChatEvent>> = Vec::new();
            for text in MOCK_THINKING {
                events.push(Ok(ChatEvent::Thinking {
                    text: (*text).to_owned(),
                }));
            }
            match mode {
                MockMode::Happy => {
                    for text in MOCK_DELTAS {
                        events.push(Ok(ChatEvent::Delta {
                            text: (*text).to_owned(),
                        }));
                    }
                    events.push(Ok(ChatEvent::Final {
                        text: mock_answer(),
                    }));
                }
                MockMode::FailAfter(n) => {
                    for text in MOCK_DELTAS.iter().take(n) {
                        events.push(Ok(ChatEvent::Delta {
                            text: (*text).to_owned(),
                        }));
                    }
                    events.push(Err(anyhow!("mock completer failed after {n} deltas")));
                }
            }

            // `then` drives the futures sequentially, so each event waits for
            // the previous delay before yielding.
            let stream = futures::stream::iter(events).then(move |event| async move {
                tokio::time::sleep(delay).await;
                event
            });
            Ok(Box::pin(stream) as ChatStream)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn retriever_honours_threshold_and_k() {
        let retriever = MockRetriever::new();
        let hits = retriever.retrieve("q", 2, 0.5).await.unwrap();
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].path, "docs/auth.md");
        assert_eq!(hits[1].start_line, 20);
    }

    #[tokio::test]
    async fn retriever_threshold_filters_everything() {
        let retriever = MockRetriever::new();
        let hits = retriever.retrieve("q", 10, 0.99).await.unwrap();
        assert!(hits.is_empty());
    }

    #[tokio::test]
    async fn happy_completer_emits_thinking_deltas_then_final() {
        let events = collect(MockCompleter::new()).await;
        let names: Vec<_> = events.iter().map(|(name, _)| *name).collect();
        assert_eq!(
            names,
            [
                "thinking", "thinking", "delta", "delta", "delta", "delta", "delta", "final"
            ]
        );

        let deltas: String = events
            .iter()
            .filter_map(|(name, event)| match (name, event) {
                (&"delta", ChatEvent::Delta { text }) => Some(text.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(deltas, mock_answer());

        let final_text = events.iter().find_map(|(_, event)| match event {
            ChatEvent::Final { text } => Some(text.as_str()),
            _ => None,
        });
        assert_eq!(final_text, Some(mock_answer().as_str()));
    }

    #[tokio::test]
    async fn failing_completer_stops_after_n_deltas() {
        let events = collect(MockCompleter::failing_after(1)).await;
        let names: Vec<_> = events.iter().map(|(name, _)| *name).collect();
        assert_eq!(names, ["thinking", "thinking", "delta", "error"]);
    }

    /// Drains a completer's stream, returning `(name, event)` pairs in order.
    ///
    /// Errors are mapped to [`ChatEvent::Error`], matching how the server
    /// surfaces a completer failure.
    async fn collect(completer: MockCompleter) -> Vec<(&'static str, ChatEvent)> {
        let stream = completer.complete("q".into(), Vec::new()).await.unwrap();
        stream
            .map(|item| {
                item.unwrap_or_else(|error| ChatEvent::Error {
                    message: error.to_string(),
                })
            })
            .collect::<Vec<_>>()
            .await
            .into_iter()
            .map(|event| (event.name(), event))
            .collect()
    }
}
