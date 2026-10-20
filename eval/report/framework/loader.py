"""Version-aware loader for the `results.json` render input.

`results.json` is a versioned contract (see `eval/runner/results.py`). The
framework renders schema v2 only: v1 artifacts predate the rerank-cache removal,
their latency samples mix warm cache hits with uncached provider calls, and
their metrics are not comparable. An unsupported version is refused loudly
rather than guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

# Every schema version this framework renders. Append when the contract's
# `RESULTS_SCHEMA_VERSION` advances and rendering support lands.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (2,)
LATEST_SCHEMA_VERSION: int = max(SUPPORTED_SCHEMA_VERSIONS)

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
                "record a v2 baseline instead"
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


def swept_k_values(results: dict) -> tuple[int, ...]:
    """The recorded `k_values`; a v2 file always carries them explicitly."""

    explicit = results.get("k_values")
    if not isinstance(explicit, list):
        raise ResultsError("results.json records no k_values")
    values = {int(value) for value in explicit if _is_int(value)}
    if not values:
        raise ResultsError("results.json records no k_values")
    return tuple(sorted(values))
