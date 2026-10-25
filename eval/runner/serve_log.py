"""Parse and aggregate the serve process's structured eval-metrics lines.

With `--log-eval-metrics` the server writes one line per query per phase:

    search q=1f3a2b4c5d6e7f80 k=20 mode=hybrid candidates=30 duration_ms=412
    rerank q=1f3a2b4c5d6e7f80 mode=hybrid model=rerank-3 docs=30 results=30 duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03

`q` is the first 16 hex chars of `sha256(question)`. Because one run queries a
question once per search mode, a record is keyed by both identity fields:
`{hash: {mode: {"search": {...}, "rerank": {...}}}}`. A repeated phase for the
same `(hash, mode)` is an anomaly (the eval runs sequentially and expects one of
each); the last line wins and a warning is emitted.

Identity fields (`q`, `mode`, `model`, `status`) are kept as **raw strings** and
never coerced to numbers. A 16-char hex prefix may look numeric — e.g.
`7812897326268e41` contains an `e` and would otherwise parse as a float, which
silently dropped that question's whole record from the metrics.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

from eval.runner.metrics import percentile

_KINDS = ("search", "rerank")
_STRING_FIELDS = frozenset(("q", "mode", "model", "status"))
_UNKNOWN_MODE = "unknown"


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
    """Parses `key=value` fields; identity fields stay strings, `error=` keeps its spaces."""

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
        if key in _STRING_FIELDS:
            fields[key] = raw
            continue
        value = _number(raw)
        fields[key] = raw if value is None else value
    if error is not None:
        fields["error"] = error
    return fields


def parse_log(log_path: Path) -> dict[str, dict[str, dict]]:
    """The per-question metrics, keyed by `hash` then `mode`.

    Shape: `{hash: {mode: {"search": {...}, "rerank": {...}}}}`. A line without
    a `mode` field is filed under `"unknown"` (older logs predate per-mode
    records); current servers always name it.
    """

    records: dict[str, dict[str, dict]] = {}
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        kind = next((name for name in _KINDS if line.startswith(name + " ")), None)
        if kind is None:
            continue
        fields = _fields(line[len(kind) + 1 :])
        q = fields.get("q")
        if not isinstance(q, str):
            continue
        mode = fields.get("mode")
        if not isinstance(mode, str):
            mode = _UNKNOWN_MODE
        entry = records.setdefault(q, {}).setdefault(mode, {})
        if kind in entry:
            warnings.warn(
                f"duplicate {kind} line for question hash {q} in mode {mode} "
                f"in {log_path}",
                stacklevel=2,
            )
        entry[kind] = fields
    return records


def modes_in(records: dict[str, dict[str, dict]]) -> tuple[str, ...]:
    """Every mode that appears anywhere in a parsed log, in first-seen order."""

    seen: dict[str, None] = {}
    for by_mode in records.values():
        for mode in by_mode:
            seen.setdefault(mode, None)
    return tuple(seen)


def _entries(records: dict[str, dict[str, dict]], mode: str) -> list[dict]:
    entries: list[dict] = []
    for by_mode in records.values():
        entry = by_mode.get(mode)
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _durations(entries: list[dict], kind: str) -> list[float]:
    values: list[float] = []
    for entry in entries:
        fields = entry.get(kind)
        if fields is None:
            continue
        duration = fields.get("duration_ms")
        if isinstance(duration, (int, float)):
            values.append(float(duration))
    return values


def rerank_stats(records: dict[str, dict[str, dict]], mode: str) -> dict:
    """Sums provider usage and failures for one mode's rerank calls."""

    calls = 0
    failures = 0
    documents = 0
    tokens: int | None = None
    cost: float | None = None
    cost_rub: float | None = None

    entries = _entries(records, mode)
    for entry in entries:
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

    durations = _durations(entries, "rerank")
    return {
        "calls": calls,
        "documents": documents,
        "tokens": tokens,
        "cost": cost,
        "cost_rub": cost_rub,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "failures": failures,
    }


def search_stats(records: dict[str, dict[str, dict]], mode: str) -> dict:
    """Aggregates one mode's local search phase (embed + Qdrant)."""

    durations = _durations(_entries(records, mode), "search")
    return {
        "calls": len(durations),
        "duration_ms": durations,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
    }
