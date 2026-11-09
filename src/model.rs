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
    if let Some(llm) = config.as_ref().and_then(|config| config.llm.as_ref()) {
        // `model` must not require credentials or contact the provider, so a
        // missing key (even for a required provider) is reported as `unset`.
        let key = llm
            .read_key(|n| std::env::var(n).ok())
            .unwrap_or(None)
            .map_or("unset".to_string(), |_| "set".to_string());
        println!(
            "llm: provider={} model={} base_url={} key={}",
            llm.provider.as_str(),
            llm.model,
            llm.base_url(),
            key
        );
    } else {
        println!("llm: disabled");
    }
    Ok(())
}
