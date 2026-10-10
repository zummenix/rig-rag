# Evaluation harness plan

Status: **P1–P3 implemented; P4–P5 pending.** This document records the decisions
taken for preparing `rig-rag` for experiments and evaluation. Work proceeds phase
by phase (see [Phases](#phases)); the Rust plumbing (P1–P2) and the Python
retrieval runner (P3) have landed, and P3 is validated end-to-end on **both**
profiles (degradation deltas populated).

## Goal

Make retrieval quality, cost, and latency measurable and reproducible on stable
corpus profiles, so controlled changes (embedding model, chunking, retrieval
strategy, added sources — and later chat) can be evaluated against a baseline
and reported as self-contained HTML.

## Guiding principles

- **Reuse, don't fork.** Profiles are pairs of the existing `rig-rag.toml` +
  `sources.json`; eval drives the real `ingest`/`serve` code paths.
- **Prod stays byte-for-byte unchanged.** With no new flags, behavior, collection
  names (`docs-*`), data root (`data/`), and the serve contract are exactly as today.
- **Instrumentation is opt-in.** Anything expensive (tokenizer, per-phase timing)
  runs only when `--report` is passed.
- **Everything is reproducible from a commit.** Corpus sources are pinned to
  commit SHAs; an experiment records the commit it was measured at.

## Decisions

| Topic | Decision |
| --- | --- |
| Harness home | Rust additions land in the **main binary** (global flags/subcommands); orchestration, metrics, and reports are a **Python** layer under `eval/`. |
| Corpus profiles | A profile = a directory `eval/profiles/<name>/{rig-rag.toml,sources.json}`. |
| CLI flags | `--config <path>` and `--sources <path>` are **global** on all subcommands. |
| New config | `[corpus] { prefix, data_root }` — `prefix` default `docs`, `data_root` default `data`. |
| Collection naming | `<prefix>-<model-slug>-<hash12>`; prod prefix `docs`. |
| promote / prune | `promote` writes `active` into the selected `--config`. `prune` is **scoped to the config's prefix**. |
| Pinning | `ref` accepts branch, tag, or **full commit SHA** (detached checkout); the resolved full SHA is always recorded; `ingest --pin` rewrites refs to SHAs. |
| Ingest report | `ingest --report <path>` writes one JSON file; **nothing extra is gathered when absent.** |
| Tokenizer | `tiktoken` (`cl100k_base`) — an approximation for BGE WordPiece, fine for relative/cost ballparks. |
| Ground truth | Gold = `{source, path, lines?, must_contain?}`; `gold: []` = unanswerable. Robust to re-chunking. |
| Retrieval knobs | Stay **hardcoded**; an experiment edits the code/const. Eval hits `/api/query`; no HTTP contract change. |
| Eval metrics | Per question × profile × k∈{1,3,5,7,10,20}: hits, hit@k, recall@k, precision@k, MRR@k, source purity; p50/p95 latency; single→multi **degradation deltas**. |
| Question set | One versioned `eval/questions.toml` with `applies_to`; single-project ⊂ multi-project is structural. |
| Experiment record | One `eval/experiments/<id>.md` per experiment: narrative + embedded JSON summary + the exact `sources.json`/`rig-rag.toml` used + the measured **git commit SHA**. |
| Reports | Shared framework + **one thin `report.py` per experiment** → self-contained HTML. Results JSON is a **versioned, unit-tested contract** so old experiments stay renderable. |
| Report design | Neutral technical-report style built from scratch; inline SVG charts; no external assets. |
| Commit identity | Record the **git commit SHA** (not a jj change-id) — portable for git-only users; jj users get the same SHA. |
| Commit reachability | The measured commit stays in history (only code is reverted, in a new commit); reports persist. |

## Corpus profiles

```
eval/profiles/
  single-project/
    rig-rag.toml     # [corpus] prefix = "eval-single-project", data_root = "data/single-project"
    sources.json     # jj pinned to a commit SHA
  multi-project/
    rig-rag.toml     # [corpus] prefix = "eval-multi-project", data_root = "data/multi-project"
    sources.json     # jj + podman + qdrant, each pinned
```

- Same Qdrant instance; isolation comes from the collection **prefix** and the
  per-profile **data root** (so profiles never clobber prod `data/jj` or each other).
- Each profile owns its own `[collection].active`, so `promote --config <profile>`
  cannot touch prod.
- Prod keeps `sources.json` (bare array) + `rig-rag.toml` (prefix `docs`, root `data`).

## Rust changes (prod-safe)

1. **Global flags** `--config <path>` / `--sources <path>` on every subcommand
   (`ingest`, `serve`, `promote`, `prune`, `model`); defaults `rig-rag.toml` /
   `sources.json`. `Config::load` and `Sources::load` take the path.
2. **`[corpus]` section** in the config: `prefix`, `data_root` (both defaulted).
   `fetch`/`ingest` place checkouts under `data_root`; `hashing::collection_name`
   and `store::verify_model` use `prefix`.
3. **Pinning**: `fetch_git` detects a full SHA in `ref` and does a detached
   checkout (shallow + sparse preserved); after checkout it records
   `git rev-parse HEAD`. `ingest --pin` rewrites branch refs to resolved SHAs in
   the sources file.
4. **`prune` scoping**: delete only collections whose name starts with
   `<prefix>-`. (Behavior change; for a prod-only setup with prefix `docs` this
   matches today.)
5. **`ingest --report <path>`**: see below.

## Ingest report schema (`--report`)

```jsonc
{
  "schema_version": 1,
  "status": "created",                 // created | reused | rebuilt
  "profile": "single-project",
  "config": "eval/profiles/single-project/rig-rag.toml",
  "sources": "eval/profiles/single-project/sources.json",
  "collection": "eval-single-project-bge-small-en-v1-5-1a2b3c4d5e6f",
  "prefix": "eval-single-project",
  "force": false,
  "model": { "slug": "bge-small-en-v1.5", "dimensions": 384 },
  "timing_ms": { "fetch": 0, "load": 0, "chunk": 0, "embed": 0, "insert": 0, "total": 0 },
  "memory": { "peak_rss_bytes": 0, "cgroup_peak_bytes": null },
  "tokenizer": "tiktoken:cl100k_base",  // null when tokens are not computed
  "sources_detail": [
    {
      "name": "jj", "url": "https://github.com/jj-vcs/jj",
      "ref": "abcdef...", "resolved_commit": "abcdef...",
      "documents": 0, "chunks": 0, "tokens": 0,
      "chunk_tokens": {
        "min": 0, "max": 0, "mean": 0.0, "p50": 0, "p90": 0,
        "buckets": { "<50": 0, "50-99": 0, "100-199": 0, "200-399": 0, ">=400": 0 }
      }
    }
  ],
  "totals": { "documents": 0, "chunks": 0, "tokens": 0, "embeddings": 0 },
  "environment": { "os": "macos", "arch": "aarch64", "qdrant_url": "..." }
}
```

Notes:

- Peak RSS is reported via `libc::getrusage` so it works on macOS (today's
  `/proc/self/status` path is Linux-only); cgroup peak stays Linux-container-only.
- Token counts are **not** gathered unless `--report` is passed (no tokenizer cost
  in normal ingest). Chunk-token distribution feeds the "drop small chunks" experiment.

## Question set (`eval/questions.toml`)

```toml
[[question]]
id = "jj-author"
question = "Who is the author of jj?"
applies_to = ["single-project", "multi-project"]
gold = [{ source = "jj", path = "testimonials.md", must_contain = "project creator and leader" }]
notes = "Answer is worded 'creator and leader'; the word 'author' only appears in unrelated design-doc metadata."

[[question]]
id = "cross-project-config"
question = "How do I set a configuration value in the CLI?"
applies_to = ["multi-project"]
gold = [
  { source = "jj", path = "config.md" },
  { source = "podman", path = "..." },
]
notes = "Terms are common to several projects — measures cross-corpus competition."

[[question]]
id = "unanswerable-license"
question = "Which license did the maintainers choose, and why?"
applies_to = ["multi-project"]
gold = []
notes = "Expected: no hit above threshold; no confident answer."
```

Rules:

- A question applies to a profile only when **every** `gold.source` exists in that
  profile; a test asserts every `single-project` question is also `multi-project`.
- Gold matching: a retrieved `DocHit` covers a gold item when `path` matches **and**
  (no `lines`, or line ranges overlap) **and** (no `must_contain`, or the snippet
  occurs in the hit text).
- `gold: []` marks an **unanswerable** question (metrics report no-hit-above-threshold).

### Seed question categories (to author in P3)

- **Factual / semantic** — e.g. `jj-author` (paraphrase gap: "author" vs "creator").
- **How-to / procedural** — commands, config, setup.
- **API / reference** — flags, options, exact identifiers.
- **Cross-project ambiguity** — same term in several projects (tests purity).
- **Unanswerable / negative** — not in the corpus, or requires synthesis across docs.

Build the multi-project set first; the single-project set is its `jj`-only subset.

## Query evaluation runner (Python)

Flow per profile:

1. `rig-rag ingest --config <profile> --sources <profile> [--report ...]`
2. `rig-rag promote --config <profile> <collection>`
3. `rig-rag serve --config <profile> --bind 127.0.0.1:<port>`; wait for `/api/health`.
4. For each applicable question × k∈{1,3,5,7,10,20}: `POST /api/query {"question","k"}`.
   - Retrieval is deterministic → correctness computed once.
   - Latency: warmup + `R=5` sequential calls → p50/p95 (concurrency 1).
5. Tear down the server.

Metrics recorded per (question, profile, k): returned hits + scores, `hit@k`,
`recall@k`, `precision@k`, `MRR@k`, **source purity** (share of hits from the gold
source). Unanswerable questions report no-hit rate. Cross-corpus **degradation** is
the single→multi delta in `recall@k`, `purity`, and `p95`.

Outputs:

- `eval/results/<experiment-id>/<run>/results.json` — versioned, the render input.
- `eval/results/<experiment-id>/<run>/hits.jsonl` — one line per returned hit for
  offline analysis.

Candidate `results.json` shape (subject to P3 refinement):

```jsonc
{
  "schema_version": 1,
  "experiment": "<id>",
  "run": "2026-10-10T12:00:00Z",
  "commit": "<git commit SHA>",
  "config": { /* profile sources + config snapshots */ },
  "environment": { /* os, arch, rust commit, qdrant url, python deps */ },
  "profiles": {
    "single-project": {
      "collection": "...",
      "latency": { "warmup": 3, "reps": 5, "p50_ms": 0, "p95_ms": 0 },
      "questions": [
        { "id": "jj-author",
          "by_k": { "1": { "hit": 1, "recall": 1.0, "precision": 0.1, "mrr": 1.0, "purity": 1.0, "hits": ["..."] } },
          "latency_ms": [0, 0, 0, 0, 0] }
      ]
    }
  },
  "degradation": { "single-project->multi-project": { "recall@7": 0.0, "purity": 0.0, "p95_ms": 0 } },
  "summary": {}
}
```

## Reporting framework

- `eval/report/framework/` — results loader (version-aware), inline-SVG chart
  primitives (recall@k line, latency bars, degradation deltas), and a layout.
- `eval/experiments/<id>/report.py` — a thin per-experiment script using the framework.
- Output: `eval/reports/<id>.html` — self-contained (all CSS/SVG inline, no network).
- **Compatibility is a tested contract:** `eval/report/tests/` holds a fixture
  `results.json` per `schema_version` and asserts it still renders; the framework
  refuses unknown future versions with a clear message.
- The results JSON is a committed artifact, so any previous experiment can be
  re-rendered.

## Experiment lifecycle (jj + git)

The runner records the **git commit SHA** (via `git rev-parse HEAD`), not a jj
change-id, so a git-only reader can check out the exact measured code.

1. Create the controlled code change on a bookmark/branch.
2. Run `ingest` (with `--report`) and the query-eval runner against it.
3. Write `eval/experiments/<id>.md`: hypothesis, controlled change, measured
   git commit SHA, embedded JSON summary, and the profile `sources.json`/`rig-rag.toml`.
4. Commit the report. If the experiment is unsuccessful, **revert only the code**
   in a new commit and merge — the measured commit stays in history, so the hash
   remains reproducible while the eval docs persist.

## Phases

### P1 — Minimal Rust eval plumbing (prod-safe)

Global `--config`/`--sources`; `[corpus] { prefix, data_root }`; SHA-in-`ref`
detached checkout + `--pin` + resolved-SHA output; prefix-scoped `prune`; the
`eval/profiles/{single-project,multi-project}` config/sources pairs.

**Acceptance:** prod `ingest`/`serve` unchanged with no flags; a profile builds a
collection named `eval-<profile>-<model>-<hash>` under `data/<profile>/` and never
touches `docs-*` or `data/`; `--pin` rewrites refs to SHAs; unit tests cover
collection naming, prefix-scoped prune, and SHA checkout.

**Done.** Code lands in `src/{main,config,sources,hashing,fetch,ingest,promote,prune,store,serve/mod,model}.rs`;
profiles under `eval/profiles/`. `just check` (fmt + clippy `-D warnings` + tests)
passes. Unit tests cover custom-prefix collection naming, `[corpus]` parsing,
`Source::dir` roots, `write_refs`, prefix-scoped prune, `is_full_sha`, and an
offline detached-SHA checkout (`fetch_git_checks_out_full_sha_detached`, local
`file://` origin). Profile pins recorded 2026-10-10:

| source | ref | commit |
| --- | --- | --- |
| jj | `main` | `9a1ad09b16e87d5d5e25759e5027953f8257be98` |
| podman | `main` | `e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365` |
| qdrant | `master` | `9855e50fc739de44a223a1c50faced806a868b6a` |

Known gap: the sparse (`--filter=blob:none --sparse`) + SHA path is implemented
but only exercised offline by a non-sparse test; smoke-test a real profile
ingest before P3 (see open risks).

### P2 — `ingest --report <path>`

Per-phase timing, portable peak RSS, per-source/total counts, conditional
tiktoken tokens, chunk-token distribution, environment block.

**Acceptance:** report JSON validates against `schema_version: 1`; no tokenizer or
timing work runs without `--report`; `status`/`reused` paths produce a report too.

**Done.** `src/report.rs` defines the versioned schema (`schema_version: 1`),
the `tiktoken:cl100k_base` counter, the chunk-token summary (`min`/`max`/`mean`/
`p50`/`p90` + `<50`/`50-99`/`100-199`/`200-399`/`>=400` buckets), and the
`environment`/`memory` blocks. `ingest --report <path>` writes it; per-phase
timings (`fetch`/`load`/`chunk`/`embed`/`insert`/`total`), per-source counts,
and totals are gathered only when the flag is given (`status` is
`created`/`reused`/`rebuilt`, and the `reused` no-op path writes a report too).
Portable peak RSS now comes from `libc::getrusage` (macOS bytes, Linux KiB) so
the printed "Peak RSS" works on the dev host; cgroup v2 peak stays
Linux-container-only. Counts and tokenization are skipped entirely without
`--report`. `just check` passes; unit tests cover the schema round-trip, the
tokenizer, bucket/percentile math, profile derivation, report writing, and
per-source aggregation.

The report deliberately does **not** record a rig-rag commit: the runtime image
has no `.git` and no `git` binary, so runtime detection would be null in exactly
the containerized runs we care about, and it would report the surrounding
checkout rather than the built binary. The Python runner owns the measured
commit in `results.json`.

Known gap: token counts use `cl100k_base`, not BGE's WordPiece vocabulary
(approximation only; see open risks).

### P3 — Eval assets + Python retrieval runner

`eval/questions.toml` seed set; per-profile serve lifecycle; `/api/query` k-sweep
runner; metrics + `results.json` + `hits.jsonl`; degradation deltas; subset test.

**Acceptance:** a full single- and multi-project run produces results JSON that
covers every applicable question and profile, with p50/p95 and deltas populated.

**Done.** `eval/questions.toml` holds the seed set: three `jj` questions applying
to both profiles (paraphrase, how-to, and reference), plus `podman`, `qdrant`,
one cross-project, and one unanswerable question applying to multi-project only.
The runner lives under `eval/runner/` (`profiles`, `gold`, `metrics`,
`httpquery`, `serve`, `rigrag`, `results`, `run`, `__main__`) and is stdlib-only
(Python 3.11+, no third-party packages). It discovers profiles, validates the
question set (asserting the single⊆multi subset and gold-source applicability),
runs the real `ingest --report` → `promote` → `serve` lifecycle on a free
loopback port, sweeps the contract grid k∈{1,3,5,7,10,20} by default (`--k`
may select any non-empty subset, recorded top-level as `k_values`), measures
p50/p95 at `--latency-k` (default 7, and validated to be one of `--k` before any
work), and writes `eval/results/<experiment>/<run>/{results.json,hits.jsonl}`
plus the ingest report and serve log. `results.json` is a versioned contract
(`schema_version: 1`) carrying per-question `by_k` metrics for the recorded
`k_values`, per-profile `summary` aggregates (including the unanswerable no-hit
rate), and `single-project->multi-project` `degradation` deltas over the shared
questions — every `@k` label is derived from the recorded set and the run's
`latency_k`, never a hardcoded default.
`eval/tests/` (65 offline `unittest`s, including a stub HTTP server) covers
question parsing/validation, gold matching, metric math, the results contract
(a `k` subset and a non-default `latency_k` are locked by regression tests), CLI
`--k`/`--latency-k` validation, profile parsing, binary resolution, and profile
selection. `just eval-test` / `just eval-run`, `.gitignore` entries for raw
dumps, and `eval/README.md` document it.

Validated end-to-end on **both profiles** (2026-10-10): the pinned sparse+SHA
fetch (the P1 known gap) succeeded for `jj`, `podman`, and `qdrant` at the exact
SHAs in the P1 table. `single-project` ingested 52 documents / 793 chunks
(`eval-single-project-…-f017d3d86bc1`, 9.0 GiB peak RSS); `multi-project`
ingested 3,718 documents / 9,956 chunks across the three sources
(`eval-multi-project-…-a3ea89d89f3c`). Both `serve` lifecycles booted and tore
down cleanly; `results.json` is exactly reproducible from `hits.jsonl` (checked
by re-deriving every `by_k` entry through the gold matcher). Baseline
(`commit f57809d`): single p50 8.10 ms / p95 10.17 ms, `mean_recall@k = 1/3`;
multi p50 9.43 ms / p95 11.33 ms, `mean_recall@10 = 1/3`; the shared-question
degradation is `recall@k = 0.0`, `purity@7 = 0.0`, `p95_ms = +1.15 ms` (adding
podman + qdrant did not change `jj` recall at these k).

Baseline findings (real retrieval behavior, not harness bugs):

- `jj-new-change` hits at k=1; `jj-author` never surfaces the paraphrase marker
  in the top 20; `jj-config-set-user` retrieves the phrase from a different file
  (rejected by the gold path check) in both profiles.
- `qdrant-docker-ports` is a hit from k=8 up (the `docker run -p 6333…`
  quickstart chunk ranks 8th), which is why multi `mean_recall` rises from 1/6
  at k=7 to 1/3 at k=10.
- The **unanswerable** question returns hits at every k (`no_hit_rate = 0.0`)
  at `THRESHOLD = 0.5`: semantically-adjacent `jj` config chunks clear the
  threshold. Either the threshold is too low for a clean negative or the
  question needs to be further from the corpus — worth resolving before the
  baseline is frozen.

Known gap: the runner inherits `serve`'s requirement for `OPENROUTER_*` because
`serve` builds the chat agent at startup, even though retrieval needs no LLM.
Running retrieval-only without a key would need an opt-in `serve` flag; deferred
to keep production behavior unchanged.

### P4 — Report framework

`eval/report/framework/`, `eval/experiments/<id>/report.py`, `eval/reports/<id>.html`;
versioned-schema fixtures + compatibility tests.

**Acceptance:** a committed `results.json` (and a prior-version fixture) render to
self-contained HTML; unknown schema versions fail loudly.

### P5 — First experiment: drop small chunks

**Hypothesis:** skipping embedding/insertion of chunks below a token threshold
reduces ingest cost (time, memory, tokens) with no material loss in recall@k.

**Change:** a configurable/code-level minimum chunk-token threshold in ingest;
`ingest --report` supplies the chunk-token distribution and cost deltas; the
query-eval runner supplies recall/purity/latency deltas versus the baseline.

## Open questions / risks

- **tiktoken fidelity.** `cl100k_base` is not BGE's WordPiece tokenizer; token
  numbers are approximations. Revisit with an HF `tokenizers` path if embedding
  cost math needs to be exact (drop-in for the `tokenizer` field).
- **Sparse-`--pin` correctness.** Detached checkout for a SHA with `--filter=blob:none
  --sparse` is verified for `jj`, `podman`, and `qdrant` (both profiles,
  2026-10-10); each checked out at its pinned SHA.
- **Profile drift.** Pinned SHAs freeze corpora; decide how/when to refresh pins
  (and whether a refresh is itself an experiment).
- **Latency noise.** Host/Qdrant warmth affects p95; record environment and keep
  warmups/reps consistent; consider containerized runs for comparability later.
- **Result size.** `hits.jsonl` + `results.json` per run can grow; keep raw dumps
  per-run and committed summary small (or gitignore raw dumps if needed).
- **Artifact path portability — TODO before freezing a baseline.** The runner
  records absolute host paths in committed artifacts: `results.json` →
  `environment.binary` and each `ingest-*.json` → `config`/`sources` are
  `/Users/<user>/…`. Record repo-relative paths instead (the runner already runs
  with `cwd=repo_root`, so pass relative `--config`/`--sources` and store a
  relative binary path), then re-run. Not yet implemented.
- **Measured-commit reachability under jj — TODO before freezing a baseline.**
  The plan requires the measured commit to stay in history, but in this
  jj-colocated repo `git rev-parse HEAD` at run time can capture a transient jj
  export that a later jj operation abandons. The multi-project baseline recorded
  `f57809d`, which is reflog-only and **not** an ancestor of `HEAD` — i.e.
  unreproducible from a clean clone. Have the runner verify (or make) the
  measured commit reachable — e.g. tag/bookmark it at run time, or run from a
  stable git commit that won't be rewritten. The abandoned
  `eval/results/baseline/*` candidates should be dropped rather than committed.
- **Unanswerable question is not a clean negative.** At `THRESHOLD = 0.5` the
  `unanswerable-license` question returns hits at every k (`no_hit_rate = 0.0`).
  Resolve (raise the threshold, or move the negative further from the corpus)
  before freezing the baseline, since P5's "no material loss" is measured
  against it.

## Deferred (future passes)

- Chat evaluation (`/api/chat` + answer judging) — keep the seam in mind, build nothing yet.
- Remote/paid embedding models (type change away from the concrete fastembed model).
- Hybrid retrieval with rerank; threshold/method sweeps.
- Adding a source to the multi-project profile as an experiment axis.
