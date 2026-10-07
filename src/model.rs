use std::path::Path;

use anyhow::Result;

use crate::config::{CONFIG_PATH, Config};
use crate::embedding;

/// Loads the embedding model and prints its identity. Serves as a build-time
/// warm/validation step (baking the model weights) and a runtime sanity check.
pub fn run() -> Result<()> {
    let slug = if Path::new(CONFIG_PATH).exists() {
        Config::load()?.embedding.model
    } else {
        embedding::DEFAULT_MODEL_SLUG.to_string()
    };

    let model = embedding::load_slug(&slug)?;
    println!("model={slug} dims={}", model.capabilities().ndims);
    Ok(())
}
