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

**Status:** rejected (not adopted) — reranking helped single-project recall
only at `k≥10`, regressed multi-project `recall@7`/`recall@10`, and cost ~1.7 s
per query; the code is reverted and the record/results remain.

## Controlled change

Retrieval gains a local cross-encoder pass; the collection schema, embedding
model, chunking, hybrid fusion, and ingest path do **not** change.

- **`src/reranker.rs`** (new) — selects
  `RerankerModel::JINARerankerV1TurboEn` (151 MB fp32 ONNX, English-only),
  loads it into the fastembed cache, and exposes a `rerank` helper returning
  `(index, score)` best-first.
- **`src/store.rs`** — the reranker is optional (`rerank_tx: Option`), so
  `Store::new` stays embedding-only and `ingest` never loads a reranker;
  `Store::connect` (the `serve` path) calls `with_reranker`. With a reranker
  configured, `search` asks Qdrant for `RERANK_CANDIDATES = 50` fused
  candidates instead of `k`, rescores them, and cuts to `k`. The fused scores
  are still used for the `threshold` filter, which remains `0.0`.
- **`src/model.rs`** — `rig-rag model` loads the reranker too, so the
  `Containerfile` warm-up bakes both sets of weights into the image.
- **`Containerfile` / `README.md`** — describe the two baked models and the
  query-time reranking step.

The change lives in its own commit (`994b615a`) so it can be reverted
independently, leaving the harness (framework, runner, this record) and the
measured runs intact.

| arm | retrieval | commit | run |
| --- | --- | --- | --- |
| baseline | hybrid RRF, no reranker | `76a006591902b0436f4feb1981e04cdbf41f8749` | `eval/results/baseline/2026-10-18T10-11-48Z` |
| treatment | hybrid RRF → rerank top 50 → top `k` | `994b615a424da2433e872ddc6380b362971cb659` | `eval/results/reranking/2026-10-18T10-56-49Z` |

Both arms ran on the same profiles —
`eval/profiles/{single,multi}-project/{rig-rag.toml,sources.json}` — with
pinned sources `jj @ 9a1ad09b16e87d5d5e25759e5027953f8257be98`,
`podman @ e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365`, and
`qdrant @ 9855e50fc739de44a223a1c50faced806a868b6a`, model
`bge-small-en-v1.5` (384 dims), and runner settings `--k
1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5`.

Both arms reused their already-built active collections (`--skip-ingest`)
rather than re-ingesting, because reranking is query-time only. Ingest cost is
therefore **not measured** in either arm; the baseline run also has no ingest
reports. Retrieval and latency are directly comparable because the collections,
source revisions, and embedding model are identical.

## Results

Ingest cost is **not measured**: both arms reused their active collections
(`--skip-ingest`), so neither run has an `ingest-*.json`. Reranking is
query-time only and does not touch the collection, embedding model, or
chunking. For footprint context (from the published model files, not measured
by this run), the Jina turbo model is 151 MB fp32 ONNX versus 2.27 GB for BGE
reranker v2-m3.

### Retrieval

| metric | single base | single rerank | Δ | multi base | multi rerank | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mean recall@1 | 0.125 | 0.125 | 0 | 0.188 | 0.188 | 0 |
| mean recall@3 | 0.125 | 0.125 | 0 | 0.250 | 0.250 | 0 |
| mean recall@5 | 0.250 | 0.250 | 0 | 0.250 | 0.250 | 0 |
| mean recall@7 | 0.250 | 0.250 | 0 | 0.313 | 0.250 | −0.063 |
| mean recall@10 | 0.250 | 0.375 | +0.125 | 0.375 | 0.313 | −0.063 |
| mean recall@20 | 0.250 | 0.625 | +0.375 | 0.500 | 0.500 | 0 |
| mean purity@7 | 1.000 | 1.000 | 0 | 0.866 | 0.902 | +0.036 |
| mean MRR@7 | 0.150 | 0.156 | +0.006 | 0.188 | 0.208 | +0.021 |
| p50 ms | 8.969 | 1,724.447 | +1,715.5 (192×) | 9.352 | 1,715.247 | +1,705.9 (183×) |
| p95 ms | 11.117 | 1,778.355 | +1,767.2 (160×) | 11.397 | 1,785.508 | +1,774.1 (157×) |
| unanswerable no-hit rate | — | — | — | 0.000 | 0.000 | 0 |

Per-question movement (first `k` whose top hits cover a gold item):

| question | profile | baseline | reranked |
| --- | --- | --- | --- |
| `jj-author` | single | never | k=10 |
| `jj-config-set-user` | single | never | k=20 |
| `jj-conflict-resolution` | single | never | k=20 |
| `jj-publish-remote` | single | k=5 | k=5 (MRR 0.200 → 0.250) |
| `jj-author` | multi | k=20 | k=10 |
| `jj-publish-remote` | multi | never | k=20 |
| `jj-conflict-resolution` | multi | never | k=20 |
| `qdrant-docker-ports` | multi | k=3 | k=1 |
| `podman-rootless-network` | multi | k=1 | k=3 |
| `podman-host-files` | multi | k=7 | never |
| `jj-colocated-convert` | multi | k=10 | never |

`jj-bookmarks-label` purity falls 0.71 → 0.43 on multi-project; no other
question loses source purity. Over the shared `jj` questions the single→multi
`recall@10` degradation grows from `0.0` to `−0.125`, and the baseline's
`+0.25` `recall@20` advantage disappears to `0.0`.

## Interpretation

