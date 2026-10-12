# Experiment: Bound chunk size to the embedding window (`chunk_max_tokens`)

<!--
Stage 2 complete: the controlled change is committed and pushed. Next, Stage 3 —
measure both arms, then fill Results and Interpretation. See the
[Checklist](#checklist) below and [`template.md`](template.md).
-->

## Checklist

**Stage 1 — Hypothesis (record only; no system code).**
- [x] Satisfy `eval/README.md`'s Requirements (Qdrant reachable at the profile's
      URL, `OPENROUTER_API_KEY`/`OPENROUTER_MODEL_NAME`, Python 3.11+, `git`).
- [x] Fill in **only** the Hypothesis and Status sections: name the knob, the
      metric(s) it should move, the direction, and what would falsify it. Do
      **not** edit system code in this stage.
- [x] Commit this hypothesis-only record on its own (a docs-only commit), so the
      prediction is fixed before the code that tests it exists.
      — committed as `40cf1eb`.

**Stage 2 — Controlled change (system code only).**
- [x] Make the controlled change in **one commit** that touches only system
      code — never the harness (this record, the runner, the framework) and
      never the render package. Work from a clean tree and push it: the runner
      attributes a run to `HEAD` and refuses uncommitted tracked changes and
      unpushed commits. (`--allow-dirty` skips the clean-tree check **only**;
      the pushed-commit check still applies, and the recorded commit no longer
      describes what was measured.)
      — committed as `e1ecbe4` (`src/chunk.rs` only) and pushed on
      `origin/zummenix/push-tzoxxwltkoup`.
- [x] Fill in the Controlled change section: the arm table and the treatment
      commit SHA. Leave Results, Interpretation, Decision, and Caveats empty
      until stage 3.
- [x] Confirm a baseline exists under the same profile definitions, pinned source
      revisions, and embedding model; if not, re-record one and pin it by path.
      — reused `eval/results/baseline/2026-10-10T15-58-51Z` @ `510d991`.
