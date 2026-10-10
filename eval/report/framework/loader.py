"""Version-aware loader for the `results.json` render input.

`results.json` is a versioned contract (see `eval/runner/results.py`). Old
experiments must stay renderable forever, so the framework keeps its own list of
the schema versions it can draw and refuses anything else loudly rather than
guessing at an unknown shape.

The loader also papers over the small shape drift the v1 contract accumulated:
the swept `k` set (`k_values`) and the pushed `commit_ref` were added after the
first artifacts were produced, so they may be absent in an older v1 file. The
accessors below derive what they can and fail clearly when they cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

# Every schema version this framework renders. Append when the contract's
# `RESULTS_SCHEMA_VERSION` advances and rendering support lands; never reorder.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)
LATEST_SCHEMA_VERSION: int = max(SUPPORTED_SCHEMA_VERSIONS)

# The latency k recorded by the runner's default when a file predates the field.
DEFAULT_LATENCY_K = 7

_PROFILE_ORDER = ("single-project", "multi-project")


class ResultsError(Exception):
    """A results file is missing, malformed, or missing a required field."""


class UnsupportedSchemaVersion(ResultsError):
    """`results.json` carries a schema version this framework cannot render."""

    def __init__(self, version: object, *, source: str = "results.json") -> None:
        known = ", ".join(str(item) for item in SUPPORTED_SCHEMA_VERSIONS)
        relation = (
            "newer than"
            if isinstance(version, int) and version > LATEST_SCHEMA_VERSION
            else "older than"
        )
        super().__init__(
            f"{source}: schema_version {version!r} is {relation} this report framework "
            f"supports (known versions: {known}). Update eval/report/framework, or check "
            "out a revision that knows this version."
        )
        self.version = version


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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


def ordered_profiles(results: dict) -> list[str]:
    """Profile names, single-project before multi-project, then alphabetically."""

    names = list((results.get("profiles") or {}))
    return sorted(
        names,
        key=lambda name: (
            _PROFILE_ORDER.index(name) if name in _PROFILE_ORDER else len(_PROFILE_ORDER),
            name,
        ),
    )


def latency_k(results: dict) -> int:
    """The k at which latency (and the labelled summaries) were measured."""

    for profile in (results.get("profiles") or {}).values():
        value = (profile.get("latency") or {}).get("latency_k")
        if _is_int(value):
            return value
    return DEFAULT_LATENCY_K


def _k_from_summary(results: dict) -> set[int]:
    values: set[int] = set()
    for profile in (results.get("summary") or {}).values():
        for label in (profile.get("mean_recall") or {}):
            if isinstance(label, str) and label.startswith("recall@"):
                suffix = label.split("@", 1)[1]
                if suffix.isdigit():
                    values.add(int(suffix))
    return values


def _k_from_by_k(results: dict) -> set[int]:
    values: set[int] = set()
    for profile in (results.get("profiles") or {}).values():
        for question in profile.get("questions", []):
            for key in (question.get("by_k") or {}):
                if str(key).isdigit():
                    values.add(int(key))
    return values


def swept_k_values(results: dict) -> tuple[int, ...]:
    """The swept k set, recorded explicitly or derived from an older v1 file."""

    explicit = results.get("k_values")
    if isinstance(explicit, list) and explicit:
        values = {int(value) for value in explicit if _is_int(value)}
        if values:
            return tuple(sorted(values))

    derived = _k_from_summary(results) | _k_from_by_k(results)
    if derived:
        return tuple(sorted(derived))
    raise ResultsError(
        "results.json records no k_values and has no by_k metrics to derive the "
        "swept k set from"
    )
