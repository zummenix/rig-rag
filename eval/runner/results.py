"""The versioned `results.json` contract plus aggregation and writing.

`results.json` is the render input for P4 and is committed as an artifact, so
its shape is a tested contract: bump `RESULTS_SCHEMA_VERSION` when a consumer
must know about a change. Fixtures per version live in `eval/report/tests`
(P4); for now `eval/tests/test_results.py` pins the current version.

Layout (v1):

```jsonc
{
  "schema_version": 1,
  "experiment": "<id>",
  "run": "2026-10-10T12-00-00Z",     // also the run directory name
  "started_at": "2026-10-10T12:00:00Z",
  "commit": "<git commit SHA>",
  "config": { "<profile>": { "rig-rag.toml": {...}, "sources.json": [...] } },
  "environment": { "os", "arch", "python", "binary", "qdrant_url" },
  "k_values": [1, 3, 5, 7, 10, 20],   // the swept k set (default = contract grid)
  "profiles": {
    "<profile>": {
      "collection": "...", "prefix": "...", "data_root": "...",
      "source_names": ["..."],
      "latency": { "warmup", "reps", "latency_k", "samples_ms",
                   "p50_ms", "p95_ms" },
      "questions": [
        { "id", "question", "answerable", "gold": [...], "notes",
          "by_k": { "1": { per-k metrics }, ... },
          "latency_ms": [...] }
      ]
    }
  },
  "degradation": { "single-project->multi-project": { "recall@k", "purity@k", "p95_ms" } },
  "summary": { "<profile>": { aggregates } }
}
```

The swept `k` set is recorded in `k_values` and defaults to the contract grid
(`metrics.K_VALUES`), but a run may sweep a subset (e.g. `--k 1,3,5`). Every
`k` used for a metric label (`recall@k`, `purity@k`, the unanswerable no-hit
rate) comes from that recorded set, and `latency_k` — recorded per profile in
`latency.latency_k` — must be one of the swept values.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from eval.questions import MULTI_PROFILE, SINGLE_PROFILE, Question
from eval.runner.metrics import delta, mean

RESULTS_SCHEMA_VERSION = 1


def utc_run_id() -> str:
    """A colon-free ISO-8601 UTC timestamp usable as a directory name."""

    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def utc_timestamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def config_snapshot(config: dict, sources: list[dict]) -> dict:
    """The exact config/sources a profile was measured with."""

    return {"rig-rag.toml": config, "sources.json": sources}


def gold_entry(question: Question) -> list[dict]:
    entries = []
    for item in question.gold:
        entry: dict = {"source": item.source, "path": item.path}
        if item.lines is not None:
            entry["lines"] = list(item.lines)
        if item.must_contain is not None:
            entry["must_contain"] = item.must_contain
        entries.append(entry)
    return entries


def validate_metric_k(k_values, latency_k: int) -> tuple[int, ...]:
    """Normalizes the swept k set and checks `latency_k` is part of it.

    Shared by the CLI and `build_results` so a programmatic caller cannot
    produce an artifact whose aggregation reads a k that was never swept.
    """

    values = tuple(sorted({int(k) for k in k_values}))
    if not values:
        raise ValueError("at least one k value is required")
    if int(latency_k) not in values:
        raise ValueError(
            f"latency_k {latency_k} must be one of the swept k values {list(values)}"
        )
    return values


def build_results(
    *,
    experiment: str,
    run: str,
    started_at: str,
    commit: str,
    config: dict,
    environment: dict,
    profiles: dict,
    k_values,
    latency_k: int,
) -> dict:
    values = validate_metric_k(k_values, latency_k)
    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "experiment": experiment,
        "run": run,
        "started_at": started_at,
        "commit": commit,
        "config": config,
        "environment": environment,
        "k_values": list(values),
        "profiles": profiles,
        "degradation": degradation(profiles, values, int(latency_k)),
        "summary": summarize(profiles, values, int(latency_k)),
    }


def _question_index(profile_result: dict) -> dict[str, dict]:
    return {question["id"]: question for question in profile_result["questions"]}


def summarize(profiles: dict, k_values: tuple[int, ...], latency_k: int) -> dict:
    """Per-profile aggregates over the applicable questions."""

    summary: dict = {}
    for name, profile in profiles.items():
        by_id = _question_index(profile)
        applied = profile["questions"]
        answered = [q for q in applied if q["answerable"]]
        unanswerable = [q for q in applied if not q["answerable"]]

        mean_recall = {}
        for k in k_values:
            key = str(k)
            mean_recall[f"recall@{k}"] = mean(
                by_id[q["id"]]["by_k"][key]["recall"] for q in answered
            )

        mean_purity = mean(
            by_id[q["id"]]["by_k"][str(latency_k)]["purity"] for q in answered
        )
        mean_mrr = mean(
            by_id[q["id"]]["by_k"][str(latency_k)]["mrr"] for q in answered
        )

        no_hit_values = [
            by_id[q["id"]]["by_k"][str(k)]["no_hit"]
            for q in unanswerable
            for k in k_values
        ]

        summary[name] = {
            "questions_applied": len(applied),
            "answerable": len(answered),
            "unanswerable": len(unanswerable),
            "mean_recall": mean_recall,
            f"mean_purity@{latency_k}": mean_purity,
            f"mean_mrr@{latency_k}": mean_mrr,
            "unanswerable_no_hit_rate": mean(no_hit_values),
            "latency": {
                "p50_ms": profile["latency"]["p50_ms"],
                "p95_ms": profile["latency"]["p95_ms"],
            },
        }
    return summary


def degradation(profiles: dict, k_values: tuple[int, ...], latency_k: int) -> dict:
    """Single -> multi deltas over the questions both profiles share."""

    if SINGLE_PROFILE not in profiles or MULTI_PROFILE not in profiles:
        return {}

    single = _question_index(profiles[SINGLE_PROFILE])
    multi = _question_index(profiles[MULTI_PROFILE])
    shared = sorted(
        set(single) & set(multi) & {qid for qid, q in single.items() if q["answerable"]}
    )

    entry: dict = {
        "from": SINGLE_PROFILE,
        "to": MULTI_PROFILE,
        "questions_compared": shared,
    }
    for k in k_values:
        key = str(k)
        single_recall = mean(single[qid]["by_k"][key]["recall"] for qid in shared)
        multi_recall = mean(multi[qid]["by_k"][key]["recall"] for qid in shared)
        entry[f"recall@{k}"] = delta(multi_recall, single_recall)

    single_purity = mean(single[qid]["by_k"][str(latency_k)]["purity"] for qid in shared)
    multi_purity = mean(multi[qid]["by_k"][str(latency_k)]["purity"] for qid in shared)
    entry[f"purity@{latency_k}"] = delta(multi_purity, single_purity)

    entry["p95_ms"] = delta(
        profiles[MULTI_PROFILE]["latency"]["p95_ms"],
        profiles[SINGLE_PROFILE]["latency"]["p95_ms"],
    )

    return {f"{SINGLE_PROFILE}->{MULTI_PROFILE}": entry}


def write_results(run_dir: Path, results: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "results.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return path


def write_hits(run_dir: Path, records: list[dict]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "hits.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path
