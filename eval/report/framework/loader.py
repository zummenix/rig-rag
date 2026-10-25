"""Version-aware loader for the `results.json` render input.

`results.json` is a versioned contract (see `eval/runner/results.py`). The
framework renders schema v2 and v3: v1 artifacts predate the rerank-cache
removal, their latency samples mix warm cache hits with uncached provider calls,
and their metrics are not comparable. v2 predates the `mode` field, so it cannot
say which vectors produced it. An unsupported version is refused loudly rather
than guessed at.

The rest of the framework consumes **series**, not raw documents: one entry per
(profile, mode). `series()` flattens v2 into a single `unknown` mode and v3 into
one entry per recorded mode, so every downstream section handles both versions
without branching on `schema_version`.
"""

from __future__ import annotations

import json
from pathlib import Path

# Every schema version this framework renders. Append when the contract's
# `RESULTS_SCHEMA_VERSION` advances and rendering support lands.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (2, 3)
LATEST_SCHEMA_VERSION: int = max(SUPPORTED_SCHEMA_VERSIONS)

# Canonical search modes (mirrors `eval.runner.results.SEARCH_MODES`; kept local
# so the report framework does not import the runner package).
SEARCH_MODES: tuple[str, ...] = ("hybrid", "dense", "sparse")
UNKNOWN_MODE = "unknown"
UNKNOWN_MODE_LABEL = "not recorded (schema v2)"

_PROFILE_ORDER = ("single-project", "multi-project")


class ResultsError(Exception):
    """A results file is missing, malformed, or missing a required field."""


class UnsupportedSchemaVersion(ResultsError):
    """`results.json` carries a schema version this framework cannot render."""

    def __init__(self, version: object, *, source: str = "results.json") -> None:
        known = ", ".join(str(item) for item in SUPPORTED_SCHEMA_VERSIONS)
        if version == 1:
            detail = (
                "v1 baselines predate cache removal and are not comparable; "
                "record a v3 baseline instead"
            )
        else:
            relation = (
                "newer than"
                if isinstance(version, int) and version > LATEST_SCHEMA_VERSION
                else "older than"
            )
            detail = f"version is {relation} this report framework supports"
        super().__init__(
            f"{source}: schema_version {version!r} cannot be rendered ({detail}; "
            f"known versions: {known}). Update eval/report/framework, or check out "
            "a revision that knows this version."
        )
        self.version = version


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _profile_modes(profile: dict, source: str, name: str) -> dict:
    modes = profile.get("modes")
    if not isinstance(modes, dict):
        raise ResultsError(f"{source}: profile {name!r} is missing its `modes` object")
    return modes


def _validate_v3(document: dict, source: str) -> None:
    """v3 must name its measured modes and repeat exactly those keys everywhere."""

    modes = document.get("modes")
    if not isinstance(modes, list) or not modes:
        raise ResultsError(f"{source}: v3 must record a non-empty `modes` list")

    names: list[str] = []
    for mode in modes:
        if mode not in SEARCH_MODES:
            raise ResultsError(
                f"{source}: unknown search mode {mode!r} in `modes` "
                f"(known: {', '.join(SEARCH_MODES)})"
            )
        if mode in names:
            raise ResultsError(f"{source}: duplicate search mode {mode!r} in `modes`")
        names.append(mode)
    expected = set(names)

    for profile_name, profile in (document.get("profiles") or {}).items():
        if not isinstance(profile, dict):
            raise ResultsError(f"{source}: profile {profile_name!r} is not an object")
        recorded = set(_profile_modes(profile, source, profile_name))
        if recorded != expected:
            raise ResultsError(
                f"{source}: profile {profile_name!r} records modes "
                f"{sorted(recorded)} but the run records {sorted(expected)}"
            )
        questions = profile.get("questions")
        if not isinstance(questions, list):
            raise ResultsError(
                f"{source}: profile {profile_name!r} is missing its `questions` list"
            )
        for question in questions:
            if not isinstance(question, dict):
                raise ResultsError(f"{source}: profile {profile_name!r} has a non-object question")
            recorded = set(_profile_modes(question, source, f"{profile_name}/{question.get('id', '?')}"))
            if recorded != expected:
                raise ResultsError(
                    f"{source}: question {question.get('id', '?')!r} in profile "
                    f"{profile_name!r} records modes {sorted(recorded)} but the run "
                    f"records {sorted(expected)}"
                )


