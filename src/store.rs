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

    verify_model(active, &config.embedding.model, &config.corpus.prefix)?;
    let model = embedding::load_slug(&config.embedding.model)?;
    Ok(new_store(client, model, active))
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

pub mod v2 {
    use anyhow::{Context, Result};
    use qdrant_client::{
        Payload, Qdrant,
        qdrant::{
            CreateCollectionBuilder, Distance, DocumentBuilder, Fusion, Modifier, NamedVectors,
            PointStruct, PrefetchQueryBuilder, QueryPointsBuilder, SparseVectorConfig,
            SparseVectorParamsBuilder, SparseVectorsConfigBuilder, UpsertPointsBuilder,
            VectorInput, VectorParamsBuilder, VectorsConfig, VectorsConfigBuilder,
            vector_input::{self},
        },
    };
    use uuid::Uuid;

    use crate::chunk::DocChunk;

    const DENSE_VECTOR_NAME: &str = "dense";
    const SPARSE_VECTOR_NAME: &str = "sparse";
    const SPARSE_MODEL_NAME: &str = "Qdrant/bm25";
    const HYBRID_PREFETCH_LIMIT: u64 = 200;

    pub async fn create_hybrid_collection(client: &Qdrant, name: &str, dims: u64) -> Result<()> {
        client
            .create_collection(
                CreateCollectionBuilder::new(name)
                    .vectors_config::<VectorsConfig>(
                        VectorsConfigBuilder::default()
                            .add_named_vector_params(
                                DENSE_VECTOR_NAME,
                                VectorParamsBuilder::new(dims, Distance::Cosine),
                            )
                            .clone()
                            .into(),
                    )
                    .sparse_vectors_config::<SparseVectorConfig>(
                        SparseVectorsConfigBuilder::default()
                            .add_named_vector_params(
                                SPARSE_VECTOR_NAME,
                                SparseVectorParamsBuilder::default().modifier(Modifier::Idf),
                            )
                            .clone()
                            .into(),
                    )
                    .build(),
            )
            .await
            .with_context(|| format!("failed to create collection {name:?}"))?;
        Ok(())
    }

    pub async fn insert_documents(
        client: &Qdrant,
        collection_name: &str,
        documents: Vec<(DocChunk, Vec<f32>)>,
    ) -> Result<()> {
        let mut points: Vec<PointStruct> = vec![];

        for (document, embeddings) in documents {
            let json_document = serde_json::to_value(&document)?;
            let doc_as_payload = Payload::try_from(json_document)
                .with_context(|| String::from("failed to create payload for insertinon"))?;

            let named_vectors = NamedVectors::default()
                .add_vector(DENSE_VECTOR_NAME, embeddings)
                .add_vector(
                    SPARSE_VECTOR_NAME,
                    DocumentBuilder::new(&document.text, SPARSE_MODEL_NAME).build(),
                );
            points.push(PointStruct::new(
                Uuid::new_v4().to_string(),
                named_vectors,
                doc_as_payload,
            ))
        }

        let request = UpsertPointsBuilder::new(collection_name, points).wait(true);
        client
            .upsert_points(request)
            .await
            .with_context(|| format!("failed to insert points into collection {collection_name}"))
            .map(|_| ())
    }

    pub async fn query_documents(
        client: &Qdrant,
        collection_name: &str,
        text_query: &str,
        embedded_query: Vec<f32>,
        limit: u64,
    ) -> Result<Vec<(DocChunk, f32)>> {
        let response = client
            .query(
                QueryPointsBuilder::new(collection_name)
                    .with_payload(true)
                    .prefetch(&[
                        PrefetchQueryBuilder::default()
                            .using(SPARSE_VECTOR_NAME)
                            .limit(HYBRID_PREFETCH_LIMIT)
                            .query(VectorInput {
                                variant: Some(vector_input::Variant::Document(
                                    DocumentBuilder::new(text_query, SPARSE_MODEL_NAME).build(),
                                )),
                            })
                            .build(),
                        PrefetchQueryBuilder::default()
                            .using(DENSE_VECTOR_NAME)
                            .limit(HYBRID_PREFETCH_LIMIT)
                            .query(embedded_query)
                            .build(),
                    ])
                    .query(Fusion::Rrf)
                    .limit(limit)
                    .build(),
            )
            .await
            .with_context(|| {
                format!("failed to perform query '{text_query}' on collection '{collection_name}'")
            })?;

        let mut result: Vec<(DocChunk, f32)> = vec![];
        for item in response.result {
            let payload = serde_json::from_value(serde_json::to_value(item.payload)?)?;
            result.push((payload, item.score));
        }
        Ok(result)
    }
}
