//! Structured per-query metrics lines consumed by the eval runner.

use std::time::Duration;

use serde::Deserialize;
use sha2::{Digest, Sha256};

/// First 16 lowercase hex chars of `sha256(question)`, taken over UTF-8 bytes.
pub fn question_hash(question: &str) -> String {
    let digest = Sha256::digest(question.as_bytes());
    let mut hex = String::with_capacity(16);
    for byte in digest.iter().take(8) {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

/// Marks whether a rerank line describes a successful provider call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RerankStatus {
    Success,
    Failed,
}

impl RerankStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
        }
    }
}

/// Provider usage fields the eval sums. Only the fields this app reports are
/// read; provider extras (`document`, `id`, `balance`, ...) are ignored.
#[derive(Debug, Deserialize)]
pub struct Usage {
    #[serde(default)]
    pub total_tokens: Option<u64>,
    #[serde(default)]
    pub cost: Option<f64>,
    #[serde(default)]
    pub cost_rub: Option<f64>,
}

/// The `search` line: fused retrieval latency, before any reranking.
pub fn search_line(question: &str, k: u64, candidates: usize, elapsed: Duration) -> String {
    format!(
        "search q={} k={k} candidates={candidates} duration_ms={}",
        question_hash(question),
        elapsed.as_millis()
    )
}

/// The `rerank` line: one per provider call, success or failure.
///
/// `error` is the last field and may contain spaces; the runner reads
/// everything after `error=` as its value.
#[allow(clippy::too_many_arguments)]
pub fn rerank_line(
    question: &str,
    model: &str,
    documents: usize,
    results: usize,
    elapsed: Duration,
    status: RerankStatus,
    usage: Option<&Usage>,
    error: Option<&str>,
) -> String {
    let mut line = format!(
        "rerank q={} model={model} docs={documents} results={results} duration_ms={} status={}",
        question_hash(question),
        elapsed.as_millis(),
        status.as_str()
    );
    if let Some(usage) = usage {
        if let Some(tokens) = usage.total_tokens {
            line.push_str(&format!(" tokens={tokens}"));
        }
        if let Some(cost) = usage.cost {
            line.push_str(&format!(" cost={cost}"));
        }
        if let Some(cost_rub) = usage.cost_rub {
            line.push_str(&format!(" cost_rub={cost_rub}"));
        }
    }
    if let Some(error) = error {
        line.push_str(&format!(" error={error}"));
    }
    line
}

/// Writes the `search` line to stderr (captured into `serve-<profile>.log`).
pub fn log_search(question: &str, k: u64, candidates: usize, elapsed: Duration) {
    eprintln!("{}", search_line(question, k, candidates, elapsed));
}

/// Writes the `rerank` line to stderr.
#[allow(clippy::too_many_arguments)]
pub fn log_rerank(
    question: &str,
    model: &str,
    documents: usize,
    results: usize,
    elapsed: Duration,
    status: RerankStatus,
    usage: Option<&Usage>,
    error: Option<&str>,
) {
    eprintln!(
        "{}",
        rerank_line(
            question, model, documents, results, elapsed, status, usage, error
        )
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    const QUESTION: &str = "Who is the author of jj?";

    #[test]
    fn question_hash_is_sixteen_lowercase_hex_chars() {
        // Cross-checked against Python's
        // `hashlib.sha256(question.encode()).hexdigest()[:16]`.
        assert_eq!(question_hash(QUESTION), "3c8d50cdcb9b43f7");
        assert_eq!(question_hash(QUESTION).len(), 16);
        assert!(
            question_hash(QUESTION)
                .chars()
                .all(|c| c.is_ascii_hexdigit())
        );
        assert_ne!(question_hash("a"), question_hash("b"));
    }

    #[test]
    fn search_line_has_a_fixed_field_order() {
        assert_eq!(
            search_line(QUESTION, 20, 30, Duration::from_millis(412)),
            "search q=3c8d50cdcb9b43f7 k=20 candidates=30 duration_ms=412"
        );
    }

    #[test]
    fn success_line_puts_status_before_usage() {
        let usage = Usage {
            total_tokens: Some(39),
            cost: Some(0.000351),
            cost_rub: Some(0.03),
        };
        assert_eq!(
            rerank_line(
                QUESTION,
                "rerank-3",
                30,
                30,
                Duration::from_millis(1188),
                RerankStatus::Success,
                Some(&usage),
                None
            ),
            "rerank q=3c8d50cdcb9b43f7 model=rerank-3 docs=30 results=30 duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03"
        );
    }

    #[test]
    fn success_line_omits_usage_when_absent() {
        assert_eq!(
            rerank_line(
                QUESTION,
                "rerank-3",
                30,
                30,
                Duration::from_millis(1188),
                RerankStatus::Success,
                None,
                None
            ),
            "rerank q=3c8d50cdcb9b43f7 model=rerank-3 docs=30 results=30 duration_ms=1188 status=success"
        );
    }

    #[test]
    fn failure_line_puts_the_error_last() {
        assert_eq!(
            rerank_line(
                QUESTION,
                "rerank-3",
                30,
                0,
                Duration::from_millis(2001),
                RerankStatus::Failed,
                None,
                Some("rerank endpoint returned 500: oops")
            ),
            "rerank q=3c8d50cdcb9b43f7 model=rerank-3 docs=30 results=0 duration_ms=2001 status=failed error=rerank endpoint returned 500: oops"
        );
    }
}
