use anyhow::{Context, Result, bail};
use fastembed::TextEmbedding;
use qdrant_client::{
    Payload, Qdrant,
    qdrant::{
        CreateCollectionBuilder, DeleteCollectionBuilder, Distance, DocumentBuilder, Modifier,
        NamedVectors, PointStruct, PrefetchQueryBuilder, Query, QueryPointsBuilder, Rrf,
        SparseVectorParamsBuilder, SparseVectorsConfigBuilder, UpsertPointsBuilder,
        VectorParamsBuilder, VectorsConfigBuilder,
    },
};
use uuid::Uuid;

use crate::chunk::DocChunk;
use crate::config::Config;
use crate::embedding;
use crate::hashing;
use crate::reranker::{Ranked, Reranker};

use tokio::sync::{mpsc, oneshot};

const EMBEDDING_WORKER_QUEUE_SIZE: usize = 4;

/// Named vectors of the hybrid collection: the dense BGE embedding plus a
/// server-side BM25 sparse vector, fused at query time.
const DENSE_VECTOR_NAME: &str = "dense";
const SPARSE_VECTOR_NAME: &str = "sparse";
/// Qdrant's built-in BM25 model, applied by the server (no local sparse model).
const SPARSE_MODEL_NAME: &str = "Qdrant/bm25";
/// Candidates pulled from each prefetch before fusion.
const HYBRID_PREFETCH_LIMIT: u64 = 50;
/// Reciprocal Rank Fusion parameters: the rank constant and the per-prefetch
/// weights in prefetch order (`[sparse, dense]`). Higher weight gives more
/// influence on the final ranking.
const RRF_K: u32 = 2;
const SPARSE_WEIGHT: f32 = 2.0;
const DENSE_WEIGHT: f32 = 1.0;

pub fn client(url: &str) -> Result<Qdrant> {
    Qdrant::from_url(url)
        .build()
        .with_context(|| format!("failed to create Qdrant client for {url}"))
}

