"""Render the `reranking` experiment into `eval/reports/reranking.html`.

From the repository root:

    python3 -m eval.experiments.reranking.report
    # or a specific run/path:
    python3 -m eval.experiments.reranking.report --results eval/results/reranking/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded. See
# eval/experiments/reranking.md.
BASELINE_RUN = "eval/results/baseline/2026-10-18T10-11-48Z"

NOTES = (
    "Controlled change: `serve` reranks the top 50 fused hybrid candidates "
    "with the local cross-encoder `jinaai/jina-reranker-v1-turbo-en` and "
    "returns the best `k`; ingest and the collection schema are unchanged.",
    "Versus the committed baseline: single-project recall@20 rose 0.250 to "
    "0.625 (recall@10 0.250 to 0.375) but recall@7 was unchanged; multi-project "
    "recall@7/recall@10 fell 0.313/0.375 to 0.250/0.313 and recall@20 stayed "
    "0.500. Mean MRR@7 moved +0.006 single / +0.021 multi. p50 rose from "
    "~9 ms to ~1.72 s on both profiles (~180-190x).",
    "Ingest cost is not measured in either arm (both reused existing "
    "collections); reranking is query-time only. The question set is 8 single "
    "/ 16 multi answerable, so small movements are not certified.",
    "Decision: not adopted. The quality movement is mixed and the ~1.7 s p50 "
    "cost is disqualifying; the code is reverted and the results remain.",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(experiment="reranking", notes=NOTES, baseline=BASELINE_RUN)
    )
