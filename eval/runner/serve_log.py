"""Parse and aggregate the serve process's structured eval-metrics lines.

With `--log-eval-metrics` the server writes one line per question per phase:

    search q=1f3a2b4c5d6e7f80 k=20 candidates=30 duration_ms=412
    rerank q=1f3a2b4c5d6e7f80 model=rerank-3 docs=30 results=30 duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03

`q` is the first 16 hex chars of `sha256(question)`, so the offline runner can
join both lines to a question without a request id. A failed provider call is
logged as well (`results=0`, `status=failed`, `error=` last), so a quality run
that silently degraded is visible in the committed results.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

from eval.runner.metrics import percentile

SEARCH_PREFIX = "search "
RERANK_PREFIX = "rerank "
_KINDS = ("search", "rerank")


def question_hash(question: str) -> str:
    """First 16 lowercase hex chars of `sha256(question)` (UTF-8, no normalization)."""

    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def _number(value: str) -> int | float | None:
    try:
        if "." in value or "e" in value.lower():
            return float(value)
        return int(value)
    except ValueError:
        return None


def _fields(text: str) -> dict:
    """Parses `key=value` fields; an `error=` tail keeps its spaces."""

    if "error=" in text:
        head, _, tail = text.partition("error=")
        error: str | None = tail
    else:
        head, error = text, None

    fields: dict = {}
    for token in head.split():
        key, separator, raw = token.partition("=")
        if not separator:
            continue
        value = _number(raw)
        fields[key] = raw if value is None else value
    if error is not None:
        fields["error"] = error
    return fields


def parse_log(log_path: Path) -> dict[str, dict]:
    """The per-question metrics, keyed by question hash.

    Shape: `{hash: {"search": {...}, "rerank": {...}}}`. A hash with a repeated
    line for the same phase is an anomaly (the eval runs sequentially and
    expects exactly one of each); the last line wins and a warning is emitted.
    """

    records: dict[str, dict] = {}
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        kind = next((name for name in _KINDS if line.startswith(name + " ")), None)
        if kind is None:
            continue
        fields = _fields(line[len(kind) + 1 :])
        q = fields.get("q")
        if not isinstance(q, str):
            continue
        entry = records.setdefault(q, {})
        if kind in entry:
            warnings.warn(
                f"duplicate {kind} line for question hash {q} in {log_path}",
                stacklevel=2,
            )
        entry[kind] = fields
    return records


def _durations(records: dict[str, dict], kind: str) -> list[float]:
    values: list[float] = []
    for entry in records.values():
        fields = entry.get(kind)
        if fields is None:
            continue
        duration = fields.get("duration_ms")
        if isinstance(duration, (int, float)):
            values.append(float(duration))
    return values


def rerank_stats(log_path: Path) -> dict:
    """Sums provider usage and failures across one serve log."""

    records = parse_log(log_path)
    calls = 0
    failures = 0
    documents = 0
    tokens: int | None = None
    cost: float | None = None
    cost_rub: float | None = None

    for entry in records.values():
        fields = entry.get("rerank")
        if fields is None:
            continue
        calls += 1
        docs = fields.get("docs")
        if isinstance(docs, int):
            documents += docs
        if fields.get("status") == "failed":
            failures += 1
        value = fields.get("tokens")
        if isinstance(value, int):
            tokens = (tokens or 0) + value
        value = fields.get("cost")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost = (cost or 0.0) + float(value)
        value = fields.get("cost_rub")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost_rub = (cost_rub or 0.0) + float(value)

    durations = _durations(records, "rerank")
    return {
        "calls": calls,
        "documents": documents,
        "tokens": tokens,
        "cost": cost,
        "cost_rub": cost_rub,
        "duration_ms": durations,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "failures": failures,
    }


def search_stats(log_path: Path) -> dict:
    """Aggregates the local search phase (embed + Qdrant) across one serve log."""

    records = parse_log(log_path)
    durations = _durations(records, "search")
    return {
        "calls": len(durations),
        "duration_ms": durations,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
    }
