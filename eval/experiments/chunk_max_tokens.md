# Experiment: Bound chunk size to the embedding window (`chunk_max_tokens`)

<!--
Measured and decided: the 384 cap is rejected (see [Decision](#decision)). The
[Checklist](#checklist) below tracks the process and is deleted once final; see
[`template.md`](template.md).
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
- [x] If re-recording a baseline: check out the baseline commit (normally the
      treatment commit's parent), then run `just eval-run --experiment baseline
      --force-ingest` plus your pinned flags; record its commit SHA and run id.
      — n/a: the committed baseline is reused by path.
- [x] Return to the treatment commit, then run `just eval-run --experiment
      chunk_max_tokens --force-ingest` plus your pinned flags; record its run id.
      — run id `2026-10-12T09-18-11Z`.
- [x] Run both arms on the same profiles/pins/model. `promote` rewrites
      `[collection].active` in the profile `rig-rag.toml`; with unchanged
      pins/model that value is stable, so the tree normally stays clean.
- [x] `<run_id>` is the UTC-timestamp directory the runner prints (e.g.
      `2026-10-10T15-58-51Z`).
- [x] Fill **Results** and **Interpretation** only from what was measured.

**Stage 4 — Decision (record and land).**
- [x] Fill **Decision** and **Caveats**.
- [x] Add a row to the [Experiments](../README.md#experiments) table.
- [x] Create `eval/experiments/chunk_max_tokens/{__init__.py,report.py}` (pin
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

**Status:** rejected (not adopted) — the 384 cap fixed truncation mechanically but
regressed `jj-new-change` (rank 1 → beyond k=10) and raised cost; see
[Decision](#decision).

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
| treatment | 384 | `72f220be4af79a742e2d4f41558431660356cacb`\* | `eval/results/chunk_max_tokens/2026-10-12T09-18-11Z` |

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

`*` The treatment was measured at `72f220b`; its `src/` is identical to the
controlled-change commit `e1ecbe4` (see [Caveats](#caveats) → process).

## Results

### Ingest cost

| metric | single base | single 384 | Δ | multi base | multi 384 | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| embeddings | 793 | 855 | +62 (+7.8%) | 9,956 | 10,494 | +538 (+5.4%) |
| tokens | 151,031 | 151,816 | +785 (+0.5%) | 1,492,068 | 1,498,256 | +6,188 (+0.4%) |
| `embed` ms\* | 40,290 | 59,389 | +19,099 (+47%) | 443,378 | 634,779 | +191,401 (+43%) |
| `insert` ms\* | 586 | 779 | +193 (+33%) | 8,414 | 12,123 | +3,709 (+44%) |
| `total` ms\* | 44,600 | 64,018 | +19,418 (+44%) | 469,987 | 664,511 | +194,524 (+41%) |
| peak RSS (GiB)\* | 9.03 | 8.99 | −0.04 | 12.48 | 16.50 | +4.02 |

`*` Timings and RSS are not attributable to the change (see [Caveats](#caveats)).

Per-source chunk distribution (`cl100k_base`):

| source | chunks | max | p90 | `>=400` |
| --- | ---: | ---: | ---: | ---: |
| jj | 793 → 855 | 542 → 422 | 445 → 369 | 97 → 7 |
| podman | 2,509 → 2,591 | 554 → 428 | 253 → 260 | 129 → 15 |
| qdrant | 6,654 → 7,048 | 586 → 445 | 382 → 342 | 606 → 42 |

The largest chunk is now 445 `cl100k` (was 586), so no chunk exceeds BGE's
512-token window; the `>=400` bucket collapses. Min chunk size is unchanged
(4 tokens) — the cap splits, it does not merge.

### Retrieval

| metric | single base | single 384 | multi base | multi 384 |
| --- | ---: | ---: | ---: | ---: |
| mean recall@1 | 0.333 | 0.000 | 0.167 | 0.000 |
| mean recall@3 | 0.333 | 0.000 | 0.167 | 0.000 |
| mean recall@5 | 0.333 | 0.000 | 0.167 | 0.000 |
| mean recall@7 | 0.333 | 0.000 | 0.167 | 0.167 |
| mean recall@10 | 0.333 | 0.000 | 0.333 | 0.167 |
| mean recall@20 | 0.333 | 0.333 | 0.333 | 0.333 |
| mean purity@7 | 1.000 | 1.000 | 1.000 | 1.000 |
| mean MRR@7 | 0.333 | 0.000 | 0.167 | 0.024 |
| p50 ms | 7.18 | 9.57 | 7.58 | 8.95 |
| p95 ms | 8.17 | 11.09 | 9.27 | 12.08 |
| unanswerable no-hit rate | — | — | 1.000 | 1.000 |

Per-question movement:

- **`jj-new-change` (both profiles): regression.** recall@1…@10 `1.0 → 0.0`
  (first gold hit at k=20); MRR@7 `1.0 → 0.0`. The gold chunk fell from rank 1 to
  rank 13.
- **`qdrant-docker-ports`: small win.** First gold `k=10 → k=7`; MRR@7
  `0.0 → 0.143`.
- `jj-author`, `jj-config-set-user`, `podman-detached-run`, `cross-project-config`:
  `0` in both arms (no change).
- single→multi degradation delta: `0.0` in both arms.

## Interpretation

- **Effect size.** One answerable question regressed (rank 1 → beyond k=10) and
  one improved (k=10 → k=7). `mean recall@20` is unchanged; `MRR@7` falls
  (single `0.333 → 0.0`, multi `0.167 → 0.024`). The regression is real, not
  noise: the same question flips in both profiles.
- **Where it comes from.** Capping re-partitions whole sections, not just the
  largest chunks. Baseline `tutorial.md:137-174` — one chunk containing the gold
  phrase at line 141, score 0.8233, rank 1 — becomes `122-157` + `157-174`. The
  phrase lands in `122-157`, which drops to rank 13, while the neighbor
  `157-174` ranks 1 without it. The damage is not the truncation being fixed;
  it is a strongly-matching chunk split into weaker pieces. `qdrant-docker-ports`
  moved the other way (its gold chunk crossed into the top 7).
- **What the suite can and cannot detect.** 3 single / 6 multi answerable
  questions; recall is quantized at 1/3 and 1/6. It detected this regression but
  cannot bound small effects, and single-question movements dominate the mean.

## Decision

**Not adopted.** The hypothesis predicted `recall@k` non-decreasing; instead
`jj-new-change` regressed from rank 1 to beyond k=10 in both profiles. The
mechanical goal was met — no chunk now exceeds the model window — but the
retrieval cost outweighs it.

- Decisive: a first-rank answerable hit became a k=20 hit, a large and
  attributable regression; the only win (`qdrant-docker-ports` k10→k7) is
  smaller.
- The corpus is not dominated by truncation-sensitive chunks; removing
  truncation did not pay for itself.
- Revisit only with a different mechanism (see [Caveats](#caveats) → follow-ups)
  and a stronger question set; re-applying is cheap.

Revert **only the code** in a follow-up commit. The measured artifacts
(`eval/results/baseline/2026-10-10T15-58-51Z`,
`eval/results/chunk_max_tokens/2026-10-12T09-18-11Z`) and the controlled-change
commit `e1ecbe4` stay in history.

## Caveats

- **Suite power.** 3 answerable single / 6 multi questions; bounds large recall
  regressions only. "No regression" would have meant "none detectable".
- **Tokenizer fidelity.** The cap is `o200k_base`; the report counts
  `cl100k_base`; BGE embeds with WordPiece. The three disagree, so 384 is a
  conservative approximation, not an exact window fit — the max chunk fell to
  445 `cl100k` (comfortably under 512), but `o200k`→WordPiece is not linear, so a
  chunk just under 512 `cl100k` could in principle still exceed 512 WordPiece.
- **Truncation premise not directly measured.** The baseline's largest chunks
  (max 586 `cl100k`) were likely truncated at BGE's 512-token limit, but the
  committed report's buckets stop at `>=400`, so the exact count above 512 is not
  recorded and this suite cannot measure the retrieval value of the truncated
  tail. The cap removed truncation mechanically without showing that it mattered.
- **Memory/latency noise.** The ~+40% wall-clock (embed/insert/total) and the
  multi-project peak-RSS `+4 GiB` are host-dependent and inconsistent with a
  +5% vector-count change; not attributable. Latency p50/p95 rose ~2–3 ms,
  within noise.
- **Collection identity.** Chunking is not part of the corpus hash, so the
  treatment targets the baseline's collection name and needs `--force`; the
  reused baseline is the committed `results.json`, not a live collection.
- **Follow-ups.** Overlap is the principled knob for answers straddling a
  boundary, but it would not have recovered this regression: the gold phrase sits
  ~16 lines from the nearest boundary, so a practical overlap would not pull it
  into the adjacent chunk (test overlap alone against the `(384, 0)` arm if
  pursued). The mirror change — merging small chunks — makes chunks larger and
  more context-rich, the direction this answer needed, but fights the model
  window. The remaining 0-recall questions (`jj-author`, `cross-project-config`,
  `podman-detached-run`) fail for semantic/ranking reasons, not size; chunking
  will not move them.
- **Model-aligned cap (untested).** 384 was aggressive: it re-partitioned whole
  sections although only the largest few percent of chunks were near BGE's
  window. A cap set just under the limit with a model-aligned tokenizer (e.g.
  `.encoding("cl100k_base").max_tokens(500)`) would split only the chunks that
  actually exceed the window and leave the rest byte-identical to the baseline —
  addressing the truncation premise without the collateral re-partitioning that
  caused this regression.
- **Process.** The run was measured at `72f220b` (the record's docs tip) rather
  than the controlled-change commit `e1ecbe4`, because the record's edits must be
  committed for a clean tree; `src/` is identical between the two. See
  eval/README.md → Caveats and open risks.

## Reproduction

The baseline is reused from the committed run `eval/results/baseline/2026-10-10T15-58-51Z`
(`510d9915d25afdcee38c32e72cfbdcd2fc2d9126`); it is not re-recorded. The
treatment was measured at `72f220be4af79a742e2d4f41558431660356cacb`
(`src/` identical to the controlled-change commit `e1ecbe4`), reachable from
`origin/zummenix/push-tzoxxwltkoup`.

```sh
# treatment arm
git checkout 72f220be4af79a742e2d4f41558431660356cacb
just eval-run --experiment chunk_max_tokens --force-ingest \
  --profiles single-project,multi-project \
  --k 1,3,5,7,10,20 --latency-k 7 --warmup 3 --reps 5

# baseline is reused from eval/results/baseline/2026-10-10T15-58-51Z (510d991)

# render the experiment from the committed JSON
just eval-report chunk_max_tokens
```
