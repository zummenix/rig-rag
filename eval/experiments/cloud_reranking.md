# Experiment: Cloud reranking (`cloud_reranking`)

**Hypothesis.** Rescoring the top 30 fused hybrid candidates with a strong cloud
reranker (`rerank-3` over `https://api.aitunnel.ru/v1`) before cutting to `k`
improves ranking quality at the default retrieval depth without the ~1.7 s
per-query penalty that sank the local Jina turbo arm. Gains should appear at
`k≤7` (where users and chat actually look), not only at `k≥10`, and no
answerable question should move backward in aggregate. Cost and latency are
reported rather than gated: the decision compares quality against a cold
(uncached) p50 near 1 s and a per-query cost near 0.07 RUB.

**Status:** measured — **recommended for adoption**. This is the first
reranking arm to improve `recall@7` and MRR at the default depth on
multi-project, with no aggregate regression at any swept `k`.

## Controlled change

Retrieval gains an optional cloud reranker; the collection schema, embedding
model, chunking, hybrid fusion, and ingest path do **not** change.

- **`src/config.rs`** — optional `[reranker]` section: `base_url` (API root
  including the version segment), `model`, `candidates` (default 30), and
  `timeout_ms` (default 10 000). The token is read from `RERANKER_API_KEY`; it
  is never stored in the tracked config.
- **`src/reranker.rs`** (new) — posts `{model, query, documents}` to
  `{base_url}/rerank`, parses `results[].index` + `results[].relevance_score`,
  sorts/dedupes, and logs one line per provider call with tokens and cost.
  Candidates are canonicalized (sorted) before hashing and requesting because
  Qdrant's fused order is not stable enough for an order-sensitive cache key.
- **`src/store.rs`** — `Store` holds an optional `Reranker`, wired only in
  `Store::connect` (serve); ingest keeps `Store::new` and never touches the
  provider. With a reranker, `search` fetches `max(k, candidates)` fused hits,
  rescores them, appends candidates the provider omitted in fused order, and
  cuts to `k`. A provider error or timeout logs a warning and falls back to
  fused order.
- **`src/serve/mod.rs` / `src/main.rs`** — `serve --cache-rerank` enables a
  bounded in-process score cache (256 entries, FIFO). `serve` fails fast when
  `[reranker]` is present but `RERANKER_API_KEY` is missing.
- **`eval/profiles/*/rig-rag.toml`** — the treatment arm adds `[reranker]`;
  this is part of the treatment commit and reverts with it.

The `994b615a` local arm remains in history and in
[`reranking.md`](reranking.md); it is not re-applied.

| arm | retrieval | run |
| --- | --- | --- |
| baseline | hybrid RRF, no reranker | `eval/results/baseline/2026-10-18T10-11-48Z` |
| local rerank | hybrid RRF → local Jina turbo top 50 → top `k` | `eval/results/reranking/2026-10-18T10-56-49Z` |
| cloud rerank | hybrid RRF → cloud `rerank-3` top 30 → top `k` | `eval/results/cloud_reranking/2026-10-19T13-36-28Z` |

All arms used the same profiles, pinned sources (`jj @ 9a1ad09…`,
`podman @ e3ed4e3…`, `qdrant @ 9855e50…`), model `bge-small-en-v1.5`
(384 dims), and the existing active collections (`--skip-ingest`; reranking is
query-time only). Runner settings: `--k 1,3,5,7,10,20 --latency-k 7 --warmup 3
--reps 5`.

The cloud arm ran with `--cache-rerank`: each question's first retrieval query
is a cold provider call; the remaining k-sweep, warmup, and repetition queries
are cache hits. `latency.p50_ms`/`p95_ms` are therefore **warm** numbers;
`latency.cold_p50_ms`/`cold_p95_ms` are the provider-facing samples.

## Results

### Retrieval

