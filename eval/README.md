# Evaluation harness (`eval/`)

Retrieval quality, cost, and latency for `rig-rag`, measured on the real
`ingest` → `promote` → `serve` pipeline rather than a reimplementation. It pins
corpus profiles, sweeps a versioned question set across a range of `k`, and
renders every run as a self-contained HTML report, so a controlled change
(embedding model, chunking, retrieval strategy, an added source — and later
chat) can be compared against a baseline.

Guiding principles: **measure the real system** (profiles are pairs of the
existing `rig-rag.toml` + `sources.json`, and eval drives the actual
ingest/serve code paths); **instrumentation is opt-in** (tokenizer and per-phase
timing run only under `--report`); **reproducible from a recorded revision**
(corpus sources are pinned to SHAs, and a run records the revision it was
measured at, best effort — the experiment record carries the human-facing
story); and **versioned, tested contracts** (the results and ingest schemas ship
with committed fixtures; `results.json` v1 is refused by design because its
latency mixed warm cache hits with real provider calls).

Layout:

```
eval/
  questions.toml          # versioned question set (gold evidence per question)
  profiles/<name>/        # a profile = rig-rag.toml + sources.json
  runner/                 # Python orchestration, metrics, results contract
  report/framework/       # self-contained HTML reporting (loader, SVG, layout)
  report/tests/           # report compatibility/render tests + fixtures
  experiments/<id>/report.py    # thin per-experiment render script
  tests/                  # offline unit tests (stdlib unittest)
  results/<experiment>/<run>/   # results.json, hits.jsonl, ingest/serve logs
  reports/<experiment>.html     # generated HTML (gitignored; regenerate on demand)
```

## Quickstart

```sh
just eval-test                          # offline eval unit tests
just eval-run --experiment baseline     # ingest → promote → serve → sweep → results
just eval-report baseline               # render the newest run as self-contained HTML
```

`just eval-run` needs Qdrant and `OPENROUTER_*` (see Requirements). For the full
flag lists, run `just eval-run-help` and `just eval-report-help`.

## Experiments

| Experiment | Status |
| --- | --- |
| [Drop small chunks](experiments/drop_small_chunks.md) | **Rejected** (not adopted) — a small, corpus-skewed ingest-cost win (−4.5% multi-project embeddings, almost all `podman` fragments) that the question suite is too weak to certify as recall-safe |
| [Bound chunk size to the embedding window](experiments/chunk_max_tokens.md) | **Rejected** (not adopted) — capping chunks at 384 `o200k_base` tokens fixed BGE-window truncation mechanically but regressed `jj-new-change` (rank 1 → beyond k=10); MRR down and cost up |
| [Hybrid dense + BM25 retrieval](experiments/hybrid_search.md) | **Adopted** — fusing a server-side BM25 sparse vector with the dense BGE vector by weighted RRF lifted multi-project `recall@20` 0.333 → 0.833 (`recall@3` 0.167 → 0.500) with single-project unchanged, at ~1.5 ms p50 and ~2× insert; the negative-control `no_hit` falls to 0 and the weights were tuned on the suite (both recorded as caveats) |
| [Cross-encoder reranking](experiments/reranking.md) | **Rejected** (not adopted) — a 151 MB Jina turbo reranker over the top 50 fused candidates promoted a few golds into the top 20 but regressed multi-project `recall@7`/`recall@10`, left `recall@20` flat, and cost ~1.7 s per query (~180–190×); the code was reverted |
| [Cloud reranking](experiments/cloud_reranking.md) | **Measured — recommended for adoption** — a cloud `rerank-3` pass over the top 30 fused candidates lifted single-project `recall@7` 0.250 → 0.500 and multi-project `recall@7`/`recall@10` 0.313/0.375 → 0.375/0.500 (`MRR@7` 0.188 → 0.294, purity@7 0.866 → 0.929); cold p50 ~1.1 s, warm cache-hit p50 ~10 ms, 30 calls cost 2.17 RUB |

Each experiment is an `eval/experiments/<id>.md` record plus a run under
`eval/results/<id>/`; add a row here when a new one lands. The `baseline` id is
the reference run (no record document — just the committed runs and its
`report.py`).

## Requirements

- Python **3.11+** (uses `tomllib`). Standard library only — no virtualenv,
  no third-party packages.
- A running Qdrant reachable at the profile's `[qdrant].url`.
- `git` on `PATH` (profiles pin corpus sources to commit SHAs).
- `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME` in the environment: `serve`
  boots the chat agent at startup even though `/api/query` needs no LLM, so the
  retrieval harness currently inherits that requirement.
