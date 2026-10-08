use anyhow::Result;

use crate::config::Config;
use crate::retrieval::{self, THRESHOLD};
use crate::store;

/// Retrieves the chunks most similar to `question` and prints them with their
/// scores.
pub async fn run(question: &str) -> Result<()> {
    let config = Config::load()?;
    let vector_store = store::connect(&config).await?;

    let hits = retrieval::search(&vector_store, question, shared::DEFAULT_K, THRESHOLD).await?;

    for hit in &hits {
        let preview = hit.text.lines().take(6).collect::<Vec<_>>().join("\n");
        println!("Score: {}", hit.score);
        println!("{} (index={})", hit.source_location(), hit.chunk_index);
        println!("\n{preview}\n...\n");
    }

    Ok(())
}
