"""The report framework: loaders, inline-SVG primitives, layout, and render.

Import the pieces you need from here rather than the submodules:

    from eval.report.framework import load_results, render_report

`load_results` and `load_ingest` validate the two versioned contracts (and
refuse unknown versions); `render_report` turns one run's loaded parts into a
self-contained HTML string. `latest_run` / `discover_run_parts` locate the parts
a run directory holds.
"""

from eval.report.framework.compare import Baseline
from eval.report.framework.ingest import (
    INGEST_LATEST_SCHEMA_VERSION,
    INGEST_SUPPORTED_SCHEMA_VERSIONS,
    IngestError,
    UnsupportedIngestSchemaVersion,
    cost_measured,
    load_ingest,
    validate_ingest,
)
from eval.report.framework.loader import (
    LATEST_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ResultsError,
    UnsupportedSchemaVersion,
    latency_k,
    latest_results,
    load_results,
    ordered_profiles,
    swept_k_values,
    validate_results,
)
from eval.report.framework.parts import (
    LoadedRun,
    RunParts,
    discover_run_parts,
    latest_run,
    load_run,
    resolve_run,
)
from eval.report.framework.render import render_report

__all__ = [
    "INGEST_LATEST_SCHEMA_VERSION",
    "INGEST_SUPPORTED_SCHEMA_VERSIONS",
    "LATEST_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Baseline",
    "IngestError",
    "LoadedRun",
    "ResultsError",
    "RunParts",
    "UnsupportedIngestSchemaVersion",
    "UnsupportedSchemaVersion",
    "cost_measured",
    "discover_run_parts",
    "latency_k",
    "latest_results",
    "latest_run",
    "load_ingest",
    "load_results",
    "load_run",
    "ordered_profiles",
    "render_report",
    "resolve_run",
    "swept_k_values",
    "validate_ingest",
    "validate_results",
]