- [x] Fix `--profiles`, `--k`, `--latency-k`, `--warmup`, and `--reps` now, and
      carry those pinned values into every `eval-run` below.
      — pinned to the reused baseline's settings (see
      [Controlled change](#controlled-change)): `--profiles
      single-project,multi-project --k 1,3,5,7,10,20 --latency-k 7 --warmup 3
      --reps 5`.

**Stage 3 — Interpretation (measure, then explain).**
- [ ] If re-recording a baseline: check out the baseline commit (normally the
      treatment commit's parent), then run `just eval-run --experiment baseline
      --force-ingest` plus your pinned flags; record its commit SHA and run id.
- [ ] Return to `<treatment_commit_sha>`, then run `just eval-run --experiment
      chunk_max_tokens --force-ingest` plus your pinned flags; record its run id.
- [ ] Run both arms on the same profiles/pins/model. `promote` rewrites
      `[collection].active` in the profile `rig-rag.toml`; with unchanged
      pins/model that value is stable, so the tree normally stays clean.
- [ ] `<run_id>` is the UTC-timestamp directory the runner prints (e.g.
      `2026-10-10T15-58-51Z`).
- [ ] Fill **Results** and **Interpretation** only from what was measured.

**Stage 4 — Decision (record and land).**
- [ ] Fill **Decision** and **Caveats**.
- [ ] Add a row to the [Experiments](../README.md#experiments) table.
- [ ] Create `eval/experiments/chunk_max_tokens/{__init__.py,report.py}` (pin
      this record's baseline run by path) so `just eval-report chunk_max_tokens`
      renders.
- [ ] Commit the artifacts: `results.json`, `ingest-*.json`, and this `.md`.
      `hits.jsonl`, `serve-*.log`, and `eval/reports/*.html` stay gitignored —
      the HTML report is regenerated from the committed input
      (`just eval-report chunk_max_tokens`).
- [ ] Push the commit that lands the results (and the measured commits), so the
      record and its runs stay reachable from a clean clone.
- [ ] Reuse the `Eval-Experiment:` / `Eval-Commit:` trailer the runner prints on
      the commit that lands the results, so a git-only reader can trace the run.
- [ ] Adopted: keep the treatment commit on the main line.
- [ ] Rejected: revert **only the code** in a new commit — the measured commit
      stays in history, so the hash remains reproducible while the eval docs
      persist.
- [ ] **Delete this Checklist section** — the record is now final.

**Hypothesis.** `chunk_md_doc` currently uses the chunker's default cap of 512
`o200k_base` tokens, which exceeds BGE-small-en-v1.5's 512-token WordPiece
window, so the longest chunks are silently truncated when embedded. Capping
chunks at `MAX_CHUNK_TOKENS = 384` (`o200k_base`) should keep every chunk inside
the model's window, so `recall@k` is non-decreasing on every answerable question,
at a modest ingest-cost increase from splitting the chunks above the cap. The
knob is the chunk-size cap in `src/chunk.rs`; the metric is `recall@k` (plus
ingest cost). Falsified by any `recall@k` regression on an answerable question,
or by a cost increase with no retrieval movement.

**Status:** draft — hypothesis recorded; controlled change committed (`e1ecbe4`)
and pushed; not yet measured.

## Controlled change

One line in `src/chunk.rs`: `chunk_md_doc` now chains
`.max_tokens(MAX_CHUNK_TOKENS)` with a new private `MAX_CHUNK_TOKENS = 384`,
replacing the chunker's implicit default of 512. The markdown strategy, overlap
(0), the embedding model, retrieval, and the serve path are unchanged. The change
is isolated in its own commit so it can be reverted without touching the harness.

Why 384: the cap is expressed in the chunker's `o200k_base` vocabulary, which
runs smaller than both the report's `cl100k_base` count and BGE's WordPiece count
for the same text. The baseline's longest chunk measured 586 `cl100k_base` tokens
under the 512 `o200k` default, so 384 leaves headroom for those vocabularies to
disagree without re-entering the truncation zone.

| arm | `MAX_CHUNK_TOKENS` | commit | run |
| --- | ---: | --- | --- |
| baseline | chunker default 512 | `510d9915d25afdcee38c32e72cfbdcd2fc2d9126` | `eval/results/baseline/2026-10-10T15-58-51Z` |
| treatment | 384 | `e1ecbe4989c835d96c98bf101b149a20e1c32d29` | `eval/results/chunk_max_tokens/<run_id>` |

The baseline is reused by path (the committed run above), not re-recorded: it was
measured on the same profiles, pinned sources, and model, with the chunker's
default 512 cap (no `MAX_CHUNK_TOKENS` override). Both arms use the profiles
`eval/profiles/{single,multi}-project/{rig-rag.toml,sources.json}` with pinned
sources `jj @ 9a1ad09b16e87d5d5e25759e5027953f8257be98`,
`podman @ e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365`,
`qdrant @ 9855e50fc739de44a223a1c50faced806a868b6a`, and model
`bge-small-en-v1.5` (384 dims). Pinned runner settings, identical to the reused
baseline: `--profiles single-project,multi-project`, `--k 1,3,5,7,10,20`,
`--latency-k 7`, `--warmup 3`, `--reps 5`, and `--force-ingest` so cost is
measured, not `reused`.

## Results

<!-- Stage 3, measurement: report the numbers you actually got. -->

### Ingest cost

| metric | single base | single <label> | Δ | multi base | multi <label> | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| embeddings | | | | | | |
| tokens | | | | | | |
| `embed` ms | | | | | | |
| `insert` ms | | | | | | |
| `total` ms | | | | | | |
| peak RSS (GiB) | | | | | | |

### Retrieval

| metric | single base | single <label> | multi base | multi <label> |
| --- | ---: | ---: | ---: | ---: |
| mean recall@{k} | | | | |
| mean purity@{latency-k} | | | | |
| mean MRR@{latency-k} | | | | |
| p50 ms | | | | |
| p95 ms | | | | |
| unanswerable no-hit rate | — | — | | |

## Interpretation

<!-- Stage 3, interpretation: what the results mean; limits of the evidence. -->

- **Effect size.** <...>
- **Where it comes from.** <...>
- **What the suite can and cannot detect.** <...>

## Decision

<!-- Stage 4. Pick exactly one: Adopted, or Rejected (revert only the code). -->

**<Adopted / Not adopted.>** <Rationale about the evidence.>

## Caveats

- **Suite power.** <...>
- **Tokenizer fidelity.** <...>
- **Memory/latency noise.** <...>

## Reproduction

<!-- Stage 4. Pin each arm's commit and record the exact commands. -->

```sh
# baseline arm
git checkout <baseline_commit_sha>
just eval-run --experiment baseline --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# treatment arm
git checkout <treatment_commit_sha>
just eval-run --experiment chunk_max_tokens --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# return to your branch, then render the experiment from the committed JSON
git checkout <your-branch>
just eval-report chunk_max_tokens
```
