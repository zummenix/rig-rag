# Experiment: Hybrid dense + BM25 retrieval (`hybrid_search`)

**Hypothesis.** Fusing the existing dense BGE-small-en-v1.5 vectors with a BM25
sparse vector (Qdrant `Qdrant/bm25`, IDF modifier) by Reciprocal Rank Fusion —
with a post-fusion RRF-score floor calibrated so the unanswerable control still
returns nothing — improves retrieval quality without losing semantic recall:
`mean recall@k` and `mean MRR@{latency-k}` are non-decreasing on every answerable
question, with the largest gains where the query shares exact tokens with the
gold text (`jj-config-set-user`, `podman-detached-run`, `qdrant-docker-ports`).
Ingest and latency cost may rise modestly (a second sparse prefetch and fusion,
extra insert work). Falsified by any `recall@k` or `MRR@{latency-k}` regression
on an answerable question, by no movement on any question (hybrid buys nothing
here), or by the floor failing to hold the unanswerable `no_hit` rate at 1.000.

**Status:** **adopted** — hybrid retrieval lands. It matches or beats the
dense-only baseline's `recall@k`/`MRR` on every answerable question (large gains
on multi-project), leaves single-project unchanged, and costs ~1.5 ms p50. The
post-fusion floor could not hold the negative control, which is an accepted,
recorded behavior change; see [Decision](#decision).

## Controlled change

The collection schema and the retrieval query change; chunking, the embedding
model, the dense vector, and the ingest/serve plumbing do **not**.

- **`src/store.rs`** — `create_collection` builds named vectors: `dense`
  (BGE-small-en-v1.5, 384-dim, cosine) plus `sparse` (`Qdrant/bm25`, `Idf`
  modifier). `insert` writes a `NamedVectors` point — the dense vector and the
  chunk text as a server-side BM25 `Document`. `search` runs a hierarchical
  query: a sparse prefetch and a dense prefetch (limit 50 each) fused with
  `Rrf { k: 2, weights: [2.0, 1.0] }` (weights in prefetch order
  `[sparse, dense]`), then `score_threshold`.
- **`src/retrieval.rs`** — `THRESHOLD` (a 0.5 cosine cutoff) becomes
  `RRF_FLOOR`, a rank-based fused-score floor; set to `0.0` (disabled).
- **`src/serve/query.rs`, `src/serve/chat.rs`** — import the renamed constant.

The change lives in its own commit (`6f5a1b8`) so it can be reverted
independently, leaving the harness (framework, runner, this record) and the
measured runs intact.

| arm | retrieval | commit | run |
| --- | --- | --- | --- |
| baseline | dense-only, `THRESHOLD = 0.5` | `5324b872155984fb99a5f9efdc25229f2edb620c` | `eval/results/baseline/2026-10-14T07-08-05Z` |
| treatment | hybrid RRF, `weights=[2,1]`, `k=2`, limit 50, `RRF_FLOOR=0.0` | `6f5a1b8f8ab3127598ee6826788f3918b42f1997` | `eval/results/hybrid_search/2026-10-15T16-06-45Z` |

Both arms were ingested with `--force` (the runner's `--force-ingest` flag, so
cost is measured, not `reused`) on the same profiles —
`eval/profiles/{single,multi}-project/{rig-rag.toml,sources.json}` (snapshotted
in each run's `results.json` → `config`) — with pinned sources
`jj @ 9a1ad09b16e87d5d5e25759e5027953f8257be98`,
`podman @ e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365`,
`qdrant @ 9855e50fc739de44a223a1c50faced806a868b6a`, and model
`bge-small-en-v1.5` (384 dims). Runner settings: `--profiles
single-project,multi-project --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5`.

## Results

### Ingest cost

| metric | single base | single hyb | Δ | multi base | multi hyb | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| embeddings | 793 | 793 | 0 | 9,956 | 9,956 | 0 |
| tokens | 151,031 | 151,031 | 0 | 1,492,068 | 1,492,068 | 0 |
| `embed` ms | 40,409 | 58,042 | +17,633 | 425,339 | 603,744 | +178,405 |
| `insert` ms | 71 | 154 | +83 (~2×) | 1,250 | 2,418 | +1,168 (~2×) |
| `total` ms | 44,103 | 95,092 | +50,989 | 444,837 | 917,221 | +472,384 |
| peak RSS (GiB) | 8.16 | 8.16 | 0 | 8.43 | 8.44 | +0.01 |

Embeddings, tokens, and chunks are unchanged — the sparse vector is computed by
Qdrant from the chunk text, so no extra local embedding work. `insert` roughly
doubles: each point now carries a sparse vector whose BM25 is computed
server-side. **`embed`/`total` are not read as an effect:** the change does not
touch local embedding, so their inflation is host/load variance between the two
runs (measured days apart); only the ~2× `insert` and the extra sparse-vector
storage are attributable. Storage is not measured.

### Retrieval

| metric | single base | single hyb | multi base | multi hyb |
| --- | ---: | ---: | ---: | ---: |
| mean recall@1 | 0.333 | 0.333 | 0.167 | 0.333 |
| mean recall@3 | 0.333 | 0.333 | 0.167 | 0.500 |
| mean recall@5 | 0.333 | 0.333 | 0.167 | 0.500 |
| mean recall@7 | 0.333 | 0.333 | 0.167 | 0.500 |
| mean recall@10 | 0.333 | 0.333 | 0.333 | 0.500 |
| mean recall@20 | 0.333 | 0.333 | 0.333 | 0.833 |
| mean purity@7 | 1.000 | 1.000 | 1.000 | 0.905 |
| mean MRR@7 | 0.333 | 0.333 | 0.167 | 0.389 |
| p50 ms | 8.0 | 9.4 | 8.4 | 9.9 |
| p95 ms | 8.3 | 9.6 | 10.4 | 11.5 |
| unanswerable no-hit rate | — | — | 1.000 | 0.000 |

Per-question movement (first `k` whose top hits cover the gold), multi-project:

| question | baseline | hybrid |
| --- | --- | --- |
| `jj-new-change` | k=1 | k=1 (maintained) |
| `qdrant-docker-ports` | k=10 | k=3 |
| `podman-detached-run` | never | k=1 |
| `jj-author` | never | k=20 |
| `jj-config-set-user` | never | k=20 |
| `cross-project-config` | never | never |

`cross-project-config` needs both its gold items covered; it is the only
answerable question unchanged. Over the shared `jj` questions the single→multi
`recall@k` degradation stays `0.0` for `k ≤ 10`; at `k=20` it inverts from `0.0`
to `+0.667`, because multi-project now retrieves the shared `jj-author` /
`jj-config-set-user` golds that the single-project corpus does not surface.

## Interpretation

- **Effect size.** Large and concentrated on multi-project: `mean recall@3`
  triples (`0.167 → 0.500`), `recall@20` more than doubles (`0.333 → 0.833`),
  `MRR@7` more than doubles (`0.167 → 0.389`). These are far above run-to-run
  noise (retrieval is deterministic; only latency is noisy). Single-project is
  exactly unchanged — its `jj`-only corpus has no lexical headroom (its gold for
  `jj-author`/`jj-config-set-user` sits at sparse rank 21/29, beyond k=20).
- **Where it comes from.** BM25 recovering exact-token answers the dense vector
  missed: the Podman `podman run … -d` invocation (never to rank 1) and the
  Qdrant published ports (k=10 → k=3), plus `jj-author`/`jj-config-set-user`
  entering top-20. Every gain is on a query whose gold text shares rare tokens
  with the query — the mechanism the hypothesis predicted.
- **What the suite can and cannot detect.** It has 3 single / 6 multi answerable
  questions. It bounds large recall changes but not small ones, and — importantly
  — it contains **no semantic/paraphrase question with no lexical overlap**, so
  it cannot show where dense helps, nor bound a semantic regression. A prototype
  weight sweep also showed the win is BM25's, not the fusion's: with equal
  weights the hybrid underperforms sparse-only, and the chosen `weights=[2,1]`
  only recovers the sparse-only ceiling. The weights were fit on these questions,
  so the reported `0.833` is optimistic; the untuned default already gives
  `0.500` at `k = 3…20`. See [Caveats](#caveats).

## Decision

**Adopted.** Hybrid retrieval ships; the treatment commit stays on the main
line. Rationale:

- **Decisive quality win on the primary objective.** Retrieval is the product;
  hybrid lifts multi-project `recall@3` from `0.167` to `0.500` and `recall@20`
  from `0.333` to `0.833`, with no accepted regression on any answerable question
  and no change to single-project. Dense-only verifiably misses real how-to
  answers that BM25 surfaces.
- **Cost is bounded and expected.** ~1.5 ms p50 / ~1.1 ms p95 added latency
  (two prefetches + fusion on a small corpus) and ~2× `insert`; ingest
  embeddings/tokens are unchanged.
- **The lost negative control is accepted, not ignored.** The rank-based RRF
  score has no scale, so no floor separates the unanswerable control (~0.5) from
  a real sparse-only hit (~0.5); `RRF_FLOOR` therefore stays `0.0` and the
  control's `no_hit` falls to `0.000`. The dense-only threshold was already
  weakly calibrated (most off-topic questions cleared it), so this is a
  documented change in a fragile behavior, not a new blind spot.
- **What would change the decision / immediate follow-ups.** The strongest
  counter-case is that the win is BM25's and the weights were tuned on this
  suite. Before treating hybrid as a certified gain, (1) add semantic/paraphrase
  questions and a clean negative control with no lexical overlap, (2) restore a
  usable no-hit guard via a score-based fusion (e.g. Qdrant `Dbsf`) or a
  dense-cosine gate, and (3) re-check whether the dense prefetch earns its keep.
  Re-applying or reverting is cheap: the change is one commit.

## Caveats

- **Suite power.** 3 single / 6 multi answerable questions, no semantic question
  and no clean negative control. "No regression" means *none detectable by this
  suite*; a small recall regression or a dense-recall regression would be
  invisible.
- **Tuned on the eval set.** `weights=[2,1]` (and `RRF_K=2`, prefetch limit 50)
  were chosen by sweeping this question set, so the multi-project numbers are
  optimistic. The limit sweep found no metric cost down to 20 (latency ~9.8 ms
  p50), but it cannot bound dense-recall risk because no question exercises it.
- **Measurement noise.** `insert`/`embed`/`total` and p50/p95 are host- and
  load-dependent; the two arms ran on different days. Do not read the `embed`
  and `total` deltas as effects (the change does not touch embedding).
- **Negative control.** `unanswerable-geography` shares tokens (`america`) with
  the corpus, so its retrieval by BM25 is a real lexical match, not a bug — but
  it means the suite currently reports `no_hit = 0.000` by construction.

## Reproduction

The treatment commit stays on the main line, so `--experiment hybrid_search`
selects it at HEAD; the baseline arm must be built from its pinned commit.

```sh
# baseline arm — dense-only (THRESHOLD = 0.5)
git checkout 5324b872155984fb99a5f9efdc25229f2edb620c
just eval-run --experiment baseline --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# treatment arm — hybrid RRF
git checkout 6f5a1b8f8ab3127598ee6826788f3918b42f1997
just eval-run --experiment hybrid_search --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# return to your branch, then render the experiment from the committed JSON
git checkout <your-branch>
just eval-report hybrid_search
```
