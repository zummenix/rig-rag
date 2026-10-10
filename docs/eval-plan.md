# Evaluation harness plan

Status: **design agreed, not yet implemented.** This document records the decisions
taken for preparing `rig-rag` for experiments and evaluation. Work proceeds phase
by phase (see [Phases](#phases)); Phase 1 is minimal Rust-only setup.

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
        "buckets": { "<50": 0, "50-100": 0, "100-200": 0, "200-400": 0, ">400": 0 }
      }
    }
  ],
  "totals": { "documents": 0, "chunks": 0, "tokens": 0, "embeddings": 0 },
  "environment": { "os": "macos", "arch": "aarch64", "rig_rag_commit": "...", "qdrant_url": "..." }
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

### P2 — `ingest --report <path>`

Per-phase timing, portable peak RSS, per-source/total counts, conditional
tiktoken tokens, chunk-token distribution, environment block.

**Acceptance:** report JSON validates against `schema_version: 1`; no tokenizer or
timing work runs without `--report`; `status`/`reused` paths produce a report too.

### P3 — Eval assets + Python retrieval runner

`eval/questions.toml` seed set; per-profile serve lifecycle; `/api/query` k-sweep
runner; metrics + `results.json` + `hits.jsonl`; degradation deltas; subset test.

**Acceptance:** a full single- and multi-project run produces results JSON that
covers every applicable question and profile, with p50/p95 and deltas populated.

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
  --sparse` needs verification across the pinned sources.
- **Profile drift.** Pinned SHAs freeze corpora; decide how/when to refresh pins
  (and whether a refresh is itself an experiment).
- **Latency noise.** Host/Qdrant warmth affects p95; record environment and keep
  warmups/reps consistent; consider containerized runs for comparability later.
- **Result size.** `hits.jsonl` + `results.json` per run can grow; keep raw dumps
  per-run and committed summary small (or gitignore raw dumps if needed).

## Deferred (future passes)

- Chat evaluation (`/api/chat` + answer judging) — keep the seam in mind, build nothing yet.
- Remote/paid embedding models (type change away from the concrete fastembed model).
- Hybrid retrieval with rerank; threshold/method sweeps.
- Adding a source to the multi-project profile as an experiment axis.
