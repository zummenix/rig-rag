use std::path::Path;

use anyhow::Result;

use crate::config::Config;
use crate::embedding;

/// Loads the embedding model and prints its identity, plus the configured
/// cloud reranker (without contacting it). Serves as a build-time
/// warm/validation step (baking the model weights) and a runtime sanity check.
/// When no config exists at `config_path`, the default model is used.
pub fn run(config_path: impl AsRef<Path>) -> Result<()> {
    let path = config_path.as_ref();
    let config = if path.exists() {
        Some(Config::load(path)?)
    } else {
        None
    };
    let slug = config
        .as_ref()
        .map(|config| config.embedding.model.clone())
        .unwrap_or_else(|| embedding::DEFAULT_MODEL_SLUG.to_string());

    embedding::load_slug(&slug)?;
    let dims = embedding::dimensions(&slug)?;
    println!("model={slug} dims={dims}");
    if let Some(reranker) = config.as_ref().and_then(|config| config.reranker.as_ref()) {
        println!(
            "reranker={} base_url={} candidates={}",
            reranker.model, reranker.base_url, reranker.candidates
        );
    }
    Ok(())
}