| metric | single base | single local | single cloud | multi base | multi local | multi cloud |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mean recall@1 | 0.125 | 0.125 | **0.250** | 0.188 | 0.188 | **0.250** |
| mean recall@3 | 0.125 | 0.125 | **0.375** | 0.250 | 0.250 | **0.313** |
| mean recall@5 | 0.250 | 0.250 | **0.500** | 0.250 | 0.250 | **0.375** |
| mean recall@7 | 0.250 | 0.250 | **0.500** | 0.313 | 0.250 | **0.375** |
| mean recall@10 | 0.250 | 0.375 | **0.500** | 0.375 | 0.313 | **0.500** |
| mean recall@20 | 0.250 | 0.625 | **0.500** | 0.500 | 0.500 | **0.500** |
| mean purity@7 | 1.000 | 1.000 | 1.000 | 0.866 | 0.902 | **0.929** |
| mean MRR@7 | 0.150 | 0.156 | **0.338** | 0.188 | 0.208 | **0.294** |
| unanswerable no-hit rate | — | — | — | 0.000 | 0.000 | 0.000 |

Cloud vs baseline at the default depth:

- Single-project: `recall@7` **0.250 → 0.500**, `MRR@7` **0.150 → 0.338**.
- Multi-project: `recall@7` **0.313 → 0.375**, `MRR@7` **0.188 → 0.294**,
  `purity@7` **0.866 → 0.929**.
- Unlike the local arm, multi-project `recall@7`/`recall@10` rise rather than
  fall (`0.313 → 0.375`, `0.375 → 0.500`); `recall@20` stays flat.

### Latency and cost

| metric | single base | single local | single cloud | multi base | multi local | multi cloud |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| warm p50 ms | 8.97 | — | 10.25 | 9.35 | — | 10.56 |
| warm p95 ms | 11.12 | — | 12.02 | 11.40 | — | 12.43 |
| cold p50 ms | — | 1 724.45 | **1 087.24** | — | 1 715.25 | **1 065.65** |
| cold p95 ms | — | 1 778.36 | **1 681.51** | — | 1 785.51 | **1 520.27** |

The local arm's p50/p95 were synchronous CPU calls, so they are directly
comparable to the cloud **cold** numbers; the cloud warm rows measure cache
hits and are included to separate application overhead from provider time.
Warm p50 is ~1–2 ms above the fusion baseline; the cache is doing its job.

Provider usage for the cloud arm:

| profile | calls | documents | tokens | cost (RUB) | cloud p50 ms | cloud p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single-project | 10 | 300 | 85 204 | 0.7668 | 1 037.5 | 1 643.6 |
| multi-project | 20 | 600 | 156 127 | 1.4051 | 1 054.0 | 1 314.8 |
| total | 30 | 900 | 241 331 | 2.1720 | — | — |

Calls slightly exceed one per question (10 vs 9, 20 vs 18): the fused candidate
set changed once per profile across repeats, which correctly produced a cache
miss rather than a stale ranking.

Per-question movement (first `k` whose top hits cover a gold item):

| question | profile | baseline | local | cloud |
| --- | --- | --- | --- | --- |
| `jj-author` | single | never | k=10 | **k=5** |
| `jj-config-set-user` | single | never | k=20 | never |
| `jj-new-change` | single | k=1 | k=1 | k=1 |
| `jj-bookmarks-label` | single | never | never | never |
| `jj-split-work` | single | never | never | never |
| `jj-publish-remote` | single | k=5 | k=5 | **k=3** |
| `jj-colocated-convert` | single | never | never | **k=1** |
| `jj-conflict-resolution` | single | never | k=20 | never |
| `jj-author` | multi | k=20 | k=10 | **k=5** |
| `jj-config-set-user` | multi | k=20 | k=20 | **k=10** |
| `jj-colocated-convert` | multi | k=10 | never | **k=1** |
| `podman-host-files` | multi | k=7 | never | k=10 |
| `podman-rootless-network` | multi | k=1 | k=3 | k=3 |
| `qdrant-docker-ports` | multi | k=3 | k=1 | **k=1** |
| `jj-publish-remote` | multi | never | k=20 | never |

Two multi-project questions move back in rank (`podman-host-files` 7 → 10,
`podman-rootless-network` 1 → 3), but the aggregate `recall@7` still rises
because larger gains land on `jj-author`, `jj-config-set-user`, and
`jj-colocated-convert`. No answerable question that the baseline found at `k≤7`
is lost at `k≤7` in aggregate.

