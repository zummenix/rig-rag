//! Optional cloud reranker for fused retrieval candidates.
//!
//! The app speaks the Jina-shaped `/v1/rerank` contract: `{base_url}/rerank`
//! receives `{model, query, documents}` and answers with
//! `results: [{index, relevance_score}]`. The authorization token is read from
//! [`crate::config::RERANKER_API_KEY`], never from the tracked config.
//!
//! Calls are best-effort: [`Reranker::rerank`] returns an error on any protocol
//! or transport failure.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::RerankerConfig;

/// Maximum number of query→ranking entries kept when caching is enabled.
const CACHE_CAPACITY: usize = 256;

/// A scored input document, in the provider's relevance order.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Ranked {
    /// Index into the document list given to [`Reranker::rerank`].
    pub index: usize,
    /// Provider-specific relevance score; only comparable within one response.
    pub score: f32,
}

/// A configured cloud reranker and, optionally, a score cache.
pub struct Reranker {
    http: reqwest::Client,
    endpoint: String,
    model: String,
    token: String,
    candidates: u64,
    cache: Option<Mutex<ScoreCache>>,
}

impl Reranker {
    /// Builds the client from config, reading the token and failing fast when
    /// it is missing. `cache` enables the bounded score cache.
    pub fn from_config(config: &RerankerConfig, cache: bool) -> Result<Self> {
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
            cache.then(|| Mutex::new(ScoreCache::new(CACHE_CAPACITY))),
        )
    }

    fn new(
        base_url: &str,
        model: &str,
        token: String,
        candidates: u64,
        timeout: Duration,
        cache: Option<Mutex<ScoreCache>>,
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
            cache,
        })
    }

    /// Fused candidates to request before cutting to `k`.
    pub fn candidates(&self) -> u64 {
        self.candidates
    }

    /// Scores `documents` against `question`, best first. Results are cached
    /// when enabled; cache hits never reach the provider.
    ///
    /// Candidates are sorted before hashing and requesting, because Qdrant's
    /// fused order is not guaranteed stable across identical queries. Cached
    /// indexes are in sorted space and mapped back to the caller's order.
    pub async fn rerank(&self, question: &str, documents: &[String]) -> Result<Vec<Ranked>> {
        let order = canonical_order(documents);
        let sorted: Vec<&str> = order
            .iter()
            .map(|&index| documents[index].as_str())
            .collect();
        let key = cache_key(&self.model, question, &sorted);
        if let Some(cached) = self.cached(key) {
            return Ok(to_original(&order, cached));
        }

        let started = Instant::now();
        let body = RequestBody {
            model: &self.model,
            query: question,
            documents: &sorted,
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
        let ranked = rank(reply.results, sorted.len())?;
        log_call(
            &self.model,
            documents.len(),
            ranked.len(),
            started.elapsed(),
            reply.usage.as_ref(),
        );

        if let Some(cache) = &self.cache {
            cache
                .lock()
                .expect("rerank cache lock poisoned")
                .insert(key, ranked.clone());
        }
        Ok(to_original(&order, ranked))
    }

    fn cached(&self, key: [u8; 32]) -> Option<Vec<Ranked>> {
        let cache = self.cache.as_ref()?;
        cache.lock().expect("rerank cache lock poisoned").get(key)
    }
}

/// Request body: the Jina / llama.cpp `/v1/rerank` shape.
#[derive(Serialize)]
struct RequestBody<'a> {
    model: &'a str,
    query: &'a str,
    documents: &'a [&'a str],
}

/// Indexes into `documents` ordered by text, tie-broken by original index.
fn canonical_order(documents: &[String]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..documents.len()).collect();
    order.sort_by(|&a, &b| documents[a].cmp(&documents[b]).then(a.cmp(&b)));
    order
}

