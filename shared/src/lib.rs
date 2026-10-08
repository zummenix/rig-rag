//! Dependency-free wire types shared by the `rig-rag` server and the WASM UI.
//!
//! This crate must stay free of native-only dependencies (`rig`, `qdrant-client`,
//! `fastembed`, ...) so it compiles to `wasm32-unknown-unknown`. It carries only
//! the HTTP/SSE contract: request bodies, response types and the `ChatEvent`
//! stream schema.

use serde::{Deserialize, Serialize};

/// Default number of hits retrieved when a request omits `k`.
pub const DEFAULT_K: u64 = 7;
/// Inclusive lower bound for the `k` retrieval parameter.
pub const MIN_K: u64 = 1;
/// Inclusive upper bound for the `k` retrieval parameter.
pub const MAX_K: u64 = 20;

/// One retrieved document chunk, as seen by the model and the UI.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DocHit {
    /// Backend similarity score.
    pub score: f64,
    /// Document path.
    pub path: String,
    /// Start line of the chunk in the document.
    pub start_line: usize,
    /// End line of the chunk in the document.
    pub end_line: usize,
    /// Zero-based position in the chunk sequence.
    pub chunk_index: usize,
    /// Text of the chunk, including its section path preamble.
    pub text: String,
}

impl DocHit {
    /// Human-readable source location, e.g. `docs/faq.md:10-24`.
    pub fn source_location(&self) -> String {
        if self.start_line >= self.end_line {
            format!("{}:{}", self.path, self.start_line)
        } else {
            format!("{}:{}-{}", self.path, self.start_line, self.end_line)
        }
    }
}

/// Author of a [`Msg`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    User,
    Assistant,
}

/// One turn of the client-owned transcript.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Msg {
    pub role: Role,
    pub content: String,
}

/// Body of `POST /api/query`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueryRequest {
    pub question: String,
    /// Number of hits to retrieve; defaults to [`DEFAULT_K`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub k: Option<u64>,
}

/// Why a requested `k` is out of range.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KError {
    /// Below [`MIN_K`].
    TooSmall(u64),
    /// Above [`MAX_K`].
    TooLarge(u64),
}

impl std::fmt::Display for KError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TooSmall(k) => write!(f, "k must be >= {MIN_K}, got {k}"),
            Self::TooLarge(k) => write!(f, "k must be <= {MAX_K}, got {k}"),
        }
    }
}

impl std::error::Error for KError {}

impl QueryRequest {
    /// The effective `k`, defaulting to [`DEFAULT_K`] when omitted.
    ///
    /// Shared so the server and the UI enforce identical bounds.
    pub fn effective_k(&self) -> Result<u64, KError> {
        let k = self.k.unwrap_or(DEFAULT_K);
        if k < MIN_K {
            return Err(KError::TooSmall(k));
        }
        if k > MAX_K {
            return Err(KError::TooLarge(k));
        }
        Ok(k)
    }
}

/// Body of `POST /api/chat`. The client owns the transcript: each turn is a
/// pure function of `(history, prompt)`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatRequest {
    pub history: Vec<Msg>,
    pub prompt: String,
}

/// Body of `GET /api/health`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Health {
    pub status: String,
}

impl Health {
    /// The canonical healthy response.
    pub fn ok() -> Self {
        Self {
            status: "ok".to_owned(),
        }
    }
}

/// One server-sent event of the chat stream.
///
/// The SSE `event:` name is the variant tag; `data:` is the JSON encoding of
/// this enum. The `Unknown` catch-all keeps the client forward-compatible with
/// events it does not know.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChatEvent {
    /// The retrieved context, emitted once after retrieval.
    Docs { docs: Vec<DocHit> },
    /// A reasoning delta; may be absent.
    Thinking { text: String },
    /// An answer text delta.
    Delta { text: String },
    /// The full answer; the server ends the body after this.
    Final { text: String },
    /// A failure; the server ends the body after this.
    Error { message: String },
    /// Reserved for future tool use; never emitted in v1.
    ToolCall {
        id: String,
        name: String,
        arguments: String,
    },
    /// Reserved for future tool use; never emitted in v1.
    ToolResult { id: String, output: String },
    /// Catch-all for unrecognized event tags.
    #[serde(other)]
    Unknown,
}

