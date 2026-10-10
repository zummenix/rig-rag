# Evaluation harness (`eval/`)

Retrieval-quality, cost, and latency measurement for `rig-rag`, layered on top
of the real `ingest` / `promote` / `serve` code paths. See `docs/eval-plan.md`
for the design; this directory holds the assets and the Python runner.

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

## Requirements

- Python **3.11+** (uses `tomllib`). Standard library only — no virtualenv,
  no third-party packages.
- A running Qdrant reachable at the profile's `[qdrant].url`.
- `git` on `PATH` (profiles pin corpus sources to commit SHAs).
- A clean work tree whose `HEAD` is contained by a pushed remote ref: the runner
  refuses uncommitted tracked changes and unpushed commits so the recorded
  `commit` stays reproducible. `--allow-dirty` skips the clean-tree check for
  ad-hoc runs.
- `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME` in the environment: `serve`
  boots the chat agent at startup even though `/api/query` needs no LLM, so the
  retrieval harness currently inherits that requirement.

## Profiles

A profile is a pair of production-shaped files:

```
eval/profiles/single-project/{rig-rag.toml,sources.json}   # jj only
eval/profiles/multi-project/{rig-rag.toml,sources.json}    # jj + podman + qdrant
```

Each profile owns its `[corpus]` prefix and `data_root`, so a run never touches
production (`docs-*`, `data/`) or another profile. `rig-rag promote --config
<profile>` writes the active collection into that profile's `rig-rag.toml`, so
expect that tracked file to change after a run.

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
- `gold = []` marks an **unanswerable** question (the no-hit rate is reported).
- The single-project set is a **subset** of the multi-project set; both are
  asserted by `eval/tests/test_questions.py`.

## Running

```sh
# From the repository root. Ingests, promotes, serves, sweeps, writes results.
python3 -m eval.runner --experiment baseline --profiles single-project

# Reuse already-ingested collections (skips fetch/embed), e.g. to iterate on
# metrics or latency:
python3 -m eval.runner --experiment latency --profiles single-project --skip-ingest

# Both profiles (writes degradation deltas):
python3 -m eval.runner --experiment baseline
```

Useful flags: `--bin <path>` (default: `cargo build --release`, then
`target/release/rig-rag`), `--allow-dirty` (skip the clean-tree check), `--k` (a
comma-separated subset of the contract grid `1,3,5,7,10,20`; default all),
`--warmup`, `--reps`, `--latency-k` (must be one of `--k`), `--port`,
`--startup-timeout`, `--repo-root`. Run `python3 -m eval.runner --help`.

The runner, in order: `ingest --report` (unless `--skip-ingest`) → `promote` →
`serve` on a free loopback port → a k-sweep plus a latency sweep over each
applicable question → teardown.

## Outputs

Per run under `eval/results/<experiment>/<run>/`:

- `results.json` — the versioned render input (schema in
  `eval/runner/results.py`). Committed as an artifact. It records the measured
  `commit` and the pushed `commit_ref` containing it, plus an `environment` block
  whose `binary` is repo-relative with its `binary_sha256`.
- `hits.jsonl` — one JSON object per returned hit (profile, question, k, rank,
  score, source, path, lines, chunk text) for offline analysis.
- `ingest-<profile>.json` — the Rust `ingest --report` document.
- `serve-<profile>.log` — the server's captured stdout/stderr.

`results.json` holds, per profile: the collection, the per-question `by_k`
metrics, per-question latency samples, and profile-level p50/p95. The swept `k`
set is recorded top-level as `k_values` (default `1,3,5,7,10,20`; a run may sweep
a subset, e.g. `--k 1,3,5`); every `@k` label is derived from it. Across profiles
it holds `degradation` (single → multi deltas over the shared questions) and
`summary` (per-profile aggregates, including the unanswerable no-hit rate),
both labelled at the run's `--latency-k`.

## Reporting

`eval/report/framework/` renders a committed `results.json` into one
self-contained HTML document (inline CSS + inline SVG, no scripts, fonts, or
network references). `render_report` takes a loaded results document and
returns the HTML; the per-experiment scripts are thin wrappers:

```sh
python3 -m eval.experiments.baseline.report        # newest baseline run
just eval-report baseline                          # same, via Just
python3 -m eval.experiments.baseline.report --results eval/results/baseline/<run>/results.json
```

Output lands at `eval/reports/<experiment>.html` (generated and **gitignored** —
regenerate it any time from the committed input with `just eval-report
<experiment>`). The report covers provenance
(commit + pushed ref + environment), a per-profile summary, mean recall@k and a
per-question breakdown, latency, cross-corpus degradation deltas, and the exact
measured configuration.

`results.json` is a **versioned contract**, so the framework keeps its own list
of supported schema versions (`eval/report/framework/loader.py`) and refuses an
unknown one with a clear message rather than guessing. An older v1 file that
predates `k_values`/`commit_ref` still renders: the loader derives the swept k
set and latency k. `eval/report/tests/fixtures/` holds a committed results
fixture per shape, and `eval/report/tests/` asserts both render and that a
future version fails loudly.

Ingestion data (`ingest-<profile>.json`) is a **separate artifact** and is not
part of this report yet: composing it into an optional ingestion section is
planned for **P5** (see `docs/eval-plan.md`).

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

Percentiles use linear interpolation between order statistics. Latency is
measured at `--latency-k` (default 7) after `--warmup` calls, over `--reps`
sequential calls; retrieval itself is deterministic, so correctness is computed
once per (question, k).

## Tests

```sh
python3 -m unittest discover -s eval -t .    # from the repo root (runner + report)
# or: just eval-test
```

The suite is fully offline (a stub HTTP server stands in for `serve`; the report
fixtures are committed JSON).
