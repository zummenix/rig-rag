"""Retrieval metrics computed per question and k.

Definitions (fixed and unit-tested, since `results.json` is a contract):

* **hit@k** — 1 if any gold item is covered by the top-k hits, else 0.
* **recall@k** — distinct gold items covered by the top-k hits / total gold
  items. Defined only for answerable questions.
* **precision@k** — top-k hits that cover at least one gold item / number of
  hits returned. Measuring the returned list (rather than dividing by k) keeps
  it meaningful when the threshold returns fewer than k hits.
* **MRR@k** — reciprocal rank of the first hit that covers any gold item.
* **source purity@k** — share of the returned hits whose source is one of the
  question's gold sources. This can be high even when recall is low (right
  project, wrong section), which is exactly what the cross-project questions
  probe.

Unanswerable questions (`gold == []`) have `recall`/`precision`/`mrr`/`purity`
of `None` and report `no_hit` (1 when no hit clears the threshold) instead.

Percentiles use linear interpolation between order statistics (the numpy
default), so p95 of a handful of samples is well-defined and deterministic.
"""

from __future__ import annotations

import math

from eval.questions import Question
from eval.runner.gold import covering_gold_indexes, source_of

# k values swept for every applicable question.
K_VALUES: tuple[int, ...] = (1, 3, 5, 7, 10, 20)

# k at which per-question latency is measured (the request default).
LATENCY_K: int = 7


def percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile; `None` for an empty sample."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (p / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def location(hit: dict) -> str:
    start = int(hit["start_line"])
    end = int(hit["end_line"])
    if start >= end:
        return f"{hit['path']}:{start}"
    return f"{hit['path']}:{start}-{end}"


def evaluate_at_k(hits: list[dict], question: Question, data_root: str) -> dict:
    """The `by_k` entry for one (question, k)."""

    locations = [location(hit) for hit in hits]

    if not question.answerable:
        return {
            "answerable": False,
            "hits_returned": len(hits),
            "no_hit": 1 if not hits else 0,
            "hit": None,
            "recall": None,
            "precision": None,
            "mrr": None,
            "purity": None,
            "hits": locations,
        }

    gold = question.gold
    covered = [False] * len(gold)
    relevant: list[bool] = []
    for hit in hits:
        indexes = covering_gold_indexes(hit, gold, data_root)
        relevant.append(bool(indexes))
        for index in indexes:
            covered[index] = True

    relevant_hits = sum(relevant)
    gold_sources = question.gold_sources()
    purity_hits = sum(
        1 for hit in hits if source_of(hit["path"], data_root) in gold_sources
    )

    mrr = 0.0
    for rank, is_relevant in enumerate(relevant, start=1):
        if is_relevant:
            mrr = 1.0 / rank
            break

    return {
        "answerable": True,
        "hits_returned": len(hits),
        "no_hit": None,
        "hit": 1 if any(covered) else 0,
        "recall": sum(covered) / len(gold),
        "precision": relevant_hits / len(hits) if hits else 0.0,
        "mrr": mrr,
        "purity": purity_hits / len(hits) if hits else 0.0,
        "hits": locations,
    }


def mean(values) -> float | None:
    """Arithmetic mean of the non-`None` values, or `None` if none remain."""

    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def delta(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return None
    return new - old
