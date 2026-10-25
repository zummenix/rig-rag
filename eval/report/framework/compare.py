"""Baseline-vs-candidate deltas for the report's comparison section.

Pure functions over loaded documents and their `(profile, mode)` series: they
extract the metrics a diff needs, pair series by `(profile, mode)`, and compute
`candidate - baseline` (and percent). They never load files or render HTML. A
missing or unmeasured value stays `None` all the way through, so the renderer
prints an em dash instead of inventing a zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval.report.framework import loader
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
    entry: dict, k_values: tuple[int, ...]
) -> dict[str, float | None]:
    """One (profile, mode) series entry's aggregate metrics, keyed for the diff table."""

    summary = entry.get("summary") or {}
    mean_recall = summary.get("mean_recall") or {}
    timings = summary.get("timings") or {}
    metrics: dict[str, float | None] = {}
    for k in k_values:
        metrics[f"recall@{k}"] = mean_recall.get(f"recall@{k}")
        metrics[f"purity@{k}"] = summary.get(f"mean_purity@{k}")
        metrics[f"mrr@{k}"] = summary.get(f"mean_mrr@{k}")
    metrics["search_p50_ms"] = (timings.get("search") or {}).get("p50_ms")
    metrics["search_p95_ms"] = (timings.get("search") or {}).get("p95_ms")
    metrics["rerank_p50_ms"] = (timings.get("rerank") or {}).get("p50_ms")
    metrics["rerank_p95_ms"] = (timings.get("rerank") or {}).get("p95_ms")
    metrics["p50_ms"] = (timings.get("e2e") or {}).get("p50_ms")
    metrics["p95_ms"] = (timings.get("e2e") or {}).get("p95_ms")
    metrics["no_hit_rate"] = summary.get("unanswerable_no_hit_rate")
    return metrics


def question_metrics(
    entry: dict, k_values: tuple[int, ...]
) -> dict[str, dict]:
    """Per-question recall/purity at every k for one series entry, keyed by id."""

    out: dict[str, dict] = {}
    for question in entry.get("questions") or []:
        metrics: dict = {"answerable": question.get("answerable", True)}
        for k in k_values:
            by_k = (question.get("by_k") or {}).get(str(k)) or {}
            metrics[f"recall@{k}"] = by_k.get("recall")
            metrics[f"purity@{k}"] = by_k.get("purity")
        out[question.get("id", "")] = metrics
    return out


def _key_order(key: tuple[str, str]) -> tuple[int, int, str, str]:
    profile, mode = key
    mode_rank = (
        loader.SEARCH_MODES.index(mode) if mode in loader.SEARCH_MODES else len(loader.SEARCH_MODES)
    )
    return loader.profile_rank(profile), mode_rank, profile, mode


def pair_series(
    candidate_results: dict | None, baseline_results: dict | None
) -> tuple[list[tuple[str, str, dict, dict]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Pairs series by `(profile, mode)`.

    Returns `(pairs, only_candidate, only_baseline)`. A v2 document contributes a
    single `unknown` series per profile, so v2↔v2 pairs normally and a v3
    candidate against a v2 baseline yields no retrieval pairs (the comparison
    section explains why instead of silently diffing different arms).
    """

    candidate = loader.series(candidate_results) if candidate_results is not None else []
    baseline = loader.series(baseline_results) if baseline_results is not None else []
    baseline_by_key = {(entry["profile"], entry["mode"]): entry for entry in baseline}
    candidate_keys = {(entry["profile"], entry["mode"]) for entry in candidate}

    pairs: list[tuple[str, str, dict, dict]] = []
    for entry in candidate:
        key = (entry["profile"], entry["mode"])
        base_entry = baseline_by_key.get(key)
        if base_entry is not None:
            pairs.append((key[0], key[1], entry, base_entry))

    only_candidate = sorted(candidate_keys - set(baseline_by_key), key=_key_order)
    only_baseline = sorted(set(baseline_by_key) - candidate_keys, key=_key_order)
    return pairs, only_candidate, only_baseline


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
