use std::path::Path;

use anyhow::Result;

use crate::config::Config;
use crate::embedding;

/// Loads the embedding model and prints its identity. Serves as a build-time
/// warm/validation step (baking the model weights) and a runtime sanity check.
/// When no config exists at `config_path`, the default model is used.
pub fn run(config_path: impl AsRef<Path>) -> Result<()> {
    let path = config_path.as_ref();
    let slug = if path.exists() {
        Config::load(path)?.embedding.model
    } else {
        embedding::DEFAULT_MODEL_SLUG.to_string()
    };

    let model = embedding::load_slug(&slug)?;
    println!("model={slug} dims={}", model.capabilities().ndims);
    Ok(())
}
