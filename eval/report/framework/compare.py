"""Baseline-vs-candidate deltas for the report's comparison section.

Pure functions over loaded documents: they extract the metrics a diff needs and
compute `candidate - baseline` (and percent). They never load files or render
HTML. A missing or unmeasured value stays `None` all the way through, so the
renderer prints an em dash instead of inventing a zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval.report.framework.ingest import cost_measured, profile_label


@dataclass(frozen=True)
class Baseline:
    """The run a report is compared against."""

    label: str
    results: dict | None = None
    ingest: tuple[dict, ...] = ()


# Ingest cost rows: (key, label, kind). `kind` drives value formatting.
INGEST_ROWS: tuple[tuple[str, str, str], ...] = (
    ("embeddings", "Embeddings", "count"),
    ("chunks", "Chunks", "count"),
    ("tokens", "Tokens", "count"),
    ("fetch", "Fetch ms", "ms"),
    ("load", "Load ms", "ms"),
    ("chunk", "Chunk ms", "ms"),
    ("embed", "Embed ms", "ms"),
    ("insert", "Insert ms", "ms"),
    ("total", "Total ms", "ms"),
    ("peak_rss", "Peak RSS", "bytes"),
)

_INGEST_PATHS: dict[str, tuple[str, ...]] = {
    "embeddings": ("totals", "embeddings"),
    "chunks": ("totals", "chunks"),
    "tokens": ("totals", "tokens"),
    "fetch": ("timing_ms", "fetch"),
    "load": ("timing_ms", "load"),
    "chunk": ("timing_ms", "chunk"),
    "embed": ("timing_ms", "embed"),
    "insert": ("timing_ms", "insert"),
    "total": ("timing_ms", "total"),
    "peak_rss": ("memory", "peak_rss_bytes"),
}

_PREFERRED_PROFILES = ("single-project", "multi-project")


def _dig(document: object, path: tuple[str, ...]) -> object:
    node = document
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def ordered_names(names: object) -> list[str]:
    """Profile names, single-project before multi-project, then alphabetically."""

    unique = {name for name in names if name}
    return sorted(
        unique,
        key=lambda name: (
            _PREFERRED_PROFILES.index(name) if name in _PREFERRED_PROFILES else len(_PREFERRED_PROFILES),
            name,
        ),
    )


def combined_profiles(
    candidate_results: dict | None,
    candidate_ingest: tuple[dict, ...],
    baseline: Baseline | None,
) -> list[str]:
    """Every profile named by either run's retrieval or ingestion part."""

    names: set[str] = set()
    if candidate_results:
        names |= set(candidate_results.get("profiles") or {})
    names |= {profile_label(document) for document in candidate_ingest}
    if baseline is not None:
        if baseline.results:
            names |= set(baseline.results.get("profiles") or {})
        names |= {profile_label(document) for document in baseline.ingest}
    return ordered_names(names)


def ingest_metrics(document: dict | None) -> dict[str, float | None]:
    """One ingest report's cost metrics; every value `None` when cost was not measured."""

    if document is None:
        return {}
    measured = cost_measured(document)
    return {
        key: (_dig(document, path) if measured else None)
        for key, path in _INGEST_PATHS.items()
    }


def retrieval_metrics(
    results: dict, profile: str, k_values: tuple[int, ...], latency_k: int
) -> dict[str, float | None]:
    """One profile's aggregate retrieval metrics, keyed for the comparison table."""

    summary = (results.get("summary") or {}).get(profile) or {}
    mean_recall = summary.get("mean_recall") or {}
    latency = summary.get("latency") or {}
    metrics: dict[str, float | None] = {}
    for k in k_values:
        metrics[f"recall@{k}"] = mean_recall.get(f"recall@{k}")
    metrics[f"purity@{latency_k}"] = summary.get(f"mean_purity@{latency_k}")
    metrics[f"mrr@{latency_k}"] = summary.get(f"mean_mrr@{latency_k}")
    metrics["p50_ms"] = latency.get("p50_ms")
    metrics["p95_ms"] = latency.get("p95_ms")
    metrics["no_hit_rate"] = summary.get("unanswerable_no_hit_rate")
    return metrics


def question_metrics(results: dict, profile: str, latency_k: int) -> dict[str, dict]:
    """Per-question `recall@purity` at the latency k, keyed by question id."""

    out: dict[str, dict] = {}
    profile_document = (results.get("profiles") or {}).get(profile) or {}
    for question in profile_document.get("questions", []):
        by_k = (question.get("by_k") or {}).get(str(latency_k)) or {}
        out[question.get("id", "")] = {
            "answerable": question.get("answerable", True),
            "recall": by_k.get("recall"),
            "purity": by_k.get("purity"),
        }
    return out


def delta(baseline: object, candidate: object) -> float | None:
    """`candidate - baseline`, or `None` when either side is missing."""

    if baseline is None or candidate is None:
        return None
    try:
        return float(candidate) - float(baseline)
    except (TypeError, ValueError):
        return None


def percent(baseline: object, candidate: object) -> float | None:
    """Relative change `(candidate - baseline) / baseline`, or `None`."""

    change = delta(baseline, candidate)
    if change is None:
        return None
    try:
        base = float(baseline)
    except (TypeError, ValueError):
        return None
    if base == 0:
        return None
    return change / base
