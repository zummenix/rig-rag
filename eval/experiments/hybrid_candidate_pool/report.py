"""Render the `hybrid_candidate_pool` experiment into `eval/reports/hybrid_candidate_pool.html`.

From the repository root:

    python3 -m eval.experiments.hybrid_candidate_pool.report
    # or a specific run/path:
    python3 -m eval.experiments.hybrid_candidate_pool.report --results eval/results/hybrid_candidate_pool/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded.
BASELINE_RUN = "eval/results/baseline/2026-10-25T14-33-40Z"

NOTES = (
    "Controlled change: hybrid prefetches are fused with equal RRF weights "
    "(Qdrant's default; the explicit sparse-heavy weights are gone) and the "
    "reranker scores the top 60 fused candidates instead of 30. Ingest, the "
    "collection, chunking, and embedding model are unchanged.",
    "Versus the baseline, single-project hybrid recall@7 rose 0.500 to 0.750 "
    "(recall@20 0.500 to 0.875, MRR@7 0.338 to 0.421); multi-project hybrid "
    "recall@7 rose 0.375 to 0.625 (recall@20 0.500 to 0.750, MRR@7 0.294 to "
    "0.398). No answerable question regressed in hybrid; jj-split-work, "
    "jj-conflict-resolution, jj-publish-remote, and qdrant-archive-collection "
    "entered the top 3.",
    "Cost: the hybrid arm scored 1,609 reranked documents (was 810) for 3.51 "
    "RUB (was 1.92 RUB), ~1.8x; hybrid cold p50 rose 904->1185 ms (single) "
    "and 993->1101 ms (multi). Dense improved slightly too; the sparse arm is "
    "confounded by three provider 429s (two single, one multi), which fell "
    "back to raw retrieval order.",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(
            experiment="hybrid_candidate_pool", notes=NOTES, baseline=BASELINE_RUN
        )
    )
