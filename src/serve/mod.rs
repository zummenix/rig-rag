//! Web server (HTTP + SSE) for retrieval and RAG chat.
//!
//! Phase 0 defines only the seams: the [`state::Retriever`] / [`state::Completer`]
//! traits and the pure helpers in [`chat`]. The axum router lands in Phase 1.

pub mod chat;
pub mod state;
