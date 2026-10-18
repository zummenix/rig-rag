use std::path::Path;

use anyhow::Result;

use crate::config::Config;
use crate::embedding;
use crate::reranker;

/// Loads the embedding and reranking models and prints their identity. Serves
/// as a build-time warm/validation step (baking the model weights) and a
/// runtime sanity check. When no config exists at `config_path`, the default
/// embedding model is used.
pub fn run(config_path: impl AsRef<Path>) -> Result<()> {
    let path = config_path.as_ref();
    let slug = if path.exists() {
        Config::load(path)?.embedding.model
    } else {
        embedding::DEFAULT_MODEL_SLUG.to_string()
    };

    embedding::load_slug(&slug)?;
    let dims = embedding::dimensions(&slug)?;
    reranker::load()?;
    println!("model={slug} dims={dims}");
    println!("reranker={}", reranker::model_slug());
    Ok(())
}
