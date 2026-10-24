"""The versioned `results.json` contract plus aggregation and writing.

`results.json` is the render input for the report framework and is committed as
an artifact, so its shape is a tested contract: bump `RESULTS_SCHEMA_VERSION`
when a consumer must know about a change. Fixtures per version live in
`eval/report/tests`; `eval/tests/test_results.py` pins the current version.

Layout (v3, per-search-mode):

```jsonc
{
  "schema_version": 3,
  "experiment": "<id>",
  "run": "2026-10-10T12-00-00Z",     // also the run directory name
  "started_at": "2026-10-10T12:00:00Z",
  "commit": "<git commit SHA>",      // best-effort; "" when git is unavailable
  "commit_ref": "origin/main",       // best-effort pushed ref containing `commit`
  "config": { "<profile>": { "rig-rag.toml": {...}, "sources.json": [...] } },
  "environment": { "os", "arch", "python", "binary", "binary_sha256", "qdrant_url" },
  "modes": ["hybrid", "dense", "sparse"],  // measured modes, canonical order
  "k_values": [3, 7, 20],                  // reported k set (default contract grid)
  "profiles": {
    "<profile>": {
      "collection": "...", "prefix": "...", "data_root": "...",
      "source_names": ["..."],
      // Per-mode rollups. `timings` holds search/rerank/e2e rollups; `rerank`
      // sums provider usage for that mode.
      "modes": {
        "hybrid": {
          "timings": { "search": {...}, "rerank": {...}, "e2e": {...} },
          "rerank": { "calls", "documents", "tokens", "cost", "cost_rub",
                      "p50_ms", "p95_ms", "failures" }
        }
      },
      // Question text/gold/notes appear once per profile; only the measured
      // data is per mode.
      "questions": [
        { "id", "question", "answerable", "gold": [...], "notes",
          "modes": {
            "hybrid": {
              "by_k": {
                "3": { per-k metrics },
                "7": { per-k metrics },
                // Only the max-k entry carries the ranked hit locations;
                // smaller-k lists are exact prefixes by construction.
                "20": { per-k metrics, "hits": ["path:start-end", ...] }
              },
              "timings": { "e2e_ms", "search_ms", "rerank_ms" },
              "rerank": { "docs", "results", "tokens", "cost", "cost_rub",
                          "status": "success"|"failed" } | null
            }
          }
        }
      ]
    }
  },
  "summary": {
    "<profile>": {
      "questions_applied", "answerable", "unanswerable",   // counts once
      "modes": { "<mode>": { aggregates } }
    }
  },
  "degradation": {
    "single-project->multi-project": {
      "from", "to", "questions_compared",                  // shared once
      "modes": { "<mode>": { "recall@k", "purity@k", "mrr@k", "p95_ms" } }
    }
  }
}
```

The reported `k` set is recorded in `k_values` and defaults to the contract grid
(`metrics.K_VALUES`), but a run may report a subset. Every question is queried
once per mode at `max(k)`; metrics at smaller k are exact prefixes of that
mode's single ranked list (the query asks for `max(k)`, and a configured
reranker's candidate pool must cover it).

Scores are on the mode's own scale (RRF for hybrid, cosine for dense, BM25 for
sparse). When a reranker is configured the returned score is the reranker's
relevance for that hit, so it is comparable across modes for the same query;
without one, scores are only meaningful within a mode.

`commit`/`commit_ref` are best-effort breadcrumbs. The runner does not require a
clean or pushed work tree (jj working-copy commits, detached `HEAD`s, and dirty
trees are all measurable), so the record's `## Reproduction` — not `results.json`
— carries the human-facing provenance.

v1 files (warmup/reps/latency_k, cache-aware latency samples) are not comparable
with v2/v3 and are refused by the report loader. v2 predates the `mode` field;
it is still renderable as a single "unknown" series but cannot name the vectors
that produced it.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from eval.questions import MULTI_PROFILE, SINGLE_PROFILE, Question
from eval.runner.metrics import delta, mean

RESULTS_SCHEMA_VERSION = 3
# Canonical order: the CLI preserves it and the report reads it in this order.
SEARCH_MODES = ("hybrid", "dense", "sparse")

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


def validate_modes(modes) -> tuple[str, ...]:
    """Normalizes the searched-mode set to canonical order.

    Rejects an empty set, duplicates, and unknown names. The canonical order is
    independent of argument order, so a subset sweep stays comparable.
    """

    requested = tuple(modes or ())
    if not requested:
        raise ValueError("at least one search mode is required")
    if len(set(requested)) != len(requested):
        raise ValueError(
            f"search modes must be unique, got {list(requested)}"
        )
    for mode in requested:
        if mode not in SEARCH_MODES:
            raise ValueError(
                f"mode must be one of {', '.join(SEARCH_MODES)}, got {mode!r}"
            )
    return tuple(mode for mode in SEARCH_MODES if mode in requested)


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
    modes,
    k_values,
) -> dict:
    mode_values = validate_modes(modes)
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
        "modes": list(mode_values),
        "k_values": list(values),
        "profiles": profiles,
        "degradation": degradation(profiles, values, mode_values),
        "summary": summarize(profiles, values, mode_values),
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


def _by_k(question: dict, mode: str, k: int) -> dict:
    """One question's metrics at one k in one mode; empty when not measured."""

    entry = ((question.get("modes") or {}).get(mode) or {}).get("by_k") or {}
    return entry.get(str(k)) or {}


