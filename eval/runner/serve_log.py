"""Aggregate reranker usage lines from a captured `serve` log.

The server prints one line per real cloud call:

    rerank model=rerank-3 docs=30 results=30 duration_ms=2314 tokens=39 cost=0.000351

Cache hits never call the provider and therefore never print a line. Rejected
responses print a separate fallback line, which is counted as a failure so a
quality run that silently degraded is visible in the committed results.
"""

from __future__ import annotations

from pathlib import Path

from eval.runner.metrics import percentile

RERANK_PREFIX = "rerank "
FALLBACK_MARKER = "serve: rerank failed (falling back to fusion)"


def _number(value: str) -> int | float | None:
    try:
        if "." in value or "e" in value.lower():
            return float(value)
        return int(value)
    except ValueError:
        return None


def rerank_stats(log_path: Path) -> dict:
    """Sums provider usage and failures from one serve log."""

    calls = 0
    documents = 0
    durations: list[float] = []
    tokens: int | None = None
    cost: float | None = None
    cost_rub: float | None = None
    failures = 0

    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if FALLBACK_MARKER in line:
            failures += 1
            continue
        if not line.startswith(RERANK_PREFIX):
            continue
        calls += 1
        for field in line[len(RERANK_PREFIX) :].split():
            key, _, raw = field.partition("=")
            value = _number(raw)
            if key == "docs" and isinstance(value, int):
                documents += value
            elif key == "duration_ms" and value is not None:
                durations.append(float(value))
            elif key == "tokens" and isinstance(value, int):
                tokens = (tokens or 0) + value
            elif key == "cost" and isinstance(value, (int, float)):
                cost = (cost or 0.0) + float(value)
            elif key == "cost_rub" and isinstance(value, (int, float)):
                cost_rub = (cost_rub or 0.0) + float(value)

    return {
        "calls": calls,
        "documents": documents,
        "tokens": tokens,
        "cost": cost,
        "cost_rub": cost_rub,
        "duration_ms": durations,
        "p50_ms": percentile(durations, 50) if durations else None,
        "p95_ms": percentile(durations, 95) if durations else None,
        "failures": failures,
    }
