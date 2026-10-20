//! Optional cloud reranker for fused retrieval candidates.
//!
//! The app speaks the Jina-shaped `/v1/rerank` contract: `{base_url}/rerank`
//! receives `{model, query, documents}` and answers with
//! `results: [{index, relevance_score}]`. The authorization token is read from
//! [`crate::config::RERANKER_API_KEY`], never from the tracked config.
//!
//! Calls are best-effort: [`Reranker::rerank`] returns an error on any protocol
//! or transport failure. Documents are sent in the order the store supplies
//! them (the fused order), so provider indexes map directly back to it.

use std::collections::HashSet;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};

use crate::config::RerankerConfig;
use crate::eval_metrics::{self, RerankStatus, Usage};

/// A scored input document, in the provider's relevance order.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Ranked {
    /// Index into the document list given to [`Reranker::rerank`].
    pub index: usize,
    /// Provider-specific relevance score; only comparable within one response.
    pub score: f32,
}

/// A configured cloud reranker.
pub struct Reranker {
    http: reqwest::Client,
    endpoint: String,
    model: String,
    token: String,
    candidates: u64,
    log_eval_metrics: bool,
}

impl Reranker {
    /// Builds the client from config, reading the token and failing fast when
    /// it is missing. `log_eval_metrics` enables structured per-call logging.
    pub fn from_config(config: &RerankerConfig, log_eval_metrics: bool) -> Result<Self> {
        if config.candidates == 0 {
            bail!("[reranker].candidates must be greater than zero");
        }
        if config.timeout_ms == 0 {
            bail!("[reranker].timeout_ms must be greater than zero");
        }
        let token = config.api_key()?;
        Self::new(
            &config.base_url,
            &config.model,
            token,
            config.candidates,
            Duration::from_millis(config.timeout_ms),
            log_eval_metrics,
        )
    }

    fn new(
        base_url: &str,
        model: &str,
        token: String,
        candidates: u64,
        timeout: Duration,
        log_eval_metrics: bool,
    ) -> Result<Self> {
        let http = reqwest::Client::builder()
            .timeout(timeout)
            .build()
            .context("failed to build reranker HTTP client")?;
        Ok(Self {
            http,
            endpoint: format!("{}/rerank", base_url.trim_end_matches('/')),
            model: model.to_owned(),
            token,
            candidates,
            log_eval_metrics,
        })
    }

    /// Fused candidates to request before cutting to `k`.
    pub fn candidates(&self) -> u64 {
        self.candidates
    }

    /// Scores `documents` against `question`, best first. Every call reaches
    /// the provider; a measured `rerank` line is logged when eval metrics are on
    /// (on both success and failure).
    pub async fn rerank(&self, question: &str, documents: &[String]) -> Result<Vec<Ranked>> {
        let started = Instant::now();
        match self.score(question, documents).await {
            Ok((ranked, usage)) => {
                if self.log_eval_metrics {
                    eval_metrics::log_rerank(
                        question,
                        &self.model,
                        documents.len(),
                        ranked.len(),
                        started.elapsed(),
                        RerankStatus::Success,
                        usage.as_ref(),
                        None,
                    );
                }
                Ok(ranked)
            }
            Err(error) => {
                if self.log_eval_metrics {
                    eval_metrics::log_rerank(
                        question,
                        &self.model,
                        documents.len(),
                        0,
                        started.elapsed(),
                        RerankStatus::Failed,
                        None,
                        Some(&format!("{error:#}")),
                    );
                } else {
                    // Preserve the operator-visible warning when metrics are off.
                    eprintln!("serve: rerank failed (falling back to fusion): {error:#}");
                }
                Err(error)
            }
        }
    }

    async fn score(
        &self,
        question: &str,
        documents: &[String],
    ) -> Result<(Vec<Ranked>, Option<Usage>)> {
        let body = RequestBody {
            model: &self.model,
            query: question,
            documents,
        };
        let response = self
            .http
            .post(&self.endpoint)
            .bearer_auth(&self.token)
            .json(&body)
            .send()
            .await
            .with_context(|| format!("rerank request to {} failed", self.endpoint))?;

        let status = response.status();
        let raw = response
            .text()
            .await
            .context("failed to read rerank response body")?;
        if !status.is_success() {
            bail!("rerank endpoint returned {status}: {}", body_excerpt(&raw));
        }

        let reply: Reply = serde_json::from_str(&raw).context("failed to parse rerank response")?;
        let ranked = rank(reply.results, documents.len())?;
        Ok((ranked, reply.usage))
    }
}

/// Request body: the Jina / llama.cpp `/v1/rerank` shape.
#[derive(Serialize)]
struct RequestBody<'a> {
    model: &'a str,
    query: &'a str,
    documents: &'a [String],
}

#[derive(Deserialize)]
struct Reply {
    results: Vec<ReplyItem>,
    #[serde(default)]
    usage: Option<Usage>,
}

#[derive(Deserialize)]
struct ReplyItem {
    index: usize,
    relevance_score: f64,
}

