"""The versioned `results.json` contract plus aggregation and writing.

`results.json` is the render input for the report framework and is committed as
an artifact, so its shape is a tested contract: bump `RESULTS_SCHEMA_VERSION`
when a consumer must know about a change. Fixtures per version live in
`eval/report/tests`; `eval/tests/test_results.py` pins the current version.

Layout (v2):

```jsonc
{
  "schema_version": 2,
  "experiment": "<id>",
  "run": "2026-10-10T12-00-00Z",     // also the run directory name
  "started_at": "2026-10-10T12:00:00Z",
  "commit": "<git commit SHA>",      // best-effort; "" when git is unavailable
  "commit_ref": "origin/main",       // best-effort pushed ref containing `commit`; "" when none
  "config": { "<profile>": { "rig-rag.toml": {...}, "sources.json": [...] } },
  "environment": { "os", "arch", "python", "binary", "binary_sha256", "qdrant_url" },
  "k_values": [3, 7, 20],            // reported k set (default = contract grid)
  "profiles": {
    "<profile>": {
      "collection": "...", "prefix": "...", "data_root": "...",
      "source_names": ["..."],
      // One sample per question per phase; percentiles are across questions,
      // not repeated trials. Samples are cold: exactly one uncached query per
      // question at max(k).
      "timings": {
        "search": { "samples_ms", "p50_ms", "p95_ms", "mean_ms", "sum_ms" },
        "rerank": { ... },              // empty when no reranker is configured
        "e2e":    { ... }               // client-observed HTTP round trip
      },
      "rerank": { "calls", "documents", "tokens", "cost", "cost_rub",
                  "duration_ms", "p50_ms", "p95_ms", "failures" },
      "questions": [
        { "id", "question", "answerable", "gold": [...], "notes",
          "by_k": { "3": { per-k metrics }, ... },
          "timings": { "e2e_ms", "search_ms", "rerank_ms" },
          "rerank": { "docs", "results", "tokens", "cost", "cost_rub",
                      "status": "success"|"failed" } | null }
      ]
    }
  },
  "degradation": { "single-project->multi-project": { "recall@k", "purity@k", "mrr@k", "p95_ms" } },
  "summary": { "<profile>": { aggregates } }
}
```

The reported `k` set is recorded in `k_values` and defaults to the contract grid
(`metrics.K_VALUES`), but a run may report a subset. Every k used for a metric
label (`recall@k`, `purity@k`, `mrr@k`, the unanswerable no-hit rate) comes from
that recorded set. Metrics at smaller k are exact prefixes of the single
`max(k)` query, because the reranker's candidate pool covers `max(k)`.

`commit`/`commit_ref` are best-effort breadcrumbs. The runner does not require a
clean or pushed work tree (jj working-copy commits, detached `HEAD`s, and dirty
trees are all measurable), so the record's `## Reproduction` — not `results.json`
— carries the human-facing provenance.

v1 files (warmup/reps/latency_k, cache-aware latency samples) are not comparable
with v2 and are refused by the report loader.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from eval.questions import MULTI_PROFILE, SINGLE_PROFILE, Question
from eval.runner.metrics import delta, mean

RESULTS_SCHEMA_VERSION = 2

_TIMING_PHASES = ("search", "rerank", "e2e")


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


def validate_metric_k(k_values) -> tuple[int, ...]:
    """Normalizes the reported k set; shared by the CLI and `build_results`."""

    values = tuple(sorted({int(k) for k in k_values}))
    if not values:
        raise ValueError("at least one k value is required")
    return values


def build_results(
    *,
    experiment: str,
    run: str,
    started_at: str,
    commit: str,
    commit_ref: str,
    config: dict,
    environment: dict,
    profiles: dict,
    k_values,
) -> dict:
    values = validate_metric_k(k_values)
    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "experiment": experiment,
        "run": run,
        "started_at": started_at,
        "commit": commit,
        "commit_ref": commit_ref,
        "config": config,
        "environment": environment,
        "k_values": list(values),
        "profiles": profiles,
        "degradation": degradation(profiles, values),
        "summary": summarize(profiles, values),
    }


def _question_index(profile_result: dict) -> dict[str, dict]:
    return {question["id"]: question for question in profile_result["questions"]}


def _timing_rollup(timing: dict | None) -> dict:
    timing = timing or {}
    return {
        "p50_ms": timing.get("p50_ms"),
        "p95_ms": timing.get("p95_ms"),
        "mean_ms": timing.get("mean_ms"),
    }


def summarize(profiles: dict, k_values: tuple[int, ...]) -> dict:
    """Per-profile aggregates over the applicable questions."""

    summary: dict = {}
    for name, profile in profiles.items():
        by_id = _question_index(profile)
        applied = profile["questions"]
        answered = [q for q in applied if q["answerable"]]
        unanswerable = [q for q in applied if not q["answerable"]]

        mean_recall = {
            f"recall@{k}": mean(
                by_id[q["id"]]["by_k"][str(k)]["recall"] for q in answered
            )
            for k in k_values
        }
        mean_purity = {
            f"mean_purity@{k}": mean(
                by_id[q["id"]]["by_k"][str(k)]["purity"] for q in answered
            )
            for k in k_values
        }
        mean_mrr = {
            f"mean_mrr@{k}": mean(
                by_id[q["id"]]["by_k"][str(k)]["mrr"] for q in answered
            )
            for k in k_values
        }

        no_hit_values = [
            by_id[q["id"]]["by_k"][str(k)]["no_hit"]
            for q in unanswerable
            for k in k_values
        ]

        timings = {
            phase: _timing_rollup((profile.get("timings") or {}).get(phase))
            for phase in _TIMING_PHASES
        }

        summary[name] = {
            "questions_applied": len(applied),
            "answerable": len(answered),
            "unanswerable": len(unanswerable),
            "mean_recall": mean_recall,
            **mean_purity,
            **mean_mrr,
            "unanswerable_no_hit_rate": mean(no_hit_values),
            "timings": timings,
            "rerank": profile.get("rerank"),
        }
    return summary


def degradation(profiles: dict, k_values: tuple[int, ...]) -> dict:
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
    for metric, extract in (
        ("recall", lambda by_k: by_k["recall"]),
        ("purity", lambda by_k: by_k["purity"]),
        ("mrr", lambda by_k: by_k["mrr"]),
    ):
        for k in k_values:
            key = str(k)
            single_values = mean(
                extract(single[qid]["by_k"][key]) for qid in shared
            )
            multi_values = mean(extract(multi[qid]["by_k"][key]) for qid in shared)
            entry[f"{metric}@{k}"] = delta(multi_values, single_values)

    single_timing = (profiles[SINGLE_PROFILE].get("timings") or {}).get("e2e") or {}
    multi_timing = (profiles[MULTI_PROFILE].get("timings") or {}).get("e2e") or {}
    entry["p95_ms"] = delta(multi_timing.get("p95_ms"), single_timing.get("p95_ms"))

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
