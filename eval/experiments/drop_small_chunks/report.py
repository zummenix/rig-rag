"""Render the `drop_small_chunks` experiment into `eval/reports/drop_small_chunks.html`.

From the repository root:

    python3 -m eval.experiments.drop_small_chunks.report
    # or a specific run/path:
    python3 -m eval.experiments.drop_small_chunks.report --results eval/results/drop_small_chunks/<run>/results.json
"""

from eval.report.framework.cli import report_main

NOTES = (
    "Controlled change: `MIN_CHUNK_TOKENS` in `src/ingest.rs` raised from 0 so "
    "chunks below the threshold are skipped before embedding; chunking, the "
    "embedding model, retrieval, and the serve path are unchanged.",
    "Cost and quality are compared against the re-recorded baseline at "
    "`MIN_CHUNK_TOKENS = 0` (its forced ingest records real cost).",
    "Retrieval is deterministic per (question, k); latency is measured at the "
    "recorded latency k after warmup.",
)


if __name__ == "__main__":
    raise SystemExit(report_main(experiment="drop_small_chunks", notes=NOTES))
