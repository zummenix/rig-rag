use anyhow::{Context, Result, bail};
use fastembed::{EmbeddingModel, TextEmbedding, TextInitOptions};

/// Slug of the default embedding model, as written in `rig-rag.toml`.
pub const DEFAULT_MODEL_SLUG: &str = "bge-small-en-v1.5";

/// Maps a config model slug to the fastembed model it selects.
pub fn model_from_slug(slug: &str) -> Result<EmbeddingModel> {
    match slug {
        DEFAULT_MODEL_SLUG => Ok(EmbeddingModel::BGESmallENV15),
        other => bail!("unsupported embedding model {other:?}; supported: {DEFAULT_MODEL_SLUG}"),
    }
}

/// Loads the embedding model named by `slug`. Every command embeds through this,
/// so the model identity and settings stay consistent between ingestion and
/// retrieval.
pub fn load_slug(slug: &str) -> Result<TextEmbedding> {
    TextEmbedding::try_new(TextInitOptions::new(model_from_slug(slug)?))
        .with_context(|| format!("failed to load embedding model {slug:?}"))
}

/// Number of dimensions the model named by `slug` produces. Reads the model's
/// metadata, so it does not load the weights.
pub fn dimensions(slug: &str) -> Result<u64> {
    let model = model_from_slug(slug)?;
    let info = TextEmbedding::get_model_info(&model)
        .with_context(|| format!("no model metadata for {slug:?}"))?;
    Ok(info.dim as u64)
}

/// Embeds each of `texts` into one dense vector, in order. fastembed runs
/// synchronously on the CPU, so this stays non-async.
pub fn embed(model: &mut TextEmbedding, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
    model
        .embed(texts, None)
        .context("failed to embed texts with the local model")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_default_slug() {
        assert!(matches!(
            model_from_slug(DEFAULT_MODEL_SLUG).unwrap(),
            EmbeddingModel::BGESmallENV15
        ));
    }

    #[test]
    fn rejects_unknown_slug() {
        assert!(model_from_slug("nope").is_err());
    }

    #[test]
    fn reports_default_model_dimensions() {
        assert_eq!(dimensions(DEFAULT_MODEL_SLUG).unwrap(), 384);
    }
}
