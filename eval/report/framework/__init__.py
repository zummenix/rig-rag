"""The report framework: loader, inline-SVG primitives, layout, and render.

Import the pieces you need from here rather than the submodules:

    from eval.report.framework import load_results, render_report

`load_results` validates the versioned `results.json` contract (and refuses an
unknown version); `render_report` turns a loaded document into a self-contained
HTML string.
"""

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
from eval.report.framework.render import render_report

__all__ = [
    "LATEST_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ResultsError",
    "UnsupportedSchemaVersion",
    "latency_k",
    "latest_results",
    "load_results",
    "ordered_profiles",
    "render_report",
    "swept_k_values",
    "validate_results",
]
