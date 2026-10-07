use anyhow::Result;
use qdrant_client::{
    Qdrant,
    qdrant::{QueryPoints, QueryPointsBuilder},
};
use rig_qdrant::QdrantVectorStore;

use crate::embedding::{self, EmbeddingModel};

pub const COLLECTION_NAME: &str = "docs";
pub const QDRANT_URL: &str = "http://localhost:6334";

pub fn query_params() -> QueryPoints {
    QueryPointsBuilder::new(COLLECTION_NAME)
        .with_payload(true)
        .build()
}

pub fn new_store(client: Qdrant, model: EmbeddingModel) -> QdrantVectorStore {
    QdrantVectorStore::new(client, model, query_params())
}

/// Connects to Qdrant and loads the shared embedding model, yielding a store
/// that both embeds queries and retrieves documents with the same model used
/// during ingestion.
pub async fn connect() -> Result<QdrantVectorStore> {
    let client = Qdrant::from_url(QDRANT_URL).build()?;
    let model = embedding::load()?;
    Ok(new_store(client, model))
}
