use anyhow::{Result, bail};

use rig::fastembed::FastembedModel;

/// Slug of the default embedding model, as written in `rig-rag.toml`.
pub const DEFAULT_MODEL_SLUG: &str = "bge-small-en-v1.5";

/// The concrete local embedding model produced by [`load_slug`].
pub type EmbeddingModel =
    rig::Model<rig::driver::Local<rig::operation::Embedding>, rig::fastembed::Fastembed>;

/// Maps a config model slug to the fastembed model it selects.
pub fn model_from_slug(slug: &str) -> Result<FastembedModel> {
    match slug {
        DEFAULT_MODEL_SLUG => Ok(FastembedModel::BGESmallENV15),
        other => bail!("unsupported embedding model {other:?}; supported: {DEFAULT_MODEL_SLUG}"),
    }
}

/// Loads the embedding model named by `slug`. Every command embeds through this,
/// so the model identity and settings stay consistent between ingestion and
/// retrieval.
pub fn load_slug(slug: &str) -> Result<EmbeddingModel> {
    load_model(model_from_slug(slug)?, slug)
}

fn load_model(model: FastembedModel, slug: &str) -> Result<EmbeddingModel> {
    let loaded = rig::fastembed::Fastembed::load(&model)?.embedding(&model, None)?;
    let dims = loaded.capabilities().ndims;
    assert!(
        dims > 0,
        "fastembed reported no embedding dimensions for {slug}"
    );
    Ok(loaded)
}

pub mod v2 {
    use anyhow::{Context, Result};
    use fastembed;

    pub fn load_slug(slug: &str) -> Result<fastembed::TextEmbedding> {
        fastembed::TextEmbedding::try_new(fastembed::InitOptions::new(super::model_from_slug(
            slug,
        )?))
        .with_context(|| format!("failed to load fastembed embedding model '{slug}'"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_default_slug() {
        assert!(matches!(
            model_from_slug(DEFAULT_MODEL_SLUG).unwrap(),
            FastembedModel::BGESmallENV15
        ));
    }

    #[test]
    fn rejects_unknown_slug() {
        assert!(model_from_slug("nope").is_err());
    }
}