impl ChatEvent {
    /// The SSE `event:` name for this variant.
    pub fn name(&self) -> &'static str {
        match self {
            Self::Docs { .. } => "docs",
            Self::Thinking { .. } => "thinking",
            Self::Delta { .. } => "delta",
            Self::Final { .. } => "final",
            Self::Error { .. } => "error",
            Self::ToolCall { .. } => "tool_call",
            Self::ToolResult { .. } => "tool_result",
            Self::Unknown => "unknown",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_hit() -> DocHit {
        DocHit {
            score: 0.83,
            path: "docs/faq.md".into(),
            start_line: 10,
            end_line: 24,
            chunk_index: 2,
            text: "FAQ > Auth\n\nTokens expire after 60 minutes.".into(),
        }
    }

    #[test]
    fn doc_hit_round_trip() {
        let hit = sample_hit();
        let json = serde_json::to_string(&hit).unwrap();
        assert_eq!(serde_json::from_str::<DocHit>(&json).unwrap(), hit);
    }

    #[test]
    fn doc_hit_source_location() {
        assert_eq!(sample_hit().source_location(), "docs/faq.md:10-24");
        let mut collapsed = sample_hit();
        collapsed.start_line = 5;
        collapsed.end_line = 5;
        assert_eq!(collapsed.source_location(), "docs/faq.md:5");
    }

    #[test]
    fn msg_round_trip_and_role_spelling() {
        let msg = Msg {
            role: Role::Assistant,
            content: "hi".into(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains("\"assistant\""));
        assert_eq!(serde_json::from_str::<Msg>(&json).unwrap(), msg);

        let user = Msg {
            role: Role::User,
            content: "q".into(),
        };
        assert!(serde_json::to_string(&user).unwrap().contains("\"user\""));
    }

    #[test]
    fn chat_event_round_trip() {
        let events = [
            ChatEvent::Docs {
                docs: vec![sample_hit()],
            },
            ChatEvent::Thinking { text: "hmm".into() },
            ChatEvent::Delta { text: "he".into() },
            ChatEvent::Final {
                text: "hello".into(),
            },
            ChatEvent::Error {
                message: "boom".into(),
            },
            ChatEvent::ToolCall {
                id: "1".into(),
                name: "read".into(),
                arguments: "{}".into(),
            },
            ChatEvent::ToolResult {
                id: "1".into(),
                output: "ok".into(),
            },
        ];
        for event in events {
            let json = serde_json::to_string(&event).unwrap();
            assert_eq!(serde_json::from_str::<ChatEvent>(&json).unwrap(), event);
        }
    }

    #[test]
    fn chat_event_tag_is_the_variant_name() {
        let event = ChatEvent::Delta { text: "x".into() };
        assert_eq!(event.name(), "delta");
        assert!(
            serde_json::to_string(&event)
                .unwrap()
                .starts_with(r#"{"type":"delta""#)
        );
    }

    #[test]
    fn chat_event_unknown_variant_is_safe() {
        let decoded: ChatEvent = serde_json::from_str(r#"{"type":"bogus","x":1}"#).unwrap();
        assert_eq!(decoded, ChatEvent::Unknown);
    }

    #[test]
    fn query_request_k_bounds() {
        let req = |k: Option<u64>| QueryRequest {
            question: "q".into(),
            k,
        };
        assert_eq!(req(None).effective_k().unwrap(), DEFAULT_K);
        assert_eq!(req(Some(MIN_K)).effective_k().unwrap(), MIN_K);
        assert_eq!(req(Some(MAX_K)).effective_k().unwrap(), MAX_K);
        assert_eq!(req(Some(0)).effective_k(), Err(KError::TooSmall(0)));
        assert_eq!(
            req(Some(MAX_K + 1)).effective_k(),
            Err(KError::TooLarge(21))
        );
    }

    #[test]
    fn query_request_round_trip_omits_missing_k() {
        let json = serde_json::to_string(&QueryRequest {
            question: "q".into(),
            k: None,
        })
        .unwrap();
        assert_eq!(json, r#"{"question":"q"}"#);
        let decoded: QueryRequest = serde_json::from_str(r#"{"question":"q"}"#).unwrap();
        assert_eq!(decoded.k, None);
    }

    #[test]
    fn chat_request_round_trip() {
        let req = ChatRequest {
            history: vec![Msg {
                role: Role::User,
                content: "hi".into(),
            }],
            prompt: "next".into(),
        };
        let json = serde_json::to_string(&req).unwrap();
        assert_eq!(serde_json::from_str::<ChatRequest>(&json).unwrap(), req);
    }

    #[test]
    fn health_ok() {
        assert_eq!(Health::ok().status, "ok");
    }
}