/// Maps indexes in sorted-candidate space back to the caller's order.
fn to_original(order: &[usize], ranked: Vec<Ranked>) -> Vec<Ranked> {
    ranked
        .into_iter()
        .map(|item| Ranked {
            index: order[item.index],
            score: item.score,
        })
        .collect()
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

/// Only the fields this app reports are read; provider extras (`document`,
/// `id`, `balance`, ...) are ignored.
#[derive(Deserialize)]
struct Usage {
    #[serde(default)]
    total_tokens: Option<u64>,
    #[serde(default)]
    cost: Option<f64>,
    #[serde(default)]
    cost_rub: Option<f64>,
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

/// One compact line per real provider call, for the eval's cost aggregation.
/// The format is intentionally stable: `rerank model=... docs=... results=...
/// duration_ms=... tokens=... cost=... cost_rub=...`.
fn log_call(
    model: &str,
    documents: usize,
    results: usize,
    elapsed: Duration,
    usage: Option<&Usage>,
) {
    let mut line = format!(
        "rerank model={model} docs={documents} results={results} duration_ms={}",
        elapsed.as_millis()
    );
    if let Some(usage) = usage {
        if let Some(tokens) = usage.total_tokens {
            line.push_str(&format!(" tokens={tokens}"));
        }
        if let Some(cost) = usage.cost {
            line.push_str(&format!(" cost={cost}"));
        }
        if let Some(cost) = usage.cost_rub {
            line.push_str(&format!(" cost_rub={cost}"));
        }
    }
    eprintln!("{line}");
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

/// Cache key over the model, question, and ordered candidate texts. Length
/// prefixes keep concatenated documents unambiguous.
fn cache_key(model: &str, question: &str, documents: &[&str]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    for field in std::iter::once(model)
        .chain(std::iter::once(question))
        .chain(documents.iter().copied())
    {
        hasher.update((field.len() as u64).to_le_bytes());
        hasher.update(field.as_bytes());
    }
    hasher.finalize().into()
}

/// FIFO-bounded score cache. The active collection and model are fixed for a
/// server's lifetime, so entries never go stale; eviction only bounds memory.
struct ScoreCache {
    capacity: usize,
    entries: HashMap<[u8; 32], Vec<Ranked>>,
    order: VecDeque<[u8; 32]>,
}

impl ScoreCache {
    fn new(capacity: usize) -> Self {
        Self {
            capacity,
            entries: HashMap::new(),
            order: VecDeque::new(),
        }
    }

    fn get(&self, key: [u8; 32]) -> Option<Vec<Ranked>> {
        self.entries.get(&key).cloned()
    }

    fn insert(&mut self, key: [u8; 32], ranked: Vec<Ranked>) {
        if self.entries.contains_key(&key) {
            return;
        }
        while self.order.len() >= self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.entries.remove(&oldest);
            }
        }
        self.order.push_back(key);
        self.entries.insert(key, ranked);
    }
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

    #[test]
    fn cache_key_separates_document_boundaries() {
        let left = cache_key("m", "q", &["ab", "c"]);
        let right = cache_key("m", "q", &["a", "bc"]);
        assert_ne!(left, right);
        assert_eq!(left, cache_key("m", "q", &["ab", "c"]));
    }

    #[test]
    fn canonical_order_is_independent_of_input_order() {
        let documents = vec!["c".to_owned(), "a".to_owned(), "b".to_owned()];
        let order = canonical_order(&documents);
        assert_eq!(order, vec![1, 2, 0]);
        assert_eq!(
            order
                .iter()
                .map(|&index| documents[index].as_str())
                .collect::<Vec<_>>(),
            vec!["a", "b", "c"]
        );
    }

    #[test]
    fn cache_evicts_the_oldest_entry() {
        let mut cache = ScoreCache::new(2);
        let a = cache_key("m", "a", &[]);
        let b = cache_key("m", "b", &[]);
        let c = cache_key("m", "c", &[]);
        cache.insert(a, Vec::new());
        cache.insert(b, Vec::new());
        cache.insert(c, Vec::new());

        assert!(cache.get(a).is_none());
        assert!(cache.get(b).is_some());
        assert!(cache.get(c).is_some());
    }

    #[tokio::test]
    async fn posts_the_jina_shape_and_caches_repeated_queries() {
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
                        assert_eq!(body["documents"], serde_json::json!(["a", "b", "c"]));
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
            Some(Mutex::new(ScoreCache::new(4))),
        )
        .unwrap();
        let reversed = vec!["c".to_owned(), "b".to_owned(), "a".to_owned()];
        let sorted = vec!["a".to_owned(), "b".to_owned(), "c".to_owned()];

        let first = reranker.rerank("question", &reversed).await.unwrap();
        let second = reranker.rerank("question", &sorted).await.unwrap();

        let reranked_texts = |ranked: &[Ranked], documents: &[String]| {
            ranked
                .iter()
                .map(|item| documents[item.index].clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(reranked_texts(&first, &reversed), vec!["c", "a"]);
        assert_eq!(reranked_texts(&second, &sorted), vec!["c", "a"]);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        server.abort();
    }
}
