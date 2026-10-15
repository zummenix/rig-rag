use anyhow::Result;

use crate::store::Store;
use shared::DocHit;

/// Fused-RRF floor below which hits are discarded.
///
/// Qdrant's RRF score is rank-based (`Σ 1/(rank+2)`), not a similarity, so this
/// is a scale-free floor on fusion rank rather than a cosine cutoff. `0.0`
/// disables it, returning the top `k` of the fusion.
pub const RRF_FLOOR: f32 = 0.0;

/// Retrieves the chunks most similar to `question`, most similar first.
///
/// Backs the web server's retrieval and chat endpoints so both apply the same
/// search semantics.
pub async fn search(store: &Store, question: &str, k: u64, threshold: f32) -> Result<Vec<DocHit>> {
    let hits = store.search(question, k, threshold).await?;

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
