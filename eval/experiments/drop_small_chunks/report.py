"""Render the `drop_small_chunks` experiment into `eval/reports/drop_small_chunks.html`.

From the repository root:

    python3 -m eval.experiments.drop_small_chunks.report
    # or a specific run/path:
    python3 -m eval.experiments.drop_small_chunks.report --results eval/results/drop_small_chunks/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded. See
# eval/experiments/drop_small_chunks.md.
BASELINE_RUN = "eval/results/baseline/2026-10-10T15-58-51Z"

NOTES = (
    "Controlled change: `MIN_CHUNK_TOKENS` in `src/ingest.rs` set to 15 (from "
    "production 0), so chunks below 15 tokens are skipped before embedding; "
    "chunking, the embedding model, retrieval, and the serve path are unchanged.",
    "Versus the re-recorded baseline (`MIN_CHUNK_TOKENS = 0`): multi-project "
    "embeddings fell ~4.5% and its embed/total time ~4.8%, with no recall@k "
    "change; single-project cost was within noise. See "
    "eval/experiments/drop_small_chunks.md.",
    "The question set is small (3 single / 6 multi answerable), so this bounds "
    "large recall regressions only, not small ones.",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(experiment="drop_small_chunks", notes=NOTES, baseline=BASELINE_RUN)
    )