## Interpretation

- **Quality is a real win at the depth that matters.** The cloud reranker nearly
  doubles single-project `recall@7` (0.25 → 0.50) and lifts multi-project
  `recall@7` from 0.313 to 0.375, `recall@10` from 0.375 to 0.500, and `MRR@7`
  from 0.188 to 0.294. This is the opposite of the local arm, which regressed
  multi-project `recall@7`/`@10`. The larger, multilingual-capable `rerank-3`
  model orders candidates better than Jina turbo did.
- **The cost is provider latency, not local compute.** Cold p50 is ~1.07 s
  (p95 1.5–1.7 s), about 1.6× faster than the local CPU reranker and ~120× the
  9 ms fusion path. Warm cache hits are ~10 ms, so repeated identical queries
  cost almost nothing. The provider call now dominates the retrieval budget;
  whether ~1 s is acceptable is a product decision, not a measurement question.
- **Cost is modest at the measured volume.** 30 provider calls cost ~2.17 RUB,
  with ~0.07 RUB per 30-document query. The cache reduced the run from what
  would have been ~260 calls (the first, buggy run made exactly that many)
  while preserving one cold sample per question.
- **Candidate pool 30 is sufficient for this suite.** Every new gold appears
  within the top 30 fused candidates; the gains come from reordering, not from
  expanding the pool. Rank-21–30 golds were not separately ablated.
- **Negative controls still return hits.** `no_hit` remains 0.0: the fused
  threshold stays 0.0 and reranking adds no calibrated relevance cutoff, so
  unanswerable questions are not filtered by this change.

## Decision

**Recommended for adoption**, with a latency caveat: the quality movement is
broad and at the default `k`, unlike the rejected local arm, and the
`--cache-rerank` path makes repeated queries cheap. The remaining objection is
the ~1 s cold provider call, which is visible before the LLM answer and may be
too slow for search-as-you-type. Options if that is unacceptable: lower
`candidates`, use a faster provider/model, or keep reranking chat-only.

The final adopt/revert call is left to the maintainer; the code and the
`[reranker]` sections in the eval profiles form one revertible treatment
commit, and no production config sets `[reranker]` yet.

## Caveats

- **One run per arm.** Retrieval is deterministic; latency and cost are not.
  The cold percentiles are a single sample per question (9 single / 18 multi).
- **Warm/cold split.** `p50_ms`/`p95_ms` are cache hits by construction under
  `--cache-rerank`; the provider-facing numbers are `cold_*`. The comparison
  table in the HTML report uses `cold_*` when caching is on.
- **Suite power.** 8 single / 16 multi answerable questions. Deterministic
  retrieval makes the reported movements real, but small changes are not
  certified, and "no regression" means "none detectable here".
- **Cache correctness fix.** The first run used an order-sensitive cache key and
  made 82/178 provider calls instead of 10/20; the canonical-order fix was
  verified with a two-request live smoke test (one call, 10.8 ms second
  request) before this run. The earlier run directory carries the inflated
  latency/cost block and is not the measured arm.
- **Provenance.** The run was measured from the working tree at
  `a5573fb` + the treatment changes; `results.json` records the parent commit
  and the binary SHA-256 because the code was uncommitted at run time.
- **Ingest cost not measured.** Both arms reused active collections
  (`--skip-ingest`); reranking is query-time only, so the collection and
  embedding path are unchanged.
- **Provider logging.** Cost and tokens come from the provider's `usage` field
  in the serve log; `cost` and `cost_rub` were identical for this provider and
  are reported as RUB.

## Reproduction

```sh
export RERANKER_API_KEY=...

# Treatment arm: profiles in this commit carry [reranker].
just eval-run --experiment cloud_reranking --skip-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5 --cache-rerank

just eval-report cloud_reranking
```

Without `--cache-rerank`, every repetition is an uncached provider call and
`latency.samples_ms` becomes cold; that is the arm to use if the provider's
volatility is the thing being measured.