- `RERANKER_API_KEY` when the selected profiles carry a `[reranker]` section
  (the `cloud_reranking` treatment arm does); `serve` fails fast without it.

## Profiles

A profile pairs a `rig-rag.toml` with a `sources.json` — the same files the
binary reads in production:

```
eval/profiles/single-project/{rig-rag.toml,sources.json}   # jj only
eval/profiles/multi-project/{rig-rag.toml,sources.json}    # jj + podman + qdrant
```

Each profile owns its `[corpus]` prefix and `data_root`, so a run never touches
production (`docs-*`, `data/`) or another profile. `rig-rag promote --config
<profile>` writes the active collection into that profile's `rig-rag.toml`, so
expect that tracked file to change after a run.

## Binary integration

The harness reuses the main binary; the eval-supporting additions are:

- **Global flags** `--config <path>` / `--sources <path>` on every subcommand
  (`ingest`, `serve`, `promote`, `prune`, `model`); they default to
  `rig-rag.toml` / `sources.json`.
- **`[corpus]` config section** with `prefix` (default `docs`) and `data_root`
  (default `data`). Checkouts land under `data_root`; collections are named
  `<prefix>-<model-slug>-<hash12>`.
- **`prune` is scoped to the config's prefix** — it deletes only `<prefix>-*`
  collections.
- **Pinning**: a source `ref` may be a branch, tag, or **full commit SHA**
  (detached checkout); the resolved SHA is always recorded, and
  `ingest --pin` rewrites branch refs to resolved SHAs in the sources file.
- **`ingest --report <path>`** writes the JSON document described below; nothing
  extra (timing, tokenizer) is gathered when the flag is absent.

Retrieval knobs stay **hardcoded** (an experiment edits the code/const); eval
hits the existing `/api/query` endpoint, so there is no HTTP contract change.
Reranking is the exception: a profile may carry `[reranker]`. The runner always
starts `serve` with `--log-eval-metrics`, asks **one uncached query per
question** at `max(k)` (no score cache, no warmup, no repetitions), and joins the server's
`search`/`rerank` lines to each question by `sha256(question)[:16]` to split
local search time, paid rerank time, and end-to-end time and to sum provider
usage/cost.

## Questions

`eval/questions.toml` is the versioned question set. Each entry names the
profiles it applies to and its gold evidence:

```toml
[[question]]
id = "jj-author"
question = "Who is the author of jj?"
applies_to = ["single-project", "multi-project"]
gold = [{ source = "jj", path = "testimonials.md", must_contain = "project creator and leader" }]
notes = "..."
```

- A question applies to a profile only when every `gold.source` exists there.
- **Gold matching:** a retrieved hit covers a gold item when its `path` matches
  **and** (no `lines`, or the line ranges overlap) **and** (no `must_contain`, or
  the snippet occurs in the hit text). This is robust to re-chunking.
- `gold = []` marks an **unanswerable** question (the no-hit rate is reported).
- The single-project set is a **subset** of the multi-project set; both are
  asserted by `eval/tests/test_questions.py`.

The set currently holds 18 questions: 8 `jj` questions applied to both profiles,
8 multi-project-only answerable questions, and two negative controls (the
multi-only `unanswerable-geography` and the shared `unanswerable-made-up-entity`).
It spans five categories: **factual/semantic** (e.g. `jj-author`, the paraphrase
gap "author" vs "creator"), **how-to/procedural** (commands, config, setup),
**API/reference** (flags, options, exact identifiers), **cross-project
ambiguity** (the same term in several projects; tests purity), and
**unanswerable/negative** (not in the corpus, or requiring synthesis across
docs). Build the multi-project set first; the single-project set is its
`jj`-only subset.

Several entries are deliberate semantic/paraphrase probes whose query avoids the
gold sentence's distinctive terms (`jj-bookmarks-label`, `jj-publish-remote`,
`jj-conflict-resolution`, `podman-host-files`, `qdrant-shrink-vectors`,
`qdrant-archive-collection`); these are the questions that can expose a
dense-recall change. `unanswerable-made-up-entity` uses only tokens absent from
both corpora, so it is the clean negative a restored no-hit guard must hold.

## Outputs

Per run under `eval/results/<experiment>/<run>/`:

- `results.json` — the versioned render input (schema in
  `eval/runner/results.py`). Committed as an artifact. It records a best-effort
  `commit` and `commit_ref` (empty when git is unavailable or the revision is
  unpushed), plus an `environment` block whose `binary` is repo-relative with its
  `binary_sha256`.
