use anyhow::{Context, Result, bail};
use qdrant_client::{
    Qdrant,
    qdrant::{
        CreateCollectionBuilder, DeleteCollectionBuilder, Distance, QueryPoints,
        QueryPointsBuilder, VectorParamsBuilder,
    },
};
use rig_qdrant::QdrantVectorStore;

use crate::config::Config;
use crate::embedding::{self, EmbeddingModel};
use crate::hashing;

pub fn client(url: &str) -> Result<Qdrant> {
    Qdrant::from_url(url)
        .build()
        .with_context(|| format!("failed to create Qdrant client for {url}"))
}

pub fn query_params(collection: &str) -> QueryPoints {
    QueryPointsBuilder::new(collection)
        .with_payload(true)
        .build()
}

pub fn new_store(client: Qdrant, model: EmbeddingModel, collection: &str) -> QdrantVectorStore {
    QdrantVectorStore::new(client, model, query_params(collection))
}

pub async fn create_collection(client: &Qdrant, name: &str, dims: u64) -> Result<()> {
    client
        .create_collection(
            CreateCollectionBuilder::new(name)
                .vectors_config(VectorParamsBuilder::new(dims, Distance::Cosine)),
        )
        .await
        .with_context(|| format!("failed to create collection {name:?}"))?;
    Ok(())
}

pub async fn delete_collection(client: &Qdrant, name: &str) -> Result<()> {
    client
        .delete_collection(DeleteCollectionBuilder::new(name))
        .await
        .with_context(|| format!("failed to delete collection {name:?}"))?;
    Ok(())
}

pub async fn list_collection_names(client: &Qdrant) -> Result<Vec<String>> {
    let response = client
        .list_collections()
        .await
        .context("failed to list collections")?;
    Ok(response.collections.into_iter().map(|c| c.name).collect())
}

/// Connects to Qdrant and returns a store bound to the collection named in the
/// config, verifying that it exists and was built with the configured model.
pub async fn connect(config: &Config) -> Result<QdrantVectorStore> {
    let client = client(&config.qdrant.url)?;
    let active = &config.collection.active;

    if active.is_empty() {
        bail!(
            "no active collection configured: set [collection].active via `rig-rag promote <collection>`"
        );
    }
    if !client.collection_exists(active).await? {
        bail!(
            "configured collection {active:?} does not exist; run `rig-rag ingest` then `rig-rag promote <collection>`, or fix [collection].active"
        );
    }

    verify_model(active, &config.embedding.model)?;
    let model = embedding::load_slug(&config.embedding.model)?;
    Ok(new_store(client, model, active))
}

/// Checks a collection was built with `configured`, using the model slug baked
/// into its name. Qdrant itself only rejects dimension mismatches.
fn verify_model(collection: &str, configured: &str) -> Result<()> {
    let prefix = format!(
        "{}-{}-",
        hashing::COLLECTION_PREFIX,
        hashing::sanitize(configured)
    );
    if !collection.starts_with(&prefix) {
        let found = hashing::model_of(collection).unwrap_or("unknown");
        bail!(
            "active collection {collection:?} was built with model {found:?}, but config selects {configured:?}; re-ingest or fix [embedding].model"
        );
    }
    Ok(())
}
