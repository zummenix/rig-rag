"""Render the `baseline` experiment into `eval/reports/baseline.html`.

From the repository root:

    python3 -m eval.experiments.baseline.report
    # or a specific run/path:
    python3 -m eval.experiments.baseline.report --results eval/results/baseline/<run>/results.json
"""

from eval.report.framework.cli import report_main

NOTES = (
    "Harness-validation baseline: no controlled change versus the production "
    "retrieval path.",
    "single-project is the jj corpus; multi-project adds podman and qdrant.",
    "Retrieval is deterministic per (question, k); latency is measured at the "
    "recorded latency k after warmup.",
)


if __name__ == "__main__":
    raise SystemExit(report_main(experiment="baseline", notes=NOTES))