- `hits.jsonl` — one JSON object per returned hit (profile, question, k, rank,
  score, source, path, lines, chunk text) for offline analysis.
- `ingest-<profile>.json` — the Rust `ingest --report` document.
- `serve-<profile>.log` — the server's captured stdout/stderr.

`results.json` (schema v2) holds, per profile: the collection, the per-question
`by_k` metrics, per-question `timings`/`rerank` entries, and profile-level
`timings` for `search`, `rerank`, and `e2e`. The reported `k` set is recorded
top-level as `k_values` (default `3,7,20`; a run may report a subset, e.g.
`--k 3,20`). Each question is queried once at `max(k)` and smaller-k metrics are
exact prefixes of that ranked list (the reranker's candidate pool covers it).
Percentiles are across questions — one cold sample per question, not repeated
trials. Across profiles it holds `degradation` (single → multi deltas for recall,
purity, and MRR at every k) and `summary` (per-profile aggregates at every k,
including the unanswerable no-hit rate and the timing rollup).

## Ingest report (`ingest --report`)

`ingest --report <path>` writes one versioned JSON document (`schema_version: 1`)
with the run's status, per-phase timing, memory, per-source counts, and
chunk-token distribution. The Rust schema lives in `src/report.rs`; a committed
baseline example is
[`ingest-single-project.json`](results/baseline/2026-10-10T15-58-51Z/ingest-single-project.json).

Notes:

- Peak RSS comes from `libc::getrusage`, so it works on macOS (the old
  `/proc/self/status` path is Linux-only); cgroup peak stays Linux-container-only.
- Counts and tokenization are skipped entirely without `--report`, so normal
  ingest pays no tokenizer cost. `chunk_tokens` is the per-source chunk-size
  distribution (the input to the drop-small-chunks experiment).
- The report deliberately does **not** record a rig-rag commit: the runtime image
  has no `.git` and no `git` binary. The Python runner owns the measured commit
  in `results.json`.
- The committed drop-small-chunks reports also carry a `min_chunk_tokens` field
  from the arm that filtered small chunks; that filtering seam was **reverted**,
  so current runs do not emit it. The framework renders the field when present.

## Reporting

`eval/report/framework/` turns one run's committed parts into one self-contained
HTML document (inline CSS + SVG, no scripts, fonts, or network): a retrieval part
(`results.json`) and one ingestion part per profile (`ingest-<profile>.json`).
The report covers provenance (commit + ref when available + environment), a
per-profile summary (recall/purity/MRR × k plus e2e p50/p95), mean recall@k with
a per-question table per k, a Timing section (local search, paid rerank, and
end-to-end charts plus a cost rollup), and cross-corpus degradation deltas.

```sh
just eval-report baseline     # newest baseline run → eval/reports/baseline.html
```

Output lands at `eval/reports/<experiment>.html` — generated and **gitignored**;
regenerate it any time from the committed inputs. Part selection and the
declared measured commit are flags; see `just eval-report-help`.

Both parts are **independently versioned, tested contracts**
(`eval/report/framework/loader.py`, `ingest.py`): an unknown schema version is
refused, and `eval/report/tests/fixtures/` commits a fixture per shape. A v1
`results.json` is refused with a clear message: it predates the rerank-cache
removal, its latency samples mixed warm and cold calls, and it is not
comparable. The ingestion section leads with `status` and marks a `reused` report as
**cost not measured** (a reused collection gathers no counts or tokens; measure
real cost with `--force` or a new collection).

### Comparison (baseline vs candidate)

An experiment can declare a baseline run; the report then leads with a
**Comparison** section diffing the two runs' ingest cost, retrieval aggregates
(recall@k, purity@k, MRR@k, search/rerank/e2e p50/p95, no-hit rate), and
per-question recall/purity at every k. The diff is computed at render time from
the two committed runs —
no contract change, and either run stays independently renderable.

## Experiment lifecycle

An experiment is a controlled code change measured against the committed
baseline. The runner records a **best-effort git revision** (`git rev-parse
HEAD`) as a breadcrumb;

Start from [`experiments/template.md`](experiments/template.md): it lays out the
record's four stages (hypothesis, controlled change, interpretation, decision)
and fixes only the shape of the record — the steps below are the process.

Work the four stages strictly in order, each as its own commit, and do not start
a stage while an earlier one is uncommitted:

1. **Hypothesis.** Copy `experiments/template.md` to
   `eval/experiments/<id>.md` and fill in only the Hypothesis and Status, then
   commit that hypothesis-only record. **No system code changes yet** — the
   prediction is fixed before the code that tests it exists.
2. **Controlled change.** Make the controlled change in **one commit** that
   touches only system code, never the harness (the record, the runner, the
   framework) or the render package. Pin `--profiles` and `--k`, and confirm a
   baseline exists under the same
   profile definitions, pinned source revisions, and model — else re-record one
   and pin it by path.
3. **Interpretation.** Run the treatment arm with the same profile definitions,
   source revisions, model, and pinned runner settings as the baseline (`just
   eval-run --experiment <id> --force-ingest`). Create the render package
   `eval/experiments/<id>/report.py` (`__init__.py` + `report.py`), pin it
   to that baseline run, and render `just eval-report <id>` — the report's
   comparison section is what you analyze. Then fill the record's Controlled
   change section (arm table: revision, run ids, pinned flags), Results, and
   Interpretation **from what was measured**, plus the profile `sources.json` /
   `rig-rag.toml` used; commit the results JSON, the ingest reports, the render
   package, and the record, reusing the `Eval-Experiment:` / `Eval-Commit:`
   trailer the runner prints (its `Eval-Commit` matches `results.json`'s
   `commit`).
4. **Decision.** Fill Decision and Caveats, add the [Experiments](#experiments)
   row, and commit the finalized record; the HTML report is regenerated from the
   committed input (`just eval-report <id>`) and is **not** committed. Push.
   If the experiment is unsuccessful, revert **only the code** in a new commit —
   the measured commit stays in history, so the hash remains reproducible while
   the eval docs persist.

Each `eval/experiments/<id>/report.py` renders that experiment's newest committed
run; the recorded experiments are listed under [Experiments](#experiments).

## Metrics

Fixed definitions (unit-tested in `eval/tests/test_metrics.py`):

- **hit@k** — 1 if any gold item is covered by the top-k hits.
- **recall@k** — distinct gold items covered / total gold items.
- **precision@k** — top-k hits covering ≥1 gold item / hits returned.
- **MRR@k** — reciprocal rank of the first hit covering any gold item.
- **source purity@k** — share of returned hits from the question's gold
  source(s); high purity with low recall means "right project, wrong section".
- Unanswerable questions report **no_hit** (1 when nothing clears the
  threshold) instead of recall/precision/MRR/purity.

Percentiles use linear interpolation between order statistics. Each question is
queried exactly once, uncached, at `max(k)`; correctness at smaller k is computed
from the returned list's prefix, and timings are split by the server's
`search`/`rerank` lines. Percentiles are across questions, not repeated trials.

Qualitative observations from the first full baseline run (both profiles) worth
keeping:

- Adding podman + qdrant did not change `jj` recall at the swept k — the
  shared-question degradation was `recall@k = 0.0`, `purity@7 = 0.0`.
- `jj-author` never surfaced its paraphrase marker in the top 20, and
  `qdrant-docker-ports` first hit at k=8 (so multi-project `mean_recall` rose
  from 1/6 at k=7 to 1/3 at k=10). These are real retrieval behavior, not
  harness artifacts.
- The suite is still small (8 single-project / 16 multi-project answerable
  questions), so it bounds large recall changes only. The added semantic probes
  are deliberately hard and are expected to start at low recall.

## Caveats and open risks

- **Tokenizer fidelity.** `cl100k_base` is not BGE's WordPiece tokenizer, so
  token counts are approximations — fine for relative/cost ballparks, not exact
  embedding-cost math.
- **No calibrated no-hit guard.** The dense-only `THRESHOLD = 0.5` was poorly
  calibrated (most off-topic questions cleared it), and the adopted hybrid path
  uses a `0.0` RRF floor; dense and sparse floors are also `0.0`, so every query
  returns `k` hits and `no_hit` is 0 by construction.
  `unanswerable-made-up-entity` exists to certify a future score-based guard;
  until then its top RRF score is not a relevance signal.
- **Profile drift.** Pinned SHAs freeze the corpora; refreshing a pin is itself a
  decision (and possibly an experiment).
- **Latency noise.** Host/Qdrant warmth and provider load affect p95; every
  sample is a cold query, so record the environment with the run.
- **Result size.** `hits.jsonl` + `results.json` per run can grow; the raw dumps
  (`hits.jsonl`, `serve-*.log`) are kept per-run and gitignored, while
  `results.json` (and `ingest-*.json`) are the committed artifacts — keep them
  small.

## Tests

```sh
just eval-test
```

The suite is fully offline (a stub HTTP server stands in for `serve`; the report
fixtures are committed JSON).
