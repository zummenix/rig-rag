# Experiment: <short title> (`<experiment_id>`)

<!--
Copy this file to eval/experiments/<experiment_id>.md and fill it in. Flags,
profiles, metrics, and the committed contracts are documented in
[`../README.md`](../README.md); the [Checklist](#checklist) below tracks the
process and is deleted once the record is final. This template fixes only the
shape of the record.

Keep `<experiment_id>` identical everywhere it appears: the `--experiment`
name, the `eval/results/<experiment_id>/` run directory, and the render package
`eval/experiments/<experiment_id>/` (add `__init__.py` + `report.py`; required
for `just eval-report <experiment_id>`). Add a row in eval/README.md's
Experiments table as well, for discoverability. Delete the guidance comments as
you go, and leave anything you could not measure as an explicit "not measured"
rather than guessing.

The record has four stages, in order:
  1. Hypothesis           — what you expect and why.
  2. Controlled change    — the system commit that tests it, isolated so the
                            docs survive a revert.
  3. Interpretation       — the measured results (## Results) and what they mean
                            (## Interpretation).
  4. Decision             — adopt the change, or revert only the code.
## Caveats and ## Reproduction are supporting sections, not stages.
-->

## Checklist

<!--
Working aid — work this top to bottom, then delete the whole section at the last
step (once the Decision is recorded), so the final record holds only the four
stages. See eval/README.md for flags, profiles, metrics, and contracts.
-->

**Set up.**
- [ ] Satisfy `eval/README.md`'s Requirements (Qdrant reachable at the profile's
      URL, `OPENROUTER_API_KEY`/`OPENROUTER_MODEL_NAME`, Python 3.11+, `git`).
- [ ] Fill in the hypothesis, the knob, and what would falsify it.
- [ ] Make the controlled change in **one commit** that touches only system
      code — never the harness. Work from a clean tree and push it: the runner
      attributes a run to `HEAD` and refuses uncommitted tracked changes and
      unpushed commits. (`--allow-dirty` skips the clean-tree check **only**;
      the pushed-commit check still applies, and the recorded commit no longer
      describes what was measured.)
- [ ] Confirm a baseline exists under the same profiles and embedding model; if
      not, re-record one and pin it by path.
- [ ] Fix `--profiles`, `--k`, `--latency-k`, `--warmup`, and `--reps` now, and
      carry those pinned values into every `eval-run` below.

**Measure.**
- [ ] If re-recording a baseline: `just eval-run --experiment baseline
      --force-ingest` plus your pinned flags; record its commit SHA and run id.
- [ ] Treatment: at the treatment commit, `just eval-run --experiment
      <experiment_id> --force-ingest` plus your pinned flags; record its commit
      SHA and run id.
- [ ] Run both arms on the same profiles/pins/model. `promote` rewrites
      `[collection].active` in the profile `rig-rag.toml`; with unchanged
      pins/model that value is stable, so the tree normally stays clean.
- [ ] `<run_id>` is the UTC-timestamp directory the runner prints (e.g.
      `2026-10-10T15-58-51Z`).

**Record.**
- [ ] Fill the Results, Interpretation, Decision, and Caveats sections.
- [ ] Add a row to the [Experiments](../README.md#experiments) table.
- [ ] Commit the artifacts: `results.json`, `ingest-*.json`, and this `.md`.
      `hits.jsonl`, `serve-*.log`, and `eval/reports/*.html` stay gitignored —
      the HTML report is regenerated from the committed input
      (`just eval-report <experiment_id>`).
- [ ] Push the commit that lands the results (and the measured commits), so the
      record and its runs stay reachable from a clean clone.
- [ ] Reuse the `Eval-Experiment:` / `Eval-Commit:` trailer the runner prints on
      the commit that lands the results, so a git-only reader can trace the run.
- [ ] Create `eval/experiments/<experiment_id>/{__init__.py,report.py}` (pin
      this record's baseline run by path) so `just eval-report <experiment_id>`
      renders.

**Decide.**
- [ ] Adopted: keep the treatment commit on the main line.
- [ ] Rejected: revert **only the code** in a new commit — the measured commit
      stays in history, so the hash remains reproducible while the eval docs
      persist.
- [ ] **Delete this Checklist section** — the record is now final.

**Hypothesis.** <The predicted effect, in one or two sentences. Name the
metric(s) it should move and the direction, and what would falsify it.>

**Status:** <draft | measured, pending decision | adopted | rejected (not
adopted)> — <one-line outcome; see [Decision](#decision).>

## Controlled change

<Exactly what changed in the system's code, and what deliberately did *not*
change. Keep it to the smallest diff that isolates the hypothesis.>

The change lives in **its own commit** so it can be reverted independently,
leaving the harness (framework, runner, this record) and the measured runs
intact.

| arm | <knob / parameter> | commit | run |
| --- | ---: | --- | --- |
| baseline | <production value> | `<baseline_commit_sha>` | `eval/results/baseline/<run_id>` |
| treatment | <treatment value> | `<treatment_commit_sha>` | `eval/results/<experiment_id>/<run_id>` |

Both arms were ingested with `--force` (the runner's `--force-ingest` flag,
forwarded to the binary) so cost is measured, not `reused`, on the same profiles
— `eval/profiles/{single,multi}-project/{rig-rag.toml,sources.json}`
(snapshotted in each run's `results.json` → `config`) — with pinned sources
`<source> @ <sha>`, … and model `<embedding_model>`. If no committed baseline was
captured under the same profiles and model, re-record one (the baseline arm in
[Reproduction](#reproduction)) and pin it by path in the report script.

## Results

<!--
Stage 3, measurement: report the numbers you actually got, not the numbers you
wanted. Tables are the convention; annotate deltas that are within noise.
Drop any subsection that does not apply, but say so here if you do.
-->

### Ingest cost

| metric | single base | single <label> | Δ | multi base | multi <label> | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| embeddings | | | | | | |
| tokens | | | | | | |
| `embed` ms | | | | | | |
| `insert` ms | | | | | | |
| `total` ms | | | | | | |
| peak RSS (GiB) | | | | | | |

`<label>` is the treatment arm's knob value (e.g. `N=15`).

<Per-source notes if one source dominates the delta.>

### Retrieval

| metric | single base | single <label> | multi base | multi <label> |
| --- | ---: | ---: | ---: | ---: |
| mean recall@{k} | | | | |
| mean purity@{latency-k} | | | | |
| mean MRR@{latency-k} | | | | |
| p50 ms | | | | |
| p95 ms | | | | |
| unanswerable no-hit rate | — | — | | |

One `mean recall@{k}` row per swept `k` (from `results.json` → `k_values`,
default `1,3,5,7,10,20`); purity and MRR exist only at `--latency-k`.

<Call out any per-question movement (recall, purity) and the single→multi
degradation delta.>

## Interpretation

<!--
Stage 3, interpretation: what do the results mean? Separate signal from noise
and state the limits of the evidence. Do not state a decision here.
-->

- **Effect size.** <Is the measured movement large relative to run-to-run noise?
  Which metrics moved and by how much?>
- **Where it comes from.** <Which source/profile/mechanism drives the delta?>
- **What the suite can and cannot detect.** <The question set has N answerable
  questions; say which regressions are bounded and which are not. "No
  regression" means "none detectable by this suite".>

## Decision

<!--
Stage 4. Pick exactly one and commit to it:
  - Adopted:   keep the code change; the treatment commit stays on the main line.
  - Rejected:  revert only the code in a follow-up commit; this record, the
               framework, and the measured runs stay in history so the numbers
               remain reproducible.

Rationale must be about the evidence, not a restatement of the hypothesis.
-->

**<Adopted / Not adopted.>** <Why.>

- <Bullet the decisive reasons: cost win vs. quality risk, corpus skew, suite
  power, operational cost of keeping the knob.>
- <If rejected: what would change the decision (e.g. a stronger question set,
  a different threshold), and note that re-applying is cheap.>

<If rejected, record what stays: the measured artifacts
(`eval/results/baseline/<run_id>`,
`eval/results/<experiment_id>/<run_id>`) and the treatment commit
`<treatment_commit_sha>` remain in history.>

## Caveats

- **Suite power.** <How many answerable questions; what that bounds.>
- **Tokenizer / measurement noise.** <Anything approximate or host-dependent
  (RSS, p50/p95) that must not be read as an effect.>
- <Any other threat to the validity of the conclusion.>

## Reproduction

<!--
Important: once a rejected experiment's code is reverted at HEAD, the
`--experiment` flag can no longer select the old behavior. Each arm must then be
built from its pinned commit; the runner attributes a run to whatever `HEAD` is
checked out, so check out the measured commit before `eval-run`. Both commits
must be reachable from pushed refs (the runner's provenance check requires it).

`just eval-report` looks up `eval/experiments/<experiment_id>/report.py`. Note
that `eval-run` only creates the *data* directory `eval/results/<experiment_id>/`;
the render package is separate and hand-written — create
`eval/experiments/<experiment_id>/` (`__init__.py` + `report.py`) and freeze this
record's baseline run by path in it (see
eval/experiments/drop_small_chunks/report.py):

    from eval.report.framework.cli import report_main
    BASELINE_RUN = "eval/results/baseline/<run_id>"
    if __name__ == "__main__":
        raise SystemExit(report_main(
            experiment="<experiment_id>", notes=(...), baseline=BASELINE_RUN))

Add a row to eval/README.md's Experiments table for discoverability.
-->

```sh
# baseline arm — the flags shown are the defaults; use the values you pinned in Set up
git checkout <baseline_commit_sha>
just eval-run --experiment baseline --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# treatment arm
git checkout <treatment_commit_sha>
just eval-run --experiment <experiment_id> --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# return to your branch, then render the experiment from the committed JSON
git checkout <your-branch>
just eval-report <experiment_id>
```

