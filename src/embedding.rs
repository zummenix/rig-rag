use anyhow::Result;
use rig::fastembed::{self, FastembedModel};

pub const MODEL: FastembedModel = FastembedModel::BGESmallENV15;

/// The concrete local embedding model produced by [`load`].
pub type EmbeddingModel =
    rig::Model<rig::driver::Local<rig::operation::Embedding>, fastembed::Fastembed>;

/// Loads the shared embedding model. Every command embeds through this, so the
/// model identity and settings stay consistent between ingest and query.
pub fn load() -> Result<EmbeddingModel> {
    let model = fastembed::Fastembed::load(&MODEL)?.embedding(&MODEL, None)?;
    let dims = model.capabilities().ndims;
    assert!(
        dims > 0,
        "fastembed reported no embedding dimensions for {MODEL:?}"
    );
    Ok(model)
}
