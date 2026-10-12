"""Render the `chunk_max_tokens` experiment into `eval/reports/chunk_max_tokens.html`.

From the repository root:

    python3 -m eval.experiments.chunk_max_tokens.report
    # or a specific run/path:
    python3 -m eval.experiments.chunk_max_tokens.report --results eval/results/chunk_max_tokens/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded. See
# eval/experiments/chunk_max_tokens.md.
BASELINE_RUN = "eval/results/baseline/2026-10-10T15-58-51Z"

NOTES = (
    "Controlled change: `chunk_md_doc` in `src/chunk.rs` now caps chunks at "
    "`MAX_CHUNK_TOKENS = 384` (o200k_base), replacing the chunker's implicit "
    "default of 512, so the longest chunks stay inside BGE-small-en-v1.5's "
    "512-token WordPiece window instead of being truncated at embedding time.",
    "Versus the reused baseline (chunker default 512): see "
    "eval/experiments/chunk_max_tokens.md.",
    "The question set is small (3 single / 6 multi answerable), so this bounds "
    "large recall regressions only, not small ones.",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(experiment="chunk_max_tokens", notes=NOTES, baseline=BASELINE_RUN)
    )
