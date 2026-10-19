"""Render the `cloud_reranking` experiment into `eval/reports/cloud_reranking.html`.

From the repository root:

    python3 -m eval.experiments.cloud_reranking.report
    # or a specific run/path:
    python3 -m eval.experiments.cloud_reranking.report --results eval/results/cloud_reranking/<run>/results.json
"""

from eval.report.framework.cli import report_main

# Pinned by path (not experiment id) so the record's hard-coded deltas keep
# matching this report as newer baseline runs are recorded.
BASELINE_RUN = "eval/results/baseline/2026-10-18T10-11-48Z"

NOTES = (
    "Controlled change: `serve` reranks the top 30 fused hybrid candidates "
    "with the cloud `rerank-3` endpoint at `https://api.aitunnel.ru/v1` and "
    "returns the best `k`; ingest and the collection schema are unchanged.",
    "Versus the committed baseline: single-project recall@7 rose 0.250 to "
    "0.500 (MRR@7 0.150 to 0.338); multi-project recall@7/recall@10 rose "
    "0.313/0.375 to 0.375/0.500 (MRR@7 0.188 to 0.294, purity@7 0.866 to "
    "0.929). Unlike the rejected local reranker, multi-project recall@7 did "
    "not regress and recall@20 stayed 0.500.",
    "Treatment arm: all retrieval queries run with `serve --cache-rerank`, "
    "so p50/p95 are warm cache hits (~10 ms); cold p50 is the first provider "
    "call per question (~1.1 s, p95 1.5-1.7 s). 30 provider calls cost 2.17 "
    "RUB in total. The comparison table uses cold samples for this arm.",
    "Caveat: one run per arm, 9/18 cold samples per profile, and the run was "
    "measured from an uncommitted working tree (binary SHA-256 recorded).",
)


if __name__ == "__main__":
    raise SystemExit(
        report_main(experiment="cloud_reranking", notes=NOTES, baseline=BASELINE_RUN)
    )