def validate_results(document: object, *, source: str = "results.json") -> int:
    """Checks the top-level shape and supported version; returns the version."""

    if not isinstance(document, dict):
        raise ResultsError(f"{source}: expected a JSON object, got {type(document).__name__}")

    version = document.get("schema_version")
    if not _is_int(version):
        raise ResultsError(f"{source}: missing or non-integer schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(version, source=source)
    if not isinstance(document.get("profiles"), dict):
        raise ResultsError(f"{source}: missing `profiles` object")
    if version == 3:
        _validate_v3(document, source)
    return version


def load_results(path: str | Path) -> dict:
    """Reads and validates a `results.json` file."""

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ResultsError(f"results file not found: {path}") from None
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResultsError(f"{path} is not valid JSON: {error}") from None
    validate_results(document, source=str(path))
    return document


def latest_results(experiment_dir: str | Path) -> Path:
    """The newest `<run>/results.json` under an experiment directory."""

    experiment_dir = Path(experiment_dir)
    candidates = sorted(
        path for path in experiment_dir.glob("*/results.json") if path.is_file()
    )
    if not candidates:
        raise ResultsError(f"no results.json under {experiment_dir}")
    return candidates[-1]


def profile_rank(name: str) -> int:
    """Canonical profile position (single before multi, unknown names last)."""

    return (
        _PROFILE_ORDER.index(name)
        if name in _PROFILE_ORDER
        else len(_PROFILE_ORDER)
    )


def ordered_profiles(results: dict) -> list[str]:
    """Profile names, single-project before multi-project, then alphabetically."""

    names = list((results.get("profiles") or {}))
    return sorted(names, key=lambda name: (profile_rank(name), name))


def swept_k_values(results: dict) -> tuple[int, ...]:
    """The recorded `k_values`; a v2 file always carries them explicitly."""

    explicit = results.get("k_values")
    if not isinstance(explicit, list):
        raise ResultsError("results.json records no k_values")
    values = {int(value) for value in explicit if _is_int(value)}
    if not values:
        raise ResultsError("results.json records no k_values")
    return tuple(sorted(values))


def recorded_modes(results: dict) -> tuple[str, ...] | None:
    """The v3 measured modes, or `None` for a v2 document."""

    if results.get("schema_version") != 3:
        return None
    modes = results.get("modes")
    if not isinstance(modes, list):
        return None
    return tuple(str(mode) for mode in modes)


def _counts(summary_entry: dict | None) -> dict:
    summary_entry = summary_entry or {}
    return {
        "questions_applied": summary_entry.get("questions_applied"),
        "answerable": summary_entry.get("answerable"),
        "unanswerable": summary_entry.get("unanswerable"),
    }


def _question_view(question: dict, *, mode: str) -> dict:
    """One question's mode-independent metadata plus one mode's measurements."""

    if mode == UNKNOWN_MODE and "by_k" in question:
        # v2: the measurements live directly on the question.
        measured = {
            "by_k": question.get("by_k") or {},
            "timings": question.get("timings") or {},
            "rerank": question.get("rerank"),
        }
    else:
        measured = (question.get("modes") or {}).get(mode) or {}
    return {
        "id": question.get("id", ""),
        "question": question.get("question", ""),
        "answerable": question.get("answerable", True),
        "gold": question.get("gold") or [],
        "notes": question.get("notes") or "",
        "by_k": measured.get("by_k") or {},
        "timings": measured.get("timings") or {},
        "rerank": measured.get("rerank"),
    }


def series(results: dict) -> list[dict]:
    """One render-ready entry per (profile, mode), in canonical order.

    Every entry carries the same fields regardless of schema version:

    * `profile`, `mode` (v2: `"unknown"`), `label` (v2: the explanatory label)
    * `timings`, `rerank` — this (profile, mode)'s rollups
    * `summary`, `counts` — this mode's aggregates and the profile-level counts
    * `questions` — each with `by_k`, `timings`, `rerank`
    """

    modes = recorded_modes(results)
    profiles = results.get("profiles") or {}
    summary = results.get("summary") or {}
    entries: list[dict] = []

    for name in ordered_profiles(results):
        profile = profiles.get(name) or {}
        counts = _counts(summary.get(name))
        if modes is None:
            entries.append(
                {
                    "profile": name,
                    "mode": UNKNOWN_MODE,
                    "label": UNKNOWN_MODE_LABEL,
                    "timings": profile.get("timings") or {},
                    "rerank": profile.get("rerank") or {},
                    "summary": summary.get(name) or {},
                    "counts": counts,
                    "questions": [
                        _question_view(question, mode=UNKNOWN_MODE)
                        for question in profile.get("questions") or []
                    ],
                }
            )
            continue

        for mode in modes:
            mode_profile = (profile.get("modes") or {}).get(mode) or {}
            mode_summary = ((summary.get(name) or {}).get("modes") or {}).get(mode) or {}
            entries.append(
                {
                    "profile": name,
                    "mode": mode,
                    "label": mode,
                    "timings": mode_profile.get("timings") or {},
                    "rerank": mode_profile.get("rerank") or {},
                    "summary": mode_summary,
                    "counts": counts,
                    "questions": [
                        _question_view(question, mode=mode)
                        for question in profile.get("questions") or []
                    ],
                }
            )
    return entries


def degradation_modes(results: dict) -> dict:
    """Degradation normalized to `{pair: {..., "modes": {mode: metrics}}}`."""

    modes = recorded_modes(results)
    normalized: dict = {}
    for key, entry in (results.get("degradation") or {}).items():
        if not isinstance(entry, dict):
            continue
        base = {
            "from": entry.get("from", ""),
            "to": entry.get("to", ""),
            "questions_compared": entry.get("questions_compared") or [],
        }
        if modes is None:
            base["modes"] = {
                UNKNOWN_MODE: {
                    metric: value
                    for metric, value in entry.items()
                    if metric not in ("from", "to", "questions_compared")
                }
            }
        else:
            base["modes"] = entry.get("modes") or {}
        normalized[key] = base
    return normalized
