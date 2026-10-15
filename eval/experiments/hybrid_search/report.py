"""Render the `hybrid_search` experiment into `eval/reports/hybrid_search.html`.

From the repository root:

    python3 -m eval.experiments.hybrid_search.report
    # or a specific run/path:
    python3 -m eval.experiments.hybrid_search.report --results eval/results/hybrid_search/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded. See
# eval/experiments/hybrid_search.md.
BASELINE_RUN = "eval/results/baseline/2026-10-14T07-08-05Z"

NOTES = (
    "Controlled change: the collection gains a named BM25 sparse vector "
    "(`Qdrant/bm25`, IDF) beside the dense BGE vector, and retrieval fuses a "
    "sparse and a dense prefetch with weighted RRF (`weights=[2,1]`, `k=2`, "
    "prefetch limit 50) instead of dense-only nearest-neighbour search.",
    "Versus the dense-only baseline: multi-project mean recall@20 rose 0.333 "
    "to 0.833 (recall@3 0.167 to 0.500); single-project was unchanged; p50 "
    "rose by ~1.5 ms. The unanswerable control's no-hit rate fell 1.000 to "
    "0.000. See eval/experiments/hybrid_search.md.",
    "The question set is small (3 single / 6 multi answerable) and has no "
    "semantic/paraphrase question, so it cannot bound a dense-recall "
    "regression nor show where dense helps.",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(experiment="hybrid_search", notes=NOTES, baseline=BASELINE_RUN)
    )
