use anyhow::{Context, Result, bail};
use fastembed::TextEmbedding;
use qdrant_client::{
    Payload, Qdrant,
    qdrant::{
        CreateCollectionBuilder, DeleteCollectionBuilder, Distance, PointStruct, Query,
        QueryPointsBuilder, UpsertPointsBuilder, VectorParamsBuilder,
    },
};
use uuid::Uuid;

use crate::chunk::DocChunk;
use crate::config::Config;
use crate::embedding;
use crate::hashing;

pub fn client(url: &str) -> Result<Qdrant> {
    Qdrant::from_url(url)
        .build()
        .with_context(|| format!("failed to create Qdrant client for {url}"))
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

/// A Qdrant collection bound to the embedding model that populated it.
///
/// Both ingestion (embed + upsert) and retrieval (embed + query) go through one
/// store, so the vectors compared are always produced by the same model.
pub struct Store {
    client: Qdrant,
    collection: String,
    model: TextEmbedding,
}

impl Store {
    pub fn new(client: Qdrant, collection: String, model: TextEmbedding) -> Self {
        Self {
            client,
            collection,
            model,
        }
    }

    /// Connects to Qdrant and returns a store bound to the collection named in
    /// the config, verifying that it exists and was built with the configured
    /// model.
    pub async fn connect(config: &Config) -> Result<Self> {
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

        verify_model(active, &config.embedding.model, &config.corpus.prefix)?;
        let model = embedding::load_slug(&config.embedding.model)?;
        Ok(Self::new(client, active.clone(), model))
    }

    /// Embeds each chunk's text into one dense vector, in chunk order.
    pub fn embed_chunks(&self, chunks: &[DocChunk]) -> Result<Vec<Vec<f32>>> {
        let texts = chunks.iter().map(|chunk| chunk.text.as_str()).collect();
        embedding::embed(&self.model, texts)
    }

    /// Upserts `(chunk, vector)` pairs, storing each chunk as its point's
    /// payload.
    pub async fn insert(&self, documents: Vec<(DocChunk, Vec<f32>)>) -> Result<()> {
        if documents.is_empty() {
            return Ok(());
        }

        let mut points = Vec::with_capacity(documents.len());
        for (document, vector) in documents {
            let payload = serde_json::to_value(&document)?;
            let payload = Payload::try_from(payload)
                .with_context(|| format!("failed to encode chunk {:?} as a payload", document))?;
            points.push(PointStruct::new(
                Uuid::new_v4().to_string(),
                vector,
                payload,
            ));
        }

        self.client
            .upsert_points(UpsertPointsBuilder::new(&self.collection, points).wait(true))
            .await
            .with_context(|| {
                format!(
                    "failed to insert points into collection {:?}",
                    self.collection
                )
            })
            .map(|_| ())
    }

    /// Returns the `k` chunks nearest to `question`, best first, dropping any
    /// whose score is below `threshold`.
    pub async fn search(
        &self,
        question: &str,
        k: u64,
        threshold: f32,
    ) -> Result<Vec<(DocChunk, f32)>> {
        let query_vector = embedding::embed(&self.model, vec![question])?
            .into_iter()
            .next()
            .context("embedding model returned no vector for the query")?;

        let response = self
            .client
            .query(
                QueryPointsBuilder::new(&self.collection)
                    .with_payload(true)
                    .query(Query::new_nearest(query_vector))
                    .score_threshold(threshold)
                    .limit(k)
                    .build(),
            )
            .await
            .with_context(|| {
                format!(
                    "failed to query collection {:?} for {question:?}",
                    self.collection
                )
            })?;

        response
            .result
            .into_iter()
            .map(|point| {
                let chunk: DocChunk = serde_json::from_value(serde_json::to_value(point.payload)?)?;
                Ok((chunk, point.score))
            })
            .collect()
    }
}

/// Checks a collection was built with `configured`, using the model slug baked
/// into its name. Qdrant itself only rejects dimension mismatches.
fn verify_model(collection: &str, configured: &str, prefix: &str) -> Result<()> {
    let expected = format!(
        "{}-{}-",
        hashing::sanitize(prefix),
        hashing::sanitize(configured)
    );
    if !collection.starts_with(&expected) {
        let found = hashing::model_of(prefix, collection).unwrap_or("unknown");
        bail!(
            "active collection {collection:?} was built with model {found:?}, but config selects {configured:?}; re-ingest or fix [embedding].model"
        );
    }
    Ok(())
}