- **Effect size.** This is not a clear quality win. Single-project improves only
  at `k=10`/`k=20` (`+0.125`/`+0.375`); `k≤7`, the default retrieval depth, is
  unchanged. Multi-project regresses at `recall@7` and `recall@10`
  (`0.313 → 0.250`, `0.375 → 0.313`) and is flat at `recall@20`. The small
  positive aggregates (`MRR@7 +0.021`, `purity@7 +0.036`) are net movements
  from a few questions, not a broad improvement. Retrieval is deterministic, so
  these changes are signal, not run-to-run noise.
- **Where it comes from.** The reranker promotes some fused-rank-11–50 golds
  into the top 20 (`jj-author`, `jj-publish-remote`, `jj-conflict-resolution`,
  and `qdrant-docker-ports` 3 → 1) but demotes previously found golds
  (`jj-colocated-convert` 10 → never, `podman-host-files` 7 → never, and
  `podman-rootless-network` 1 → 3). On multi-project those cancel at `k=20`, so
  the headline recall is flat. On single-project every gain is at `k≥10`, which
  the default chat/query path (`k=7`) does not see.
- **Cost.** p50 is ~1.72 s versus ~9 ms — roughly **two orders of magnitude**,
  not the one order the hypothesis allowed — and p95 is ~1.78 s. The reranker
  runs synchronously on CPU for all 50 candidates, so it dominates the
  retrieval budget. The 151 MB model is a large disk/memory improvement over
  the 2.27 GB v2-m3, but it does not buy compute.
- **What the suite can and cannot detect.** 8 single / 16 multi answerable
  questions, deterministic retrieval. That is enough to establish the reported
  movements, but not to certify small ones. The reranker tokenizer truncates
  each chunk at 512 tokens, so long chunks can lose the evidence that made them
  relevant. The negative controls still return hits (`no_hit` 0): the fused
  `threshold` remains 0.0 and reranking adds no relevance cutoff.

## Decision

**Not adopted.** Reranking is reverted; the measured commit `994b615a` stays in
history, and this record plus the committed results remain for reproducibility.

- **Quality is mixed, not a win.** Multi-project `recall@7` and `recall@10`
  regress (`0.313 → 0.250`, `0.375 → 0.313`) and `recall@20` is flat. The
  single-project gain exists only at `k=10`/`k=20`, while the default retrieval
  depth (`k=7`, used by chat and `/api/query` when `k` is absent) sees no recall
  gain and only `+0.006` MRR. The small aggregate improvements come from a
  handful of questions, and they are offset by demotions of previously found
  golds on multi-project.
- **Latency is disqualifying.** p50 rises from ~9 ms to ~1.72 s (183–192×) and
  p95 to ~1.78 s. That is two orders of magnitude, not the one order the
  hypothesis allowed, and it dominates the retrieval budget before the LLM
  call. Reranking all 50 candidates synchronously on CPU is the cause; the
  151 MB model is only smaller than v2-m3, not faster.
- **The falsification condition is met.** The hypothesis required a gain large
  enough to justify the latency and footprint. The measured quality movement is
  small, regresses the default-depth multi-project metrics, and does not
  improve the default `k`.
- **What would change the decision.** A much faster reranker (int8/quantized,
  GPU, or a smaller candidate pool) that keeps p50 within an interactive
  budget, or a reranker whose cross-encoder ordering clearly beats RRF at
  `k≤7`. Re-run this suite before adopting; re-applying the code is cheap
  because `994b615a` is a single commit. The code changes from that commit — the
  optional reranker seam in `Store`, the `model`-subcommand warm-up, and the
  Containerfile/README updates — are reverted with it and would need to be
  re-applied.

## Caveats

- **Suite power.** 8 single / 16 multi answerable questions. Retrieval is
  deterministic, so the reported movements are real, but the suite cannot
  certify small changes, and "no regression" means "none detectable here".
- **Ingest cost not measured.** Both arms reused existing collections
  (`--skip-ingest`), so neither run has an ingest report. This is safe for the
  conclusion because reranking is query-time only and the collection/embedding
  path is unchanged, but the ingest-cost table is intentionally omitted rather
  than filled with an estimate.
- **Latency is host-dependent.** The ~1.7 s p50/p95 was measured on this ARM
  host's CPU with 50 candidates per query; a different host, a quantized model,
  or a GPU could change the absolute numbers. The relative conclusion (much
  slower than ~9 ms) is robust.
- **Reranker input truncation.** fastembed truncates each query/document pair at
  512 tokens. Long chunks can lose the evidence that made them relevant, which
  may explain some of the demotions; this was not separately ablated.
- **No relevance cutoff.** The fused `threshold` remains `0.0`, so every query
  returns `k` hits and the negative controls report `no_hit = 0` by
  construction. Reranking does not add a calibrated cutoff.
- **English-only model.** `jinaai/jina-reranker-v1-turbo-en` is English-only;
  the corpus is English, so this run cannot say how it behaves on other
  languages.
- **One run per arm.** Retrieval is deterministic, but latency is not; the
  p50/p95 values are a single five-repetition sample per question.

## Reproduction

The treatment code is reverted, so `--experiment reranking` no longer selects
it at HEAD. Build each arm from its pinned commit. Both commands use
`--skip-ingest`, matching the measured run; the profile configs already point
at the built collections (`eval-single-project-bge-small-en-v1-5-f017d3d86bc1`
and `eval-multi-project-bge-small-en-v1-5-a3ea89d89f3c`).

```sh
# baseline arm — hybrid RRF, no reranker
git checkout 76a006591902b0436f4feb1981e04cdbf41f8749
just eval-run --experiment baseline --skip-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# treatment arm — rerank top 50, cut to k
git checkout 994b615a424da2433e872ddc6380b362971cb659
just eval-run --experiment reranking --skip-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# return to your branch, then render the comparison from the committed JSON
git checkout <your-branch>
just eval-report reranking
```


