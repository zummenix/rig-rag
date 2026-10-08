use anyhow::Result;
use rig::vector_store::{VectorSearchRequest, VectorStoreIndex};

use crate::chunk::DocChunk;
use crate::config::Config;
use crate::store;

const SAMPLES: u64 = 7;

/// Retrieves the chunks most similar to `question` and prints them with their
/// scores.
pub async fn run(question: &str) -> Result<()> {
    let config = Config::load()?;
    let vector_store = store::connect(&config).await?;

    let req = VectorSearchRequest::builder()
        .query(question)
        .samples(SAMPLES)
        .threshold(0.5)
        .build();

    let hits = vector_store.top_n::<DocChunk>(req).await?;

    for (score, _, chunk) in &hits {
        let preview = chunk.text.lines().take(6).collect::<Vec<_>>().join("\n");
        println!("Score: {score}");
        println!("{} (index={})", chunk.source_location(), chunk.chunk_index);
        println!("\n{preview}\n...\n");
    }

    Ok(())
}
