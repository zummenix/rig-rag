# Experiment: Cross-encoder reranking (`reranking`)

**Hypothesis.** Reranking the top 50 fused hybrid candidates with a local
cross-encoder (`jinaai/jina-reranker-v1-turbo-en`, 151 MB ONNX) before cutting
to `k` improves ranking quality: `mean recall@{1,3,5}` and
`mean MRR@{latency-k}` rise on multi-project, with the largest gains where the
gold chunk is already in the candidate pool but fused below the top few. Recall
at larger `k` may rise only where the gold sits at fused ranks 21–50.
Single-project quality is unchanged or improves, and no answerable question
regresses. The cost is latency: every query scores 50 candidates instead of
returning `k` fused hits, so p50/p95 grow by an order of magnitude on CPU.
Ingest is unaffected (reranking is query-time only). Falsified by no movement
in `recall@k`/`MRR`, by an answerable-question regression, or by a gain too
small to justify the added latency and model footprint.

**Status:** draft
