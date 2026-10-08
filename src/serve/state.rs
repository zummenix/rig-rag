use std::pin::Pin;

use anyhow::Result;
use futures::{Stream, future::BoxFuture};

use shared::{ChatEvent, DocHit, Msg};

/// Boxed stream of chat events for a single stateless turn.
pub type ChatStream = Pin<Box<dyn Stream<Item = Result<ChatEvent>> + Send + 'static>>;

/// Retrieves document chunks relevant to a question.
///
/// Dyn-compatible so the router can hold an `Arc<dyn Retriever>`; production
/// wraps Qdrant, tests inject a mock.
pub trait Retriever: Send + Sync {
    /// Returns the `k` most similar chunks at or above `threshold`, best first.
    fn retrieve<'a>(
        &'a self,
        question: &'a str,
        k: u64,
        threshold: f32,
    ) -> BoxFuture<'a, Result<Vec<DocHit>>>;
}

/// Produces the model's answer stream for one turn.
///
/// The completer emits only answer events (`Thinking` / `Delta` / `Final` /
/// `Error`); the `Docs` event is emitted by the server after retrieval. It is
/// independent of rig types so tests can supply a mock.
pub trait Completer: Send + Sync {
    /// Starts a turn. `prompt` already carries the retrieved context.
    fn complete<'a>(
        &'a self,
        prompt: String,
        history: Vec<Msg>,
    ) -> BoxFuture<'a, Result<ChatStream>>;
}
