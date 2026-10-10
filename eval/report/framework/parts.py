"""Run-directory discovery for the two independently-versioned report parts.

A run directory (`eval/results/<experiment>/<run>/`) can contain a retrieval
part (`results.json`), one ingestion part per profile (`ingest-<profile>.json`),
both, or — transiently — neither. The framework composes whichever parts a run
holds, so these helpers locate them without assuming either is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eval.report.framework.ingest import load_ingest, profile_label
from eval.report.framework.loader import ResultsError, load_results


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


@dataclass(frozen=True)
class LoadedRun:
    """A run's discovered parts, loaded and version-validated.

    `ingest` pairs each document with the profile it belongs to, in the order
    the files were discovered (`ingest-multi-project.json` before
    `ingest-single-project.json`).
    """

    parts: RunParts
    results: dict | None
    ingest: tuple[tuple[str, dict], ...]

    @property
    def label(self) -> str:
        """A human label: the retrieval experiment/run, else the directory name."""

        if self.results is not None:
            experiment = self.results.get("experiment") or "experiment"
            run = self.results.get("run") or self.parts.run_dir.name
            return f"{experiment} ({run})"
        return self.parts.run_dir.name

    @property
    def is_empty(self) -> bool:
        return self.results is None and not self.ingest

    def ingest_documents(self) -> tuple[dict, ...]:
        return tuple(document for _, document in self.ingest)

    def ingest_by_profile(self) -> dict[str, dict]:
        return {profile: document for profile, document in self.ingest}


def load_run(run_dir: str | Path) -> LoadedRun:
    """Discovers, loads, and validates the parts in one run directory."""

    parts = discover_run_parts(run_dir)
    if parts.is_empty:
        raise ResultsError(f"no results.json or ingest report under {parts.run_dir}")

    results = load_results(parts.results) if parts.results is not None else None
    ingest_documents: list[tuple[str, dict]] = []
    for path in parts.ingest:
        document = load_ingest(path)
        ingest_documents.append((profile_label(document), document))
    return LoadedRun(parts=parts, results=results, ingest=tuple(ingest_documents))


def resolve_run(reference: str | Path, *, root: str | Path) -> LoadedRun:
    """Loads a run named by an experiment id or by a path.

    An existing path is used directly — a run directory, or a `results.json`
    whose directory supplies the ingestion parts. Anything else is an experiment
    id under `<root>/eval/results/<id>`, resolved to its newest run with parts.
    """

    path = Path(reference)
    if path.exists():
        return load_run(path.parent if path.is_file() else path)
    experiment_dir = Path(root) / "eval" / "results" / reference
    return load_run(latest_run(experiment_dir).run_dir)
