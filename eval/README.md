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
timing run only under `--report`); **reproducible from a commit** (corpus sources
are pinned to SHAs, and a run records the commit it was measured at); and
**versioned, tested contracts** (the results and ingest schemas ship with
committed fixtures, so any earlier experiment stays renderable).

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

Each experiment is an `eval/experiments/<id>.md` record plus a run under
`eval/results/<id>/`; add a row here when a new one lands. The `baseline` id is
the reference run (no record document — just the committed runs and its
`report.py`).

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

The seed set spans five categories: **factual/semantic** (e.g. `jj-author`, the
paraphrase gap "author" vs "creator"), **how-to/procedural** (commands, config,
setup), **API/reference** (flags, options, exact identifiers), **cross-project
ambiguity** (the same term in several projects; tests purity), and
**unanswerable/negative** (not in the corpus, or requiring synthesis across
docs). Build the multi-project set first; the single-project set is its
`jj`-only subset.

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
The report covers provenance (commit + pushed ref + environment), a per-profile
summary, mean recall@k with a per-question breakdown, latency, and cross-corpus
degradation deltas.

```sh
just eval-report baseline     # newest baseline run → eval/reports/baseline.html
```

Output lands at `eval/reports/<experiment>.html` — generated and **gitignored**;
regenerate it any time from the committed inputs. Part selection and the
declared measured commit are flags; see `just eval-report-help`.

Both parts are **independently versioned, tested contracts**
(`eval/report/framework/loader.py`, `ingest.py`): an unknown schema version is
refused, and `eval/report/tests/fixtures/` commits a fixture per shape. An older
v1 `results.json` still renders — the loader derives the swept k set and latency
k. The ingestion section leads with `status` and marks a `reused` report as
**cost not measured** (a reused collection gathers no counts or tokens; measure
real cost with `--force` or a new collection).

### Comparison (baseline vs candidate)

An experiment can declare a baseline run; the report then leads with a
**Comparison** section diffing the two runs' ingest cost, retrieval aggregates
(recall@k, purity@k, MRR@k, p50/p95, no-hit rate), and per-question
recall/purity. The diff is computed at render time from the two committed runs —
no contract change, and either run stays independently renderable.

## Experiment lifecycle

An experiment is a controlled code change measured against the committed
baseline. The runner records the **git commit SHA** (`git rev-parse HEAD`), not
a jj change-id, so a git-only reader can check out the exact measured code.

Start from [`experiments/template.md`](experiments/template.md): it lays out the
record's four stages (hypothesis, controlled change, interpretation, decision)
and carries a copy-and-follow checklist covering the whole process — copy the
template, work the checklist, then delete it once the Decision is recorded.

Work the four stages strictly in order, each as its own commit, and do not start
a stage while an earlier one is uncommitted:

1. **Hypothesis.** Copy `experiments/template.md` to
   `eval/experiments/<id>.md` and fill in only the Hypothesis and Status, then
   commit that hypothesis-only record. **No system code changes yet** — the
   prediction is fixed before the code that tests it exists.
2. **Controlled change.** Make the controlled change in **one commit** that
   touches only system code, never the harness (the record, the runner, the
   framework) or the render package, from a clean pushed tree. Fill the record's
   Controlled change section (arm table + treatment commit SHA).
3. **Interpretation.** Run the arms with identical profile definitions, source
   revisions, model, and pinned runner settings (`just eval-run --experiment
   <id> --force-ingest`, plus a baseline arm if no matching baseline exists),
   then fill the record's Results and Interpretation **from what was measured**,
   plus the profile `sources.json` / `rig-rag.toml` used.
4. **Decision.** Fill Decision and Caveats, add the [Experiments](#experiments)
   row, create `eval/experiments/<id>/report.py`, and commit the results JSON,
   the ingest reports, and the record; the HTML report is regenerated from the
   committed input (`just eval-report <id>`) and is **not** committed. If the
   experiment is unsuccessful, revert **only the code** in a new commit — the
   measured commit stays in history, so the hash remains reproducible while the
   eval docs persist.

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

Percentiles use linear interpolation between order statistics. Latency is
measured at `--latency-k` (default 7) after `--warmup` calls, over `--reps`
sequential calls; retrieval itself is deterministic, so correctness is computed
once per (question, k).

Qualitative observations from the first full baseline run (both profiles) worth
keeping:

- Adding podman + qdrant did not change `jj` recall at the swept k — the
  shared-question degradation was `recall@k = 0.0`, `purity@7 = 0.0`.
- `jj-author` never surfaced its paraphrase marker in the top 20, and
  `qdrant-docker-ports` first hit at k=8 (so multi-project `mean_recall` rose
  from 1/6 at k=7 to 1/3 at k=10). These are real retrieval behavior, not
  harness artifacts.
- The suite is small (3 single-project / 6 multi-project answerable questions),
  so it bounds large recall regressions only.

## Caveats and open risks

- **Tokenizer fidelity.** `cl100k_base` is not BGE's WordPiece tokenizer, so
  token counts are approximations — fine for relative/cost ballparks, not exact
  embedding-cost math.
- **Weak relevance threshold.** Dense scores are poorly calibrated here: most
  off-topic questions clear `THRESHOLD = 0.5` (only 2 of 20 probed did not), so
  a clean unanswerable question must be moved well away from the corpus.
- **Profile drift.** Pinned SHAs freeze the corpora; refreshing a pin is itself a
  decision (and possibly an experiment).
- **Latency noise.** Host/Qdrant warmth affects p95; keep warmups/reps consistent
  and record the environment.
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
