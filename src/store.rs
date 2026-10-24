use std::time::Instant;

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
use crate::eval_metrics;
use crate::hashing;
use crate::reranker::{Ranked, Reranker};
use shared::SearchMode;

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

/// Builds the Qdrant request for `mode`, embedding `question` through `embed`
/// only for modes that search the dense vector.
///
/// Keeping the embedding behind a function argument means `mode` decides both
/// whether to embed and which vectors to query in one place, while tests can
/// supply a canned vector instead of loading the model.
///
/// No score threshold is applied. Qdrant treats `score_threshold` as an active
/// filter (it drops anything worse than the value), so `0.0` is not a disabled
/// sentinel: it would discard negative cosine similarities in dense mode.
/// A future calibrated cutoff must only be set when it is `Some`, and separately
/// per mode because RRF, cosine, and BM25 scores live on different scales.
async fn query_request(
    collection: &str,
    mode: SearchMode,
    question: &str,
    limit: u64,
    embed: impl AsyncFnOnce(&str) -> Result<Vec<f32>>,
) -> Result<QueryPointsBuilder> {
    let request = QueryPointsBuilder::new(collection)
        .with_payload(true)
        .limit(limit);
    let sparse_query =
        || Query::new_nearest(DocumentBuilder::new(question, SPARSE_MODEL_NAME).build());

    Ok(match mode {
        SearchMode::Hybrid => {
            let dense = embed(question).await?;
            let sparse_prefetch = PrefetchQueryBuilder::default()
                .using(SPARSE_VECTOR_NAME)
                .query(sparse_query())
                .limit(HYBRID_PREFETCH_LIMIT);
            let dense_prefetch = PrefetchQueryBuilder::default()
                .using(DENSE_VECTOR_NAME)
                .query(Query::new_nearest(dense))
                .limit(HYBRID_PREFETCH_LIMIT);
            request
                .add_prefetch(sparse_prefetch)
                .add_prefetch(dense_prefetch)
                .query(Query::new_rrf(Rrf {
                    k: Some(RRF_K),
                    weights: vec![SPARSE_WEIGHT, DENSE_WEIGHT],
                }))
        }
        SearchMode::Dense => {
            let dense = embed(question).await?;
            request
                .using(DENSE_VECTOR_NAME)
                .query(Query::new_nearest(dense))
        }
        SearchMode::Sparse => request.using(SPARSE_VECTOR_NAME).query(sparse_query()),
    })
}

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
    log_eval_metrics: bool,
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
            log_eval_metrics: false,
        }
    }

    /// Connects to Qdrant and returns a store bound to the collection named in
    /// the config, verifying that it exists and was built with the configured
    /// model. `log_eval_metrics` emits structured `search`/`rerank` lines for
    /// the eval runner; it never changes retrieval behavior.
    pub async fn connect(config: &Config, log_eval_metrics: bool) -> Result<Self> {
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
        store.log_eval_metrics = log_eval_metrics;
        if let Some(reranker) = &config.reranker {
            store.reranker = Some(Reranker::from_config(reranker, log_eval_metrics)?);
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

    /// Returns the `k` chunks most relevant to `question`, best first, using
    /// the vectors selected by `mode`.
    ///
    /// [`SearchMode::Hybrid`] fuses a BM25 sparse prefetch and a dense prefetch
    /// with Reciprocal Rank Fusion; [`SearchMode::Dense`] and
    /// [`SearchMode::Sparse`] query one vector directly (sparse never embeds the
    /// question locally). No score cutoff is applied, so a query returns up to
    /// `k` hits. Raw retrieval scores are per-mode — an RRF score for
    /// [`SearchMode::Hybrid`], a cosine similarity for [`SearchMode::Dense`], and
    /// a BM25 score for [`SearchMode::Sparse`] — so they are not comparable across
    /// modes. When a reranker rescores a hit, the returned score is the
    /// reranker's relevance score for the same query and model, which is
    /// comparable across modes; the per-mode caveat applies to un-reranked hits.
    ///
    /// With a reranker configured, up to `max(k, [reranker].candidates)` hits
    /// are scored first and the result is cut to `k`. A reranker failure falls
    /// back to the retrieved order after logging the error.
    pub async fn search(
        &self,
        question: &str,
        k: u64,
        mode: SearchMode,
    ) -> Result<Vec<(DocChunk, f32)>> {
        let started = Instant::now();
        let limit = match &self.reranker {
            Some(reranker) => reranker.candidates().max(k),
            None => k,
        };
        let request = query_request(&self.collection, mode, question, limit, async |question| {
            self.embed_question(question).await
        })
        .await?
        .build();

        let response = self
            .client
            .query(request)
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

        if self.log_eval_metrics {
            eval_metrics::log_search(question, k, mode, hits.len(), started.elapsed());
        }

        if let Some(reranker) = &self.reranker {
            let documents: Vec<String> = hits.iter().map(|(chunk, _)| chunk.text.clone()).collect();
            // On failure the reranker has already logged it and retrieval order is the fallback.
            if let Ok(ranked) = reranker.rerank(question, &documents, mode).await {
                return Ok(select_reranked(hits, &ranked, k));
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

    async fn embed_question(&self, question: &str) -> Result<Vec<f32>> {
        self.embed_texts(vec![question.to_owned()])
            .await?
            .into_iter()
            .next()
            .context("embedding model returned no vector for the query")
    }
}

/// Applies a reranker's best-first ordering to retrieved `hits`, taking at most
/// `k`. Candidates the reranker omitted are appended in retrieval order with
/// their retrieval scores, so the caller still receives `k` hits when the pool
/// has them.
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
    use qdrant_client::qdrant::{
        query::Variant as QueryVariant, vector_input::Variant as VectorVariant,
    };

    fn nearest_variant(request: &qdrant_client::qdrant::QueryPoints) -> VectorVariant {
        match request
            .query
            .as_ref()
            .and_then(|query| query.variant.as_ref())
        {
            Some(QueryVariant::Nearest(vector)) => {
                vector.variant.clone().expect("nearest query has a vector")
            }
            _ => panic!("expected a nearest query"),
        }
    }

    #[tokio::test]
    async fn dense_request_embeds_the_question_and_uses_only_the_dense_vector() {
        let request = query_request(
            "docs-test",
            SearchMode::Dense,
            "how do tokens work?",
            7,
            async |_| Ok(vec![0.25, -0.5]),
        )
        .await
        .expect("dense query request")
        .build();

        assert_eq!(request.using.as_deref(), Some(DENSE_VECTOR_NAME));
        assert!(request.prefetch.is_empty());
        assert_eq!(request.limit, Some(7));
        // A `0.0` threshold would drop negative cosine similarities; unset is
        // the only disabled representation.
        assert_eq!(request.score_threshold, None);
        match nearest_variant(&request) {
            VectorVariant::Dense(dense) => assert_eq!(dense.data, vec![0.25, -0.5]),
            other => panic!("expected a dense query vector, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn sparse_request_uses_bm25_without_an_embedding() {
        let embedded = std::cell::Cell::new(false);
        let request = query_request(
            "docs-test",
            SearchMode::Sparse,
            "rust ownership",
            3,
            async |_| {
                embedded.set(true);
                Ok(vec![0.0])
            },
        )
        .await
        .expect("sparse query request")
        .build();

        assert!(!embedded.get(), "sparse search must not embed the question");
        assert_eq!(request.using.as_deref(), Some(SPARSE_VECTOR_NAME));
        assert!(request.prefetch.is_empty());
        assert_eq!(request.score_threshold, None);
        match nearest_variant(&request) {
            VectorVariant::Document(document) => {
                assert_eq!(document.text, "rust ownership");
                assert_eq!(document.model, SPARSE_MODEL_NAME);
            }
            other => panic!("expected a BM25 document query, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn hybrid_request_prefetches_sparse_then_dense_with_rrf_weights() {
        let request = query_request(
            "docs-test",
            SearchMode::Hybrid,
            "question",
            20,
            async |_| Ok(vec![1.0, 2.0]),
        )
        .await
        .expect("hybrid query request")
        .build();

        assert_eq!(request.score_threshold, None);
        assert_eq!(request.prefetch.len(), 2);

        // The weights are positional: sparse must stay first.
        assert_eq!(
            request.prefetch[0].using.as_deref(),
            Some(SPARSE_VECTOR_NAME)
        );
        assert_eq!(
            request.prefetch[1].using.as_deref(),
            Some(DENSE_VECTOR_NAME)
        );
        assert_eq!(request.prefetch[0].limit, Some(HYBRID_PREFETCH_LIMIT));
        assert_eq!(request.prefetch[1].limit, Some(HYBRID_PREFETCH_LIMIT));

        match request.prefetch[0]
            .query
            .as_ref()
            .and_then(|query| query.variant.as_ref())
        {
            Some(QueryVariant::Nearest(vector)) => {
                assert!(matches!(vector.variant, Some(VectorVariant::Document(_))));
            }
            _ => panic!("sparse prefetch must query a BM25 document"),
        }
        match request.prefetch[1]
            .query
            .as_ref()
            .and_then(|query| query.variant.as_ref())
        {
            Some(QueryVariant::Nearest(vector)) => {
                assert!(matches!(vector.variant, Some(VectorVariant::Dense(_))));
            }
            _ => panic!("dense prefetch must query the dense vector"),
        }

        match request
            .query
            .as_ref()
            .and_then(|query| query.variant.as_ref())
        {
            Some(QueryVariant::Rrf(rrf)) => {
                assert_eq!(rrf.k, Some(RRF_K));
                assert_eq!(rrf.weights, vec![SPARSE_WEIGHT, DENSE_WEIGHT]);
            }
            _ => panic!("hybrid request must fuse with RRF"),
        }
    }

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
    fn select_reranked_appends_omitted_candidates_in_retrieval_order() {
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
