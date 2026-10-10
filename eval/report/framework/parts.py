"""Run-directory discovery for the two independently-versioned report parts.

A run directory (`eval/results/<experiment>/<run>/`) can contain a retrieval
part (`results.json`), one ingestion part per profile (`ingest-<profile>.json`),
both, or — transiently — neither. The framework composes whichever parts a run
holds, so these helpers locate them without assuming either is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eval.report.framework.loader import ResultsError


@dataclass(frozen=True)
class RunParts:
    """The report parts found in one run directory."""

    run_dir: Path
    results: Path | None
    ingest: tuple[Path, ...]

    @property
    def has_retrieval(self) -> bool:
        return self.results is not None

    @property
    def has_ingestion(self) -> bool:
        return bool(self.ingest)

    @property
    def is_empty(self) -> bool:
        return self.results is None and not self.ingest


def discover_run_parts(run_dir: str | Path) -> RunParts:
    """Locates `results.json` and every `ingest-*.json` in `run_dir`.

    Missing parts are `None`/empty rather than an error: a run may legitimately
    hold only one artifact (e.g. an ingest-only measurement).
    """

    run_dir = Path(run_dir)
    results = run_dir / "results.json"
    results_path = results if results.is_file() else None
    ingest = tuple(sorted(path for path in run_dir.glob("ingest-*.json") if path.is_file()))
    return RunParts(run_dir=run_dir, results=results_path, ingest=ingest)


def experiment_runs(experiment_dir: str | Path) -> list[Path]:
    """Run directories under an experiment directory, oldest first.

    The run directory name is an ISO-8601 UTC timestamp, so lexical order is
    chronological order.
    """

    experiment_dir = Path(experiment_dir)
    if not experiment_dir.is_dir():
        return []
    return sorted(path for path in experiment_dir.iterdir() if path.is_dir())


def latest_run(experiment_dir: str | Path) -> RunParts:
    """The newest run under an experiment directory that holds at least one part."""

    for run_dir in reversed(experiment_runs(experiment_dir)):
        parts = discover_run_parts(run_dir)
        if not parts.is_empty:
            return parts
    raise ResultsError(f"no results.json or ingest report under {experiment_dir}")
