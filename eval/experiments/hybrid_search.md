# Experiment: Hybrid dense + BM25 retrieval (`hybrid_search`)

<!--
Copy this file to eval/experiments/<experiment_id>.md and fill it in.
Flags, profiles, metrics, and the committed contracts are documented in
[`../README.md`](../README.md) (see Experiment lifecycle); the four stage
comments below and that section are the process. This template fixes only the
shape of the record.

Keep `<experiment_id>` identical everywhere it appears: the `--experiment`
name, the `eval/results/<experiment_id>/` run directory, and the render package
`eval/experiments/<experiment_id>/` (add `__init__.py` + `report.py`; required
for `just eval-report <experiment_id>`). Add a row in eval/README.md's
Experiments table as well, for discoverability. Delete the guidance comments as
you go, and leave anything you could not measure as an explicit "not measured"
rather than guessing.

The record has four stages, in order. Work them strictly one at a time, each as
its own commit, and do not start a stage while an earlier one is uncommitted:
  1. Hypothesis
  2. Controlled change
  3. Interpretation
  4. Decision
## Caveats and ## Reproduction are supporting sections, not stages.
-->

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

**Status:** draft — hypothesis recorded, not yet measured; see
[Decision](#decision).

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
Important: once a rejected experiment's code is reverted, the `--experiment` flag
can no longer select the old behavior. Each arm must then be built from its
pinned commit, so check that commit out before `eval-run`. The runner records a
best-effort revision and does not require a clean or pushed tree, so the commits
in these commands are the human-facing provenance — keep them accurate.

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
# baseline arm — the flags shown are the defaults; use the values you pinned in stage 2
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
