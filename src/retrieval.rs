use anyhow::Result;

use crate::store::{SearchMode, Store};
use shared::DocHit;

/// Retrieves the chunks most similar to `question`, most similar first, using
/// the vectors selected by `mode` ([`SearchMode::Hybrid`] by default).
///
/// Backs the web server's retrieval and chat endpoints so both apply the same
/// search semantics.
pub async fn search(
    store: &Store,
    question: &str,
    k: u64,
    mode: SearchMode,
) -> Result<Vec<DocHit>> {
    let hits = store.search(question, k, mode).await?;

    Ok(hits
        .into_iter()
        .map(|(chunk, score)| DocHit {
            score: f64::from(score),
            path: chunk.path,
            start_line: chunk.start_line,
            end_line: chunk.end_line,
            chunk_index: chunk.chunk_index,
            text: chunk.text,
        })
        .collect())
}
