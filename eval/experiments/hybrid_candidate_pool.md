# Experiment: widen the hybrid candidate pool (`hybrid_candidate_pool`)

**Hypothesis.** Hybrid retrieval fuses a BM25 sparse prefetch and a dense
prefetch with a 2:1 sparse weight and hands the reranker only the top 30 fused
candidates. RRF scores by rank, so a gold chunk that one retriever ranks deep
(dense rank 21–30, or sparse rank 21–29) is truncated before the reranker can
promote it. Fusing the same prefetches with Qdrant's default equal weights and
rescoring the top **60** fused candidates should let those deep golds reach the
reranker and lift mean `recall@7` on both profiles, with no answerable question
moving backward, at roughly twice the reranked documents per query. Falsified by
no recall movement, by any answerable question regressing, or by a rerank-cost
and latency increase that the quality gain does not justify.

**Status:** measured, pending decision — equal weights and the 60-document pool
lifted hybrid `recall@7` from 0.500 to 0.750 on single-project and 0.375 to 0.625
on multi-project, with no answerable hybrid regression; see [Results](#results).

## Controlled change

Hybrid retrieval keeps its two prefetches, its prefetch limit (50), and its RRF
rank constant (`k = 2`). Two knobs change:

- **`src/store.rs`** — the explicit RRF weights (`[SPARSE_WEIGHT = 2.0,
  DENSE_WEIGHT = 1.0]`) are removed; the request now sends no weights. Qdrant
  treats an absent weight list as equal weights, verified against the running
  server as identical to `[1,1]`.
- **`src/config.rs`** — `DEFAULT_RERANK_CANDIDATES` rises from 30 to 60, so
  `serve` fetches and rescored `max(k, candidates)` = 60 fused candidates by
  default. `eval/profiles/{single,multi}-project/rig-rag.toml` pin
  `candidates = 60`; `README.md` documents the new default.

Deliberately unchanged: the collection schema, BM25 model, dense embedding
model, chunking, ingest path, prefetch depth (50), and the reranker
(`rerank-3`, 10 s timeout). The change is query-time only.

The change lives in its own commit so it can be reverted independently, leaving
the harness (framework, runner, this record) and the measured runs intact.

| arm | hybrid pool | commit | run |
| --- | --- | --- | --- |
| baseline | RRF `[2,1]`, 30 candidates | `227b8d11a5a41ec5b59d252805f70caf754465de` | `eval/results/baseline/2026-10-25T14-33-40Z` |
| treatment | equal RRF, 60 candidates | `522be733ffee3c40debb740e622c7c0d9aa17158` | `eval/results/hybrid_candidate_pool/2026-10-28T13-46-06Z` |

Both arms used `--skip-ingest`: the change is query-time only, so the active
collections were reused and **ingest cost is not measured**. Same profiles and
pinned sources as the baseline — `jj @ 9a1ad09b16e87d5d5e25759e5027953f8257be98`,
`podman @ e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365`,
`qdrant @ 9855e50fc739de44a223a1c50faced806a868b6a` — model
`bge-small-en-v1.5` (384 dims), and runner settings
`--profiles single-project,multi-project --k 3,7,20 --modes hybrid,dense,sparse`.

## Results

### Retrieval (hybrid, the hypothesis arm)

| metric | single base | single 60 | Δ | multi base | multi 60 | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mean recall@3 | 0.375 | **0.625** | +0.250 | 0.312 | **0.562** | +0.250 |
| mean recall@7 | 0.500 | **0.750** | +0.250 | 0.375 | **0.625** | +0.250 |
| mean recall@20 | 0.500 | **0.875** | +0.375 | 0.500 | **0.750** | +0.250 |
| mean purity@7 | 1.000 | 1.000 | 0 | 0.929 | **0.955** | +0.027 |
| mean MRR@7 | 0.338 | **0.421** | +0.083 | 0.294 | **0.398** | +0.104 |
| rerank documents | 270 | 534 | +264 | 540 | 1,075 | +535 |
| rerank cost (RUB) | 0.658 | 1.269 | ×1.93 | 1.264 | 2.240 | ×1.77 |
| rerank p50 / p95 ms | 904 / 1,604 | 1,185 / 1,788 | +281 / +184 | 993 / 1,382 | 1,101 / 1,535 | +108 / +152 |
| e2e p50 / p95 ms | 925 / 1,621 | 1,224 / 1,808 | +299 / +187 | 1,017 / 1,403 | 1,122 / 1,555 | +105 / +152 |

Per-question movement (first `k` whose hits cover a gold item):

| question | profile | baseline | 60 candidates |
| --- | --- | --- | --- |
| `jj-split-work` | both | never | **k=3** |
| `jj-conflict-resolution` | both | never | **k=3** |
| `jj-publish-remote` | multi | never | **k=3** |
| `qdrant-archive-collection` | multi | never | **k=3** |
| `jj-config-set-user` | single | never | k=20 |

No answerable question moved backward in hybrid: every baseline hit kept its
first-`k` position or improved. Multi-project `purity@3` dipped 1.000 → 0.958
while `purity@7` rose 0.929 → 0.955. The four questions the probe placed outside
both retrievers' top 50 (`jj-bookmarks-label`, `qdrant-filter-search`,
`qdrant-shrink-vectors`, `cross-project-config`) still return no gold.

