use anyhow::Result;
use rig::vector_store::{VectorSearchRequest, VectorStoreIndex};

use crate::chunk::DocChunk;
use crate::store;

const SAMPLES: u64 = 7;

/// Retrieves the chunks most similar to `question` and prints them with their
/// scores.
pub async fn run(question: &str) -> Result<()> {
    let vector_store = store::connect().await?;

    let req = VectorSearchRequest::builder()
        .query(question)
        .samples(SAMPLES)
        .build();

    let hits = vector_store.top_n::<DocChunk>(req).await?;

    for (score, id, chunk) in &hits {
        let preview = chunk.text.lines().take(6).collect::<Vec<_>>().join("\n");
        println!("Score: {score}");
        println!("ID: {id}");
        println!("\n{preview}\n...\n");
    }

    Ok(())
}