def _profile_mode(profile: dict, mode: str) -> dict:
    return ((profile.get("modes") or {}).get(mode)) or {}


def summarize(profiles: dict, k_values: tuple[int, ...], modes) -> dict:
    """Per-profile aggregates over the applicable questions, per mode."""

    summary: dict = {}
    for name, profile in profiles.items():
        applied = profile["questions"]
        answered = [q for q in applied if q["answerable"]]
        unanswerable = [q for q in applied if not q["answerable"]]

        by_mode: dict = {}
        for mode in modes:
            mean_recall = {
                f"recall@{k}": mean(
                    _by_k(q, mode, k).get("recall") for q in answered
                )
                for k in k_values
            }
            mean_purity = {
                f"mean_purity@{k}": mean(
                    _by_k(q, mode, k).get("purity") for q in answered
                )
                for k in k_values
            }
            mean_mrr = {
                f"mean_mrr@{k}": mean(_by_k(q, mode, k).get("mrr") for q in answered)
                for k in k_values
            }

            no_hit_values = [
                _by_k(q, mode, k).get("no_hit")
                for q in unanswerable
                for k in k_values
            ]

            mode_profile = _profile_mode(profile, mode)
            timings = {
                phase: _timing_rollup((mode_profile.get("timings") or {}).get(phase))
                for phase in _TIMING_PHASES
            }

            by_mode[mode] = {
                "mean_recall": mean_recall,
                **mean_purity,
                **mean_mrr,
                "unanswerable_no_hit_rate": mean(no_hit_values),
                "timings": timings,
                "rerank": mode_profile.get("rerank"),
            }

        summary[name] = {
            "questions_applied": len(applied),
            "answerable": len(answered),
            "unanswerable": len(unanswerable),
            "modes": by_mode,
        }
    return summary


def degradation(profiles: dict, k_values: tuple[int, ...], modes) -> dict:
    """Single -> multi deltas over the questions both profiles share, per mode."""

    if SINGLE_PROFILE not in profiles or MULTI_PROFILE not in profiles:
        return {}

    single = _question_index(profiles[SINGLE_PROFILE])
    multi = _question_index(profiles[MULTI_PROFILE])
    shared = sorted(
        set(single) & set(multi) & {qid for qid, q in single.items() if q["answerable"]}
    )

    by_mode: dict = {}
    for mode in modes:
        entry: dict = {}
        for metric, extract in (
            ("recall", lambda by_k: by_k.get("recall")),
            ("purity", lambda by_k: by_k.get("purity")),
            ("mrr", lambda by_k: by_k.get("mrr")),
        ):
            for k in k_values:
                single_values = mean(
                    extract(_by_k(single[qid], mode, k)) for qid in shared
                )
                multi_values = mean(
                    extract(_by_k(multi[qid], mode, k)) for qid in shared
                )
                entry[f"{metric}@{k}"] = delta(multi_values, single_values)

        single_timing = (
            (_profile_mode(profiles[SINGLE_PROFILE], mode).get("timings") or {}).get(
                "e2e"
            )
            or {}
        )
        multi_timing = (
            (_profile_mode(profiles[MULTI_PROFILE], mode).get("timings") or {}).get(
                "e2e"
            )
            or {}
        )
        entry["p95_ms"] = delta(multi_timing.get("p95_ms"), single_timing.get("p95_ms"))
        by_mode[mode] = entry

    return {
        f"{SINGLE_PROFILE}->{MULTI_PROFILE}": {
            "from": SINGLE_PROFILE,
            "to": MULTI_PROFILE,
            "questions_compared": shared,
            "modes": by_mode,
        }
    }


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