/// Sorts, de-duplicates, and validates provider results. Out-of-range indexes
/// are dropped; a reply that ranks none of the inputs is a protocol error.
fn rank(items: Vec<ReplyItem>, document_count: usize) -> Result<Vec<Ranked>> {
    let mut seen = HashSet::new();
    let mut ranked: Vec<Ranked> = items
        .into_iter()
        .filter(|item| item.index < document_count && seen.insert(item.index))
        .map(|item| Ranked {
            index: item.index,
            score: item.relevance_score as f32,
        })
        .collect();

    if ranked.is_empty() && document_count > 0 {
        bail!("rerank response ranked none of the {document_count} documents");
    }
    ranked.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.index.cmp(&b.index))
    });
    Ok(ranked)
}

/// A bounded error-body excerpt, keeping logs readable on huge provider errors.
fn body_excerpt(body: &str) -> String {
    const MAX: usize = 200;
    let body = body.trim();
    if body.len() <= MAX {
        return body.to_owned();
    }
    let mut end = MAX;
    while !body.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...", &body[..end])
}

#[cfg(test)]
mod tests {
    use super::*;

    const REPLY: &str = r#"{"model":"rerank-3","results":[{"index":2,"relevance_score":0.64,"document":{"text":"c"}},{"index":0,"relevance_score":0.82,"document":{"text":"a"}},{"index":1,"relevance_score":0.71}],"usage":{"total_tokens":39,"cost":0.000351,"cost_rub":0.03,"balance":613.7},"id":"gen-1"}"#;

    fn ranked(reply: &str, count: usize) -> Result<Vec<Ranked>> {
        let reply: Reply = serde_json::from_str(reply).unwrap();
        rank(reply.results, count)
    }

    #[test]
    fn parses_orders_and_ignores_provider_extras() {
        let items = ranked(REPLY, 3).unwrap();
        assert_eq!(
            items,
            vec![
                Ranked {
                    index: 0,
                    score: 0.82
                },
                Ranked {
                    index: 1,
                    score: 0.71
                },
                Ranked {
                    index: 2,
                    score: 0.64
                },
            ]
        );
    }

    #[test]
    fn drops_out_of_range_and_duplicate_indexes() {
        let reply = r#"{"results":[{"index":3,"relevance_score":0.9},{"index":1,"relevance_score":0.8},{"index":1,"relevance_score":0.7}]}"#;
        assert_eq!(
            ranked(reply, 2).unwrap(),
            vec![Ranked {
                index: 1,
                score: 0.8
            }]
        );
    }

    #[test]
    fn rejects_a_reply_that_ranks_nobody() {
        assert!(ranked(r#"{"results":[]}"#, 3).is_err());
        assert!(ranked(r#"{"results":[{"index":9,"relevance_score":1.0}]}"#, 3).is_err());
    }

    #[tokio::test]
    async fn posts_the_jina_shape_in_fused_order_without_caching() {
        use axum::{
            Json, Router,
            extract::State,
            http::{HeaderMap, StatusCode},
            routing::post,
        };
        use std::sync::Arc;
        use std::sync::atomic::{AtomicUsize, Ordering};

        #[derive(Clone)]
        struct Seen {
            calls: Arc<AtomicUsize>,
        }

        let calls = Arc::new(AtomicUsize::new(0));
        let seen = Seen {
            calls: calls.clone(),
        };
        let app = Router::new()
            .route(
                "/v1/rerank",
                post(
                    |State(seen): State<Seen>,
                     headers: HeaderMap,
                     Json(body): Json<serde_json::Value>| async move {
                        seen.calls.fetch_add(1, Ordering::SeqCst);
                        assert_eq!(
                            headers
                                .get("authorization")
                                .and_then(|value| value.to_str().ok()),
                            Some("Bearer secret")
                        );
                        assert_eq!(body["model"], "rerank-3");
                        assert_eq!(body["query"], "question");
                        assert_eq!(body["documents"], serde_json::json!(["c", "b", "a"]));
                        (
                            StatusCode::OK,
                            Json(serde_json::json!({
                                "results": [
                                    {"index": 2, "relevance_score": 0.9},
                                    {"index": 0, "relevance_score": 0.1}
                                ]
                            })),
                        )
                    },
                ),
            )
            .with_state(seen);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

        let reranker = Reranker::new(
            &format!("http://{address}/v1"),
            "rerank-3",
            "secret".to_owned(),
            30,
            Duration::from_secs(2),
            false,
        )
        .unwrap();
        let documents = vec!["c".to_owned(), "b".to_owned(), "a".to_owned()];

        let first = reranker.rerank("question", &documents).await.unwrap();
        let second = reranker.rerank("question", &documents).await.unwrap();

        let reranked_texts = |ranked: &[Ranked]| {
            ranked
                .iter()
                .map(|item| documents[item.index].clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(reranked_texts(&first), vec!["a", "c"]);
        assert_eq!(reranked_texts(&second), vec!["a", "c"]);
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        server.abort();
    }
}