pub async fn create_collection(client: &Qdrant, name: &str, dims: u64) -> Result<()> {
    let mut vectors = VectorsConfigBuilder::default();
    vectors.add_named_vector_params(
        DENSE_VECTOR_NAME,
        VectorParamsBuilder::new(dims, Distance::Cosine),
    );
    let mut sparse = SparseVectorsConfigBuilder::default();
    sparse.add_named_vector_params(
        SPARSE_VECTOR_NAME,
        SparseVectorParamsBuilder::default().modifier(Modifier::Idf),
    );

    client
        .create_collection(
            CreateCollectionBuilder::new(name)
                .vectors_config(vectors)
                .sparse_vectors_config(sparse),
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
    embed_tx: mpsc::Sender<EmbedRequest>,
    reranker: Option<Reranker>,
}

impl Store {
    pub fn new(client: Qdrant, collection: String, model: TextEmbedding) -> Self {
        let (embed_tx, embed_rx) = mpsc::channel(EMBEDDING_WORKER_QUEUE_SIZE);
        std::thread::Builder::new()
            .name("embedder".into())
            .spawn(move || embedding_worker(model, embed_rx))
            .expect("failed to spawn embedding worker");

        Self {
            client,
            collection,
            embed_tx,
            reranker: None,
        }
    }

    /// Connects to Qdrant and returns a store bound to the collection named in
    /// the config, verifying that it exists and was built with the configured
    /// model. When `[reranker]` is configured, `cache_rerank` enables its score
    /// cache.
    pub async fn connect(config: &Config, cache_rerank: bool) -> Result<Self> {
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
        let mut store = Self::new(client, active.clone(), model);
        if let Some(reranker) = &config.reranker {
            store.reranker = Some(Reranker::from_config(reranker, cache_rerank)?);
        }
        Ok(store)
    }

    /// Embeds each chunk's text into one dense vector, in chunk order.
    pub async fn embed_chunks(&self, chunks: &[DocChunk]) -> Result<Vec<Vec<f32>>> {
        let texts = chunks.iter().map(|chunk| chunk.text.to_owned()).collect();
        self.embed_texts(texts).await
    }

    /// Upserts `(chunk, dense vector)` pairs, storing each chunk as its point's
    /// payload and its text as a server-side BM25 sparse vector.
    pub async fn insert(&self, documents: Vec<(DocChunk, Vec<f32>)>) -> Result<()> {
        if documents.is_empty() {
            return Ok(());
        }

        let mut points = Vec::with_capacity(documents.len());
        for (document, vector) in documents {
            let payload = serde_json::to_value(&document)?;
            let payload = Payload::try_from(payload)
                .with_context(|| format!("failed to encode chunk {:?} as a payload", document))?;
            let vectors = NamedVectors::default()
                .add_vector(DENSE_VECTOR_NAME, vector)
                .add_vector(
                    SPARSE_VECTOR_NAME,
                    DocumentBuilder::new(&document.text, SPARSE_MODEL_NAME).build(),
                );
            points.push(PointStruct::new(
                Uuid::new_v4().to_string(),
                vectors,
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

    /// Returns the `k` chunks most relevant to `question`, best first, by
    /// fusing a BM25 sparse prefetch and a dense prefetch with Reciprocal Rank
    /// Fusion; drops any fused hit whose RRF score is below `threshold`.
    ///
    /// With a reranker configured, up to `max(k, [reranker].candidates)` fused
    /// hits are scored first and the result is cut to `k`. A reranker failure
    /// falls back to the fused order after logging the error.
    pub async fn search(
        &self,
        question: &str,
        k: u64,
        threshold: f32,
    ) -> Result<Vec<(DocChunk, f32)>> {
        let query_vector = self
            .embed_texts(vec![question.to_owned()])
            .await?
            .into_iter()
            .next()
            .context("embedding model returned no vector for the query")?;

        let sparse = PrefetchQueryBuilder::default()
            .using(SPARSE_VECTOR_NAME)
            .query(Query::new_nearest(
                DocumentBuilder::new(question, SPARSE_MODEL_NAME).build(),
            ))
            .limit(HYBRID_PREFETCH_LIMIT);
        let dense = PrefetchQueryBuilder::default()
            .using(DENSE_VECTOR_NAME)
            .query(Query::new_nearest(query_vector))
            .limit(HYBRID_PREFETCH_LIMIT);

        let limit = match &self.reranker {
            Some(reranker) => reranker.candidates().max(k),
            None => k,
        };

        let response = self
            .client
            .query(
                QueryPointsBuilder::new(&self.collection)
                    .with_payload(true)
                    .add_prefetch(sparse)
                    .add_prefetch(dense)
                    .query(Query::new_rrf(Rrf {
                        k: Some(RRF_K),
                        weights: vec![SPARSE_WEIGHT, DENSE_WEIGHT],
                    }))
                    .score_threshold(threshold)
                    .limit(limit)
                    .build(),
            )
            .await
            .with_context(|| format!("failed to query collection {:?}", self.collection))?;

        let hits: Vec<(DocChunk, f32)> = response
            .result
            .into_iter()
            .map(|point| {
                let chunk: DocChunk = serde_json::from_value(serde_json::to_value(point.payload)?)?;
                Ok((chunk, point.score))
            })
            .collect::<Result<_>>()?;

        if let Some(reranker) = &self.reranker {
            let documents: Vec<String> = hits.iter().map(|(chunk, _)| chunk.text.clone()).collect();
            match reranker.rerank(question, &documents).await {
                Ok(ranked) => return Ok(select_reranked(hits, &ranked, k)),
                Err(error) => {
                    eprintln!("serve: rerank failed (falling back to fusion): {error:#}");
                }
            }
        }

        Ok(hits.into_iter().take(k as usize).collect())
    }

    async fn embed_texts(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        let (reply_tx, reply_rx) = oneshot::channel();
        self.embed_tx
            .send(EmbedRequest::Embed { texts, reply_tx })
            .await
            .context("embedding worker has shut down")?;
        reply_rx.await?
    }
}

/// Applies a reranker's best-first ordering to fused `hits`, taking at most
/// `k`. Candidates the reranker omitted are appended in fused order with their
/// fused scores, so the caller still receives `k` hits when the pool has them.
fn select_reranked(hits: Vec<(DocChunk, f32)>, ranked: &[Ranked], k: u64) -> Vec<(DocChunk, f32)> {
    let limit = k as usize;
    let mut slots: Vec<Option<(DocChunk, f32)>> = hits.into_iter().map(Some).collect();
    let mut selected = Vec::with_capacity(limit.min(slots.len()));

    for item in ranked {
        if selected.len() >= limit {
            break;
        }
        if let Some(hit) = slots.get_mut(item.index).and_then(Option::take) {
            selected.push((hit.0, item.score));
        }
    }
    for hit in slots.into_iter().flatten() {
        if selected.len() >= limit {
            break;
        }
        selected.push(hit);
    }
    selected
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

enum EmbedRequest {
    Embed {
        texts: Vec<String>,
        reply_tx: oneshot::Sender<Result<Vec<Vec<f32>>>>,
    },
}

fn embedding_worker(mut model: TextEmbedding, mut rx: mpsc::Receiver<EmbedRequest>) {
    while let Some(EmbedRequest::Embed { texts, reply_tx }) = rx.blocking_recv() {
        let result = embedding::embed(&mut model, texts);
        // Ignore send errors: the caller may have timed out / been cancelled.
        let _ = reply_tx.send(result);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hit(text: &str, score: f32) -> (DocChunk, f32) {
        (
            DocChunk {
                path: "a.md".to_owned(),
                start_line: 1,
                end_line: 1,
                text: text.to_owned(),
                chunk_index: 0,
            },
            score,
        )
    }

    #[test]
    fn select_reranked_reorders_and_truncates_to_k() {
        let hits = vec![hit("a", 0.9), hit("b", 0.8), hit("c", 0.7)];
        let ranked = vec![
            Ranked {
                index: 2,
                score: 0.95,
            },
            Ranked {
                index: 0,
                score: 0.5,
            },
            Ranked {
                index: 1,
                score: 0.1,
            },
        ];

        let selected = select_reranked(hits, &ranked, 2);

        assert_eq!(selected.len(), 2);
        assert_eq!(selected[0].0.text, "c");
        assert_eq!(selected[0].1, 0.95);
        assert_eq!(selected[1].0.text, "a");
    }

    #[test]
    fn select_reranked_appends_omitted_candidates_in_fused_order() {
        let hits = vec![hit("a", 0.9), hit("b", 0.8), hit("c", 0.7)];
        let ranked = vec![Ranked {
            index: 1,
            score: 0.6,
        }];

        let selected = select_reranked(hits, &ranked, 3);

        assert_eq!(
            selected
                .iter()
                .map(|(c, _)| c.text.as_str())
                .collect::<Vec<_>>(),
            vec!["b", "a", "c"]
        );
        assert_eq!(selected[0].1, 0.6);
        assert_eq!(selected[1].1, 0.9);
        assert_eq!(selected[2].1, 0.7);
    }

    #[test]
    fn select_reranked_ignores_out_of_range_indexes() {
        let hits = vec![hit("a", 0.9)];
        let ranked = vec![Ranked {
            index: 3,
            score: 1.0,
        }];

        let selected = select_reranked(hits, &ranked, 1);

        assert_eq!(selected.len(), 1);
        assert_eq!(selected[0].0.text, "a");
        assert_eq!(selected[0].1, 0.9);
    }
}