### Other modes (measured, not the hypothesis)

The deeper pool reaches dense and sparse too. Dense multi-project `recall@7`
rose 0.375 → 0.438 and single-project was flat at 0.625. Sparse multi-project
`recall@7` rose 0.500 → 0.562; sparse single-project fell 0.625 → 0.500, but
that arm is confounded: **two of its nine rerank calls failed with provider
HTTP 429** (multi had one too) and fell back to raw retrieval order. The hybrid
arm had zero rerank failures.

### Cost

Across the six (profile, mode) arms the run scored 4,740 reranked documents for
9.79 RUB versus 2,955 documents for 5.33 RUB in the baseline (**1.84×**). The
hybrid pair alone is 1,609 vs 810 documents and 3.51 vs 1.92 RUB (**1.83×**).
Hybrid cold p50 rose by 31% (single) and 11% (multi); p95 by 11–12% on both.

## Interpretation

- **Effect size.** The movement is large and deterministic: hybrid `recall@7`
  +0.250 on both profiles, `recall@20` +0.250 to +0.375, and MRR@7 +0.083 to
  +0.104. The four questions that flipped all had their golds at first-stage
  ranks 21–30 (dense) or 21–29 (sparse), exactly the depth the old pool
  truncated. The reranker did the rest, promoting them to k=3.
- **Where it comes from.** Equal weights stop the sparse head from burying
  dense-only deep hits (`jj-split-work` dense rank 26, `jj-conflict-resolution`
  dense rank 23). The 60-document pool admits the sparse-side deep hits
  (`jj-publish-remote` sparse rank 28, `qdrant-archive-collection` sparse rank
  21) and `jj-config-set-user` (sparse rank 29). Nothing about the corpus,
  embedding, or reranker model changed.
- **Cost is superlinear only in documents, not latency.** Doubling the pool
  doubled reranked documents (1.99×) and cost (1.83×) but raised cold p50 by
  11–31% and p95 by 11–12%, because provider latency is not proportional to
  document count. Retrieval is still dominated by the ~1 s rerank call.
- **What the suite can and cannot detect.** 8 single / 16 multi answerable
  questions; retrieval is deterministic, so the reported flips are real, but
  small effects are not certified. The change does not address the four
  vocabulary-gap questions, which remain outside both retrievers' top 50 — that
  is a query/embedding problem, not a pool-depth problem.
- **Confound.** The sparse arm's single-project regression is not a clean
  effect: two of its rerank calls hit the provider TPM limit and fell back to
  raw BM25 order. Hybrid had no failures, so the hypothesis arm is clean.
  A deeper pool can also admit a new candidate that outranks a previously
  found gold — the sparse `jj-config-set-user` single-project 7 → 20 movement is
  that effect — so "no regression" is bounded by this suite.
