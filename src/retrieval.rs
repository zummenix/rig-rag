use anyhow::Result;
use rig::vector_store::{VectorSearchRequest, VectorStoreIndex};
use rig_qdrant::QdrantVectorStore;

use crate::chunk::DocChunk;
use shared::DocHit;

/// Fixed similarity threshold below which hits are discarded.
pub const THRESHOLD: f32 = 0.5;

/// Retrieves the chunks most similar to `question`, most similar first.
///
/// Shared by the `query` subcommand and the web server so both apply the same
/// search semantics.
pub async fn search(
    store: &QdrantVectorStore,
    question: &str,
    k: u64,
    threshold: f32,
) -> Result<Vec<DocHit>> {
    let req = VectorSearchRequest::builder()
        .query(question)
        .samples(k)
        .threshold(f64::from(threshold))
        .build();

    let hits = store.top_n::<DocChunk>(req).await?;

    Ok(hits
        .into_iter()
        .map(|hit| DocHit {
            score: hit.score,
            path: hit.document.path,
            start_line: hit.document.start_line,
            end_line: hit.document.end_line,
            chunk_index: hit.document.chunk_index,
            text: hit.document.text,
        })
        .collect())
}
